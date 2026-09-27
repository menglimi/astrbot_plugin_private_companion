# -*- coding: utf-8 -*-
"""Provider 配置域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（15 个方法 / 367 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import re
from .event_dispatch import EventDispatchMixin, _ON_WAITING_LLM_REQUEST
from .helpers import _flat_get, _safe_float, _safe_int, _set_into_config, _single_line
from .main_shared import _PERSONA_SETTING_MANIFEST, _multi_persona_event_context
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.entities import LLMResponse
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginProviderConfigMixin:
    """Provider 配置域（从 PrivateCompanionPlugin 拆出）。"""

    @_ON_WAITING_LLM_REQUEST(priority=110000)
    @_multi_persona_event_context
    async def route_model_replacement_before_agent_hook(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.route_model_replacement_before_agent(
            self,
            event,
            *args,
            **kwargs,
        )

    @filter.on_llm_request(priority=110000)
    @_multi_persona_event_context
    async def enforce_model_replacement_request_hook(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.enforce_model_replacement_request(
            self,
            event,
            req,
            *args,
            **kwargs,
        )

    @filter.on_llm_response(priority=-100000)
    @_multi_persona_event_context
    async def clear_model_replacement_context_hook(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.clear_model_replacement_context(
            self,
            event,
            resp,
            *args,
            **kwargs,
        )

    @staticmethod
    def _schema_runtime_default(
        key: str,
        fallback: Any,
        *,
        schema_types: frozenset[str] | None = None,
    ) -> Any:
        entry = _PERSONA_SETTING_MANIFEST.get(key)
        if (
            isinstance(entry, dict)
            and "default" in entry
            and (
                schema_types is None
                or str(entry.get("type") or "") in schema_types
            )
        ):
            return deepcopy(entry["default"])
        return deepcopy(fallback)

    @staticmethod
    def _cfg_raw(config: AstrBotConfig, key: str, default: Any = None) -> Any:
        # ``None`` is retained as the explicit missing-value probe used by
        # compatibility migrations. All ordinary public defaults come from
        # the schema manifest, not from call-site literals.
        effective = (
            default
            if default is None
            else PrivateCompanionPluginProviderConfigMixin._schema_runtime_default(key, default)
        )
        return _flat_get(config, key, effective)

    @staticmethod
    def _cfg_bool(config: AstrBotConfig, key: str, default: bool = True) -> bool:
        effective = PrivateCompanionPluginProviderConfigMixin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"bool"}),
        )
        value = _flat_get(config, key, effective)
        if isinstance(value, str):
            text = value.strip().lower()
            parsed: bool | None = None
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                parsed = True
            elif text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                parsed = False
            if parsed is not None:
                _set_into_config(config, key, parsed)
                return parsed
        return bool(value)

    @staticmethod
    def _cfg_str(config: AstrBotConfig, key: str, default: str = "", fallback: str = "") -> str:
        effective = PrivateCompanionPluginProviderConfigMixin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"string", "text"}),
        )
        return str(_flat_get(config, key, effective)).strip() or fallback

    @staticmethod
    def _cfg_int(config: AstrBotConfig, key: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
        effective = PrivateCompanionPluginProviderConfigMixin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"int"}),
        )
        return _safe_int(
            _flat_get(config, key, effective),
            effective,
            minimum,
            maximum,
        )

    @staticmethod
    def _cfg_float(
        config: AstrBotConfig,
        key: str,
        default: float,
        minimum: float = 0.0,
        maximum: float | None = None,
    ) -> float:
        effective = PrivateCompanionPluginProviderConfigMixin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"float", "int"}),
        )
        return _safe_float(
            _flat_get(config, key, effective),
            effective,
            minimum,
            maximum,
        )

    @staticmethod
    def _cfg_unit_interval(config: AstrBotConfig, key: str, default: float, minimum: float = 0.0) -> float:
        effective = PrivateCompanionPluginProviderConfigMixin._schema_runtime_default(
            key,
            default,
            schema_types=frozenset({"float", "int"}),
        )
        original = _safe_float(
            _flat_get(config, key, effective),
            effective,
            minimum,
        )
        value = original / 100.0 if original > 1.0 else original
        value = max(minimum, min(1.0, value))
        if value != original:
            _set_into_config(config, key, value)
        return value

    @staticmethod
    def _normalize_provider_config_mode(value: Any, config: Any = None) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "quick": "quick",
            "fast": "quick",
            "simple": "quick",
            "快速": "quick",
            "快速配置": "quick",
            "precision": "precision",
            "precise": "precision",
            "advanced": "precision",
            "detail": "precision",
            "detailed": "precision",
            "精准": "precision",
            "精准配置": "precision",
            "分流": "precision",
            "分流模型": "precision",
        }
        if text in aliases:
            return aliases[text]
        if text in {"quick", "precision"}:
            return text

        precision_keys = (
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
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
        )
        if any(str(_flat_get(config, key, "") or "").strip() for key in precision_keys):
            return "precision"
        return "quick"

    def _apply_quick_provider_defaults(self) -> None:
        fast = str(getattr(self, "fast_response_provider_id", "") or "").strip()
        complex_model = str(getattr(self, "complex_reasoning_provider_id", "") or "").strip()
        creative = str(getattr(self, "creative_model_provider_id", "") or "").strip()
        plugin_vision = str(getattr(self, "plugin_vision_provider_id", "") or "").strip()
        config = getattr(self, "config", None)

        def configured_provider(config_key: str, fallback: str = "") -> str:
            # Preserve an explicit empty value while tolerating older configs
            # that do not yet contain the independent vision key.
            raw = self._cfg_raw(config, config_key, None)
            return fallback if raw is None else str(raw or "").strip()

        # These routes are independent of quick/precision text assignment and
        # must be refreshed in either mode when the page saves a new value.
        for attr, config_key in (
            ("embedding_provider_id", "EMBEDDING_PROVIDER_ID"),
            ("group_member_safety_provider_id", "GROUP_MEMBER_SAFETY_PROVIDER_ID"),
            ("reaction_expression_embedding_provider_id", "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID"),
            ("deepseek_peak_replacement_provider_id", "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID"),
            ("sensitive_replacement_provider_id", "SENSITIVE_REPLACEMENT_PROVIDER_ID"),
        ):
            setattr(self, attr, self._cfg_str(config, config_key, ""))

        attr_config_keys = {
            "llm_provider_id": "LLM_PROVIDER_ID",
            "mai_style_provider_id": "MAI_STYLE_PROVIDER_ID",
            "daily_plan_provider_id": "DAILY_PLAN_PROVIDER_ID",
            "detail_enhancement_provider_id": "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "history_summary_provider_id": "HISTORY_SUMMARY_PROVIDER_ID",
            "relationship_analysis_provider_id": "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "companion_memory_provider_id": "COMPANION_MEMORY_PROVIDER_ID",
            "dialogue_episode_provider_id": "DIALOGUE_EPISODE_PROVIDER_ID",
            "group_episode_provider_id": "GROUP_EPISODE_PROVIDER_ID",
            "forward_message_provider_id": "FORWARD_MESSAGE_PROVIDER_ID",
            "proactive_persona_judge_provider_id": "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "response_review_provider_id": "RESPONSE_REVIEW_PROVIDER_ID",
            "smart_silence_provider_id": "SMART_SILENCE_PROVIDER_ID",
            "troubleshooting_provider_id": "TROUBLESHOOTING_PROVIDER_ID",
            "daily_review_provider_id": "DAILY_REVIEW_PROVIDER_ID",
            "emotion_judgement_provider_id": "EMOTION_JUDGEMENT_PROVIDER_ID",
            "smart_message_debounce_provider_id": "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "rest_wakeup_provider_id": "REST_WAKEUP_PROVIDER_ID",
            "group_followup_judge_provider_id": "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "group_interject_provider_id": "GROUP_INTERJECT_PROVIDER_ID",
            "group_slang_provider_id": "GROUP_SLANG_PROVIDER_ID",
            "voice_prompt_provider_id": "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id": "tts_conversion_provider_id",
            "narration_provider_id": "NARRATION_PROVIDER_ID",
            "news_provider_id": "NEWS_PROVIDER_ID",
            "web_exploration_provider_id": "WEB_EXPLORATION_PROVIDER_ID",
            "creative_provider_id": "CREATIVE_PROVIDER_ID",
            "creative_outline_provider_id": "CREATIVE_OUTLINE_PROVIDER_ID",
            "creative_review_provider_id": "CREATIVE_REVIEW_PROVIDER_ID",
            "dream_diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "dream_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "diary_provider_id": "DREAM_DIARY_PROVIDER_ID",
            "photo_prompt_provider_id": "PHOTO_PROMPT_PROVIDER_ID",
            "embedding_provider_id": "EMBEDDING_PROVIDER_ID",
            "group_member_safety_provider_id": "GROUP_MEMBER_SAFETY_PROVIDER_ID",
            "reaction_expression_embedding_provider_id": "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID",
            "deepseek_peak_replacement_provider_id": "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID",
            "sensitive_replacement_provider_id": "SENSITIVE_REPLACEMENT_PROVIDER_ID",
        }

        if str(getattr(self, "provider_config_mode", "quick") or "quick").strip().lower() != "quick":
            for attr, config_key in attr_config_keys.items():
                setattr(self, attr, self._cfg_str(config, config_key, ""))
            self.plugin_vision_provider_id = configured_provider("PLUGIN_VISION_PROVIDER_ID", plugin_vision)
            return

        def fill(attr: str, provider_id: str) -> None:
            setattr(self, attr, provider_id)

        fill("llm_provider_id", complex_model)
        fill("mai_style_provider_id", fast or complex_model)

        for attr in (
            "daily_plan_provider_id",
            "detail_enhancement_provider_id",
            "history_summary_provider_id",
            "relationship_analysis_provider_id",
            "companion_memory_provider_id",
            "dialogue_episode_provider_id",
            "group_episode_provider_id",
            "forward_message_provider_id",
            "proactive_persona_judge_provider_id",
            "troubleshooting_provider_id",
            "daily_review_provider_id",
        ):
            fill(attr, complex_model)

        for attr in (
            "response_review_provider_id",
            "smart_silence_provider_id",
            "emotion_judgement_provider_id",
            "smart_message_debounce_provider_id",
            "rest_wakeup_provider_id",
            "group_interject_provider_id",
            "group_slang_provider_id",
            "voice_prompt_provider_id",
            "tts_conversion_provider_id",
            "narration_provider_id",
            "news_provider_id",
            "web_exploration_provider_id",
        ):
            fill(attr, fast or complex_model)
        fill("group_followup_judge_provider_id", fast)

        for attr in (
            "creative_provider_id",
            "creative_outline_provider_id",
            "creative_review_provider_id",
            "dream_diary_provider_id",
            "dream_provider_id",
            "diary_provider_id",
            "photo_prompt_provider_id",
        ):
            fill(attr, creative or complex_model)
        self.plugin_vision_provider_id = configured_provider("PLUGIN_VISION_PROVIDER_ID", plugin_vision)

    @staticmethod
    def _parse_version_tuple(value: Any) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
        if not match:
            return None
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )

    @filter.on_agent_begin()
    @_multi_persona_event_context
    async def enforce_memo_reminder_tool_boundary(self, event: AstrMessageEvent, run_context: Any, *args, **kwargs):
        """AstrBot 会在请求钩子之后补内置工具，因此在 Agent 启动时做最终互斥。"""
        if self is None or event is None:
            return
        await self._acknowledge_official_llm_timer_trigger(event)
        self._finalize_passive_reply_tool_boundary(event)
        if self._finalize_memo_request_tool_boundary(event):
            logger.info(
                "明确便签请求已从最终工具集移除 future_task,避免重复提醒: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_agent_done()
    @_multi_persona_event_context
    async def complete_official_llm_timer_lifecycle(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        """Only finalize timer state when the cron event matches this plugin's timer id/job id."""
        if self is None or event is None:
            return
        await self._complete_official_llm_timer_event(event)
