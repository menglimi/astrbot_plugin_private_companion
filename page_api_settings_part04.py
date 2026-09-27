# -*- coding: utf-8 -*-
"""PageSettingNormalizerPart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_settings.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 367 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PageSettingNormalizerMixin）。
"""
from __future__ import annotations

from .page_api_settings_shared import _SETTING_UNHANDLED
from typing import Any



class PageSettingNormalizerPart04Mixin:
    """PageSettingNormalizerPart04Mixin（从 PageSettingNormalizerMixin 拆出）。"""


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
