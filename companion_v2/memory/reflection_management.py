"""Core administration of captured reflection failures, without raw-text exposure."""

import json

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.execution.service import outcome

from .management import MemoryManagement
from .models import owner_key, text


class ReflectionManagement:
    def __init__(self, application, operator):
        self.reflection = application.memory_reflection
        self.continuations = application.memory_schedule.continuations
        self.manager = MemoryManagement(application.memory, operator)

    def _row(self, context, batch_id):
        self.manager.writer.access.check(context)
        text(batch_id, "batch_id", 256)
        row = self.reflection.runtime.read_one("SELECT * FROM memory_reflections WHERE batch_id = ? AND owner_key = ?",
                                                (batch_id, owner_key(context)))
        if row is None or TaskContext.restore(json.loads(row["context_json"])).key != context.key:
            raise TaskError("memory_reflection_unavailable", "reflection belongs to another authenticated context")
        return row

    @outcome
    def list(self, context, *, batch_id=None, limit=20, after_id="", history=False):
        self.manager.writer.access.check(context)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid reflection queue limit")
        if type(history) is not bool:
            raise ValueError("invalid reflection history mode")
        text(after_id, "after_id", 256, empty=True)
        if batch_id is not None and after_id:
            raise ValueError("select either a reflection or a queue page")
        if batch_id is not None:
            row = self._row(context, batch_id)
            return self.reflection.describe(context, row, self.manager.writer.access)
        with self.reflection.runtime.read_snapshot():
            self.manager.writer.access.check(context)
            # The anchor survives completion/linking. Current authority and the
            # original account, role and audience are checked on every page.
            anchor = self._row(context, after_id) if after_id else None
            history_clause = "" if history else (
                "AND state NOT IN ('complete', 'empty', 'abandoned') "
                "AND json_extract(receipts_json, '$.repair_batch') IS NULL "
            )
            rows = self.reflection.runtime.read_all("""SELECT * FROM memory_reflections
                WHERE owner_key = ? AND json_remove(context_json, '$.actor.policy_revision') = json_remove(?, '$.actor.policy_revision')
                """ + history_clause + """AND (created_at, batch_id) > (?, ?)
                ORDER BY created_at, batch_id LIMIT ?""",
                (owner_key(context), encode(context), anchor["created_at"] if anchor else "", after_id, limit + 1))
            return {"items": [self.reflection.describe(context, row, self.manager.writer.access) for row in rows[:limit]],
                    "next_cursor": rows[limit - 1]["batch_id"] if len(rows) > limit else None,
                    "has_more": len(rows) > limit, "scope": "private_memory_reflections",
                    "operator_id": self.manager.operator.subject.subject_id,
                    "view": "history" if history else "pending"}

    @outcome
    def prepare_retry(self, context, *, batch_id, operation_key):
        row = self._row(context, batch_id)
        text(operation_key, "operation_key", 256)
        request_id = "memory-management-repair:" + digest([self.manager.operator.subject, context.key, batch_id, operation_key])
        repair_id = "memory-reflection-" + digest([owner_key(context), request_id])
        existing = self.reflection.runtime.read_one("SELECT 1 FROM memory_reflections WHERE batch_id = ? AND owner_key = ?",
                                                   (repair_id, owner_key(context)))
        if not existing and row["state"] in ("selecting", "prepared", "received", "modelled", "complete", "empty"):
            return {"batch_id": row["batch_id"], "state": row["state"]}
        prepared = require(self.reflection.retry(context, row["batch_id"], request_id=request_id))
        return prepared

    async def run(self, context, batch_id):
        self._row(context, batch_id)
        return await self.reflection.run(context, batch_id)

    @outcome
    def discard(self, context, *, batch_id, operation_key):
        """Close a failed/partial batch without deleting receipts or saved facts.

        Repair keeps re-reading the same old manifest; when the model cannot
        complete it, the user needs a durable way to stop seeing the same stuck
        item.  ``abandoned`` is terminal for the queue but the batch remains in
        history with its receipts.
        """
        row = self._row(context, batch_id)
        text(operation_key, "operation_key", 256)
        if row["state"] in ("complete", "empty", "abandoned"):
            return {"batch_id": row["batch_id"], "state": row["state"], "discarded": False}
        if row["state"] not in ("failed", "partial"):
            raise TaskError("memory_reflection_running", "only a failed or partial batch can be discarded")
        with self.reflection.runtime.transaction() as connection:
            result = json.loads(row["result_json"] or "{}")
            result.setdefault("issues", []).append({"code": "memory_reflection_discarded", "path": "$"})
            changed = connection.execute(
                """UPDATE memory_reflections SET state = 'abandoned', result_json = ?, updated_at = ?
                   WHERE batch_id = ? AND owner_key = ? AND state IN ('failed', 'partial')""",
                (encode(result), stamp(self.reflection.tasks.now()), batch_id, owner_key(context)))
            if changed.rowcount != 1:
                raise TaskError("memory_reflection_changed", "reflection changed during discard")
        return {"batch_id": batch_id, "state": "abandoned", "discarded": True}
        with self.reflection.runtime.transaction():
            self._row(context, batch_id)
            text(operation_key, "operation_key", 256)
            request_id = digest([batch_id, operation_key])
            existing = self.continuations.receipt(context, self.manager.operator, request_id)
            if existing is not None:
                return existing
            self.continuations._active()
            prepared = require(self.prepare_retry(context, batch_id=batch_id, operation_key=operation_key))
            return self.continuations.enqueue(context, self.manager.operator, batch_id=prepared["batch_id"],
                request_id=request_id)
