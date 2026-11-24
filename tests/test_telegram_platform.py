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
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.native import event_native_client, supported_platform_name
from companion_v2.execution.models import TaskError, require
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_main_no_reply import ENTRY

OWNER = 5123456789012


def telegram_bot(token="424242:synthetic"):
    return NS(token=token)


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

    def test_group_messages_are_not_served(self):
        event = telegram_event(TelegramPlatform())
        event.message_obj.type = MessageType.GROUP_MESSAGE
        event.session.message_type = MessageType.GROUP_MESSAGE
        with self.assertRaises(TaskError) as refused:
            group_event_route(event)
        self.assertEqual(refused.exception.code, "host_route_mismatch")


class TelegramHostFixture(MultiFixture):
    def setUp(self):
        super().setUp()
        self.platform = self.native.platforms["tg-main"] = TelegramPlatform()
        self.native.selections["tg-main"], self.native.conversations["tg-main"] = "original", "tg-conversation"
        self.window_personas[f"tg-main:FriendMessage:{OWNER}"] = "original"
        self.types = replace(self.types, event=TelegramPlatformEvent)
        self.native.get_config = lambda umo=None: {"platform": [dict(id="tg-main", type="telegram", enable=True)]}
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

    def bound_person(self):
        authority, target = self.controller.authority, self.controller.settings.target("tg-owner")
        return require(authority.identities.resolve_authenticated(
            authority.principal(target), observed_at=self.clock.now())).subject.subject_id

    def started_plugin(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging, plugin._host_start_task = self.controller, None
        return plugin

    async def connect_late(self, plugin):
        """AstrBot loads the channel, then announces it to the running plugin."""
        self.native.platforms["tg-main"] = self.platform
        await plugin.on_companion_platform_loaded()
        if plugin._host_start_task is not None:
            await asyncio.wait_for(plugin._host_start_task, timeout=20)


class TelegramPrivateAdmissionTests(TelegramHostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_text_is_admitted_with_the_bot_astrbot_rebuilt(self):
        await self.start_host()
        # AstrBot replaces the bot object after repeated polling errors (same token).
        self.platform.client = telegram_bot()
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        self.assertEqual(await self.admitted(plugin, 1), f"telegram-user:{OWNER}")

    async def test_private_text_is_refused_once_another_bot_answers(self):
        await self.start_host()
        self.platform.client = telegram_bot("535353:synthetic")
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = self.controller
        self.assertIsNone(await self.admitted(plugin, 1))

    async def test_a_present_channel_without_a_bot_joins_once_it_has_one(self):
        bot, self.platform.client = self.platform.client, None
        await self.start_host()
        self.platform.client, plugin = bot, self.started_plugin()
        await self.connect_late(plugin)
        self.assertEqual(await self.admitted(plugin, 1), f"telegram-user:{OWNER}")

    async def test_first_start_before_the_channel_loads_keeps_the_telegram_person(self):
        del self.native.platforms["tg-main"]
        plugin = self.started_plugin()
        self.assertTrue(await plugin._try_start_host())
        self.assertEqual(self.bound_person(), f"telegram-user:{OWNER}")
        await self.connect_late(plugin)
        self.assertEqual(await self.admitted(plugin, 1), f"telegram-user:{OWNER}")

    async def test_first_start_with_disabled_channel_keeps_the_telegram_person(self):
        del self.native.platforms["tg-main"]
        self.native.get_config = lambda umo=None: {"platform": [dict(id="tg-main", type="telegram", enable=False)]}
        self.assertTrue(await self.started_plugin()._try_start_host())
        self.assertEqual(self.bound_person(), f"telegram-user:{OWNER}")

    def test_disabled_telegram_row_does_not_claim_another_platform(self):
        del self.native.platforms["tg-main"]
        rows = [dict(id="tg-main", type="telegram", enable=False),
                dict(id="tg-main", type="aiocqhttp", enable=True)]
        self.native.get_config = lambda umo=None: {"platform": rows}
        self.assertEqual(self.controller._configured_platform_name("tg-main"), "")
        self.assertEqual(self.controller._configured_platform_name("other"), "")

    async def test_restart_before_the_channel_loads_keeps_the_telegram_person(self):
        await self.start_host()
        await self.controller.terminate()
        del self.native.platforms["tg-main"]
        self.controller = self.make_controller(self.controller.config)
        plugin = self.started_plugin()
        self.assertTrue(await plugin._try_start_host())
        self.assertEqual(self.bound_person(), f"telegram-user:{OWNER}")
        await self.connect_late(plugin)
        self.assertEqual(await self.admitted(plugin, 1), f"telegram-user:{OWNER}")


if __name__ == "__main__":
    unittest.main()
