# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _FakeTtsProvider:
    def __init__(self, provider_id: str) -> None:
        self.provider_config = {"id": provider_id, "type": "edge"}


class _Context:
    def __init__(self, providers: list[_FakeTtsProvider], current: _FakeTtsProvider) -> None:
        self.providers = providers
        self.current = current

    def get_all_tts_providers(self):
        return list(self.providers)

    def get_using_tts_provider(self, *_args, **_kwargs):
        return self.current


class _Harness(TtsEnhancementMixin):
    def __init__(self) -> None:
        self.tts_voice_language = "ja"
        self.tts_synthesis_backend = "astrbot_provider"
        self.tts_provider_id_ja = "tts-ja"
        self.tts_provider_id_en = "tts-en"
        self.tts_provider_id_zh = "tts-zh"
        self.ja = _FakeTtsProvider("tts-ja")
        self.en = _FakeTtsProvider("tts-en")
        self.zh = _FakeTtsProvider("tts-zh")
        self.default = _FakeTtsProvider("tts-default")
        self.context = _Context([self.ja, self.en, self.zh, self.default], self.default)


class TtsTurnLanguageOverrideTests(unittest.TestCase):
    def test_explicit_language_request_only_changes_current_event(self) -> None:
        harness = _Harness()
        english_event = SimpleNamespace(
            message_str="这次请用英语回复我",
            unified_msg_origin="default:FriendMessage:10001",
        )

        self.assertEqual("en", harness._ensure_turn_tts_voice_language(english_event))
        self.assertEqual("en", harness._tts_voice_language_for_event(english_event))
        self.assertIs(
            harness._resolve_tts_synthesis_provider(english_event, harness.default),
            harness.en,
        )
        self.assertEqual("ja", harness.tts_voice_language)

        next_event = SimpleNamespace(
            message_str="继续说吧",
            unified_msg_origin="default:FriendMessage:10001",
        )
        self.assertEqual("ja", harness._tts_voice_language_for_event(next_event))
        self.assertIs(
            harness._resolve_tts_synthesis_provider(next_event, harness.default),
            harness.ja,
        )

    def test_common_chinese_and_english_requests_are_recognized(self) -> None:
        harness = _Harness()
        cases = {
            "用日语说一句晚安": "ja",
            "英文语音回复": "en",
            "这回改用普通话回答": "zh",
            "Please reply in English": "en",
            "speak Japanese please": "ja",
            "日本語で話して": "ja",
            "英語で返事して": "en",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                event = SimpleNamespace(message_str=message)
                self.assertEqual(expected, harness._ensure_turn_tts_voice_language(event))

    def test_questions_negations_and_persistent_command_are_not_overridden(self) -> None:
        harness = _Harness()
        for message in (
            "英语怎么说",
            "不要用英语回复",
            "陪伴 TTS语种 英语",
        ):
            with self.subTest(message=message):
                event = SimpleNamespace(message_str=message)
                self.assertEqual("", harness._ensure_turn_tts_voice_language(event))
                self.assertEqual("ja", harness._tts_voice_language_for_event(event))

    def test_prompt_and_language_guard_use_turn_language(self) -> None:
        harness = _Harness()
        event = SimpleNamespace(message_str="这次用英语说", unified_msg_origin="test-session")
        harness._ensure_turn_tts_voice_language(event)

        prompt = harness._build_tts_rule_prompt("edge", event=event)

        self.assertIn("本轮明确要求使用英语", prompt)
        self.assertIn("当前语音正文目标语种：英语", prompt)
        self.assertTrue(
            harness._tts_text_needs_language_conversion(
                "今日は一緒に話そう。",
                provider_kind="edge",
                event=event,
            )
        )
        self.assertFalse(
            harness._tts_text_needs_language_conversion(
                "Let us talk for a while.",
                provider_kind="edge",
                event=event,
            )
        )


class TtsTurnLanguageRequestIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_language_request_bypasses_automatic_probability_and_injects_turn_rule(self) -> None:
        harness = _Harness()
        harness.enabled = True
        harness.enable_tts_enhancement = True
        harness.tts_generation_mode = "fast_tag"
        harness.tts_frequency_control_mode = "global"
        harness.tts_trigger_probability = 0.0
        harness.tts_constraint_mode = "weak"
        harness.tts_conversion_scope = "full"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness.context.get_config = lambda _umo: {}
        event = SimpleNamespace(
            message_str="这次用英语回复我",
            unified_msg_origin="default:FriendMessage:10001",
        )
        request = SimpleNamespace(system_prompt="base")

        await harness.apply_tts_enhancement_request(event, request)

        self.assertEqual("en", harness._tts_voice_language_for_event(event))
        self.assertTrue(getattr(event, "_private_companion_tts_request_applied", False))
        self.assertIn("本轮明确要求使用英语", request.system_prompt)
        self.assertIn("当前语音正文目标语种：英语", request.system_prompt)
        self.assertIn("【用户语音请求】", request.system_prompt)
        self.assertEqual("ja", harness.tts_voice_language)


if __name__ == "__main__":
    unittest.main()
