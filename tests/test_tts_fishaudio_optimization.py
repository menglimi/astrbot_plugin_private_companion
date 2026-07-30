# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _FishProvider:
    name = "FishAudio"
    provider_type = "fishaudio_tts_api"

    def __init__(self, *, api_base: str = "https://api.fish.audio/v1", model: str = "") -> None:
        self.api_base = api_base
        self.model_name = model
        self.headers = {"Authorization": "Bearer test"}

    def get_model(self) -> str:
        return self.model_name

    def set_model(self, model: str) -> None:
        self.model_name = model


class _RecordingFishProvider(_FishProvider):
    def __init__(self, audio_path: str) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.received_text = ""

    async def get_audio(self, text: str) -> str:
        self.received_text = text
        return self.audio_path


class _TtsHarness(TtsEnhancementMixin):
    tts_fishaudio_model = "auto"
    tts_fishaudio_emotion_mode = "balanced"
    tts_voice_language = "ja"
    tts_generation_mode = "fast_tag"
    tts_frequency_control_mode = "global"
    tts_delivery_mode = "voice_and_text"
    tts_foreign_text_mode = "translation"
    tts_conversion_scope = "partial"
    tts_extra_prompt = ""

    async def _after_tts_audio_generated(self, *args, **kwargs) -> None:
        return None


class _ConversionHarness(_TtsHarness):
    conversion_prompt = ""
    conversion_text = "うーん、もうやめてよ。"

    async def _get_tts_conversion_provider(self, event):
        return object()

    async def _format_tts_persona_voice_context(self, event) -> str:
        return ""

    async def _tts_provider_text_chat(self, provider, prompt: str, **kwargs):
        self.conversion_prompt = prompt
        return type("Response", (), {"completion_text": self.conversion_text})()


