# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.integration_status import IntegrationStatusMixin


class _Provider:
    def __init__(self, provider_id: str, name: str, model: str) -> None:
        self.provider_id = provider_id
        self.provider_config = {
            "id": provider_id,
            "name": name,
            "model": model,
        }


class _Event:
    unified_msg_origin = "default:FriendMessage:10001"

    @staticmethod
    def get_extra(key: str):
        return "chat-main" if key == "selected_provider" else ""


class _Context:
    def __init__(self, *, tts_provider=None, tts_enabled: bool = True) -> None:
        self.providers = {
            "chat-main": _Provider("chat-main", "对话模型", "chat-model"),
            "tts-convert": _Provider("tts-convert", "TTS转换", "doubao-mini"),
        }
        self.tts_provider = tts_provider
        self.tts_enabled = tts_enabled

    def get_provider_by_id(self, provider_id: str):
        return self.providers.get(provider_id)

    def get_using_provider(self, *args, **kwargs):
        return self.providers["chat-main"]

    def get_using_tts_provider(self, *args, **kwargs):
        return self.tts_provider

    def get_config(self, *args, **kwargs):
        return {
            "provider_tts_settings": {
                "enable": self.tts_enabled,
                "type": "fishaudio",
            }
        }


class _ModelPerceptionHarness(IntegrationStatusMixin):
    enable_model_perception = True
    enable_tts_enhancement = True
    tts_generation_mode = "fast_tag"
    tts_voice_language = "ja"
    tts_conversion_scope = "partial"
    tts_delivery_mode = "voice_and_text"
    tts_conversion_provider_id = "tts-convert"

    def __init__(self, context: _Context) -> None:
        self.context = context

    @staticmethod
    def _private_image_caption_provider_id(_umo: str):
        return "", "", None

    @staticmethod
    def _format_photo_generation_perception() -> str:
        return ""

    @staticmethod
    def _tts_provider_kind(_provider, _settings) -> str:
        return "fishaudio"

    @staticmethod
    def _tts_language_label() -> str:
        return "日语"


class ModelPerceptionTtsTests(unittest.TestCase):
    def test_model_perception_includes_tts_provider_and_delivery_settings(self) -> None:
        tts_provider = _Provider("tts-main", "FishAudio", "speech-1.5")
        harness = _ModelPerceptionHarness(_Context(tts_provider=tts_provider))

        text = harness._format_model_perception(_Event())

        self.assertIn("对话模型=chat-model", text)
        self.assertIn("TTS能力=合成 Provider 可用", text)
        self.assertIn("FishAudio", text)
        self.assertIn("文本转换模型:doubao-mini", text)
        self.assertIn("路径:快速标签", text)
        self.assertIn("语种:日语", text)
        self.assertIn("范围:局部转换", text)
        self.assertIn("交付:语音+文字", text)

    def test_model_perception_reports_unavailable_tts_without_sensitive_details(self) -> None:
        harness = _ModelPerceptionHarness(_Context(tts_provider=None, tts_enabled=False))
        harness.enable_tts_enhancement = False

        text = harness._format_model_perception(_Event())

        self.assertIn("TTS能力=当前会话未配置 TTS Provider", text)
        self.assertIn("TTS强化:关闭", text)
        self.assertNotIn("文本转换模型", text)
        self.assertNotIn("api_key", text.lower())


if __name__ == "__main__":
    unittest.main()
