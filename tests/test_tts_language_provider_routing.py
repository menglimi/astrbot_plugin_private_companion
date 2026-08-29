# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _FakeTtsProvider:
    def __init__(self, provider_id: str, name: str, model: str) -> None:
        self.provider_config = {
            "id": provider_id,
            "name": name,
            "type": "fake_tts",
            "model": model,
        }
        self.model_name = model

    def get_model(self) -> str:
        return self.model_name


class _FakeContext:
    def __init__(self, providers: list[_FakeTtsProvider], current: _FakeTtsProvider | None = None) -> None:
        self.providers = providers
        self.current = current

    def get_all_tts_providers(self) -> list[_FakeTtsProvider]:
        return list(self.providers)

    def get_using_tts_provider(self, *_args, **_kwargs):
        return self.current


class _RoutingHarness(TtsEnhancementMixin):
    def __init__(self) -> None:
        self.zh = _FakeTtsProvider("tts-zh", "中文声线", "cosyvoice-v2")
        self.ja = _FakeTtsProvider("tts-ja", "日语声线", "fish-s2.1")
        self.en = _FakeTtsProvider("tts-en", "English Voice", "edge-en-US")
        self.default = _FakeTtsProvider("tts-default", "默认声线", "default")
        self.context = _FakeContext([self.zh, self.ja, self.en, self.default], self.default)
        self.tts_synthesis_backend = "astrbot_provider"
        self.tts_voice_language = "ja"
        self.tts_provider_id_zh = "tts-zh"
        self.tts_provider_id_ja = "tts-ja"
        self.tts_provider_id_en = "tts-en"
        self.data = {"runtime_settings": {}}
        self.config = {"tts_voice_language": "ja"}
        self.saved = 0
        self.persona_values: dict[str, object] = {}

    def persona_setting(self, key: str, default: object = None) -> object:
        return self.persona_values.get(key, getattr(self, key, default))

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class TtsLanguageProviderRoutingTests(unittest.TestCase):
    def test_each_language_resolves_its_configured_provider(self) -> None:
        harness = _RoutingHarness()
        for language, expected in (("zh", harness.zh), ("ja", harness.ja), ("en", harness.en)):
            with self.subTest(language=language):
                harness.tts_voice_language = language
                self.assertIs(harness._resolve_tts_synthesis_provider(None, harness.default), expected)

    def test_missing_or_invalid_language_provider_falls_back_to_session_provider(self) -> None:
        harness = _RoutingHarness()
        harness.tts_voice_language = "zh"
        harness.tts_provider_id_zh = ""
        self.assertIs(harness._resolve_tts_synthesis_provider(None, harness.default), harness.default)

        harness.tts_provider_id_zh = "removed-provider"
        self.assertIs(harness._resolve_tts_synthesis_provider(None, harness.default), harness.default)

    def test_explicit_mimo_backend_keeps_mimo_precedence(self) -> None:
        harness = _RoutingHarness()
        mimo = object()
        harness.tts_synthesis_backend = "mimo_voice_clone"
        harness._find_mimo_voice_clone_tts_adapter = lambda _event: mimo
        self.assertIs(harness._resolve_tts_synthesis_provider(None, harness.default), mimo)

    def test_language_command_switches_provider_without_reload(self) -> None:
        harness = _RoutingHarness()
        response = harness._set_tts_voice_language_from_command("中文")
        self.assertIn("中文", response)
        self.assertEqual(harness.data["runtime_settings"]["tts_voice_language"], "zh")
        self.assertIs(harness._resolve_tts_synthesis_provider(None, harness.default), harness.zh)
        self.assertEqual(harness.saved, 1)

    def test_dedicated_language_provider_model_beats_global_fish_fallback(self) -> None:
        harness = _RoutingHarness()
        harness.tts_voice_language = "ja"
        harness.tts_fishaudio_model = "s1"
        harness.ja.provider_config["model"] = "s2.1-pro"
        harness.ja.model_name = "s2.1-pro"
        self.assertEqual(
            harness._tts_fishaudio_model_for_provider(harness.ja, harness.ja.provider_config),
            "s2.1-pro",
        )

    def test_language_provider_uses_active_persona_override(self) -> None:
        harness = _RoutingHarness()
        harness.tts_voice_language = "zh"
        harness.tts_provider_id_zh = "tts-zh"
        harness.persona_values["tts_provider_id_zh"] = "tts-en"

        self.assertIs(
            harness._resolve_tts_synthesis_provider(None, harness.default),
            harness.en,
        )

    def test_page_api_lists_only_tts_provider_choices(self) -> None:
        harness = _RoutingHarness()
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace(context=harness.context)
        items = api._available_tts_provider_items()
        self.assertEqual(items[0]["id"], "tts-default")
        self.assertEqual({item["id"] for item in items[1:]}, {"tts-en", "tts-ja", "tts-zh"})
        default = items[0]
        self.assertTrue(default["is_default"])
        japanese = next(item for item in items if item["id"] == "tts-ja")
        self.assertEqual(japanese["model"], "fish-s2.1")


if __name__ == "__main__":
    unittest.main()