class _TextProvider:
    def __init__(self, provider_id: str, completion: str) -> None:
        self.provider_id = provider_id
        self.completion = completion
        self.calls = 0

    async def text_chat(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(completion_text=self.completion)


class _ConversionFallbackHarness(_TtsHarness):
    def __init__(self, primary: _TextProvider, backup: _TextProvider) -> None:
        self.providers = {primary.provider_id: primary, backup.provider_id: backup}
        self.context = SimpleNamespace(
            get_provider_by_id=lambda provider_id: self.providers.get(provider_id)
        )
        self.usage: list[dict] = []

    @staticmethod
    def _provider_id_from_instance(provider) -> str:
        return provider.provider_id

    @staticmethod
    def _model_fallback_provider_id(_key: str, primary_id: str = "") -> str:
        return "backup" if primary_id != "backup" else ""

    def _record_llm_usage(self, **kwargs) -> None:
        self.usage.append(dict(kwargs))


class _RealtimeHarness(_TtsHarness):
    def __init__(self, converted_text: str) -> None:
        self.converted_text = converted_text
        self.conversion_calls = 0
        self.context = type(
            "Context",
            (),
            {"get_using_provider": lambda _self: object()},
        )()

    async def _format_tts_persona_voice_context(self, event) -> str:
        return ""

    async def _tts_provider_text_chat(self, provider, prompt: str, **kwargs):
        self.conversion_calls += 1
        return type("Response", (), {"completion_text": self.converted_text})()


class FishAudioTtsOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def test_official_fishaudio_auto_uses_s2_and_default_model_header(self) -> None:
        harness = _TtsHarness()
        provider = _FishProvider()

        self.assertEqual("fishaudio_s2", harness._tts_provider_kind(provider, {}))
        self.assertEqual("s2.1-pro-free", harness._prepare_fishaudio_provider_model(provider, {}))
        self.assertEqual("s2.1-pro-free", provider.headers["model"])
        self.assertEqual("s2.1-pro-free", provider.get_model())

    def test_custom_fishaudio_auto_does_not_force_model_header(self) -> None:
        harness = _TtsHarness()
        provider = _FishProvider(api_base="http://127.0.0.1:8080/v1")

        self.assertEqual("fishaudio_s2", harness._tts_provider_kind(provider, {}))
        self.assertEqual("", harness._prepare_fishaudio_provider_model(provider, {}))
        self.assertNotIn("model", provider.headers)

    def test_explicit_s1_selects_legacy_syntax_and_header(self) -> None:
        harness = _TtsHarness()
        harness.tts_fishaudio_model = "s1"
        provider = _FishProvider()

        self.assertEqual("fishaudio_s1", harness._tts_provider_kind(provider, {}))
        harness._prepare_fishaudio_provider_model(provider, {})
        self.assertEqual("s1", provider.headers["model"])

    def test_s2_prompt_uses_official_placement_and_combination_guidance(self) -> None:
        harness = _TtsHarness()

        prompt = harness._build_tts_rule_prompt("fishaudio_s2")

        self.assertIn("Fish Audio S2", prompt)
        self.assertIn("[happy]", prompt)
        self.assertIn("[くすくす笑い]", prompt)
        self.assertIn("[強調]", prompt)
        self.assertIn("[興奮]", prompt)
        self.assertIn("同一位置只放一个标签", prompt)
        self.assertIn("不要自动使用喘息", prompt)

    def test_s2_sanitizer_keeps_one_cjk_cue_per_consecutive_run(self) -> None:
        harness = _TtsHarness()
        source = (
            "[嬉しい][照れ][眠い][考え込む]今日は一緒にいよう。"
            "[懐かしい][拗ねる]また思い出したね。[1]"
        )

        sanitized = harness._sanitize_tts_spoken_text(source, provider_kind="fishaudio_s2")

        self.assertEqual("[嬉しい]今日は一緒にいよう。[懐かしい]また思い出したね。", sanitized)

    def test_s2_keeps_official_natural_language_cues_at_phrase_positions(self) -> None:
        harness = _TtsHarness()
        source = (
            "あれ？[くすくす笑い]知らなかった？私が往生堂七十七代目堂主、"
            "[強調]胡桃だよ！[興奮]これからも頑張るね。"
        )

        self.assertEqual(
            source,
            harness._sanitize_tts_spoken_text(source, provider_kind="fishaudio_s2"),
        )

    def test_s2_keeps_concise_natural_language_cues_and_drops_long_metadata(self) -> None:
        harness = _TtsHarness()
        too_long = "x" * 41

        sanitized = harness._sanitize_tts_spoken_text(
            f"[laughing nervously]本当なの？[{too_long}]続けよう。",
            provider_kind="fishaudio_s2",
        )

        self.assertEqual("[laughing nervously]本当なの？続けよう。", sanitized)

    def test_s1_converts_aliases_to_fixed_parenthesis_cues(self) -> None:
        harness = _TtsHarness()

        sanitized = harness._sanitize_tts_spoken_text(
            "[嬉しい](whispering)[眠い][unknown](舞台动作)そばにいるよ。",
            provider_kind="fishaudio_s1",
        )

        self.assertEqual("(joyful)(whispering)(soft tone)そばにいるよ。", sanitized)

    def test_non_fish_provider_behavior_is_unchanged(self) -> None:
        harness = _TtsHarness()

        self.assertEqual(
            "[嬉しい]こんにちは。",
            harness._sanitize_tts_spoken_text("[嬉しい]こんにちは。", provider_kind="gsv"),
        )
        self.assertEqual(
            "こんにちは。",
            harness._sanitize_tts_spoken_text("[嬉しい]こんにちは。", provider_kind="generic"),
        )

    def test_balanced_mode_adds_context_cues_to_plain_converted_text(self) -> None:
        harness = _TtsHarness()
        source = "呜……都说了就一下嘛，怎么还捏个不停啦！再捏下去脸都要肿了哦……笨蛋。"
        spoken = "うーん、ちょっとだけって言ったじゃん！まだ捏ってるの！バカ！"

        controlled, cues = harness._apply_fishaudio_emotion_control(
            spoken,
            provider_kind="fishaudio_s2",
            source_text=source,
        )

        self.assertEqual(["upset"], cues)
        self.assertEqual(f"[upset]{spoken}", controlled)
        self.assertNotIn("[upset]", source)

    def test_expressive_mode_does_not_turn_fillers_into_breathing_cues(self) -> None:
        harness = _TtsHarness()
        harness.tts_fishaudio_emotion_mode = "expressive"

        controlled, cues = harness._apply_fishaudio_emotion_control(
            "うーん、ちょっとだけって言ったじゃん！バカ！",
            provider_kind="fishaudio_s2",
            source_text="呜呜……都说了别继续啦，笨蛋。",
        )

        self.assertEqual(["upset"], cues)
        self.assertEqual(f"[upset]うーん、ちょっとだけって言ったじゃん！バカ！", controlled)
        self.assertNotIn("sighing", controlled)
        self.assertNotIn("panting", controlled)

    def test_auto_modes_drop_breath_effects_and_unproven_sighs(self) -> None:
        for mode in ("balanced", "expressive"):
            with self.subTest(mode=mode):
                harness = _TtsHarness()
                harness.tts_fishaudio_emotion_mode = mode

                sanitized = harness._sanitize_tts_spoken_text(
                    "[喘息][panting][groaning][sighing]うーん、もうやめてよ。",
                    provider_kind="fishaudio_s2",
                )

                self.assertEqual("うーん、もうやめてよ。", sanitized)

    def test_manual_mode_keeps_explicit_breath_effect_cues(self) -> None:
        harness = _TtsHarness()
        harness.tts_fishaudio_emotion_mode = "manual"

        sanitized = harness._sanitize_tts_spoken_text(
            "[panting][sighing]待って。",
            provider_kind="fishaudio_s2",
        )

        self.assertEqual("[panting][sighing]待って。", sanitized)

    def test_explicit_sigh_action_can_use_sighing_control(self) -> None:
        harness = _TtsHarness()

        controlled, cues = harness._apply_fishaudio_emotion_control(
            "ため息をついて、今日は少し疲れた。",
            provider_kind="fishaudio_s2",
            source_text="她叹了一口气，说今天有点累。",
        )

        self.assertEqual(["sighing"], cues)
        self.assertTrue(controlled.startswith("[sighing]"))

    def test_manual_mode_does_not_infer_cues(self) -> None:
        harness = _TtsHarness()
        harness.tts_fishaudio_emotion_mode = "manual"
        spoken = "うーん、もうやめてよ、バカ！"

        controlled, cues = harness._apply_fishaudio_emotion_control(
            spoken,
            provider_kind="fishaudio_s2",
            source_text="都说了别捏啦，笨蛋。",
        )

        self.assertEqual([], cues)
        self.assertEqual(spoken, controlled)

    def test_existing_valid_cue_is_not_duplicated(self) -> None:
        harness = _TtsHarness()
        spoken = "[upset]もうやめてよ、バカ！"

        controlled, cues = harness._apply_fishaudio_emotion_control(
            spoken,
            provider_kind="fishaudio_s2",
            source_text="都说了别继续啦。",
        )

        self.assertEqual([], cues)
        self.assertEqual(spoken, controlled)

    def test_balanced_mode_leaves_neutral_text_untagged(self) -> None:
        harness = _TtsHarness()
        spoken = "わかった。あとで確認するね。"

        controlled, cues = harness._apply_fishaudio_emotion_control(
            spoken,
            provider_kind="fishaudio_s2",
            source_text="知道了，我稍后确认。",
        )

        self.assertEqual([], cues)
        self.assertEqual(spoken, controlled)

    async def test_spoken_language_conversion_requests_fishaudio_controls(self) -> None:
        harness = _ConversionHarness()

        converted = await harness._convert_text_to_spoken_language(
            "都说了别继续啦，笨蛋。",
            object(),
            provider_kind="fishaudio_s2",
        )

        self.assertEqual("うーん、もうやめてよ。", converted)
        self.assertIn("Fish Audio S2", harness.conversion_prompt)
        self.assertIn("[happy]", harness.conversion_prompt)
        self.assertIn("这些控制词属于合成指令", harness.conversion_prompt)
        self.assertIn("不是在向你请求执行、评价或审核原文内容", harness.conversion_prompt)
        self.assertIn("绝对不要输出", harness.conversion_prompt)

    async def test_spoken_language_conversion_rejects_provider_safety_speech(self) -> None:
        harness = _ConversionHarness()
        source = "今天是纯白的啦，就给你看一眼哦。"
        harness.conversion_text = (
            "你的描述包含低俗色情且不适当的内容，不符合公序良俗和道德规范，"
            "因此我不能按照你的要求进行处理。建议你提出积极健康的话题。"
        )

        converted = await harness._convert_text_to_spoken_language(
            source,
            SimpleNamespace(unified_msg_origin="test-session"),
            provider_kind="fishaudio_s2",
        )

        self.assertEqual(source, converted)
        self.assertTrue(harness._tts_text_is_provider_safety_refusal(harness.conversion_text))

    async def test_spoken_conversion_safety_refusal_uses_configured_fallback(self) -> None:
        refusal = (
            "你的描述包含低俗色情且不适当的内容，不符合公序良俗和道德规范，"
            "因此我不能按照你的要求进行处理。"
        )
        primary = _TextProvider("primary", refusal)
        backup = _TextProvider("backup", "今日は白だよ。")
        harness = _ConversionFallbackHarness(primary, backup)

        response = await harness._tts_provider_text_chat(
            primary,
            "转换提示词",
            task="tts_spoken_conversion",
        )

        self.assertEqual("今日は白だよ。", response.completion_text)
        self.assertEqual(1, primary.calls)
        self.assertEqual(1, backup.calls)
        self.assertFalse(harness.usage[0]["success"])
        self.assertEqual("provider_safety_refusal", harness.usage[0]["error"])

    def test_s1_fallback_uses_fixed_parenthesis_cues(self) -> None:
        harness = _TtsHarness()

        controlled, cues = harness._apply_fishaudio_emotion_control(
            "もう見ないでよ、バカ！",
            provider_kind="fishaudio_s1",
            source_text="都说了别看啦，笨蛋！",
        )

        self.assertEqual(["upset"], cues)
        self.assertTrue(controlled.startswith("(upset)"))

    async def test_final_record_path_applies_fallback_before_provider_call(self) -> None:
        harness = _TtsHarness()
        source = "呜……都说了就一下嘛，怎么还捏个不停啦！笨蛋。"
        spoken = "うーん、ちょっとだけって言ったじゃん！まだ捏ってるの！バカ！"
        with tempfile.NamedTemporaryFile(
            dir=get_astrbot_data_path(),
            prefix="pc_fishaudio_test_",
            suffix=".wav",
            delete=False,
        ) as temp_audio:
            temp_audio.write(b"test")
            audio_path = temp_audio.name
        provider = _RecordingFishProvider(audio_path)
        try:
            component = await harness._tts_record_component(
                spoken,
                provider,
                {},
                {},
                source_text=source,
            )
            await asyncio.sleep(0)

            self.assertIsNotNone(component)
            self.assertEqual(f"[upset]{spoken}", provider.received_text)
            self.assertEqual(source, component.text)
            _, recorded_source = harness._lookup_tts_record_text(component)
            self.assertEqual(source, recorded_source)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_realtime_japanese_voice_converts_chinese_before_synthesis(self) -> None:
        harness = _RealtimeHarness("うん、一緒に見よう。")
        with tempfile.NamedTemporaryFile(
            dir=get_astrbot_data_path(), suffix=".wav", delete=False
        ) as temp_audio:
            temp_audio.write(b"test")
            audio_path = temp_audio.name
        provider = _RecordingFishProvider(audio_path)
        try:
            result = await harness._synthesize_realtime_voice(
                "好呀，我们一起看。",
                tts_provider=provider,
                source="together_companion",
            )
            await asyncio.sleep(0)

            self.assertEqual("ja-JP", result["language"])
            self.assertEqual(str(Path(audio_path).resolve()), result["audio_path"])
            self.assertIn("一緒に見よう", provider.received_text)
            self.assertNotIn("我们一起看", provider.received_text)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def test_realtime_foreign_voice_blocks_unconverted_chinese(self) -> None:
        harness = _RealtimeHarness("好呀，我们一起看。")
        provider = _RecordingFishProvider("unused.wav")

        result = await harness._synthesize_realtime_voice(
            "好呀，我们一起看。",
            tts_provider=provider,
        )

        self.assertEqual("language_conversion_failed", result["reason"])
        self.assertEqual("ja-JP", result["language"])
        self.assertEqual("", result["fallback_text"])
        self.assertEqual("", result["audio_path"])
        self.assertEqual("", provider.received_text)
        self.assertEqual(2, harness.conversion_calls)

    async def test_realtime_missing_provider_still_returns_converted_fallback(self) -> None:
        harness = _RealtimeHarness("うん、一緒に見よう。")

        result = await harness._synthesize_realtime_voice(
            "好呀，我们一起看。",
            tts_provider=None,
        )

        self.assertFalse(result["available"])
        self.assertEqual("tts_provider_unavailable", result["reason"])
        self.assertEqual("ja-JP", result["language"])
        self.assertIn("一緒に見よう", result["fallback_text"])
        self.assertEqual("", result["audio_path"])
        self.assertEqual(1, harness.conversion_calls)

    async def test_realtime_voice_can_disable_local_playback(self) -> None:
        harness = _RealtimeHarness("うん、一緒に見よう。")
        harness.enable_tts_local_playback = True
        harness.enable_tts_local_playback_live_only = False
        harness.enable_tts_live_subtitle_sync = False

        with patch.object(harness, "_open_tts_audio_file_local") as local_playback:
            await TtsEnhancementMixin._after_tts_audio_generated(
                harness,
                "voice.wav",
                "一緒に見よう。",
                source="together_companion",
                allow_local_playback=False,
            )

        local_playback.assert_not_called()

    async def test_realtime_chinese_voice_synthesizes_without_conversion(self) -> None:
        harness = _RealtimeHarness("不应调用")
        harness.tts_voice_language = "zh"
        with tempfile.NamedTemporaryFile(
            dir=get_astrbot_data_path(), suffix=".wav", delete=False
        ) as temp_audio:
            temp_audio.write(b"test")
            audio_path = temp_audio.name
        provider = _RecordingFishProvider(audio_path)
        try:
            result = await harness._synthesize_realtime_voice(
                "好呀，我们一起看。",
                tts_provider=provider,
            )
            await asyncio.sleep(0)

            self.assertEqual("zh-CN", result["language"])
            self.assertIn("我们一起看", provider.received_text)
        finally:
            Path(audio_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
