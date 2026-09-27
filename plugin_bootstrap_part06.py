# -*- coding: utf-8 -*-
"""plugin_bootstrap 拆分件 part06：插件引导初始化（机械搬移，行为不变）。

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
from .plugin_bootstrap_shared import (
    DEFAULT_AI_DAILY_SOURCES,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    logger,
)



def _initialize_group_and_provider_config(self: Any, c: Any) -> None:
    if self.group_wakeup_context_words == ["有人叫你", "提到你", "说到你", "机器人", "AI"]:
        self.group_wakeup_context_words = ["机器人", "bot"]
    self.group_wakeup_interest_keywords = self._parse_text_list_config(self._cfg_raw(c, "group_wakeup_interest_keywords", []))
    self.group_wakeup_interest_probability = self._cfg_int(c, "group_wakeup_interest_probability", 18, 0, 100) / 100
    self.enable_group_wakeup_question = self._cfg_bool(c, "enable_group_wakeup_question", True)
    self.group_wakeup_question_threshold = self._cfg_int(c, "group_wakeup_question_threshold", 65, 0, 100)
    self.enable_group_wakeup_cold_group = self._cfg_bool(c, "enable_group_wakeup_cold_group", False)
    self.group_wakeup_cold_group_threshold = self._cfg_int(c, "group_wakeup_cold_group_threshold", 65, 0, 100)
    self.group_wakeup_cold_group_idle_minutes = self._cfg_int(c, "group_wakeup_cold_group_idle_minutes", 25, 3, 720)
    self.group_wakeup_cooldown_seconds = self._cfg_int(c, "group_wakeup_cooldown_seconds", 90, 0, 3600)
    self.group_wakeup_generated_keyword_limit = self._cfg_int(c, "group_wakeup_generated_keyword_limit", 24, 4, 80)
    self.group_wakeup_topic_interest_max_boost = self._cfg_int(c, "group_wakeup_topic_interest_max_boost", 45, 0, 150) / 100
    self.group_wakeup_debounce_pending_penalty = self._cfg_int(c, "group_wakeup_debounce_pending_penalty", 65, 0, 100) / 100
    self.group_wakeup_fatigue_limit = self._cfg_int(c, "group_wakeup_fatigue_limit", 5, 1, 20)
    self.group_wakeup_fatigue_decay_minutes = self._cfg_int(c, "group_wakeup_fatigue_decay_minutes", 90, 5, 720)
    self.group_wakeup_log_limit = self._cfg_int(c, "group_wakeup_log_limit", 80, 10, 300)
    self.group_wakeup_short_text_wait_seconds = self._cfg_float(c, "group_wakeup_short_text_wait_seconds", 15.0, 0.0)
    self.enable_group_high_intensity_mode = self._cfg_bool(c, "enable_group_high_intensity_mode", True)
    self.group_high_intensity_wakeup_window_seconds = self._cfg_int(c, "group_high_intensity_wakeup_window_seconds", 60, 15, 600)
    self.group_high_intensity_wakeup_threshold = self._cfg_int(c, "group_high_intensity_wakeup_threshold", 3, 2, 20)
    self.group_high_intensity_cooldown_seconds = self._cfg_int(c, "group_high_intensity_cooldown_seconds", 150, 30, 1800)
    self.group_high_intensity_merge_seconds = self._cfg_int(c, "group_high_intensity_merge_seconds", 8, 1, 30)
    self.group_high_intensity_max_merge_messages = self._cfg_int(c, "group_high_intensity_max_merge_messages", 8, 0, 50)
    self.group_high_intensity_merge_scope = self._cfg_str(c, "group_high_intensity_merge_scope", "group", "group").lower()
    if self.group_high_intensity_merge_scope in {"sender", "same_sender", "same_user", "user"}:
        self.group_high_intensity_merge_scope = "same_user"
    elif self.group_high_intensity_merge_scope not in {"group", "same_user"}:
        self.group_high_intensity_merge_scope = "group"
    self.enable_group_interjection = self._cfg_bool(c, "enable_group_interjection", False)
    self.enable_group_repeat_follow = self._cfg_bool(c, "enable_group_repeat_follow", True)
    self.group_repeat_trigger_threshold = self._cfg_int(c, "group_repeat_trigger_threshold", 4, 3, 20)
    self.group_repeat_count_distinct_users_only = self._cfg_bool(c, "group_repeat_count_distinct_users_only", False)
    self.group_repeat_follow_probability = self._cfg_int(c, "group_repeat_follow_probability", 18, 0, 100) / 100
    self.group_repeat_interrupt_probability = self._cfg_int(c, "group_repeat_interrupt_probability", 10, 0, 100) / 100
    self.group_repeat_interrupt_probability_step = self._cfg_int(c, "group_repeat_interrupt_probability_step", 12, 0, 100) / 100
    self.group_repeat_interrupt_text = self._cfg_str(c, "group_repeat_interrupt_text", "禁止复读", "禁止复读")
    self.group_repeat_interrupt_image_path = self._cfg_str(c, "group_repeat_interrupt_image_path", "")
    self.group_interject_min_interval_minutes = self._cfg_int(c, "group_interject_min_interval_minutes", 180, 10, 1440)
    self.group_interject_max_daily = self._cfg_int(c, "group_interject_max_daily", 2, 0, 12)
    self.max_group_recent_messages = self._cfg_int(c, "max_group_recent_messages", 80, 20, 300)
    self.max_group_slang_terms = self._cfg_int(c, "max_group_slang_terms", 40, 8, 160)
    self.enable_group_topic_threads = self._cfg_bool(c, "enable_group_topic_threads", True)
    self.enable_group_episode_memory = self._cfg_bool(c, "enable_group_episode_memory", True)
    self.enable_group_interjection_feedback = self._cfg_bool(c, "enable_group_interjection_feedback", True)
    self.enable_group_slang_meanings = self._cfg_bool(c, "enable_group_slang_meanings", True)
    self.enable_group_slang_web_search = self._cfg_bool(c, "enable_group_slang_web_search", False)
    self.group_slang_web_search_terms = self._cfg_int(c, "group_slang_web_search_terms", 4, 1, 12)
    self.group_slang_web_search_results = self._cfg_int(c, "group_slang_web_search_results", 2, 1, 5)
    self.enable_group_relationship_graph = self._cfg_bool(c, "enable_group_relationship_graph", True)
    self.enable_group_privacy_guard = self._cfg_bool(c, "enable_group_privacy_guard", True)
    self.enable_group_third_party_portrait_guard = self._cfg_bool(
        c, "enable_group_third_party_portrait_guard", True
    )
    self.enable_worldbook_member_recognition = self._cfg_bool(c, "enable_worldbook_member_recognition", True)
    self.enable_atrelay_tools = self._cfg_bool(c, "enable_atrelay_tools", True)
    self.enable_cross_user_memory_bridge = self._cfg_bool(c, "enable_cross_user_memory_bridge", False)
    self.cross_user_memory_owner_only = self._cfg_bool(c, "cross_user_memory_owner_only", True)
    self.atrelay_require_worldbook_first = self._cfg_bool(c, "atrelay_require_worldbook_first", True)
    self.atrelay_member_cache_minutes = self._cfg_int(c, "atrelay_member_cache_minutes", 60, 1, 1440)
    self.atrelay_sensitive_confirm = self._cfg_bool(c, "atrelay_sensitive_confirm", True)
    self.enable_atrelay_llm_rewrite = self._cfg_bool(c, "enable_atrelay_llm_rewrite", True)
    self.atrelay_default_relay_style = self._cfg_str(c, "atrelay_default_relay_style", "persona", "persona")
    self.atrelay_multi_target_limit = self._cfg_int(c, "atrelay_multi_target_limit", 5, 1, 20)
    self.worldbook_auto_import = self._cfg_bool(c, "worldbook_auto_import", True)
    self.worldbook_member_match_aliases = self._cfg_bool(c, "worldbook_member_match_aliases", True)
    self.worldbook_self_registration = self._cfg_bool(c, "worldbook_self_registration", True)
    self.worldbook_self_registration_block_words = self._parse_text_list_config(
        self._cfg_raw(c, "worldbook_self_registration_block_words", []),
        limit=120,
    )
    self.worldbook_self_registration_block_reply = self._cfg_str(
        c,
        "worldbook_self_registration_block_reply",
        "这个称呼我不记。",
    )
    if self.worldbook_self_registration_block_reply in {"这个称呼我先不记。", "你是小猪"}:
        self.worldbook_self_registration_block_reply = "这个称呼我不记。"
        _set_into_config(c, "worldbook_self_registration_block_reply", self.worldbook_self_registration_block_reply)
    self.worldbook_auto_pending_observations = self._cfg_bool(c, "worldbook_auto_pending_observations", True)
    self.worldbook_member_inject_limit = self._cfg_int(c, "worldbook_member_inject_limit", 6, 1, 20)
    self.worldbook_config_paths = self._cfg_str(c, "worldbook_config_paths", "")
    self.group_interject_provider_id = self._cfg_str(c, "GROUP_INTERJECT_PROVIDER_ID", "")
    self.group_episode_provider_id = self._cfg_str(c, "GROUP_EPISODE_PROVIDER_ID", "")
    self.group_slang_provider_id = self._cfg_str(c, "GROUP_SLANG_PROVIDER_ID", "")
    self.group_followup_judge_provider_id = self._cfg_str(c, "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID", "")
    self.group_member_safety_provider_id = self._cfg_str(c, "GROUP_MEMBER_SAFETY_PROVIDER_ID", "")
    self.enable_livingmemory_integration = self._cfg_bool(c, "enable_livingmemory_integration", True)
    self.livingmemory_tool_name = self._cfg_str(c, "livingmemory_tool_name", "recall_long_term_memory", "recall_long_term_memory")
    self.memory_companion_context_timeout_seconds = self._cfg_float(c, "memory_companion_context_timeout_seconds", 1.2, 0.2)
    self.enable_memory_companion_emotional_drift = self._cfg_bool(c, "enable_memory_companion_emotional_drift", True)
    self.enable_memory_companion_cross_window_emotion = self._cfg_bool(c, "enable_memory_companion_cross_window_emotion", True)
    self.enable_memory_companion_dream_fragment = self._cfg_bool(c, "enable_memory_companion_dream_fragment", True)
    self.enable_memory_companion_open_loop_search = self._cfg_bool(c, "enable_memory_companion_open_loop_search", True)
    self.enable_memory_companion_feature_context = self._cfg_bool(c, "enable_memory_companion_feature_context", True)
    self.enable_memory_companion_private_recall = self._cfg_bool(c, "enable_memory_companion_private_recall", True)
    self.memory_companion_context_top_k = self._cfg_int(c, "memory_companion_context_top_k", 5, 1, 10)
    self.memory_companion_context_max_chars = self._cfg_int(c, "memory_companion_context_max_chars", 900, 240, 1800)
    self.enable_bilibili_integration = self._cfg_bool(c, "enable_bilibili_integration", True)
    self.enable_bilibili_boredom_watch = self._cfg_bool(c, "enable_bilibili_boredom_watch", True)
    self.bilibili_boredom_min_interval_hours = self._cfg_int(c, "bilibili_boredom_min_interval_hours", 8, 2, 72)
    self.bilibili_share_probability = self._cfg_unit_interval(c, "bilibili_share_probability", 0.35, 0.0)
    self.bilibili_share_min_score = self._cfg_int(c, "bilibili_share_min_score", 7, 0, 10)
    self.enable_news_integration = self._cfg_bool(c, "enable_news_integration", False)
    self.enable_news_boredom_read = self._cfg_bool(c, "enable_news_boredom_read", True)
    self.enable_news_daily_hot_read = self._cfg_bool(c, "enable_news_daily_hot_read", self._cfg_bool(c, "enable_hot_trend_sources", True))
    self.news_min_interval_hours = self._cfg_int(c, "news_min_interval_hours", 6, 1, 72)
    self.news_share_probability = self._cfg_unit_interval(c, "news_share_probability", 0.22, 0.0)
    self.enable_external_event_self_link = self._cfg_bool(c, "enable_external_event_self_link", True)
    self.external_event_self_link_probability = self._cfg_unit_interval(c, "external_event_self_link_probability", 0.62, 0.0)
    self.external_event_self_link_cooldown_hours = self._cfg_int(c, "external_event_self_link_cooldown_hours", 12, 1, 168)
    self.external_link_share_cooldown_hours = self._cfg_int(c, "external_link_share_cooldown_hours", 72, 0, 168)
    self.news_max_items_per_source = self._cfg_int(c, "news_max_items_per_source", 5, 1, 20)
    self.external_event_idle_minutes = self._cfg_int(c, "external_event_idle_minutes", 90, 5, 1440)
    self.external_event_idle_strong_minutes = self._cfg_int(c, "external_event_idle_strong_minutes", 20, 1, 1440)
    self.news_share_cooldown_hours = self._cfg_int(c, "news_share_cooldown_hours", 8, 0, 168)
    self.bilibili_share_cooldown_hours = self._cfg_int(c, "bilibili_share_cooldown_hours", 10, 0, 168)
    self.web_exploration_share_cooldown_hours = self._cfg_int(c, "web_exploration_share_cooldown_hours", 10, 0, 168)
    self.external_event_self_link_override_min_relevance = self._cfg_int(c, "external_event_self_link_override_min_relevance", 0, 0, 10)
    self.external_event_self_link_override_min_desire = self._cfg_int(c, "external_event_self_link_override_min_desire", 0, 0, 10)
    self.external_event_self_link_override_probability = self._cfg_unit_interval(c, "external_event_self_link_override_probability", 0.6, 0.0)
    self.external_event_share_min_total = self._cfg_int(c, "external_event_share_min_total", 18, 0, 100)
    self.news_hot_sources = self._cfg_str(c, "news_hot_sources", self._cfg_str(c, "hot_trend_sources", "weibo,hackernews"))
    self.news_hot_max_items = self._cfg_int(c, "news_hot_max_items", self._cfg_int(c, "hot_trend_max_items", 12, 3, 30), 3, 30)
    self.enable_ai_daily_watch = self._cfg_bool(c, "enable_ai_daily_watch", True)
    self.ai_daily_sources = self._cfg_str(c, "ai_daily_sources", DEFAULT_AI_DAILY_SOURCES)
    self.ai_daily_source_uid = re.sub(r"\D+", "", self._cfg_str(c, "ai_daily_source_uid", "285286947")) or "285286947"
    self.ai_daily_prefer_text_version = self._cfg_bool(c, "ai_daily_prefer_text_version", True)
    self.news_sources = self._cfg_str(
        c,
        "news_sources",
        DEFAULT_NEWS_SOURCES,
    )
    if str(self.news_sources or "").strip() in {LEGACY_DEFAULT_NEWS_SOURCES, PREVIOUS_TECH_DEFAULT_NEWS_SOURCES}:
        self.news_sources = DEFAULT_NEWS_SOURCES
    self.news_provider_id = self._cfg_str(c, "NEWS_PROVIDER_ID", "")
    self.enable_web_exploration = self._cfg_bool(c, "enable_web_exploration", False)
    self.enable_web_exploration_boredom_search = self._cfg_bool(c, "enable_web_exploration_boredom_search", True)
    self.web_exploration_min_interval_hours = self._cfg_int(c, "web_exploration_min_interval_hours", 8, 1, 168)
    self.web_exploration_share_probability = self._cfg_unit_interval(c, "web_exploration_share_probability", 0.18, 0.0)
    self.web_exploration_max_results = self._cfg_int(c, "web_exploration_max_results", 6, 3, 20)
    self.web_exploration_interests = self._cfg_str(
        c,
        "web_exploration_interests",
        "按 Bot 人格自行决定；可偏向最近聊天、日程、人设兴趣、作品、技术、生活小知识、流行梗、时讯、新鲜事物。",
    )
    self.web_exploration_provider_id = self._cfg_str(c, "WEB_EXPLORATION_PROVIDER_ID", "")
    self.web_exploration_api_base_url = self._cfg_str(c, "WEB_EXPLORATION_API_BASE_URL", "")
    self.web_exploration_api_key = self._cfg_str(c, "WEB_EXPLORATION_API_KEY", "")
    self.web_exploration_api_model = self._cfg_str(c, "WEB_EXPLORATION_API_MODEL", "")
    self.enable_qzone_integration = self._cfg_bool(c, "enable_qzone_integration", True)
    self.qzone_cookie = self._cfg_str(c, "QZONE_COOKIE", "")
    self.enable_qzone_life_publish = self._cfg_bool(c, "enable_qzone_life_publish", False)
    self.qzone_life_publish_min_interval_hours = self._cfg_int(c, "qzone_life_publish_min_interval_hours", 24, 4, 168)
    self.qzone_life_publish_probability = self._cfg_unit_interval(c, "qzone_life_publish_probability", 0.18, 0.0)
    self.qzone_life_publish_max_daily = self._cfg_int(c, "qzone_life_publish_max_daily", 1, 1)
    self.qzone_life_publish_window_mode = self._cfg_str(c, "qzone_life_publish_window_mode", "template_double")
    self.qzone_life_publish_windows = self._cfg_str(c, "qzone_life_publish_windows", "")
    self.qzone_life_publish_allow_insomnia_night = self._cfg_bool(
        c,
        "qzone_life_publish_allow_insomnia_night",
        False,
    )
    self.qzone_life_publish_intra_day_gap_minutes = self._cfg_int(
        c,
        "qzone_life_publish_intra_day_gap_minutes",
        45,
        0,
        1440,
    )
    self.qzone_life_publish_double_windows = self._cfg_str(
        c,
        "qzone_life_publish_double_windows",
        "07:00-10:00\n18:00-22:00",
    )
    self.qzone_life_publish_custom_windows = self._cfg_str(c, "qzone_life_publish_custom_windows", "")
    self.qzone_life_publish_similarity_threshold = self._cfg_int(c, "qzone_life_publish_similarity_threshold", 2, 1, 20)
    self.qzone_publish_style_prompt = self._cfg_str(c, "qzone_publish_style_prompt", "")
    self.enable_qzone_generated_image_publish = self._cfg_bool(c, "enable_qzone_generated_image_publish", True)
    self.qzone_generated_image_probability = self._cfg_unit_interval(c, "qzone_generated_image_probability", 0.25, 0.0)
    self.qzone_publish_image_style_prompt = self._cfg_str(c, "qzone_publish_image_style_prompt", "")
    self.enable_qzone_comment_inbox = self._cfg_bool(c, "enable_qzone_comment_inbox", False)
    self.qzone_comment_inbox_interval_minutes = self._cfg_int(c, "qzone_comment_inbox_interval_minutes", 60, 5, 1440)
    self.qzone_comment_inbox_recent_posts = self._cfg_int(c, "qzone_comment_inbox_recent_posts", 5, 1, 20)
    self.qzone_comment_inbox_max_replies_per_tick = self._cfg_int(c, "qzone_comment_inbox_max_replies_per_tick", 1, 1, 5)
    self.enable_qzone_emotional_vent_publish = self._cfg_bool(c, "enable_qzone_emotional_vent_publish", False)
    self.qzone_emotional_vent_threshold = self._cfg_int(c, "qzone_emotional_vent_threshold", 90, 40, 100)
    self.qzone_emotional_vent_cooldown_hours = self._cfg_int(c, "qzone_emotional_vent_cooldown_hours", 72, 4, 336)
    self.qzone_emotional_vent_probability = self._cfg_unit_interval(c, "qzone_emotional_vent_probability", 0.35, 0.0)
    self.plugin_vision_provider_id = self._cfg_str(c, "PLUGIN_VISION_PROVIDER_ID", "")
    self._apply_quick_provider_defaults()
    self.group_episode_refresh_minutes = self._cfg_int(c, "group_episode_refresh_minutes", 180, 30, 1440)
    self.group_slang_summary_minutes = self._cfg_int(c, "group_slang_summary_minutes", 360, 60, 2880)
    self.max_group_topic_threads = self._cfg_int(c, "max_group_topic_threads", 12, 3, 40)
    self.max_group_episodes = self._cfg_int(c, "max_group_episodes", 10, 3, 40)
    self.max_group_relationship_edges = self._cfg_int(c, "max_group_relationship_edges", 80, 10, 300)
    # Backward-compatible aliases for stored daily plans and older code paths.
    self.allow_photo_text_action = self.enable_photo_text_action
    self.allow_screen_peek_action = self.enable_screen_glance_action
    self.allow_poke_action = self.enable_poke_action
    self.allow_voice_action = self.enable_voice_action

@story_startup_sync_operation("startup.store-persona-load")
def initialize_plugin_runtime(self: Any) -> None:
    # These references are process-local capabilities. Never inherit them from
    # mixin class attributes or a previous hot-reloaded plugin instance.
    self._bridge_cache = None
    # Fence HDSI plans created by a previous plugin instance or hot reload.
    self.hdsi_runtime_generation = uuid.uuid4().hex
    self._bridge_cache_ts = 0.0
    self._bridge_last_status = {}
    self._bridge_dependency_failure_until = 0.0
    self._bridge_dependency_failure_module = ""
    self._memory_companion_emotion_capability_bridge = None
    self._memory_companion_emotion_producer_capability_cache = None
    self._patch_livingmemory_processor_compat()
    self._report_integrated_feature_conflicts()
    self._data_lock = asyncio.Lock()
    self._daily_state_generation_lock = asyncio.Lock()
    self._daily_diary_generation_lock = asyncio.Lock()
    self._daily_review_generation_lock = asyncio.Lock()
    self._conversation_db_lock = asyncio.Lock()
    self._framework_agent_lock = asyncio.Lock()
    self._stop_event = asyncio.Event()
    self._task: asyncio.Task | None = None
    self._default_persona_prompt_cache = ""
    self._default_persona_prompt_cache_at = 0.0
    self._default_persona_prompt_cache_umo = ""
    self._default_persona_prompt_cache_persona_id = ""
    self._default_persona_prompt_refresh_task: asyncio.Task | None = None
    self._default_persona_prompt_cache_by_scope: dict[str, dict[str, Any]] = {}
    self._default_persona_prompt_refresh_tasks: dict[str, asyncio.Task] = {}
    self._passive_light_injection_cache: dict[str, Any] = {}
    self._passive_state_session_cache: dict[str, dict[str, Any]] = {}
    self._data_save_task: asyncio.Task | None = None
    self._data_save_dirty: dict[str, int] = {}
    self._data_save_deleted: dict[str, int] = {}
    self._data_save_dirty_since: dict[str, float] = {}
    self._data_save_section_revisions: dict[str, int] = {}
    self._data_save_full_revision = 0
    self._data_save_full_since = 0.0
    self._data_save_revision = 0
    self._data_save_max_delay_seconds = 2.0
    self._data_save_retry_base_seconds = 3.0
    self._data_save_retry_max_seconds = 30.0
    self._persona_data_save_tasks: dict[str, asyncio.Task] = {}
    self._persona_data_save_dirty: dict[str, dict[str, int]] = {}
    self._persona_data_save_deleted: dict[str, dict[str, int]] = {}
    self._persona_data_save_dirty_since: dict[str, dict[str, float]] = {}
    self._persona_data_save_section_revisions: dict[str, dict[str, int]] = {}
    self._persona_data_save_full_revision: dict[str, int] = {}
    self._persona_data_save_revision: dict[str, int] = {}
    self._maintenance_failure_cooldowns: dict[str, dict[str, Any]] = {}
    self._framework_captured_send_cache: dict[str, list[Any]] = {}
    self._framework_captured_send_cache_at: dict[str, float] = {}
    self._framework_deferred_photo_cache: dict[str, dict[str, Any]] = {}
    self._framework_deferred_photo_cache_at: dict[str, float] = {}
    self._segmented_reply_remainder_locks: dict[str, asyncio.Lock] = {}
    self._last_input_status_at: dict[str, float] = {}
    self._passive_input_status_tasks: dict[str, asyncio.Task] = {}
    self._recent_inbound_activity_by_scope: dict[str, dict[str, Any]] = {}
    # Monotonic per-conversation turn marker used to stop delayed TTS/remainder
    # tasks from delivering content belonging to an older inbound message.
    self._reply_turn_generation_by_scope: dict[str, int] = {}
    self._recent_outfit_command_sends: dict[str, float] = {}
    self._startup_maintenance_task: asyncio.Task | None = None
    self._startup_background_tasks: dict[str, asyncio.Task] = {}
    self._lifecycle_background_tasks: dict[asyncio.Task, str] = {}
    self._group_image_understanding_tasks: dict[str, dict[str, Any]] = {}
    self._qzone_last_bot = None
    startup_load_started = time.perf_counter()
    self.data = self._load_data_sync()
    timezone_reconciler = getattr(self, "_invalidate_timezone_derived_state", None)
    if callable(timezone_reconciler):
        runtime_state = (
            self.data.get("proactive_runtime")
            if isinstance(self.data.get("proactive_runtime"), dict)
            else {}
        )
        timezone_result = timezone_reconciler(
            str(runtime_state.get("window_timezone") or ""),
            str(getattr(self, "environment_perception_timezone", "") or ""),
            schedule_save=False,
        )
        timezone_sections = set(timezone_result.get("sections") or [])
        if timezone_result.get("changed") and timezone_sections:
            self._save_data_sync(sections=timezone_sections)
    manager = getattr(self, "store_manager", None)
    next_revision = getattr(manager, "next_revision", None)
    if callable(next_revision):
        try:
            self._data_save_revision = max(
                int(self._data_save_revision or 0),
                max(0, int(next_revision()) - 1),
            )
        except Exception:
            pass
    retire_legacy_routing = getattr(self, "_retire_legacy_persona_routing_sync", None)
    if callable(retire_legacy_routing):
        try:
            retire_legacy_routing()
        except Exception as exc:
            logger.warning(
                "旧人格路由停用失败，保留原数据等待下次启动重试: %s",
                _single_line(exc, 180),
            )
    migrate_profiles = getattr(self, "_migrate_persona_profiles_sync", None)
    if callable(migrate_profiles):
        try:
            self._persona_settings_migration_status = migrate_profiles()
        except Exception as exc:
            self._persona_settings_migration_status = {
                "ok": False,
                "migrated": [],
                "degraded": ["startup"],
                "skipped": [],
                "error": _single_line(exc, 180),
            }
            logger.warning(
                "启动人格配置迁移失败: %s",
                _single_line(exc, 180),
            )
    self._body_monitor_integration = BodyMonitorIntegration(self)
    self._apply_tts_runtime_overrides()
    load_elapsed_ms = int((time.perf_counter() - startup_load_started) * 1000)
    if load_elapsed_ms > 1200:
        logger.warning("启动读取数据耗时较高: elapsed=%sms", load_elapsed_ms)
    self._proactive_chat_runtime_bridge = ProactiveChatRuntimeBridge(self)
    self.page_api = None
    self.standalone_webui = None
    self._patch_astrbot_plugin_page_asset_token_compat()
    self._register_page_api_if_available()


def initialize_plugin_post_runtime_state(self: Any, config: Any) -> None:
    self.enable_p5_source_observer = self._cfg_bool(config, "enable_p5_source_observer", False)
    self.enable_p5_b1_recall_gate = self._cfg_bool(config, "enable_p5_b1_recall_gate", False)
    self.enable_p5_b1_bridge_gate = self._cfg_bool(config, "enable_p5_b1_bridge_gate", False)
    self.p5_attestation_registry = P5AttestationRegistry()
    self._bot_personal_outboxes = {}
    self._bot_personal_outbox = None
    outbox_getter = getattr(self, "_memory_companion_outbox", None)
    if callable(outbox_getter):
        self._bot_personal_outbox = outbox_getter()
    self.unified_person_registry = UnifiedPersonRegistry(self.data)
    self.req041_migration_coordinator = MigrationCoordinator(self.data_dir)
    self.req041_migration_outbox = MigrationOutbox(
        Path(self.data_dir) / "req041_migration_outbox.db"
    )
    self.req041_migration_status = {
        "required": False,
        "state": "uninitialized",
        "code": "migration_not_started",
    }
    self.req041_migration_backfill = None
    self.req041_relationship_store = None
    self.req041_dual_write_producer = None
    self.req041_scoped_projection_sync = None
    self.req041_scoped_projection_status = {
        "ok": False, "code": "scoped_projection_not_initialized", "scopes": []
    }
    self._req041_scoped_sync_task = None
    self._req041_scoped_sync_requested = False
