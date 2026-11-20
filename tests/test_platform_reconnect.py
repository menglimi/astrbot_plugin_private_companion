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

    async def test_offline_startup_latches_first_connected_bridges_and_detects_later_reload(self):
        platforms = self.native.platforms.copy()
        self.native.platforms.clear()
        await self.start_host()
        initial_session = self.controller.session
        self.assertTrue(self.controller.native_bridges_current())

        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None

        self.native.platforms.update(platforms)
        await plugin.on_companion_platform_loaded()
        self.assertIsNone(plugin._host_start_task)
        self.assertIs(self.controller.session, initial_session)
        self.assertTrue(self.controller.native_bridges_current())

        replacement = FakePlatform("qq-main")
        self.native.platforms["qq-main"] = replacement
        self.assertFalse(self.controller.native_bridges_current())
        await plugin.on_companion_platform_loaded()
        self.assertIsNotNone(plugin._host_start_task)
        await asyncio.wait_for(plugin._host_start_task, timeout=10)
        self.assertIsNot(self.controller.session, initial_session)
        self.assertIs(self.controller.session.bridge.platforms["qq-main"][0], replacement)
        self.assertTrue(self.controller.native_bridges_current())

    async def test_existing_profile_loaded_while_waiting_detects_reload_after_start(self):
        (await self.controller.initialize_profile()).unwrap()
        await self.controller.terminate()
        platforms = self.native.platforms.copy()
        self.native.platforms.clear()

        restored = self.make_controller(self.controller.config)
        loaded = await restored.load_existing()
        self.assertTrue(loaded.is_success, loaded.failure)
        self.assertTrue(loaded.unwrap()["loaded"])

        self.native.platforms.update(platforms)
        started = await restored.start()
        self.assertTrue(started.is_success, started.failure)
        self.assertTrue(restored.native_bridges_current())
        previous = restored.session

        replacement = FakePlatform("qq-main")
        self.native.platforms["qq-main"] = replacement
        self.assertFalse(restored.native_bridges_current())
        restarted = await restored.start()
        self.assertTrue(restarted.is_success, restarted.failure)
        self.assertIsNot(restored.session, previous)
        self.assertIs(restored.session.bridge.platforms["qq-main"][0], replacement)
        self.assertTrue(restored.native_bridges_current())

    async def test_partial_offline_account_joins_without_restart_then_detects_reload(self):
        second = self.native.platforms.pop("qq-second")
        await self.start_host()
        initial_session = self.controller.session
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None

        self.native.platforms["qq-second"] = second
        await plugin.on_companion_platform_loaded()
        self.assertIsNone(plugin._host_start_task)
        self.assertIs(self.controller.session, initial_session)
        self.assertTrue(self.controller.native_bridges_current())

        replacement = FakePlatform("qq-second")
        self.native.platforms["qq-second"] = replacement
        self.assertFalse(self.controller.native_bridges_current())
        await plugin.on_companion_platform_loaded()
        self.assertIsNotNone(plugin._host_start_task)
        await asyncio.wait_for(plugin._host_start_task, timeout=10)
        self.assertIsNot(self.controller.session, initial_session)
        self.assertIs(self.controller.session.bridge.groups.platforms["qq-second"][0], replacement)
        self.assertTrue(self.controller.native_bridges_current())

    async def test_registered_waiting_platform_client_swap_before_first_login_does_not_restart_until_later_reload(self):
        self.native.platforms["qq-second"].client._wsr_api_clients.pop("101")
        await self.start_host()
        initial_session = self.controller.session
        self.assertTrue(self.controller.native_bridges_current())
        self.assertEqual(set(initial_session.native_bridge_pairs[0]), {"qq-main"})
        self.assertEqual(set(initial_session.native_bridge_pairs[1]), {"qq-main"})
        self.assertTrue(self.controller.claims(self.group_event("group-a")))
        self.assertFalse(self.controller.claims(self.group_event("group-c")))

        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        plugin._host_start_task = None

        still_waiting = FakePlatform("qq-second")
        still_waiting.client._wsr_api_clients.pop("101")
        self.native.platforms["qq-second"] = still_waiting
        await plugin.on_companion_platform_loaded()
        self.assertIsNone(plugin._host_start_task)
        self.assertIs(self.controller.session, initial_session)
        self.assertTrue(self.controller.native_bridges_current())
        self.assertNotIn("qq-second", initial_session.native_bridge_pairs[0])

        first_online_client = FakePlatform("qq-second").client
        still_waiting.client = first_online_client
        await plugin.on_companion_platform_loaded()
        self.assertIsNone(plugin._host_start_task)
        self.assertIs(self.controller.session, initial_session)
        self.assertTrue(self.controller.native_bridges_current())
        self.assertEqual(initial_session.native_bridge_pairs[0]["qq-second"], (still_waiting, first_online_client))
        self.assertEqual(initial_session.native_bridge_pairs[1]["qq-second"], (still_waiting, first_online_client))
        self.assertTrue(self.controller.claims(self.group_event("group-a")))
        self.assertTrue(self.controller.claims(self.group_event("group-c")))

        second_online = FakePlatform("qq-second")
        self.native.platforms["qq-second"] = second_online
        self.assertFalse(self.controller.native_bridges_current())
        await plugin.on_companion_platform_loaded()
        self.assertIsNotNone(plugin._host_start_task)
        await asyncio.wait_for(plugin._host_start_task, timeout=10)
        self.assertIsNot(self.controller.session, initial_session)
        self.assertEqual(initial_session.application.state, "stopped")
        self.assertIs(self.controller.session.bridge.platforms["qq-second"][0], second_online)
        self.assertIs(self.controller.session.bridge.groups.platforms["qq-second"][0], second_online)
        self.assertTrue(self.controller.native_bridges_current())

    async def test_registered_waiting_platform_direct_start_and_post_online_waiting_cycle(self):
        self.native.platforms["qq-second"].client._wsr_api_clients.pop("101")
        await self.start_host()
        initial_session = self.controller.session
        self.assertNotIn("qq-second", initial_session.native_bridge_pairs[0])

        first_online = FakePlatform("qq-second")
        self.native.platforms["qq-second"] = first_online
        replayed = await self.controller.start()
        self.assertTrue(replayed.is_success, replayed.failure)
        self.assertTrue(replayed.unwrap().get("replayed"))
        self.assertIs(self.controller.session, initial_session)
        self.assertEqual(initial_session.native_bridge_pairs[0]["qq-second"], (first_online, first_online.client))
        self.assertTrue((await self.controller.session.bridge.capture_core_reply(self.event())).is_success)

        # Going waiting again after being online must keep the latched baseline
        # so a later reconnect with a new client still triggers a bridge rebuild.
        first_online.client._wsr_api_clients.pop("101")
        await self.controller.recover_accounts()
        self.assertTrue(self.controller.native_bridges_current())
        self.assertIs(self.controller.session, initial_session)

        second_online = FakePlatform("qq-second")
        self.native.platforms["qq-second"] = second_online
        self.assertFalse(self.controller.native_bridges_current())
        restarted = await self.controller.start()
        self.assertTrue(restarted.is_success, restarted.failure)
        self.assertIsNot(self.controller.session, initial_session)
        self.assertIs(self.controller.session.bridge.platforms["qq-second"][0], second_online)
        self.assertTrue(self.controller.native_bridges_current())


if __name__ == "__main__":
    unittest.main()
