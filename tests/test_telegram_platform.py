"""Telegram private text routing through the official AstrBot adapter."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from telegram import Chat, Message, Update, User
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from astrbot_host.recalls import authenticate_notice_route
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_main_no_reply import ENTRY
from tests.staging_bootstrap_support import wait_for_batch


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
