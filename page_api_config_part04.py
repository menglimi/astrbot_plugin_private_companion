# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiConfigPart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_config.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 575 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiConfigMixin）。
"""
from __future__ import annotations

from .page_api_config_shared import logger
from .page_api_config_shared import Any
from .page_api_config_shared import CATALOG_VERSION
from .page_api_config_shared import PAGE_FONT_NAMES
from .page_api_config_shared import PAGE_THEME_NAMES
from .page_api_config_shared import TTS_RUNTIME_KEYS
from .page_api_config_shared import _normalize_timezone_name
from .page_api_config_shared import _normalize_timezone_setting
from .page_api_config_shared import build_rules
from .page_api_config_shared import current_interaction_projection
from .page_api_config_shared import deepcopy
from .page_api_config_shared import load_catalog
from .page_api_config_shared import migrate_relationship_positive_stage_cap
from .page_api_config_shared import normalize_normal_interaction_band_cap
from .page_api_config_shared import normalize_relationship_positive_stage_cap_key
from .page_api_config_shared import normalize_relationship_stage_policy
from .page_api_config_shared import normalize_relationship_stage_provider_routes
from .page_api_config_shared import normalize_scope
from .page_api_config_shared import relationship_stage_policy_json
from .page_api_config_shared import time



class PrivateCompanionPageApiConfigPart04Mixin:
    """PrivateCompanionPageApiConfigPart04Mixin（从 PrivateCompanionPageApiConfigMixin 拆出）。"""


    def _apply_config_value(self, key: str, value: Any, overrides: dict[str, Any] | None = None) -> None:
        if key == "relationship_stage_provider_routes":
            normalized = normalize_relationship_stage_provider_routes(value)
            self._set_config_value(key, normalized)
            self.plugin.relationship_stage_provider_routes = normalized
            return
        if key == "relationship_stage_policy":
            normalized = normalize_relationship_stage_policy(value)
            self._set_config_value(key, relationship_stage_policy_json(normalized))
            self.plugin.relationship_stage_policy = normalized
            return
        if key == "relationship_positive_stage_cap_key":
            old_key = normalize_relationship_positive_stage_cap_key(
                getattr(self.plugin, key, "close")
            )
            new_key = normalize_relationship_positive_stage_cap_key(value)
            self._set_config_value(key, new_key)
            self.plugin.relationship_positive_stage_cap_key = new_key
            if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                overrides["__relationship_positive_cap_change"] = (old_key, new_key)
                overrides["__relationship_profile_batch"] = True
                return
            users = self.plugin.data.get("users", {}) if isinstance(getattr(self.plugin, "data", None), dict) else {}
            touched = False
            if isinstance(users, dict):
                for user in users.values():
                    if not isinstance(user, dict):
                        continue
                    previous_cap = user.get("relationship_positive_stage_cap_key")
                    result = migrate_relationship_positive_stage_cap(
                        user,
                        old_cap_key=old_key,
                        new_cap_key=new_key,
                        now=time.time(),
                    )
                    touched = touched or previous_cap != new_key or bool(result.get("changed"))
            if touched:
                if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                    overrides["__relationship_data_changed"] = True
                else:
                    self.plugin._save_data_sync(sections={"users"})
            return
        if key == "normal_interaction_band_cap":
            previous_runtime_cap = normalize_normal_interaction_band_cap(getattr(self.plugin, key, "warm"))
            normalized = normalize_normal_interaction_band_cap(value)
            self._set_config_value(key, normalized)
            self.plugin.normal_interaction_band_cap = normalized
            if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                overrides["__relationship_interaction_cap_change"] = (
                    previous_runtime_cap,
                    normalized,
                )
                overrides["__relationship_profile_batch"] = True
                return
            users = self.plugin.data.get("users", {}) if isinstance(getattr(self.plugin, "data", None), dict) else {}
            touched = False
            if isinstance(users, dict):
                for user in users.values():
                    if not isinstance(user, dict):
                        continue
                    before = deepcopy(user.get("current_interaction"))
                    previous_cap = user.get("normal_interaction_band_cap")
                    user["current_interaction"] = current_interaction_projection(
                        before,
                        relationship_role=user.get("relationship_role"),
                        relationship_mode=user.get("relationship_mode"),
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=normalized,
                        now=time.time(),
                    )
                    user["normal_interaction_band_cap"] = normalized
                    touched = touched or previous_cap != normalized or before != user["current_interaction"]
            if touched:
                if isinstance(overrides, dict) and overrides.get("__defer_relationship_data_save"):
                    overrides["__relationship_data_changed"] = True
                else:
                    self.plugin._save_data_sync(sections={"users"})
            return
        if key in {"owner_group_relationship_projection", "owner_group_interaction_projection"}:
            normalized = self._normalize_bool_value(value)
            self._set_config_value(key, normalized)
            setattr(self.plugin, key, normalized)
            return
        self._set_config_value(key, value)
        if key == "enable_llm_streaming":
            self.plugin.enable_llm_streaming = self._normalize_bool_value(value)
            return
        if key == "enable_body_monitor_integration":
            enabled = self._normalize_bool_value(value)
            self._forward_runtime_config_effects(key, enabled, overrides)
            return
        if key == "enable_multi_persona_mode":
            enabled = self._normalize_bool_value(value)
            self._forward_runtime_config_effects(key, enabled, overrides)
            primary_getter = getattr(self.plugin, "_primary_persona_id", None)
            primary = primary_getter() if callable(primary_getter) else ""
            if primary:
                self.plugin._page_current_persona_id = primary
            return
        if key == "plugin_specific_persona_id":
            normalizer = getattr(self.plugin, "_sanitize_persona_id", None)
            normalized = normalizer(value) if callable(normalizer) else str(value or "").strip()[:96]
            if bool(getattr(self.plugin, "enable_multi_persona_mode", False)):
                current_getter = getattr(self.plugin, "_primary_persona_id", None)
                current = current_getter() if callable(current_getter) else ""
                if current and normalized != current:
                    raise ValueError("多人格模式下不能切换主人格，请先关闭多人格模式")
            self.plugin.plugin_specific_persona_id = normalized
            self.plugin._page_current_persona_id = normalized
            if normalized:
                self.plugin._multi_persona_primary_requires_configuration = False
                self.plugin._multi_persona_primary_invalid = False
                self.plugin._multi_persona_enable_requested = bool(
                    getattr(self.plugin, "_multi_persona_enable_requested", False)
                    or getattr(self.plugin, "enable_multi_persona_mode", False)
                )
            return
        if key == "multi_persona_ids":
            self.plugin.multi_persona_ids = self.plugin._configured_multi_persona_ids()
            return
        if key == "photo_reference_catalog":
            loaded = load_catalog(
                value,
                catalog_version=CATALOG_VERSION,
                preset_names=self._photo_reference_preset_names(),
            )
            self._set_config_value("photo_reference_catalog_version", CATALOG_VERSION)
            self._set_config_value("photo_reference_catalog_user_cleared", not bool(loaded.references))
            self.plugin.photo_reference_catalog = loaded.references
            self.plugin.photo_reference_catalog_version = CATALOG_VERSION
            self.plugin.photo_reference_catalog_user_cleared = not bool(loaded.references)
            self.plugin.photo_reference_catalog_read_only = loaded.read_only
            return
        if key == "external_image_api_endpoints":
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
            endpoints = normalizer(value) if callable(normalizer) else (value if isinstance(value, list) else [])
            self.plugin.external_image_api_endpoints = endpoints
            self._sync_legacy_external_image_api_config_from_endpoints(endpoints)
            return
        if key == "external_image_download_use_environment_proxy":
            self.plugin.external_image_download_use_environment_proxy = self._normalize_bool_value(value)
            return
        if key == "photo_generation_negative_prompt_mode":
            # This setting is displayed through ``runtime_persona_setting``.
            # Keep the hot-applied runtime value in sync with the persisted
            # schema value so the panel does not revert to the startup default
            # until the next plugin restart.
            normalizer = getattr(self.plugin, "_normalize_photo_generation_negative_prompt_mode", None)
            normalized = (
                normalizer(value)
                if callable(normalizer)
                else self._normalize_setting_value(key, value)
            )
            self.plugin.photo_generation_negative_prompt_mode = normalized
            return
        if key == "provider_config_mode":
            normalizer = getattr(self.plugin, "_normalize_provider_config_mode", None)
            self.plugin.provider_config_mode = (
                normalizer(value, getattr(self.plugin, "config", None))
                if callable(normalizer)
                else str(value or "quick").strip().lower()
            )
            return
        if key == "background_llm_request_max_attempts":
            self.plugin.background_llm_request_max_attempts = self.plugin._normalize_request_max_attempts(value)
            return
        if key == "model_request_max_attempts_overrides":
            self.plugin.model_request_max_attempts_overrides = self.plugin._normalize_model_request_max_attempts_overrides(value)
            return
        if key == "model_timeout_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_timeout_overrides", None)
            self.plugin.model_timeout_overrides = normalizer(value) if callable(normalizer) else {}
            return
        if key == "model_token_limit_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_token_limit_overrides", None)
            self.plugin.model_token_limit_overrides = normalizer(value) if callable(normalizer) else {}
            return
        if key == "model_fallback_overrides":
            normalizer = getattr(self.plugin, "_normalize_model_fallback_overrides", None)
            self.plugin.model_fallback_overrides = normalizer(value) if callable(normalizer) else {}
            return
        if key == "model_replacement_scope":
            self.plugin.model_replacement_scope = normalize_scope(value)
            return
        if key == "model_replacement_rules":
            rules, warnings = build_rules(value)
            self.plugin.model_replacement_rules = rules
            for warning in warnings:
                logger.warning("模型替换规则：%s", self._single_line(warning, 180))
            return
        if key == "enable_sensitive_model_replacement":
            self.plugin.enable_sensitive_model_replacement = self._normalize_bool_value(value)
            return
        if key == "sensitive_replacement_keywords":
            self.plugin.sensitive_replacement_keywords = str(value or "").strip()
            return
        if key == "environment_perception_timezone":
            previous_timezone = str(
                getattr(self.plugin, "environment_perception_timezone", "") or ""
            )
            timezone_setting = _normalize_timezone_setting(value)
            resolver = getattr(self.plugin, "_resolve_environment_perception_timezone", None)
            timezone_name = resolver(timezone_setting) if callable(resolver) else _normalize_timezone_name(timezone_setting)
            self.plugin.environment_perception_timezone_setting = timezone_setting
            self.plugin.environment_perception_timezone = timezone_name
            runtime_overrides = overrides if isinstance(overrides, dict) else {}
            runtime_overrides["__previous_environment_perception_timezone"] = previous_timezone
            self._forward_runtime_config_effects(
                key,
                timezone_setting,
                runtime_overrides,
            )
            return
        if key == "enable_deepseek_peak_replacement":
            self.plugin.enable_deepseek_peak_replacement = self._normalize_bool_value(value)
            return
        if key == "enable_llm_streaming":
            self.plugin.enable_llm_streaming = self._normalize_bool_value(value)
            return
        if key in {"deepseek_peak_windows", "deepseek_peak_timezone", "deepseek_peak_match_keywords"}:
            setattr(self.plugin, key, str(value or "").strip())
            return
        if key == "proactive_intensity_preset":
            normalizer = getattr(self.plugin, "_normalize_proactive_intensity_preset", None)
            self.plugin.proactive_intensity_preset = (
                normalizer(value)
                if callable(normalizer)
                else str(value or "off").strip().lower()
            )
            return
        if key == "max_daily_messages":
            self.plugin.max_daily_messages = max(0, self._int(value))
            self._forward_runtime_config_effects(
                key,
                self.plugin.max_daily_messages,
                overrides,
            )
            return
        if key == "page_font_family":
            text = str(value or "original").strip().lower()
            self.plugin.page_font_family = text if text in PAGE_FONT_NAMES else "original"
            return
        if key == "page_theme":
            text = str(value or "classic").strip().lower()
            self.plugin.page_theme = text if text in PAGE_THEME_NAMES else "classic"
            return
        if key == "storage_backend":
            backend = str(value or "json").strip().lower() or "json"
            self.plugin.storage_backend = backend if backend in {"json", "sqlite"} else "json"
            self._forward_runtime_config_effects(
                key,
                self.plugin.storage_backend,
                overrides,
            )
            return
        if key == "storage_sqlite_path":
            self.plugin.storage_sqlite_path = str(value or "").strip()
            self._forward_runtime_config_effects(
                key,
                self.plugin.storage_sqlite_path,
                overrides,
            )
            return
        attr_map = {
            "FAST_RESPONSE_PROVIDER_ID": "fast_response_provider_id",
            "COMPLEX_REASONING_PROVIDER_ID": "complex_reasoning_provider_id",
            "CREATIVE_MODEL_PROVIDER_ID": "creative_model_provider_id",
            "EMBEDDING_PROVIDER_ID": "embedding_provider_id",
            "LLM_PROVIDER_ID": "llm_provider_id",
            "MAI_STYLE_PROVIDER_ID": "mai_style_provider_id",
            "DAILY_PLAN_PROVIDER_ID": "daily_plan_provider_id",
            "DETAIL_ENHANCEMENT_PROVIDER_ID": "detail_enhancement_provider_id",
            "DREAM_DIARY_PROVIDER_ID": "dream_diary_provider_id",
            "CREATIVE_PROVIDER_ID": "creative_provider_id",
            "CREATIVE_OUTLINE_PROVIDER_ID": "creative_outline_provider_id",
            "CREATIVE_REVIEW_PROVIDER_ID": "creative_review_provider_id",
            "VOICE_PROMPT_PROVIDER_ID": "voice_prompt_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID": "photo_prompt_provider_id",
            "NARRATION_PROVIDER_ID": "narration_provider_id",
            "HISTORY_SUMMARY_PROVIDER_ID": "history_summary_provider_id",
            "RESPONSE_REVIEW_PROVIDER_ID": "response_review_provider_id",
            "SMART_SILENCE_PROVIDER_ID": "smart_silence_provider_id",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": "proactive_persona_judge_provider_id",
            "TROUBLESHOOTING_PROVIDER_ID": "troubleshooting_provider_id",
            "DAILY_REVIEW_PROVIDER_ID": "daily_review_provider_id",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID": "relationship_analysis_provider_id",
            "EMOTION_JUDGEMENT_PROVIDER_ID": "emotion_judgement_provider_id",
            "COMPANION_MEMORY_PROVIDER_ID": "companion_memory_provider_id",
            "DIALOGUE_EPISODE_PROVIDER_ID": "dialogue_episode_provider_id",
            "GROUP_INTERJECT_PROVIDER_ID": "group_interject_provider_id",
            "GROUP_EPISODE_PROVIDER_ID": "group_episode_provider_id",
            "GROUP_SLANG_PROVIDER_ID": "group_slang_provider_id",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": "group_followup_judge_provider_id",
            "GROUP_MEMBER_SAFETY_PROVIDER_ID": "group_member_safety_provider_id",
            "FORWARD_MESSAGE_PROVIDER_ID": "forward_message_provider_id",
            "PLUGIN_VISION_PROVIDER_ID": "plugin_vision_provider_id",
            "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID": "reaction_expression_embedding_provider_id",
            "NEWS_PROVIDER_ID": "news_provider_id",
            "WEB_EXPLORATION_PROVIDER_ID": "web_exploration_provider_id",
            "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID": "deepseek_peak_replacement_provider_id",
            "SENSITIVE_REPLACEMENT_PROVIDER_ID": "sensitive_replacement_provider_id",
            "WEB_EXPLORATION_API_BASE_URL": "web_exploration_api_base_url",
            "WEB_EXPLORATION_API_KEY": "web_exploration_api_key",
            "WEB_EXPLORATION_API_MODEL": "web_exploration_api_model",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": "smart_message_debounce_provider_id",
            "REST_WAKEUP_PROVIDER_ID": "rest_wakeup_provider_id",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "comfyui_text2img_workflow_name",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "comfyui_selfie_workflow_name",
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "external_image_download_proxy": "external_image_download_proxy",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
        }
        if key in attr_map:
            setattr(self.plugin, attr_map[key], str(value or "").strip())
            if key == "DREAM_DIARY_PROVIDER_ID":
                shared = str(value or "").strip()
                self.plugin.dream_provider_id = shared
                self.plugin.diary_provider_id = shared
            return
        if key == "group_access_mode":
            self.plugin.group_access_mode = str(value or "whitelist").lower()
            return
        if key == "group_whitelist_ids":
            self.plugin.group_whitelist_ids = list(value or [])
            return
        if key == "group_blacklist_ids":
            self.plugin.group_blacklist_ids = list(value or [])
            return
        if key == "target_user_ids":
            self.plugin.target_user_ids = self._normalize_private_target_id_list(value)
            return
        if key == "QZONE_COOKIE":
            self.plugin.qzone_cookie = str(value or "").strip()
            return
        if key == "roleplay_knowledge_source_ids":
            normalizer = getattr(self.plugin, "_normalize_roleplay_knowledge_source_ids", None)
            self.plugin.roleplay_knowledge_source_ids = normalizer(value) if callable(normalizer) else list(value or [])
            return
        reading_archive_attr_map = {
            "enable_reading_archive_integration": "enable_reading_archive_integration",
            "enable_reading_archive_boredom_read": "enable_reading_archive_boredom_read",
            "enable_reading_archive_ask_recommendation": "enable_reading_archive_ask_recommendation",
            "enable_reading_archive_vision": "enable_reading_archive_vision",
            "enable_reading_archive_page_comments": "enable_reading_archive_page_comments",
            "enable_reading_archive_rating": "enable_reading_archive_rating",
            "enable_reading_archive_preference_influence": "enable_reading_archive_preference_influence",
            "reading_archive_min_interval_hours": "reading_archive_min_interval_hours",
            "reading_archive_max_photo_count": "reading_archive_max_photo_count",
            "reading_archive_share_probability": "reading_archive_share_probability",
            "reading_archive_ask_probability": "reading_archive_ask_probability",
            "reading_archive_preference_min_ratings": "reading_archive_preference_min_ratings",
            "reading_archive_preference_max_terms": "reading_archive_preference_max_terms",
            "reading_archive_default_keywords": "reading_archive_default_keywords",
            "reading_archive_blocked_tags": "reading_archive_blocked_tags",
        }
        if key in reading_archive_attr_map:
            setattr(self.plugin, reading_archive_attr_map[key], value)
            return
        if key == "plugin_specific_persona_id":
            self.plugin.plugin_specific_persona_id = str(value or "").strip()
            self.plugin._default_persona_prompt_cache = ""
            self.plugin._default_persona_prompt_cache_persona_id = ""
            self.plugin._default_persona_prompt_cache_by_scope = {}
            return
        if key == "private_user_aliases":
            self.plugin.private_user_aliases = self.plugin._parse_private_user_aliases(value)
            if self.plugin._merge_private_user_alias_records():
                self.plugin._save_data_sync(
                    sections={"users", "private_user_alias_merge_backups"}
                )
            return
        if key == "private_user_delivery_aliases":
            self.plugin.private_user_delivery_aliases = self.plugin._parse_private_user_aliases(value)
            users = self.plugin.data.get("users", {})
            if isinstance(users, dict):
                for raw_user_id, user in users.items():
                    if isinstance(user, dict):
                        self.plugin._ensure_private_user_umo(str(raw_user_id), user)
                self.plugin._save_data_sync(sections={"users"})
            return
        if key == "worldbook_self_registration_block_words":
            parser = getattr(self.plugin, "_parse_text_list_config", None)
            if callable(parser):
                self.plugin.worldbook_self_registration_block_words = parser(value, limit=120)
            else:
                self.plugin.worldbook_self_registration_block_words = value
            return
        if key == "worldbook_self_registration_block_reply":
            reply = str(value or "").strip()
            self.plugin.worldbook_self_registration_block_reply = "这个称呼我不记。" if reply in {"这个称呼我先不记。", "你是小猪"} else reply
            return
        if key in {"group_repeat_follow_probability", "group_repeat_interrupt_probability", "group_repeat_interrupt_probability_step"}:
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key == "rest_reply_probability":
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key in {"proactive_photo_text_probability", "proactive_share_probability"}:
            raw = float(value or 0)
            setattr(self.plugin, key, max(0.0, min(1.0, raw / 100.0 if raw > 1 else raw)))
            return
        if key == "enable_tts_enhancement" or key in TTS_RUNTIME_KEYS:
            self._forward_runtime_config_effects(key, value, overrides)
            if key == "tts_generation_mode":
                # Keep the live value authoritative even when AstrBot's config wrapper
                # still exposes a stale grouped/default value during the same request.
                normalized_mode = self._normalize_setting_value("tts_generation_mode", value)
                self.plugin.tts_generation_mode = normalized_mode
            return
        if key == "enable_passive_response_review":
            normalized = self._normalize_bool_value(value)
            self.plugin.enable_passive_response_review = normalized
            self.plugin.enable_response_self_review = normalized
            return
        if key == "passive_review_mode":
            normalized = self._normalize_setting_value(key, value)
            self.plugin.passive_review_mode = normalized
            self.plugin.response_review_mode = normalized
            return
        if key in self._allowed_feature_keys():
            normalized = self._normalize_bool_value(value)
            setattr(self.plugin, key, normalized)
            self._forward_runtime_config_effects(key, normalized, overrides)
            return
        if key in self._allowed_setting_keys():
            setattr(self.plugin, key, value)

    def _allowed_feature_keys(self) -> set[str]:
        return {
            "enable_proactive_only_mode",
            "enable_auto_user_profile_creation",
            "enable_mai_style_integration",
            "enable_companion_memory",
            "enable_expression_learning",
            "enable_intent_emotion_analysis",
            "enable_response_self_review",
            "enable_passive_response_review",
            "enable_framework_error_leak_guard",
            "enable_outbound_secret_redaction",
            "enable_proactive_message_review",
            "enable_smart_silence",
            "enable_llm_proactive_message",
            "enable_llm_proactive_persona_judge",
            "enable_reaction_expression_experiment",
            "enable_maslow_motivation_experiment",
            "enable_experimental_motivation_model",
            "enable_experimental_bluetooth_wakeup",
            "enable_personality_iteration_experiment",
            "enable_daily_case_review_experiment",
            "enable_passive_topic_suppression",
            "enable_custom_relationship_stage_policy",
            "enable_relationship_stage_provider_routing",
            "enable_group_relationship_affinity",
            "enable_relationship_content_tiers",
            "enable_relationship_analysis",
            "enable_relationship_state_machine",
            "enable_emotion_simulation",
            "enable_dialogue_episode_memory",
            "enable_open_loop_tracking",
            "enable_user_habit_learning",
            "enable_food_menu_recommendation",
            "enable_personal_goals",
            "enable_humanized_states",
            "enable_health_state",
            "enable_hunger_state",
            "enable_segmented_proactive_reply",
            "enable_proactive_quote_trigger_message",
            "enable_quote_group_reply",
            "enable_quote_group_interjection",
            "enable_quote_private_proactive",
            "enable_photo_text_action",
            "inject_passive_states",
            "enable_passive_state_delta_injection",
            "enable_passive_state_continuity_anchor",
            "enable_cycle_state",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_message_debounce",
            "enable_smart_message_debounce",
            "enable_recall_enhancement",
            "enable_recall_cancel_reply",
            "enable_recall_message_cache",
            "enable_recall_transcribe_command",
            "enable_forbidden_word_recall",
            "enable_semantic_message_debounce",
            "enable_environment_perception",
            "enable_balance_awareness",
            "enable_holiday_perception",
            "enable_platform_perception",
            "enable_model_perception",
            "enable_worldview_perception",
            "enable_lunar_perception",
            "enable_solar_term_perception",
            "enable_almanac_perception",
            "enable_group_companion",
            "enable_group_social_context",
            "enable_group_member_safety",
            "enable_group_slang_learning",
            "enable_group_member_profiles",
            "enable_group_context_injection",
            "enable_group_history_injection",
            "enable_group_image_understanding",
            "enable_group_image_wakeup",
            "enable_group_injection_guard",
            "enable_group_persona_denoise",
            "enable_forward_message_adaptation",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_wakeup_question",
            "enable_group_wakeup_cold_group",
            "enable_group_high_intensity_mode",
            "enable_group_air_reply_guard",
            "enable_private_image_self_recognition",
            "enable_private_image_gif_enhancement",
            "enable_group_conversation_followup",
            "enable_group_interjection",
            "enable_group_repeat_follow",
            "group_repeat_count_distinct_users_only",
            "enable_group_topic_threads",
            "enable_group_episode_memory",
            "enable_group_interjection_feedback",
            "enable_group_slang_meanings",
            "enable_group_slang_web_search",
            "enable_group_relationship_graph",
            "enable_group_privacy_guard",
            "enable_group_third_party_portrait_guard",
            "enable_worldbook_member_recognition",
            "enable_cross_user_memory_bridge",
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_boredom_read",
            "enable_news_daily_hot_read",
            "enable_ai_daily_watch",
            "enable_external_event_self_link",
            "enable_body_monitor_integration",
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
            "enable_screen_glance_action",
            "enable_poke_action",
            "enable_voice_action",
            "enable_yesterday_screen_diary_context",
            "enable_tts_enhancement",
            "enable_creative_writing",
            "enable_creative_work_read_guard",
            "creative_hidden_mode",
            "enable_reply_interception_forward",
        }
