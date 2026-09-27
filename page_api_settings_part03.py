# -*- coding: utf-8 -*-
"""PageSettingNormalizerPart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_settings.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 340 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PageSettingNormalizerMixin）。
"""
from __future__ import annotations

import re
from .page_api_settings_shared import _SETTING_UNHANDLED
from .segmented_message import normalize_component_order
from typing import Any



class PageSettingNormalizerPart03Mixin:
    """PageSettingNormalizerPart03Mixin（从 PageSettingNormalizerMixin 拆出）。"""


    def _normalize_page_delivery_setting(self, key: str, value: Any) -> Any:
        if key == "segmented_proactive_component_order":
            return normalize_component_order(value)
        canonical_key = re.sub(
            r"^segmented_proactive_(?:private|group)_",
            "segmented_proactive_",
            key,
        )
        if canonical_key in {
            "segmented_proactive_voice_strategy",
            "segmented_proactive_image_strategy",
            "segmented_proactive_at_strategy",
            "segmented_proactive_face_strategy",
            "segmented_proactive_other_strategy",
        }:
            defaults = {
                "segmented_proactive_voice_strategy": "separate",
                "segmented_proactive_image_strategy": "separate",
                "segmented_proactive_at_strategy": "inline",
                "segmented_proactive_face_strategy": "inline",
                "segmented_proactive_other_strategy": "separate",
            }
            mode = str(value or defaults[canonical_key]).strip().lower()
            aliases = {
                "embed": "inline",
                "embedded": "inline",
                "same_message": "inline",
                "嵌入": "inline",
                "同一消息": "inline",
                "standalone": "separate",
                "separate_before": "separate",
                "separate_after": "separate",
                "单独": "separate",
                "独立": "separate",
                "follow_previous": "previous",
                "跟随上段": "previous",
                "follow_next": "next",
                "跟随下段": "next",
                "接下文": "next",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"inline", "separate", "previous", "next"} else defaults[canonical_key]
        if canonical_key == "segmented_proactive_split_mode":
            mode = str(value or "regex").strip().lower()
            return mode if mode in {"regex", "words"} else "regex"
        if canonical_key == "segmented_proactive_scope":
            mode = str(value or "proactive_only").strip().lower()
            aliases = {
                "plugin": "proactive_only",
                "plugins": "proactive_only",
                "proactive": "proactive_only",
                "插件": "proactive_only",
                "插件主动": "proactive_only",
                "all": "all_llm",
                "llm": "all_llm",
                "全部": "all_llm",
                "全部分段": "all_llm",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"proactive_only", "all_llm"} else "proactive_only"
        if canonical_key == "segmented_proactive_chat_scope":
            mode = str(value or "all").strip().lower()
            aliases = {
                "全部": "all",
                "all_chat": "all",
                "both": "all",
                "私聊": "private",
                "仅私聊": "private",
                "private_only": "private",
                "群聊": "group",
                "仅群聊": "group",
                "group_only": "group",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"all", "private", "group"} else "all"
        if canonical_key == "segmented_proactive_interval_method":
            mode = str(value or "log").strip().lower()
            return mode if mode in {"log", "random"} else "log"
        if canonical_key == "segmented_proactive_content_cleanup_scope":
            mode = str(value or "all").strip().lower()
            return mode if mode in {"all", "trailing"} else "all"
        if key in {"segmented_proactive_split_words", "segmented_proactive_content_cleanup_words"}:
            def _decode_segmented_word(raw: Any) -> str:
                text = str(raw or "")
                stripped = text.strip()
                lowered = stripped.lower()
                if lowered in {"<space>", "{space}", "[space]", "\\s", "\\u0020", "空格"}:
                    return " "
                if lowered in {"<newline>", "{newline}", "[newline]", "\\n", "换行"}:
                    return "\n"
                if lowered in {"<tab>", "{tab}", "[tab]", "\\t", "tab"}:
                    return "\t"
                if lowered in {"<comma>", "{comma}", "[comma]", "comma", "英文逗号"}:
                    return ","
                if lowered in {"<zh_comma>", "{zh_comma}", "[zh_comma]", "zh_comma", "中文逗号", "逗号"}:
                    return "，"
                if text and text.isspace():
                    return text[:1]
                return stripped

            if isinstance(value, list):
                words = [_decode_segmented_word(item) for item in value]
            else:
                raw_words = str(value or "")
                parts = re.split(r"\r?\n", raw_words) if ("\n" in raw_words or "\r" in raw_words) else re.split(r"[,、]+", raw_words)
                words = [_decode_segmented_word(part) for part in parts]
            words = [word for word in words if word != ""]
            return words[:80]
        if key == "segmented_proactive_content_replacements":
            if isinstance(value, list):
                rules = [item for item in value if isinstance(item, dict) or str(item or "").strip()]
            else:
                rules = [line.strip() for line in str(value or "").splitlines() if line.strip()]
            return rules[:80]
        if key in {"segmented_proactive_regex", "segmented_proactive_content_cleanup_rule"}:
            return str(value or "").strip()[:800]
        if key == "atrelay_default_relay_style":
            mode = str(value or "persona").strip()
            return mode if mode in {"persona", "soft", "original"} else "persona"
        if key == "enable_persona_voice_channels":
            return self._normalize_bool_value(value)
        if key in {
            "reply_style_prompt",
            "worldview_adaptation_prompt",
            "persona_conversation_voice_prompt",
            "persona_creative_voice_prompt",
            "persona_planning_voice_prompt",
            "persona_inner_voice_prompt",
            "persona_proactive_voice_prompt",
        }:
            return str(value or "").strip()[:1200]
        if key == "roleplay_knowledge_source_ids":
            normalizer = getattr(self.plugin, "_normalize_roleplay_knowledge_source_ids", None)
            if callable(normalizer):
                return normalizer(value)
            return []
        if key in {"schedule_persona_prompt", "schedule_worldview_prompt", "roleplay_user_profile_prompt"}:
            return str(value or "").strip()[:2000]
        return _SETTING_UNHANDLED

    def _normalize_page_companion_setting(self, key: str, value: Any) -> Any:
        if key == "relationship_boundary_vent_targets":
            raw_items = (
                value
                if isinstance(value, (list, tuple, set))
                else re.split(r"[\r\n,，、;；]+", str(value or ""))
            )
            targets: list[str] = []
            for item in raw_items:
                target = " ".join(str(item or "").split())[:24]
                if target and target not in targets:
                    targets.append(target)
            return targets[:32]
        if key == "humanized_state_intensity":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 50
        if key in {"local_photo_cpu_busy_percent", "local_photo_memory_busy_percent"}:
            try:
                return max(1, min(100, int(value)))
            except (TypeError, ValueError):
                return 85 if key == "local_photo_cpu_busy_percent" else 88
        if key == "local_photo_defer_minutes":
            try:
                return max(1, min(240, int(value)))
            except (TypeError, ValueError):
                return 30
        if key == "comfyui_photo_wait_seconds":
            try:
                return max(5, min(600, int(value)))
            except (TypeError, ValueError):
                return 90
        if key in {"external_image_api_timeout_seconds", "backup_external_image_api_timeout_seconds"}:
            try:
                return max(20, min(600, int(value)))
            except (TypeError, ValueError):
                return 180
        if key == "photo_action_max_daily":
            try:
                return max(0, min(5, int(value)))
            except (TypeError, ValueError):
                return 1
        if key == "natural_language_photo_generation_max_daily":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 2
        if key == "command_photo_generation_max_daily":
            try:
                return max(-1, min(100, int(value)))
            except (TypeError, ValueError):
                return -1
        if key in self.PERCENT_PROBABILITY_KEYS:
            try:
                raw = float(value)
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                if key == "rest_reply_probability":
                    return 18
                if key in {"tts_trigger_probability", "auto_voice_probability"}:
                    return 25
                return 20
        if key in self.INHERIT_PERCENT_PROBABILITY_KEYS:
            try:
                raw = float(value)
                if raw < 0:
                    return -1
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                return -1
        if key in {"rest_reply_llm_threshold", "group_wakeup_question_threshold", "group_wakeup_cold_group_threshold"}:
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 65
        if key == "rest_reply_awake_grace_minutes":
            try:
                return max(0, min(240, int(value)))
            except (TypeError, ValueError):
                return 30
        if key in {"busy_reply_min_delay_seconds", "busy_reply_max_delay_seconds"}:
            try:
                return max(0, min(900, int(value)))
            except (TypeError, ValueError):
                return 60 if key == "busy_reply_min_delay_seconds" else 300
        if key == "busy_reply_proactive_resume_buffer_minutes":
            try:
                return max(0, min(120, int(value)))
            except (TypeError, ValueError):
                return 10
        if key == "proactive_persona_judge_send_threshold":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 62
        if key == "proactive_intensity_preset":
            normalizer = getattr(self.plugin, "_normalize_proactive_intensity_preset", None)
            return normalizer(value) if callable(normalizer) else str(value or "off").strip().lower()
        if key in {"proactive_review_strength", "passive_review_strength"}:
            text = str(value or "lenient").strip().lower()
            aliases = {
                "宽松": "lenient",
                "标准": "balanced",
                "严格": "strict",
            }
            text = aliases.get(text, text)
            return text if text in {"lenient", "balanced", "strict"} else "lenient"
        if key in {"passive_review_mode", "proactive_review_mode", "response_review_mode"}:
            default = "full" if key == "proactive_review_mode" else "severe_only"
            text = str(value or default).strip().lower()
            return text if text in {"local_only", "severe_only", "full"} else default
        if key == "quote_skip_short_reply_chars":
            try:
                return max(0, min(120, int(value)))
            except (TypeError, ValueError):
                return 0
        if key == "rest_backlog_max_messages":
            try:
                return max(1, min(12, int(value)))
            except (TypeError, ValueError):
                return 4
        if key == "proactive_reply_context_hours":
            try:
                return max(1, min(72, int(value)))
            except (TypeError, ValueError):
                return 12
        if key == "proactive_persona_judge_cache_minutes":
            try:
                return max(5, min(720, int(value)))
            except (TypeError, ValueError):
                return 180
        if key == "proactive_persona_judge_max_daily":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 12
        if key == "enable_maslow_schedule_influence":
            return self._normalize_bool_value(value)
        if key == "enable_experimental_motivation_model":
            return self._normalize_bool_value(value)
        if key == "enable_experimental_bluetooth_wakeup":
            return self._normalize_bool_value(value)
        if key == "enable_personality_iteration_experiment":
            return self._normalize_bool_value(value)
        if key == "enable_personality_iteration_auto_tune":
            return self._normalize_bool_value(value)
        if key == "maslow_motivation_strength":
            try:
                return max(0, min(100, int(value)))
            except (TypeError, ValueError):
                return 35
        if key == "memory_companion_context_timeout_seconds":
            try:
                return max(0.2, min(6.0, float(value)))
            except (TypeError, ValueError):
                return 1.2
        if key in (
            "enable_memory_companion_emotional_drift",
            "enable_memory_companion_cross_window_emotion",
            "enable_memory_companion_dream_fragment",
            "enable_memory_companion_open_loop_search",
            "enable_memory_companion_feature_context",
        ):
            return self._normalize_bool_value(value)
        if key == "memory_companion_context_top_k":
            try:
                return max(1, min(10, int(value)))
            except (TypeError, ValueError):
                return 5
        if key == "memory_companion_context_max_chars":
            try:
                return max(240, min(1800, int(value)))
            except (TypeError, ValueError):
                return 900
        if key == "max_proactive_plan_lag_minutes":
            try:
                return max(5, min(1440, int(value)))
            except (TypeError, ValueError):
                return 180
        if key == "external_link_share_cooldown_hours":
            try:
                return max(0, min(168, int(value)))
            except (TypeError, ValueError):
                return 72
        if key == "web_exploration_min_interval_hours":
            try:
                return max(1, min(168, int(value)))
            except (TypeError, ValueError):
                return 8
        if key == "web_exploration_max_results":
            try:
                return max(3, min(20, int(value)))
            except (TypeError, ValueError):
                return 6
        if key == "qzone_life_publish_max_daily":
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                return 1
        return _SETTING_UNHANDLED
