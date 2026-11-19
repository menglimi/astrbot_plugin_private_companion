"""Each configured channel account starts and operates on its own."""

from tests import astrbot_data_root  # noqa: F401

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.delivery.models import DeliveryEnvelope
from companion_v2.execution.models import TaskError, digest
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import FakePlatform
from tests.telegram_reply_support import install_history, run_pipeline
from tests.test_core_reply_pipeline import PipelineEvent
from tests.test_main_no_reply import ENTRY
from tests.test_telegram_private_reply import OWNER, TelegramPlatform, telegram_event

QQ_WINDOW = "qq-main:FriendMessage:200"
TELEGRAM_WINDOW = f"tg-main:FriendMessage:{OWNER}"


class SendingTelegramPlatform(TelegramPlatform, TelegramPlatformAdapter):
    """The synthetic channel with AstrBot's own session sender for background text."""


class ChannelIsolationFixture(MultiFixture):
    """One QQ account and one Telegram account, each with its own owner window."""

    def setUp(self):
        super().setUp()
        self.qq = self.native.platforms["qq-main"]
        self.telegram = SendingTelegramPlatform()
        self.native.platforms = {"qq-main": self.qq, "tg-main": self.telegram}
        self.native.selections["tg-main"] = "original"
        self.native.conversations["tg-main"] = "telegram-conversation"
        self.window_personas[TELEGRAM_WINDOW] = "original"
        self.types = replace(self.types, event=AstrMessageEvent)
        self.controller = self.make_controller(multi_config(
            targets=[dict(target_id="qq-owner", platform_id="qq-main", account_id="100",
                          bot_id="bot-a", participant_id="200"),
                     dict(target_id="tg-owner", platform_id="tg-main", account_id="tg-main",
                          bot_id="bot-tg", participant_id=str(OWNER))],
            personas=[dict(bot_id="bot-a", persona_id="original", role_id="role-a"),
                      dict(bot_id="bot-tg", persona_id="original", role_id="role-tg")],
            group_targets=[]))
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.plugin.staging = self.controller
        self.plugin._host_start_task = None
        self.plugin._session_settings_warm_task = None
        self.plugin._qzone_life_publisher = object()
        self.plugin._startup_failure_key = None
        self.plugin._startup_failure_at = 0.0
        self.addAsyncCleanup(self.stop_retry)

    async def stop_retry(self):
        task = self.plugin._host_start_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def boot(self):
        """Start the plugin the way AstrBot does, and report whether the host accepts."""
        with patch.object(ENTRY, "warm_session_settings", new=AsyncMock()):
            await self.plugin.initialize()
        self.plugin._session_settings_warm_task = None
        return self.controller.status()["accepting"]

    async def until(self, reached):
        """Wait for something the host does by itself, with no restart and no event from AstrBot."""
        async with asyncio.timeout(30):
            while not reached():
                await asyncio.sleep(0.05)

    async def start_up_retry_finished(self):
        """Wait for the plugin's start-up retry, if one is scheduled; say whether the host accepts."""
        if self.plugin._host_start_task is not None:
            await asyncio.wait_for(self.plugin._host_start_task, timeout=30)
        return self.controller.status()["accepting"]

    async def settled(self):
        """Let the host finish binding every account that is online now."""
        await self.controller.session.account_recovery.refresh(wait=True)

    def connections(self):
        return {(row["platform_id"], row["account_id"]): (row["state"], row["reason_code"])
                for row in self.controller.status()["account_connections"]}

    def started_channels(self, bind):
        return [call.args[0].scope.platform for call in bind.call_args_list]

    async def flush(self):
        observer = self.controller.session.core_reply_observations
        while pending := observer._pending - observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))
            # A task can be done with its own done callbacks still queued; gather then
            # completes without yielding, so yield once to let them clear the set.
            await asyncio.sleep(0)

    async def telegram_reply(self, text="我在 Telegram 这里", identifier=1):
        self.event = event = telegram_event(self.telegram, identifier=identifier)
        event.created_at = self.clock.now().timestamp()
        await self.plugin.receive_companion_message(event)
        await self.flush()
        await run_pipeline(self, event, text=text)
        return [call.kwargs["text"] for call in self.telegram.client.send_message.await_args_list]

    async def qq_reply(self, text="我在 QQ 这里"):
        event = PipelineEvent()
        event.bot = self.qq.client
        event.created_at = self.clock.now().timestamp()
        await self.plugin.receive_companion_message(event)
        await self.flush()
        await run_pipeline(self, event, text=text)
        return ["".join(part.text for part in chain) for chain in event.sent]

    async def background_text(self, target_id, role_id, text="该休息了"):
        """Send one background text to a configured owner window through the host's sender."""
        session = self.controller.session
        context = self.controller.authority.context(target_id, role_id)
        return await session.send(DeliveryEnvelope(
            "part-" + target_id, "operation-" + target_id, context, 1, text, digest(text),
            session.sender_binding, lambda: True))

    def qq_calls(self):
        return self.qq.client._api._wsr_api.calls

    def qq_disconnect(self):
        self.qq.client._wsr_api_clients.clear()

    def qq_connect(self):
        self.qq.client._wsr_api_clients.update({"100": object()})


class StartWithOneChannelTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_telegram_is_answered_while_the_qq_channel_is_missing_at_start(self):
        self.native.platforms.pop("qq-main")
        self.assertTrue(await self.boot())
        await install_history(self)
        sent = await self.telegram_reply()
        self.assertEqual(len(sent), 1)
        self.assertIn("我在 Telegram 这里", sent[0])

    async def test_telegram_is_answered_while_the_qq_client_is_disconnected_at_start(self):
        self.qq_disconnect()
        self.assertTrue(await self.boot())
        await install_history(self)
        self.assertIn("我在 Telegram 这里", (await self.telegram_reply())[0])

    async def test_qq_is_answered_while_the_telegram_channel_is_missing_at_start(self):
        self.native.platforms.pop("tg-main")
        self.assertTrue(await self.boot())
        await install_history(self, QQ_WINDOW)
        self.assertEqual(await self.qq_reply(), ["我在 QQ 这里"])
        self.telegram.client.send_message.assert_not_awaited()

    async def test_qq_is_answered_while_the_telegram_client_is_not_running_at_start(self):
        self.telegram.application.running = False
        self.assertTrue(await self.boot())
        await install_history(self, QQ_WINDOW)
        self.assertEqual(await self.qq_reply(), ["我在 QQ 这里"])
        self.telegram.client.send_message.assert_not_awaited()


class LateChannelTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_channel_that_loads_later_is_served_without_a_restart(self):
        self.native.platforms.pop("tg-main")
        self.assertTrue(await self.boot())
        await self.settled()
        session = self.controller.session
        life = session.application.life_runtime
        with patch.object(life, "bind", wraps=life.bind) as bind:
            self.assertEqual(self.connections()[("tg-main", "tg-main")], ("waiting", "host_platform_unavailable"))
            self.native.platforms["tg-main"] = self.telegram
            # AstrBot announces the loaded platform; the running host picks the account up.
            await self.plugin.on_companion_platform_loaded()
            await self.settled()
            self.assertEqual(self.started_channels(bind), ["tg-main"])
        self.assertEqual(self.connections()[("tg-main", "tg-main")], ("online", ""))
        self.assertIs(self.controller.session, session)
        await install_history(self)
        self.assertIn("我在 Telegram 这里", (await self.telegram_reply())[0])
        context = self.event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]["context"]
        self.assertEqual(context.actor.subject.subject_id, f"telegram-user:{OWNER}")

    async def test_a_client_that_connects_later_is_served_without_a_restart(self):
        self.qq_disconnect()
        self.assertTrue(await self.boot())
        await self.settled()
        session = self.controller.session
        life = session.application.life_runtime
        with patch.object(life, "bind", wraps=life.bind) as bind:
            self.assertEqual(self.connections()[("qq-main", "100")], ("waiting", "host_account_offline"))
            self.qq_connect()
            # No event from AstrBot for a reconnecting client: the host's own recovery driver finds it.
            await self.until(lambda: self.started_channels(bind))
            await self.settled()
            self.assertEqual(self.started_channels(bind), ["qq-main"])
        self.assertIs(self.controller.session, session)
        await install_history(self, QQ_WINDOW)
        self.assertEqual(await self.qq_reply(), ["我在 QQ 这里"])

    async def test_a_late_account_whose_start_up_work_fails_is_tried_again_and_holds_up_no_other(self):
        self.controller = self.plugin.staging = self.make_controller(multi_config(
            targets=[dict(target_id="qq-owner", platform_id="qq-main", account_id="100",
                          bot_id="bot-a", participant_id="200"),
                     dict(target_id="qq-friend", platform_id="qq-main", account_id="100",
                          bot_id="bot-a", participant_id="201"),
                     dict(target_id="tg-owner", platform_id="tg-main", account_id="tg-main",
                          bot_id="bot-tg", participant_id=str(OWNER))],
            personas=[dict(bot_id="bot-a", persona_id="original", role_id="role-a"),
                      dict(bot_id="bot-tg", persona_id="original", role_id="role-tg")],
            group_targets=[]))
        self.window_personas["qq-main:FriendMessage:201"] = "original"
        self.qq_disconnect()
        self.assertTrue(await self.boot())
        first, second = [target.participant_id for target in self.controller.settings.primary_targets
                         if target.platform_id == "qq-main"]
        # AstrBot's own lookup fails for the first late window, and not with a companion error.
        failing = {"qq-main:FriendMessage:" + first}
        lookup = self.native.conversation_manager.get_curr_conversation_id

        async def conversation_id(umo):
            if umo in failing:
                raise OSError("synthetic native lookup failure")
            return await lookup(umo)

        self.native.conversation_manager.get_curr_conversation_id = conversation_id
        await self.settled()
        life = self.controller.session.application.life_runtime
        with patch.object(life, "bind", wraps=life.bind) as bind:
            def started():
                return [call.args[0].scope.native_scope_id for call in bind.call_args_list]
            self.qq_connect()
            await self.settled()
            # Upstream binds an account's windows together: the failure holds this account, not Telegram.
            self.assertEqual(self.connections()[("qq-main", "100")], ("error", "host_account_recovery_failed"))
            self.assertEqual(self.connections()[("tg-main", "tg-main")], ("online", ""))
            self.assertEqual((await self.background_text("tg-owner", "role-tg")).status, "delivered")
            failing.clear()
            await self.settled()
            self.assertEqual(sorted(started()), sorted([first, second]))
        self.assertEqual(self.connections()[("qq-main", "100")], ("online", ""))

    async def test_an_account_offline_at_a_dashboard_start_is_served_when_it_comes_online(self):
        self.qq_disconnect()
        # The Dashboard's Start, with no plugin start-up loop behind it.
        self.assertTrue((await self.controller.start()).is_success)
        await self.settled()
        life = self.controller.session.application.life_runtime
        with patch.object(life, "bind", wraps=life.bind) as bind:
            self.qq_connect()
            await self.until(lambda: self.started_channels(bind))
            self.assertEqual(self.started_channels(bind), ["qq-main"])

    async def test_an_account_offline_at_a_mapping_save_is_served_when_it_comes_online(self):
        from copy import deepcopy
        self.assertTrue(await self.boot())
        self.controller.save_plugin_config = lambda document: None
        self.qq_disconnect()
        saved = await self.controller.apply_bindings(deepcopy(self.controller.config))
        self.assertTrue(saved["started"], saved)
        await self.settled()
        life = self.controller.session.application.life_runtime
        with patch.object(life, "bind", wraps=life.bind) as bind:
            self.qq_connect()
            await self.until(lambda: self.started_channels(bind))
            self.assertEqual(self.started_channels(bind), ["qq-main"])


class OperatorStopTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    def picking_up(self):
        return [task for task in asyncio.all_tasks() if not task.done()
                and task.get_name().startswith(("companion:account-connections", "companion:account-recovery"))]

    async def test_a_retry_scheduled_before_a_stop_does_not_start_the_host_again(self):
        self.assertTrue(await self.boot())
        await self.plugin.on_companion_platform_loaded()
        self.assertEqual((await self.controller.stop()).unwrap().state, "stopped")
        self.assertFalse(await self.start_up_retry_finished())

    async def test_a_stop_while_every_account_waits_is_not_undone_when_they_come_back(self):
        self.qq_disconnect()
        self.native.platforms.pop("tg-main")
        # Upstream starts the host with every account offline instead of waiting for one.
        self.assertTrue(await self.boot())
        await self.controller.stop()
        self.qq_connect()
        self.native.platforms["tg-main"] = self.telegram
        await self.plugin.on_companion_platform_loaded()
        await self.controller.recover_accounts()
        # Upstream: an operator Stop holds until the operator starts the host again.
        self.assertFalse(await self.start_up_retry_finished())
        self.assertEqual(self.controller.status()["state"], "stopped")

    async def test_a_host_stopped_while_an_account_waits_is_not_started_again(self):
        self.qq_disconnect()
        self.assertTrue(await self.boot())
        self.assertTrue(self.picking_up())
        life = self.controller.session.application.life_runtime
        with patch.object(life, "bind", wraps=life.bind) as bind:
            await self.controller.stop()
            self.qq_connect()
            await self.controller.recover_accounts()
            self.assertEqual(self.picking_up(), [])
            self.assertEqual(self.started_channels(bind), [])
        self.assertFalse(await self.start_up_retry_finished())
        self.assertEqual(self.controller.status()["state"], "stopped")

    async def test_terminating_ends_the_pick_up_of_a_waiting_account(self):
        self.qq_disconnect()
        self.assertTrue(await self.boot())
        self.assertTrue(self.picking_up())
        await self.controller.terminate()
        self.assertEqual(self.picking_up(), [])


class DroppedChannelTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertTrue(await self.boot())

    async def test_a_dropped_qq_account_leaves_telegram_turns_and_background_text_running(self):
        self.qq_disconnect()
        self.assertEqual((await self.background_text("tg-owner", "role-tg")).status, "delivered")
        refused = await self.background_text("qq-owner", "role-a")
        self.assertEqual(refused.status, "not_sent")
        self.assertEqual(self.qq_calls(), [])
        self.assertEqual(self.telegram.client.send_message.await_count, 1)
        await install_history(self)
        self.assertIn("我在 Telegram 这里", (await self.telegram_reply())[-1])
        self.assertTrue(self.controller.status()["accepting"])

    async def test_a_dropped_telegram_account_leaves_qq_turns_and_background_text_running(self):
        # A stopped Telegram application waits for its bot id; a removed platform waits to load again.
        for drop, code in ((lambda: setattr(self.telegram.application, "running", False), "host_account_offline"),
                           (lambda: self.native.platforms.pop("tg-main"), "host_platform_unavailable")):
            drop()
            self.qq_calls().clear()
            self.assertEqual((await self.background_text("qq-owner", "role-a")).status, "delivered")
            refused = await self.background_text("tg-owner", "role-tg")
            self.assertEqual((refused.status, refused.failure_code), ("not_sent", code))
            self.assertEqual(len(self.qq_calls()), 1)
            self.telegram.client.send_message.assert_not_awaited()
        await install_history(self, QQ_WINDOW)
        self.assertEqual(await self.qq_reply(), ["我在 QQ 这里"])
        self.assertTrue(self.controller.status()["accepting"])


class ChannelHealthTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    def account_health(self):
        status = self.controller.status()
        return (status["state"], status["accepting"], status["last_failure"]), self.connections()

    async def test_status_shows_each_account_and_keeps_the_companion_online(self):
        self.qq_disconnect()
        self.assertTrue(await self.boot())
        await self.settled()
        self.assertEqual(self.account_health(), (("running", True, None), {
            ("qq-main", "100"): ("waiting", "host_account_offline"),
            ("tg-main", "tg-main"): ("online", "")}))
        self.qq_connect()
        self.native.platforms.pop("tg-main")
        await self.settled()
        self.assertEqual(self.account_health(), (("running", True, None), {
            ("qq-main", "100"): ("online", ""),
            ("tg-main", "tg-main"): ("waiting", "host_platform_unavailable")}))


class RefusalTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_channel_of_the_wrong_kind_is_still_refused(self):
        wrong = FakePlatform("tg-main")
        wrong.metadata.name = "synthetic_unsupported"
        self.native.platforms["tg-main"] = wrong
        # Upstream refuses the account, not the host: QQ still starts and is served.
        self.assertTrue(await self.boot())
        await self.settled()
        self.assertEqual(self.connections()[("tg-main", "tg-main")], ("identity_changed", "host_platform_changed"))
        # The refused account's window is never bound, so nothing can be addressed to it.
        with self.assertRaises(TaskError) as refused:
            await self.background_text("tg-owner", "role-tg")
        self.assertEqual(refused.exception.code, "host_context_unavailable")
        self.assertEqual((await self.background_text("qq-owner", "role-a")).status, "delivered")


class UnchangedStartTests(ChannelIsolationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_all_online_accounts_start_together_and_leave_nothing_to_retry(self):
        from companion_v2.role_work.life.coordinator import RoleLifeCoordinator as Life
        with patch.object(Life, "bind", autospec=True, side_effect=Life.bind) as bind:
            self.assertTrue(await self.boot())
            self.assertIsNone(self.plugin._host_start_task)
            await self.plugin.on_companion_platform_loaded()
            self.assertIsNone(self.plugin._host_start_task)
            await self.settled()
            self.assertEqual(sorted(call.args[1].scope.platform for call in bind.call_args_list),
                             ["qq-main", "tg-main"])
        self.assertEqual(self.controller.startup_readiness(), None)
        self.assertEqual(set(self.connections().values()), {("online", "")})
        await install_history(self, QQ_WINDOW, TELEGRAM_WINDOW)
        self.assertEqual(await self.qq_reply(), ["我在 QQ 这里"])
        self.assertIn("我在 Telegram 这里", (await self.telegram_reply())[0])

    async def test_a_single_account_configuration_serves_its_account_once_it_connects(self):
        self.controller = self.plugin.staging = self.make_controller(multi_config(
            targets=[dict(target_id="qq-owner", platform_id="qq-main", account_id="100",
                          bot_id="bot-a", participant_id="200")],
            personas=[dict(bot_id="bot-a", persona_id="original", role_id="role-a")], group_targets=[]))
        self.native.platforms.pop("tg-main")
        self.qq_disconnect()
        # Upstream starts the host with its only account offline instead of waiting for it.
        self.assertTrue(await self.boot())
        await self.settled()
        self.assertEqual(self.connections(), {("qq-main", "100"): ("waiting", "host_account_offline")})
        session = self.controller.session
        self.qq_connect()
        await self.settled()
        self.assertEqual(self.connections(), {("qq-main", "100"): ("online", "")})
        self.assertIs(self.controller.session, session)
        await install_history(self, QQ_WINDOW)
        self.assertEqual(await self.qq_reply(), ["我在 QQ 这里"])


if __name__ == "__main__":
    unittest.main()
