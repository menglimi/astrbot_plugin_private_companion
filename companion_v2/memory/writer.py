"""One authoritative memory writer: evidence, CAS, pending work and receipts."""

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3
from uuid import uuid4

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import SourceRef, VersionRef, ScopeRef, OperationRef
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.dependencies import link
from companion_v2.sources.privacy import redact_text, redact_fields, REDACTED
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.models import OperationState

from . import codec
from .operation_receipts import resolved as resolved_receipt
from .access import MemoryAccess
from .models import AtomRef, Proposal, MemoryPolicy, owner_key, text, timestamp


DEGRADED_READ_CODES = frozenset({
    "memory_inactive",
    "memory_forgotten",
    "memory_expired",
    "memory_not_current",
    "memory_session_mismatch",
    "source_revoked",
    "source_expired",
    "memory_source_unavailable",
    "memory_evidence_forgotten",
    "conversation_context_reset",
})


class MemoryWriter:
    def __init__(self, tasks, *, policy=MemoryPolicy(), operator=None, settings=None):
        self.tasks, self.runtime, self.policy = tasks, tasks.runtime, policy
        self.access = MemoryAccess(tasks, policy, operator=operator, settings=settings)
        self.sources = self.access.sources
        self.operations = OperationRepository(self.runtime)

    def caller(self, context):
        return (self.access.operator or context.actor).subject.subject_id

    def operation_id(self, context, namespace, operation_key):
        text(operation_key, "operation_key", 512)
        return "memory-op-" + digest([owner_key(context), self.caller(context), namespace, operation_key])

    def _reserve(self, context, identifier, payload_hash):
        reference = OperationRef(identifier, "memory", context.scope, VersionRef(identifier, 1), identifier)
        require(self.operations.reserve(reference, parameter_hash=payload_hash, observed_at=self.tasks.now()))

    def _complete(self, identifier):
        receipt = require(self.operations.get(identifier))
        if receipt.state is OperationState.ACCEPTED:
            require(self.operations.transition(identifier, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED, observed_at=self.tasks.now()))

    def _head(self, context, reference, *, allow_inactive=False):
        row = self.runtime.read_one("SELECT * FROM memory_atoms WHERE atom_id = ? AND owner_key = ?",
                                   (reference.atom_id, owner_key(context)))
        if row is None:
            raise TaskError("memory_unavailable", "memory is unavailable to this relationship")
        if row["revision"] != reference.revision:
            raise TaskError("memory_revision_conflict", "memory has a newer revision")
        if not allow_inactive and row["state"] != "active":
            raise TaskError("memory_inactive", "memory is not active")
        return row

    def _valid(self, context, row, *, at=None):
        now = self.tasks.now()
        at = timestamp(at) or now
        value = json.loads(row["record_json"])
        if value.get("forgotten"):
            raise TaskError("memory_forgotten", "memory was forgotten")
        item = codec.proposal(value["proposal"])
        if item.retention == "session" and ScopeRef.restore(row["scope_json"] if isinstance(row["scope_json"], dict)
                else json.loads(row["scope_json"])) != context.scope:
            raise TaskError("memory_session_mismatch", "session memory belongs to its original session")
        if item.valid_to and at >= item.valid_to:
            raise TaskError("memory_expired", "memory validity ended")
        if item.valid_from and at < item.valid_from:
            raise TaskError("memory_not_current", "memory is not yet valid")
        # Historical validity never rewinds today's authorization or withdrawal.
        for evidence in item.evidence:
            self.access.evidence(context, evidence, at=now)
        require(self.sources.validate_reference(codec.source(json.loads(row["source_json"])), observed_at=now))
        for dependency in item.dependencies:
            head = self._head(context, dependency)
            require(self.sources.validate_reference(codec.source(json.loads(head["source_json"])), observed_at=now))
        return value, item

    @staticmethod
    def _project_active(row, value, item):
        return {"atom_id": row["atom_id"], "revision": row["revision"], "state": row["state"],
            "namespace": item.namespace, "subject": item.subject, "predicate": item.predicate,
            "object": item.object, "qualifiers": item.qualifiers, "polarity": item.polarity,
            "attribution": item.attribution, "representation": item.representation, "retention": item.retention,
            "context_mode": item.context_mode,
            "valid_from": stamp(item.valid_from) if item.valid_from else None,
            "valid_to": stamp(item.valid_to) if item.valid_to else None, "confidence": item.confidence,
            "observed_at": value["observed_at"], "accepted_by": value.get("accepted_by"), "updated_at": row["updated_at"],
            "evidence": json.loads(encode(item.evidence)), "source": json.loads(row["source_json"])}

    @staticmethod
    def _project_degraded(row, reason_code):
        return {
            "atom_id": row["atom_id"],
            "revision": row["revision"],
            "state": row["state"],
            "representation": None,
            "object": None,
            "updated_at": row["updated_at"],
            "unavailable_reason": reason_code,
            "read_only": True,
        }

    @outcome
    def read(self, context, reference, *, at=None):
        self.access.check(context)
        with self.runtime.read_snapshot():
            row = self._head(context, reference)
            value, item = self._valid(context, row, at=at)
            return self._project_active(row, value, item)

    @outcome
    def read_or_degraded(self, context, reference, *, degraded_codes=DEGRADED_READ_CODES, at=None):
        """Read one authorized fact, degrading inactive or unreadable heads within a single snapshot."""
        self.access.check(context)
        with self.runtime.read_snapshot():
            row = self._head(context, reference, allow_inactive=True)
            if row["state"] != "active":
                if "memory_inactive" in degraded_codes:
                    return self._project_degraded(row, "memory_inactive")
                raise TaskError("memory_inactive", "memory is not active")
            try:
                value, item = self._valid(context, row, at=at)
            except TaskError as error:
                if error.code in degraded_codes:
                    return self._project_degraded(row, error.code)
                raise
            return self._project_active(row, value, item)

    def _validate_proposal(self, context, item, action):
        self.access.check(context, action)
        if item.retention in ("session", "short"):
            from companion_v2.conversation.reset import source_current
            if any(not source_current(self.runtime, evidence.source) for evidence in item.evidence):
                raise TaskError("conversation_context_reset", "temporary memory evidence precedes its window's reset")
        if item.retention == "protected":
            self.access.check(context, "memory.protect")
        for evidence in item.evidence:
            if evidence.source.owner.startswith("memory."):
                raise TaskError("memory_derived_evidence", "derived memory cannot certify another fact; use its original evidence")
            self.access.evidence(context, evidence, action=action)
        if item.attribution == "user" and item.subject == "self" and item.operation in ("add", "correct"):
            if not any(e.source_kind == "human_message" and e.speaker_id == context.actor.subject.subject_id for e in item.evidence):
                raise TaskError("memory_attribution_unproven", "a user assertion needs their captured message")
        if item.subject != "self" and not item.subject.startswith("entity:"):
            raise ValueError("subjects are self or source-scoped entity references, never platform accounts")
        if item.subject.startswith("entity:") and item.attribution == "user":
            raise TaskError("memory_attribution_unproven", "a mentioned entity is not the authenticated speaker")
        if item.attribution == "bot" and item.subject == "self":
            raise TaskError("memory_role_owner_required", "role self-interpretation belongs to the role or relationship owner")
        for dependency in item.dependencies:
            require(self.read(context, dependency))
        if item.operation in ("add", "correct"):
            keys = {digest(e.source) for e in item.evidence}
            guards = self.runtime.read_all("SELECT evidence_key FROM memory_guards WHERE owner_key = ? AND fact_key = ?",
                                          (owner_key(context), item.fact_key))
            if keys and keys.issubset({row["evidence_key"] for row in guards}):
                raise TaskError("memory_corrected_evidence", "old evidence alone cannot restore a corrected or forgotten conclusion")

    @staticmethod
    def prepare_proposal(item):
        """Use the same credential representation for staged and committed facts."""
        if not isinstance(item, Proposal):
            raise ValueError("writer requires a structured proposal")
        values = {key: redact_text(getattr(item, key)) for key in (
            "predicate", "object", "representation", "reason", "stability_estimate")}
        if item.operation in ("add", "correct"):
            values["object"] = redact_fields({item.predicate: item.object}).get(redact_text(item.predicate), REDACTED)
        return replace(item, **values, qualifiers=redact_fields(item.qualifiers))

    @outcome
    def submit(self, context, item: Proposal, *, operation_key: str):
        item = self.prepare_proposal(item)
        text(operation_key, "operation_key", 512)
        owner = owner_key(context)
        identifier = self.operation_id(context, item.namespace, operation_key)
        action = {"correct": "memory.correct", "retract": "memory.retract"}.get(item.operation, "memory.propose")
        with self.runtime.transaction() as connection:
            self.access.check(context, action)
            old = connection.execute("SELECT * FROM memory_operations WHERE operation_id = ?", (identifier,)).fetchone()
            if old is not None:
                if old["payload_hash"] != digest(item):
                    raise TaskError("memory_operation_conflict", "original operation has different proposal content")
                return self._receipt(old)
            self._validate_proposal(context, item, action)
            self._reserve(context, identifier, digest(item))
            now = stamp(self.tasks.now())
            result = {"operation_id": identifier, "state": "pending" if item.defer_until_boundary else "validated",
                      "atom_id": None, "revision": None, "delivery": "not_started"}
            connection.execute("INSERT INTO memory_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, owner, self.caller(context), item.namespace, digest(item), encode(item),
                 encode(context), result["state"], encode(result), now, now))
            if not item.defer_until_boundary:
                result = self._commit(context, item, identifier)
            self._complete(identifier)
            return result

    @staticmethod
    def _receipt(row):
        # Receipts deliberately contain no remembered body or original evidence text.
        return json.loads(row["result_json"])

    @outcome
    def operation(self, context, operation_id):
        self.access.check(context)
        row = self.runtime.read_one("SELECT * FROM memory_operations WHERE operation_id = ? AND owner_key = ? AND caller_id = ?",
            (operation_id, owner_key(context), self.caller(context)))
        if row is None:
            raise TaskError("memory_operation_unavailable", "the original operation is unavailable")
        return self._receipt(row)

    @outcome
    def commit_pending(self, context, operation_id):
        with self.runtime.transaction():
            self.access.check(context, "memory.propose")
            row = self.runtime.read_one("SELECT * FROM memory_operations WHERE operation_id = ? AND owner_key = ?",
                                       (operation_id, owner_key(context)))
            if row is None or row["caller_id"] != self.caller(context):
                raise TaskError("memory_operation_unavailable", "the original proposal is unavailable")
            if row["state"] != "pending":
                return self._receipt(row)
            original = json.loads(row["context_json"])
            if original["role_binding"] != json.loads(encode(context.role_binding)):
                raise TaskError("memory_pending_context_changed", "pending proposal needs its original role/session binding")
            item = codec.proposal(json.loads(row["proposal_json"]))
            if item.valid_to and item.valid_to <= self.tasks.now():
                raise TaskError("memory_expired", "pending memory is no longer current")
            return self._commit(context, item, operation_id)

    @outcome
    def pending(self, context, *, limit=32, only_operation_ids=None, exclude_operation_ids=(), exclude_reflection_claims=False):
        """Filter claimed work before applying the page limit; repairs keep their original set."""
        self.access.check(context, "memory.propose")
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("pending page size must be bounded")
        def identifiers(values):
            if type(values) not in (tuple, list, set, frozenset):
                raise ValueError("pending selection requires operation IDs")
            return encode(sorted({text(value, "operation_id", 512) for value in values}))
        excluded = identifiers(exclude_operation_ids)
        selected = None if only_operation_ids is None else identifiers(only_operation_ids)
        if type(exclude_reflection_claims) is not bool:
            raise ValueError("reflection reservation filtering must be explicit")
        from .reflection_inputs import HELD_SQL
        items = []
        for row in self.runtime.read_all("""SELECT * FROM memory_operations
            WHERE owner_key = ? AND caller_id = ? AND state = 'pending'
            AND operation_id NOT IN (SELECT value FROM json_each(?))
            AND (? IS NULL OR operation_id IN (SELECT value FROM json_each(?)))
            AND (? = 0 OR NOT EXISTS (SELECT 1 FROM memory_reflection_inputs i
                JOIN memory_reflections r ON r.batch_id = i.batch_id
                WHERE i.owner_key = memory_operations.owner_key AND i.input_key = memory_operations.operation_id
                AND i.kind = 'pending' AND r.state IN """ + HELD_SQL + """))
            ORDER BY created_at, operation_id LIMIT ?""",
            (owner_key(context), self.caller(context), excluded, selected, selected, int(exclude_reflection_claims), limit)):
            item = codec.proposal(json.loads(row["proposal_json"]))
            try:
                self._validate_proposal(context, item, "memory.propose")
                if item.valid_to and item.valid_to <= self.tasks.now():
                    raise TaskError("memory_expired", "pending memory is no longer current")
            except TaskError as error:
                items.append({"operation_id": row["operation_id"], "state": "unavailable", "reason": error.code})
                continue
            items.append({"operation_id": row["operation_id"], "state": "pending", "proposal": json.loads(row["proposal_json"])})
        return items

    @outcome
    def discard_pending(self, context, operation_id):
        self.access.check(context, "memory.propose")
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT * FROM memory_operations WHERE operation_id = ? AND owner_key = ? AND caller_id = ?",
                                     (operation_id, owner_key(context), self.caller(context))).fetchone()
            if row is None:
                raise TaskError("memory_operation_unavailable", "the original proposal is unavailable")
            if row["state"] != "pending":
                return self._receipt(row)
            item = codec.proposal(json.loads(row["proposal_json"]))
            result = resolved_receipt(self, context, item, {**self._receipt(row), "state": "discarded"})
            connection.execute("UPDATE memory_operations SET state = 'discarded', result_json = ?, proposal_json = NULL, updated_at = ? WHERE operation_id = ?",
                               (encode(result), stamp(self.tasks.now()), operation_id))
            return result

    def _commit(self, context, item, identifier):
        action = {"correct": "memory.correct", "retract": "memory.retract"}.get(item.operation, "memory.propose")
        self._validate_proposal(context, item, action)
        now, owner = self.tasks.now(), owner_key(context)
        target = self._head(context, item.target) if item.target else None
        if target:
            previous = codec.proposal(json.loads(target["record_json"])["proposal"])
            if previous.retention == "protected":
                self.access.check(context, "memory.protect")
            if item.namespace != target["namespace"]:
                raise TaskError("memory_target_mismatch", "correction must keep the target namespace")
            if item.operation == "correct" and (item.subject, item.predicate, item.qualifiers) != (
                    previous.subject, previous.predicate, previous.qualifiers):
                raise TaskError("memory_target_mismatch", "correct the explicit fact; create a separate fact for a different subject or attribute")
            self._guard(context, previous, target["atom_id"], item.operation)
            self._revoke(codec.source(json.loads(target["source_json"])))
        atom_id, revision = (target["atom_id"], target["revision"] + 1) if target else ("memory-" + digest(identifier), 1)
        state = "skipped" if item.operation == "no_op" else "persisted"
        with self.runtime.transaction() as connection:
            if item.operation in ("add", "correct"):
                others = connection.execute("SELECT atom_id, single_value FROM memory_atoms WHERE owner_key = ? AND fact_key = ? AND state = 'active' AND atom_id != ?",
                    (owner, item.fact_key, atom_id)).fetchall()
                if any(item.single_value or other["single_value"] for other in others):
                    raise TaskError("memory_fact_conflict", "a single-valued fact already exists; correct its explicit revision")
                # A revision number can be reused after an outer transaction
                # rolls back. The opaque stamp lets derived readers detect that.
                document = {"proposal": json.loads(encode(item)), "observed_at": stamp(now),
                            "accepted_by": self.caller(context), "change_token": uuid4().hex}
                source = SourceRef(atom_id, "memory.atom", context.scope, VersionRef(atom_id, revision), now,
                                   item.valid_to if item.retention == "short" else None)
                require(self.sources.publish(SourceRecord(source, context.audience, digest(item.representation), item.representation), observed_at=now))
                dependency_sources = [e.source for e in item.evidence]
                dependency_sources += [codec.source(json.loads(self._head(context, r)["source_json"])) for r in item.dependencies]
                link(self.runtime, source, tuple(set(dependency_sources)), now)
                if target:
                    connection.execute("UPDATE memory_revisions SET state = 'superseded' WHERE atom_id = ? AND revision = ?", (atom_id, target["revision"]))
                connection.execute("""INSERT INTO memory_atoms VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    ON CONFLICT(atom_id) DO UPDATE SET revision = excluded.revision, state = excluded.state,
                    record_json = excluded.record_json, source_json = excluded.source_json, updated_at = excluded.updated_at,
                    single_value = excluded.single_value, scope_json = excluded.scope_json""",
                    (atom_id, owner, item.namespace, item.subject, item.fact_key, int(item.single_value), revision,
                     encode(context.scope), encode(document), encode(source), stamp(now)))
                connection.execute("INSERT INTO memory_revisions VALUES (?, ?, 'active', ?, ?)", (atom_id, revision, encode(document), encode(source)))
            elif item.operation == "retract":
                document = encode({**json.loads(target["record_json"]), "change_token": uuid4().hex})
                connection.execute("UPDATE memory_atoms SET state = 'invalidated', revision = ?, record_json = ?, updated_at = ? WHERE atom_id = ?",
                                   (revision, document, stamp(now), atom_id))
                connection.execute("UPDATE memory_revisions SET state = 'invalidated' WHERE atom_id = ? AND revision = ?", (atom_id, target["revision"]))
                connection.execute("INSERT INTO memory_revisions VALUES (?, ?, 'invalidated', ?, ?)",
                                   (atom_id, revision, document, target["source_json"]))
            if item.operation != "no_op":
                connection.execute("INSERT INTO memory_changes(owner_key, namespace, atom_id, revision, kind, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (owner, item.namespace, atom_id, revision, {"add": "added", "correct": "corrected", "retract": "retracted"}[item.operation], stamp(now)))
            result = {"operation_id": identifier, "state": state, "atom_id": atom_id if item.operation != "no_op" else None,
                      "revision": revision if item.operation != "no_op" else None, "delivery": "not_started"}
            result = resolved_receipt(self, context, item, result)
            connection.execute("UPDATE memory_operations SET state = ?, result_json = ?, updated_at = ? WHERE operation_id = ?",
                               (state, encode(result), stamp(now), identifier))
            return result

    def _revoke(self, reference):
        row = self.runtime.read_one("SELECT governance_revision FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?",
                                   (reference.source_id, reference.version.lineage_id, reference.version.revision))
        if row:
            require(self.sources.revoke(reference, expected_governance_revision=row["governance_revision"], observed_at=self.tasks.now()))

    def _guard(self, context, previous, atom_id, reason):
        with self.runtime.transaction() as connection:
            for evidence in previous.evidence:
                connection.execute("""INSERT INTO memory_guards VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(owner_key, fact_key, evidence_key) DO UPDATE SET reason =
                    CASE WHEN excluded.reason = 'forgotten' THEN 'forgotten' ELSE memory_guards.reason END""",
                    (owner_key(context), previous.fact_key, digest(evidence.source), atom_id, reason))

    def _forget_record(self, context, row):
        """Erase a fact's bodies and suppress its evidence in the caller's transaction."""
        if row["owner_key"] != owner_key(context):
            raise TaskError("memory_unavailable", "memory is unavailable to this relationship")
        current = json.loads(row["record_json"])
        if current.get("forgotten"):
            raise TaskError("memory_forgotten", "memory is already forgotten")
        revisions = self.runtime.read_all(
            "SELECT record_json, source_json FROM memory_revisions WHERE atom_id = ? ORDER BY revision",
            (row["atom_id"],),
        )
        documents = [current, *(json.loads(item["record_json"]) for item in revisions)]
        proposals = [codec.proposal(document["proposal"]) for document in documents if "proposal" in document]
        if any(item.retention == "protected" for item in proposals):
            self.access.check(context, "memory.protect")
        for item in proposals:
            self._guard(context, item, row["atom_id"], "forgotten")
        references = {codec.source(json.loads(item["source_json"])) for item in revisions}
        references.add(codec.source(json.loads(row["source_json"])))
        for reference in references:
            self._revoke(reference)
        revision, now = row["revision"] + 1, stamp(self.tasks.now())
        tombstone = encode({"forgotten": True})
        with self.runtime.transaction() as connection:
            connection.execute(
                "UPDATE memory_atoms SET state = 'invalidated', revision = ?, record_json = ?, updated_at = ? WHERE atom_id = ?",
                (revision, encode({"forgotten": True, "change_token": uuid4().hex}), now, row["atom_id"]),
            )
            connection.execute("UPDATE memory_revisions SET state = 'invalidated', record_json = ? WHERE atom_id = ?",
                               (tombstone, row["atom_id"]))
            connection.execute("INSERT INTO memory_revisions VALUES (?, ?, 'invalidated', ?, ?)",
                               (row["atom_id"], revision, tombstone, row["source_json"]))
            connection.execute(
                "UPDATE memory_operations SET proposal_json = NULL WHERE owner_key = ? AND json_extract(result_json, '$.atom_id') = ?",
                (row["owner_key"], row["atom_id"]),
            )
            connection.execute(
                "INSERT INTO memory_changes(owner_key, namespace, atom_id, revision, kind, occurred_at) VALUES (?, ?, ?, ?, 'forgotten', ?)",
                (row["owner_key"], row["namespace"], row["atom_id"], revision, now),
            )
        return {"atom_id": row["atom_id"], "revision": revision, "namespace": row["namespace"]}

    def _clean_forgotten_index(self, context, result):
        # Search is a disposable projection; its availability cannot undo a
        # durable forgetting receipt. A replay can retry cache cleanup.
        from .search import SearchIndex
        try:
            SearchIndex(self.runtime.data_root).remove_owner(owner_key(context))
        except (OSError, sqlite3.Error):
            return {**result, "search_projection": "cleanup_pending"}
        return result

    @outcome
    def forget(self, context, reference, *, evidence, operation_key):
        """Logical forgetting with a durable suppression marker, not physical erasure."""
        self.access.check(context, "memory.forget")
        text(operation_key, "operation_key", 512)
        with self.runtime.transaction() as connection:
            # Idempotency is checked before inspecting a now-inactive target.
            identifier = "memory-forget-" + digest([owner_key(context), self.caller(context), operation_key])
            payload_hash = digest([reference, evidence])
            old = connection.execute("SELECT * FROM memory_operations WHERE operation_id = ?", (identifier,)).fetchone()
            if old:
                if old["payload_hash"] != payload_hash:
                    raise TaskError("memory_operation_conflict", "forget operation has different content")
                result = self._receipt(old)
            else:
                row = self._head(context, reference, allow_inactive=True)
                if not evidence:
                    raise ValueError("forget requires the user's governed request")
                for item in evidence:
                    self.access.evidence(context, item, action="memory.forget")
                self._reserve(context, identifier, payload_hash)
                forgotten = self._forget_record(context, row)
                now = stamp(self.tasks.now())
                result = {"operation_id": identifier, "state": "persisted", "atom_id": reference.atom_id,
                    "revision": forgotten["revision"], "forgetting": "logical",
                    "physical_erasure": "not_performed", "delivery": "not_started"}
                connection.execute("INSERT INTO memory_operations VALUES (?, ?, ?, ?, ?, NULL, ?, 'persisted', ?, ?, ?)",
                    (identifier, owner_key(context), self.caller(context), forgotten["namespace"], payload_hash,
                     encode(context), encode(result), now, now))
                self._complete(identifier)
        return self._clean_forgotten_index(context, result)

    @outcome
    def changes(self, context, *, after=0, limit=50):
        self.access.check(context, "memory.subscribe")
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid change cursor or page size")
        rows = self.runtime.read_all("SELECT * FROM memory_changes WHERE owner_key = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                                     (owner_key(context), after, limit + 1))
        items = [{k: r[k] for k in ("sequence", "namespace", "atom_id", "revision", "kind", "occurred_at")} for r in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["sequence"] if len(rows) > limit else None}
