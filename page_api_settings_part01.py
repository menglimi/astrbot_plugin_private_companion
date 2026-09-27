# -*- coding: utf-8 -*-
"""PageSettingNormalizerPart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_settings.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 305 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PageSettingNormalizerMixin）。
"""
from __future__ import annotations

import json
import re
from .companion_interaction_expression import normalize_normal_interaction_band_cap
from .constants import PAGE_FONT_NAMES, PAGE_THEME_NAMES
from .helpers import _normalize_timezone_name, _normalize_timezone_setting
from .model_routing import normalize_rule_configs, normalize_scope
from .page_api_settings_shared import _SETTING_UNHANDLED
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_policy import normalize_relationship_stage_provider_routes, relationship_stage_policy_json
from typing import Any



class PageSettingNormalizerPart01Mixin:
    """PageSettingNormalizerPart01Mixin（从 PageSettingNormalizerMixin 拆出）。"""


    def _normalize_setting_value(self, key: str, value: Any) -> Any:
        """Dispatch panel values to one focused normalization domain."""
        for normalizer in (
            self._normalize_page_core_setting,
            self._normalize_page_voice_photo_setting,
            self._normalize_page_delivery_setting,
            self._normalize_page_companion_setting,
            self._normalize_page_runtime_setting,
        ):
            normalized = normalizer(key, value)
            if normalized is not _SETTING_UNHANDLED:
                return normalized
        return self._normalize_page_schema_fallback(key, value)

    def _normalize_page_core_setting(self, key: str, value: Any) -> Any:
        if key == "relationship_stage_policy":
            return relationship_stage_policy_json(value)
        if key == "relationship_stage_provider_routes":
            return normalize_relationship_stage_provider_routes(value)
        if key == "relationship_positive_stage_cap_key":
            return normalize_relationship_positive_stage_cap_key(value)
        if key == "normal_interaction_band_cap":
            return normalize_normal_interaction_band_cap(value)
        if key == "auto_profile_platforms":
            raw_items = value if isinstance(value, (list, tuple, set)) else re.split(r"[\s,，、;；]+", str(value or ""))
            aliases = {
                "aiocqhttp": "onebot",
                "napcat": "onebot",
                "qq": "onebot",
                "qqofficial": "qq_official",
                "qqbot": "qq_official",
                "telegram_bot": "telegram",
                "telegrambot": "telegram",
                "tg": "telegram",
            }
            allowed = {"onebot", "qq_official", "telegram", "webchat", "generic"}
            normalized: list[str] = []
            for item in raw_items:
                platform = str(item or "").strip().lower().replace("-", "_").replace(" ", "")
                platform = aliases.get(platform, platform)
                if platform in allowed and platform not in normalized:
                    normalized.append(platform)
            return normalized or ["onebot", "qq_official", "telegram", "webchat", "generic"]
        if key == "default_nickname_strategy":
            strategy = str(value or "platform_display_name").strip().lower()
            return strategy if strategy in {"platform_display_name", "fixed", "user_id"} else "platform_display_name"
        if key == "default_proactive_daily_limit":
            try:
                return max(0, min(30, int(value)))
            except (TypeError, ValueError):
                return 0
        if key == "enable_body_monitor_integration":
            return self._normalize_bool_value(value)
        if key == "enable_multi_persona_mode":
            return self._normalize_bool_value(value)
        if key == "multi_persona_ids":
            raw = value if isinstance(value, (list, tuple, set)) else re.split(r"[\s,，、]+", str(value or ""))
            result = []
            for item in raw:
                pid = self.plugin._sanitize_persona_id(item)
                if pid and pid not in result:
                    result.append(pid)
            return result
        if key in {"enable_cycle_state", "enable_advanced_cycle_strategy", "advanced_cycle_link_intensity"}:
            return self._normalize_bool_value(value)
        if key == "advanced_cycle_start_offset":
            try:
                return max(0, min(180, int(value)))
            except (TypeError, ValueError):
                return 0
        if key in {
            "advanced_cycle_menstrual_days",
            "advanced_cycle_follicular_days",
            "advanced_cycle_pre_ovulation_days",
            "advanced_cycle_ovulation_days",
            "advanced_cycle_luteal_days",
            "advanced_cycle_pms_days",
        }:
            try:
                return max(1, min(30, int(value)))
            except (TypeError, ValueError):
                return 1
        if key in {
            "advanced_cycle_menstrual_energy",
            "advanced_cycle_follicular_energy",
            "advanced_cycle_pre_ovulation_energy",
            "advanced_cycle_ovulation_energy",
            "advanced_cycle_luteal_energy",
            "advanced_cycle_pms_energy",
        }:
            try:
                return max(-50, min(30, int(value)))
            except (TypeError, ValueError):
                return 0
        if key in {
            "advanced_cycle_menstrual_prompt",
            "advanced_cycle_menstrual_mood",
            "advanced_cycle_follicular_prompt",
            "advanced_cycle_follicular_mood",
            "advanced_cycle_pre_ovulation_prompt",
            "advanced_cycle_pre_ovulation_mood",
            "advanced_cycle_ovulation_prompt",
            "advanced_cycle_ovulation_mood",
            "advanced_cycle_luteal_prompt",
            "advanced_cycle_luteal_mood",
            "advanced_cycle_pms_prompt",
            "advanced_cycle_pms_mood",
        }:
            return str(value or "").strip()[:1200]
        if key in self._schema_bool_keys():
            return self._normalize_bool_value(value)
        if key == "reaction_expression_delivery_mode":
            mode = str(value or "separate_after").strip().lower()
            return (
                mode
                if mode in {"separate_after", "same_message", "separate_before"}
                else "separate_after"
            )
        if key == "reaction_expression_image_format":
            image_format = str(value or "image").strip().lower()
            return image_format if image_format in {"image", "qq_emoji"} else "image"
        expression_modes = {
            "expression_private_learning_source_mode": ({"owner", "selected", "all"}, "owner"),
            "expression_group_learning_source_mode": ({"disabled", "selected", "all"}, "disabled"),
            "expression_private_application_mode": ({"all", "selected"}, "all"),
            "expression_group_application_mode": ({"disabled", "all", "selected"}, "all"),
        }
        if key in expression_modes:
            allowed, default = expression_modes[key]
            mode = str(value or default).strip().lower()
            return mode if mode in allowed else default
        expression_id_keys = {
            "expression_private_learning_source_ids",
            "expression_group_learning_source_ids",
            "expression_private_application_user_ids",
            "expression_group_application_ids",
        }
        if key in expression_id_keys:
            ids = self._normalize_id_list(value)
            if key in {
                "expression_private_learning_source_ids",
                "expression_private_application_user_ids",
            }:
                canonicalizer = getattr(self.plugin, "_canonical_private_user_id", None)
                if callable(canonicalizer):
                    normalized_ids: list[str] = []
                    for item in ids:
                        try:
                            normalized_ids.append(self._single_line(canonicalizer(item), 80) or item)
                        except Exception:
                            normalized_ids.append(item)
                    ids = normalized_ids
            return list(dict.fromkeys(item for item in ids if item))[:500]
        if key == "environment_perception_timezone":
            return _normalize_timezone_setting(value)
        if key == "deepseek_peak_timezone":
            return _normalize_timezone_name(value)
        if key == "model_replacement_scope":
            return normalize_scope(value)
        if key == "model_replacement_rules":
            return normalize_rule_configs(value)
        if key == "sensitive_replacement_keywords":
            return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:4000]
        if key == "target_user_ids":
            return self._normalize_private_target_id_list(value)
        if key == "plugin_specific_persona_id":
            return self.plugin._sanitize_persona_id(value)
        if key == "page_font_family":
            text = str(value or "original").strip().lower()
            return text if text in PAGE_FONT_NAMES else "original"
        if key == "page_theme":
            text = str(value or "classic").strip().lower()
            return text if text in PAGE_THEME_NAMES else "classic"
        if key == "provider_config_mode":
            normalizer = getattr(self.plugin, "_normalize_provider_config_mode", None)
            if callable(normalizer):
                return normalizer(value, getattr(self.plugin, "config", None))
            text = str(value or "quick").strip().lower()
            aliases = {
                "fast": "quick",
                "simple": "quick",
                "快速": "quick",
                "快速配置": "quick",
                "precise": "precision",
                "advanced": "precision",
                "精准": "precision",
                "精准配置": "precision",
                "分流": "precision",
            }
            text = aliases.get(text, text)
            return text if text in {"quick", "precision"} else "quick"
        if key == "background_llm_request_max_attempts":
            return self.plugin._normalize_request_max_attempts(value)
        if key == "model_request_max_attempts_overrides":
            normalized = self.plugin._normalize_model_request_max_attempts_overrides(value)
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if key == "model_timeout_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_timeout_overrides", None)
            normalized = normalizer(value) if callable(normalizer) else {}
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if key == "model_token_limit_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_token_limit_overrides", None)
            normalized = normalizer(value) if callable(normalizer) else {}
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if key == "model_fallback_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_fallback_overrides", None)
            normalized = normalizer(value) if callable(normalizer) else {}
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if key == "storage_backend":
            text = str(value or "json").strip().lower()
            return text if text in {"json", "sqlite"} else "json"
        if key == "storage_sqlite_path":
            return str(value or "").strip()[:1000]
        if key == "passive_injection_position":
            normalizer = getattr(self.plugin, "_normalize_passive_injection_position", None)
            if callable(normalizer):
                return normalizer(value)
            text = str(value or "prompt").strip().lower()
            return text if text in {"auto", "prompt", "system_prompt"} else "prompt"
        if key == "rest_reply_active_windows":
            return re.sub(r"\s+", "", str(value or ""))[:160]
        if key == "quote_target_strategy":
            text = str(value or "current").strip().lower()
            aliases = {
                "当前": "current",
                "当前消息": "current",
                "触发消息": "current",
                "引用旧消息": "quoted",
                "旧消息": "quoted",
                "被引用消息": "quoted",
                "自动": "auto",
            }
            text = aliases.get(text, text)
            return text if text in {"current", "quoted", "auto"} else "current"
        if key == "group_high_intensity_merge_scope":
            text = str(value or "group").strip().lower()
            aliases = {
                "sender": "same_user",
                "same_sender": "same_user",
                "user": "same_user",
                "同一用户": "same_user",
                "同一发送者": "same_user",
                "全群": "group",
            }
            text = aliases.get(text, text)
            return text if text in {"group", "same_user"} else "group"
        if key in {"private_user_aliases", "private_user_delivery_aliases"}:
            return str(value or "").strip()[:4000]
        if key == "worldbook_config_paths":
            return str(value or "").strip()[:1000]
        if key in {"news_sources", "ai_daily_sources"}:
            return self._normalize_multiline_source_config(value, limit=4000)
        if key in {"news_hot_sources", "web_exploration_interests", "reading_archive_default_keywords", "reading_archive_blocked_tags"}:
            return str(value or "").strip()[:1200]
        if key == "WEB_EXPLORATION_API_BASE_URL":
            raw = str(value or "").strip()[:800]
            if not raw or raw.startswith(("http://", "https://")):
                return raw
            if re.match(r"^[a-z][a-z0-9+.-]*://", raw, flags=re.I):
                return raw
            local_pattern = r"^(localhost|127\.|10\.|172\.(1[6-9]|2\d|3[0-1])\.|192\.168\.|\[?::1\]?)"
            scheme = "http://" if re.match(local_pattern, raw, flags=re.I) else "https://"
            return f"{scheme}{raw}"
        if key == "WEB_EXPLORATION_API_KEY":
            return str(value or "").strip()[:800]
        if key == "WEB_EXPLORATION_API_MODEL":
            return str(value or "").strip()[:160]
        if key == "worldbook_self_registration_block_words":
            return str(value or "").strip()[:1200]
        if key == "worldbook_self_registration_block_reply":
            reply = str(value or "").strip()[:200]
            return "这个称呼我不记。" if reply in {"这个称呼我先不记。", "你是小猪"} else reply
        if key == "QZONE_COOKIE":
            return str(value or "").replace("\r", ";").replace("\n", ";").strip()[:8000]
        if key in {"group_wakeup_direct_words", "group_wakeup_owner_direct_words", "group_wakeup_context_words", "group_wakeup_interest_keywords", "recall_forbidden_words"}:
            parser = getattr(self.plugin, "_parse_text_list_config", None)
            if callable(parser):
                limit = 300 if key == "recall_forbidden_words" else 120
                try:
                    return parser(value, limit=limit)
                except TypeError:
                    return parser(value)
            if isinstance(value, list):
                limit = 300 if key == "recall_forbidden_words" else 120
                return [str(item).strip() for item in value if str(item or "").strip()][:limit]
            text = str(value or "").strip()[:1200]
            if not text:
                return []
            return [part.strip() for part in re.split(r"[\n,，、;；]+", text) if part.strip()]
        if key == "recall_forbidden_scope":
            scope = str(value or "bot_and_group").strip().lower()
            return scope if scope in {"bot_only", "group_only", "bot_and_group"} else "bot_and_group"
        if key == "private_image_self_recognition_hint":
            return str(value or "").strip()[:1200]
        if key == "private_image_vision_custom_prompt":
            return str(value or "").strip()[:12000]
        if key == "private_image_vision_max_chars":
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = 2400
            return max(300, min(12000, parsed))
        if key == "worldview_adaptation_mode":
            mode = str(value or "auto").strip()
            return mode if mode in {"auto", "modern", "fantasy", "sci_fi", "custom", "off"} else "auto"
        return _SETTING_UNHANDLED
