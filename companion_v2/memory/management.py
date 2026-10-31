"""Core administration records the actual operator separately from memory subject."""

from dataclasses import replace
import json

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.execution.service import outcome
from companion_v2.configuration.contract import contract_of, layers_of, provenance_of
from companion_v2.configuration.models import ConfigLayer
from companion_v2.foundation.refs import OperationRef, SourceRef, VersionRef
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.privacy import redact_text, redact_fields

from . import codec
from .models import AtomRef, Evidence, owner_key, text
from .writer import MemoryWriter
from .configuration import GROUP, role_target_for, target_for, validate_schedule


class MemoryManagement:
    def __init__(self, memory, operator):
        self.memory, self.operator = memory, operator
        self.writer = MemoryWriter(memory.tasks, policy=memory.policy, operator=operator, settings=memory.settings)
        self.writer.access.sent_sources = memory.writer.access.sent_sources

    def token(self, context, reference):
        return digest([context.key, self.operator, reference, self.writer.access.access.revision])

    @staticmethod
    def _settings_target(context, layer):
        if layer == ConfigLayer.ROLE.value:
            return role_target_for(context)
        if layer == ConfigLayer.WINDOW.value:
            return target_for(context)
        raise TaskError("memory_configuration_scope_invalid", "choose role defaults or this private window")

    @outcome
    def settings(self, context, *, layer=ConfigLayer.ROLE.value):
        target = self._settings_target(context, layer)
        if target.layer is ConfigLayer.WINDOW:
            self.writer.access.check(context)
        if self.memory.settings is None:
            raise TaskError("memory_configuration_unavailable", "memory configuration was not registered by this host")
        configuration = self.memory.settings.configuration
        snapshot = require(configuration.read(self.operator, target=target, group_id=GROUP, observed_at=self.writer.tasks.now()))
        # ``layers``/``provenance``/``fields`` are the same read contract every other
        # config section publishes, so the shared workbench can draw its layer rail
        # here too. ``inheritance`` is kept because the page already renders it.
        # Short names only, like ``GroupEditor._settings``: ``contract_of`` and
        # ``provenance_of`` both strip the group prefix, so a long-form ``values``
        # map would match nothing and render an empty card. ``validate_schedule``
        # and ``MemorySettings.read`` keep the qualified ids on the backend side.
        prefix = GROUP + "."
        return {"values": {key.removeprefix(prefix): value for key, value in snapshot.values.items()
                           if key.startswith(prefix)},
                "edit_token": snapshot.edit_token, "revision": snapshot.revision,
                "target": snapshot.target.document(),
                "inheritance": {key.removeprefix(prefix): value.source.document() if value.source else None
                                for key, value in snapshot.fields.items() if key.startswith(prefix)},
                "layers": layers_of(target),
                "provenance": provenance_of(snapshot, GROUP),
                "fields": contract_of(configuration.registry, GROUP),
                "scope": target.layer.value,
                "application": "read_on_use", "reflection": "scheduled_after_authenticated_conversation"}

    @outcome
    def patch_settings(self, context, *, operation_key, expected_edit_token, changes,
                       reset_to_inherit=(), layer=ConfigLayer.ROLE.value):
        target = self._settings_target(context, layer)
        if target.layer is ConfigLayer.WINDOW:
            self.writer.access.check(context)
        text(operation_key, "operation_key", 512)
        if type(changes) is not dict or not isinstance(reset_to_inherit, (tuple, list)):
            raise ValueError("settings require explicit changes and inherited-field resets")
        require(self.settings(context, layer=layer))
        configuration = self.memory.settings.configuration
        identifier = "memory-config-" + digest([target.key, self.operator.subject, operation_key])
        operation = OperationRef(identifier, "configuration", target.authorization_scope, VersionRef(identifier, 1), identifier)
        # ``changes``/``reset_to_inherit`` stay fully qualified here, matching the
        # ``memory.`` ids ``ConfigurationService._normalize_patch`` keys its registry
        # by (and the memory host API's own tests). ``settings()`` publishes short
        # names for the shared workbench, so mapping between the two is the sending
        # page's job — ``MemoryManagement`` does not speak both dialects.
        with self.writer.runtime.transaction():
            receipt = require(configuration.patch(self.operator, target=target, group_id=GROUP, operation=operation,
                expected_edit_token=expected_edit_token, changes=changes, reset_to_inherit=tuple(reset_to_inherit), observed_at=self.writer.tasks.now()))
            values, _ = self.memory.settings.read(context)
            validate_schedule(values)
            return {"operation_id": receipt.operation_id, "revision": receipt.revision, "replayed": receipt.replayed,
                    "changed_fields": receipt.changed_fields, "application": "read_on_use"}

    @outcome
    def list(self, context, *, after_id="", limit=20):
        self.writer.access.check(context)
        text(after_id, "after_id", 512, empty=True)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid memory page size")
        items = []
        for row in self.writer.runtime.read_all("SELECT * FROM memory_atoms WHERE owner_key = ? AND atom_id > ? ORDER BY atom_id LIMIT ?",
                                               (owner_key(context), after_id, limit + 1)):
            ref = AtomRef(row["atom_id"], row["revision"])
            read = self.writer.read(context, ref)
            item = read.unwrap() if read.is_success else {"atom_id": ref.atom_id, "revision": ref.revision,
                "state": row["state"], "representation": None, "unavailable_reason": read.failure.code}
            item.pop("evidence", None)
            item.pop("source", None)
            item["view_token"] = self.token(context, ref)
            items.append(item)
        return {"items": items[:limit], "next_cursor": items[limit - 1]["atom_id"] if len(items) > limit else None,
            "operator_id": self.writer.caller(context), "subject_id": context.actor.subject.subject_id,
            "supported_visibility": ["private"], "physical_erasure": "not_supported"}

    @outcome
    def overview(self, context):
        """Return counts for this authorized relationship without exposing memory text."""
        self.writer.access.check(context)
        owner = owner_key(context)
        facts = {row["state"]: row["count"] for row in self.writer.runtime.read_all(
            "SELECT state, COUNT(*) AS count FROM memory_atoms WHERE owner_key = ? GROUP BY state", (owner,))}
        experiences = {row["state"]: row["count"] for row in self.writer.runtime.read_all(
            "SELECT state, COUNT(*) AS count FROM memory_views WHERE owner_key = ? AND kind = 'experience' GROUP BY state",
            (owner,))}
        reflections = {row["state"]: row["count"] for row in self.writer.runtime.read_all(
            """SELECT state, COUNT(*) AS count FROM memory_reflections WHERE owner_key = ?
                AND json_remove(context_json, '$.actor.policy_revision') = json_remove(?, '$.actor.policy_revision')
                GROUP BY state""", (owner, encode(context)))}
        scope = context.scope
        # Two writers record an authorized conversation input for memory: the host
        # event adapter publishes ``conversation.host_input`` and the native core
        # observation path publishes ``conversation.native_input``. Counting only
        # one owner reports zero for windows that the other writer serves, so the
        # overview counts both and deduplicates by source id.
        sources = self.writer.runtime.read_one(
            """SELECT COUNT(DISTINCT s.source_id) AS count FROM sources s
                WHERE s.owner IN ('conversation.host_input', 'conversation.native_input')
                AND s.state = 'active'
                AND (s.expires_at IS NULL OR s.expires_at > ?)
                AND s.installation_id = ? AND s.platform = ? AND s.scope_kind = ? AND s.native_scope_id = ?
                AND s.bot_id IS ? AND s.account_id IS ? AND s.conversation_id IS ? AND s.session_id IS ?
                AND EXISTS (SELECT 1 FROM json_each(s.recipients_json) recipient
                    WHERE json_extract(recipient.value, '$.kind') = ?
                    AND json_extract(recipient.value, '$.subject_id') = ?)""",
            (stamp(self.writer.tasks.now()), scope.installation_id, scope.platform, scope.kind.value,
             scope.native_scope_id, scope.bot_id, scope.account_id, scope.conversation_id, scope.session_id,
             context.actor.subject.kind.value, context.actor.subject.subject_id))
        return {
            "facts": {"total": sum(facts.values()), "active": facts.get("active", 0),
                      "archived": facts.get("archived", 0), "by_state": facts, "other": sum(
                          count for state, count in facts.items() if state not in {"active", "archived"})},
            "experiences": {"total": sum(experiences.values()), "active": experiences.get("active", 0),
                            "by_state": experiences},
            "sources": {"available_messages": sources["count"]},
            "reflections": {"total": sum(reflections.values()), "by_state": reflections},
        }

    def _clear_token(self, context, rows):
        # 聊天内自证（operator 为空）与管理页（operator 为管理员）必须对同一份
        # 预览算出同一个确认令牌；caller 在两种身份下都由 writer 统一解析。
        return digest([
            owner_key(context), self.writer.caller(context), self.writer.access.access.revision,
            [(row["atom_id"], row["revision"], row["namespace"], row["state"]) for row in rows],
        ])

    @staticmethod
    def confirmation_code(token):
        """Short code a chat-side confirmation must echo for this exact preview."""
        return digest(["memory-clear-confirm.v1", token])[:6].upper()

    @outcome
    def preview_clear(self, context):
        """Preview this relationship's fact archive; other domains keep their owners."""
        self.writer.access.check(context, "memory.forget")
        owner = owner_key(context)
        rows = self.writer.runtime.read_all(
            "SELECT atom_id, revision, namespace, state FROM memory_atoms WHERE owner_key = ? ORDER BY atom_id",
            (owner,),
        )
        return {"owner_id": owner, "subject_id": context.actor.subject.subject_id,
                "operator_id": self.writer.caller(context),
                "counts": {"active": sum(row["state"] == "active" for row in rows),
                           "already_inactive": sum(row["state"] != "active" for row in rows)},
                "revision_token": self._clear_token(context, rows),
                "preview_only": True, "scope": "private_relationship_facts_and_revisions"}

    @outcome
    def clear(self, context, *, expected_revision_token, operation_key):
        self.writer.access.check(context, "memory.forget")
        expected_revision_token = text(expected_revision_token, "expected_revision_token", 512)
        operation_key = text(operation_key, "operation_key", 512)
        owner = owner_key(context)
        caller = self.writer.caller(context)
        identifier = "memory-clear-" + digest([owner, caller, operation_key])
        payload_hash = digest(["management.clear", expected_revision_token])
        result = None
        with self.writer.runtime.transaction() as connection:
            self.writer.access.check(context, "memory.forget")
            existing = connection.execute("SELECT * FROM memory_operations WHERE operation_id = ?", (identifier,)).fetchone()
            if existing:
                if existing["owner_key"] != owner or existing["caller_id"] != caller:
                    raise TaskError("memory_operation_unavailable", "the original clear operation is unavailable")
                if existing["payload_hash"] != payload_hash:
                    raise TaskError("memory_operation_conflict", "original clear operation has a different preview")
                result = json.loads(existing["result_json"])
            else:
                rows = connection.execute(
                    "SELECT * FROM memory_atoms WHERE owner_key = ? ORDER BY atom_id", (owner,)
                ).fetchall()
                current_token = self._clear_token(context, rows)
                if current_token != expected_revision_token:
                    raise TaskError("memory_clear_stale", "refresh the memory clear preview before confirming")

                now = stamp(self.writer.tasks.now())
                changed = []
                for row in rows:
                    if json.loads(row["record_json"]).get("forgotten"):
                        continue
                    changed.append(self.writer._forget_record(context, row))

                body = encode({
                    "operation": "clear",
                    "subject": context.actor.subject.subject_id,
                    "count": len(changed),
                })
                source_id = "memory-clear-source:" + digest([owner, caller, operation_key])
                source = SourceRef(
                    source_id,
                    "management.memory",
                    context.scope,
                    VersionRef(identifier, 1),
                    self.writer.tasks.now(),
                )
                safe = self.writer.sources.prepare_message(
                    SourceRecord(source, context.audience, digest(body), body)
                )
                require(self.writer.sources.publish(safe, observed_at=self.writer.tasks.now()))
                self.writer._reserve(context, identifier, payload_hash)
                result = {
                    "operation_id": identifier,
                    "state": "persisted",
                    "forgotten_count": len(changed),
                    "items": changed,
                    "forgetting": "logical",
                    "physical_erasure": "not_performed",
                    "scope": "private_relationship_facts_and_revisions",
                }
                connection.execute(
                    "INSERT INTO memory_operations VALUES (?, ?, ?, ?, ?, NULL, ?, 'persisted', ?, ?, ?)",
                    (identifier, owner, caller, "management.clear", payload_hash, encode(context),
                     encode(result), now, now),
                )
                self.writer._complete(identifier)

        return self.writer._clean_forgotten_index(context, result)

    @outcome
    def revisions(self, context, *, atom_id, after_revision=0, limit=32):
        self.writer.access.check(context)
        text(atom_id, "atom_id", 512)
        if type(after_revision) is not int or after_revision < 0 or type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("invalid revision page")
        with self.writer.runtime.read_snapshot():
            self.writer.access.check(context)
            active = self.writer.runtime.read_one("SELECT owner_key FROM memory_atoms WHERE atom_id = ?", (atom_id,))
            if active is None or active["owner_key"] != owner_key(context):
                raise TaskError("memory_unavailable", "memory is unavailable to this relationship")
            rows = self.writer.runtime.read_all(
                "SELECT * FROM memory_revisions WHERE atom_id = ? AND revision > ? ORDER BY revision LIMIT ?",
                (atom_id, after_revision, limit + 1),
            )
            items = [self._revision(context, row) for row in rows[:limit]]
            return {"items": items, "next_revision": items[-1]["revision"] if len(rows) > limit else None}

    def _revision(self, context, row):
        metadata = {"revision": row["revision"], "state": row["state"], "content": None}
        document = json.loads(row["record_json"])
        if row["state"] == "invalidated" or document.get("forgotten"):
            return metadata
        proposal = codec.proposal(document["proposal"])
        try:
            source = codec.source(json.loads(row["source_json"]))
            if proposal.retention == "session" and source.scope != context.scope:
                raise TaskError("memory_session_mismatch", "session memory belongs to its original session")
            # Supersession revokes the derived fact version. Historical review
            # may show it only while its original evidence is still authorized.
            for evidence in proposal.evidence:
                self.writer.access.evidence(context, evidence)
            for dependency in proposal.dependencies:
                require(self.writer.read(context, dependency))
            if row["state"] == "active":
                require(self.writer.sources.validate_reference(source, observed_at=self.writer.tasks.now()))
        except TaskError as error:
            return {**metadata, "unavailable_reason": error.code}
        return {"revision": row["revision"], "state": row["state"], "representation": proposal.representation,
                "object": proposal.object, "predicate": proposal.predicate, "polarity": proposal.polarity,
                "retention": proposal.retention, "context_mode": proposal.context_mode,
                "observed_at": document.get("observed_at"), "attribution": proposal.attribution}

    @outcome
    def command(self, context, *, atom_id, expected_revision, view_token, operation_key, action, value=None):
        self.writer.access.check(context)
        ref = AtomRef(atom_id, expected_revision)
        if view_token != self.token(context, ref):
            raise TaskError("memory_view_stale", "refresh the memory view after identity or permission changes")
        if action not in ("correct", "retract", "forget", "pin", "unpin"):
            raise ValueError("unsupported management command")
        text(operation_key, "operation_key", 512)
        if action == "correct":
            if type(value) is not dict or set(value) != {"object", "representation"}:
                raise ValueError("correction requires object and representation")
            for key in value:
                text(value[key], key, 8192)
            value = {key: redact_text(item) for key, item in value.items()}
        elif value is not None:
            raise ValueError("this command takes no replacement text")
        with self.writer.runtime.transaction():
            command_id = "memory-admincmd-" + digest([owner_key(context), self.operator.subject, operation_key])
            payload_hash = digest([action, ref, value])
            existing = self.writer.runtime.read_one("SELECT * FROM memory_operations WHERE operation_id = ?", (command_id,))
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise TaskError("memory_operation_conflict", "the management command key already has different content")
                return self.writer._receipt(existing)
            original = self.writer._head(context, ref, allow_inactive=action == "forget")
            document = json.loads(original["record_json"])
            if document.get("forgotten"):
                raise TaskError("memory_forgotten", "memory is already forgotten")
            prior = codec.proposal(document["proposal"])
            if action == "correct":
                value = {**value, "object": redact_fields({prior.predicate: value["object"]})[redact_text(prior.predicate)]}
            # This is an administrator's actual command, not a fabricated user message.
            body = json.dumps({"action": action, "target": atom_id, "revision": expected_revision,
                "operator_id": self.operator.subject.subject_id, "value": value}, ensure_ascii=False)
            body = redact_text(body)
            source_id = "memory-management-" + digest([owner_key(context), self.operator.subject, operation_key])
            now = self.writer.tasks.now()
            source = SourceRef(source_id, "management.memory", context.scope, VersionRef(source_id, 1), now)
            require(self.writer.sources.publish(SourceRecord(source, context.audience, digest(body), body), observed_at=now))
            evidence = Evidence(source, digest(body), 0, len(body), digest(body), self.operator.subject.subject_id, "source")
            if action == "forget":
                result = require(self.writer.forget(context, ref, evidence=(evidence,), operation_key=operation_key))
            elif action in ("pin", "unpin"):
                # Changing presentation does not replace the fact's original evidence.
                require(self.writer.read(context, ref))
                proposed = replace(prior, operation="correct", target=ref,
                    evidence=tuple(dict.fromkeys((*prior.evidence, evidence))),
                    context_mode="pinned" if action == "pin" else "relevant",
                    defer_until_boundary=False, reason="Explicit memory context selection")
                result = require(self.writer.submit(context, proposed, operation_key=operation_key))
            else:
                proposed = replace(prior, operation=action, target=ref, evidence=(evidence,), attribution="third_party",
                    defer_until_boundary=False, reason="Explicit administrator correction" if action == "correct" else "Explicit administrator retraction",
                    **(value or {}))
                result = require(self.writer.submit(context, proposed, operation_key=operation_key))
            # The command and its authoritative receipt commit atomically. Replays
            # must return it before requiring the target's now obsolete revision.
            with self.writer.runtime.transaction() as connection:
                connection.execute("INSERT INTO memory_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (command_id, owner_key(context), self.writer.caller(context), "management", payload_hash, "{}",
                     encode(context), "persisted", encode(result), stamp(now), stamp(now)))
            return result
