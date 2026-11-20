"""Independent startup and reconnect using isolated native host doubles."""

import asyncio
import unittest
from unittest.mock import patch

from tests import astrbot_data_root  # noqa: F401
from tests.multi_host_support import MultiFixture
from tests.native_adapter_support import FakePlatform
from companion_v2.execution.models import TaskError


class MultiAccountRecoveryTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def statuses(self):
        return {row["platform_id"]: row for row in self.controller.status()["account_connections"]}

    async def test_missing_account_does_not_block_start_and_joins_when_available(self):
        second = self.native.platforms.pop("qq-second")
        self.assertIsNone(self.controller.startup_readiness())
        app = await self.start_host()
        session = self.controller.session
        self.assertEqual(self.statuses()["qq-second"]["state"], "waiting")
        first = await session.prepare_core_reply_context(self.event(), self.controller.settings.target("first"))
        self.assertTrue(first.is_success, first.failure)
        self.assertEqual(len(self.controller.settings.accounts), 2)
        self.native.platforms["qq-second"] = second
        await session.account_recovery.refresh(wait=True)
        ready = await session.prepare_core_reply_context(
            self.event("second"), self.controller.settings.target("second"))
        self.assertTrue(ready.is_success, ready.failure)
        self.assertIs(self.controller.session.application, app)
        self.assertEqual(self.statuses()["qq-second"]["state"], "online")
        self.assertIsNotNone(self.statuses()["qq-second"]["last_recovered_at"])
        self.assertIs(session.bridge.connections, session.bridge.groups.connections)

    async def test_all_accounts_offline_keeps_shared_data_and_resumes_without_restart(self):
        platforms = self.native.platforms.copy()
        self.native.platforms.clear()
        app = await self.start_host()
        self.assertTrue(self.controller._accepting)
        self.assertTrue(all(row["state"] == "waiting" for row in self.statuses().values()))
        self.native.platforms.update(platforms)
        await self.controller.session.account_recovery.refresh(wait=True)
        self.assertTrue(all(row["state"] == "online" for row in self.statuses().values()))
        self.assertIs(app, self.controller.session.application)

    async def test_slow_account_binding_does_not_delay_other_account_intake(self):
        initialized = await self.controller.initialize_profile(automatic=True)
        self.assertTrue(initialized.is_success, initialized.failure)
        session = self.controller.session
        original = session.synchronize
        waiting, release = asyncio.Event(), asyncio.Event()

        async def slow_second(target):
            if target.platform_id == "qq-second":
                waiting.set()
                await release.wait()
            return await original(target)

        with patch.object(session, "synchronize", side_effect=slow_second):
            started = await asyncio.wait_for(self.controller.start(automatic=True), 2)
            self.assertTrue(started.is_success, started.failure)
            await asyncio.wait_for(waiting.wait(), 2)
            self.assertTrue(self.controller._accepting)
            healthy = await session.prepare_core_reply_context(
                self.event(), self.controller.settings.target("first"))
            self.assertTrue(healthy.is_success, healthy.failure)
            self.assertEqual(self.statuses()["qq-second"]["state"], "recovering")
            release.set()
            await session.account_recovery.refresh(wait=True)

    async def test_waiting_automatic_start_cannot_override_manual_stop(self):
        await self.start_host()
        await self.controller.stop()
        async with self.controller._lifecycle_lock:
            pending = asyncio.create_task(self.controller.start(automatic=True))
            await asyncio.sleep(0)
        result = await pending
        self.assertEqual(result.failure.code, "host_manually_stopped")
        self.assertFalse(self.controller._accepting)

    async def test_replacement_fences_old_plan_without_fencing_other_account(self):
        await self.start_host()
        session = self.controller.session
        a_target, b_target = (self.controller.settings.target(name) for name in ("first", "second"))
        a_event, b_event = self.event(), self.event("second")
        a_plan = (await session.prepare_core_reply_context(a_event, a_target)).unwrap()
        b_plan = (await session.prepare_core_reply_context(b_event, b_target)).unwrap()
        self.native.platforms["qq-main"] = FakePlatform()
        await session.account_recovery.refresh(wait=True)
        denied = await session.prepare_core_reply(a_event, a_target, plan=a_plan)
        self.assertEqual(denied.failure.code, "host_connection_changed")
        session.bridge.connections.assert_current(b_plan.connection_lease)
        fresh = await session.prepare_core_reply_context(self.event(identifier="fresh"), a_target)
        self.assertTrue(fresh.is_success, fresh.failure)
        self.assertGreater(fresh.unwrap().connection_lease.generation, a_plan.connection_lease.generation)

    async def test_connection_changes_during_capture_are_rejected(self):
        await self.start_host()
        session = self.controller.session
        original = session.bridge._role

        async def replace_during_lookup(*args):
            result = await original(*args)
            self.native.platforms["qq-main"] = FakePlatform()
            return result

        with patch.object(session.bridge, "_role", side_effect=replace_during_lookup):
            result = await session.bridge.capture_core_reply(self.event())
        self.assertEqual(result.failure.code, "host_connection_changed")

    async def test_repeated_recovery_notifications_bind_account_once(self):
        await self.start_host()
        session = self.controller.session
        self.native.platforms["qq-main"] = FakePlatform()
        with patch.object(session, "synchronize", wraps=session.synchronize) as synchronize:
            await asyncio.gather(*(self.controller.recover_accounts() for _ in range(5)))
            await session.account_recovery.refresh(wait=True)
        self.assertEqual(synchronize.await_count, 1)
        self.assertEqual(synchronize.await_args.args[0].platform_id, "qq-main")

    async def test_failed_account_binding_does_not_stop_healthy_account(self):
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        session = self.controller.session
        original = session.synchronize

        async def broken_second(target):
            if target.platform_id == "qq-second":
                raise TaskError("host_native_lookup_timeout", "synthetic lookup failure")
            return await original(target)

        with patch.object(session, "synchronize", side_effect=broken_second):
            result = await self.controller.start()
            await session.account_recovery.refresh(wait=True)
        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(self.statuses()["qq-second"]["state"], "error")
        healthy = await session.prepare_core_reply_context(self.event(), self.controller.settings.target("first"))
        self.assertTrue(healthy.is_success, healthy.failure)
        await session.account_recovery.refresh(wait=True)
        self.assertEqual(self.statuses()["qq-second"]["state"], "online")

    async def test_manual_stop_does_not_resume_on_connection_notification(self):
        await self.start_host()
        session = self.controller.session
        await self.controller.stop()
        self.native.platforms["qq-main"] = FakePlatform()
        await self.controller.recover_accounts()
        self.assertFalse(self.controller._accepting)
        self.assertIsNone(self.controller.session)
        self.assertTrue(session.account_recovery.closed)
        self.assertTrue(session.account_recovery.driver.done())

    async def test_read_access_does_not_require_live_connection(self):
        await self.start_host()
        session = self.controller.session
        plan = (await session.prepare_core_reply_context(self.event(), self.controller.settings.target("first"))).unwrap()
        self.native.platforms.pop("qq-main")
        session.bridge.connections.refresh()
        result = session.application.tasks.check_context_access(
            plan.context, action="conversation.reply", observed_at=self.clock.now())
        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(session.application.state, "running")
