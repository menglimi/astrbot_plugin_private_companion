"""Idempotent, bounded receipt-to-experience production for authenticated life windows."""

from dataclasses import replace
import json

from companion_v2.execution.models import AudienceRef, TaskContext, TaskError, digest, encode, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.sources.dependencies import link
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import _source_from_row

from .activity_receipts import RoleReceiptReader, TABLES
from .models import Evidence, owner_key


OWNER = "memory.role_activity_receipt"
SCHEMA = "memory.role-activity-receipt.v1"
CURSOR_PREFIX = "memory.activity-ingestion.v2:"
TERMINAL_UNAVAILABLE = frozenset({"source_revoked", "source_expired", "source_dependency_unavailable",
    "source_dependency_expired", "journal_unavailable", "journal_dependency_changed", "memory_evidence_forgotten"})


class MemoryActivityIngestion:
    page_size = 16

    def __init__(self, memory, life_context, *, require_running=None):
        if life_context.tasks is not memory.tasks:
            raise ValueError("activity memory must retain the original life and memory task owner")
        self.memory, self.tasks, self.runtime = memory, memory.tasks, memory.runtime
        self.reader = RoleReceiptReader(life_context)
        self.require_running = require_running
        self.closed = False
        self._last_advance = {}
        self._binding_offset = 0
        self.guard = bind(self.runtime, OWNER, self._validate)

    def _active(self):
        if self.closed or self.guard.closed:
            raise TaskError("memory_activity_closed", "activity memory producer is stopped")
        if self.require_running:
            self.require_running()
        require(self.tasks.check_generation())

    def _authorize(self, context, *, write=False, at=None):
        self._active()
        at = at or self.tasks.now()
        self.memory.writer.access.check(context, "memory.propose" if write else "memory.read", at=at)
        self.reader.life.authorize(context, at)

    @staticmethod
    def _identity(context, stream, operation_id):
        return "life-experience-" + digest([owner_key(context), context.scope, stream, operation_id])

    @staticmethod
    def _payload(context, stream, operation_id, experience):
        # Only spans after this control header are offered to recall/model tools.
        header = encode({"schema": SCHEMA, "context": context, "stream": stream, "operation_id": operation_id})
        return header + "\n" + experience.text, len(header) + 1

    def _validated_context(self, stored_context, at):
        """Re-resolve live authority for a stored receipt without changing owner or role.

        Switching a private window to a new conversation scope bumps the prior
        scope's persona binding revision to fence in-flight work, while keeping
        the same human and role assigned to that historical scope. Revalidating
        against the current actor and role binding preserves revocation and
        reassignment checks without stranding already-recorded experiences.
        """
        current_actor = require(self.tasks.identity.resolve_authenticated(stored_context.actor.principal, observed_at=at))
        if current_actor.subject != stored_context.actor.subject:
            raise TaskError("binding_rebound", "original receipt belongs to another human")
        current_role = require(self.tasks.identity.resolve_role(stored_context.scope, observed_at=at))
        if current_role.role != stored_context.role_binding.role:
            raise TaskError("role_binding_changed", "original receipt belongs to another role")
        return replace(stored_context, actor=current_actor, role_binding=current_role)

    @staticmethod
    def _same_receipt_source(original, context, stream, operation_id, experience):
        if original.audience != context.audience or "\n" not in original.payload:
            return False
        header, body = original.payload.split("\n", 1)
        if body != experience.text:
            return False
        try:
            value = json.loads(header)
            if (set(value) != {"schema", "context", "stream", "operation_id"}
                    or value["schema"] != SCHEMA or value["stream"] != stream
                    or value["operation_id"] != operation_id):
                return False
            stored = TaskContext.restore(value["context"])
        except (KeyError, TypeError, ValueError):
            return False
        return (stored.actor.principal == context.actor.principal
                and stored.actor.subject == context.actor.subject
                and stored.scope == context.scope
                and stored.role_binding.role == context.role_binding.role
                and stored.audience == context.audience)

    @staticmethod
    def _same_private_window_scope(left, right):
        return (left.kind is ScopeKind.PRIVATE and right.kind is ScopeKind.PRIVATE
                and left.installation_id == right.installation_id
                and left.platform == right.platform
                and left.native_scope_id == right.native_scope_id
                and left.bot_id == right.bot_id
                and left.account_id == right.account_id
                and left.session_id == right.session_id)

    def _operation_context(self, context, op_scope, at):
        """Match same-window receipts across conversation_id rotation (e.g. V1 import -> first chat)."""
        if op_scope == context.scope:
            return context
        if not self._same_private_window_scope(context.scope, op_scope):
            return None
        resolved_persona = self.memory.writer.access.personas.resolve(op_scope, observed_at=at)
        if not resolved_persona.is_success:
            return None
        persona = resolved_persona.unwrap()
        if (persona.route.user != context.actor.subject
                or persona.binding.role != context.role_binding.role):
            return None
        resolved_actor = self.tasks.identity.resolve_authenticated(context.actor.principal, observed_at=at)
        if not resolved_actor.is_success:
            return None
        actor = resolved_actor.unwrap()
        if actor.subject != context.actor.subject:
            return None
        candidate = TaskContext(actor, persona.binding, AudienceRef(op_scope, (actor.subject,)))
        try:
            self._authorize(candidate, write=True, at=at)
        except TaskError:
            return None
        return candidate

    def _validate(self, record, at):
        try:
            header, _ = record.payload.split("\n", 1)
            value = json.loads(header)
            if set(value) != {"schema", "context", "stream", "operation_id"} or value["schema"] != SCHEMA:
                raise ValueError("invalid receipt source header")
            stored_context = TaskContext.restore(value["context"])
            validated_context = self._validated_context(stored_context, at)
            self._authorize(validated_context, at=at)
            identity = self._identity(stored_context, value["stream"], value["operation_id"])
            experience = self.reader.read(stored_context, value["stream"], value["operation_id"], at)
            if experience is None:
                raise ValueError("operation is not a completed experience")
            for source in experience.sources:
                # Memory-specific forgetting does not revoke the producer's
                # original source globally. Respect it throughout this projection.
                self.memory.writer.access.source(validated_context, source, at=at)
            reference = SourceRef(identity, OWNER, stored_context.scope, VersionRef(identity, 1), experience.occurred_at)
            payload, _ = self._payload(stored_context, value["stream"], value["operation_id"], experience)
            if (record.reference != reference or record.audience != stored_context.audience
                    or record.payload != payload or record.content_hash != digest(payload)):
                raise ValueError("original receipt source changed")
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise TaskError("memory_activity_source_changed", "original role receipt cannot attest this source") from error

    @outcome
    def record(self, context, *, stream, operation_id):
        """Trusted owner receipt IDs only; no inferred intention or model JSON input."""
        with self.runtime.transaction():
            now = self.tasks.now()
            self._authorize(context, write=True, at=now)
            experience = self.reader.read(context, stream, operation_id, now)
            if experience is None:
                return {"state": "not_completed", "view_id": None}
            identity = self._identity(context, stream, operation_id)
            reference = SourceRef(identity, OWNER, context.scope, VersionRef(identity, 1), experience.occurred_at)
            payload, offset = self._payload(context, stream, operation_id, experience)
            row = self.runtime.read_one("""SELECT * FROM sources WHERE source_id = ?
                AND lineage_id = ? AND source_revision = 1""", (identity, identity))
            if row is not None:
                original = _source_from_row(row)
                require(self.memory.writer.sources.validate_reference(original.reference, observed_at=now))
                if not self._same_receipt_source(original, context, stream, operation_id, experience):
                    raise TaskError("memory_activity_context_changed", "original experience retains its captured authority")
                payload = original.payload
                offset = original.payload.index("\n") + 1
            else:
                require(self.memory.writer.sources.publish(SourceRecord(reference, context.audience,
                    digest(payload), payload), observed_at=now))
                link(self.runtime, reference, experience.sources, now)
            statements = []
            for statement in experience.statements:
                evidence = Evidence(reference, digest(payload), offset, offset + len(statement), digest(statement),
                    context.role_binding.role.subject_id, "observation")
                statements.append({"text": statement, "evidence": (evidence,), "atoms": ()})
                offset += len(statement) + 1
            return require(self.memory.experiences.save(context, view_id=identity, expected_revision=0,
                title=experience.title, statements=tuple(statements), entities=experience.entities,
                occurred_at=experience.occurred_at, operation_key=identity))

    def _cursor_key(self, context):
        return CURSOR_PREFIX + digest([owner_key(context), context.scope])

    @outcome
    def advance(self, context):
        """Seek one finite page per stream; commit projections and cursor together.

        Reading a global rowid page before owner filtering bounds database work
        even when other roles have many records. No per-wake history-sized scan.
        A transient missing owner stops that stream at its original receipt.
        """
        with self.runtime.transaction() as connection:
            now = self.tasks.now()
            self._authorize(context, write=True, at=now)
            key = self._cursor_key(context)
            saved = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
            cursor = json.loads(saved["value"]) if saved else {stream: 0 for stream in TABLES}
            if (type(cursor) is not dict or set(cursor) != set(TABLES)
                    or any(type(value) is not int or value < 0 for value in cursor.values())):
                raise TaskError("memory_activity_cursor_invalid", "receipt ingestion cursor is invalid")
            report = {"state": "ready", "examined": 0, "saved": 0, "skipped": 0, "more": False, "blocked": []}
            role_key = digest(self.reader.life.owner(context))
            for stream, table in TABLES.items():
                rows = connection.execute(f"SELECT rowid AS scan_id, * FROM {table} WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (cursor[stream], self.page_size + 1)).fetchall()
                report["more"] |= len(rows) > self.page_size
                for row in rows[:self.page_size]:
                    report["examined"] += 1
                    if row["owner_key"] != role_key:
                        report["skipped"] += 1
                    else:
                        operation = require(self.reader.life.life.operations.get(row["operation_id"]))
                        target_context = self._operation_context(context, operation.operation.scope, now)
                        if target_context is None:
                            report["skipped"] += 1
                        else:
                            result = self.record(target_context, stream=stream, operation_id=row["operation_id"])
                            if result.is_success:
                                report["saved" if result.unwrap()["view_id"] else "skipped"] += 1
                            elif result.failure.code in TERMINAL_UNAVAILABLE or (
                                    target_context is not context and result.failure.code == "life_context_scope"):
                                report["skipped"] += 1
                            else:
                                report["blocked"].append({"stream": stream, "operation_id": row["operation_id"],
                                    "code": result.failure.code})
                                report["state"] = "blocked"
                                report["more"] = True
                                break
                    cursor[stream] = row["scan_id"]
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, encode(cursor)))
            return report

    def close(self):
        self.closed = True
        self._last_advance.clear()
        self._binding_offset = 0
        self.guard.close()

    def has_due_bindings(self, bindings) -> bool:
        """Fast O(1) readiness probe so idle ticks skip worker-thread dispatch."""
        if self.closed:
            return False
        windows = tuple(bindings)
        if not windows:
            return bool(self._last_advance)
        now = self.tasks.now()
        start, count = self._binding_offset % len(windows), min(16, len(windows))
        for index in range(count):
            context = windows[(start + index) % len(windows)][0]
            if context.scope.kind is not ScopeKind.PRIVATE:
                continue
            previous = self._last_advance.get(context.key)
            if previous is None or not (0 <= (now - previous).total_seconds() < 30):
                return True
        return False

    @outcome
    def advance_bound(self, bindings):
        """Poll at most 16 trusted bindings and at most once per window per 30 s.

        The supplied tuples belong to the host's authenticated binding owner.
        This local projection does no model, transport or live host dispatch.
        """
        self._active()
        windows = tuple(bindings)
        report = {"considered": 0, "advanced": 0, "saved": 0, "blocked": []}
        if not windows:
            self._last_advance.clear()
            self._binding_offset = 0
            return report
        keys = {window[0].key for window in windows}
        self._last_advance = {key: at for key, at in tuple(self._last_advance.items()) if key in keys}
        now = self.tasks.now()
        start, count = self._binding_offset % len(windows), min(16, len(windows))
        self._binding_offset = (start + count) % len(windows)
        for index in range(count):
            context = windows[(start + index) % len(windows)][0]
            report["considered"] += 1
            if context.scope.kind is not ScopeKind.PRIVATE:
                continue
            previous = self._last_advance.get(context.key)
            if previous is not None and 0 <= (now - previous).total_seconds() < 30:
                continue
            self._last_advance[context.key] = now
            result = self.advance(context)
            report["advanced"] += 1
            if result.is_success:
                value = result.unwrap()
                report["saved"] += value["saved"]
                report["blocked"].extend(value["blocked"])
            else:
                self._active()
                report["blocked"].append({"context_key": context.key, "code": result.failure.code})
        return report
