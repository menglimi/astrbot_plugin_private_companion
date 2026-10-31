"""One bounded background coordinator for registered domain planners and delivery."""

from __future__ import annotations

import asyncio

from companion_v2.execution.models import require
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.waiting import BackgroundTaskQueue


class BackgroundCoordinator:
    def __init__(self, tasks, delivery, planners, *, notify):
        self.tasks, self.delivery, self.planners = tasks, delivery, tuple(planners)
        self.notify = notify
        # Share the real background lane with planners without dropping outbound work
        # when another background task temporarily holds the last physical slot.
        self.waiting = BackgroundTaskQueue(tasks)
        self.runner = ExactTaskRunner(
            tasks, frozenset(r.definition.kind for r in delivery.registrations),
            delivery.deliver, waiting=self.waiting,
        )
        self._workers = {}
        self._closed = False

    @property
    def active_handles(self):
        return self.runner.active_handles

    @property
    def pending(self):
        return bool(self._workers)

    def _done(self, worker):
        if worker.cancelled():
            return
        error = worker.exception()
        result = worker.result() if error is None else None
        # A saturated lane must wait for the bounded poll, not a self-waking busy loop.
        if error is not None or result.failure or result.unwrap().task is not None:
            self.notify()

    def advance(self, *, planners=True):
        if self._closed:
            return False
        require(self.tasks.check_generation())
        for task_id, worker in tuple(self._workers.items()):
            if worker.done():
                del self._workers[task_id]
                if not worker.cancelled():
                    result = worker.result()
                    if result.failure:
                        # Exact claims can fail before mutating the queue after binding/permission changes.
                        self.delivery.cancel_unstarted(task_id, reason=result.failure.code)
        more = self.delivery.settle() == 32
        if planners:
            for planner in self.planners:
                more = require(planner.advance()) or more
        available = self.tasks.limits.background_slots - len(self._workers)
        if available > 0:
            for context, task_id in self.delivery.pending(limit=available, excluded=tuple(self._workers)):
                worker = asyncio.create_task(self.runner.run(context, task_id), name="companion:background:" + task_id)
                self._workers[task_id] = worker
                worker.add_done_callback(self._done)
        return more

    def next_wake(self):
        if self._closed or len(self._workers) >= self.tasks.limits.background_slots:
            return None
        return self.delivery.next_wake(excluded=tuple(self._workers))

    def cancel(self):
        self._closed = True
        for worker in self._workers.values():
            worker.cancel()

    async def close(self, *, grace_seconds=0.1):
        self._closed = True
        workers = tuple(self._workers.values())
        if workers:
            _, pending = await asyncio.wait(workers, timeout=grace_seconds)
            for worker in pending:
                worker.cancel()
            if pending:
                await asyncio.wait(pending, timeout=0.25)
        await self.runner.close()
        for task_id, worker in tuple(self._workers.items()):
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                del self._workers[task_id]
