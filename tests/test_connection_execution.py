"""Host connection leases fence background work without hiding stored records."""

import asyncio
import unittest

from companion_v2.execution.models import Lane, TaskError, TaskResult
from companion_v2.execution.runner import LocalTaskRunner
from tests.test_execution import ExecutionFixture


class ExecutionConnection:
    def __init__(self):
        self.generation = 1
        self.online = True

    def capture(self, spec, definition, *, submitting=False):
        if definition.kind == "fixture.background":
            return None
        if not self.online:
            raise TaskError("host_account_offline", "account is offline")
        return self.generation

    def assert_current(self, spec, lease):
        if not self.online or lease != self.generation:
            raise TaskError("host_connection_changed", "original account connection is unavailable")


class ConnectionExecutionTests(ExecutionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.connection = ExecutionConnection()
        self.tasks = self.supervisor()
        self.tasks.bind_execution_guard(self.connection)
        self.tasks.attach(expected_generation=1).unwrap()

    def test_offline_rejects_new_outbound_work_but_preserves_read_access(self):
        task = self.submit("readable")
        self.connection.online = False
        self.assertEqual(self.tasks.submit(self.spec("offline")).failure.code, "host_account_offline")
        self.assertIsNone(self.runtime.read_one("SELECT task_id FROM execution_tasks WHERE task_id = 'offline'", ()))
        self.assertEqual(self.tasks.read(self.context, task.task_id).unwrap().task_id, task.task_id)
        self.assertTrue(self.tasks.check_context_access(self.context, action="configuration.read",
                                                      observed_at=self.clock.now()).is_success)
        self.assertTrue(self.tasks.submit(self.spec("local-maintenance", kind="fixture.background")).is_success)

    def test_reconnected_account_cannot_reuse_queued_task_lease(self):
        self.submit("old")
        self.connection.generation += 1
        self.assertIsNone(self.tasks.claim_task(self.context, "old").unwrap().claim)
        self.assertEqual(self.read_task("old").reason, "host_connection_changed")
        self.submit("new")
        self.assertIsNotNone(self.tasks.claim_task(self.context, "new").unwrap().claim)

    async def test_replacement_cancels_waiting_worker_and_retains_unknown_effect(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def run(task):
            task.begin_effect()
            task.record_operation("original-operation")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            return TaskResult("succeeded")

        runner = LocalTaskRunner(self.tasks, {kind: run for kind in self.tasks.registrations}, check_interval=0.005)
        self.addAsyncCleanup(runner.close)
        self.submit("inflight", kind="fixture.effect")
        worker = asyncio.create_task(runner.run_once(Lane.FOREGROUND))
        await asyncio.wait_for(entered.wait(), 1)
        self.connection.generation += 1
        result = (await asyncio.wait_for(worker, 1)).unwrap().task
        self.assertTrue(stopped.is_set())
        self.assertEqual((result.state, result.effect, result.operation_id),
                         ("uncertain", "unknown", "original-operation"))
        self.assertIsNone((await runner.run_once(Lane.FOREGROUND)).unwrap().task)
