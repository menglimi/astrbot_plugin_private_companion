"""Regression tests for voice tag deduplication, semantic voice_only coverage, WAV->MP3 compression and chunk fault isolation."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import io
import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
import wave

from tests import astrbot_data_root  # noqa: F401 - isolate AstrBot SDK paths
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
from astrbot_host.core_reply_voice import (
    _synthesize_chunks,
    observe_core_voice_response,
    prepare_core_reply_voice_message,
)
from astrbot_host.core_reply_voice_authoring import _should_repair
from astrbot_host.fish_audio import DRAMA_DIRECTION, provider_direction
from astrbot_host.voice import (
    NativeVoiceReply,
    VoiceAudioTooLargeError,
    _prepare_audio_bytes,
    _read_audio,
)
from companion_v2.conversation.reply_settings import (
    configuration_fields as reply_configuration_fields,
    validate as validate_reply_settings,
    voice_settings,
)
from companion_v2.conversation.segmentation import LLM_SEGMENT_MARKER, SegmentationPolicy
from companion_v2.conversation.text_processing import ReplyTextPolicy
from companion_v2.conversation.voice_markup import visible_speech
from companion_v2.conversation.voice_reply import (
    VoiceReplyPolicy,
    estimate_speech_seconds,
    separate_spoken,
    speech_candidates,
    speech_chunks,
    speech_covers_visible,
)
from companion_v2.delivery.components import build_component_plan
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from tests.test_core_reply_voice import FakeEvent


RECORD = {"type": "record", "data": {"file": "base64://YQ=="}}


async def _allow(*_):
    return Outcome.success(True)


def _synth_wav_bytes(duration_seconds: float, *, sample_rate: int = 44100, freq_hz: float = 440.0) -> bytes:
    """Synthesize a valid mono 16-bit PCM WAV in memory."""
    frames = max(1, int(sample_rate * duration_seconds))
    # Repeat one short waveform cycle so generating long test audio stays fast.
    cycle_len = max(1, int(sample_rate / freq_hz))
    cycle = struct.pack(
        f"<{cycle_len}h",
        *(int(12000 * math.sin(2.0 * math.pi * i / cycle_len)) for i in range(cycle_len)),
    )
    repeats, rem = divmod(frames, cycle_len)
    pcm = (cycle * repeats) + cycle[: rem * 2]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


class VoiceTagDedupTests(unittest.TestCase):
    """Bidirectional deduplication and trailing <pc_tts> appendix rebinding."""

    zh = VoiceReplyPolicy(
        trigger_mode="always",
        generation_mode="model_tag",
        conversion_scope="full",
        language="zh",
    )

    def assert_anchored(self, visible, spans):
        for offset, speech in spans:
            shown = visible_speech(speech)
            self.assertEqual(
                visible[offset: offset + len(shown)],
                shown,
                f"span {speech!r} not anchored at {offset} in {visible!r}",
            )

    def test_trailing_pc_tts_appendix_rebinds_segmented_plain_text(self):
        source = (
            "想得美，大白天的做梦呢\n"
            f"{LLM_SEGMENT_MARKER}\n"
            "这理解力，系统终端都带不动你\n"
            "<pc_tts>想得美，大白天的做梦呢！</pc_tts>\n"
            f"{LLM_SEGMENT_MARKER}\n"
            "<pc_tts>这理解力，系统终端都带不动你</pc_tts>"
        )
        visible, spans = separate_spoken(source, self.zh)
        self.assert_anchored(visible, spans)
        self.assertEqual(visible.count("想得美"), 1)
        self.assertEqual(visible.count("系统终端都带不动你"), 1)
        self.assertNotIn("带不动你想得美", visible.replace("\n", ""))
        parts = SegmentationPolicy(enabled=True, llm_controlled=True).parts(visible)
        self.assertEqual(len(parts), 2)
        self.assertEqual(
            speech_candidates(visible, parts, spans, self.zh),
            ("想得美，大白天的做梦呢！", "这理解力，系统终端都带不动你"),
        )

    def test_trailing_pc_tts_appendix_with_newline_only_segments(self):
        source = (
            "第一句先这样\n"
            "第二句也一样\n"
            "<pc_tts>第一句先这样！</pc_tts>\n"
            "<pc_tts>第二句也一样。</pc_tts>"
        )
        visible, spans = separate_spoken(source, self.zh)
        self.assert_anchored(visible, spans)
        self.assertEqual(visible, "第一句先这样！\n第二句也一样。")
        self.assertEqual(len(spans), 2)

    def test_preceding_plain_text_before_tag_is_deduplicated_without_dropping_prefix(self):
        source = "先喝口水。想得美，大白天的做梦呢\n<pc_tts>[轻笑]想得美，大白天的做梦呢！</pc_tts>\n走吧。"
        visible, spans = separate_spoken(source, self.zh)
        self.assert_anchored(visible, spans)
        self.assertEqual(visible, "先喝口水。想得美，大白天的做梦呢！\n走吧。")
        self.assertEqual(spans, ((5, "[轻笑]想得美，大白天的做梦呢！"),))

    def test_forward_restatement_before_segment_marker_and_punctuation_tail(self):
        source = f"<pc_tts>想得美，大白天的做梦呢！</pc_tts>想得美，大白天的做梦呢\n{LLM_SEGMENT_MARKER}\n下一句。"
        visible, spans = separate_spoken(source, self.zh)
        self.assert_anchored(visible, spans)
        self.assertEqual(visible.count("想得美"), 1)
        self.assertIn(LLM_SEGMENT_MARKER, visible)
        self.assertIn("下一句。", visible)

        visible_tilde, spans_tilde = separate_spoken("<pc_tts>晚安</pc_tts>晚安~", self.zh)
        self.assert_anchored(visible_tilde, spans_tilde)
        self.assertEqual(visible_tilde, "晚安")

    def test_distinct_preceding_text_is_never_dropped(self):
        source = "开场白。\n<pc_tts>你好呀。</pc_tts>"
        visible, spans = separate_spoken(source, self.zh)
        self.assert_anchored(visible, spans)
        self.assertEqual(visible, "开场白。\n你好呀。")
        self.assertEqual(spans, ((5, "你好呀。"),))


class VoiceSemanticCoverageTests(unittest.IsolatedAsyncioTestCase):
    """Semantic voice_only coverage, uses_model_tags unity, and pure-prose fallback."""

    def test_speech_covers_visible_handles_cues_punctuation_cleanup_and_safety_redlines(self):
        self.assertTrue(speech_covers_visible("晚安，早点睡", "[轻声]晚安！早点睡~"))
        self.assertTrue(speech_covers_visible("晚安", "(whispering) 晚安。"))
        self.assertTrue(speech_covers_visible("晚安", "［轻声］晚安！"))
        presentation = ReplyTextPolicy(
            content_cleanup_enabled=True,
            content_cleanup_words='["。"]',
            content_cleanup_scope="trailing",
            content_replacement_enabled=True,
            content_replacements='[{"from":"嗯","to":"好的"}]',
        )
        self.assertTrue(speech_covers_visible("好的，早点睡", "嗯，早点睡。", presentation=presentation))

        # Safety redlines: never hide code, URLs, Markdown tables, foreign translations, or extra prose.
        self.assertFalse(speech_covers_visible("看这个 https://example.com/a", "看这个 https://example.com/a"))
        self.assertFalse(speech_covers_visible("运行 `pytest -q`", "运行 pytest -q"))
        self.assertFalse(speech_covers_visible("```python\nprint(1)\n```", "print(1)"))
        self.assertFalse(speech_covers_visible("| A | B |\n| --- | --- |\n| 1 | 2 |", "A B 1 2"))
        self.assertFalse(speech_covers_visible("你好。", "Hello there."))
        self.assertFalse(speech_covers_visible("晚安。顺便提醒你明天早会。", "晚安。"))

    def test_delivery_component_plan_uses_semantic_coverage(self):
        covered = build_component_plan("晚安。", (), RECORD, voice_only=True, speech="[轻声]晚安！")
        self.assertTrue(covered.text_hidden)
        self.assertEqual(covered.kinds, ("voice",))

        with_url = build_component_plan("看链接 https://example.com", (), RECORD,
                                        voice_only=True, speech="看链接 https://example.com")
        self.assertFalse(with_url.text_hidden)
        self.assertEqual(with_url.kinds, ("text", "voice"))

    async def test_voice_only_hides_text_across_content_cleanup_and_logs_reason(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(
            trigger_mode="always",
            generation_mode="model_tag",
            conversion_scope="full",
            delivery_mode="voice_only",
        )
        prepared = {
            "context": object(),
            "event_id": "cov-1",
            "event_ref": "cov-ref",
            "voice_reply_policy": policy,
            "presentation_settings": {
                "content_cleanup_enabled": True,
                "content_cleanup_words": '["。"]',
                "content_cleanup_scope": "trailing",
            },
        }
        prepare_core_reply_event(event, prepared, authorize=_allow)
        response = LLMResponse(role="assistant", completion_text="<pc_tts>[轻声]晚安。</pc_tts>")
        observe_core_voice_response(event, response)

        async def renderer(*_, **__):
            return RECORD

        # Simulate core presentation stripping trailing "。" from visible text before voice send.
        cleaned_msg = MessageChain(chain=[Plain("晚安")])
        object.__setattr__(cleaned_msg, "_companion_v2_voice_source", "晚安。")
        with self.assertLogs("astrbot", level="INFO") as captured:
            result = await prepare_core_reply_voice_message(
                event, prepared, cleaned_msg, renderer=renderer, delivery=None, authorize=_allow,
            )
        self.assertEqual([type(part) for part in result.chain], [Record])
        diag = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_reply_diagnostics"][-1]
        self.assertTrue(diag["hide_text"])
        self.assertEqual(diag["hide_reason"], "covered")
        self.assertTrue(any("hide_text=True" in line and "hide_reason=covered" in line for line in captured.output))

    async def test_voice_only_full_falls_back_to_plain_prose_when_tags_omitted(self):
        policy = VoiceReplyPolicy(
            trigger_mode="always",
            generation_mode="model_tag",
            conversion_scope="full",
            delivery_mode="voice_only",
            language="zh",
        )

        async def renderer(_ctx, text, **__):
            spoken.append(text)
            return RECORD

        # Pure prose without <pc_tts>: should still synthesize and hide text.
        spoken = []
        event = FakeEvent()
        prepared = {"context": object(), "event_id": "fb-1", "event_ref": "fb-1",
                    "voice_reply_policy": policy, "native_input": NS(text="跟我说晚安")}
        prepare_core_reply_event(event, prepared, authorize=_allow)
        result = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain("晚安，早点睡吧。")]),
            renderer=renderer, delivery=None, authorize=_allow,
        )
        self.assertEqual(spoken, ["晚安，早点睡吧。"])
        self.assertEqual([type(part) for part in result.chain], [Record])

        # User requested text-only: must NOT fall back to voice.
        spoken = []
        event2 = FakeEvent()
        prepared2 = {"context": object(), "event_id": "fb-2", "event_ref": "fb-2",
                     "voice_reply_policy": policy, "native_input": NS(text="这次只发文字，不要语音")}
        prepare_core_reply_event(event2, prepared2, authorize=_allow)
        result2 = await prepare_core_reply_voice_message(
            event2, prepared2, MessageChain(chain=[Plain("好的，只发文字。")]),
            renderer=renderer, delivery=None, authorize=_allow,
        )
        self.assertEqual(spoken, [])
        self.assertEqual([type(part) for part in result2.chain], [Plain])

        # Message contains URL: must NOT fall back to voice_only hiding text.
        spoken = []
        event3 = FakeEvent()
        prepared3 = {"context": object(), "event_id": "fb-3", "event_ref": "fb-3",
                     "voice_reply_policy": policy, "native_input": NS(text="发链接给我")}
        prepare_core_reply_event(event3, prepared3, authorize=_allow)
        result3 = await prepare_core_reply_voice_message(
            event3, prepared3, MessageChain(chain=[Plain("在这里 https://example.com")]),
            renderer=renderer, delivery=None, authorize=_allow,
        )
        self.assertEqual(spoken, [])
        self.assertEqual([type(part) for part in result3.chain], [Plain])

    async def test_uses_model_tags_unified_for_plain_partial(self):
        policy = VoiceReplyPolicy(
            trigger_mode="always",
            generation_mode="plain",
            conversion_scope="partial",
        )
        self.assertTrue(policy.uses_model_tags)
        response = LLMResponse(role="assistant", completion_text="这是一段普通回复。")
        self.assertTrue(_should_repair({"prepared": {"voice_reply_policy": policy}}, response))

        provider = NS(
            provider_config={"type": "fishaudio_tts_api", "model": "drama-3"},
            get_model=lambda: "drama-3",
        )
        context = NS(get_using_tts_provider_async=AsyncMock(return_value=provider))
        self.assertEqual(await provider_direction(context, policy, "qq:FriendMessage:1"), DRAMA_DIRECTION)


class VoiceCompressionAndFaultIsolationTests(unittest.IsolatedAsyncioTestCase):
    """WAV->MP3 auto compression, 300s timeout, duration chunking, and chunk fault isolation."""

    def test_timeout_upper_bound_raised_to_300(self):
        policy = VoiceReplyPolicy(timeout_seconds=300)
        self.assertEqual(policy.timeout_seconds, 300)
        with self.assertRaises(ValueError):
            VoiceReplyPolicy(timeout_seconds=301)

        from dataclasses import asdict
        fields = {
            field.field_id: field
            for field in reply_configuration_fields(SegmentationPolicy(), False, 0)
        }
        self.assertEqual(fields["conversation.presentation.voice_timeout_seconds"].maximum, 300)
        base = {**asdict(SegmentationPolicy()), "streaming": False, "part_gap_ms": 0, **voice_settings()}
        validate_reply_settings({**base, "voice_timeout_seconds": 300})
        with self.assertRaises(Exception):
            validate_reply_settings({**base, "voice_timeout_seconds": 301})

    async def test_large_real_wav_compresses_to_mp3_via_pyav_and_passes_render(self):
        # ~95s mono 44.1kHz 16-bit WAV = ~8.38 MB (> old 8 MB limit, > 2 MB compression threshold)
        raw_wav = _synth_wav_bytes(95.0)
        self.assertGreater(len(raw_wav), 8_000_000)

        with TemporaryDirectory(prefix="companion-v2-mp3-") as tmp:
            wav_path = Path(tmp) / "long_asmr.wav"
            wav_path.write_bytes(raw_wav)
            provider = NS(get_audio=lambda _: str(wav_path))
            context = NS(get_using_tts_provider=lambda _: provider)
            task = NS(scope=NS(kind=ScopeKind.PRIVATE, platform="test", session_id="200"))

            with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=tmp):
                result = await NativeVoiceReply(context).render(task, "长语音测试")

        self.assertIsNotNone(result)
        encoded = base64.b64decode(result["data"]["file"].removeprefix("base64://"))
        self.assertTrue(
            encoded[:3] == b"ID3" or (encoded[0] == 0xFF and (encoded[1] & 0xE0) == 0xE0),
            f"expected MP3 header, got {encoded[:4]!r}",
        )
        self.assertLess(len(encoded), 2_500_000)

    def test_adaptive_bitrate_and_oversize_raises_named_error(self):
        # ~12s WAV (~1.06 MB): test adaptive bitrate stepping down when max_bytes forces 96k/64k
        wav_data = _synth_wav_bytes(12.0)
        mp3_128 = _prepare_audio_bytes(wav_data, max_bytes=1_000_000, compress_threshold=100_000)
        self.assertTrue(mp3_128[:3] == b"ID3" or mp3_128[0] == 0xFF)
        # Force step-down below 128kbps output size (~192 KB) so 96k or 64k (~96 KB) succeeds.
        mp3_low = _prepare_audio_bytes(wav_data, max_bytes=120_000, compress_threshold=100_000)
        self.assertLessEqual(len(mp3_low), 120_000)
        self.assertLess(len(mp3_low), len(mp3_128))

        # Tight limit smaller than even 64kbps output must raise VoiceAudioTooLargeError, never return None.
        with self.assertLogs("astrbot_host.voice", level="WARNING"), \
                self.assertRaises(VoiceAudioTooLargeError) as ctx:
            _prepare_audio_bytes(wav_data, max_bytes=10_000, compress_threshold=100_000)
        self.assertEqual(ctx.exception.audio_format, "mp3")
        self.assertGreater(ctx.exception.actual_bytes, 10_000)

    async def test_oversize_error_in_render_never_falls_back_to_mimo(self):
        with TemporaryDirectory(prefix="companion-v2-too-big-") as tmp:
            big_mp3 = Path(tmp) / "huge.mp3"
            big_mp3.write_bytes(b"ID3" + (b"\x00" * 5000))
            provider = NS(get_audio=lambda _: str(big_mp3))
            context = NS(get_using_tts_provider=lambda _: provider)
            task = NS(scope=NS(kind=ScopeKind.PRIVATE, platform="test", session_id="200"))

            with patch("astrbot_host.voice.get_astrbot_temp_path", return_value=tmp), \
                    patch("astrbot_host.voice.MAX_VOICE_BYTES", 1024), \
                    patch("astrbot_host.voice._find_mimo_voice_clone") as mimo_fallback, \
                    self.assertLogs("astrbot_host.voice", level="WARNING"):
                with self.assertRaises(VoiceAudioTooLargeError):
                    await NativeVoiceReply(context).render(task, "超限音频")
            mimo_fallback.assert_not_called()

    def test_duration_aware_chunking_splits_long_whisper_asmr_even_with_large_max_chars(self):
        asmr = "……[ひそひそ声]<whisper>" + ("ゆっくり休んでね、大丈夫だよ……" * 18) + "</whisper>"
        self.assertGreater(estimate_speech_seconds(asmr), 75.0)
        chunks = speech_chunks(asmr, 10000, max_estimated_seconds=75.0)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(
            "".join(visible_speech(c) for c in chunks),
            visible_speech(asmr),
        )
        for chunk in chunks:
            self.assertEqual(chunk.count("<whisper>"), chunk.count("</whisper>"))

    async def test_multi_chunk_fault_isolation_delivers_succeeded_chunks_and_keeps_text(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(
            trigger_mode="always",
            generation_mode="plain",
            delivery_mode="voice_only",
            max_speech_chars=8,
        )
        prepared = {"context": object(), "event_id": "iso-1", "event_ref": "iso-1",
                    "voice_reply_policy": policy}
        prepare_core_reply_event(event, prepared, authorize=_allow)
        calls = []

        async def renderer(_ctx, text, **__):
            calls.append(text)
            if "第一段" in text:
                raise VoiceAudioTooLargeError(11_000_000, 10_000_000, audio_format="mp3")
            await asyncio.sleep(0.01)
            return RECORD

        result = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain("第一段说话。第二段说话。")]),
            renderer=renderer, delivery=None, authorize=_allow,
        )
        records = [part for part in result.chain if isinstance(part, Record)]
        plains = [part for part in result.chain if isinstance(part, Plain)]
        self.assertEqual(len(records), 1)
        self.assertIn("第二段说话", records[0].text)
        # Because chunk 1 failed, visible text MUST be preserved so user loses nothing.
        self.assertEqual(len(plains), 1)
        diag = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_reply_diagnostics"][-1]
        self.assertFalse(diag["hide_text"])
        self.assertEqual(diag["hide_reason"], "partial_chunk_failure")
        self.assertEqual(diag["failed_chunks"], 1)

    async def test_oversize_chunk_bisects_and_retries_once(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(max_speech_chars=1000)
        long_chunk = ("今天晚上风很轻，我们慢慢聊一会儿吧。" * 6)
        seen = []

        async def renderer(_ctx, text, **__):
            seen.append(text)
            if len(visible_speech(text)) > 80:
                raise VoiceAudioTooLargeError(11_000_000, 10_000_000, audio_format="mp3")
            return RECORD

        report = {}
        records, _ = await _synthesize_chunks(
            event, object(), (long_chunk,), policy,
            kind="private", singing=False, renderer=renderer,
            segment_index=0, segment_count=1, whole=False, report=report,
        )
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(report["failed"], 0)
        self.assertEqual("".join(r.text for r in records), long_chunk)


if __name__ == "__main__":
    unittest.main()
