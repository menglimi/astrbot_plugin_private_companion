# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api.message_components import Plain, Record

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _TtsHarness(TtsEnhancementMixin):
    enabled = True
    enable_tts_enhancement = True
    tts_generation_mode = "postprocess"

    @staticmethod
    def _feature_enabled_or_temp_unlocked(key):
        return key == "enable_tts_enhancement"


class _VoiceComponentHarness(ProactiveMessageMixin):
    def __init__(self):
        self.context = SimpleNamespace(
            get_config=lambda _target: {"provider_tts_settings": {"enable": True}},
            get_using_tts_provider=lambda _target: object(),
        )
        self._build_tts_modify_components = AsyncMock()


class _MediaSendHarness(ProactiveMessageMixin):
    def __init__(self):
        self.sent: list[list[object]] = []

    async def _maybe_send_input_status(self, _umo, _text):
        return None

    @staticmethod
    def _split_proactive_text(text, **_kwargs):
        return [text]

    @staticmethod
    def _segmented_scope_allows_umo(_umo):
        return True

    @staticmethod
    def _should_cancel_reply_for_recalled_message_ids(_message_id):
        return ""

    @staticmethod
    def _quote_skip_reason_for_short_reply(_text):
        return ""

    @staticmethod
    def _with_optional_reply(chain, _message_id):
        return chain

    async def _send_chain_components(self, _umo, chain):
        self.sent.append(chain)
        return True


class ProactiveVoiceTtsDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_prebuilt_voice_suppresses_tts_for_companion_text(self):
        harness = _MediaSendHarness()
        record = Record(file="voice.wav")

        outcome = await harness._send_media_proactive_chain(
            "default:FriendMessage:10001",
            "这是主动语音对应的可见正文。",
            extra_components=[record],
        )

        self.assertTrue(outcome)
        self.assertTrue(outcome.complete)
        self.assertEqual(2, len(harness.sent))
        text_component = harness.sent[0][0]
        self.assertIsInstance(text_component, Plain)
        self.assertTrue(text_component._private_companion_skip_tts_enhancement)
        self.assertIs(record, harness.sent[1][0])

    async def test_tts_hook_does_not_convert_marked_proactive_text(self):
        harness = _TtsHarness()
        harness._maybe_convert_plain_reply_to_tts = AsyncMock(return_value=[Record(file="duplicate.wav")])
        plain = Plain("已有主动语音的可见正文")
        object.__setattr__(plain, "_private_companion_skip_tts_enhancement", True)
        result = SimpleNamespace(chain=[plain])

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"

            @staticmethod
            def get_result():
                return result

        await harness.apply_tts_enhancement_before_send(Event())

        harness._maybe_convert_plain_reply_to_tts.assert_not_awaited()
        self.assertIs(plain, result.chain[0])

    async def test_proactive_voice_action_keeps_only_record_component(self):
        harness = _VoiceComponentHarness()
        record = Record(file="voice.wav")
        harness._build_tts_modify_components.return_value = (
            [record, Plain("语音对应的重复可见文本")],
            "voice.wav",
        )

        components, note = await harness._create_voice_record_component(
            "default:FriendMessage:10001",
            "<tts>声に出す内容。</tts>对应文本",
        )

        self.assertEqual([record], components)
        self.assertEqual("voice.wav", note)

    async def test_plain_proactive_voice_uses_unified_tts_record_builder(self):
        harness = _VoiceComponentHarness()
        record = Record(file="voice.wav")
        harness._tts_record_component = AsyncMock(return_value=record)

        components, note = await harness._create_voice_record_component(
            "default:FriendMessage:10001",
            "主动说一句话。",
        )

        self.assertEqual([record], components)
        self.assertEqual("voice.wav", note)
        call = harness._tts_record_component.await_args
        self.assertEqual("主动说一句话。", call.args[0])
        self.assertEqual("private_companion", call.kwargs["source"])

    async def test_full_segmented_proactive_message_generates_only_one_voice(self):
        harness = _TtsHarness()
        harness.tts_conversion_scope = "full"
        harness.tts_frequency_control_mode = "legacy"
        harness.tts_voice_language = "zh"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness._tts_auto_voice_last_at = {}
        harness._convert_text_to_tts_markup = AsyncMock(
            side_effect=lambda text, _event, **_kwargs: f"<tts>{text}</tts>"
        )
        harness._process_tts_tags = AsyncMock(return_value=[Record(file="voice.wav")])
        full_text = "第一段主动正文。第二段主动正文。"
        builder = _MediaSendHarness()
        first = builder._proactive_plain_segment_component(
            "第一段主动正文。",
            full_text=full_text,
            index=0,
            count=2,
        )
        second = builder._proactive_plain_segment_component(
            "第二段主动正文。",
            full_text=full_text,
            index=1,
            count=2,
        )

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = ""

            def __init__(self, component):
                self.result = SimpleNamespace(chain=[component])

            def get_result(self):
                return self.result

        first_result = await harness._maybe_convert_plain_reply_to_tts(first.text, Event(first))
        second_result = await harness._maybe_convert_plain_reply_to_tts(second.text, Event(second))

        self.assertEqual(1, len(first_result))
        self.assertEqual([], second_result)
        self.assertEqual(1, harness._convert_text_to_tts_markup.await_count)
        self.assertEqual(full_text, harness._convert_text_to_tts_markup.await_args.args[0])

    async def test_partial_segmented_proactive_message_only_converts_first_segment(self):
        harness = _TtsHarness()
        harness.tts_conversion_scope = "partial"
        harness.tts_frequency_control_mode = "legacy"
        harness.tts_voice_language = "zh"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness._tts_auto_voice_last_at = {}
        harness._convert_text_to_tts_markup = AsyncMock(
            side_effect=lambda text, _event, **_kwargs: f"<tts>{text}</tts>"
        )
        harness._process_tts_tags = AsyncMock(return_value=[Record(file="voice.wav")])
        full_text = "第一段主动正文。第二段主动正文。"

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = ""

            def __init__(self, text, index):
                self.result = SimpleNamespace(chain=[Plain(text)])
                self._private_companion_proactive_full_text = full_text
                self._private_companion_proactive_segment_index = index
                self._private_companion_proactive_segment_count = 2

            def get_result(self):
                return self.result

        first_text = "第一段主动正文。"
        second_text = "第二段主动正文。"
        first_result = await harness._maybe_convert_plain_reply_to_tts(first_text, Event(first_text, 0))
        second_result = await harness._maybe_convert_plain_reply_to_tts(second_text, Event(second_text, 1))

        self.assertEqual(1, len(first_result))
        self.assertEqual([], second_result)
        self.assertEqual(1, harness._convert_text_to_tts_markup.await_count)
        self.assertEqual(first_text, harness._convert_text_to_tts_markup.await_args.args[0])

    async def test_later_segment_stays_as_plain_text_in_send_hook(self):
        harness = _TtsHarness()
        harness.tts_conversion_scope = "partial"
        harness._build_result_from_chain = lambda chain: SimpleNamespace(chain=list(chain))
        original = Plain("后续分段必须保留为文字。")

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            message_str = ""
            _private_companion_proactive_full_text = "首段可以转语音。后续分段必须保留为文字。"
            _private_companion_proactive_segment_index = 1
            _private_companion_proactive_segment_count = 2

            def __init__(self):
                self.result = SimpleNamespace(chain=[original])

            def get_result(self):
                return self.result

            def set_result(self, result):
                self.result = result

        event = Event()
        await harness.apply_tts_enhancement_before_send(event)

        self.assertEqual(1, len(event.result.chain))
        self.assertIsInstance(event.result.chain[0], Plain)
        self.assertEqual("后续分段必须保留为文字。", event.result.chain[0].text)


if __name__ == "__main__":
    unittest.main()
