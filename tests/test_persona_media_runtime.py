from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_private_companion.private_image import PrivateImageMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _PersonaImageHarness(PrivateImageMixin):
    def __init__(self) -> None:
        self.private_image_provider_timeout_seconds = 12.0
        self.private_image_vision_wait_seconds = 30.0
        self._overrides: dict[str, object] = {}

    def persona_setting(self, key: str, default: object = None, persona_id: str = "") -> object:
        return self._overrides.get(key, default)


class _PersonaTtsHarness(TtsEnhancementMixin):
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self._overrides: dict[str, object] = {}

    def persona_setting(self, key: str, default: object = None, persona_id: str = "") -> object:
        return self._overrides.get(key, default)

    def _save_data_sync(self, **_kwargs: object) -> None:
        return None


def test_private_image_runtime_timeout_reads_active_persona_override() -> None:
    harness = _PersonaImageHarness()
    assert harness._private_image_provider_timeout_seconds() == 12.0

    harness._overrides["private_image_provider_timeout_seconds"] = 4.5
    assert harness._private_image_provider_timeout_seconds() == 4.5


def test_tts_voice_language_reads_persona_and_runtime_overrides_without_attr_mutation() -> None:
    harness = _PersonaTtsHarness()
    harness._overrides["tts_voice_language"] = "ja"
    assert harness._tts_voice_language_for_event(None) == "ja"

    harness.data["runtime_settings"] = {"tts_voice_language": "en"}
    assert harness._tts_voice_language_for_event(None) == "en"

    event = SimpleNamespace(unified_msg_origin="test:session", message_str="")
    assert harness._set_tts_voice_language_from_command("中文").startswith("已切换")
    assert harness._tts_voice_language_for_event(event) == "zh"
    assert not hasattr(harness, "tts_voice_language")
