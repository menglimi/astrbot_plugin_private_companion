"""Telegram private capture through the real NativePrivateBridge (offline SDK stub).

Run with the extended sandbox SDK:
    /home/helloworld/astrbot/.venv/bin/python \
        /home/helloworld/astrbot/research/sandbox_sdk_ext.py --run -- \
        tests/test_telegram_capture.py -q
"""

from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace as NS

from astrbot.core.message.components import File, Image, Plain, Record, Reply, Video

from astrbot_host.audio.loader import load_native_audio
from astrbot_host.files.loader import load_native_file
from astrbot_host.media import load_native_image
from astrbot_host.media_components import private_components
from astrbot_host.quotes import load_native_quote
from astrbot_host.telegram_media import prepare_telegram_media
from astrbot_host.video.loader import load_native_video
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.native import (
    HostAccount,
    HostPersona,
    NativePrivateBridge,
    persona_instruction,
)
from companion_v2.execution.models import digest
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal
from tests.test_host_application import HostFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE

PNG_BASE64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
              "hKmMIQAAAABJRU5ErkJggg==")


class FakeTelegramClient:
    def __init__(self):
        self.id = 987654321
        self.actions = []

    async def get_chat(self, chat_id):
        return NS(username="alice", first_name="Alice", title="Companion Group")

    async def get_me(self):
        return NS(username="companion_bot", first_name="Companion")

    async def send_chat_action(self, **kwargs):
        self.actions.append(kwargs)


class FakeTelegramPlatform:
    def __init__(self, platform_id="telegram"):
        self.metadata = NS(name="telegram", id=platform_id)
        self.client = FakeTelegramClient()
        self.file_base_url = "https://api.telegram.org/file/bot"

    def meta(self):
        return self.metadata

    def get_client(self):
        return self.client


class FakeTelegramContext:
    def __init__(self):
        self.platform = FakeTelegramPlatform()
        self.cid = "conversation-1"
        self.persona = {"name": "original", "prompt": "你是原来的角色，比折的陪伴者。"}
        self.selected = "original"
        self.persona_manager = NS(
            resolve_selected_persona=self.resolve,
            personas_v3=[self.persona],
            get_persona_v3_by_id=self.persona_by_id,
        )
        self.conversation_manager = NS(
            get_curr_conversation_id=self.current,
            get_conversation=self.conversation,
        )

    def get_platform_inst(self, platform_id):
        return self.platform if self.platform.meta().id == platform_id else None

    def get_config(self, *, umo):
        return {"provider_settings": {"default_personality": "original"}}

    async def current(self, umo):
        return self.cid

    def persona_by_id(self, persona_id):
        return self.persona if persona_id == "original" else None

    async def conversation(self, umo, cid, *, create_if_not_exists):
        assert not create_if_not_exists
        return NS(persona_id="original")

    async def resolve(self, **kwargs):
        assert kwargs["platform_name"] == "telegram"
        return self.selected, self.persona, None, False


class FakeTelegramEvent:
    def __init__(self, context, *, sender="123456789", text="你好", parts=None,
                 group_id="", message_id="42"):
        self.client = context.platform.client
        self.platform_meta = context.platform.meta()
        kind = NS(value="GroupMessage" if group_id else "FriendMessage")
        session_id = group_id or sender
        self.message_obj = NS(
            type=kind,
            group_id=group_id,
            self_id="companion_bot",
            sender=NS(user_id=sender, nickname="alice"),
            message_id=message_id,
            session_id=session_id,
            message=list(parts) if parts is not None else [Plain(text)],
            raw_message=NS(),
        )
        self.session = NS(message_type=kind, platform_name=self.platform_meta.id,
                          platform_id=self.platform_meta.id, session_id=session_id)
        self.unified_msg_origin = f"{self.platform_meta.id}:{kind.value}:{session_id}"
        self.created_at = NOW.timestamp()
        self._extra = {}
        self.sent = []

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value

    def track_temporary_local_file(self, path):
        pass

    async def send(self, message):
        self.sent.append(message)


class TelegramCaptureFixture(HostFixture):
    def setUp(self):
        super().setUp()
        self.native = FakeTelegramContext()
        principal = AuthenticatedHostPrincipal(INSTALLATION, "host", "telegram",
                                               "123456789", "bot-a", "telegram")
        self.identity.bind(binding_id="telegram-user", principal=principal,
                           subject=self.important.subject, valid_from=NOW,
                           expected_revision=self.identity.revision)
        persona = HostPersona("bot-a", "original", ROLE,
                              digest(persona_instruction(self.native.persona)))
        self.bridge = NativePrivateBridge(
            self.native, version="4.29.1", event_type=FakeTelegramEvent,
            installation_id=INSTALLATION, host_id="host", identities=self.identity,
            accounts=(HostAccount("telegram", "telegram", "bot-a"),),
            personas=(persona,),
            image_type=Image, image_loader=load_native_image,
            reply_type=Reply, quote_loader=load_native_quote,
            audio_type=Record, audio_loader=load_native_audio,
            video_type=Video, video_loader=load_native_video,
            file_type=File, file_loader=load_native_file,
            component_reader=private_components,
            media_preparer=prepare_telegram_media,
        )

    async def capture(self, event=None):
        event = event or FakeTelegramEvent(self.native)
        return await self.bridge.capture(event)


