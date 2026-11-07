"""Telegram platform adaptation: identity, allow-list, quotes and host paths.

These tests exercise the pure adapter layer only; they do not need a real
AstrBot installation.  Host modules that import the SDK are covered by the
sandbox-SDK suite instead.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from companion_v2.adapters.astrbot import host_paths
from companion_v2.adapters.astrbot.native import (
    SUPPORTED_PLATFORM_NAMES,
    event_account_id,
    event_group_id,
    event_peer_id,
    native_client_connected,
    native_user_subject_id,
    platform_client_matches,
    route_id,
)
from companion_v2.adapters.astrbot.telegram import (
    PLATFORM_NAME,
    TelegramQuoteImage,
    TelegramQuoteLocator,
    bot_mention_ids,
    event_bot_username,
    event_client,
    telegram_quote_locator,
)
from companion_v2.execution.models import TaskError


def telegram_event(*, group_id="", self_id="companion_bot", sender_id="123456789",
                   message_id="42", platform_id="telegram", client=None):
    message = NS(
        self_id=self_id,
        sender=NS(user_id=sender_id, nickname="alice"),
        group_id=group_id,
        message_id=message_id,
        message=[],
    )
    return NS(
        platform_meta=NS(name=PLATFORM_NAME, id=platform_id),
        message_obj=message,
        client=client,
        unified_msg_origin=f"{platform_id}:{'GroupMessage' if group_id else 'FriendMessage'}:"
                           f"{group_id or sender_id}",
    )


class FakeClient:
    def __init__(self, identifier=987654321):
        self.id = identifier
        self.is_shutting_down = False


class FakePlatform:
    def __init__(self, name=PLATFORM_NAME, platform_id="telegram", client=None):
        self.metadata = NS(name=name, id=platform_id)
        self.client = client or FakeClient()

    def meta(self):
        return self.metadata

    def get_client(self):
        return self.client


class TelegramAllowListTests(unittest.TestCase):
    def test_telegram_is_an_admitted_platform(self):
        self.assertIn("telegram", SUPPORTED_PLATFORM_NAMES)
        # Existing families stay admitted.
        for name in ("aiocqhttp", "qq_official", "qq_official_webhook", "lark"):
            self.assertIn(name, SUPPORTED_PLATFORM_NAMES)

    def test_event_account_id_uses_platform_instance_id(self):
        event = telegram_event()
        self.assertEqual(event_account_id(event), "telegram")
        # A custom instance id is the account key, not the bot username.
        custom = telegram_event(platform_id="telegram-main")
        self.assertEqual(event_account_id(custom), "telegram-main")

    def test_event_peer_and_group_ids(self):
        private = telegram_event()
        self.assertEqual(event_peer_id(private), "123456789")
        with self.assertRaises(ValueError):
            event_group_id(private)

        group = telegram_event(group_id="-1001234567890")
        self.assertEqual(event_group_id(group), "-1001234567890")
        topic = telegram_event(group_id="-1001234567890#42")
        self.assertEqual(event_group_id(topic), "-1001234567890#42")

    def test_user_subject_prefix(self):
        self.assertEqual(native_user_subject_id("telegram", "123456789"),
                         "telegram-user:123456789")
        self.assertEqual(native_user_subject_id("aiocqhttp", "123456789"),
                         "onebot-user:123456789")
        self.assertEqual(native_user_subject_id("lark", "ou_1"), "lark-user:ou_1")

    def test_negative_and_topic_route_ids_are_stable(self):
        for value in ("-1001234567890", "-1001234567890#42", "123456789"):
            self.assertEqual(route_id(value), value)
        for value in ("", "a:b", "has space", "0"):
            with self.assertRaises(ValueError):
                route_id(value)


class TelegramClientTests(unittest.TestCase):
    def test_event_client_reads_bot_or_client(self):
        onebot = NS(bot=object())
        self.assertIs(event_client(onebot), onebot.bot)
        telegram = telegram_event(client=object())
        self.assertIs(event_client(telegram), telegram.client)
        self.assertIsNone(event_client(NS()))

    def test_platform_client_matches_telegram_identity(self):
        client = FakeClient()
        platform = FakePlatform(client=client)
        self.assertTrue(platform_client_matches(platform, client))
        self.assertFalse(platform_client_matches(platform, FakeClient()))

    def test_native_client_connected_telegram(self):
        platform = FakePlatform()
        self.assertTrue(native_client_connected(platform, "telegram"))
        platform.client.is_shutting_down = True
        self.assertFalse(native_client_connected(platform, "telegram"))

    def test_bot_mention_ids_include_username(self):
        event = telegram_event(self_id="companion_bot")
        self.assertEqual(bot_mention_ids(event, "telegram"),
                         frozenset({"telegram", "companion_bot"}))
        self.assertEqual(event_bot_username(event), "companion_bot")


class TelegramQuoteTests(unittest.TestCase):
    @staticmethod
    def reply(*, message_id="41", sender_id="123456789", text="earlier words",
              images=(), time=1700000000):
        chain = []
        for index, value in enumerate(images):
            chain.append(NS(type=NS(value="Image"), file=value, url=value))
        return NS(
            id=message_id,
            sender_id=sender_id,
            sender_nickname="alice",
            time=time,
            message_str=text,
            text=text,
            qq=sender_id,
            chain=chain,
            type=NS(value="Reply"),
        )

    def test_quote_locator_freezes_text_and_images(self):
        locator = telegram_quote_locator(2, self.reply(images=("https://api.telegram.org/file/botx/a.jpg",)))
        self.assertIsInstance(locator, TelegramQuoteLocator)
        self.assertEqual(locator.position, 2)
        self.assertEqual(locator.message_id, "41")
        self.assertEqual(locator.sender_id, "123456789")
        self.assertEqual(locator.sent_at, 1700000000)
        self.assertEqual(locator.text, "earlier words")
        self.assertEqual(locator.images,
                         (TelegramQuoteImage(0, "https://api.telegram.org/file/botx/a.jpg"),))

    def test_image_only_quote_gets_a_placeholder_body(self):
        locator = telegram_quote_locator(0, self.reply(text="", images=("https://x/a.jpg",)))
        self.assertIn("[图片 1]", locator.text)

    def test_unusable_quotes_are_refused(self):
        self.assertIsNone(telegram_quote_locator(0, self.reply(text="", images=())))
        self.assertIsNone(telegram_quote_locator(0, self.reply(message_id="not-a-number")))
        self.assertIsNone(telegram_quote_locator(0, self.reply(sender_id="alice")))

    def test_read_telegram_quote_builds_captured_quote(self):
        import asyncio

        from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor

        import hashlib

        content = b"\x89PNG"

        async def image_loader(locator):
            self.assertEqual(locator.position, 0)
            descriptor = ImageDescriptor(0, "image/png", 1, 1, len(content),
                                         hashlib.sha256(content).hexdigest())
            return CapturedImage(descriptor, content)

        locator = telegram_quote_locator(1, self.reply(images=("https://x/a.png",)))
        quote = asyncio.run(_read(locator, image_loader))
        self.assertEqual(quote.message_id, "41")
        self.assertEqual(quote.sender_id, "123456789")
        self.assertEqual(len(quote.images), 1)


async def _read(locator, image_loader):
    from companion_v2.adapters.astrbot.telegram import read_telegram_quote

    return await read_telegram_quote(locator, image_loader=image_loader)


class HostTempPathTests(unittest.TestCase):
    def tearDown(self):
        host_paths.register_host_temp_root(None)

    def test_only_registered_temp_root_is_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inside = root / "voice.wav"
            inside.write_bytes(b"RIFF")
            outside = root.parent / "outside-voice.wav"
            outside.write_bytes(b"RIFF")
            try:
                self.assertFalse(host_paths.is_host_temp_file(str(inside)))
                host_paths.register_host_temp_root(root)
                self.assertTrue(host_paths.is_host_temp_file(str(inside)))
                self.assertFalse(host_paths.is_host_temp_file(str(outside)))
                self.assertFalse(host_paths.is_host_temp_file("base64://AAAA"))
                self.assertFalse(host_paths.is_host_temp_file("relative/path.wav"))
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
