# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiTtsPart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_tts.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 73 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiTtsMixin）。
"""
from __future__ import annotations
from .page_api_tts_shared import Any



class PrivateCompanionPageApiTtsPart03Mixin:
    """PrivateCompanionPageApiTtsPart03Mixin（从 PrivateCompanionPageApiTtsMixin 拆出）。"""


    def _tts_runtime_summary(self, users: dict[str, Any]) -> dict[str, Any]:
        umo = self._preferred_tts_test_umo(
            users,
            owner_only=True,
            resolve_delivery_route=True,
        )
        config: dict[str, Any] = {}
        provider_settings: dict[str, Any] = {}
        provider = None
        context = getattr(self.plugin, "context", None)
        if context is not None:
            getter = getattr(context, "get_config", None)
            if callable(getter):
                try:
                    config = getter(umo) if umo else getter()
                    if not isinstance(config, dict):
                        config = {}
                except Exception:
                    config = {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
            provider_getter = getattr(context, "get_using_tts_provider", None)
            if callable(provider_getter):
                try:
                    provider = provider_getter(umo) if umo else provider_getter()
                except Exception:
                    provider = None
        synthesis_backend = self._single_line(
            getattr(self.plugin, "tts_synthesis_backend", ""),
            32,
        ) or "astrbot_provider"
        synthesis_resolver = getattr(self.plugin, "_resolve_tts_synthesis_provider", None)
        effective_provider = provider
        if callable(synthesis_resolver):
            try:
                effective_provider = synthesis_resolver(None, provider)
            except Exception:
                effective_provider = provider
        mimo_adapter = (
            effective_provider
            if effective_provider is not None
            and effective_provider.__class__.__name__ == "_MimoVoiceCloneTtsAdapter"
            else None
        )
        provider_label = ""
        if mimo_adapter is not None:
            provider_label = "MiMo TTS Voice Clone 插件"
        elif effective_provider is not None:
            provider_id = self._provider_id(effective_provider)
            provider_label = self._provider_name(effective_provider, provider_id) if provider_id else getattr(effective_provider, "__class__", type(effective_provider)).__name__
        return {
            "enhancement_enabled": bool(getattr(self.plugin, "enable_tts_enhancement", False)),
            "synthesis_backend": synthesis_backend,
            "mimo_voice_clone_available": mimo_adapter is not None,
            "mimo_tool_name": self._single_line(
                getattr(self.plugin, "tts_mimo_tool_name", ""),
                120,
            ) or "mimo_tts_speak",
            "mode": self._single_line(getattr(self.plugin, "tts_generation_mode", ""), 24) or "fast_tag",
            "language": self.plugin._tts_language_label() if hasattr(self.plugin, "_tts_language_label") else "",
            "fishaudio_model": self._single_line(getattr(self.plugin, "tts_fishaudio_model", ""), 32) or "auto",
            "fishaudio_emotion_mode": self._single_line(
                getattr(self.plugin, "tts_fishaudio_emotion_mode", ""),
                24,
            ) or "balanced",
            "delivery_mode": self._single_line(getattr(self.plugin, "tts_delivery_mode", ""), 32) or "voice_and_text",
            "foreign_text_mode": self._single_line(getattr(self.plugin, "tts_foreign_text_mode", ""), 32) or "translation",
            "message_scope": self._single_line(getattr(self.plugin, "tts_message_scope", ""), 32) or "replies_only",
            "conversion_scope": self._single_line(getattr(self.plugin, "tts_conversion_scope", ""), 24) or "partial",
            "umo": umo,
            "settings_enabled": bool(provider_settings.get("enable", False)) or synthesis_backend == "mimo_voice_clone",
            "provider_available": effective_provider is not None,
            "provider_label": self._single_line(provider_label, 80) or "未知 provider",
        }
