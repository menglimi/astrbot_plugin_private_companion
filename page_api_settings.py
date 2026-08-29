# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from .companion_interaction_expression import normalize_normal_interaction_band_cap
from .constants import PAGE_FONT_NAMES, PAGE_THEME_NAMES
from .helpers import (
    _normalize_timezone_name,
    _normalize_timezone_setting,
    _path_text,
    normalize_bot_relationship_cards,
    normalize_photo_generation_scopes,
)
from .photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    normalize_photo_generation_scope_limit,
)
from .photo_reference_catalog import CatalogValidationError, validate_and_serialize
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_policy import (
    normalize_relationship_stage_provider_routes,
    relationship_stage_policy_json,
)
from .segmented_message import normalize_component_order
from .model_routing import normalize_rule_configs, normalize_scope

_SETTING_UNHANDLED = object()


class PageSettingNormalizerMixin:
    """Normalize panel setting payloads while preserving the page API contract."""
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

    def _normalize_page_voice_photo_setting(self, key: str, value: Any) -> Any:
        if key == "rest_reply_mode":
            mode = str(value or "probability").strip().lower()
            aliases = {
                "概率": "probability",
                "仅概率": "probability",
                "仅概率醒来": "probability",
                "模型": "llm",
                "模型判断": "llm",
                "模型判断是否醒来": "llm",
                "model": "llm",
                "llm_judge": "llm",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"probability", "llm"} else "probability"
        if key == "REST_WAKEUP_PROVIDER_ID":
            return str(value or "").strip()[:160]
        if key == "tts_synthesis_backend":
            mode = str(value or "astrbot_provider").strip().lower()
            aliases = {
                "astrbot": "astrbot_provider",
                "provider": "astrbot_provider",
                "official": "astrbot_provider",
                "官方": "astrbot_provider",
                "mimo": "mimo_voice_clone",
                "mimotts": "mimo_voice_clone",
                "mimo_plugin": "mimo_voice_clone",
                "插件": "mimo_voice_clone",
                "自动": "auto",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"astrbot_provider", "mimo_voice_clone", "auto"} else "astrbot_provider"
        if key == "tts_generation_mode":
            mode = str(value or "fast_tag").strip().lower()
            aliases = {
                "hybrid": "fast_tag",
                "direct": "fast_tag",
                "tag": "fast_tag",
                "fast": "fast_tag",
                "快速": "fast_tag",
                "标签": "fast_tag",
                "标签直出": "fast_tag",
                "convert": "postprocess",
                "post": "postprocess",
                "llm": "postprocess",
                "后处理": "postprocess",
                "判断翻译": "postprocess",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"fast_tag", "postprocess"} else "fast_tag"
        if key == "tts_frequency_control_mode":
            mode = str(value or "global").strip().lower()
            aliases = {
                "全局": "global",
                "全局频控": "global",
                "新版": "global",
                "旧版": "legacy",
                "旧版行为": "legacy",
                "legacy_mode": "legacy",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"global", "legacy"} else "global"
        if key == "tts_constraint_mode":
            mode = str(value or "weak").strip().lower()
            aliases = {
                "弱": "weak",
                "弱约束": "weak",
                "软": "weak",
                "软约束": "weak",
                "强": "strong",
                "强约束": "strong",
                "硬": "strong",
                "硬约束": "strong",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"weak", "strong"} else "weak"
        if key == "tts_voice_language":
            lang = str(value or "zh").strip().lower()
            return lang if lang in {"ja", "zh", "en"} else "zh"
        if key in {"tts_provider_id_zh", "tts_provider_id_ja", "tts_provider_id_en"}:
            return str(value or "").strip()[:160]
        if key == "tts_fishaudio_model":
            model = str(value or "auto").strip().lower()
            return model if model in {"auto", "s2.1-pro-free", "s2.1-pro", "s2-pro", "s1"} else "auto"
        if key == "tts_fishaudio_emotion_mode":
            mode = str(value or "balanced").strip().lower()
            return mode if mode in {"balanced", "expressive", "manual"} else "balanced"
        if key == "tts_delivery_mode":
            mode = str(value or "voice_and_text").strip().lower()
            return mode if mode in {"voice_only", "voice_and_text"} else "voice_and_text"
        if key == "tts_foreign_text_mode":
            mode = str(value or "translation").strip().lower()
            return mode if mode in {"original", "translation", "bilingual"} else "translation"
        if key == "tts_conversion_scope":
            mode = str(value or "partial").strip().lower()
            return mode if mode in {"partial", "full"} else "partial"
        if key in {"tts_extra_prompt", "main_user_mention_voice_prompt"}:
            return str(value or "").strip()[:1200]
        if key == "tts_trigger_keywords":
            return str(value or "").strip()[:2000]
        if key in {
            "natural_language_photo_extra_prompt",
            "photo_generation_fixed_prompt",
            "photo_generation_text2img_fixed_prompt",
            "photo_generation_selfie_fixed_prompt",
            "photo_generation_edit_fixed_prompt",
            "photo_generation_negative_prompt",
            "photo_generation_text2img_negative_prompt",
            "photo_generation_selfie_negative_prompt",
            "photo_generation_edit_negative_prompt",
            "photo_generation_scene_presets",
        }:
            return str(value or "").strip()[:5000]
        if key == "photo_generation_prompt_format":
            normalizer = getattr(self.plugin, "_normalize_photo_generation_prompt_format", None)
            if callable(normalizer):
                return normalizer(value)
            mode = str(value or "traditional").strip().lower().replace("-", "_")
            if mode in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion"}:
                return "nai"
            return "natural_language" if mode in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"} else "traditional"
        if key == "photo_generation_negative_prompt_mode":
            normalizer = getattr(self.plugin, "_normalize_photo_generation_negative_prompt_mode", None)
            if callable(normalizer):
                return normalizer(value)
            mode = str(value or "safe_default").strip().lower().replace("-", "_")
            aliases = {
                "合并": "merge",
                "合并自定义": "merge",
                "替换": "replace",
                "完全替换": "replace",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"safe_default", "merge", "replace"} else "safe_default"
        if key == "natural_language_photo_generation_mode":
            mode = str(value or "tool_first").strip().lower()
            aliases = {
                "tool": "tool_first",
                "工具": "tool_first",
                "工具优先": "tool_first",
                "规则": "rule_fast",
                "规则快判": "rule_fast",
                "快判": "rule_fast",
                "关闭": "off",
                "关": "off",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"tool_first", "rule_fast", "off"} else "tool_first"
        if key == "tts_conversion_provider_id":
            return str(value or "").strip()[:160]
        if key == "tts_session_min_interval_seconds":
            try:
                return max(0.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return 90.0
        if key in {"tts_private_min_interval_seconds", "tts_group_min_interval_seconds"}:
            try:
                return max(-1.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return -1.0
        if key == "main_user_mention_voice_keywords":
            return str(value or "").strip()[:1200]
        if key == "forward_message_mode":
            mode = str(value or "inject").strip().lower()
            if mode in {"注入", "injection"}:
                return "inject"
            if mode in {"转述", "summary", "summarize", "narrate", "relay"}:
                return "transcribe"
            return mode if mode in {"inject", "transcribe"} else "inject"
        if key == "photo_generation_backend":
            mode = str(value or "auto").strip().lower()
            return mode if mode in {"auto", "comfyui", "sdgen", "external", "tool_call", "nai"} else "auto"
        if key == "photo_generation_allowed_scopes":
            return normalize_photo_generation_scopes(value)
        if key in {"photo_structured_reference_assets", "owned_reaction_assets"}:
            raw_items = value
            if isinstance(raw_items, str):
                try:
                    raw_items = json.loads(raw_items or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_items = []
            if not isinstance(raw_items, list):
                return []
            limit = 16 if key == "photo_structured_reference_assets" else 96
            return [deepcopy(item) for item in raw_items if isinstance(item, dict)][:limit]
        if key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values():
            return normalize_photo_generation_scope_limit(value)
        if key == "photo_reference_catalog":
            raw_items = value
            if isinstance(value, str):
                try:
                    raw_items = json.loads(value or "[]")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CatalogValidationError({"photo_reference_catalog": ["目录必须是 JSON 数组"]}) from exc
            if not isinstance(raw_items, list):
                raise CatalogValidationError({"photo_reference_catalog": ["目录必须是数组"]})
            return validate_and_serialize(raw_items, preset_names=self._photo_reference_preset_names())
        if key == "bot_relationship_cards":
            normalizer = getattr(self.plugin, "_normalize_bot_relationship_cards", None)
            if callable(normalizer):
                return normalizer(value)
            return normalize_bot_relationship_cards(value)
        if key == "photo_reference_library":
            return self._normalize_photo_reference_library(value)
        if key == "external_image_api_endpoints":
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
            return normalizer(value) if callable(normalizer) else (value if isinstance(value, list) else [])
        if key in {"external_image_api_platform", "backup_external_image_api_platform"}:
            mode = str(value or "auto").strip().lower()
            aliases = {
                "openai兼容": "openai",
                "openai-compatible": "openai",
                "openrouter": "openrouter",
                "open-router": "openrouter",
                "open_router": "openrouter",
                "openrouter.ai": "openrouter",
                "agnes": "agnes",
                "agnes-ai": "agnes",
                "agnes_ai": "agnes",
                "sapiens": "agnes",
                "百炼": "bailian",
                "阿里云百炼": "bailian",
                "dashscope": "bailian",
                "modelscope": "modelscope",
                "model_scope": "modelscope",
                "魔搭": "modelscope",
                "魔搭社区": "modelscope",
                "api-inference": "modelscope",
                "doubao": "doubao",
                "豆包": "doubao",
                "火山": "doubao",
                "火山引擎": "doubao",
                "seedream": "doubao",
                "volcengine": "doubao",
                "gemini": "gemini",
                "google": "gemini",
                "谷歌": "gemini",
                "generativelanguage": "gemini",
                "sensenova": "sensenova",
                "sense-nova": "sensenova",
                "日日新": "sensenova",
                "minimax": "minimax",
                "minimaxi": "minimax",
                "minimax-ai": "minimax",
                "minimax_ai": "minimax",
                "海螺": "minimax",
                "海螺ai": "minimax",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"auto", "openai", "openrouter", "agnes", "sensenova", "bailian", "modelscope", "doubao", "gemini", "minimax"} else "auto"
        return _SETTING_UNHANDLED

    def _normalize_photo_reference_library(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            raw_items = []
            parsed_array = False
            if raw_text.startswith("[") and raw_text.endswith("]"):
                try:
                    parsed_items = json.loads(raw_text)
                    if isinstance(parsed_items, list):
                        raw_items = parsed_items
                        parsed_array = True
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            if not parsed_array and raw_text:
                raw_items = raw_text.split("\n")
        items: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                item = dict(raw_item)
            else:
                text = str(raw_item or "").strip()
                if not text:
                    continue
                item = {}
                if text.startswith("{") and text.endswith("}"):
                    try:
                        parsed_item = json.loads(text)
                        if isinstance(parsed_item, dict):
                            item = dict(parsed_item)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                if not item:
                    parts = re.split(r"\s*(?:\|\||｜｜)\s*", text, maxsplit=2)
                    item = {
                        "path": parts[0] if parts else "",
                        "note": parts[1] if len(parts) > 1 else "",
                    }
                    if len(parts) > 2:
                        metadata_text = str(parts[2] or "").strip()
                        if metadata_text.startswith("{"):
                            try:
                                metadata = json.loads(metadata_text)
                                if isinstance(metadata, dict):
                                    item.update(
                                        {
                                            name: field_value
                                            for name, field_value in metadata.items()
                                            if name not in {"source", "path", "url", "note", "description"}
                                        }
                                    )
                                else:
                                    item["note"] = f"{item['note']} || {metadata_text}".strip(" |")
                            except (TypeError, ValueError, json.JSONDecodeError):
                                item["note"] = f"{item['note']} || {metadata_text}".strip(" |")
                        else:
                            item["note"] = f"{item['note']} || {metadata_text}".strip(" |")

            source = _path_text(item.get("source") or item.get("path") or item.get("url"), 1000)
            if not source or source in seen_sources:
                continue
            seen_sources.add(source)
            note = str(item.get("note") or item.get("description") or "")
            note = note.replace("\r\n", "\n").replace("\r", "\n").strip()[:500]
            item["path"] = source
            item["note"] = note
            for field in ("reference_roles", "scene_categories", "time_categories"):
                if field in item and not isinstance(item.get(field), list):
                    item[field] = [
                        part
                        for part in re.split(r"[,，、/|\s]+", str(item.get(field) or ""))
                        if part
                    ]
            if "outfit_lock_default" in item:
                raw_lock = item.get("outfit_lock_default")
                if raw_lock is None or (isinstance(raw_lock, str) and not raw_lock.strip()):
                    item.pop("outfit_lock_default", None)
                else:
                    item["outfit_lock_default"] = self._normalize_bool_value(raw_lock)
            items.append(item)
        return items[:24]

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

    def _normalize_page_runtime_setting(self, key: str, value: Any) -> Any:
        if key in {
            "check_interval_seconds",
            "daily_token_limit",
            "daily_token_soft_limit",
            "rest_reply_llm_threshold",
            "idle_minutes",
            "min_interval_minutes",
            "max_daily_messages",
            "proactive_burst_max_messages",
            "proactive_burst_gap_min_seconds",
            "proactive_burst_gap_max_seconds",
            "segmented_proactive_threshold",
            "segmented_proactive_min_segment_chars",
            "segmented_proactive_max_segments",
            "segmented_proactive_private_threshold",
            "segmented_proactive_private_min_segment_chars",
            "segmented_proactive_private_max_segments",
            "segmented_proactive_group_threshold",
            "segmented_proactive_group_min_segment_chars",
            "segmented_proactive_group_max_segments",
            "group_conversation_followup_seconds",
            "group_conversation_followup_max_turns",
            "group_interject_min_interval_minutes",
            "group_interject_max_daily",
            "group_scene_recent_limit",
            "group_scene_recent_max_chars",
            "group_wakeup_cooldown_seconds",
            "group_wakeup_cold_group_idle_minutes",
            "group_wakeup_generated_keyword_limit",
            "group_wakeup_topic_interest_max_boost",
            "group_wakeup_debounce_pending_penalty",
            "group_wakeup_fatigue_limit",
            "group_wakeup_fatigue_decay_minutes",
            "group_wakeup_log_limit",
            "group_high_intensity_wakeup_window_seconds",
            "group_high_intensity_wakeup_threshold",
            "group_high_intensity_cooldown_seconds",
            "group_high_intensity_merge_seconds",
            "group_high_intensity_max_merge_messages",
            "photo_action_max_daily",
            "comfyui_photo_wait_seconds",
            "local_photo_cpu_busy_percent",
            "local_photo_memory_busy_percent",
            "local_photo_defer_minutes",
            "external_image_api_timeout_seconds",
            "forward_message_max_messages",
            "forward_message_max_chars",
            "forward_message_image_limit",
            "max_group_recent_messages",
            "max_group_slang_terms",
            "memory_refresh_interval_minutes",
            "episode_memory_refresh_messages",
            "episode_memory_refresh_minutes",
            "max_companion_memory_items",
            "max_learned_expression_items",
            "max_dialogue_episodes",
            "user_habit_min_count",
            "user_habit_max_items",
            "emotional_gate_hurt_threshold",
            "emotional_gate_refuse_threshold",
            "emotional_gate_recovery_per_hour",
            "emotional_gate_max_hurt_minutes",
            "bilibili_boredom_min_interval_hours",
            "bilibili_share_min_score",
            "news_min_interval_hours",
            "news_max_items_per_source",
            "news_hot_max_items",
            "external_event_self_link_cooldown_hours",
            "external_link_share_cooldown_hours",
            "qzone_life_publish_min_interval_hours",
            "qzone_life_publish_max_daily",
            "qzone_life_publish_intra_day_gap_minutes",
            "qzone_life_publish_similarity_threshold",
            "qzone_emotional_vent_threshold",
            "qzone_emotional_vent_cooldown_hours",
            "reading_archive_min_interval_hours",
            "reading_archive_max_photo_count",
            "reading_archive_preference_min_ratings",
            "reading_archive_preference_max_terms",
            "unanswered_screen_peek_after_minutes",
            "unanswered_screen_peek_cooldown_minutes",
            "goodnight_screen_check_delay_minutes",
            "creative_chars_per_session",
            "creative_max_active_projects",
            "worldbook_member_inject_limit",
            "atrelay_member_cache_minutes",
            "atrelay_multi_target_limit",
            "private_image_vision_cache_max_items",
            "context_image_caption_max_items",
            "group_image_max_images",
            "group_slang_web_search_terms",
            "group_slang_web_search_results",
            "auto_voice_max_chars",
            "auto_voice_cooldown_seconds",
        }:
            try:
                if key == "group_high_intensity_max_merge_messages":
                    return max(0, min(50, int(value)))
                if key == "group_image_max_images":
                    return max(0, min(12, int(value)))
                if key == "group_scene_recent_limit":
                    return max(2, min(100, int(value)))
                if key == "group_scene_recent_max_chars":
                    return max(500, min(20000, int(value)))
                parsed = max(0, int(value))
                return parsed
            except (TypeError, ValueError):
                if key == "group_high_intensity_max_merge_messages":
                    return 8
                if key == "group_scene_recent_limit":
                    return 20
                if key == "group_scene_recent_max_chars":
                    return 4000
                return 0
        if key == "group_wakeup_interest_probability":
            try:
                raw = float(value)
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                return 0
        if key == "inbound_message_debounce_seconds":
            try:
                return max(0.0, min(30.0, float(value)))
            except (TypeError, ValueError):
                return 3.0
        if key == "semantic_message_debounce_seconds":
            try:
                return max(0.0, min(15.0, float(value)))
            except (TypeError, ValueError):
                return 8.0
        if key in {"text_message_debounce_seconds", "image_message_debounce_seconds", "forward_message_debounce_seconds"}:
            try:
                return max(0.0, min(15.0, float(value)))
            except (TypeError, ValueError):
                return 0.0 if key != "image_message_debounce_seconds" else 8.0
        if key == "text_message_debounce_max_wait_seconds":
            try:
                return max(0.0, min(30.0, float(value)))
            except (TypeError, ValueError):
                return 12.0
        if key == "message_debounce_max_merge_messages":
            try:
                return max(0, min(30, int(value)))
            except (TypeError, ValueError):
                return 8
        if key in {"smart_message_debounce_model_timeout_seconds", "smart_message_debounce_wait_seconds", "smart_message_debounce_learning_window_seconds"}:
            try:
                upper = 5.0 if key == "smart_message_debounce_model_timeout_seconds" else 30.0
                lower = 0.2 if key == "smart_message_debounce_model_timeout_seconds" else 0.0
                return max(lower, min(upper, float(value)))
            except (TypeError, ValueError):
                if key == "smart_message_debounce_model_timeout_seconds":
                    return 0.8
                return 3.0 if key == "smart_message_debounce_wait_seconds" else 8.0
        if key == "smart_message_debounce_examples_limit":
            try:
                return max(0, min(30, int(value)))
            except (TypeError, ValueError):
                return 8
        if key == "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID":
            return self._single_line(value, 160)
        if key == "SMART_SILENCE_PROVIDER_ID":
            return self._single_line(value, 160)
        if key == "smart_silence_judge_mode":
            mode = str(value or "boundary_only").strip().lower()
            aliases = {
                "边界": "boundary_only",
                "明确边界": "boundary_only",
                "保守": "boundary_only",
                "上下文": "contextual",
                "模型判断": "contextual",
                "更智能": "contextual",
                "智能": "contextual",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"boundary_only", "contextual"} else "boundary_only"
        if key == "smart_silence_model_timeout_seconds":
            try:
                return max(0.2, min(5.0, float(value)))
            except (TypeError, ValueError):
                return 1.2
        if key == "private_image_vision_wait_seconds":
            try:
                return max(0.0, min(600.0, float(value)))
            except (TypeError, ValueError):
                return 30.0
        if key == "group_image_vision_wait_seconds":
            try:
                return max(0.0, min(60.0, float(value)))
            except (TypeError, ValueError):
                return 8.0
        if key == "private_image_provider_timeout_seconds":
            try:
                return max(0.0, min(600.0, float(value)))
            except (TypeError, ValueError):
                return 12.0
        if key == "private_image_provider_failure_cooldown_seconds":
            try:
                return max(0.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return 0.0
        if key == "private_image_vision_provider_priority":
            normalizer = getattr(self.plugin, "_normalize_private_image_vision_provider_priority", None)
            if callable(normalizer):
                return normalizer(value)
            normalized = str(value or "astrbot_first").strip().lower()
            return normalized if normalized in {"astrbot_first", "plugin_first", "recent_success_first"} else "astrbot_first"
        if key == "context_image_caption_timeout_seconds":
            try:
                return max(0.0, min(600.0, float(value)))
            except (TypeError, ValueError):
                return 8.0
        if key == "private_image_gif_max_frames":
            try:
                return max(1, min(8, int(value)))
            except (TypeError, ValueError):
                return 4
        if key == "group_repeat_trigger_threshold":
            try:
                return max(3, min(20, int(value)))
            except (TypeError, ValueError):
                return 4
        if key in {
            "group_repeat_follow_probability",
            "group_repeat_interrupt_probability",
            "group_repeat_interrupt_probability_step",
        }:
            try:
                raw = float(value)
                return max(0, min(100, int(round(raw * 100 if 0 <= raw <= 1 else raw))))
            except (TypeError, ValueError):
                return 0
        return _SETTING_UNHANDLED

    def _normalize_page_schema_fallback(self, key: str, value: Any) -> Any:
        if key in self.FRACTIONAL_PERCENT_SETTING_KEYS:
            return self._normalize_fractional_percent_value(value)
        if key == "skill_growth_rate":
            try:
                return max(0.1, min(3.0, float(value)))
            except (TypeError, ValueError):
                return 1.0
        if key in {
            "segmented_proactive_interval_min",
            "segmented_proactive_interval_max",
            "segmented_proactive_log_base",
            "segmented_proactive_private_interval_min",
            "segmented_proactive_private_interval_max",
            "segmented_proactive_private_log_base",
            "segmented_proactive_group_interval_min",
            "segmented_proactive_group_interval_max",
            "segmented_proactive_group_log_base",
        }:
            try:
                raw = float(value)
                if key.endswith("_log_base"):
                    return max(1.1, min(10.0, raw))
                return max(0.1, min(30.0, raw))
            except (TypeError, ValueError):
                return 1.8 if key.endswith("_log_base") else 1.5
        if key in {
            "enable_daily_token_soft_limit",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "ai_daily_prefer_text_version",
            "enable_external_event_self_link",
            "enable_web_exploration",
            "enable_web_exploration_boredom_search",
            "enable_qzone_integration",
            "enable_qzone_life_publish",
            "enable_qzone_generated_image_publish",
            "enable_qzone_comment_inbox",
            "enable_qzone_emotional_vent_publish",
            "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision",
            "enable_reading_archive_page_comments",
            "enable_reading_archive_rating",
            "enable_reading_archive_preference_influence",
            "enable_unanswered_screen_peek_followup",
            "enable_goodnight_screen_check",
            "enable_creative_writing",
            "enable_creative_work_read_guard",
            "creative_hidden_mode",
            "enable_environment_perception",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "auto_voice_enabled",
            "auto_voice_full_conversion_enabled",
            "enable_humanized_states",
            "inject_passive_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_cycle_state",
            "enable_worldbook_member_recognition",
            "enable_group_scene_awareness",
            "enable_group_history_injection",
            "intercept_astrbot_group_context",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_high_intensity_mode",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_forward_message_adaptation",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_skill_growth_schedule_influence",
            "forward_message_parse_nested",
            "forward_message_image_vision",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "recall_forbidden_word_case_sensitive",
            "enable_semantic_message_debounce",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_local_photo_load_guard",
            "enable_generated_photo_cleanup",
            "enable_private_image_self_recognition",
            "enable_context_image_captioning",
            "enable_private_image_gif_enhancement",
            "enable_private_image_vision_cache",
            "enable_group_image_understanding",
            "enable_group_image_wakeup",
            "enable_segmented_proactive_reply",
            "segmented_proactive_send_as_forward",
            "enable_segmented_proactive_content_cleanup",
            "enable_segmented_proactive_content_replacement",
            "enable_humanized_states",
            "inject_passive_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_cycle_state",
            "enable_group_conversation_followup",
            "worldbook_auto_import",
            "worldbook_member_match_aliases",
            "worldbook_self_registration",
            "enable_atrelay_tools",
            "enable_cross_user_memory_bridge",
            "atrelay_require_worldbook_first",
            "cross_user_memory_owner_only",
            "atrelay_sensitive_confirm",
            "enable_atrelay_llm_rewrite",
        }:
            return self._normalize_bool_value(value)
        schema_item = self._schema_item_for_key(key)
        if schema_item:
            return self._normalize_schema_setting_value(value, schema_item)
        return self._single_line(value, 240)
