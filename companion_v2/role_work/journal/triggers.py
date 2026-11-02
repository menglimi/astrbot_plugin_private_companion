"""Trusted daily/sleep ingress, with durable admission before any model work.

The existing operation ledger owns date admission. Accepted or unknown operations
are never resumed implicitly, even when no child task was created before a crash.
"""

import asyncio
from dataclasses import dataclass, replace
import json

from companion_v2.execution.models import TaskError, TERMINAL, digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, RoleOwnerRef, VersionRef, normalize_utc
from companion_v2.storage.models import OperationState

from .materials import JournalMaterial, MaterialKind, source_origin
from .models import JournalContext, JournalKey, JournalKind, JournalState


@dataclass(frozen=True)
class JournalTriggerReceipt:
    operation_id: str | None
    state: str
    replayed: bool = False
    selection_task_id: str | None = None
    writing_task_id: str | None = None
    failure_code: str | None = None
    # 只有“已结论的失败”才允许显式重试。unknown/accepted/已成功都必须挡住，
    # 页面据此决定要不要把这条放进重试列表，而不是只看 state == failed。
    retryable: bool = False


@dataclass(frozen=True)
class _ChildStatus:
    task_id: str
    state: str
    effect: str
    reason: str


class JournalTriggers:
    OWNER = "role.journal.trigger"

    def __init__(self, writer, collector, *, require_running):
        if writer.runtime is not collector.runtime:
            raise ValueError("journal triggers must share their writer and collector runtime")
        self.writer, self.collector = writer, collector
        self.journal, self.runtime, self.tasks = writer.journal, writer.runtime, collector.tasks
        self.require_running = require_running
        self._invocations = set()
        self._closed = False

    def target(self, context, kind, local_date, timezone):
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        return JournalContext(JournalKey(owner, kind, local_date), context.scope, timezone)

    def authorize(self, context, event, *, role_instruction):
        if self._closed:
            raise TaskError("task_runner_closed", "journal triggers are closed")
        self.require_running()
        require(self.tasks.authorize_context(context, action="role.journal.write", observed_at=self.tasks.now()))
        if (event.scope != context.scope or event.principal != context.actor.principal
                or event.received_at > self.tasks.now()
                or (event.runtime_scope is not None and event.runtime_scope.persona != context.role_binding)):
            raise TaskError("journal_context_mismatch", "trigger must retain its authenticated private route and time")
        if not isinstance(role_instruction, str) or not role_instruction.strip() or len(role_instruction) > 65536:
            raise ValueError("journal trigger requires the original persona instruction")
        record = require(self.journal.sources.read(event.source, resolved=context.actor,
            capability="role.journal.write", observed_at=self.tasks.now(), authorizer=self.tasks.identity))
        if not set(context.audience.recipients).issubset(record.audience.recipients):
            raise TaskError("source_audience_mismatch", "trigger source is unavailable to this writing audience")
        if record.content_hash != digest(record.payload) or record.content_hash != event.input_hash:
            raise TaskError("journal_source_changed", "trigger no longer matches its authenticated source")

    async def daily(self, context, event, *, local_date, timezone, role_instruction,
                    material_ids=None, retry_of=None, retry_id=None):
        target = self.target(context, JournalKind.DIARY, local_date, timezone)
        today = JournalKey.at(target.key.owner, JournalKind.DIARY, self.tasks.now(), timezone=timezone).local_date
        if local_date > today:
            return Outcome.failed("journal_trigger_in_future", "daily writing cannot run for a future date")
        return await self._trigger(context, event, target, role_instruction, material_ids=material_ids,
                                   retry_of=retry_of, retry_id=retry_id)

    async def sleep_completed(self, context, event, *, started_at, completed_at, timezone, role_instruction,
                              material_ids=None, retry_of=None, retry_id=None):
        """A trusted life executor reports sleep; clock time or chat text never infers it."""
        start = normalize_utc(started_at, field="sleep started at")
        end = normalize_utc(completed_at, field="sleep completed at")
        if not start < end <= event.received_at <= self.tasks.now():
            return Outcome.failed("journal_sleep_time_invalid", "sleep completion must be explicitly observed in the past")
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        target = JournalContext(JournalKey.at(owner, JournalKind.DREAM, end, timezone=timezone), context.scope, timezone)
        sleep = JournalMaterial("sleep-" + event.scoped_key, MaterialKind.ACTION,
            f"可信生活执行端报告一段睡眠已结束（{start.isoformat()} 至 {end.isoformat()}）。"
            "这只确认报告了睡眠完成，不代表记得完整梦境，也没有报告其他醒后动作。",
            source_origin(event.source), target.key.local_date, event.source)
        return await self._trigger(context, event, target, role_instruction, material_ids=material_ids,
                                   extra=(sleep,), retry_of=retry_of, retry_id=retry_id)

    @staticmethod
    def _task_id(context, operation_id, phase):
        return "journal-task-" + digest([context.key, operation_id + ":" + phase])

    def _tasks_for(self, context, operation_id):
        tasks = []
        for phase in ("select", "write"):
            task_id = self._task_id(context, operation_id, phase)
            row = self.runtime.read_one("SELECT * FROM execution_tasks WHERE task_id = ?", (task_id,))
            if row is not None and row["origin_key"] != context.key:
                raise TaskError("journal_context_mismatch", "trigger task belongs to another authority")
            data = json.loads(row["record_json"]) if row is not None else None
            # Trusted accounting must settle even when the actor was revoked during generation.
            tasks.append(_ChildStatus(task_id, row["state"], data["effect"], data["reason"]) if data else None)
        return tuple(tasks)

    def _unresolved(self, tasks):
        for task in tasks:
            if task is None:
                continue
            if task.state not in TERMINAL or task.state == "uncertain" or task.effect == "unknown":
                return True
            if self.runtime.read_one("""SELECT 1 FROM model_calls WHERE task_id = ?
                    AND state IN ('reserved', 'dispatched', 'unknown') LIMIT 1""", (task.task_id,)):
                return True
        return False

    def _selection_batches(self, context, operation_id):
        rows = self.runtime.read_all("""SELECT task_id, state, record_json FROM execution_tasks
            WHERE origin_key = ? AND kind = 'role.journal.select'
            AND json_extract(record_json, '$.spec.idempotency_key') LIKE ? ORDER BY task_id""",
            (context.key, operation_id + ":select:batch:%"))
        return tuple(_ChildStatus(row["task_id"], row["state"], json.loads(row["record_json"])["effect"],
                                  json.loads(row["record_json"])["reason"]) for row in rows)

    def _receipt(self, context, operation, *, replayed=False, failure_code=None):
        selection, writing = self._tasks_for(context, operation.operation.operation_id)
        batches = self._selection_batches(context, operation.operation.operation_id)
        selection = selection or next((task for task in batches if task.state != "succeeded"), None)
        if operation.state is OperationState.UNKNOWN:
            state = "unknown"
        elif operation.state is OperationState.ACCEPTED:
            state = "accepted"
        elif writing is not None and writing.state == "succeeded":
            state = "written"
        else:
            state = "failed"
            failed = writing or selection
            failure_code = failure_code or (failed.reason if failed else "journal_preparation_failed")
        retryable = False
        if state == "failed":
            retryable = (operation.state is OperationState.COMPLETED
                         and not self._unresolved((selection, writing))
                         and not (writing is not None and writing.state == "succeeded"))
        return JournalTriggerReceipt(operation.operation.operation_id, state, replayed,
            selection.task_id if selection else None, writing.task_id if writing else None, failure_code,
            retryable)

    def inspect(self, context, target, operation_id):
        """Authorized metadata only; this never retries or returns stored prose."""
        self.require_running()
        require(self.tasks.authorize_context(context, action="role.journal.write", observed_at=self.tasks.now()))
        operation = require(self.journal.operations.get(operation_id))
        expected = self.target(context, target.key.kind, target.key.local_date, target.timezone)
        if (expected != target or operation.operation.owner != self.OWNER
                or operation.operation.scope != context.scope
                or operation.operation.version.lineage_id != digest(target.key)
                or operation.operation.idempotency_key != operation_id + ":" + context.key):
            raise TaskError("journal_context_mismatch", "trigger receipt belongs to another role or scope")
        return self._receipt(context, operation, replayed=True)

    def _admit(self, context, target, role_instruction, retry_of, retry_id):
        if (retry_of is None) != (retry_id is None):
            raise ValueError("explicit retry needs both its original operation and a new request ID")
        parameters = digest([context.key, target, role_instruction])
        if retry_of is not None:
            if not isinstance(retry_id, str) or not retry_id.strip() or len(retry_id) > 256:
                raise ValueError("retry requires a stable bounded request ID")
            original = require(self.journal.operations.get(retry_of))
            self.inspect(context, target, retry_of)
            original_tasks = self._tasks_for(context, retry_of) + self._selection_batches(context, retry_of)
            if (original.state is not OperationState.COMPLETED or self._unresolved(original_tasks)
                    or (original_tasks[1] is not None and original_tasks[1].state == "succeeded")):
                raise TaskError("journal_trigger_unresolved", "only a settled unsuccessful trigger permits an explicit retry")
        # One first attempt per role/kind/date, across schedules and private windows.
        identifier = "journal-trigger-" + digest([target.key, retry_of])
        operation = OperationRef(identifier, self.OWNER, target.scope, VersionRef(digest(target.key), 1),
                                 identifier + ":" + context.key)
        parameter_hash = digest([parameters, retry_id])
        with self.runtime.transaction():
            existing = self.runtime.read_one("SELECT 1 FROM operations WHERE operation_id = ?", (identifier,))
            if not existing:
                head = self.journal.head(target.key)
                if head.state is not JournalState.ABSENT:
                    return JournalTriggerReceipt(None, head.state.value)
            return require(self.journal.operations.reserve(operation, parameter_hash=parameter_hash,
                                                           observed_at=self.tasks.now()))

    def _settle(self, context, operation, *, interrupted=False):
        require(self.tasks.check_generation())
        unknown = interrupted or self._unresolved(self._tasks_for(context, operation.operation.operation_id)
            + self._selection_batches(context, operation.operation.operation_id))
        return require(self.journal.operations.transition(operation.operation.operation_id,
            expected_state=OperationState.ACCEPTED,
            next_state=OperationState.UNKNOWN if unknown else OperationState.COMPLETED, observed_at=self.tasks.now()))

    async def _trigger(self, context, event, target, role_instruction, *, material_ids, extra=(), retry_of, retry_id):
        operation = None
        invocation = asyncio.current_task()
        try:
            self.authorize(context, event, role_instruction=role_instruction)
            operation = self._admit(context, target, role_instruction, retry_of, retry_id)
            if isinstance(operation, JournalTriggerReceipt):
                return Outcome.success(operation)
            if operation.replayed:
                return Outcome.success(self._receipt(context, operation, replayed=True))
            self._invocations.add(invocation)
            candidates = self.collector.for_day(context, target)
            if extra:
                candidates = replace(candidates, materials=candidates.materials + extra)
            identifier = operation.operation.operation_id
            if candidates.requires_selection and material_ids is None:
                selected = require(await self.writer.select(context, event, candidates,
                    role_instruction=role_instruction, request_id=identifier + ":select"))
                if selected.run.task.state != "succeeded" or selected.material_ids is None:
                    return Outcome.success(self._receipt(context, self._settle(context, operation)))
                material_ids = selected.material_ids
            # Selection changes neither content nor references; the writer reads the original candidates.
            require(await self.writer.write_collected(context, event, candidates, role_instruction=role_instruction,
                                                      material_ids=material_ids, request_id=identifier + ":write"))
            return Outcome.success(self._receipt(context, self._settle(context, operation)))
        except asyncio.CancelledError:
            if operation is not None and not isinstance(operation, JournalTriggerReceipt) and not operation.replayed:
                try:
                    self._settle(context, operation, interrupted=True)
                except TaskError:
                    pass  # A replaced host cannot mutate the new host's ledger.
            raise
        except (TaskError, ValueError) as error:
            code = error.code if isinstance(error, TaskError) else "journal_trigger_input_invalid"
            if operation is not None and not isinstance(operation, JournalTriggerReceipt) and not operation.replayed:
                try:
                    return Outcome.success(self._receipt(context, self._settle(context, operation), failure_code=code))
                except TaskError:
                    pass
            return Outcome.failed(code, "journal trigger did not complete; inspect its original receipt")
        finally:
            self._invocations.discard(invocation)

    @property
    def active_handles(self):
        return tuple("journal-trigger-" + str(id(task)) for task in self._invocations)

    def cancel_running(self):
        for task in tuple(self._invocations):
            task.cancel()

    async def close(self):
        self._closed = True
        self.cancel_running()
        if self._invocations:
            await asyncio.wait(tuple(self._invocations), timeout=0.25)
