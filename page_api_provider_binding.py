# -*- coding: utf-8 -*-
"""provider_binding 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 442 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
import re
from .helpers import _MISSING
from typing import Any
from urllib.parse import urlparse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiProviderBindingMixin:
    """provider_binding 域（从 PrivateCompanionPageApi 拆出）。"""


    def _roleplay_provider_role(self, provider_id: Any) -> str:
        pid = str(provider_id or "").strip()
        if not pid:
            return ""
        roles = [
            ("FAST_RESPONSE_PROVIDER_ID", getattr(self.plugin, "fast_response_provider_id", "")),
            ("COMPLEX_REASONING_PROVIDER_ID", getattr(self.plugin, "complex_reasoning_provider_id", "")),
            ("LLM_PROVIDER_ID", getattr(self.plugin, "llm_provider_id", "")),
        ]
        for role, value in roles:
            if pid == str(value or "").strip():
                return role
        return "CUSTOM_PROVIDER_ID"

    def _loads_json_object(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            raise ValueError("模型返回为空")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
            text = re.sub(r"\s*```$", "", text).strip()
        try:
            parsed = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("模型没有返回 JSON 对象")
            parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("模型返回的 JSON 不是对象")
        return parsed

    async def list_available_providers(self) -> dict[str, Any]:
        try:
            items = self._available_provider_items()
            embedding_items = await self._available_embedding_provider_items()
            tts_items = self._available_tts_provider_items()
            return self._ok(
                {
                    "items": items,
                    "total": len(items),
                    "embedding_items": embedding_items,
                    "embedding_total": len(embedding_items),
                    "tts_items": tts_items,
                    "tts_total": len(tts_items),
                }
            )
        except Exception as exc:
            logger.error(f"获取 Provider 列表失败: {exc}", exc_info=True)
            return self._exception_error("获取 Provider 列表失败")

    def _provider_settings(self) -> dict[str, str]:
        keys = [
            "FAST_RESPONSE_PROVIDER_ID",
            "COMPLEX_REASONING_PROVIDER_ID",
            "CREATIVE_MODEL_PROVIDER_ID",
            "EMBEDDING_PROVIDER_ID",
            "LLM_PROVIDER_ID",
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "DAILY_REVIEW_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PLUGIN_VISION_PROVIDER_ID",
            "READING_ARCHIVE_VISION_PROVIDER_ID",
            "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        ]
        for key in sorted(self._schema_provider_keys(public_only=True)):
            if key not in keys:
                keys.append(key)
        values = {key: self._config_get(key) for key in keys}

        def runtime_fallback(config_key: str, attr_name: str) -> None:
            # Runtime values are only a compatibility fallback for configs
            # that predate the grouped Provider key.  They must not override
            # an explicit empty value saved by the user.
            if self._config_get_raw(config_key, _MISSING) is _MISSING:
                values[config_key] = str(getattr(self.plugin, attr_name, "") or "")

        for config_key, attr_name in (
            ("FAST_RESPONSE_PROVIDER_ID", "fast_response_provider_id"),
            ("COMPLEX_REASONING_PROVIDER_ID", "complex_reasoning_provider_id"),
            ("CREATIVE_MODEL_PROVIDER_ID", "creative_model_provider_id"),
            ("PLUGIN_VISION_PROVIDER_ID", "plugin_vision_provider_id"),
            ("EMBEDDING_PROVIDER_ID", "embedding_provider_id"),
            ("READING_ARCHIVE_VISION_PROVIDER_ID", "reading_archive_vision_provider_id"),
            ("DREAM_DIARY_PROVIDER_ID", "dream_diary_provider_id"),
            ("SMART_MESSAGE_DEBOUNCE_PROVIDER_ID", "smart_message_debounce_provider_id"),
            ("SMART_SILENCE_PROVIDER_ID", "smart_silence_provider_id"),
            ("REST_WAKEUP_PROVIDER_ID", "rest_wakeup_provider_id"),
            ("PROACTIVE_PERSONA_JUDGE_PROVIDER_ID", "proactive_persona_judge_provider_id"),
            ("tts_conversion_provider_id", "tts_conversion_provider_id"),
        ):
            runtime_fallback(config_key, attr_name)
        return values

    @staticmethod
    def _normalize_provider_mode_value(value: Any) -> str:
        text = str(value or "").strip().lower()
        return "precision" if text in {"precision", "precise", "advanced", "精准", "精准配置", "分流"} else "quick"

    def _precision_bundle_from_quick(self, values: dict[str, str]) -> dict[str, str]:
        fast = self._single_line(values.get("FAST_RESPONSE_PROVIDER_ID"), 160)
        complex_model = self._single_line(values.get("COMPLEX_REASONING_PROVIDER_ID") or values.get("LLM_PROVIDER_ID"), 160)
        creative = self._single_line(values.get("CREATIVE_MODEL_PROVIDER_ID"), 160)
        return {
            "LLM_PROVIDER_ID": complex_model,
            "MAI_STYLE_PROVIDER_ID": fast or complex_model,
            "DAILY_PLAN_PROVIDER_ID": complex_model,
            "DETAIL_ENHANCEMENT_PROVIDER_ID": complex_model,
            "HISTORY_SUMMARY_PROVIDER_ID": complex_model,
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID": complex_model,
            "COMPANION_MEMORY_PROVIDER_ID": complex_model,
            "DIALOGUE_EPISODE_PROVIDER_ID": complex_model,
            "GROUP_EPISODE_PROVIDER_ID": complex_model,
            "FORWARD_MESSAGE_PROVIDER_ID": complex_model,
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": complex_model,
            "RESPONSE_REVIEW_PROVIDER_ID": fast or complex_model,
            "SMART_SILENCE_PROVIDER_ID": fast or complex_model,
            "TROUBLESHOOTING_PROVIDER_ID": complex_model,
            "DAILY_REVIEW_PROVIDER_ID": complex_model,
            "EMOTION_JUDGEMENT_PROVIDER_ID": fast or complex_model,
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": fast or complex_model,
            "REST_WAKEUP_PROVIDER_ID": fast or complex_model,
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": fast,
            "GROUP_INTERJECT_PROVIDER_ID": fast or complex_model,
            "GROUP_SLANG_PROVIDER_ID": fast or complex_model,
            "VOICE_PROMPT_PROVIDER_ID": fast or complex_model,
            "tts_conversion_provider_id": fast or complex_model,
            "NARRATION_PROVIDER_ID": fast or complex_model,
            "NEWS_PROVIDER_ID": fast or complex_model,
            "WEB_EXPLORATION_PROVIDER_ID": fast or complex_model,
            "CREATIVE_PROVIDER_ID": creative or complex_model,
            "CREATIVE_OUTLINE_PROVIDER_ID": creative or complex_model,
            "CREATIVE_REVIEW_PROVIDER_ID": creative or complex_model,
            "DREAM_DIARY_PROVIDER_ID": creative or complex_model,
            "PHOTO_PROMPT_PROVIDER_ID": creative or complex_model,
        }

    def _quick_bundle_from_precision(self, values: dict[str, str]) -> dict[str, str]:
        fast = self._single_line(
            values.get("RESPONSE_REVIEW_PROVIDER_ID")
            or values.get("SMART_MESSAGE_DEBOUNCE_PROVIDER_ID")
            or values.get("SMART_SILENCE_PROVIDER_ID")
            or values.get("MAI_STYLE_PROVIDER_ID")
            or values.get("LLM_PROVIDER_ID"),
            160,
        )
        complex_model = self._single_line(
            values.get("LLM_PROVIDER_ID")
            or values.get("DAILY_PLAN_PROVIDER_ID")
            or values.get("COMPANION_MEMORY_PROVIDER_ID")
            or values.get("MAI_STYLE_PROVIDER_ID"),
            160,
        )
        creative = self._single_line(
            values.get("CREATIVE_PROVIDER_ID")
            or values.get("DREAM_DIARY_PROVIDER_ID")
            or values.get("PHOTO_PROMPT_PROVIDER_ID")
            or complex_model,
            160,
        )
        return {
            "FAST_RESPONSE_PROVIDER_ID": fast,
            "COMPLEX_REASONING_PROVIDER_ID": complex_model,
            "CREATIVE_MODEL_PROVIDER_ID": creative,
        }

    def _expand_provider_overwrite_bundle(self, mode: str, values: dict[str, str]) -> dict[str, str]:
        merged = {key: self._single_line(value, 160) for key, value in self._provider_settings().items()}
        for key, value in values.items():
            if key in self._allowed_provider_keys():
                merged[key] = self._single_line(value, 160)
        if self._normalize_provider_mode_value(mode) == "quick":
            merged.update(self._precision_bundle_from_quick(merged))
        else:
            merged.update(self._quick_bundle_from_precision(merged))
        return {key: self._single_line(value, 160) for key, value in merged.items() if key in self._allowed_provider_keys()}

    def _available_provider_items(self) -> list[dict[str, Any]]:
        providers: list[Any] = []
        context = getattr(self.plugin, "context", None)
        get_all = getattr(context, "get_all_providers", None)
        if callable(get_all):
            try:
                providers = list(get_all() or [])
            except Exception:
                providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            inst_map = getattr(manager, "inst_map", None)
            if isinstance(inst_map, dict):
                providers = list(inst_map.values())

        using_id = ""
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            try:
                using_id = self._provider_id(get_using())
            except Exception:
                using_id = ""

        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for provider in providers:
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider),
                    "model": self._provider_model(provider),
                    "is_default": provider_id == using_id,
                }
            )
        items.sort(key=lambda item: (not item["is_default"], item["name"].lower(), item["id"].lower()))
        return items

    @staticmethod
    def _is_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    async def _available_embedding_provider_items(self) -> list[dict[str, Any]]:
        context = getattr(self.plugin, "context", None)
        providers: list[Any] = []
        get_all = getattr(context, "get_all_embedding_providers", None)
        if callable(get_all):
            try:
                resolved = get_all()
                if asyncio.iscoroutine(resolved) or hasattr(resolved, "__await__"):
                    resolved = await resolved
                providers = list(resolved.values()) if isinstance(resolved, dict) else list(resolved or [])
            except Exception:
                providers = []
        manager = getattr(context, "provider_manager", None)
        if not providers:
            providers = list(getattr(manager, "embedding_provider_insts", None) or [])
        if not providers and isinstance(getattr(manager, "inst_map", None), dict):
            providers = [
                provider
                for provider in manager.inst_map.values()
                if self._is_embedding_provider(provider)
            ]

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for provider in providers:
            if not self._is_embedding_provider(provider):
                continue
            provider_id = self._provider_id(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            items.append(
                {
                    "id": provider_id,
                    "name": self._provider_name(provider, provider_id),
                    "type": self._provider_type(provider) or "embedding",
                    "model": self._provider_model(provider),
                    "is_default": False,
                }
            )
        items.sort(key=lambda item: (item["name"].lower(), item["id"].lower()))
        return items

    async def _embedding_provider_for_test(self, provider_id: str) -> Any:
        context = getattr(self.plugin, "context", None)
        if provider_id and context is not None:
            for getter_name in ("get_embedding_provider_by_id", "get_provider_by_id"):
                getter = getattr(context, getter_name, None)
                if not callable(getter):
                    continue
                try:
                    provider = getter(provider_id)
                    if asyncio.iscoroutine(provider) or hasattr(provider, "__await__"):
                        provider = await provider
                except Exception:
                    provider = None
                if self._is_embedding_provider(provider):
                    return provider
            manager = getattr(context, "provider_manager", None)
            candidates = list(getattr(manager, "embedding_provider_insts", None) or [])
            if isinstance(getattr(manager, "inst_map", None), dict):
                candidates.extend(manager.inst_map.values())
            for provider in candidates:
                if self._provider_id(provider) == provider_id and self._is_embedding_provider(provider):
                    return provider
            return None
        resolver = getattr(self.plugin, "_reaction_embedding_provider", None)
        if callable(resolver):
            resolved = resolver()
            if asyncio.iscoroutine(resolved) or hasattr(resolved, "__await__"):
                resolved = await resolved
            if isinstance(resolved, tuple) and resolved and self._is_embedding_provider(resolved[0]):
                return resolved[0]
        manager = getattr(context, "provider_manager", None)
        for provider in list(getattr(manager, "embedding_provider_insts", None) or []):
            if self._is_embedding_provider(provider):
                return provider
        return None

    @staticmethod
    def _provider_config(provider: Any) -> Any:
        return getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}

    @classmethod
    def _provider_config_value(cls, provider: Any, *keys: str) -> str:
        config = cls._provider_config(provider)
        for key in keys:
            value = ""
            if isinstance(config, dict):
                value = str(config.get(key, "") or "")
            else:
                value = str(getattr(config, key, "") or "")
            if value:
                return value.strip()
        return ""

    @classmethod
    def _provider_id(cls, provider: Any) -> str:
        if provider is None:
            return ""
        return (
            cls._provider_config_value(provider, "id", "provider_id")
            or str(getattr(provider, "provider_id", "") or "").strip()
            or str(getattr(provider, "id", "") or "").strip()
        )

    @classmethod
    def _provider_name(cls, provider: Any, provider_id: str) -> str:
        explicit_name = (
            cls._provider_config_value(provider, "name", "display_name", "label", "title")
            or str(getattr(provider, "name", "") or "").strip()
            or str(getattr(provider, "display_name", "") or "").strip()
        )
        if explicit_name and not cls._provider_name_is_protocol(explicit_name):
            return explicit_name
        if provider_id and not cls._provider_name_is_protocol(provider_id):
            return provider_id
        inferred = cls._provider_vendor_from_config(provider)
        if inferred:
            return inferred
        protocol_name = cls._provider_config_value(provider, "provider", "type", "provider_type")
        return explicit_name or protocol_name or provider_id

    @staticmethod
    def _provider_name_is_protocol(value: Any) -> bool:
        text = str(value or "").strip().lower()
        normalized = re.sub(r"[\s_\-]+", "", text)
        return normalized in {
            "openai",
            "openai兼容",
            "openai-compatible",
            "openaicompatible",
            "compatible",
            "兼容",
            "兼容模式",
        }

    @classmethod
    def _provider_vendor_from_config(cls, provider: Any) -> str:
        source = " ".join(
            cls._provider_config_value(
                provider,
                "api_base",
                "base_url",
                "api_base_url",
                "api_url",
                "endpoint",
                "url",
                "model",
                "model_name",
                "api_model",
                "model_id",
            ).split()
        ).lower()
        if not source:
            return ""
        try:
            parsed = urlparse(source if "://" in source else f"https://{source}")
            host = parsed.netloc.lower()
        except Exception:
            host = ""
        haystack = f"{source} {host}"
        vendors = [
            ("火山引擎", ("volces.com", "volcengine", "huoshan", "火山", "doubao", "ark.cn-beijing")),
            ("DeepSeek", ("deepseek.com", "deepseek")),
            ("阿里云百炼", ("dashscope", "aliyuncs.com", "bailian", "百炼", "qwen", "tongyi")),
            ("魔搭社区", ("modelscope", "api-inference", "魔搭")),
            ("OpenRouter", ("openrouter.ai", "openrouter")),
            ("硅基流动", ("siliconflow.cn", "siliconflow")),
            ("智谱", ("bigmodel.cn", "zhipu", "glm")),
            ("月之暗面", ("moonshot.cn", "moonshot", "kimi")),
            ("Google", ("generativelanguage.googleapis.com", "googleapis.com", "gemini")),
            ("Anthropic", ("anthropic.com", "claude")),
            ("OpenAI", ("api.openai.com", "openai.com", "gpt-")),
        ]
        for label, needles in vendors:
            if any(needle in haystack for needle in needles):
                return label
        if host:
            core = host.split(":")[0]
            for prefix in ("api.", "ark.", "www."):
                if core.startswith(prefix):
                    core = core[len(prefix):]
            return core.split(".")[0] or ""
        return ""

    @classmethod
    def _provider_model(cls, provider: Any) -> str:
        configured = cls._provider_config_value(provider, "model", "model_name", "api_model", "model_id")
        if configured:
            return configured
        get_model = getattr(provider, "get_model", None)
        if callable(get_model):
            try:
                return str(get_model() or "").strip()
            except Exception:
                pass
        return str(getattr(provider, "model_name", "") or getattr(provider, "model", "") or "").strip()

    @classmethod
    def _provider_type(cls, provider: Any) -> str:
        return (
            cls._provider_config_value(provider, "type", "provider_type")
            or provider.__class__.__name__
        )
