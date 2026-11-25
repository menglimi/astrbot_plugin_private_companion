"""Telegram private text routing through the official AstrBot adapter."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from telegram.error import NetworkError, RetryAfter
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.execution.models import TaskError
from tests.multi_host_support import MultiFixture, multi_config
from tests.staging_bootstrap_support import wait_for_batch


PLATFORM_ID = "tg-main"
USER_ID = "5123456789012"


def telegram_client(token="424242:synthetic"):
    return NS(token=token, send_chat_action=AsyncMock(), send_message=AsyncMock())


class TelegramFixture(MultiFixture):
    def setUp(self):
        super().setUp()
        self.platform = TelegramPlatformAdapter.__new__(TelegramPlatformAdapter)
        self.platform.config = {"id": PLATFORM_ID}
        self.platform.application = NS(running=True)
        self.platform.client = telegram_client()
        self.native.platforms[PLATFORM_ID] = self.platform
        self.native.selections[PLATFORM_ID] = "original"
        self.native.conversations[PLATFORM_ID] = "tg-conversation"
        self.window_personas[f"{PLATFORM_ID}:FriendMessage:{USER_ID}"] = "original"
        self.platform_rows = [dict(id=PLATFORM_ID, type="telegram", enable=True)]
        self.native.get_config = lambda umo=None: {"platform": self.platform_rows}
        self.types = replace(self.types, event=TelegramPlatformEvent)
        self.controller = self.make_controller(multi_config(
            accounts=[dict(platform_id=PLATFORM_ID, account_id=PLATFORM_ID, bot_id="bot-tg")],
            targets=[dict(target_id="tg-owner", platform_id=PLATFORM_ID, account_id=PLATFORM_ID,
                          bot_id="bot-tg", participant_id=USER_ID)],
            personas=[dict(bot_id="bot-tg", persona_id="original", role_id="role-tg")],
            group_targets=[],
        ))

    def telegram_event(self, *, sender=USER_ID, group_id=""):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE
        message.group_id = group_id
        message.session_id = group_id or sender
        message.self_id = "synthetic_bot"
        message.message_id = "42"
        message.sender = MessageMember(sender, "Synthetic")
        message.message_str = "你好"
        message.message = [Plain(text=message.message_str)]
        event = self.platform.create_event(message)
        event.created_at = self.clock.now().timestamp()
        return event


class TelegramPrivateTextTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def test_late_loading_keeps_identity_and_delivers_private_text(self):
        del self.native.platforms[PLATFORM_ID]
        await self.start_host()
        target = self.controller.settings.target("tg-owner")
        self.assertEqual(self.controller.authority.actor(target).subject.subject_id, f"telegram-user:{USER_ID}")

        self.native.platforms[PLATFORM_ID] = self.platform
        await self.controller.session.account_recovery.refresh(wait=True)
        event = self.telegram_event()
        self.assertFalse(self.controller.claims(self.telegram_event(sender="123")))
        plan = await self.controller.prepare_core_reply_context(event)
        self.assertTrue(plan.is_success, plan.failure)
        self.assertEqual(plan.unwrap().context.scope.session_id, USER_ID)

        with patch("astrbot.core.platform.platform.Metric.upload", new_callable=AsyncMock):
            result = await self.controller.receive_claimed(event)
            self.assertTrue(result.is_success, result.failure)
            await wait_for_batch(self.controller.session, result.unwrap())
        self.platform.client.send_message.assert_awaited_once()
        sent = self.platform.client.send_message.await_args.kwargs
        self.assertEqual(sent["chat_id"], USER_ID)
        self.assertIn("我在这里", sent["text"])

    async def test_same_bot_can_reconnect_and_another_bot_cannot_claim(self):
        await self.start_host()
        original = self.telegram_event()
        self.assertTrue(self.controller.claims(original))

        self.platform.application.running = False
        self.assertFalse(self.controller.claims(original))
        self.platform.client = telegram_client("424242:rotated-synthetic")
        self.platform.application.running = True
        self.assertFalse(self.controller.claims(original))
        self.assertTrue(self.controller.claims(self.telegram_event()))

        self.platform.client = telegram_client("535353:synthetic")
        self.assertFalse(self.controller.claims(self.telegram_event()))

    def test_disabled_platform_only_supplies_namespace_when_unambiguous(self):
        del self.native.platforms[PLATFORM_ID]
        self.platform_rows[0]["enable"] = False
        self.assertEqual(self.controller._configured_platform_name(PLATFORM_ID), "telegram")
        self.platform_rows.append(dict(id=PLATFORM_ID, type="aiocqhttp", enable=True))
        self.assertEqual(self.controller._configured_platform_name(PLATFORM_ID), "")

    async def test_group_event_has_no_companion_route(self):
        await self.start_host()
        event = self.telegram_event(group_id="-1001234567890")
        self.assertFalse(self.controller.claims(event))
        with self.assertRaises(TaskError) as refused:
            group_event_route(event)
        self.assertEqual(refused.exception.code, "host_route_mismatch")


class TelegramPresenceTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.context, _ = await self.controller.session.synchronize(self.controller.settings.target("tg-owner"))
        self.action = self.platform.client.send_chat_action
        self.action.return_value = True
        self.presence = self.app.presence

    def enable_typing(self, interval=1):
        from astrbot_host.management_identity import dashboard_operator
        from companion_v2.role_work.state.management import StateManagement
        manager = StateManagement(self.app, self.context)
        operator = dashboard_operator(self.app.runtime, "synthetic", at=self.clock.now())
        status = manager.status(operator).unwrap()
        manager.command(operator, dict(action="configure", operation_key="typing-on",
            edit_token=status["edit_token"], role_edit_token=status["role_edit_token"],
            changes={"typing_enabled": True, "typing_refresh_seconds": interval})).unwrap()

    async def test_long_reply_renews_typing_until_it_finishes(self):
        self.enable_typing(interval=10)
        renewed = asyncio.Event()

        async def typing(**kwargs):
            if self.action.await_count >= 2:
                renewed.set()
            return True

        self.action.side_effect = typing
        self.presence.begin_native_reply(self.context, "long-reply")
        worker = self.presence.typing[self.presence.route(self.context)][2]
        try:
            await asyncio.wait_for(renewed.wait(), 8)
        finally:
            self.presence.finish_native_reply(self.context, "long-reply")
        self.action.assert_awaited_with(chat_id=int(USER_ID), action="typing")
        await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), 3)
        sent = self.action.await_count
        self.presence.advance()
        self.assertEqual((self.presence.typing, self.action.await_count), ({}, sent))

    async def test_setting_off_does_not_start_typing(self):
        self.presence.begin_native_reply(self.context, "disabled")
        self.assertIsNotNone(self.presence.active_turn(self.context))
        self.assertFalse(self.presence.typing)
        self.presence.finish_native_reply(self.context, "disabled")
        self.action.assert_not_awaited()

    async def test_disconnect_and_revoked_grant_prevent_typing(self):
        self.enable_typing()
        port = self.presence.port
        self.assertEqual((await port.typing(self.context))["state"], "accepted")
        self.action.reset_mock()
        self.platform.application.running = False
        self.assertEqual((await port.typing(self.context))["state"], "not_started")
        self.platform.application.running = True
        identities = self.controller.session.bridge.identities
        grant = next(item for item in identities.grants_for_subject(self.context.actor.subject)
                     if item.capability == "platform.typing")
        identities.revoke_grant(grant.grant_id, revoked_at=self.clock.now(), expected_revision=identities.revision)
        self.assertEqual((await port.typing(self.context))["state"], "not_started")
        self.action.assert_not_awaited()

    async def test_failed_typing_ends_for_this_reply_only(self):
        self.enable_typing()
        route = "typing:" + self.presence.route(self.context)
        for number, (error, state) in enumerate(((RetryAfter(1), "failed"), (NetworkError("lost"), "unknown"))):
            with self.subTest(state=state):
                self.action.reset_mock(side_effect=True)
                self.action.side_effect = error
                turn = f"reply-{number}"
                self.presence.begin_native_reply(self.context, turn)
                worker = self.presence.typing[self.presence.route(self.context)][2]
                await asyncio.wait_for(worker, 3)
                self.presence.advance()
                self.assertEqual(self.presence.results_for(self.context)[route]["state"], state)
                self.assertIsNotNone(self.presence.active_turn(self.context))
                self.action.assert_awaited_once()
                self.presence.finish_native_reply(self.context, turn)

    async def test_account_status_is_unsupported(self):
        port = self.presence.port
        self.assertEqual((await port.online_status(self.context, "online"))["code"], "presence_unsupported")
        self.assertEqual((await port.custom_status(self.context, "休息"))["code"], "presence_unsupported")
        self.action.assert_not_awaited()
