"""Telegram private text: the bot is the event's ``client``, and a rebuilt bot keeps the chat admitted."""

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import unittest

from telegram import Chat, Message, Update, User
from astrbot.core.message.components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.telegram.tg_event import TelegramPlatformEvent
from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from companion_v2.adapters.astrbot.native import event_native_client, supported_platform_name
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_main_no_reply import ENTRY

OWNER = 5123456789012


def telegram_bot():
    return NS()


class TelegramPlatform:
    def __init__(self):
        self.metadata = NS(name="telegram", id="tg-main")
        self.client = telegram_bot()

    def meta(self):
        return self.metadata

    def get_client(self):
        return self.client


def telegram_event(platform, number=1, text="你好 😀"):
    message = AstrBotMessage()
    message.type, message.self_id, message.message_id = MessageType.FRIEND_MESSAGE, "synthetic_bot", str(number)
    message.sender, message.message, message.message_str = MessageMember(str(OWNER), "Synthetic"), [Plain(text)], text
    message.raw_message = Update(number, message=Message(number, date=datetime.now(timezone.utc),
                                                         chat=Chat(OWNER, "private"),
                                                         from_user=User(OWNER, "Synthetic", False), text=text))
    event = TelegramPlatformEvent(text, message, platform.meta(), str(OWNER), platform.client)
    event.set_extra("activated_handlers", [])
    return event


class TelegramPlatformTests(unittest.TestCase):
    def test_the_bot_is_the_event_client(self):
        event = telegram_event(TelegramPlatform())
        self.assertTrue(supported_platform_name("telegram"))
        self.assertIs(event_native_client(None, event), event.client)


class TelegramPrivateAdmissionTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.platform = self.native.platforms["tg-main"] = TelegramPlatform()
        self.native.selections["tg-main"], self.native.conversations["tg-main"] = "original", "tg-conversation"
        self.window_personas[f"tg-main:FriendMessage:{OWNER}"] = "original"
        self.types = replace(self.types, event=TelegramPlatformEvent)
        self.controller = self.make_controller(multi_config(
            accounts=[dict(platform_id="tg-main", account_id="tg-main", bot_id="bot-tg")],
            targets=[dict(target_id="tg-owner", platform_id="tg-main", account_id="tg-main",
                          bot_id="bot-tg", participant_id=str(OWNER))],
            personas=[dict(bot_id="bot-tg", persona_id="original", role_id="role-tg")], group_targets=[]))

    async def admitted(self, plugin, number):
        event = telegram_event(self.platform, number)
        event.created_at = self.clock.now().timestamp()
        await plugin.receive_companion_message(event)
        observer = self.controller.session.core_reply_observations
        while pending := observer._pending - observer._intervention_tasks:
            await asyncio.gather(*tuple(pending))
            await asyncio.sleep(0)
        prepared = (event.get_extra(CORE_REPLY_CONTEXT_EXTRA) or {}).get("prepared")
        return prepared and prepared["context"].actor.subject.subject_id

    async def test_private_text_is_admitted_with_the_bot_astrbot_rebuilt(self):
        await self.start_host()
        # AstrBot replaces the bot object after repeated polling errors (same token).
        self.platform.client = telegram_bot()
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        self.assertEqual(await self.admitted(plugin, 1), f"telegram-user:{OWNER}")

if __name__ == "__main__":
    unittest.main()
