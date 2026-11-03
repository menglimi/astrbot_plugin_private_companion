"""Cooperative local async workers with bounded cancellation and tracked handles.

Handlers must use cancellable async I/O and own/await their children. Blocking or
untrusted plugin code needs process isolation and is not supported by this runner.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import inspect
from logging import getLogger
from time import perf_counter
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping

from companion_v2.foundation.outcome import Outcome

from .models import Lane, QUEUED, TaskClaim, TaskDeferred, TaskError, TaskRecord, TaskResult, require
from .service import TaskSupervisor


logger = getLogger(__name__)

# Refusals that name physical capacity, not a property of the row: the queue
# simply could not hand this attempt a slot *yet*. Every other refusal code
# describes the row itself (`task_not_queued`, `task_not_due`, ...) and means
# the reservation is unusable for this owner. A runner that keeps capacity
# refusals opts in with ``retain_refusals=True``; see ``_unrun``.
CAPACITY_REFUSALS = frozenset({"task_lane_full", "task_concurrency_limited"})


@dataclass(frozen=True)
class RunResult:
    """What one run attempt produced.

    ``task`` is the authoritative record afterwards. ``failure_code`` is the
    stable code when the attempt produced no owner result, and ``retry_at``
    marks the subset that a fresh owner task may attempt again later.

    ``refused`` names the queue refusal that stopped this attempt. Neither is
    set when a claim was produced, so the common case is ``task`` alone. A
    runner that retains capacity refusals reports ``task`` *and* ``refused``
    together: nothing ran, the row is still queued exactly as submitted, and
    the owner must decide whether to retry it by ID.
    """

    task: TaskRecord | None
    failure_code: str | None = None
    retry_at: datetime | None = None
    refused: str | None = None

    @property
    def stop_code(self) -> str | None:
        """Return the stable code that ended this attempt, or None when it succeeded.

        Owners of a bounded child read this instead of the outer outcome: a
        handler error is reported as a successful `Outcome` carrying the code
        here, so `Outcome.failure` alone would miss every model-level stop.
        """
        if self.failure_code:
            return self.failure_code
        if self.task is None:
            return self.refused or "task_not_admitted"
        return None if self.task.state == "succeeded" else (self.task.reason or "task_stopped")


@dataclass(frozen=True)
class TaskRun:
    supervisor: TaskSupervisor
    claim: TaskClaim
    task: TaskRecord

    def validate(self) -> TaskRecord:
        return require(self.supervisor.validate(self.claim))

    def begin_effect(self) -> TaskRecord:
        return require(self.supervisor.begin_effect(self.claim))

    def record_operation(self, operation_id: str) -> TaskRecord:
        return require(self.supervisor.record_operation(self.claim, operation_id))


class _TaskRunner:
    def __init__(self, supervisor: TaskSupervisor,
                 handlers: Mapping[str, Callable[[TaskRun], Awaitable[TaskResult]]], *,
                 check_interval: float = 0.05, stop_grace: float = 0.1):
        if not 0.001 <= check_interval <= 1 or not 0 <= stop_grace <= 1:
            raise ValueError("runner cancellation intervals must be bounded")
        if (not handlers or not set(handlers).issubset(supervisor.registrations)
                or not all(inspect.iscoroutinefunction(h) for h in handlers.values())):
            raise ValueError("handlers must be registered trusted asynchronous functions")
        self.supervisor = supervisor
        self.handlers = MappingProxyType(dict(handlers))
        self.check_interval = check_interval
        self.stop_grace = stop_grace
        self._handles: dict[str, tuple[TaskClaim, asyncio.Task]] = {}
        self._abandoned: dict[str, str] = {}
        self._closed = False

    @property
    def active_handles(self) -> tuple[str, ...]:
        return tuple(self._handles)

    def _stopped(self, claim: TaskClaim, reason: str):
        stopped = self.supervisor.stop(claim, reason=reason)
        if stopped.failure:
            # A replaced controller may accept stop evidence, but never our result.
            return self.supervisor.acknowledge_stopped(claim, proof_ref="supervised_coroutine_stopped")
        return stopped

    def _done(self, claim: TaskClaim, worker: asyncio.Task):
        if not worker.cancelled():
            worker.exception()
        reason = self._abandoned.pop(claim.attempt_id, None)
        if reason:
            self._stopped(claim, reason)
        self._handles.pop(claim.attempt_id, None)

    async def _abort(self, claim, worker, reason):
        self._abandoned[claim.attempt_id] = reason
        abandon_reason = "runner_cancelled" if reason == "cancelled" else reason
        abandoned = self.supervisor.abandon(claim, reason=abandon_reason)
        worker.cancel()
        await asyncio.wait((worker,), timeout=self.stop_grace)
        if worker.done():
            self._abandoned.pop(claim.attempt_id, None)
            return self._stopped(claim, reason)
        return abandoned

    async def _run(self, select) -> Outcome[RunResult]:
        if self._closed:
            return Outcome.failed("task_runner_closed", "runner no longer accepts work")
        claim = None
        worker = None
        try:
            claim_result = require(select())
            claim = claim_result.claim
            if claim is None:
                return Outcome.success(RunResult(None, refused=claim_result.refused))
            task = require(self.supervisor.begin(claim))
            definition = self.supervisor.registrations[task.spec.kind].definition
            budget = min(definition.timeout_seconds, (task.spec.deadline_at - self.supervisor.now()).total_seconds())
            started = perf_counter()
            worker = asyncio.create_task(self.handlers[task.spec.kind](TaskRun(self.supervisor, claim, task)),
                                         name=f"companion:{task.spec.kind}:{task.task_id}:{task.attempt_no}")
            self._handles[claim.attempt_id] = (claim, worker)
            worker.add_done_callback(lambda done: self._done(claim, done))
            while True:
                remaining = budget - (perf_counter() - started)
                if remaining <= 0:
                    return Outcome.success(RunResult(require(await self._abort(claim, worker, "timeout"))))
                done, _ = await asyncio.wait((worker,), timeout=min(self.check_interval, remaining))
                if done:
                    if worker.cancelled():
                        return Outcome.success(RunResult(require(self._stopped(claim, "cancelled"))))
                    try:
                        result = worker.result()
                    except (Exception, asyncio.CancelledError) as error:
                        code = error.code if isinstance(error, TaskError) else "unhandled_exception"
                        if isinstance(error, TaskDeferred):
                            logger.info("[CompanionV2] 后台任务等待本地预算 task=%s kind=%s code=%s retry_at=%s",
                                        task.task_id, task.spec.kind, code, error.retry_at.isoformat())
                            return Outcome.success(RunResult(require(self._stopped(claim, "worker_error")),
                                                             code, error.retry_at))
                        logger.error("[CompanionV2] 后台任务执行失败 task=%s kind=%s code=%s error_type=%s",
                                     task.task_id, task.spec.kind, code, type(error).__name__)
                        logger.debug("[CompanionV2][开发者追踪] 后台任务异常 task=%s kind=%s code=%s",
                                     task.task_id, task.spec.kind, code, exc_info=True)
                        return Outcome.success(RunResult(require(self._stopped(claim, "worker_error")), code))
                    if not isinstance(result, TaskResult):
                        return Outcome.success(RunResult(require(self._stopped(claim, "worker_error")),
                                                         "worker_result_invalid"))
                    finished = self.supervisor.finish(claim, result, execution_ms=(perf_counter() - started) * 1000)
                    if finished.failure:
                        reason = "timeout" if finished.failure.code == "task_attempt_expired" else "permission_changed"
                        return Outcome.success(RunResult(require(self._stopped(claim, reason))))
                    return Outcome.success(RunResult(finished.unwrap()))
                valid = self.supervisor.validate(claim)
                if valid.failure:
                    reason = ("cancelled" if valid.failure.code == "task_cancellation_requested" else
                              "timeout" if valid.failure.code == "task_attempt_expired" else "permission_changed")
                    return Outcome.success(RunResult(require(await self._abort(claim, worker, reason))))
        except asyncio.CancelledError:
            if claim is not None and worker is not None:
                await self._abort(claim, worker, "runner_cancelled")
            raise
        except TaskError as error:
            logger.warning("[CompanionV2] 后台任务启动失败 task=%s code=%s",
                           claim.task_id if claim is not None else "unclaimed", error.code)
            logger.debug("[CompanionV2][开发者追踪] 后台任务启动异常 task=%s code=%s",
                         claim.task_id if claim is not None else "unclaimed", error.code, exc_info=True)
            if claim is not None and worker is None:
                self.supervisor.abandon(claim, reason="permission_changed")
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logger.error("[CompanionV2] 后台任务调度失败 task=%s error_type=%s",
                         claim.task_id if claim is not None else "unclaimed", type(error).__name__)
            logger.debug("[CompanionV2][开发者追踪] 后台任务调度异常 task=%s error_type=%s",
                         claim.task_id if claim is not None else "unclaimed", type(error).__name__, exc_info=True)
            if claim is not None:
                if worker is None:
                    self.supervisor.abandon(claim, reason="worker_error")
                else:
                    await self._abort(claim, worker, "worker_error")
            return Outcome.failed("task_runner_error", "local execution failed; private error details omitted")

    async def close(self) -> tuple[str, ...]:
        """Return still-running handles after a bounded stop; retain their quota slots."""
        self._closed = True
        handles = tuple(self._handles.values())
        for claim, worker in handles:
            self._abandoned[claim.attempt_id] = "runner_cancelled"
            self.supervisor.abandon(claim, reason="runner_cancelled")
            worker.cancel()
        if handles:
            await asyncio.wait(tuple(worker for _, worker in handles), timeout=self.stop_grace)
        return self.active_handles


class LocalTaskRunner(_TaskRunner):
    def __init__(self, supervisor, handlers, *, check_interval=0.05, stop_grace=0.1):
        if set(handlers) != set(supervisor.registrations):
            raise ValueError("every registered task requires a trusted asynchronous handler")
        super().__init__(supervisor, handlers, check_interval=check_interval, stop_grace=stop_grace)

    async def run_once(self, lane: Lane) -> Outcome[RunResult]:
        return await self._run(lambda: self.supervisor.claim(lane))


class ExactTaskRunner(_TaskRunner):
    """Run one submitted child; share cancellation/fencing semantics with queue workers."""

    def __init__(self, supervisor, kinds: frozenset[str], handler, *, check_interval=0.05, stop_grace=0.1,
                 waiting=None, retain_refusals=False):
        super().__init__(supervisor, {kind: handler for kind in kinds},
                         check_interval=check_interval, stop_grace=stop_grace)
        if waiting is not None and waiting.supervisor is not supervisor:
            raise ValueError("waiting must use the original task supervisor")
        if type(retain_refusals) is not bool:
            raise ValueError("retained refusals must be an explicit boolean choice")
        if waiting is not None:
            waiting.register(kinds)
        self.waiting, self._waiting = waiting, {}
        self.retain_refusals = retain_refusals

    @property
    def active_handles(self):
        return super().active_handles + tuple("waiting:" + task_id for task_id in self._waiting.values())

    async def run(self, context, task_id: str) -> Outcome[RunResult]:
        if self._closed:
            return Outcome.failed("task_runner_closed", "runner no longer accepts work")
        current = self.supervisor.read(context, task_id)
        if current.failure:
            return Outcome(failure=current.failure)
        if current.unwrap().spec.kind not in self.handlers:
            return Outcome.failed("task_kind_mismatch", "exact runner does not own this task kind")
        if self.waiting is None:
            result = await self._run(lambda: self.supervisor.claim_task(context, task_id))
        else:
            invocation = asyncio.current_task()
            self._waiting[invocation] = task_id
            try:
                selected = await self.waiting.claim(context, task_id)
            finally:
                self._waiting.pop(invocation, None)
            result = await self._run(lambda: selected)
        if result.is_success and result.unwrap().task is None:
            return self._unrun(context, task_id, result.unwrap())
        return result

    def _unrun(self, context, task_id: str, attempt: RunResult) -> Outcome[RunResult]:
        """Report an exact task this invocation did not execute.

        By default the owner's prepared inputs die with the invocation that
        submitted them, so a still-queued reservation can never be served later.
        Retire it with its real cause instead of a generic shutdown label, and
        never report unexecuted work as an owner result.

        ``retain_refusals`` opts an owner into keeping the reservation when the
        queue names a capacity shortage (``CAPACITY_REFUSALS``). That is only
        sound for an owner that has a policy for converging the row by ID after
        this invocation gives up: it re-reads the row, retries it, and retires it
        once it is no longer worth waiting for. Owners without such a policy keep
        the default and must not inherit a queued row they never look at again.
        The refusal is always named in the result, so a retaining owner can tell
        "kept, try again later" from "kept, give up now".
        """
        current = self.supervisor.read(context, task_id)
        if current.failure:
            return current
        task = current.unwrap()
        code = attempt.refused or "task_not_admitted"
        if self.retain_refusals and attempt.refused in CAPACITY_REFUSALS and task.state in QUEUED:
            # Capacity shortfall: the row is still exactly what the owner submitted.
            logger.info("[CompanionV2] 后台任务暂未获准入 task=%s kind=%s code=%s state=%s",
                        task_id, task.spec.kind, code, task.state)
            return Outcome.success(RunResult(task, code, refused=code))
        if task.state not in QUEUED:
            # Settled before this invocation could begin: the row is authoritative.
            return Outcome.success(RunResult(task, task.reason))
        logger.info("[CompanionV2] 后台任务未获准入 task=%s kind=%s code=%s", task_id, task.spec.kind, code)
        retired = self.supervisor.discard_queued(task_id, reason="owner_unavailable", code=code)
        if retired.failure:
            return Outcome(failure=retired.failure)
        return Outcome.success(RunResult(retired.unwrap(), code))

    async def close(self):
        self._closed = True
        waiting = tuple(self._waiting)
        for invocation in waiting:
            invocation.cancel()
        if waiting:
            await asyncio.wait(waiting, timeout=self.stop_grace)
        return await super().close()
