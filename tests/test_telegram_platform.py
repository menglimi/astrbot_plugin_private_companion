"""Telegram private text routing through the official AstrBot adapter."""

from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
import wave

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter, TimedOut
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_adapter import TelegramPlatformAdapter
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.platform_sender import AstrBotPlatformSender
from companion_v2.delivery.models import DeliveryEnvelope
from companion_v2.execution.models import ProviderBinding, TaskError, digest
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
