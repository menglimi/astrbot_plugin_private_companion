"""Telegram group admission facts and host media helpers (offline, no SDK)."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from astrbot_host import group_admission
from astrbot_host import telegram_media


def component(kind, **fields):
    return NS(type=NS(value=kind), **fields)


def telegram_group_event(*, self_id="companion_bot", sender_id="123456789",
                         group_id="-1001234567890", parts=(), client_id=987654321):
    message = NS(
        self_id=self_id,
        sender=NS(user_id=sender_id, nickname="alice"),
        group_id=group_id,
        message_id="42",
        message=list(parts),
        raw_message=NS(),  # a telegram.Update is not a Mapping
    )
    return NS(
        platform_meta=NS(name="telegram", id="telegram"),
        message_obj=message,
        client=NS(id=client_id),
        unified_msg_origin=f"telegram:GroupMessage:{group_id}",
        is_at_or_wake_command=False,
    )


class GroupAdmissionTests(unittest.TestCase):
    def test_bot_username_mention_is_recognised(self):
        event = telegram_group_event(parts=[component("At", qq="companion_bot"),
                                            component("Plain", text=" hi")])
        facts = group_admission.group_trigger_facts(event, "telegram")
        self.assertTrue(facts["mentioned"])
        self.assertFalse(facts["mentions_others"])

    def test_other_username_mention_is_not_the_bot(self):
        event = telegram_group_event(parts=[component("At", qq="someone_else"),
                                            component("Plain", text=" hi")])
        facts = group_admission.group_trigger_facts(event, "telegram")
        self.assertFalse(facts["mentioned"])
        self.assertTrue(facts["mentions_others"])

    def test_reply_to_bot_uses_numeric_bot_id(self):
        event = telegram_group_event(parts=[component("Reply", id="41", sender_id="987654321")])
        facts = group_admission.group_trigger_facts(event, "telegram")
        self.assertTrue(facts["quoted_bot"])
        self.assertEqual(facts["reply_ids"], ("41",))

    def test_reply_to_other_user_is_not_quoted_bot(self):
        event = telegram_group_event(parts=[component("Reply", id="41", sender_id="123456789")])
        facts = group_admission.group_trigger_facts(event, "telegram")
        self.assertFalse(facts["quoted_bot"])

    def test_image_and_plain_facts(self):
        event = telegram_group_event(parts=[component("Image", file="x", url="x"),
                                            component("Plain", text="look")])
        facts = group_admission.group_trigger_facts(event, "telegram")
        self.assertTrue(facts["has_image"])
        self.assertFalse(facts["mentioned"])

    def test_mentioned_account_ids_ignores_telegram_usernames(self):
        event = telegram_group_event(parts=[component("At", qq="alice"),
                                            component("At", qq="123456789")])
        self.assertEqual(group_admission.mentioned_account_ids(event), ("123456789",))


class TelegramVoiceFormatTests(unittest.TestCase):
    def test_audio_format_detection(self):
        self.assertEqual(telegram_media.audio_format(b"OggS\x00"), "ogg")
        self.assertEqual(telegram_media.audio_format(b"RIFF\x00\x00\x00\x00WAVEfmt "), "wav")
        self.assertEqual(telegram_media.audio_format(b"ID3\x04"), "mp3")
        self.assertEqual(telegram_media.audio_format(b"\x00\x00\x00\x18ftypM4A "), "m4a")
        self.assertEqual(telegram_media.audio_format(b"junk"), "")

    def test_ogg_voice_passthrough_writes_host_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            original = telegram_media._temp_media_path
            telegram_media._temp_media_path = lambda suffix: Path(directory) / ("voice" + suffix)
            try:
                path = asyncio.run(telegram_media.telegram_voice_path("base64://T2dnUw=="))
            finally:
                telegram_media._temp_media_path = original
            self.assertIsNotNone(path)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(Path(path).read_bytes(), b"OggS")

    def test_unavailable_audio_returns_none(self):
        self.assertIsNone(asyncio.run(telegram_media.telegram_voice_path("")))
        self.assertIsNone(asyncio.run(telegram_media.telegram_voice_path("base64://not-base64!")))

    def test_voice_document_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            original = telegram_media._temp_media_path
            telegram_media._temp_media_path = lambda suffix: Path(directory) / ("voice" + suffix)
            try:
                # RIFF + size + WAVE header is enough to be recognised as wav.
                fallback = telegram_media.telegram_voice_document(
                    "base64://" + base64.b64encode(b"RIFF\x00\x00\x00\x00WAVEfmt ").decode("ascii"))
            finally:
                telegram_media._temp_media_path = original
            self.assertIsNotNone(fallback)
            path, name = fallback
            self.assertTrue(Path(path).is_file())
            self.assertTrue(name.endswith(".wav"))

    def test_mention_name_only_for_known_targets(self):
        event = telegram_group_event()
        self.assertEqual(telegram_media.telegram_mention_name(event, "123456789"), "alice")
        self.assertEqual(telegram_media.telegram_mention_name(event, "companion_bot"),
                         "companion_bot")
        self.assertEqual(telegram_media.telegram_mention_name(event, "555"), "")


class TelegramMediaPreparationTests(unittest.TestCase):
    def tearDown(self):
        from companion_v2.adapters.astrbot import host_paths

        host_paths.register_host_temp_root(None)

    def test_foreign_host_is_refused(self):
        platform = NS(file_base_url="https://api.telegram.org/file/bot")
        event = NS()
        part = component("Image", file="https://evil.example/x.jpg", url="https://evil.example/x.jpg")
        with self.assertRaises(Exception):
            asyncio.run(telegram_media.prepare_telegram_media(event, platform, [part]))

    def test_relative_path_is_refused(self):
        platform = NS(file_base_url="https://api.telegram.org/file/bot")
        part = component("Image", file="photos/file_1.jpg", url="photos/file_1.jpg")
        with self.assertRaises(Exception):
            asyncio.run(telegram_media.prepare_telegram_media(NS(), platform, [part]))

    def test_host_temp_file_is_left_untouched(self):
        from companion_v2.adapters.astrbot import host_paths

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host_paths.register_host_temp_root(root)
            local = root / "already-downloaded.jpg"
            local.write_bytes(b"jpeg")
            part = component("Image", file=str(local), url=str(local))
            parts = asyncio.run(telegram_media.prepare_telegram_media(NS(), NS(), [part]))
            self.assertIs(parts[0], part)
            self.assertEqual(part.file, str(local))


if __name__ == "__main__":
    unittest.main()
