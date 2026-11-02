"""Read-only status for the daily self-review task.

The daily review keeps its cursor on disk but its last report only in memory, so
a restarted process used to describe the same window as both "never ran" and
"last run 10/31 22:29": the state came from the fresh in-memory map while the
timestamp came from the persisted cursor. These checks pin the status contract
on both sides of that restart boundary.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from companion_v2.execution.models import TaskError, encode, stamp
from companion_v2.foundation.refs import ScopeKind
from companion_v2.role_work.journal.self_review_service import (KIND, OUTCOME_PREFIX, SelfReviewService)
from tests.multi_host_support import MultiFixture, multi_config

INSTRUCTION = "原角色人格提示"


class SelfReviewStatusTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def start(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        context, _ = await self.accepted()
        return app, context

    async def scheduled(self, service, run, context, at):
        """Run the scheduled loop once with a stubbed model outcome.

        ``_run_scheduled`` is the production path that turns a run into a
        persisted cursor or retry deadline; only the model call is replaced.
        """
        key, _ = service._cursor(context)
        original, service.run = service.run, run
        try:
            return await service._run_scheduled(context, INSTRUCTION, key, at)
        finally:
            service.run = original

    async def settlement(self, service, context, at):
        async def succeeded(context, instruction):
            return {"state": "succeeded", "task_id": "self-review:test"}

        return await self.scheduled(service, succeeded, context, at)

    async def failure(self, service, context, at, code="self_review_material_changed"):
        async def failing(context, instruction):
            raise TaskError(code, "reviewed behaviour changed or was withdrawn")

        return await self.scheduled(service, failing, context, at)

    def restart(self, service):
        """A fresh process keeps the database and loses every in-memory record.

        ``workers`` belongs to that boundary as well: a task started before the
        restart cannot survive it, so the helper drops it and pins the empty
        state instead of leaving a half-restarted service behind.
        """
        service.results.clear()
        service.next_checks.clear()
        service.workers.clear()
        self.assertFalse(service.results)
        self.assertFalse(service.next_checks)
        self.assertFalse(service.workers)

    async def test_status_reports_a_window_that_has_not_run_yet(self):
        app, context = await self.start()
        service = app.self_review

        self.assertFalse(service.results)
        status = service.status(context)
        self.assertEqual(status["state"], "not_run")
        self.assertTrue(status["due"])
        self.assertIsNone(status["last_run_at"])
        self.assertFalse(status["worker"])
        self.assertIsNone(status["code"])

    async def test_restart_keeps_a_settled_window_visible(self):
        app, context = await self.start()
        service = app.self_review
        at = self.clock.now()

        await self.settlement(service, context, at)
        before = service.status(context)
        self.assertEqual(before["state"], "succeeded")
        self.assertEqual(before["last_run_at"], stamp(at))

        self.restart(service)
        status = service.status(context)
        # Both are keys the dashboard already labels ("最近一次已产出"), so a
        # restart may not fall back to the "还没运行过" wording.
        self.assertIn(status["state"], ("succeeded", "completed"))
        self.assertEqual(status["last_run_at"], stamp(at))
        self.assertIsNone(status["code"])
        self.assertFalse(status["due"])
        self.assertFalse(status["worker"])

    async def test_restart_drops_the_worker_of_the_previous_process(self):
        app, context = await self.start()
        service = app.self_review
        at = self.clock.now()

        await self.settlement(service, context, at)
        # A running task cannot survive the restart; keeping it visible would
        # again let the status line describe a process that no longer exists.
        service.workers[context.key] = SimpleNamespace(done=lambda: False)
        self.assertTrue(service.status(context)["worker"])

        self.restart(service)
        status = service.status(context)
        self.assertFalse(status["worker"])
        self.assertIn(status["state"], ("succeeded", "completed"))
        self.assertEqual(status["last_run_at"], stamp(at))

    async def test_due_keeps_following_the_cursor_after_a_restart(self):
        app, context = await self.start()
        service = app.self_review
        at = self.clock.now()

        await self.settlement(service, context, at)
        self.restart(service)
        self.assertFalse(service.status(context)["due"])

        self.clock.advance(timedelta(seconds=service.scan_seconds))
        due = service.status(context)
        self.assertTrue(due["due"])
        self.assertEqual(due["state"], "succeeded")
        self.assertEqual(due["last_run_at"], stamp(at))

    async def test_restart_still_reports_the_last_failure(self):
        app, context = await self.start()
        service = app.self_review
        at = self.clock.now()

        with self.assertRaises(TaskError):
            await self.failure(service, context, at)
        self.assertNotIn(context.key, service.results)

        self.restart(service)
        failed = service.status(context)
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["code"], "self_review_material_changed")
        # A failed run never closes the window, so there is no settled run yet.
        self.assertIsNone(failed["last_run_at"])

        later = self.clock.advance(timedelta(seconds=service.scan_seconds))
        await self.settlement(service, context, later)
        self.restart(service)
        settled = service.status(context)
        self.assertEqual(settled["state"], "succeeded")
        self.assertIsNone(settled["code"])
        self.assertEqual(settled["last_run_at"], stamp(later))

    async def test_a_corrupt_remembered_outcome_falls_back_to_the_cursor(self):
        app, context = await self.start()
        service = app.self_review
        at = self.clock.now()

        await self.settlement(service, context, at)
        with self.assertRaises(TaskError):
            await self.failure(service, context, at + timedelta(minutes=1))
        with app.runtime.transaction() as connection:
            keys = [row["key"] for row in connection.execute(
                "SELECT key FROM runtime_metadata WHERE substr(key, 1, ?) = ?",
                (len(OUTCOME_PREFIX), OUTCOME_PREFIX))]
        self.assertEqual(len(keys), 1)

        for corrupt in ("not json", '{"state":"weird","code":"x","at":"2026-10-31T00:00:00+00:00"}',
                        '{"state":"failed","code":"","at":"2026-10-31T00:00:00+00:00"}',
                        '{"state":"failed","at":"2026-10-31T00:00:00+00:00"}',
                        '{"state":"failed","code":"x","at":"without a timezone"}'):
            with self.subTest(corrupt=corrupt):
                with app.runtime.transaction() as connection:
                    connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                                       (corrupt, keys[0]))
                self.restart(service)
                status = service.status(context)
                self.assertEqual(status["state"], "succeeded")
                self.assertIsNone(status["code"])
                self.assertEqual(status["last_run_at"], stamp(at))

    async def test_a_remembered_outcome_with_an_extra_field_is_still_read(self):
        app, context = await self.start()
        service = app.self_review
        at = self.clock.now()

        with self.assertRaises(TaskError):
            await self.failure(service, context, at)
        # A later version may store more than the required fields; such a record
        # must keep explaining the window instead of degrading to the cursor.
        with app.runtime.transaction() as connection:
            row = connection.execute(
                "SELECT key FROM runtime_metadata WHERE substr(key, 1, ?) = ?",
                (len(OUTCOME_PREFIX), OUTCOME_PREFIX)).fetchone()
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?", (encode({
                "state": "failed", "code": "self_review_material_changed",
                "at": stamp(at), "attempt": 2}), row["key"]))

        self.restart(service)
        status = service.status(context)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["code"], "self_review_material_changed")
        self.assertIsNone(status["last_run_at"])


class SelfReviewFailureRecordTests(unittest.IsolatedAsyncioTestCase):
    """Both non-success branches leave a record the next process can read."""

    NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)

    def service(self):
        service = object.__new__(SelfReviewService)
        service.results, service.next_checks, service.workers = {}, {}, {}
        service.cursor, service.closed = 0, False
        service.tasks = SimpleNamespace(now=lambda: self.NOW)
        service._record_failure = Mock()
        return service

    async def test_a_failed_run_records_its_code_before_reraising(self):
        service = self.service()
        service.run = AsyncMock(side_effect=TaskError("self_review_output_invalid", "invalid output"))
        service._advance_cursor = Mock()
        context = SimpleNamespace(key="role-one")

        with self.assertRaises(TaskError):
            await service._run_scheduled(context, INSTRUCTION, "cursor", self.NOW)

        service._advance_cursor.assert_not_called()
        service._record_failure.assert_called_once_with(
            context, "failed", "self_review_output_invalid", self.NOW)

    async def test_a_stopped_run_records_the_reason_instead_of_a_success(self):
        service = self.service()
        service.run = AsyncMock(return_value={"state": "failed", "code": "model_estimation_failed"})
        service._advance_cursor = Mock()
        context = SimpleNamespace(key="role-one")

        await service._run_scheduled(context, INSTRUCTION, "cursor", self.NOW)

        service._advance_cursor.assert_not_called()
        service._record_failure.assert_called_once_with(
            context, "failed", "model_estimation_failed", self.NOW)
        self.assertEqual(service.next_checks[context.key], self.NOW + timedelta(minutes=5))

    def test_a_blocked_scan_is_remembered_with_its_binding_code(self):
        service = self.service()
        service.gateway = SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()}))
        context = SimpleNamespace(key="role-one", scope=SimpleNamespace(kind=ScopeKind.PRIVATE))
        service.app = SimpleNamespace(state_maintenance=SimpleNamespace(
            bindings={context.key: (context, INSTRUCTION, None)}))
        service.due = Mock(return_value=True)
        service.prepare = Mock(side_effect=TaskError("self_review_binding_changed", "changed"))

        service.advance()

        self.assertEqual(service.results[context.key],
                         {"state": "blocked", "code": "self_review_binding_changed"})
        service._record_failure.assert_called_once_with(
            context, "blocked", "self_review_binding_changed", self.NOW)


if __name__ == "__main__":
    unittest.main()
