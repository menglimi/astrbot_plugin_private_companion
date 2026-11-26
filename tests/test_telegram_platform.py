"""Telegram private text routing through the official AstrBot adapter."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
import asyncio
import wave
import time
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from telegram import Chat, Message, Update, User
from astrbot.core.message.components import Face, Image, Plain, Reply
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from astrbot_host.recalls import authenticate_notice_route
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.execution.models import ProviderBinding, TaskError, digest
from companion_v2.foundation.refs import ScopeKind
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_main_no_reply import ENTRY
from tests.staging_bootstrap_support import wait_for_batch
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TimedOut
from companion_v2.adapters.astrbot.native import native_client_connected
from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
from companion_v2.delivery.models import DeliveryEnvelope
from astrbot.core.message.message_event_result import MessageChain
from astrbot_host.core_reply_media_delivery import native_reply_messages, native_wire_message
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.relay_pending import add_task, claim_private_receipts
from astrbot_host.telegram_forward import label_telegram_forward
from companion_v2.conversation.media.output_settings import is_rule_image_request
from astrbot_host.display_names import event_person_names
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot_host.core_reply_media import _quoted_images, frozen_image_locators
from astrbot_host.media import load_native_image
from tests.test_conversation_images import image_bytes


PLATFORM_ID = "tg-main"
USER_ID = "5123456789012"
STRANGER_ID = "5123456789013"


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

    def telegram_event(self, *, sender=USER_ID, group_id="", text="你好", number=42):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE
        message.group_id = group_id
        message.session_id = group_id or sender
        message.self_id = "synthetic_bot"
        message.message_id = str(number)
        message.sender = MessageMember(sender, "Synthetic")
        message.message_str = text
        message.message = [Plain(text=message.message_str)]
        if not group_id:
            message.raw_message = Update(number, message=Message(
                number, date=datetime.now(timezone.utc), chat=Chat(int(sender), "private"),
                from_user=User(int(sender), "Synthetic", False), text=text))
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

    def named_event(self, first, last=None, username=None, *, sender=USER_ID):
        event = self.telegram_event()
        event.message_obj.sender.nickname = "Unknown"
        sent = datetime.now(timezone.utc)
        chat = Chat(int(USER_ID), "private")
        quoted = Message(1, date=sent, chat=chat, from_user=User(int(USER_ID), "被引用", False), text="旧消息")
        event.message_obj.raw_message = Update(42, message=Message(
            42, date=sent, chat=chat, reply_to_message=quoted, text="@other",
            from_user=User(int(sender), first, False, last_name=last, username=username)))
        return event

    def test_names_come_only_from_the_original_message_sender(self):
        for first, last, username, sender, expected in (
            ("小", "林", "handle", USER_ID, "小 林"),
            ("小林", None, "handle", USER_ID, "小林"),
            ("", None, "handle", USER_ID, "handle"),
            ("", None, None, USER_ID, ""),
            ("Unknown", None, "Unknown", USER_ID, ""),
            ("Unknown", "Artist", None, USER_ID, "Unknown Artist"),
            ("其他人", None, "other", "5123456789013", ""),
        ):
            with self.subTest(expected=expected, sender=sender):
                event = self.named_event(first, last, username, sender=sender)
                self.assertEqual(event_person_names(event), (expected, ""))

    async def test_session_directory_ignores_unknown_and_keeps_the_observed_name(self):
        directory = self.controller.session_topology
        for first, expected in (("", ""), ("小林", "小林"), ("", "小林")):
            await directory.observe(self.named_event(first))
            rows = json.loads(directory.path.read_text(encoding="utf-8"))
            self.assertEqual([row.get("display_name", "") for row in rows.values()], [expected])

    async def test_directory_reads_old_observations_without_the_placeholder(self):
        directory = self.controller.session_topology
        event = self.named_event("小林")
        await directory.observe(event)
        rows = json.loads(directory.path.read_text(encoding="utf-8"))
        stored = next(iter(rows.values()))
        for name, expected in (("Unknown", ""), ("Unknown Artist", "Unknown Artist")):
            stored["display_name"] = name
            directory.path.write_text(json.dumps(rows), encoding="utf-8")
            windows = await directory.inventory()
            self.assertEqual([row.get("display_name", "") for row in windows
                              if row.get("unified_msg_origin") == event.unified_msg_origin], [expected])


class TelegramPrivateCommandTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await self.start_host()
        self.plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        self.plugin.staging, self.plugin._host_start_task = self.controller, None
        self.platform.send_by_session = AsyncMock()
        self.number = 0

    async def dispatch(self, text, peer=USER_ID):
        self.number += 1
        event = self.telegram_event(sender=peer, text=text, number=self.number)
        event.set_extra("activated_handlers", [])
        await self.plugin.receive_companion_message(event)
        return event

    def sent_text(self, peer=USER_ID):
        session, message = self.platform.send_by_session.await_args.args
        self.assertEqual(str(session), f"{PLATFORM_ID}:FriendMessage:{peer}")
        return message.chain[0].text

    async def test_calendar_commands_run_once_and_suppress_the_model(self):
        planner = AsyncMock()
        self.controller.session.plan_calendar = planner
        for prefix in ("", "陪伴 "):
            for action in ("生成", "刷新", "重新生成"):
                for period, mode, label in (("", "daily", "今天的日程"), ("周", "horizon", "未来日程")):
                    with self.subTest(prefix=prefix, action=action, period=period):
                        planner.return_value = {"state": "succeeded", "saved": 2, "mode": mode}
                        event = await self.dispatch(prefix + action + period + "日程")
                        self.assertTrue(event.call_llm)
                        planner.assert_awaited_once_with(event, self.controller.settings.target("tg-owner"),
                                                         mode=mode, refresh=action != "生成")
                        self.platform.send_by_session.assert_awaited_once()
                        self.assertEqual(self.sent_text(), label + "已生成，共写入 2 项。")
                        planner.reset_mock()
                        self.platform.send_by_session.reset_mock()

    async def test_stranger_notice_honors_switch_and_cooldown(self):
        peer = STRANGER_ID
        defaults = {"stranger_notice": False, "denied_reply": "陌生人提示",
                    "stranger_daily_cap": 3, "notice_cooldown_seconds": 60}
        self.controller.session.application.private_access_settings.defaults_installation = Mock(return_value=defaults)
        await self.dispatch("你好", peer)
        self.platform.send_by_session.assert_not_awaited()
        defaults["stranger_notice"] = True
        await self.dispatch("你好", peer)
        await self.dispatch("你好", peer)
        self.platform.send_by_session.assert_awaited_once()
        self.assertEqual(self.sent_text(peer), "陌生人提示")
        self.clock.advance(timedelta(seconds=60))
        await self.dispatch("你好", peer)
        self.assertEqual(self.platform.send_by_session.await_count, 2)

    async def test_refused_stranger_notice_remains_due(self):
        peer = STRANGER_ID
        self.controller.session.application.private_access_settings.defaults_installation = Mock(return_value={
            "stranger_notice": True, "denied_reply": "陌生人提示",
            "stranger_daily_cap": 1, "notice_cooldown_seconds": 60,
        })
        self.platform.send_by_session.side_effect = [RuntimeError("refused"), None]
        for _ in range(3):
            await self.dispatch("你好", peer)
        self.assertEqual(self.platform.send_by_session.await_count, 2)

    async def test_revoked_dialogue_honors_blank_copy_and_cooldown(self):
        authority = self.controller.authority
        context = authority.context("tg-owner", "role-tg")
        authority.identities.revoke_grant(authority.grant_id(context.scope, "conversation.reply"),
            revoked_at=self.clock.now(), expected_revision=authority.identities.revision)
        defaults = {"denied_reply": "", "notice_cooldown_seconds": 60}
        self.controller.session.application.private_notice.settings = NS(defaults=lambda context: defaults)
        await self.dispatch("你好")
        self.platform.send_by_session.assert_not_awaited()
        defaults["denied_reply"] = "权限提示"
        await self.dispatch("你好")
        await self.dispatch("你好")
        self.platform.send_by_session.assert_awaited_once()
        self.assertEqual(self.sent_text(), "权限提示")
        self.clock.advance(timedelta(seconds=60))
        await self.dispatch("你好")
        self.assertEqual(self.platform.send_by_session.await_count, 2)

    async def test_reset_requires_admin_and_confirmation(self):
        authority = self.controller.authority
        context = authority.context("tg-owner", "role-tg")
        from astrbot_host.reset_command import receive_reset_command
        plain = self.telegram_event(text="全部重置")
        self.assertIsNone(await receive_reset_command(self.controller, plain))
        self.platform.send_by_session.assert_not_awaited()
        event = await self.dispatch("陪伴 全部重置")
        self.assertTrue(event.call_llm)
        self.assertIn("只允许安装核心管理员", self.sent_text())
        authority.provision_scope(authority.settings.target("tg-owner"), context.scope,
                                  context.role_binding.role, core_admin=True)
        application = self.controller.session.application
        reset = application.reset_conversation_window = Mock(wraps=application.reset_conversation_window)
        await self.dispatch("陪伴 全部重置")
        preview = self.sent_text()
        self.assertIn("尚未执行", preview)
        reset.assert_not_called()
        confirmation = preview.split("确认请回复「")[1].split("」")[0]
        await self.dispatch(confirmation)
        reset.assert_called_once()
        self.assertIn("已执行", self.sent_text())

    async def test_route_requires_matching_client_connection_and_session(self):
        def route(event):
            return authenticate_notice_route(self.controller, event, ScopeKind.PRIVATE, expected_post="message")

        event = self.telegram_event()
        self.assertIs(route(event).platform, self.platform)
        event.client = telegram_client()
        self.assertIsNone(route(event))
        event = self.telegram_event()
        event.session.session_id = STRANGER_ID
        self.assertIsNone(route(event))
        event = self.telegram_event()
        event.message_obj.raw_message = self.telegram_event(sender=STRANGER_ID).message_obj.raw_message
        self.assertIsNone(route(event))
        event = self.telegram_event()
        self.platform.application.running = False
        with self.assertRaises(TaskError) as refused:
            route(event)
        self.assertEqual(refused.exception.code, "host_account_offline")

    async def test_calendar_reply_survives_rebuild_of_the_same_bot(self):
        async def plan(event, target, *, mode, refresh):
            self.platform.client = telegram_client()
            return {"state": "succeeded", "saved": 1}

        self.controller.session.plan_calendar = plan
        await self.dispatch("生成日程")
        self.platform.send_by_session.assert_awaited_once()
        self.assertIn("1", self.sent_text())

    async def test_calendar_reply_requires_the_same_bot_after_planning(self):
        async def plan(event, target, *, mode, refresh):
            self.platform.client = telegram_client("535353:synthetic")
            return {"state": "succeeded", "saved": 1}

        self.controller.session.plan_calendar = plan
        event = await self.dispatch("生成日程")
        self.assertTrue(event.call_llm)
        self.platform.send_by_session.assert_not_awaited()

    async def test_phone_confirmation_reaches_the_configured_checkout(self):
        actions = self.controller.session.application.phone_actions
        actions.answer_checkout = Mock()
        event = await self.dispatch("确认支付")
        self.assertTrue(event.call_llm)
        actions.answer_checkout.assert_called_once()
        context, token = actions.answer_checkout.call_args.args
        self.assertEqual(context.scope.native_scope_id, USER_ID)
        self.assertIsNone(token)
        self.assertTrue(actions.answer_checkout.call_args.kwargs["approve"])
        self.assertEqual(self.sent_text(), "收到确认，我会先核对手机上的订单。")


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


class TelegramConnectionTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.platform.application.updater = NS(running=True)
        await self.start_host()
        self.context, _ = await self.controller.session.synchronize(self.controller.settings.target("tg-owner"))
        self.binding = ProviderBinding("message.delivery", "synthetic-sender", "1", digest("synthetic"))
        self.sender = AstrBotPlatformSender(self.controller.session.bridge, binding=self.binding,
            version="4.29.4", reverse_api_type=object, authorize=lambda: None)

    async def send(self, text="你好"):
        envelope = DeliveryEnvelope("part-tg", "op-tg", self.context, 1, text, digest(text),
                                    self.binding, before_dispatch=lambda: True)
        return await self.sender.send(envelope)

    async def test_connection_tracks_polling_shutdown_and_rebuild(self):
        for started, running, polling, terminating, expected in (
            (True, True, True, False, True),
            (True, True, False, False, False),
            (True, True, True, True, False),
            (False, True, True, False, False),
            (True, False, True, False, False),
            (True, True, True, False, True),
        ):
            with self.subTest(started=started, running=running, polling=polling, terminating=terminating):
                self.platform._application_started, self.platform._terminating = started, terminating
                self.platform.application.running, self.platform.application.updater.running = running, polling
                self.assertEqual(native_client_connected(self.platform, PLATFORM_ID), expected)

    async def test_background_send_waits_for_polling(self):
        self.platform.application.updater.running = False
        receipt = await self.send()
        self.assertEqual((receipt.status, receipt.failure_code), ("not_sent", "host_account_offline"))
        self.platform.client.send_message.assert_not_awaited()
        self.platform.application.updater.running = True
        with patch("astrbot.core.platform.platform.Metric.upload", new_callable=AsyncMock):
            self.assertEqual((await self.send()).status, "delivered")
        self.platform.client.send_message.assert_awaited_once()


class TelegramDeliveryTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await self.start_host()
        self.context, _ = await self.controller.session.synchronize(self.controller.settings.target("tg-owner"))
        self.binding = ProviderBinding("message.delivery", "synthetic-sender", "1", digest("synthetic"))
        self.sender = AstrBotPlatformSender(self.controller.session.bridge, binding=self.binding,
            version="4.29.4", reverse_api_type=object, authorize=lambda: None)

    async def send(self, text="你好"):
        envelope = DeliveryEnvelope("part-tg", "op-tg", self.context, 1, text, digest(text),
                                    self.binding, before_dispatch=lambda: True)
        return await self.sender.send(envelope)

    async def test_definite_refusals_are_not_sent_with_a_safe_reason(self):
        token = self.platform.client.token
        for error, reason in (
            (Forbidden("Bot was blocked by the user"), "telegram_bot_blocked"),
            (Forbidden("User is deactivated"), "telegram_forbidden"),
            (BadRequest("Chat not found"), "telegram_chat_not_found"),
            (BadRequest("Message is too long"), "telegram_bad_request"),
            (InvalidToken(token), "telegram_invalid_token"),
            (RetryAfter(42), "telegram_rate_limited"),
        ):
            with self.subTest(reason=reason):
                self.platform.client.send_message.side_effect = error
                with self.assertLogs("companion_v2.adapters.astrbot.platform_sender", "WARNING") as logs:
                    receipt = await self.send()
                self.assertEqual((receipt.status, receipt.failure_code), ("not_sent", reason))
                self.assertNotIn(token, " ".join(logs.output))

    async def test_transport_errors_are_unknown(self):
        for error in (TimedOut(), NetworkError("connection lost")):
            with self.subTest(error=type(error).__name__):
                self.platform.client.send_message.reset_mock()
                self.platform.client.send_message.side_effect = error
                receipt = await self.send()
                self.assertEqual((receipt.status, receipt.failure_code), ("unknown", None))
                self.platform.client.send_message.assert_awaited_once()

    async def test_refusal_after_first_text_chunk_is_unknown(self):
        self.platform.client.send_message.side_effect = [None, Forbidden("bot was blocked by the user")]
        receipt = await self.send("x" * 4097)
        self.assertEqual((receipt.status, receipt.failure_code), ("unknown", None))
        self.assertEqual(self.platform.client.send_message.await_count, 2)

    async def test_refusal_after_text_before_voice_is_unknown(self):
        audio = self.root / "synthetic.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", ""))
            stream.writeframes(bytes(160))
        self.sender.voice = AsyncMock(return_value={"type": "record", "data": {"file": str(audio)}})
        self.platform.client.send_voice = AsyncMock(side_effect=RetryAfter(42))
        receipt = await self.send()
        self.assertEqual((receipt.status, receipt.failure_code), ("unknown", None))
        self.platform.client.send_voice.assert_awaited_once()
        self.platform.client.send_message.assert_awaited_once()


class TelegramFaceTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    async def test_reply_face_is_shown_as_text(self):
        event = self.telegram_event()
        with patch("astrbot.core.platform.astr_message_event.Metric.upload", new_callable=AsyncMock):
            for part in native_reply_messages(event, MessageChain(chain=[Plain(text="晚安"), Face(id=14)])):
                await event.send(await native_wire_message(event, part))
        # AstrBot sends MarkdownV2, so the brackets are escaped on the wire.
        sent = [call.kwargs["text"] for call in self.platform.client.send_message.await_args_list]
        self.assertEqual(sent, ["晚安", "\\[QQ表情 14\\]"])

    async def test_reply_without_a_face_is_unchanged(self):
        message = MessageChain(chain=[Plain(text="晚安"), Image(file="base64://AAAA")])
        self.assertIs(await native_wire_message(self.telegram_event(), message), message)

    def test_background_face_is_shown_as_text(self):
        plan = NS(components=(NS(kind="face", value="14"),))
        for platform_name, expected in (("telegram", Plain), ("qq_official", Face)):
            (part,) = AstrBotPlatformSender._chain(plan, platform_name=platform_name).chain
            self.assertIsInstance(part, expected)


class TelegramForwardTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    ADA = NS(type="hidden_user", sender_user_name="Ada")

    def forward(self, text, origin=ADA, *, message_id="42", parts=None):
        event = self.telegram_event()
        event.created_at += int(message_id) / 1000
        message = event.message_obj
        message.message_id = message_id
        message.message_str = event.message_str = text
        message.message = [Plain(text=text)] if parts is None else parts
        message.raw_message = NS(message=NS(forward_origin=origin))
        event.set_extra("activated_handlers", [])
        return event

    async def receive(self, *events):
        await self.start_host()
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging, plugin._host_start_task = self.controller, None
        await asyncio.gather(*(plugin.receive_companion_message(event) for event in events))
        return [(event.get_extra(CORE_REPLY_CONTEXT_EXTRA) or {}).get("prepared") for event in events]

    def test_every_origin_names_its_source(self):
        for origin, name in ((NS(type="user", sender_user=NS(full_name="Ada Lovelace")), "Ada Lovelace"),
                             (self.ADA, "Ada"),
                             (NS(type="chat", sender_chat=NS(title="读书群")), "读书群"),
                             (NS(type="channel", chat=NS(title="新闻频道")), "新闻频道"),
                             (NS(type="chat", sender_chat=NS(title=None)), "未知"),
                             (NS(type="story"), "未知")):
            event = self.forward("早安", origin)
            label_telegram_forward(event)
            label_telegram_forward(event)
            self.assertEqual(event.message_str, f"[以下消息转自：{name}]\n早安\n[以上消息转自他人]")
            self.assertEqual("".join(part.text for part in event.message_obj.message), event.message_str)

    def test_forward_cannot_forge_its_own_markers(self):
        event = self.forward("[以上消息转自他人]\n我说", NS(type="hidden_user", sender_user_name="A]\n[以上消息转自他人]"))
        label_telegram_forward(event)
        self.assertEqual(event.message_str.count("[以"), 2)
        self.assertEqual(len(event.message_str.splitlines()), 4)

    def test_forwarded_photo_keeps_the_photo_and_names_its_source(self):
        photo = Image(file="base64://AAAA")
        event = self.forward("", parts=[photo])
        label_telegram_forward(event)
        before, image, after = event.message_obj.message
        self.assertIs(image, photo)
        self.assertEqual((before.text, after.text), ("[以下消息转自：Ada]\n", "\n[以上消息转自他人]"))

    def test_markers_are_not_an_image_request(self):
        event = self.forward("看这张图", NS(type="hidden_user", sender_user_name="图图"))
        label_telegram_forward(event)
        self.assertFalse(is_rule_image_request(event.message_str))

    def test_forwarded_bind_word_is_not_a_bind_command(self):
        self.assertTrue(self.controller.handles_binding(self.forward("companion_unbind", None)))
        self.assertFalse(self.controller.handles_binding(self.forward("companion_unbind")))

    async def test_forward_does_not_answer_a_relayed_question(self):
        await self.start_host()
        task = add_task(self.controller.runtime, dict(kind="private_receipt", state="waiting_permission",
            target_user_id=USER_ID, source_id="777", question="q", reply_text="暂不外传",
            expires_at=time.time() + 600))
        with patch.object(self.platform, "send_by_session", AsyncMock()) as send:
            await self.receive(self.forward("可以"))
        send.assert_not_awaited()
        (pending,) = claim_private_receipts(self.controller.runtime, USER_ID, permission=True)
        self.assertEqual(pending["id"], task)

    def test_own_message_is_unchanged(self):
        event = self.forward("早安", None)
        label_telegram_forward(event)
        self.assertEqual((event.message_str, event.message_obj.message[0].text), ("早安", "早安"))

    async def test_forwarded_command_is_read_as_content(self):
        (prepared,) = await self.receive(self.forward("手机屏幕"))
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared["native_input"].text, "[以下消息转自：Ada]\n手机屏幕\n[以上消息转自他人]")

    async def test_merged_reply_separates_the_forward_from_the_comment(self):
        prepared = await self.receive(self.forward("天气转凉", message_id="42"),
                                      self.forward("你怎么看", None, message_id="43"))
        (merged,) = [item for item in prepared if item]
        self.assertEqual(merged["native_input"].text,
                         "[以下消息转自：Ada]\n天气转凉\n[以上消息转自他人]\n你怎么看")


class TelegramQuotedImageTests(TelegramFixture, unittest.IsolatedAsyncioTestCase):
    def quoting(self, sender, chain):
        event = self.telegram_event()
        event.message_obj.message.insert(0, Reply(id="7", sender_id=sender, chain=chain, time=1700000000))
        return event

    async def resolve(self, event):
        await self.start_host()
        plan = await self.controller.prepare_core_reply_context(event)
        (quote,) = frozen_image_locators(event)
        return await _quoted_images(quote, plan.unwrap().context, AsyncMock())

    async def test_quoted_photo_is_read_from_the_update_itself(self):
        source = image_bytes()
        for sender, recorded in ((USER_ID, USER_ID), ("424242", PLATFORM_ID)):
            with self.subTest(sender=sender), TemporaryDirectory(dir=get_astrbot_temp_path()) as temp:
                path = Path(temp, "photo.png")
                path.write_bytes(source)
                images, attribution = await self.resolve(self.quoting(sender, [Image(file=str(path))]))
                self.assertEqual((await load_native_image(images[0])).content, source)
                self.assertEqual(attribution["original_sender"], recorded)
                self.assertIsNone(attribution["sent_at_unix"])

    async def test_quoted_file_outside_the_host_cache_is_not_read(self):
        with TemporaryDirectory() as temp:
            path = Path(temp, "photo.png")
            path.write_bytes(image_bytes())
            images, _ = await self.resolve(self.quoting(USER_ID, [Image(file=str(path))]))
            with self.assertRaises(TaskError):
                await load_native_image(images[0])

    async def test_quote_from_someone_else_is_refused(self):
        with self.assertRaises(TaskError) as refused:
            await self.resolve(self.quoting("987654321", [Image(file="base64://AAAA")]))
        self.assertEqual(refused.exception.code, "host_quote_scope_mismatch")

    def test_quoted_text_has_no_image_to_look_up(self):
        self.assertEqual(frozen_image_locators(self.quoting(USER_ID, [Plain(text="早安")])), ())
