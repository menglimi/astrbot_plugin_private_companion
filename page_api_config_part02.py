# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiConfigPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_config.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 420 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiConfigMixin）。
"""
from __future__ import annotations

from .page_api_config_shared import logger
from .page_api_config_shared import Any
from .page_api_config_shared import re
from .page_api_config_shared import request
from .page_api_config_shared import runtime_persona_setting
from .page_api_config_shared import time



class PrivateCompanionPageApiConfigPart02Mixin:
    """PrivateCompanionPageApiConfigPart02Mixin（从 PrivateCompanionPageApiConfigMixin 拆出）。"""


    async def apply_setup_guide(self) -> dict[str, Any]:
        """Persist the first setup guide draft into plugin config and data."""
        payload = await request.get_json(silent=True) or {}
        draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else payload
        if not isinstance(draft, dict):
            return self._error("缺少首次配置草稿")

        def bool_value(key: str, default: bool = False) -> bool:
            if key not in draft:
                return default
            return self._normalize_bool_value(draft.get(key))

        def text_value(key: str, limit: int = 2000) -> str:
            return str(draft.get(key) or "").strip()[:limit]

        def number_value(key: str, default: Any = 0) -> Any:
            value = draft.get(key, default)
            return default if value in (None, "") else value

        raw_target_ids = draft.get("targetUserIds", draft.get("target_user_ids", []))
        if isinstance(raw_target_ids, str):
            target_ids = self._normalize_private_target_id_list(re.split(r"[\s,，;；、]+", raw_target_ids))
        else:
            target_ids = self._normalize_private_target_id_list(raw_target_ids)
        if not target_ids:
            return self._error("请先填写目标用户 ID")

        proactive_private = bool_value("proactivePrivate", True)
        proactive_group = bool_value("proactiveGroup", False)
        group_interjection = self._single_line(draft.get("groupInterjection"), 40) or "observe"
        group_wake_enhancement = proactive_group and bool_value("groupWakeEnhancement", False)
        worldbook_enabled = bool_value("worldbookEnabled", True)
        target_platform = text_value("targetPlatform", 80) or "aiocqhttp"

        settings: dict[str, Any] = {
            "provider_config_mode": "quick",
            "enable_llm_streaming": bool_value("enable_llm_streaming", False),
            "target_user_ids": target_ids,
            "target_platform": target_platform,
            "quiet_hours": text_value("quietHours", 80) or "23:00-08:30",
            "require_private_opt_in": bool_value("requirePrivateOptIn", True),
            "proactive_intensity_preset": self._single_line(draft.get("privateIntensity"), 40) or "off",
            "max_daily_messages": number_value("privateMaxDailyMessages", 8) if proactive_private else 0,
            "idle_minutes": number_value("privateIdleMinutes", 60),
            "min_interval_minutes": number_value("privateMinIntervalMinutes", 120),
            "proactive_persona_judge_send_threshold": number_value("privatePersonaJudgeThreshold", 62),
            "proactive_review_strength": self._single_line(draft.get("privateReviewStrength"), 40) or "lenient",
            "proactive_unanswered_slowdown_start": number_value("privateUnansweredSlowdownStart", 1),
            "proactive_unanswered_max_interval_multiplier": number_value("privateUnansweredMaxIntervalMultiplier", 2.2),
            "friend_unanswered_max_cooldown_hours": number_value("privateFriendUnansweredMaxCooldownHours", 60),
            "group_wakeup_direct_words": text_value("groupWakeDirectWords", 1200),
            "group_wakeup_owner_direct_words": text_value("groupWakeOwnerDirectWords", 1200),
            "group_wakeup_context_words": text_value("groupWakeContextWords", 1200),
            "group_wakeup_interest_keywords": text_value("groupWakeInterestKeywords", 1200),
            "group_wakeup_interest_probability": number_value("groupWakeInterestProbability", 18),
            "group_wakeup_question_threshold": number_value("groupWakeQuestionThreshold", 65),
            "group_wakeup_cold_group_threshold": number_value("groupWakeColdGroupThreshold", 65),
            "group_wakeup_cold_group_idle_minutes": number_value("groupWakeColdGroupIdleMinutes", 45),
            "group_wakeup_cooldown_seconds": number_value("groupWakeCooldownSeconds", 90),
            "group_wakeup_generated_keyword_limit": number_value("groupWakeGeneratedKeywordLimit", 8),
            "group_wakeup_topic_interest_max_boost": number_value("groupWakeTopicInterestMaxBoost", 50),
            "group_wakeup_debounce_pending_penalty": number_value("groupWakeDebouncePendingPenalty", 30),
            "group_wakeup_fatigue_limit": number_value("groupWakeFatigueLimit", 5),
            "group_wakeup_fatigue_decay_minutes": number_value("groupWakeFatigueDecayMinutes", 20),
            "group_wakeup_short_text_wait_seconds": number_value("groupWakeShortTextWaitSeconds", 8),
            "group_interject_min_interval_minutes": number_value("groupInterjectMinIntervalMinutes", 180),
            "group_interject_max_daily": number_value("groupInterjectMaxDaily", 2),
            "worldbook_self_registration": bool_value("worldbookSelfRegistration", True),
        }
        if text_value("worldKnowledgePersona", 5000):
            settings["schedule_persona_prompt"] = text_value("worldKnowledgePersona", 5000)
        if text_value("worldKnowledgeWorld", 5000):
            settings["schedule_worldview_prompt"] = text_value("worldKnowledgeWorld", 5000)
        if text_value("worldKnowledgeUser", 5000):
            settings["roleplay_user_profile_prompt"] = text_value("worldKnowledgeUser", 5000)
        world_knowledge_extra = text_value("worldKnowledgeExtra", 5000)
        image_hint_match = re.search(r"自我识别提示[:：]\s*(.+?)(?:\n\s*\n|$)", world_knowledge_extra, flags=re.S)
        if image_hint_match:
            settings["private_image_self_recognition_hint"] = self._multi_line(image_hint_match.group(1), 1200)
        translation_match = re.search(r"翻译词[:：]\s*(.+?)(?:\n\s*\n|$)", world_knowledge_extra, flags=re.S)
        if translation_match:
            settings["worldview_adaptation_mode"] = "custom"
            settings["worldview_adaptation_prompt"] = self._multi_line(translation_match.group(1), 2000)

        features: dict[str, bool] = {
            "enable_llm_proactive_message": proactive_private,
            "enable_llm_proactive_persona_judge": proactive_private and bool_value(
                "enable_llm_proactive_persona_judge",
                bool(getattr(self.plugin, "enable_llm_proactive_persona_judge", True)),
            ),
            "enable_passive_response_review": proactive_private and bool_value(
                "enable_passive_response_review",
                bool(getattr(self.plugin, "enable_passive_response_review", True)),
            ),
            "enable_proactive_message_review": proactive_private and bool_value(
                "enable_proactive_message_review",
                bool(getattr(self.plugin, "enable_proactive_message_review", True)),
            ),
            "enable_group_companion": proactive_group,
            "enable_group_context_injection": proactive_group,
            "enable_group_injection_guard": proactive_group,
            "enable_group_wakeup_enhancement": group_wake_enhancement,
            "enable_group_wakeup_question": group_wake_enhancement and bool_value("groupWakeQuestion", True),
            "enable_group_wakeup_cold_group": group_wake_enhancement and bool_value("groupWakeColdGroup", False),
            "enable_group_interjection": proactive_group and group_interjection == "low",
            "enable_group_interjection_feedback": proactive_group and group_interjection == "low" and bool_value("groupInterjectionFeedback", True),
            "enable_worldbook_member_recognition": worldbook_enabled,
        }

        providers = {
            key: self._single_line(draft.get(key), 160)
            for key in (
                "FAST_RESPONSE_PROVIDER_ID",
                "COMPLEX_REASONING_PROVIDER_ID",
                "CREATIVE_MODEL_PROVIDER_ID",
                "PLUGIN_VISION_PROVIDER_ID",
            )
            if self._single_line(draft.get(key), 160)
        }
        worldbook_user_id = self._normalize_worldbook_member_id(text_value("worldbookUserId", 80))
        worldbook_name = self._single_line(draft.get("worldbookNickname"), 80)
        worldbook_should_save = bool(worldbook_enabled and worldbook_user_id and (worldbook_name or text_value("worldbookContent", 2000)))
        if worldbook_should_save and not self._worldbook_setup_member_id_valid(
            worldbook_user_id,
            target_ids=target_ids,
            target_platform=target_platform,
        ):
            return self._error("关系网词条必须使用有效 QQ 号、平台身份 ID 或外部身份键")
        worldbook_aliases = [
            self._single_line(item, 40)
            for item in re.split(r"[\n,，、;；]+", text_value("worldbookAliases", 1200))
            if self._single_line(item, 40)
        ][:20]

        changed: dict[str, Any] = {}
        try:
            for key, value in features.items():
                if key in self._allowed_feature_keys():
                    changed[key] = self._normalize_bool_value(value)
            for key, value in settings.items():
                if key in self._allowed_setting_keys():
                    changed[key] = self._normalize_setting_value(key, value)
            for key, value in providers.items():
                if key in self._allowed_provider_keys():
                    changed[key] = value

            for key, value in changed.items():
                self._apply_config_value(key, value, changed)

            if providers or changed.get("provider_config_mode"):
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()

            sync_targets = getattr(self.plugin, "_sync_configured_targets", None)
            if callable(sync_targets):
                async with self.plugin._data_lock:
                    sync_targets()
                    self.plugin._save_data_sync(sections={"users"})

            worldbook_saved = False
            if worldbook_should_save:
                async with self.plugin._data_lock:
                    profiles = self.plugin.data.setdefault("worldbook_member_profiles", {})
                    if not isinstance(profiles, dict):
                        profiles = {}
                        self.plugin.data["worldbook_member_profiles"] = profiles
                    profile = profiles.get(worldbook_user_id)
                    if not isinstance(profile, dict):
                        profile = {
                            "user_id": worldbook_user_id,
                            "important_memories": [],
                            "priority": 120,
                            "source_entries": ["首次配置引导"],
                            "observed_names": [],
                        }
                        profiles[worldbook_user_id] = profile
                    profile.update(
                        {
                            "user_id": worldbook_user_id,
                            "identity_type": "qq" if worldbook_user_id.isdigit() else "external",
                            "enabled": bool_value("worldbookEnabled", True),
                            "name": worldbook_name or worldbook_user_id,
                            "gender": self._single_line(draft.get("worldbookGender"), 40),
                            "aliases": worldbook_aliases,
                            "content": text_value("worldbookContent", 2000),
                            "identity_note": text_value("worldbookIdentityNote", 2000),
                            "boundary_note": text_value("worldbookBoundaryNote", 1200),
                            "manual_edit_ts": time.time(),
                            "setup_guide_ts": time.time(),
                        }
                    )
                    deleted = self.plugin.data.setdefault("worldbook_deleted_member_ids", [])
                    if isinstance(deleted, list) and worldbook_user_id in deleted:
                        self.plugin.data["worldbook_deleted_member_ids"] = [
                            item for item in deleted if str(item) != worldbook_user_id
                        ]
                    self.plugin._save_data_sync(
                        sections={"worldbook_member_profiles", "worldbook_deleted_member_ids"}
                    )
                    worldbook_saved = True

            async with self.plugin._data_lock:
                self.plugin.data["setup_guide_completed_at"] = time.time()
                self.plugin.data["setup_guide_completed_version"] = "5.7.2-first-setup"
                self.plugin._save_data_sync(
                    sections={"setup_guide_completed_at", "setup_guide_completed_version"}
                )

            config_saved = True
            if changed:
                config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if self._is_http_error_response(overview):
                return overview
            if overview.get("success"):
                data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
                data["setup_applied"] = True
                data["setup_changed"] = changed
                data["setup_worldbook_saved"] = worldbook_saved
                data["config_saved"] = config_saved
            return overview
        except Exception as exc:
            logger.error(f"首次配置落地失败: {exc}", exc_info=True)
            return self._exception_error("首次配置落地失败")

    def _feature_flags(self) -> dict[str, bool]:
        keys = [
            "enable_proactive_only_mode",
            "enable_proactive_chat_integration",
            "enable_mai_style_integration",
            "enable_companion_memory",
            "enable_expression_learning",
            "enable_intent_emotion_analysis",
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
            "enable_screen_glance_action",
            "enable_goodnight_screen_check",
            "enable_poke_action",
            "enable_voice_action",
            "enable_photo_reference_image",
            "inject_passive_states",
            "enable_passive_state_delta_injection",
            "enable_passive_state_continuity_anchor",
            "enable_cycle_state",
            "enable_skill_growth_simulation",
            "enable_skill_growth_passive_injection",
            "enable_personal_goals",
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
            "enable_group_scene_awareness",
            "enable_group_reality_promise_guard",
            "enable_group_wakeup_enhancement",
            "enable_group_wakeup_question",
            "enable_group_wakeup_cold_group",
            "enable_group_high_intensity_mode",
            "enable_group_air_reply_guard",
            "enable_private_image_self_recognition",
            "enable_backup_external_image_api",
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
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_bilibili_integration",
            "enable_bilibili_boredom_watch",
            "enable_news_integration",
            "enable_news_daily_hot_read",
            "enable_news_boredom_read",
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
        ]
        values = {
            key: bool(
                runtime_persona_setting(
                    self.plugin,
                    key,
                    getattr(self.plugin, key, False),
                )
            )
            for key in keys
        }
        try:
            reality_api_getter = getattr(self.plugin, "_reality_companion_api", None)
            reality_api = reality_api_getter() if callable(reality_api_getter) else None
            reality_status_getter = getattr(reality_api, "status", None) if reality_api is not None else None
            reality_status = reality_status_getter() if callable(reality_status_getter) else None
            if isinstance(reality_status, dict):
                values["enable_experimental_bluetooth_wakeup"] = bool(reality_status.get("enabled"))
        except Exception:
            pass
        try:
            bilibili_available = bool(getattr(self.plugin, "_bilibili_available", lambda: False)())
        except Exception:
            bilibili_available = False
        try:
            screen_companion_available = bool(self._screen_companion_available())
        except Exception:
            screen_companion_available = False
        try:
            reading_archive_available = bool(getattr(self.plugin, "_reading_archive_available", lambda: False)())
        except Exception:
            reading_archive_available = False
        values["enable_livingmemory_integration"] = bool(getattr(self.plugin, "enable_livingmemory_integration", False))
        values["enable_bilibili_integration"] = bool(bilibili_available and getattr(self.plugin, "enable_bilibili_integration", False))
        values["enable_bilibili_boredom_watch"] = bool(bilibili_available and getattr(self.plugin, "enable_bilibili_boredom_watch", False))
        values["enable_qzone_integration"] = bool(getattr(self.plugin, "enable_qzone_integration", False))
        values["enable_qzone_life_publish"] = bool(getattr(self.plugin, "enable_qzone_life_publish", False))
        values["enable_qzone_generated_image_publish"] = bool(getattr(self.plugin, "enable_qzone_generated_image_publish", False))
        values["enable_qzone_comment_inbox"] = bool(getattr(self.plugin, "enable_qzone_comment_inbox", False))
        values["enable_qzone_emotional_vent_publish"] = bool(getattr(self.plugin, "enable_qzone_emotional_vent_publish", False))
        values["enable_yesterday_screen_diary_context"] = bool(screen_companion_available and getattr(self.plugin, "enable_yesterday_screen_diary_context", False))
        values["enable_reading_archive_integration"] = bool(
            reading_archive_available and getattr(self.plugin, "enable_reading_archive_integration", False)
        )
        values["enable_reading_archive_boredom_read"] = bool(
            reading_archive_available and getattr(self.plugin, "enable_reading_archive_boredom_read", False)
        )
        values["enable_reading_archive_ask_recommendation"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_ask_recommendation", False))
        values["enable_reading_archive_vision"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_vision", True))
        values["enable_reading_archive_page_comments"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_page_comments", True))
        values["enable_reading_archive_rating"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_rating", True))
        values["enable_reading_archive_preference_influence"] = bool(reading_archive_available and getattr(self.plugin, "enable_reading_archive_preference_influence", True))
        return values
