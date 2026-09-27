# -*- coding: utf-8 -*-
"""plugin_bootstrap 拆分件 part02：插件引导初始化（机械搬移，行为不变）。

函数体逐字搬自 plugin_bootstrap.py，仅调整模块级依赖的导入来源。
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools

from .body_monitor_integration import BodyMonitorIntegration
from .bot_personal_contract import capability_descriptor, contract_self_check
from .config_migration import migrate_flat_config_into_schema_groups
from .hdsi_experiment import normalize_continuity_scope, normalize_window_modes
from .constants import (
    DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT,
    DEFAULT_REPLY_STYLE_PROMPT,
    PAGE_FONT_NAMES,
    PAGE_THEME_NAMES,
    PLUGIN_NAME,
)
from .helpers import (
    _flat_get,
    _normalize_timezone_setting,
    _set_into_config,
    _set_today_key_timezone,
    _single_line,
    normalize_photo_generation_scopes,
)
from .p5_attestation import P5AttestationRegistry
from .plugin_identity import PLUGIN_ID, PLUGIN_VERSION, plugin_identity_snapshot
from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS
from .photo_reference_catalog import load_catalog, validate_and_serialize
from .proactive_chat_runtime_bridge import ProactiveChatRuntimeBridge
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_affinity_runtime import normalize_group_allowlist
from .relationship_policy import (
    normalize_relationship_stage_policy,
    normalize_relationship_stage_provider_routes,
)
from .runtime_compat import probe_runtime_capabilities
from .migration_coordinator import MigrationCoordinator
from .wardrobe import (
    WARDROBE_MAX_ITEMS,
    WARDROBE_PROMPT_MAX_ITEMS,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
)
from .migration_outbox import MigrationOutbox
from .model_routing import DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS, build_rules, normalize_scope
from .segmented_message import normalize_component_order, normalize_component_strategy
from .story_authority import story_startup_sync_operation
from .unified_person_registry import UnifiedPersonRegistry
from .logging_util import get_module_logger
from .plugin_bootstrap_shared import logger



def _initialize_world_and_model_config(self: Any, c: Any) -> None:
    self.relationship_positive_daily_cap = self._cfg_int(c, "relationship_positive_daily_cap", 12, 0, 120)
    self.relationship_decay_grace_days = self._cfg_int(c, "relationship_decay_grace_days", 3, 0, 30)
    self.relationship_decay_early_per_day = self._cfg_int(c, "relationship_decay_early_per_day", 2, 0, 30)
    self.relationship_decay_middle_per_day = self._cfg_int(c, "relationship_decay_middle_per_day", 5, 0, 30)
    self.relationship_decay_late_per_day = self._cfg_int(c, "relationship_decay_late_per_day", 8, 0, 30)
    self.enable_environment_perception = self._cfg_bool(c, "enable_environment_perception", True)
    configured_timezone = _flat_get(c, "environment_perception_timezone", None)
    if configured_timezone in (None, ""):
        configured_timezone = _flat_get(c, "timezone", None) or "global"
    self.environment_perception_timezone_setting = _normalize_timezone_setting(configured_timezone)
    self.environment_perception_timezone = self._resolve_environment_perception_timezone(
        self.environment_perception_timezone_setting
    )
    _set_today_key_timezone(self.environment_perception_timezone)
    self.enable_holiday_perception = self._cfg_bool(c, "enable_holiday_perception", True)
    self.holiday_country = self._cfg_str(c, "holiday_country", "CN", "CN").upper()
    self.enable_platform_perception = self._cfg_bool(c, "enable_platform_perception", True)
    self.enable_model_perception = self._cfg_bool(c, "enable_model_perception", True)
    self.enable_worldview_perception = self._cfg_bool(c, "enable_worldview_perception", False)
    self.enable_lunar_perception = self._cfg_bool(c, "enable_lunar_perception", True)
    self.enable_solar_term_perception = self._cfg_bool(c, "enable_solar_term_perception", True)
    self.enable_almanac_perception = self._cfg_bool(c, "enable_almanac_perception", False)
    self.provider_config_mode = self._normalize_provider_config_mode(
        self._cfg_raw(c, "provider_config_mode", None),
        c,
    )
    self.background_llm_request_max_attempts = self._normalize_request_max_attempts(
        self._cfg_raw(c, "background_llm_request_max_attempts", 0)
    )
    self.model_request_max_attempts_overrides = self._normalize_model_request_max_attempts_overrides(
        self._cfg_raw(c, "model_request_max_attempts_overrides", {})
    )
    self.model_timeout_overrides = self._normalize_model_timeout_overrides(
        self._cfg_raw(c, "model_timeout_overrides", {})
    )
    self.model_token_limit_overrides = self._normalize_model_token_limit_overrides(
        self._cfg_raw(c, "model_token_limit_overrides", {})
    )
    self.model_fallback_overrides = self._normalize_model_fallback_overrides(
        self._cfg_raw(c, "model_fallback_overrides", {})
    )
    self.enable_llm_streaming = self._cfg_bool(c, "enable_llm_streaming", False)
    self.enable_deepseek_peak_replacement = self._cfg_bool(c, "enable_deepseek_peak_replacement", False)
    self.model_replacement_scope = normalize_scope(
        self._cfg_raw(c, "model_replacement_scope", "plugin"),
    )
    raw_model_replacement_rules = self._cfg_raw(c, "model_replacement_rules", [])
    self.model_replacement_rules, model_replacement_warnings = build_rules(raw_model_replacement_rules)
    if not self.model_replacement_rules:
        legacy_metadata = None
        legacy_getter = getattr(getattr(self, "context", None), "get_registered_star", None)
        if callable(legacy_getter):
            try:
                legacy_metadata = legacy_getter("astrbot_plugin_keyword_model_router")
            except Exception:
                legacy_metadata = None
        legacy_config = getattr(getattr(legacy_metadata, "star_cls", None), "config", None)
        if isinstance(legacy_config, dict):
            self.model_replacement_rules, legacy_warnings = build_rules(legacy_config.get("route_rules", []))
            model_replacement_warnings.extend(f"兼容旧插件：{warning}" for warning in legacy_warnings)
    for warning in model_replacement_warnings:
        logger.warning("模型替换规则：%s", warning)
    self.enable_sensitive_model_replacement = self._cfg_bool(
        c,
        "enable_sensitive_model_replacement",
        False,
    )
    self.sensitive_replacement_provider_id = self._cfg_str(
        c,
        "SENSITIVE_REPLACEMENT_PROVIDER_ID",
        "",
    )
    self.sensitive_replacement_keywords = self._cfg_str(
        c,
        "sensitive_replacement_keywords",
        "；".join(DEFAULT_SENSITIVE_REPLACEMENT_KEYWORDS),
    )
    self.deepseek_peak_replacement_provider_id = self._cfg_str(c, "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID", "")
    self.deepseek_peak_windows = self._cfg_str(c, "deepseek_peak_windows", "09:00-12:00\n14:00-18:00")
    self.deepseek_peak_timezone = self._cfg_str(c, "deepseek_peak_timezone", "Asia/Shanghai", "Asia/Shanghai")
    self.deepseek_peak_match_keywords = self._cfg_str(c, "deepseek_peak_match_keywords", "deepseek,深度求索")
    self._deepseek_peak_last_log_key = ""
    _page_font = str(self._cfg_raw(c, "page_font_family", "original") or "original").strip().lower()
    self.page_font_family = _page_font if _page_font in PAGE_FONT_NAMES else "original"
    _page_theme = str(self._cfg_raw(c, "page_theme", "classic") or "classic").strip().lower()
    self.page_theme = _page_theme if _page_theme in PAGE_THEME_NAMES else "classic"
    self.fast_response_provider_id = self._cfg_str(c, "FAST_RESPONSE_PROVIDER_ID", "")
    self.complex_reasoning_provider_id = self._cfg_str(c, "COMPLEX_REASONING_PROVIDER_ID", "")
    self.creative_model_provider_id = self._cfg_str(c, "CREATIVE_MODEL_PROVIDER_ID", "")
    self.llm_provider_id = self._cfg_str(c, "LLM_PROVIDER_ID", "")
    self.daily_token_limit = self._cfg_int(c, "daily_token_limit", 1_000_000, 0)
    legacy_soft_enabled = self._cfg_bool(c, "enable_maintenance_token_saver", True)
    legacy_soft_limit = self._cfg_int(c, "maintenance_token_soft_limit", 800_000, 0)
    self.enable_daily_token_soft_limit = self._cfg_bool(c, "enable_daily_token_soft_limit", legacy_soft_enabled)
    self.daily_token_soft_limit = self._cfg_int(c, "daily_token_soft_limit", legacy_soft_limit, 0)
    self.enable_maintenance_token_saver = self.enable_daily_token_soft_limit
    self.maintenance_token_soft_limit = self.daily_token_soft_limit
    self.daily_plan_provider_id = self._cfg_str(c, "DAILY_PLAN_PROVIDER_ID", "")
    self.enable_daily_plan = self._cfg_bool(c, "enable_daily_plan", True)
    self.daily_plan_time = self._cfg_str(c, "daily_plan_time", "07:30")
    self.bot_name = self._cfg_str(c, "bot_name", "小星", "小星")
    self.bot_scope_mode = self._cfg_str(c, "bot_scope_mode", "all", "all").strip().lower()
    if self.bot_scope_mode not in {"all", "allowlist", "denylist"}:
        self.bot_scope_mode = "all"
    self.bot_scope_ids = self._cfg_raw(c, "bot_scope_ids", [])
    self.include_schedule_in_messages = self._cfg_bool(c, "include_schedule_in_messages", True)
    self.daily_plan_prompt = self._cfg_str(c, "daily_plan_prompt", "")
    self.schedule_persona_prompt = self._cfg_str(c, "schedule_persona_prompt", "")
    self.schedule_worldview_prompt = self._cfg_str(c, "schedule_worldview_prompt", "")
    self.roleplay_user_profile_prompt = self._cfg_str(c, "roleplay_user_profile_prompt", "")
    self.roleplay_knowledge_source_ids = self._normalize_roleplay_knowledge_source_ids(
        self._cfg_raw(c, "roleplay_knowledge_source_ids", [])
    )
    self.private_image_self_recognition_hint = self._cfg_str(c, "private_image_self_recognition_hint", "")
    self.daily_plan_item_count = self._cfg_int(c, "daily_plan_item_count", 10, 5, 24)
    self.enable_humanized_states = self._cfg_bool(c, "enable_humanized_states", True)
    self.enable_health_state = self._cfg_bool(c, "enable_health_state", True)
    self.enable_hunger_state = self._cfg_bool(c, "enable_hunger_state", True)
    self.enable_cycle_state = self._cfg_bool(c, "enable_cycle_state", True)
    self.enable_group_cycle_awareness = self._cfg_bool(c, "enable_group_cycle_awareness", False)
    self.humanized_state_intensity = self._cfg_int(c, "humanized_state_intensity", 50, 0, 100)
    self.enable_advanced_cycle_strategy = self._cfg_bool(c, "enable_advanced_cycle_strategy", False)
    self.advanced_cycle_link_intensity = self._cfg_bool(c, "advanced_cycle_link_intensity", False)
    self.advanced_cycle_start_offset = self._cfg_int(c, "advanced_cycle_start_offset", 0, 0, 180)
    self.advanced_cycle_menstrual_days = self._cfg_int(c, "advanced_cycle_menstrual_days", 5, 1, 30)
    self.advanced_cycle_menstrual_prompt = self._cfg_str(
        c, "advanced_cycle_menstrual_prompt", "处于月经期，身体更容易疲倦，情绪感受稍敏锐"
    )
    self.advanced_cycle_menstrual_mood = self._cfg_str(c, "advanced_cycle_menstrual_mood", "疲惫")
    self.advanced_cycle_menstrual_energy = self._cfg_int(c, "advanced_cycle_menstrual_energy", -12, -50, 30)
    self.advanced_cycle_follicular_days = self._cfg_int(c, "advanced_cycle_follicular_days", 5, 1, 30)
    self.advanced_cycle_follicular_prompt = self._cfg_str(
        c, "advanced_cycle_follicular_prompt", "处于卵泡期，精力平稳回升，心情逐渐轻快"
    )
    self.advanced_cycle_follicular_mood = self._cfg_str(c, "advanced_cycle_follicular_mood", "轻快")
    self.advanced_cycle_follicular_energy = self._cfg_int(c, "advanced_cycle_follicular_energy", 0, -50, 30)
    self.advanced_cycle_pre_ovulation_days = self._cfg_int(c, "advanced_cycle_pre_ovulation_days", 3, 1, 30)
    self.advanced_cycle_pre_ovulation_prompt = self._cfg_str(
        c, "advanced_cycle_pre_ovulation_prompt", "处于排卵前期，身体逐渐轻盈，精力有所上升"
    )
    self.advanced_cycle_pre_ovulation_mood = self._cfg_str(c, "advanced_cycle_pre_ovulation_mood", "期待")
    self.advanced_cycle_pre_ovulation_energy = self._cfg_int(c, "advanced_cycle_pre_ovulation_energy", 8, -50, 30)
    self.advanced_cycle_ovulation_days = self._cfg_int(c, "advanced_cycle_ovulation_days", 1, 1, 30)
    self.advanced_cycle_ovulation_prompt = self._cfg_str(
        c, "advanced_cycle_ovulation_prompt", "处于排卵期，精力较充足，社交意愿稍有增强"
    )
    self.advanced_cycle_ovulation_mood = self._cfg_str(c, "advanced_cycle_ovulation_mood", "明朗")
    self.advanced_cycle_ovulation_energy = self._cfg_int(c, "advanced_cycle_ovulation_energy", 9, -50, 30)
    self.advanced_cycle_luteal_days = self._cfg_int(c, "advanced_cycle_luteal_days", 8, 1, 30)
    self.advanced_cycle_luteal_prompt = self._cfg_str(
        c, "advanced_cycle_luteal_prompt", "处于黄体期，精力尚可，情绪整体平稳"
    )
    self.advanced_cycle_luteal_mood = self._cfg_str(c, "advanced_cycle_luteal_mood", "平稳")
    self.advanced_cycle_luteal_energy = self._cfg_int(c, "advanced_cycle_luteal_energy", 5, -50, 30)
    self.advanced_cycle_pms_days = self._cfg_int(c, "advanced_cycle_pms_days", 6, 1, 30)
    self.advanced_cycle_pms_prompt = self._cfg_str(
        c, "advanced_cycle_pms_prompt", "处于 PMS 期，精力有所下降，情绪波动稍明显"
    )
    self.advanced_cycle_pms_mood = self._cfg_str(c, "advanced_cycle_pms_mood", "敏感")
    self.advanced_cycle_pms_energy = self._cfg_int(c, "advanced_cycle_pms_energy", -8, -50, 30)
    self.advanced_cycle_discomfort_simulation = self._cfg_bool(
        c, "advanced_cycle_discomfort_simulation", False
    )
    self.advanced_cycle_discomfort_chance = self._cfg_int(
        c, "advanced_cycle_discomfort_chance", 55, 0, 100
    )
    self.advanced_cycle_discomfort_types = self._cfg_str(
        c, "advanced_cycle_discomfort_types", "痛经,头痛,腰酸,乏力"
    )
    self.enable_rest_reply_simulation = self._cfg_bool(c, "enable_rest_reply_simulation", False)
    self.rest_reply_mode = self._cfg_str(c, "rest_reply_mode", "probability", "probability").strip().lower()
    if self.rest_reply_mode in {"model", "模型", "llm_judge", "llm-judge"}:
        self.rest_reply_mode = "llm"
    if self.rest_reply_mode not in {"probability", "llm"}:
        self.rest_reply_mode = "probability"
    self.rest_reply_probability = self._cfg_int(c, "rest_reply_probability", 18, 0, 100) / 100.0
    self.rest_reply_llm_threshold = self._cfg_int(c, "rest_reply_llm_threshold", 65, 0, 100)
    self.rest_reply_active_windows = self._cfg_str(c, "rest_reply_active_windows", "23:00-08:30,12:20-13:40")
    self.rest_reply_awake_grace_minutes = self._cfg_int(c, "rest_reply_awake_grace_minutes", 30, 0, 240)
    self.enable_rest_backlog_reply = self._cfg_bool(c, "enable_rest_backlog_reply", True)
    self.rest_backlog_max_messages = self._cfg_int(c, "rest_backlog_max_messages", 4, 1, 12)
    self.rest_wakeup_provider_id = self._cfg_str(c, "REST_WAKEUP_PROVIDER_ID", "")
    self.enable_busy_reply_gate = self._cfg_bool(c, "enable_busy_reply_gate", False)
    self.busy_reply_min_delay_seconds = self._cfg_int(c, "busy_reply_min_delay_seconds", 60, 0, 900)
    self.busy_reply_max_delay_seconds = self._cfg_int(c, "busy_reply_max_delay_seconds", 300, 0, 900)
    if self.busy_reply_max_delay_seconds < self.busy_reply_min_delay_seconds:
        self.busy_reply_min_delay_seconds, self.busy_reply_max_delay_seconds = (
            self.busy_reply_max_delay_seconds,
            self.busy_reply_min_delay_seconds,
        )
    self.busy_reply_proactive_resume_buffer_minutes = self._cfg_int(
        c,
        "busy_reply_proactive_resume_buffer_minutes",
        10,
        0,
        120,
    )
    self.enable_enhanced_dreams = self._cfg_bool(c, "enable_enhanced_dreams", False)
    self.dream_diary_provider_id = self._cfg_str(
        c,
        "DREAM_DIARY_PROVIDER_ID",
        self._cfg_str(c, "DREAM_PROVIDER_ID", self._cfg_str(c, "DIARY_PROVIDER_ID", "")),
    )
    self.dream_provider_id = self.dream_diary_provider_id
    self.diary_provider_id = self.dream_diary_provider_id
    self.dream_afterglow_mode = self._cfg_str(c, "dream_afterglow_mode", "auto", "auto")
    if self.dream_afterglow_mode not in {"auto", "轻", "标准", "明显"}:
        self.dream_afterglow_mode = "auto"
    self.enable_mixed_dream_themes = self._cfg_bool(c, "enable_mixed_dream_themes", True)
    self.enable_intimate_dream_theme = self._cfg_bool(c, "enable_intimate_dream_theme", False)
    self.dream_theme_candidates = self._cfg_str(
        c,
        "dream_theme_candidates",
        "温柔日常,奇幻,恐怖,追逐,悬疑,荒诞,怀旧,暧昧春梦",
    )
    self.inject_passive_states = self._cfg_bool(c, "inject_passive_states", True)
    self.enable_passive_state_delta_injection = self._cfg_bool(c, "enable_passive_state_delta_injection", True)
    self.enable_passive_state_continuity_anchor = self._cfg_bool(
        c,
        "enable_passive_state_continuity_anchor",
        False,
    )
    self.passive_injection_position = self._normalize_passive_injection_position(
        self._cfg_str(c, "passive_injection_position", "prompt")
    )
    self.proactive_share_probability = self._cfg_int(c, "proactive_share_probability", 45, 0, 100) / 100
    self.enable_daily_greetings = self._cfg_bool(c, "enable_daily_greetings", True)
    self.greeting_idle_minutes = self._cfg_int(c, "greeting_idle_minutes", 30, 0, 240)
    self.allow_insomnia_night_message = self._cfg_bool(c, "allow_insomnia_night_message", True)
    self.proactive_reply_context_hours = self._cfg_int(c, "proactive_reply_context_hours", 12, 1, 72)
    self.enable_creative_writing = self._cfg_bool(c, "enable_creative_writing", False)
    self.enable_creative_work_read_guard = self._cfg_bool(
        c, "enable_creative_work_read_guard", True
    )
    self.creative_inspiration_probability = self._cfg_unit_interval(c, "creative_inspiration_probability", 0.20, 0.0)
    self.creative_share_probability = self._cfg_unit_interval(c, "creative_share_probability", 0.28, 0.0)
    self.creative_chars_per_session = self._cfg_int(
        c,
        "creative_chars_per_session",
        self._cfg_int(c, "creative_base_chars_per_hour", 220, 60, 1200),
        60,
        1200,
    )
    self.creative_base_chars_per_hour = self.creative_chars_per_session
    self.creative_max_active_projects = self._cfg_int(c, "creative_max_active_projects", 2, 1, 5)
    self.creative_hidden_mode = self._cfg_bool(c, "creative_hidden_mode", True)
    self.creative_direction_prompt = self._cfg_str(c, "creative_direction_prompt", "")[:2000]
    self.creative_provider_id = self._cfg_str(c, "CREATIVE_PROVIDER_ID", "")
    self.creative_outline_provider_id = self._cfg_str(c, "CREATIVE_OUTLINE_PROVIDER_ID", "")
    self.creative_review_provider_id = self._cfg_str(c, "CREATIVE_REVIEW_PROVIDER_ID", "")
    self.voice_prompt_provider_id = self._cfg_str(c, "VOICE_PROMPT_PROVIDER_ID", "")
    self.history_summary_provider_id = self._cfg_str(c, "HISTORY_SUMMARY_PROVIDER_ID", "")
    self.enable_llm_proactive_message = self._cfg_bool(c, "enable_llm_proactive_message", True)
    self.proactive_generation_history_limit = self._cfg_int(
        c,
        "proactive_generation_history_limit",
        20,
        1,
        200,
    )
    self.proactive_history_context_mode = self._cfg_str(
        c,
        "proactive_history_context_mode",
        "compact",
        "compact",
    ).lower()
    if self.proactive_history_context_mode not in {"recent_only", "compact", "expanded"}:
        self.proactive_history_context_mode = "compact"
    self.proactive_history_recent_raw_count = self._cfg_int(
        c,
        "proactive_history_recent_raw_count",
        8,
        1,
        50,
    )
