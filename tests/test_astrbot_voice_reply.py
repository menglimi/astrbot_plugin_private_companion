"""Offline OneBot component ordering and native TTS provider behavior."""

import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import struct
from types import SimpleNamespace as NS
import unittest
import wave
from unittest.mock import patch

from astrbot_host.voice import NativeVoiceReply
from companion_v2.conversation.delivery import DeliveryEnvelope
from companion_v2.conversation.media.models import ImageDescriptor, PromptImage, OWNER
from companion_v2.conversation.voice_reply import VoiceReplyDelivery
from companion_v2.delivery.media import ImageDeliveryEnvelope, payload_hash
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import SourceRef, VersionRef
from tests.native_adapter_support import NativeFixture
from tests.test_management_access import NOW


RECORD = {"type": "record", "data": {"file": "base64://YQ=="}}


class VoiceSenderTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def original(self):
        await super().envelope()
        return DeliveryEnvelope("part-base", "operation-base", self.native_context, 1, "你好", digest("你好"),
                                self.native_sender.binding, lambda: True)

    def with_image(self, envelope):
        content = b"synthetic-image"
        scope = envelope.context.scope
        image = PromptImage(
            SourceRef("voice-image", OWNER, scope, VersionRef("voice-image", 1), NOW),
            SourceRef("voice-parent", "conversation.input", scope, VersionRef("voice-parent", 1), NOW),
            ImageDescriptor(0, "image/png", 1, 1, len(content), hashlib.sha256(content).hexdigest()),
            base64.b64encode(content).decode("ascii"),
        )
        return ImageDeliveryEnvelope(envelope.part_id, envelope.operation_id, envelope.context, envelope.sequence,
                                     envelope.text, payload_hash(envelope.text, (image,)), envelope.sender,
                                     envelope.before_dispatch, (image,))

    def seed(self, envelope, *, speech="你好", mode="voice_and_text", position="after_media", cooldown=0):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO conversation_batches
                (batch_id, origin_key, context_json, role_instruction, state, revision, created_at, contributions_json)
                VALUES (?, 'origin', '{}', '', 'ready', 1, ?, '{}')""", (envelope.part_id, NOW.isoformat()))
            connection.execute("""INSERT INTO conversation_parts
                (part_id, batch_id, sequence, text, payload_hash, state)
                VALUES (?, ?, 1, ?, ?, 'pending')""", (envelope.part_id, envelope.part_id, envelope.text,
                                                       envelope.payload_hash))
            connection.execute("""INSERT INTO conversation_voice_parts
                (part_id, speech, provider_id, delivery_mode, position, timeout_seconds, cooldown_seconds)
                VALUES (?, ?, 'selected-tts', ?, ?, 12, ?)""", (envelope.part_id, speech, mode, position, cooldown))
            connection.execute("""INSERT INTO conversation_voice_diagnostics
                (part_id, candidate_reason, render_state, component_order_json, text_hidden)
                VALUES (?, 'planned', 'pending', '[]', 0)""", (envelope.part_id,))

    def diagnostic(self, part_id):
        row = self.runtime.read_one("SELECT * FROM conversation_voice_diagnostics WHERE part_id = ?", (part_id,))
        return row["render_state"], json.loads(row["component_order_json"]), row["text_hidden"]

    async def test_voice_and_text_images_use_independent_native_requests(self):
        original = await self.original()
        self.native_sender.voice_delivery = VoiceReplyDelivery(self.runtime, self.clock.now)
        spoken = []

        async def voice(context, text, *, provider_id, timeout_seconds):
            spoken.append((context, text, provider_id, timeout_seconds))
            return RECORD

        self.native_sender.voice = voice
        expected = {"before_text": ["record", "text", "image"],
                    "after_text": ["text", "record", "image"],
                    "after_media": ["text", "image", "record"]}
        api = self.native.platform.client._api._wsr_api
        for position, types in expected.items():
            envelope = self.with_image(replace(original, part_id="part-" + position))
            self.seed(envelope, position=position)
            start = len(api.calls)
            self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
            messages = [params["message"] for _, params in api.calls[start:]]
            self.assertEqual([[segment["type"] for segment in message] for message in messages],
                             [["record"], ["text", "image"]] if position == "before_text"
                             else [["text", "image"], ["record"]])
            self.assertEqual(next(segment["data"]["text"] for message in messages
                                  for segment in message if segment["type"] == "text"), envelope.text)
            self.assertEqual(self.diagnostic(envelope.part_id),
                             ("ready", ["voice" if kind == "record" else kind for kind in types], 0))
        self.assertEqual([(speech, provider, timeout) for _, speech, provider, timeout in spoken],
                         [("你好", "selected-tts", 12)] * 3)

    async def test_voice_only_keeps_unspoken_text_and_failed_audio_releases_cooldown(self):
        original = await self.original()
        self.native_sender.voice_delivery = VoiceReplyDelivery(self.runtime, self.clock.now)

        async def voice(_, text, **__):
            return None if text == "失败" else RECORD

        self.native_sender.voice = voice
        for part_id, speech, expected in (("part-full", "你好", ["record"]),
                                          ("part-partial", "你", ["text", "record"]),
                                          ("part-failed", "失败", ["text"])):
            envelope = replace(original, part_id=part_id)
            self.seed(envelope, speech=speech, mode="voice_only", cooldown=90 if part_id == "part-failed" else 0)
            api = self.native.platform.client._api._wsr_api
            start = len(api.calls)
            self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
            messages = [params["message"] for _, params in api.calls[start:]]
            self.assertEqual([[part["type"] for part in message] for message in messages],
                             [["text"], ["record"]] if part_id == "part-partial" else [expected])
            self.assertEqual(self.diagnostic(part_id),
                             (("empty" if part_id == "part-failed" else "ready"),
                              ["voice" if kind == "record" else kind for kind in expected],
                              int(part_id == "part-full")))
        self.assertIsNone(self.runtime.read_one("SELECT * FROM conversation_voice_cooldowns", ()))

    async def test_malformed_audio_keeps_text_and_images_and_releases_cooldown(self):
        envelope = self.with_image(replace(await self.original(), part_id="part-malformed"))
        self.seed(envelope, speech="你好", mode="voice_only", cooldown=90)
        self.native_sender.voice_delivery = VoiceReplyDelivery(self.runtime, self.clock.now)

        async def voice(_, text, **__):
            return {"type": "record", "data": {"file": ""}}

        self.native_sender.voice = voice
        self.assertEqual((await self.native_sender.send(envelope)).status, "delivered")
        message = self.native.platform.client._api._wsr_api.calls[-1][1]["message"]
        self.assertEqual([segment["type"] for segment in message], ["text", "image"])
        self.assertEqual(self.diagnostic(envelope.part_id), ("invalid", ["text", "image"], 0))
        self.assertIsNone(self.runtime.read_one("SELECT * FROM conversation_voice_cooldowns", ()))

    async def test_cancelled_synthesis_releases_pre_send_cooldown(self):
        envelope = replace(await self.original(), part_id="part-cancel")
        self.seed(envelope, cooldown=90)
        self.native_sender.voice_delivery = VoiceReplyDelivery(self.runtime, self.clock.now)
        started = asyncio.Event()

        async def voice(*_, **__):
            started.set()
            await asyncio.Event().wait()

        self.native_sender.voice = voice
        task = asyncio.create_task(self.native_sender.send(envelope))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.runtime.read_one("SELECT * FROM conversation_voice_cooldowns", ()))
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
        self.assertEqual(self.diagnostic(envelope.part_id), ("pending", [], 0))

    async def test_unknown_voice_ack_keeps_cooldown_and_next_reply_sends_text(self):
        original = await self.original()
        self.native_sender.voice_delivery = VoiceReplyDelivery(self.runtime, self.clock.now)
        spoken = []

        async def voice(_, text, **__):
            spoken.append(text)
            return RECORD

        self.native_sender.voice = voice
        first = replace(original, part_id="part-unknown")
        second = replace(original, part_id="part-after-unknown")
        self.seed(first, cooldown=90)
        self.seed(second, cooldown=90)
        api = self.native.platform.client._api._wsr_api
        api.result = {}
        self.assertEqual((await self.native_sender.send(first)).status, "unknown")
        self.assertEqual(self.diagnostic(first.part_id), ("ready", ["text", "voice"], 0))
        self.assertIsNotNone(self.runtime.read_one("SELECT * FROM conversation_voice_cooldowns", ()))
        api.result = {"message_id": 456}
        self.assertEqual((await self.native_sender.send(second)).status, "delivered")
        self.assertEqual([segment["type"] for segment in api.calls[-1][1]["message"]], ["text"])
        self.assertEqual(self.diagnostic(second.part_id), ("cooldown", ["text"], 0))
        self.assertEqual(spoken, ["你好"])


class NativeVoiceProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-v2-native-voice-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audio = self.root / "audio.wav"
        self.audio.write_bytes(b"RIFFsynthetic-audio")

    async def test_selected_provider_and_explicit_tts_provider_use_original_session(self):
        task_context = NS(scope=NS(kind=NS(value="private"), platform="qq-main", session_id="200"))
        selected = []
        main_thread = threading.get_ident()

        def get_audio(text):
            selected.append((text, threading.get_ident()))
            return str(self.audio)

        provider = NS(get_audio=get_audio)
        context = NS(get_using_tts_provider=lambda umo: selected.append(umo) or provider,
                     get_provider_by_id=lambda identifier: provider if identifier == "tts-a" else None,
                     get_all_tts_providers=lambda: [provider])
        native = NativeVoiceReply(context)
        with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=str(self.root)):
            result = await native.render(task_context, " hello ")
            explicit = await native.render(task_context, "world", provider_id="tts-a")
            missing = await native.render(task_context, "no", provider_id="missing")
        self.assertEqual(result, explicit)
        self.assertEqual(base64.b64decode(result["data"]["file"].removeprefix("base64://")), self.audio.read_bytes())
        self.assertIsNone(missing)
        self.assertEqual(selected[0], "qq-main:FriendMessage:200")
        self.assertEqual([call[0] for call in selected[1:]], ["hello", "world"])
        self.assertTrue(all(call[1] != main_thread for call in selected[1:]))

    async def test_wrong_provider_type_timeout_and_outside_temp_fall_back(self):
        task_context = NS(scope=NS(kind=NS(value="private"), platform="qq-main", session_id="200"))
        provider = NS(get_audio=lambda _: str(self.audio))
        context = NS(get_using_tts_provider=lambda _: provider,
                     get_provider_by_id=lambda _: provider,
                     get_all_tts_providers=lambda: [])
        native = NativeVoiceReply(context)
        with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=str(self.root / "other")):
            self.assertIsNone(await native.render(task_context, "hello"))
        with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=str(self.root)):
            self.assertIsNone(await native.render(task_context, "hello", provider_id="not-a-tts-provider"))
            async def slow(_):
                await asyncio.sleep(0.1)
                return str(self.audio)
            context.get_using_tts_provider = lambda _: NS(get_audio=slow)
            self.assertIsNone(await native.render(task_context, "hello", timeout_seconds=0.001))

    async def test_mimo_voice_clone_tool_bridge_is_used_when_no_astrbot_tts_exists(self):
        task_context = NS(scope=NS(kind=NS(value="private"), platform="qq-main", session_id="200"))
        calls = []

        class MimoPlugin:
            def __init__(self, audio):
                self.audio = audio

            async def synthesize_text(self, text, **kwargs):
                calls.append((text, kwargs))
                return str(self.audio)

        plugin = MimoPlugin(self.audio)
        tool = NS(handler=plugin.synthesize_text)
        manager = NS(get_tool=lambda name: tool if name == "mimo_tts_speak" else None)
        context = NS(get_using_tts_provider=lambda _: None,
                     get_provider_by_id=lambda _: None,
                     get_all_tts_providers=lambda: [],
                     get_llm_tool_manager=lambda: manager)
        with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=str(self.root)), \
                patch("astrbot_host.voice.get_astrbot_data_path", return_value=str(self.root)):
            result = await NativeVoiceReply(context).render(task_context, "晚安")
        self.assertIsNotNone(result)
        self.assertEqual(base64.b64decode(result["data"]["file"].removeprefix("base64://")),
                         self.audio.read_bytes())
        self.assertEqual(calls[0][0], "晚安")
        self.assertEqual(calls[0][1]["group_id"], "qq-main:FriendMessage:200")

    async def test_streamed_wav_lengths_are_finalized_before_returning_the_record(self):
        pcm = b"\x00\x00" * 4410
        header = (b"RIFF" + struct.pack("<I", 0xFFFFFF24) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16,
                  1, 1, 44100, 88200, 2, 16) + b"data" + struct.pack("<I", 0xFFFFFF00))
        self.audio.write_bytes(header + pcm)
        provider = NS(get_audio=lambda _: str(self.audio))
        context = NS(get_using_tts_provider=lambda _: provider)
        task = NS(scope=NS(kind=NS(value="private"), platform="test", session_id="private"))
        with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=str(self.root)):
            result = await NativeVoiceReply(context).render(task, "晚安")
        audio = base64.b64decode(result["data"]["file"].removeprefix("base64://"))
        self.assertEqual(struct.unpack_from("<I", audio, 4)[0], len(audio) - 8)
        self.assertEqual(audio[44:], pcm)
        with wave.open(io.BytesIO(audio), "rb") as wav:
            self.assertEqual(wav.getnframes(), 4410)
            self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 0.1)
        self.assertEqual(self.audio.read_bytes(), header + pcm)
