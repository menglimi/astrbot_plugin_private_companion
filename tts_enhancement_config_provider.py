# -*- coding: utf-8 -*-
"""TtsEnhancementConfigProviderMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（22 个方法 + 1 个模块级名字 + 0 个类级赋值 / 860 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
from .helpers import _single_line
from .persona_config import runtime_persona_setting
from .tts_enhancement_shared import (
    DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
    FISH_AUDIO_EMOTION_MODES,
    FISH_AUDIO_MODELS,
    TTS_LANGUAGE_PROVIDER_ATTRS,
    logger,
)
from typing import Any



class _MimoVoiceCloneTtsAdapter:
    """Expose MiMo TTS Voice Clone's public service as an AstrBot-like TTS provider."""

    name = "MiMo TTS Voice Clone plugin"
    provider_type = "tts"
    model_name = "mimo-v2.5-tts-voiceclone"

    def __init__(
        self,
        plugin: Any,
        event: Any,
        *,
        voice_name: str = "",
        style: str = "",
        tool_name: str = DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
    ) -> None:
        self.plugin = plugin
        self.event = event
        self.voice_name = str(voice_name or "").strip()
        self.style = str(style or "").strip()
        self.tool_name = str(tool_name or DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME).strip()

    def get_model(self) -> str:
        return self.model_name

    def readiness(self) -> tuple[bool, str]:
        plugin_config = getattr(self.plugin, "plugin_config", None)
        if plugin_config is not None:
            api_key = str(getattr(plugin_config, "api_key", "") or "").strip()
            if not api_key:
                return False, "missing_api_key"

        voice_getter = getattr(self.plugin, "list_available_voices", None)
        if callable(voice_getter):
            try:
                voices = list(voice_getter() or [])
            except Exception:
                voices = []
            if not voices:
                return False, "missing_voice"
            if self.voice_name:
                matched = any(
                    self.voice_name
                    in {
                        str(item.get("id", "") or "").strip(),
                        str(item.get("name", "") or "").strip(),
                    }
                    for item in voices
                    if isinstance(item, dict)
                )
                if not matched:
                    return False, "voice_not_found"
        return True, "ready"

    @staticmethod
    def _supported_kwargs(method: Any, values: dict[str, Any]) -> dict[str, Any]:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return values
        accepts_extra = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_extra:
            return values
        return {key: value for key, value in values.items() if key in signature.parameters}

    def _event_user_id(self) -> str:
        getter = getattr(self.event, "get_sender_id", None)
        if callable(getter):
            try:
                return str(getter() or "").strip()
            except Exception:
                pass
        session_id = self._event_session_id()
        if ":" in session_id:
            return session_id.rsplit(":", 1)[-1].strip()
        return ""

    def _event_session_id(self) -> str:
        return str(getattr(self.event, "unified_msg_origin", "") or "").strip()

    @staticmethod
    async def _await_result(result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _audio_path_from_result(result: Any) -> str:
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        if isinstance(result, os.PathLike):
            return os.fspath(result)
        if isinstance(result, str):
            return result.strip()
        for attr in ("audio_path", "output_path", "path", "file", "url"):
            value = getattr(result, attr, "")
            if isinstance(value, os.PathLike):
                return os.fspath(value)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def get_audio(self, text: str) -> str:
        synthesize = getattr(self.plugin, "synthesize_text", None)
        if callable(synthesize):
            kwargs = self._supported_kwargs(
                synthesize,
                {
                    "voice_name": self.voice_name or None,
                    "context": self.style,
                    "user_id": self._event_user_id(),
                    "group_id": self._event_session_id(),
                    "split": False,
                },
            )
            result = await self._await_result(synthesize(str(text or ""), **kwargs))
            return self._audio_path_from_result(result)

        compatibility = getattr(self.plugin, "text_to_speech", None)
        if callable(compatibility):
            kwargs = self._supported_kwargs(
                compatibility,
                {
                    "voice_name": self.voice_name,
                    "context": self.style,
                    "target_umo": self._event_session_id(),
                    "session_id": self._event_session_id(),
                },
            )
            result = await self._await_result(compatibility(str(text or ""), **kwargs))
            return self._audio_path_from_result(result)
        return ""


class TtsEnhancementConfigProviderMixin:
    """TtsEnhancementConfigProviderMixin（从 TtsEnhancementMixin 拆出）。"""


    def _tts_setting(self, key: str, default: Any = None) -> Any:
        """Read TTS configuration for the active persona without shared mutation."""
        return runtime_persona_setting(self, key, default)

    def _create_tts_background_task(self, operation: Any, *, label: str) -> asyncio.Task | None:
        creator = getattr(self, "_create_lifecycle_background_task", None)
        if callable(creator):
            task = creator(operation, label=label)
            if task is None:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
            return task
        try:
            task = asyncio.create_task(operation, name=f"private-companion-tts-{label}")
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            return None

        def consume(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "TTS background task failed: label=%s error=%s",
                    label,
                    _single_line(exc, 160),
                )

        task.add_done_callback(consume)
        return task

    def _load_tts_enhancement_config(self, config: Any) -> None:
        self.enable_tts_enhancement = self._cfg_bool(config, "enable_tts_enhancement", False)
        self.tts_message_scope = self._cfg_str(
            config,
            "tts_message_scope",
            "replies_only",
            "replies_only",
        ).lower()
        if self.tts_message_scope not in {"replies_only", "replies_and_proactive"}:
            self.tts_message_scope = "replies_only"
        raw_synthesis_backend = self._cfg_str(
            config,
            "tts_synthesis_backend",
            "astrbot_provider",
            "astrbot_provider",
        ).lower()
        synthesis_backend_aliases = {
            "astrbot": "astrbot_provider",
            "provider": "astrbot_provider",
            "official": "astrbot_provider",
            "mimo": "mimo_voice_clone",
            "mimotts": "mimo_voice_clone",
            "mimo_plugin": "mimo_voice_clone",
            "plugin": "mimo_voice_clone",
        }
        self.tts_synthesis_backend = synthesis_backend_aliases.get(
            raw_synthesis_backend,
            raw_synthesis_backend,
        )
        if self.tts_synthesis_backend not in {"astrbot_provider", "mimo_voice_clone", "auto"}:
            self.tts_synthesis_backend = "astrbot_provider"
        self.tts_mimo_tool_name = self._cfg_str(
            config,
            "tts_mimo_tool_name",
            DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
            DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
        )
        self.tts_mimo_voice_name = self._cfg_str(config, "tts_mimo_voice_name", "")
        self.tts_mimo_style_prompt = self._cfg_str(config, "tts_mimo_style_prompt", "")
        raw_mode = self._cfg_str(config, "tts_generation_mode", "fast_tag", "fast_tag").lower()
        mode_aliases = {
            "hybrid": "fast_tag",
            "direct": "fast_tag",
            "tag": "fast_tag",
            "tags": "fast_tag",
            "fast": "fast_tag",
            "convert": "postprocess",
            "post": "postprocess",
            "llm": "postprocess",
        }
        self.tts_generation_mode = mode_aliases.get(raw_mode, raw_mode)
        if self.tts_generation_mode not in {"fast_tag", "postprocess"}:
            self.tts_generation_mode = "fast_tag"
        self.tts_legacy_generation_mode = raw_mode
        self.tts_voice_language = self._cfg_str(config, "tts_voice_language", "zh", "zh").lower()
        if self.tts_voice_language not in {"ja", "zh", "en"}:
            self.tts_voice_language = "zh"
        for language, attr in TTS_LANGUAGE_PROVIDER_ATTRS.items():
            setattr(self, attr, self._cfg_str(config, attr, ""))
        self.tts_delivery_mode = self._cfg_str(config, "tts_delivery_mode", "voice_and_text", "voice_and_text").lower()
        if self.tts_delivery_mode not in {"voice_only", "voice_and_text"}:
            self.tts_delivery_mode = "voice_and_text"
        self.tts_foreign_text_mode = self._cfg_str(config, "tts_foreign_text_mode", "translation", "translation").lower()
        if self.tts_foreign_text_mode not in {"original", "translation", "bilingual"}:
            self.tts_foreign_text_mode = "translation"
        self.tts_conversion_provider_id = self._cfg_str(config, "tts_conversion_provider_id", "")
        self.tts_extra_prompt = self._cfg_str(config, "tts_extra_prompt", "")
        self.tts_fishaudio_model = self._cfg_str(config, "tts_fishaudio_model", "auto", "auto").lower()
        if self.tts_fishaudio_model not in {"auto", *FISH_AUDIO_MODELS}:
            self.tts_fishaudio_model = "auto"
        self.tts_fishaudio_emotion_mode = self._cfg_str(
            config,
            "tts_fishaudio_emotion_mode",
            "balanced",
            "balanced",
        ).lower()
        if self.tts_fishaudio_emotion_mode not in FISH_AUDIO_EMOTION_MODES:
            self.tts_fishaudio_emotion_mode = "balanced"
        self.tts_frequency_control_mode = self._cfg_str(config, "tts_frequency_control_mode", "global", "global").lower()
        if self.tts_frequency_control_mode not in {"global", "legacy"}:
            self.tts_frequency_control_mode = "global"
        self.tts_constraint_mode = self._cfg_str(config, "tts_constraint_mode", "weak", "weak").lower()
        if self.tts_constraint_mode not in {"weak", "strong"}:
            self.tts_constraint_mode = "weak"
        self.tts_session_min_interval_seconds = self._cfg_float(config, "tts_session_min_interval_seconds", 90.0, 0.0)
        self.tts_private_min_interval_seconds = self._cfg_float(config, "tts_private_min_interval_seconds", -1.0, -1.0)
        self.tts_group_min_interval_seconds = self._cfg_float(config, "tts_group_min_interval_seconds", -1.0, -1.0)
        self.tts_trigger_probability = self._cfg_int(
            config,
            "tts_trigger_probability",
            self._cfg_int(config, "auto_voice_probability", self._cfg_int(config, "auto_japanese_voice_probability", 25, 0, 100), 0, 100),
            0,
            100,
        ) / 100.0
        self.tts_private_trigger_probability = self._cfg_int(config, "tts_private_trigger_probability", -1, -1, 100) / 100.0
        self.tts_group_trigger_probability = self._cfg_int(config, "tts_group_trigger_probability", -1, -1, 100) / 100.0
        self.tts_trigger_keywords = self._normalize_tts_trigger_keywords(
            self._cfg_raw(config, "tts_trigger_keywords", "")
        )
        self.auto_voice_enabled = self._cfg_bool(config, "auto_voice_enabled", self._cfg_bool(config, "auto_japanese_voice_enabled", False))
        legacy_full_conversion = self._cfg_bool(
            config,
            "auto_voice_full_conversion_enabled",
            self._cfg_bool(config, "auto_japanese_voice_full_conversion_enabled", False),
        )
        raw_conversion_scope = self._cfg_raw(config, "tts_conversion_scope", None)
        self.tts_conversion_scope = (
            str(raw_conversion_scope).strip().lower()
            if raw_conversion_scope not in (None, "")
            else ("full" if legacy_full_conversion else "partial")
        )
        if self.tts_conversion_scope not in {"partial", "full"}:
            self.tts_conversion_scope = "partial"
        self.auto_voice_full_conversion_enabled = self.tts_conversion_scope == "full"
        self.auto_voice_probability = self._cfg_int(
            config,
            "auto_voice_probability",
            int(round(self.tts_trigger_probability * 100)),
            0,
            100,
        ) / 100.0
        self.auto_voice_max_chars = self._cfg_int(
            config,
            "auto_voice_max_chars",
            self._cfg_int(config, "auto_japanese_voice_max_chars", 50, 0),
            0,
        )
        self.auto_voice_cooldown_seconds = self._cfg_int(
            config,
            "auto_voice_cooldown_seconds",
            self._cfg_int(config, "auto_japanese_voice_cooldown_seconds", 120, 0),
            0,
        )
        self.main_user_voice_probability = self._cfg_int(
            config,
            "main_user_voice_probability",
            self._cfg_int(config, "auto_japanese_voice_admin_probability", -1, -1, 100),
            -1,
            100,
        ) / 100.0
        self.main_user_mention_voice_keywords = self._parse_text_list_config(
            config.get("main_user_mention_voice_keywords", config.get("admin_mention_keyword_voice_keywords", "")),
            limit=80,
        )
        self.main_user_mention_voice_probability = self._cfg_int(
            config,
            "main_user_mention_voice_probability",
            self._cfg_int(config, "admin_mention_keyword_voice_probability", 0, 0, 100),
            0,
            100,
        ) / 100.0
        self.main_user_mention_voice_prompt = self._cfg_str(
            config,
            "main_user_mention_voice_prompt",
            self._cfg_str(config, "admin_mention_keyword_voice_prompt", ""),
        )
        self.enable_tts_local_playback = self._cfg_bool(config, "enable_tts_local_playback", False)
        self.enable_tts_local_playback_live_only = self._cfg_bool(config, "enable_tts_local_playback_live_only", False)
        self.enable_tts_live_subtitle_sync = self._cfg_bool(config, "enable_tts_live_subtitle_sync", False)
        self.tts_live_subtitle_url = self._cfg_str(config, "tts_live_subtitle_url", "http://127.0.0.1:18081/show", "http://127.0.0.1:18081/show")
        self.tts_local_playback_volume = self._cfg_int(config, "tts_local_playback_volume", 35, 0, 100)
        self.tts_local_playback_min_interval_seconds = self._cfg_float(config, "tts_local_playback_min_interval_seconds", 0.0, 0.0)
        self._tts_local_playback_last_at = 0.0
        self._tts_local_playback_failures = 0
        self._tts_local_playback_retry_after = 0.0
        self._tts_auto_voice_last_at: dict[str, float] = {}
        if not isinstance(getattr(self, "_tts_session_last_at", None), dict):
            self._tts_session_last_at: dict[str, float] = {}
        self._apply_tts_runtime_overrides()

    @staticmethod
    def _mimo_voice_clone_plugin_from_handler(handler: Any) -> Any | None:
        pending = [handler]
        visited: set[int] = set()
        while pending:
            current = pending.pop(0)
            if current is None or id(current) in visited:
                continue
            visited.add(id(current))
            owner = getattr(current, "__self__", None)
            if owner is not None:
                pending.append(owner)
            pending.extend(list(getattr(current, "args", ()) or ()))
            wrapped = getattr(current, "func", None)
            if wrapped is not None and wrapped is not current:
                pending.append(wrapped)
            if callable(getattr(current, "synthesize_text", None)) or callable(
                getattr(current, "text_to_speech", None)
            ):
                return current
        return None

    def _find_mimo_voice_clone_tts_adapter(self, event: Any) -> Any | None:
        tool_name = _single_line(
            self._tts_setting("tts_mimo_tool_name", DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME),
            120,
        ) or DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME
        try:
            manager = self.context.get_llm_tool_manager()
        except Exception as exc:
            logger.debug("读取 MiMo TTS 工具管理器失败: %s", _single_line(exc, 120))
            return None
        if manager is None:
            return None

        tool = None
        get_tool = getattr(manager, "get_tool", None)
        if callable(get_tool):
            try:
                tool = get_tool(tool_name)
            except Exception:
                tool = None
        if tool is None:
            get_func = getattr(manager, "get_func", None)
            if callable(get_func):
                try:
                    tool = get_func(tool_name)
                except Exception:
                    tool = None
        if tool is None:
            return None

        handler = getattr(tool, "handler", None)
        plugin = self._mimo_voice_clone_plugin_from_handler(handler)
        if plugin is None:
            warning_key = f"{tool_name}:{id(handler)}"
            if getattr(self, "_tts_mimo_bridge_handler_warning_key", "") != warning_key:
                self._tts_mimo_bridge_handler_warning_key = warning_key
                logger.warning(
                    "已找到 MiMo TTS 工具但无法取得插件公开合成服务: tool=%s",
                    tool_name,
                )
            return None
        bridge_key = f"{tool_name}:{id(plugin)}"
        if getattr(self, "_tts_mimo_bridge_key", "") != bridge_key:
            self._tts_mimo_bridge_key = bridge_key
            logger.info(
                "已发现 MiMo TTS Voice Clone: tool=%s service=%s",
                tool_name,
                plugin.__class__.__name__,
            )
        return _MimoVoiceCloneTtsAdapter(
            plugin,
            event,
            voice_name=self._tts_setting("tts_mimo_voice_name", ""),
            style=self._tts_setting("tts_mimo_style_prompt", ""),
            tool_name=tool_name,
        )

    @staticmethod
    def _tts_synthesis_provider_id(provider: Any) -> str:
        if provider is None:
            return ""
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
        if isinstance(config, dict):
            provider_id = _single_line(config.get("id") or config.get("provider_id"), 160)
            if provider_id:
                return provider_id
        for attr in ("provider_id", "id"):
            provider_id = _single_line(getattr(provider, attr, ""), 160)
            if provider_id:
                return provider_id
        meta_getter = getattr(provider, "meta", None)
        if callable(meta_getter):
            try:
                metadata = meta_getter()
                if isinstance(metadata, dict):
                    return _single_line(metadata.get("id"), 160)
                return _single_line(getattr(metadata, "id", ""), 160)
            except Exception:
                pass
        return ""

    def _language_tts_provider(self, event: Any = None) -> Any:
        language = self._tts_voice_language_for_event(event)
        attr = TTS_LANGUAGE_PROVIDER_ATTRS.get(language, "")
        provider_id = _single_line(self._tts_setting(attr, ""), 160) if attr else ""
        if not provider_id:
            return None
        context = getattr(self, "context", None)
        get_all = getattr(context, "get_all_tts_providers", None)
        try:
            providers = list(get_all() or []) if callable(get_all) else []
        except Exception:
            providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            providers = list(getattr(manager, "tts_provider_insts", None) or [])
        for provider in providers:
            if self._tts_synthesis_provider_id(provider) == provider_id:
                return provider
        warning_key = f"{language}:{provider_id}"
        if getattr(self, "_tts_language_provider_warning_key", "") != warning_key:
            self._tts_language_provider_warning_key = warning_key
            logger.warning(
                "当前语种配置的 TTS Provider 不可用,已回退现有合成链路: language=%s provider=%s",
                language,
                provider_id,
            )
        return None

    def _resolve_tts_synthesis_provider(self, event: Any, astrbot_provider: Any = None) -> Any:
        mode = str(
            self._tts_setting("tts_synthesis_backend", "astrbot_provider")
            or "astrbot_provider"
        ).lower()
        if mode != "mimo_voice_clone":
            language_provider = self._language_tts_provider(event)
            if language_provider is not None:
                return language_provider
        if mode == "astrbot_provider" or (mode == "auto" and astrbot_provider is not None):
            return astrbot_provider

        mimo_adapter = self._find_mimo_voice_clone_tts_adapter(event)
        if mimo_adapter is not None:
            if mode == "auto":
                ready, reason = mimo_adapter.readiness()
                if not ready:
                    state_key = f"{mimo_adapter.tool_name}:{reason}"
                    if getattr(self, "_tts_mimo_auto_readiness_log_key", "") != state_key:
                        self._tts_mimo_auto_readiness_log_key = state_key
                        reason_label = {
                            "missing_api_key": "尚未配置 API Key",
                            "missing_voice": "尚未上传可用克隆音色",
                            "voice_not_found": "指定音色不存在或已停用",
                        }.get(reason, reason)
                        logger.info(
                            "自动识别到 MiMo TTS Voice Clone,但暂不接管合成: reason=%s fallback=%s",
                            reason_label,
                            "AstrBot TTS provider" if astrbot_provider is not None else "文字/浏览器朗读",
                        )
                    return astrbot_provider
                ready_key = f"{mimo_adapter.tool_name}:ready:{id(mimo_adapter.plugin)}"
                if getattr(self, "_tts_mimo_auto_ready_log_key", "") != ready_key:
                    self._tts_mimo_auto_ready_log_key = ready_key
                    logger.info(
                        "MiMo TTS Voice Clone 已自动识别并接管语音合成: tool=%s",
                        mimo_adapter.tool_name,
                    )
            return mimo_adapter
        if mode == "mimo_voice_clone" and astrbot_provider is not None:
            fallback_key = _single_line(self._tts_setting("tts_mimo_tool_name", ""), 120) or DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME
            if getattr(self, "_tts_mimo_bridge_fallback_warning_key", "") != fallback_key:
                self._tts_mimo_bridge_fallback_warning_key = fallback_key
                logger.warning(
                    "MiMo TTS Voice Clone 联动不可用,本次回退 AstrBot TTS provider: tool=%s",
                    fallback_key,
                )
        return astrbot_provider

    def _tts_fishaudio_model_for_provider(
        self,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        *,
        voice_language: str = "",
    ) -> str:
        configured = str(self._tts_setting("tts_fishaudio_model", "auto") or "auto").strip().lower()
        language = self._normalize_tts_voice_language_value(
            voice_language or self._tts_setting("tts_voice_language", "zh")
        ) or "zh"
        language_attr = TTS_LANGUAGE_PROVIDER_ATTRS.get(language, "")
        language_provider_id = (
            _single_line(self._tts_setting(language_attr, ""), 160)
            if language_attr
            else ""
        )
        active_provider_id = self._tts_synthesis_provider_id(tts_provider)
        has_dedicated_language_provider = bool(
            language_provider_id
            and active_provider_id
            and language_provider_id == active_provider_id
        )
        if configured in FISH_AUDIO_MODELS and not has_dedicated_language_provider:
            return configured

        candidates: list[str] = []
        if tts_provider is not None:
            get_model = getattr(tts_provider, "get_model", None)
            if callable(get_model):
                try:
                    candidates.append(str(get_model() or ""))
                except Exception:
                    pass
            for attr in ("model_name", "model"):
                candidates.append(str(getattr(tts_provider, attr, "") or ""))
        if provider_settings:
            candidates.append(str(provider_settings.get("model", "") or ""))
        for candidate in candidates:
            normalized = candidate.strip().lower()
            if normalized in FISH_AUDIO_MODELS:
                return normalized

        api_base = str(getattr(tts_provider, "api_base", "") or "").strip().lower()
        if not api_base and provider_settings:
            api_base = str(provider_settings.get("api_base", "") or "").strip().lower()
        if "api.fish.audio" in api_base or "api.fish-audio.cn" in api_base:
            return "s2.1-pro-free"
        return ""

    def _tts_provider_kind(
        self,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        *,
        voice_language: str = "",
    ) -> str:
        pieces: list[str] = []
        if tts_provider is not None:
            pieces.extend([
                tts_provider.__class__.__name__,
                str(getattr(tts_provider, "name", "") or ""),
                str(getattr(tts_provider, "provider_type", "") or ""),
            ])
        if provider_settings:
            for key in ("type", "provider", "provider_id", "api_base", "model", "name"):
                pieces.append(str(provider_settings.get(key, "") or ""))
        text = " ".join(pieces).lower()
        if "fish" in text:
            model = self._tts_fishaudio_model_for_provider(
                tts_provider,
                provider_settings,
                voice_language=voice_language,
            )
            return "fishaudio_s1" if model == "s1" else "fishaudio_s2"
        if "gsv" in text or "gptsovits" in text or "so-vits" in text:
            return "gsv"
        if "openai" in text:
            return "openai"
        if "edge" in text:
            return "edge"
        if "azure" in text:
            return "azure"
        if "gemini" in text:
            return "gemini"
        if "minimax" in text:
            return "minimax"
        if "mimo" in text:
            return "mimo_tts"
        if "aliyun" in text or "alibaba" in text or "阿里" in text:
            return "aliyun"
        if "volc" in text or "huoshan" in text or "火山" in text:
            return "volcengine"
        return "generic"

    def _tts_provider_kind_for_event(self, event: Any, *, config: dict[str, Any] | None = None) -> str:
        tts_provider = None
        provider_settings: dict[str, Any] = {}
        try:
            if config is None:
                config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
        except Exception:
            provider_settings = {}
        try:
            if event is not None:
                tts_provider = self.context.get_using_tts_provider(str(getattr(event, "unified_msg_origin", "") or ""))
        except Exception:
            tts_provider = None
        tts_provider = self._resolve_tts_synthesis_provider(event, tts_provider)
        return self._tts_provider_kind(
            tts_provider,
            provider_settings,
            voice_language=self._tts_voice_language_for_event(event),
        )

    def _tts_provider_allows_emotion_tags(self, kind: str) -> bool:
        return kind.startswith("fishaudio") or kind == "gsv"

    def _tts_emotion_tag_examples(
        self,
        provider_kind: str = "generic",
        *,
        voice_language: str = "",
    ) -> tuple[str, str]:
        if not self._tts_provider_allows_emotion_tags(provider_kind):
            return "", ""
        if provider_kind == "fishaudio_s1":
            return "(joyful)", "(sad)"
        if provider_kind.startswith("fishaudio"):
            return "[happy]", "[sad]"
        voice_lang = self._normalize_tts_voice_language_value(
            voice_language or self._tts_setting("tts_voice_language", "zh")
        ) or "zh"
        if voice_lang == "zh":
            return "[开心]", "[难过]"
        if voice_lang == "en":
            return "[happy]", "[sad]"
        return "[嬉しい]", "[悲しい]"

    def _tts_emotion_tag_rule(
        self,
        provider_kind: str = "generic",
        *,
        subject: str = "语音块内",
        voice_language: str = "",
    ) -> str:
        positive, negative = self._tts_emotion_tag_examples(
            provider_kind,
            voice_language=voice_language,
        )
        if not positive or not negative:
            return ""
        emotion_mode = str(self._tts_setting("tts_fishaudio_emotion_mode", "balanced") or "balanced").lower()
        if provider_kind.startswith("fishaudio") and emotion_mode == "manual":
            syntax = "英文圆括号" if provider_kind == "fishaudio_s1" else "方括号"
            return f"Fish Audio 手动模式：{subject}只保留输入中已有的合法{syntax}控制词，不要自动新增情绪或语气控制。"
        if provider_kind == "fishaudio_s1":
            base = (
                f"Fish Audio S1 在{subject}只使用官方英文圆括号控制标记，如 {positive}、{negative}、"
                "(whispering)、(sighing)；句级情绪放在句首，每句只选一个主要情绪，避免冲突和滥用。"
            )
            if emotion_mode == "expressive":
                return base + "情绪明确时可再组合语气或音效，总数最多 3 个。"
            return base + "仅在情绪明确时使用 1 个主要情绪，必要时再加 1 个语气控制。"
        if provider_kind.startswith("fishaudio"):
            base = (
                f"Fish Audio S2 在{subject}使用简短方括号自然语言控制，如 {positive}、{negative}；"
                "控制词优先使用朗读语言，并紧贴放在实际生效的短语前。日语可参考官方写法："
                "あれ？[くすくす笑い]知らなかった？私が[強調]胡桃だよ！[興奮]これからも頑張るね。"
                "同一位置只放一个标签，不要在句首连续堆叠多个标签。停顿词、拖音以及“唔、呜、うーん”"
                "只是口语表达，不代表叹气或喘息；原文没有明确的叹气动作时不要使用 [sighing]，"
                "不要自动使用喘息、喘气、呼吸急促、呻吟、panting、breathing 或 groaning。"
            )
            if emotion_mode == "expressive":
                return base + "可随句意在不同短语前稀疏切换表现，但每个短语只选最贴切的一种控制，中性短句不要硬加标签。"
            return base + "本模式仅在情绪明确时使用控制；一条短回复通常只需 1 个，中性短句不要硬加标签。"
        return f"可以在{subject}插入方括号情绪标签，如 {positive}、{negative}。"

    def _tts_language_label(self, event: Any = None, *, voice_language: str = "") -> str:
        language = self._normalize_tts_voice_language_value(voice_language)
        if not language:
            language = self._tts_voice_language_for_event(event)
        return {"ja": "日语", "zh": "中文", "en": "英语"}.get(language, "中文")

    def _normalize_tts_voice_language_value(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        compact = re.sub(r"[\s_\\/-]+", "", text)
        aliases = {
            "ja": "ja",
            "jp": "ja",
            "japanese": "ja",
            "日语": "ja",
            "日語": "ja",
            "日文": "ja",
            "日本语": "ja",
            "日本語": "ja",
            "zh": "zh",
            "cn": "zh",
            "chinese": "zh",
            "中文": "zh",
            "汉语": "zh",
            "漢語": "zh",
            "汉文": "zh",
            "普通话": "zh",
            "国语": "zh",
            "國語": "zh",
            "中国语": "zh",
            "中国語": "zh",
            "en": "en",
            "eng": "en",
            "english": "en",
            "英语": "en",
            "英語": "en",
            "英文": "en",
        }
        return aliases.get(compact, "")

    def _tts_voice_language_for_event(self, event: Any = None) -> str:
        turn_language = self._normalize_tts_voice_language_value(
            getattr(event, "_private_companion_tts_voice_language", "")
            if event is not None
            else ""
        )
        if turn_language:
            return turn_language
        runtime_settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(runtime_settings, dict):
            runtime_language = self._normalize_tts_voice_language_value(runtime_settings.get("tts_voice_language"))
            if runtime_language:
                return runtime_language
        return self._normalize_tts_voice_language_value(
            self._tts_setting("tts_voice_language", "zh")
        ) or "zh"

    def _detect_turn_tts_voice_language(self, event: Any) -> tuple[str, str]:
        """Recognize an explicit reply-language request without changing saved settings."""
        raw_text = str(getattr(event, "message_str", "") or "").strip()
        if not raw_text or re.search(r"(?:陪伴\s*)?TTS\s*语种", raw_text, flags=re.IGNORECASE):
            return "", ""
        text = re.sub(r"^(?:\s*\[At:\d+\]\s*)+", "", raw_text, flags=re.IGNORECASE)
        text = re.sub(r"^@\S+\s+", "", text).strip().lower()
        compact = re.sub(r"[\s，,。！？!?、；;：:~～]+", "", text)
        if not compact:
            return "", ""
        if re.search(r"(?:不要|别|不用|不必|禁止|取消|停止).{0,6}(?:用|说|讲|回复|回答|朗读|念|读)", compact):
            return "", ""
        if re.search(r"(?:怎么|如何).{0,4}(?:说|表达|翻译)|(?:翻译|译).{0,3}(?:成|为)", compact):
            return "", ""

        language_tokens = {
            "ja": r"(?:日语|日語|日文|日本语|日本語|japanese)",
            "zh": r"(?:中文|汉语|漢語|普通话|国语|國語|中国语|中国語|chinese)",
            "en": r"(?:英语|英語|英文|english)",
        }
        request_before = r"(?:这次|这回|本次|这一条|这一句|接下来)?(?:请|麻烦|可以|能不能|能否|改成|换成|改用|切成|来|直接)?(?:用|说|讲|回复|回答|回我|朗读|念|读)"
        request_after = r"(?:说|讲|回复|回答|回我|朗读|念|读|来一句|来一段|语音回复|语音回答)"
        for language, token in language_tokens.items():
            patterns = (
                rf"{request_before}.{{0,5}}{token}",
                rf"{token}.{{0,5}}{request_after}",
                rf"(?:来|说|讲|回|回复|回答|念|读)(?:一句|一段|一下)?{token}",
            )
            for pattern in patterns:
                match = re.search(pattern, compact, flags=re.IGNORECASE)
                if match:
                    return language, _single_line(match.group(0), 80)

        english_patterns = {
            "ja": r"(?:speak|reply|answer|say|read)(?:it)?(?:in)?(?:japanese)|in(?:japanese)(?:please)?",
            "zh": r"(?:speak|reply|answer|say|read)(?:it)?(?:in)?(?:chinese)|in(?:chinese)(?:please)?",
            "en": r"(?:speak|reply|answer|say|read)(?:it)?(?:in)?(?:english)|in(?:english)(?:please)?",
        }
        for language, pattern in english_patterns.items():
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return language, _single_line(match.group(0), 80)
        japanese_patterns = {
            "ja": r"(?:日本語|日語)で(?:話して|答えて|返事して|読んで|お願い)",
            "zh": r"(?:中国語|中文)で(?:話して|答えて|返事して|読んで|お願い)",
            "en": r"英語で(?:話して|答えて|返事して|読んで|お願い)",
        }
        for language, pattern in japanese_patterns.items():
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return language, _single_line(match.group(0), 80)
        return "", ""

    def _ensure_turn_tts_voice_language(self, event: Any) -> str:
        existing = self._normalize_tts_voice_language_value(
            getattr(event, "_private_companion_tts_voice_language", "")
            if event is not None
            else ""
        )
        if existing or event is None:
            return existing
        language, matched = self._detect_turn_tts_voice_language(event)
        if not language:
            return ""
        try:
            setattr(event, "_private_companion_tts_voice_language", language)
            setattr(event, "_private_companion_tts_voice_language_match", matched)
        except Exception:
            return ""
        logger.info(
            "已识别本轮 TTS 语种要求: session=%s language=%s match=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            language,
            matched,
        )
        return language

    def _apply_tts_runtime_overrides(self) -> None:
        settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(settings, dict):
            return
        lang = self._normalize_tts_voice_language_value(settings.get("tts_voice_language"))
        if not lang:
            lang = self._normalize_tts_voice_language_value(self._tts_setting("tts_voice_language", "zh"))

    def _format_tts_voice_language_status(self) -> str:
        settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        override = ""
        if isinstance(settings, dict):
            override = self._normalize_tts_voice_language_value(settings.get("tts_voice_language"))
        source = "指令覆盖" if override else "配置页"
        return f"当前 TTS 语音语种：{self._tts_language_label()}（来源：{source}）。可用：日语 / 中文 / 英语；发送“陪伴 TTS语种 默认”可恢复配置页设置。"

    def _set_tts_voice_language_from_command(self, value: str) -> str:
        text = str(value or "").strip()
        if not text or text in {"查看", "状态", "当前"}:
            return self._format_tts_voice_language_status()
        settings = self.data.setdefault("runtime_settings", {})
        if not isinstance(settings, dict):
            settings = {}
            self.data["runtime_settings"] = settings
        if text.lower() in {"default", "config", "reset", "clear"} or text in {"默认", "配置", "配置页", "重置", "清除", "跟随配置"}:
            settings.pop("tts_voice_language", None)
            if not bool(getattr(self, "enable_multi_persona_mode", False)) and hasattr(self, "tts_voice_language"):
                config_value = ""
                config = getattr(self, "config", {})
                if isinstance(config, dict):
                    config_value = config.get("tts_voice_language", "")
                    for group in config.values():
                        if isinstance(group, dict) and "tts_voice_language" in group:
                            config_value = group["tts_voice_language"]
                            break
                self.tts_voice_language = self._normalize_tts_voice_language_value(
                    config_value or self._tts_setting("tts_voice_language", "zh")
                ) or "zh"
            self._save_data_sync(sections={"runtime_settings"})
            return f"已恢复 TTS 语音语种为配置页设置：{self._tts_language_label()}。"
        lang = self._normalize_tts_voice_language_value(text)
        if not lang:
            return "没认出这个 TTS 语种。可用：日语 / 中文 / 英语；例如：陪伴 TTS语种 日语。"
        settings["tts_voice_language"] = lang
        if not bool(getattr(self, "enable_multi_persona_mode", False)) and hasattr(self, "tts_voice_language"):
            self.tts_voice_language = lang
        self._save_data_sync(sections={"runtime_settings"})
        return f"已切换 TTS 语音语种：{self._tts_language_label()}。之后 <tts> 和自动语音转换会按这个语种处理。"
