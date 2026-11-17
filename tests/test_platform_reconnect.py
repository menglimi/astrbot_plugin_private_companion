"""A native adapter reload must rebuild both bridges for the same account."""

import asyncio
import unittest
from unittest.mock import patch

from astrbot_host.multi_session import MultiHostSession
from tests.group_host_support import GroupFixture
from tests.native_adapter_support import FakePlatform
from tests.test_main_no_reply import ENTRY


class PlatformReconnectTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    async def test_platform_reload_rebuilds_private_and_group_bridges(self):
        await self.start_host()
        previous = self.controller.session
        old_event = self.group_event()
        replacement = FakePlatform("qq-main")
        self.native.platforms["qq-main"] = replacement

        self.assertFalse(self.controller.native_bridges_current())
        restarted = await self.controller.start()

        self.assertTrue(restarted.is_success, restarted.failure)
        self.assertIsNot(self.controller.session, previous)
        self.assertEqual(previous.application.state, "stopped")
        self.assertIs(self.controller.session.bridge.platforms["qq-main"][0], replacement)
        self.assertIs(self.controller.session.bridge.groups.platforms["qq-main"][0], replacement)
        self.assertTrue(self.controller.native_bridges_current())
        self.assertFalse(self.controller.claims(old_event))
        self.assertTrue(self.controller.claims(self.group_event()))
        self.assertTrue(self.controller.claims(self.group_event("group-c")))
        self.assertTrue((await self.controller.session.bridge.capture_core_reply(self.event())).is_success)

    async def test_client_replacement_on_same_platform_rebuilds_both_bridges(self):
        await self.start_host()
        previous = self.controller.session
        platform = self.native.platforms["qq-main"]
        platform.client = FakePlatform("qq-main").client

        self.assertFalse(self.controller.native_bridges_current())
        restarted = await self.controller.start()

        self.assertTrue(restarted.is_success, restarted.failure)
        self.assertIsNot(self.controller.session, previous)
        self.assertIs(self.controller.session.bridge.platforms["qq-main"][1], platform.client)
        self.assertIs(self.controller.session.bridge.groups.platforms["qq-main"][1], platform.client)
        self.assertTrue(self.controller.claims(self.group_event()))

    async def test_replacement_with_different_login_does_not_close_old_session(self):
        await self.start_host()
        previous = self.controller.session
        replacement = FakePlatform("qq-main")
        replacement.client._wsr_api_clients.pop("100")
        self.native.platforms["qq-main"] = replacement

        refused = await self.controller.start()

        self.assertEqual(refused.failure.code, "host_account_unavailable")
        self.assertIs(self.controller.session, previous)
        self.assertEqual(previous.application.state, "running")
        self.assertFalse(self.controller.claims(self.group_event()))
        replacement.client._wsr_api_clients["100"] = object()
        restarted = await self.controller.start()
        self.assertTrue(restarted.is_success, restarted.failure)
        self.assertIsNot(self.controller.session, previous)
        self.assertTrue(self.controller.claims(self.group_event()))

    async def test_platform_loaded_hook_schedules_recovery(self):
        await self.start_host()
        previous = self.controller.session
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None

        await plugin.on_companion_platform_loaded()
        self.assertIsNone(plugin._host_start_task)

        self.native.platforms["qq-main"] = FakePlatform("qq-main")
        await plugin.on_companion_platform_loaded()
        self.assertIsNotNone(plugin._host_start_task)
        await asyncio.wait_for(plugin._host_start_task, timeout=10)
        self.assertIsNot(self.controller.session, previous)
        self.assertTrue(self.controller.native_bridges_current())

    async def test_platform_loaded_checker_failure_does_not_fail_adapter_hook(self):
        await self.start_host()
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None

        with patch.object(self.controller, "native_bridges_current", side_effect=AttributeError("missing")):
            await plugin.on_companion_platform_loaded()
            self.assertIsNotNone(plugin._host_start_task)
            plugin._host_start_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await plugin._host_start_task

    async def test_platform_loaded_waits_for_the_original_login_to_reconnect(self):
        await self.start_host()
        previous = self.controller.session
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None
        replacement = FakePlatform("qq-main")
        replacement.client._wsr_api_clients.pop("100")
        self.native.platforms["qq-main"] = replacement

        await plugin.on_companion_platform_loaded()
        await asyncio.sleep(0)
        self.assertFalse(plugin._host_start_task.done())
        self.assertIs(self.controller.session, previous)
        self.assertFalse(self.controller.claims(self.group_event()))

        replacement.client._wsr_api_clients["100"] = object()
        await asyncio.wait_for(plugin._host_start_task, timeout=10)
        self.assertIsNot(self.controller.session, previous)
        self.assertTrue(self.controller.claims(self.group_event()))

    async def test_same_platform_object_recovers_after_temporary_unload(self):
        await self.start_host()
        previous = self.controller.session
        platform = self.native.platforms.pop("qq-main")
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None

        await plugin.on_companion_platform_loaded()
        await asyncio.sleep(0)
        self.assertFalse(self.controller._accepting)
        self.assertFalse(plugin._host_start_task.done())

        self.native.platforms["qq-main"] = platform
        await asyncio.wait_for(plugin._host_start_task, timeout=10)
        self.assertIsNot(self.controller.session, previous)
        self.assertTrue(self.controller._accepting)
        self.assertTrue(self.controller.claims(self.group_event()))

    async def test_client_change_during_startup_stays_unavailable_until_retry(self):
        await self.start_host()
        self.native.platforms["qq-main"] = FakePlatform("qq-main")
        original_start = MultiHostSession.start

        async def change_client_after_start(session, **kwargs):
            result = await original_start(session, **kwargs)
            self.native.platforms["qq-main"].client = FakePlatform("qq-main").client
            return result

        with patch.object(MultiHostSession, "start", change_client_after_start):
            failed = await self.controller.start()
        self.assertEqual(failed.failure.code, "host_account_changed")
        self.assertFalse(self.controller.claims(self.group_event()))

        retried = await self.controller.start()
        self.assertTrue(retried.is_success, retried.failure)
        self.assertTrue(self.controller.native_bridges_current())


if __name__ == "__main__":
    unittest.main()
