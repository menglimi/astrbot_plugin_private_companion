"""Telegram private text routing through the official AstrBot adapter."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from astrbot.core.message.components import Image, Plain, Reply
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.execution.models import TaskError
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot_host.core_reply_media import _quoted_images, frozen_image_locators
from astrbot_host.media import load_native_image
from tests.multi_host_support import MultiFixture, multi_config
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_conversation_images import image_bytes


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
