"""Telegram private text routing through the official AstrBot adapter."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from telegram import Chat, Message, Update, User
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from astrbot_host.display_names import event_person_names
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.execution.models import TaskError
from tests.multi_host_support import MultiFixture, multi_config
from tests.staging_bootstrap_support import wait_for_batch


PLATFORM_ID = "tg-main"
USER_ID = "5123456789012"


def telegram_client(token="424242:synthetic"):
    return NS(token=token, send_chat_action=AsyncMock(), send_message=AsyncMock())


class TelegramPrivateTextTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
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
