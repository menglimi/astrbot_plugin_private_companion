# -*- coding: utf-8 -*-
"""plugin_bootstrap 拆分件 part05：插件引导初始化（机械搬移，行为不变）。

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




def _initialize_review_and_group_config(self: Any, c: Any) -> None:
    self.expression_group_learning_daily_batch_limit = self._cfg_int(
        c,
        "expression_group_learning_daily_batch_limit",
        6,
        1,
        50,
    )
    self.expression_group_learning_min_new_messages = self._cfg_int(
        c,
        "expression_group_learning_min_new_messages",
        20,
        5,
        80,
    )
    self.expression_private_application_mode = self._cfg_str(
        c, "expression_private_application_mode", "all", "all"
    ).lower()
    if self.expression_private_application_mode not in {"all", "selected"}:
        self.expression_private_application_mode = "all"
    self.expression_private_application_user_ids = self._cfg_raw(c, "expression_private_application_user_ids", [])
    self.expression_group_application_mode = self._cfg_str(
        c, "expression_group_application_mode", "all", "all"
    ).lower()
    if self.expression_group_application_mode not in {"disabled", "all", "selected"}:
        self.expression_group_application_mode = "all"
    self.expression_group_application_ids = self._cfg_raw(c, "expression_group_application_ids", [])
    self.enable_expression_manual_review = self._cfg_bool(c, "enable_expression_manual_review", False)
    self.enable_expression_style_review = self._cfg_bool(c, "enable_expression_style_review", True)
    self.enable_intent_emotion_analysis = self._cfg_bool(c, "enable_intent_emotion_analysis", True)
    legacy_response_review_enabled = self._cfg_bool(c, "enable_response_self_review", True)
    self.enable_passive_response_review = self._cfg_bool(
        c,
        "enable_passive_response_review",
        legacy_response_review_enabled,
    )
    self.enable_framework_error_leak_guard = self._cfg_bool(
        c,
        "enable_framework_error_leak_guard",
        True,
    )
    # Runtime alias for older integrations. It no longer gates proactive review.
    self.enable_response_self_review = self.enable_passive_response_review
    self.enable_proactive_message_review = self._cfg_bool(
        c,
        "enable_proactive_message_review",
        legacy_response_review_enabled,
    )
    self.external_share_require_source_link = self._cfg_bool(c, "external_share_require_source_link", True)
    self.proactive_review_history_limit = self._cfg_int(
        c,
        "proactive_review_history_limit",
        30,
        1,
        200,
    )
    legacy_response_review_mode = self._cfg_str(c, "response_review_mode", "severe_only", "severe_only").lower()
    self.passive_review_mode = self._cfg_str(
        c,
        "passive_review_mode",
        legacy_response_review_mode,
        legacy_response_review_mode,
    ).lower()
    if self.passive_review_mode not in {"local_only", "severe_only", "full"}:
        self.passive_review_mode = "severe_only"
    self.response_review_mode = self.passive_review_mode
    self.passive_review_strength = self._cfg_str(c, "passive_review_strength", "lenient", "lenient").lower()
    if self.passive_review_strength not in {"lenient", "balanced", "strict"}:
        self.passive_review_strength = "lenient"
    self.proactive_review_mode = self._cfg_str(c, "proactive_review_mode", "full", "full").lower()
    if self.proactive_review_mode not in {"local_only", "severe_only", "full"}:
        self.proactive_review_mode = "full"
    self.enable_smart_silence = self._cfg_bool(c, "enable_smart_silence", True)
    self.smart_silence_judge_mode = self._cfg_str(c, "smart_silence_judge_mode", "boundary_only", "boundary_only").strip().lower()
    if self.smart_silence_judge_mode not in {"boundary_only", "contextual"}:
        self.smart_silence_judge_mode = "boundary_only"
    self.smart_silence_provider_id = self._cfg_str(c, "SMART_SILENCE_PROVIDER_ID", "")
    self.smart_silence_min_confidence = self._cfg_unit_interval(c, "smart_silence_min_confidence", 0.66, 0.0)
    self.smart_silence_model_timeout_seconds = self._cfg_float(c, "smart_silence_model_timeout_seconds", 1.2, 0.2)
    self._smart_silence_cache: dict[str, dict[str, Any]] = {}
    self.proactive_review_strength = self._cfg_str(c, "proactive_review_strength", "lenient", "lenient").lower()
    if self.proactive_review_strength not in {"lenient", "balanced", "strict"}:
        self.proactive_review_strength = "lenient"
    self.proactive_review_hard_risk_threshold = self._cfg_unit_interval(c, "proactive_review_hard_risk_threshold", 0.70, 0.0)
    self.proactive_review_low_score_threshold = self._cfg_unit_interval(c, "proactive_review_low_score_threshold", 0.34, 0.0)
    self.proactive_review_pressure_threshold = self._cfg_unit_interval(c, "proactive_review_pressure_threshold", 0.55, 0.0)
    self.enable_passive_topic_suppression = self._cfg_bool(c, "enable_passive_topic_suppression", True)
    # The unified ledger owns runtime updates.  Keep the legacy switches
    # readable for diagnostics and old config pages, but do not let their
    # persisted values disable the unified path.
    self.enable_relationship_analysis = self._cfg_bool(c, "enable_relationship_analysis", True)
    self.enable_relationship_state_machine = self._cfg_bool(c, "enable_relationship_state_machine", True)
    self.enable_emotion_simulation = self._cfg_bool(c, "enable_emotion_simulation", True)
    self.enable_relationship_violation_penalties = self._cfg_bool(c, "enable_relationship_violation_penalties", True)
    self.enable_relationship_boundary_feedback = self._cfg_bool(c, "enable_relationship_boundary_feedback", True)
    self.enable_relationship_boundary_stage = self._cfg_bool(c, "enable_relationship_boundary_stage", True)
    self.enable_relationship_boundary_apology = self._cfg_bool(c, "enable_relationship_boundary_apology", True)
    self.enable_relationship_boundary_bottom_line = self._cfg_bool(c, "enable_relationship_boundary_bottom_line", True)
    self.relationship_boundary_tier_adaptive = self._cfg_bool(c, "relationship_boundary_tier_adaptive", True)
    self.relationship_boundary_penalty_light = self._cfg_int(c, "relationship_boundary_penalty_light", 4, 1, 60)
    self.relationship_boundary_penalty_mid = self._cfg_int(c, "relationship_boundary_penalty_mid", 7, 1, 60)
    self.relationship_boundary_penalty_severe = self._cfg_int(c, "relationship_boundary_penalty_severe", 12, 1, 60)
    self.relationship_boundary_penalty_bottom_line = self._cfg_int(c, "relationship_boundary_penalty_bottom_line", 14, 1, 60)
    self.relationship_boundary_stage_avoid_points = self._cfg_int(c, "relationship_boundary_stage_avoid_points", 6, 1, 120)
    self.relationship_boundary_stage_forbid_points = self._cfg_int(c, "relationship_boundary_stage_forbid_points", 12, 1, 120)
    self.relationship_boundary_stage_reflect_points = self._cfg_int(c, "relationship_boundary_stage_reflect_points", 20, 1, 120)
    if self.relationship_boundary_stage_forbid_points < self.relationship_boundary_stage_avoid_points:
        self.relationship_boundary_stage_forbid_points = self.relationship_boundary_stage_avoid_points
    if self.relationship_boundary_stage_reflect_points < self.relationship_boundary_stage_forbid_points:
        self.relationship_boundary_stage_reflect_points = self.relationship_boundary_stage_forbid_points
    self.relationship_boundary_cold_minutes = self._cfg_int(c, "relationship_boundary_cold_minutes", 180, 10, 1440)
    self.relationship_boundary_apology_restore_ratio = self._cfg_unit_interval(c, "relationship_boundary_apology_restore_ratio", 0.6, 0.0)
    self.relationship_boundary_apology_duplicate_limit = self._cfg_int(c, "relationship_boundary_apology_duplicate_limit", 3, 1, 20)
    self.relationship_boundary_apology_speedup_multiplier = self._cfg_float(c, "relationship_boundary_apology_speedup_multiplier", 3.0, 1.0, 10.0)
    self.relationship_boundary_recover_ratio_light = self._cfg_unit_interval(c, "relationship_boundary_recover_ratio_light", 0.5, 0.0)
    self.relationship_boundary_recover_ratio_mid = self._cfg_unit_interval(c, "relationship_boundary_recover_ratio_mid", 0.33, 0.0)
    self.relationship_boundary_recover_ratio_severe = self._cfg_unit_interval(c, "relationship_boundary_recover_ratio_severe", 0.25, 0.0)
    self.enable_relationship_boundary_vent = self._cfg_bool(c, "enable_relationship_boundary_vent", True)
    self.enable_relationship_boundary_owner_report = self._cfg_bool(c, "enable_relationship_boundary_owner_report", True)
    self.relationship_boundary_vent_targets = self._cfg_raw(c, "relationship_boundary_vent_targets", [])
    self.relationship_boundary_vent_scene_template = self._cfg_str(c, "relationship_boundary_vent_scene_template", "")
    self.relationship_boundary_bottom_line_baseline = self._cfg_str(c, "relationship_boundary_bottom_line_baseline", "")
    self.relationship_boundary_tone_confession = self._cfg_str(
        c,
        "relationship_boundary_tone_confession",
        "把这次表达当作心意，不当作冒犯；结合当前关系自然害羞、迟疑或温和说明节奏，不必机械拒绝。",
    )
    self.relationship_boundary_tone_light = self._cfg_str(
        c,
        "relationship_boundary_tone_light",
        "轻微降低亲密度，带一点迟疑或回避并自然说明节奏；不要把普通互动渲染成严重冒犯。",
    )
    self.relationship_boundary_tone_mid = self._cfg_str(
        c,
        "relationship_boundary_tone_mid",
        "平静而明确地划清界限，减少主动贴近和暧昧回应；可以说明原因，但不要反复说教。",
    )
    self.relationship_boundary_tone_severe = self._cfg_str(
        c,
        "relationship_boundary_tone_severe",
        "明显收住亲密表达，直接说明不舒服并拒绝继续；保持角色口吻，不使用系统式警告。",
    )
    self.relationship_boundary_tone_bottom_line = self._cfg_str(
        c,
        "relationship_boundary_tone_bottom_line",
        "明确表达这触碰了重要底线，受伤和距离感可以真实存在；不要功能化播报惩罚，也不要立即恢复亲密。",
    )
    self.relationship_boundary_tone_silent = self._cfg_str(
        c,
        "relationship_boundary_tone_silent",
        "关系尚浅时不必长篇袒露脆弱，可以安静收住互动并记住这次不舒服。",
    )
    self.relationship_boundary_tone_communicate = self._cfg_str(
        c,
        "relationship_boundary_tone_communicate",
        "关系很深时可以因为信任而说清为什么难过或生气，但亲密关系不等于放弃边界。",
    )
    for _boundary_level, _boundary_default in {
        "light": 0.15,
        "mid": 0.35,
        "severe": 0.6,
        "bottom_line": 0.9,
    }.items():
        setattr(self, f"relationship_boundary_vent_probability_{_boundary_level}", self._cfg_unit_interval(c, f"relationship_boundary_vent_probability_{_boundary_level}", _boundary_default, 0.0))
    for _boundary_level, _boundary_default in {
        "light": 0.12,
        "mid": 0.3,
        "severe": 0.55,
        "bottom_line": 0.85,
    }.items():
        setattr(self, f"relationship_boundary_owner_report_probability_{_boundary_level}", self._cfg_unit_interval(c, f"relationship_boundary_owner_report_probability_{_boundary_level}", _boundary_default, 0.0))
    self.relationship_violation_recovery_minutes_per_point = self._cfg_int(
        c,
        "relationship_violation_recovery_minutes_per_point",
        180,
        15,
        10080,
    )
    self.enable_llm_emotion_judgement = self._cfg_bool(c, "enable_llm_emotion_judgement", False)
    self.emotion_judgement_mode = self._cfg_str(c, "emotion_judgement_mode", "suspicious", "suspicious").lower()
    if self.emotion_judgement_mode not in {"suspicious", "always", "off"}:
        self.emotion_judgement_mode = "suspicious"
    self.emotional_gate_hurt_threshold = self._cfg_int(c, "emotional_gate_hurt_threshold", 70, 10, 100)
    if self.emotional_gate_hurt_threshold == 55:
        self.emotional_gate_hurt_threshold = 70
        _set_into_config(c, "emotional_gate_hurt_threshold", self.emotional_gate_hurt_threshold)
    self.emotional_gate_refuse_threshold = self._cfg_int(c, "emotional_gate_refuse_threshold", 90, 20, 100)
    if self.emotional_gate_refuse_threshold == 80:
        self.emotional_gate_refuse_threshold = 90
        _set_into_config(c, "emotional_gate_refuse_threshold", self.emotional_gate_refuse_threshold)
    if self.emotional_gate_refuse_threshold <= self.emotional_gate_hurt_threshold:
        self.emotional_gate_refuse_threshold = min(100, self.emotional_gate_hurt_threshold + 5)
    self.emotional_gate_recovery_per_hour = self._cfg_int(c, "emotional_gate_recovery_per_hour", 24, 1, 60)
    if self.emotional_gate_recovery_per_hour == 12:
        self.emotional_gate_recovery_per_hour = 24
        _set_into_config(c, "emotional_gate_recovery_per_hour", self.emotional_gate_recovery_per_hour)
    self.emotional_gate_max_hurt_minutes = self._cfg_int(c, "emotional_gate_max_hurt_minutes", 90, 10, 720)
    if self.emotional_gate_max_hurt_minutes == 180:
        self.emotional_gate_max_hurt_minutes = 90
        _set_into_config(c, "emotional_gate_max_hurt_minutes", self.emotional_gate_max_hurt_minutes)
    self.enable_dialogue_episode_memory = self._cfg_bool(c, "enable_dialogue_episode_memory", True)
    self.enable_open_loop_tracking = self._cfg_bool(c, "enable_open_loop_tracking", True)
    self.enable_user_habit_learning = self._cfg_bool(c, "enable_user_habit_learning", True)
    self.enable_food_menu_recommendation = self._cfg_bool(c, "enable_food_menu_recommendation", True)
    self.enable_meal_care_proactive = self._cfg_bool(c, "enable_meal_care_proactive", True)
    self.meal_care_max_daily = self._cfg_int(c, "meal_care_max_daily", 1, 0, 3)
    self.meal_care_min_interval_hours = self._cfg_int(c, "meal_care_min_interval_hours", 48, 0, 168)
    self.meal_care_followup_minutes = self._cfg_int(c, "meal_care_followup_minutes", 45, 15, 180)
    self.user_habit_min_count = self._cfg_int(c, "user_habit_min_count", 3, 2, 20)
    self.user_habit_max_items = self._cfg_int(c, "user_habit_max_items", 24, 8, 80)
    self.enable_skill_growth_simulation = self._cfg_bool(c, "enable_skill_growth_simulation", True)
    self.skill_growth_rate = self._cfg_float(c, "skill_growth_rate", 1.0, 0.1)
    self.skill_growth_custom_skills = self._cfg_str(c, "skill_growth_custom_skills", "")
    self.enable_skill_growth_passive_injection = self._cfg_bool(c, "enable_skill_growth_passive_injection", False)
    self.enable_skill_growth_schedule_influence = self._cfg_bool(c, "enable_skill_growth_schedule_influence", True)
    self.skill_growth_schedule_influence_strength = self._cfg_unit_interval(c, "skill_growth_schedule_influence_strength", 0.35, 0.0)
    self.enable_personal_goals = self._cfg_bool(c, "enable_personal_goals", True)
    self.enable_personal_goal_auto_progress = self._cfg_bool(c, "enable_personal_goal_auto_progress", True)
    self.personal_goal_share_cooldown_hours = self._cfg_float(c, "personal_goal_share_cooldown_hours", 12.0, 1.0, 168.0)
    self.personal_goal_stall_days = self._cfg_int(c, "personal_goal_stall_days", 3, 1, 30)
    self.memory_refresh_interval_minutes = self._cfg_int(c, "memory_refresh_interval_minutes", 360, 30, 4320)
    self.max_companion_memory_items = self._cfg_int(c, "max_companion_memory_items", 36, 8, 120)
    self.max_learned_expression_items = self._cfg_int(c, "max_learned_expression_items", 60, 12, 240)
    self.mai_style_provider_id = self._cfg_str(c, "MAI_STYLE_PROVIDER_ID", "")
    self.companion_memory_provider_id = self._cfg_str(c, "COMPANION_MEMORY_PROVIDER_ID", "")
    self.dialogue_episode_provider_id = self._cfg_str(c, "DIALOGUE_EPISODE_PROVIDER_ID", "")
    self.relationship_analysis_provider_id = self._cfg_str(c, "RELATIONSHIP_ANALYSIS_PROVIDER_ID", "")
    self.response_review_provider_id = self._cfg_str(c, "RESPONSE_REVIEW_PROVIDER_ID", "")
    self.troubleshooting_provider_id = self._cfg_str(c, "TROUBLESHOOTING_PROVIDER_ID", "")
    self.daily_review_provider_id = self._cfg_str(c, "DAILY_REVIEW_PROVIDER_ID", "")
    self.emotion_judgement_provider_id = self._cfg_str(c, "EMOTION_JUDGEMENT_PROVIDER_ID", "")
    self.response_review_max_chars = self._cfg_int(c, "response_review_max_chars", 260, 80, 900)
    self.passive_topic_memory_hours = self._cfg_int(c, "passive_topic_memory_hours", 8, 1, 72)
    self.episode_memory_refresh_messages = self._cfg_int(c, "episode_memory_refresh_messages", 8, 3, 40)
    self.episode_memory_refresh_minutes = self._cfg_int(c, "episode_memory_refresh_minutes", 90, 15, 1440)
    self.max_dialogue_episodes = self._cfg_int(c, "max_dialogue_episodes", 12, 3, 40)
    self.enable_group_companion = self._cfg_bool(c, "enable_group_companion", True)
    self.group_access_mode = self._cfg_str(c, "group_access_mode", "whitelist", "whitelist").lower()
    if self.group_access_mode not in {"whitelist", "blacklist"}:
        self.group_access_mode = "whitelist"
    self.target_group_ids = self._cfg_raw(c, "target_group_ids", [])
    self.group_whitelist_ids = self._cfg_raw(c, "group_whitelist_ids", self.target_group_ids)
    self.group_blacklist_ids = self._cfg_raw(c, "group_blacklist_ids", [])
    self.require_target_group = self._cfg_bool(c, "require_target_group", True)
    self.enable_group_slang_learning = self._cfg_bool(c, "enable_group_slang_learning", True)
    # Group observation no longer writes the retired Worldbook member profile,
    # but keep this compatibility switch readable for existing installations.
    self.enable_group_member_profiles = self._cfg_bool(c, "enable_group_member_profiles", True)
    self.enable_group_member_safety = self._cfg_bool(c, "enable_group_member_safety", True)
    self.group_member_safety_review_mode = self._cfg_str(
        c, "group_member_safety_review_mode", "directed", "directed"
    ).lower()
    if self.group_member_safety_review_mode not in {"directed", "suspicious", "all"}:
        self.group_member_safety_review_mode = "directed"
    self.group_member_safety_hidden_marker_mode = self._cfg_str(
        c, "group_member_safety_hidden_marker_mode", "reply_only", "reply_only"
    ).lower()
    if self.group_member_safety_hidden_marker_mode not in {"supplement", "reply_only", "disabled"}:
        self.group_member_safety_hidden_marker_mode = "reply_only"
    self.group_member_safety_strike_threshold = self._cfg_int(
        c, "group_member_safety_strike_threshold", 3, 1, 20
    )
    self.group_member_safety_strike_window_days = self._cfg_int(
        c, "group_member_safety_strike_window_days", 30, 1, 365
    )
    self.group_member_safety_block_hours = self._cfg_int(
        c, "group_member_safety_block_hours", 168, 0, 8760
    )
    self.group_member_safety_min_confidence = self._cfg_float(
        c, "group_member_safety_min_confidence", 0.86, 0.5, 1.0
    )
    self.group_member_safety_exempt_managers = self._cfg_bool(
        c, "group_member_safety_exempt_managers", True
    )
    self.group_member_safety_audit_limit = self._cfg_int(
        c, "group_member_safety_audit_limit", 40, 10, 200
    )
    self.enable_group_context_injection = self._cfg_bool(c, "enable_group_context_injection", True)
    self.enable_group_history_injection = self._cfg_bool(c, "enable_group_history_injection", True)
    self.intercept_astrbot_group_context = self._cfg_bool(
        c, "intercept_astrbot_group_context", True
    )
    self.enable_group_injection_guard = self._cfg_bool(c, "enable_group_injection_guard", True)
    self.enable_group_persona_denoise = self._cfg_bool(c, "enable_group_persona_denoise", True)
    # Keep the grouped social-context switches in the runtime snapshot too.
    # They are consumed by the group observer and exposed by the WebUI; when
    # they were only present in the schema, a restart made a saved ``true``
    # value look disabled because the feature flag reader fell back to a
    # missing attribute.
    self.enable_group_social_context = self._cfg_bool(c, "enable_group_social_context", False)
    self.enable_group_mood_detection = self._cfg_bool(c, "enable_group_mood_detection", False)
    self.enable_group_roleplay_strength = self._cfg_bool(c, "enable_group_roleplay_strength", False)
    self.enable_group_moments = self._cfg_bool(c, "enable_group_moments", False)
    self.enable_group_moment_portrait = self._cfg_bool(c, "enable_group_moment_portrait", False)
    self.enable_group_joke_guard = self._cfg_bool(c, "enable_group_joke_guard", False)
    self.enable_forward_message_adaptation = self._cfg_bool(c, "enable_forward_message_adaptation", True)
    self.forward_message_mode = self._cfg_str(c, "forward_message_mode", "inject", "inject").lower()
    if self.forward_message_mode in {"注入", "injection"}:
        self.forward_message_mode = "inject"
    elif self.forward_message_mode in {"转述", "summary", "summarize", "narrate", "relay"}:
        self.forward_message_mode = "transcribe"
    elif self.forward_message_mode not in {"inject", "transcribe"}:
        self.forward_message_mode = "inject"
    self.forward_message_provider_id = self._cfg_str(c, "FORWARD_MESSAGE_PROVIDER_ID", "")
    self.forward_message_max_messages = self._cfg_int(c, "forward_message_max_messages", 80, 5, 300)
    self.forward_message_max_chars = self._cfg_int(c, "forward_message_max_chars", 5000, 800, 20000)
    self.forward_message_parse_nested = self._cfg_bool(c, "forward_message_parse_nested", True)
    self.forward_message_image_vision = self._cfg_bool(c, "forward_message_image_vision", True)
    self.forward_message_image_limit = self._cfg_int(c, "forward_message_image_limit", 4, 0, 12)
    self.forward_message_image_vision_timeout_seconds = self._cfg_float(
        c,
        "forward_message_image_vision_timeout_seconds",
        60.0,
        0.0,
        600.0,
    )
    self.enable_group_scene_awareness = self._cfg_bool(c, "enable_group_scene_awareness", True)
    self.group_scene_recent_limit = self._cfg_int(c, "group_scene_recent_limit", 20, 2, 100)
    self.group_scene_recent_max_chars = self._cfg_int(c, "group_scene_recent_max_chars", 4000, 500, 20000)
    self.enable_group_reality_promise_guard = self._cfg_bool(c, "enable_group_reality_promise_guard", True)
    self.enable_group_wakeup_enhancement = self._cfg_bool(c, "enable_group_wakeup_enhancement", True)
    self.group_wakeup_direct_words = self._parse_text_list_config(self._cfg_raw(c, "group_wakeup_direct_words", []))
    self.enable_group_bot_name_wakeup = self._cfg_bool(c, "enable_group_bot_name_wakeup", True)
    self.group_wakeup_owner_direct_words = self._parse_text_list_config(
        self._cfg_raw(c, "group_wakeup_owner_direct_words", [])
    )
    self.group_wakeup_context_words = self._parse_text_list_config(
        self._cfg_raw(c, "group_wakeup_context_words", ["机器人", "bot"])
    )