class TelegramPrivateCaptureTests(TelegramCaptureFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_text_capture(self):
        captured = await self.capture(FakeTelegramEvent(self.native, text="晚上好"))
        self.assertTrue(captured.is_success, captured)
        value = captured.unwrap()
        self.assertEqual(value.route.scope.platform, "telegram")
        self.assertEqual(value.route.scope.account_id, "telegram")
        self.assertEqual(value.route.scope.native_scope_id, "123456789")
        self.assertEqual(value.text, "晚上好")
        self.assertEqual(value.principal.native_account_id, "123456789")

    async def test_private_image_capture_uses_base64_original(self):
        event = FakeTelegramEvent(self.native, parts=[
            Image(file="base64://" + PNG_BASE64, url="base64://" + PNG_BASE64),
            Plain("看图"),
        ])
        captured = await self.capture(event)
        self.assertTrue(captured.is_success, captured)
        value = captured.unwrap()
        self.assertEqual(len(value.images), 1)
        self.assertEqual(value.images[0].descriptor.mime_type, "image/png")
        self.assertIn("[图片 1]", value.text)

    async def test_private_quote_capture_from_component(self):
        quoted = Reply(
            id="41",
            sender_id="123456789",
            sender_nickname="alice",
            time=int(NOW.timestamp()),
            message_str="之前说的话",
            text="之前说的话",
            qq="123456789",
            chain=[Plain("之前说的话")],
        )
        event = FakeTelegramEvent(self.native, parts=[quoted, Plain("这句呢")])
        captured = await self.capture(event)
        self.assertTrue(captured.is_success, captured)
        value = captured.unwrap()
        self.assertEqual(len(value.quotes), 1)
        quote = value.quotes[0]
        self.assertEqual(quote.message_id, "41")
        self.assertEqual(quote.sender_id, "123456789")
        self.assertIn("之前说的话", quote.text)

    async def test_private_quote_with_image(self):
        quoted = Reply(
            id="41",
            sender_id="123456789",
            sender_nickname="alice",
            time=int(NOW.timestamp()),
            message_str="",
            text="",
            qq="123456789",
            chain=[Image(file="base64://" + PNG_BASE64, url="base64://" + PNG_BASE64)],
        )
        event = FakeTelegramEvent(self.native, parts=[quoted, Plain("这张图")])
        captured = await self.capture(event)
        self.assertTrue(captured.is_success, captured)
        quote = captured.unwrap().quotes[0]
        self.assertEqual(len(quote.images), 1)

    async def test_private_voice_local_wav_is_host_resolved(self):
        # AstrBot's Telegram adapter hands over a local wav path; the capture
        # must accept it as a host-resolved attachment instead of fetching it.
        import tempfile
        from pathlib import Path

        from companion_v2.adapters.astrbot import host_paths

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host_paths.register_host_temp_root(root)
            try:
                import wave

                wav = root / "voice.wav"
                with wave.open(str(wav), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(16000)
                    stream.writeframes(b"\x00\x00" * 1600)
                event = FakeTelegramEvent(self.native, parts=[Record(file=str(wav), url=str(wav))])
                captured = await self.capture(event)
                self.assertTrue(captured.is_success, captured)
                self.assertIn("[语音 1]", captured.unwrap().text)
            finally:
                host_paths.register_host_temp_root(None)


class TelegramGroupRouteTests(unittest.TestCase):
    def test_group_event_route_accepts_negative_and_topic_ids(self):
        context = FakeTelegramContext()
        for group_id in ("-1001234567890", "-1001234567890#42"):
            event = FakeTelegramEvent(context, group_id=group_id, parts=[Plain("hi")])
            route = group_event_route(event)
            self.assertEqual(route.platform_id, "telegram")
            self.assertEqual(route.account_id, "telegram")
            self.assertEqual(route.sender_id, "123456789")
            self.assertEqual(route.group_id, group_id)
            self.assertEqual(route.session_id, group_id)
            self.assertEqual(route.platform_name, "telegram")


if __name__ == "__main__":
    unittest.main()
