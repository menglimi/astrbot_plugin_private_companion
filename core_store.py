# -*- coding: utf-8 -*-
"""
CoreStoreMixin — 配置、数据存储、用户/群组基础访问
"""
from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import html
import importlib
import inspect
import json
import math
import os
import random
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid
import zoneinfo
from collections.abc import Collection, Mapping
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core.db.po import Conversation
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    import chinese_calendar as calendar_cn
except Exception:
    calendar_cn = None

try:
    from lunarcalendar import Converter, Solar
except Exception:
    Converter = None
    Solar = None

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    DEFAULT_HUMANIZED_STATE,
    PLUGIN_NAME,
    DATA_VERSION,
    PROACTIVE_ABILITY_REGISTRY,
    VOICE_FALLBACK_TEMPLATES,
    TIMER_TAG_PATTERN,
    SUPPORTED_TIMER_FORMATS,
    _ACTION_TEXT,
    _DATA_STORE_KEYS,
    _DEFAULT_GROUP_TEMPLATE,
    _DEFAULT_USER_TEMPLATE,
    _REASON_TEXT,
    _SIMULATION_FALLBACK_EVENTS,
)
from .dreaming import (
    build_dream_memory_fragments,
    dream_fragment_effective_weight,
    dream_theme_specs,
    extract_weighted_dream_fragments,
    fallback_diary_payload,
    fallback_dream_fragments_for_diary,
    generate_daily_diary,
    generate_enhanced_dream_pick,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .helpers import (
    _date_key,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
    _strip_persisted_chat_control_tags,
    _today_key,
    normalize_photo_generation_scopes,
)
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .config_migration import _ensure_config_parent_dir
from .relationship_ledger import (
    apply_natural_relationship_decay,
    apply_relationship_event,
    clamp_relationship_positive_stage_cap,
    migrate_legacy_relationship_score,
    normalize_relationship_mode,
    normalize_relationship_positive_stage_cap_key,
)
from .storage.store_manager import StoreManager
from .storage.path_generation import capture_write_ticket, replace_if_ticket_current
from .person_context_contract import empty_person_store, ensure_person_store
from .photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LABELS,
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    PHOTO_GENERATION_SCOPES,
    normalize_photo_generation_scope_limit,
)
from .persona_config import runtime_persona_setting
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_sync_operation,
    story_startup_sync_operation,
)
from .story_handoff import (
    STORY_MIGRATION_COMMIT_KEY,
    preflight_story_handoff_sections,
)
from .unified_profile_service import (
    DEFAULT_CLOSED_REPAIR_OPERATION_ID,
    ensure_legacy_profile_capabilities,
    ensure_new_profile_capabilities,
    migrate_legacy_capabilities,
    repair_default_closed_capabilities,
)
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


DEFAULT_AI_DAILY_NEWS_SOURCE = "B站 AI早报|bilibili:285286947"

DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
        DEFAULT_AI_DAILY_NEWS_SOURCE,
    ]
)

LEGACY_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
    ]
)

PREVIOUS_TECH_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)



_LUNAR_MONTH_NAMES = [
    "正月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "冬月",
    "腊月",
]
_LUNAR_DAY_NAMES = [
    "初一",
    "初二",
    "初三",
    "初四",
    "初五",
    "初六",
    "初七",
    "初八",
    "初九",
    "初十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
    "廿一",
    "廿二",
    "廿三",
    "廿四",
    "廿五",
    "廿六",
    "廿七",
    "廿八",
    "廿九",
    "三十",
]
_SOLAR_TERM_DATES = {
    (1, 5): "小寒",
    (1, 20): "大寒",
    (2, 4): "立春",
    (2, 19): "雨水",
    (3, 5): "惊蛰",
    (3, 20): "春分",
    (4, 4): "清明",
    (4, 20): "谷雨",
    (5, 5): "立夏",
    (5, 21): "小满",
    (6, 5): "芒种",
    (6, 21): "夏至",
    (7, 7): "小暑",
    (7, 22): "大暑",
    (8, 7): "立秋",
    (8, 23): "处暑",
    (9, 7): "白露",
    (9, 23): "秋分",
    (10, 8): "寒露",
    (10, 23): "霜降",
    (11, 7): "立冬",
    (11, 22): "小雪",
    (12, 7): "大雪",
    (12, 22): "冬至",
}
_ALMANAC_YI = ["整理房间", "写字", "散步", "读书", "听歌", "轻度创作", "复盘", "安静休息"]
_ALMANAC_JI = ["熬夜", "冲动发言", "硬撑", "反复纠结", "过度解释", "临时加压", "情绪化决定"]
_PLATFORM_DISPLAY_NAMES = {
    "aiocqhttp": "QQ",
    "qq": "QQ",
    "onebot": "QQ",
    "telegram": "Telegram",
    "wechat": "微信",
    "discord": "Discord",
}

# Durable top-level sections are intentionally explicit.  Save requests may
# only name sections registered here; legacy roots that exist only in a live
# snapshot are handled by explicit full-scope migration/reset paths.
_DURABLE_SECTION_NAMES = frozenset(
    {
        "version",
        "primary_store_ownership",
        "users",
        "private_user_alias_merge_backups",
        "groups",
        "persona_routing_warnings",
        "hdsi_trial_observations",
        "hdsi_event_ledger",
        "hdsi_actor_state",
        "daily_plan",
        "daily_plan_history",
        "agenda_version",
        "agenda_contract_version",
        "observed_activities",
        "calendar_version",
        "calendar_events",
        "calendar_rules",
        "calendar_exceptions",
        "calendar_candidates",
        "calendar_observations",
        "place_cognitive_maps",
        "reality_touch_outputs",
        "window_snapshots",
        "agenda_reconciliation_history",
        "daily_state",
        "daily_weather",
        "state_conditions",
        "state_generated_day",
        "body_cycle_state",
        "body_cycle_strategy_mode",
        "bot_diaries",
        "dream_fragments",
        "daily_dream",
        "diary_generated_day",
        "daily_diary_deleted_days",
        "daily_diary_delete_revision",
        "daily_diary_failed_day",
        "daily_diary_failed_at",
        "daily_diary_last_error",
        "daily_diary_postprocess_error",
        "daily_outfit_photo",
        "daily_outfit_history",
        "dialogue_outfit_override",
        "recent_photo_generations",
        "recent_photo_continuity",
        "daily_story_plan",
        "daily_story_plan_history",
        "bot_personal_outbox",
        "bot_personal_archive_revisions",
        "skill_growth",
        "detail_enhanced_day",
        "detail_enhanced_segments",
        "detail_enhanced_history",
        "schedule_adjustments",
        "yesterday_conversation_summary",
        "can_do",
        "important_dates",
        "qq_presence_state",
        "token_usage",
        "bilibili_integration",
        "news_integration",
        "web_exploration",
        "qzone_integration",
        "reading_archive_integration",
        "bookshelf_items",
        "bookshelf_secret",
        "bookshelf_store_revision",
        "memo_notes",
        "creative_projects",
        "creative_memory_pool",
        STORY_MIGRATION_COMMIT_KEY,
        "proactive_candidate_pool",
        "proactive_runtime",
        "proactive_review_runtime",
        "proactive_audit_log",
        "passive_no_reply_records",
        "external_proactive_abilities",
        "external_event_pool",
        "external_event_self_link_cache",
        "expression_learning_runtime",
        "expression_voice_profile",
        "extension_migration_notice_preferences",
        "boundary_feedback_reports",
        "boundary_feedback_vent_history",
        "worldbook_entries",
        "worldbook_member_profiles",
        "worldbook_group_profiles",
        "worldbook_deleted_member_ids",
        "worldbook_deleted_group_ids",
        "photo_reference_assets",
        "worldbook_import_state",
        "runtime_settings",
        "manual_diagnosis_pending_config",
        "manual_diagnosis_recent_context",
        "atrelay_send_log",
        "inbound_debounce_stats",
        "smart_message_debounce",
        "group_llm_reply_blocks",
        "reaction_expression_group_states",
        "cache_metrics",
        "persona_lifecycle",
        "balance_awareness",
        "qweather_location",
        "weather_alerts",
        "weather_alert_awareness",
        "body_monitor_integration",
        "environment_change_awareness",
        "personal_goal_state",
        "personal_goals",
        "food_menu",
        "hunger_window_attempts",
        "last_food_state_feedback_at",
        "last_food_state_feedback_text",
        "live_stream_companion",
        "pending_atrelay_receipts",
        "pending_atrelay_requests",
        "personality_iteration_auto_tune",
        "private_image_vision_cache",
        "private_image_visual_provider_state",
        "proactive_only_temp_unlocks",
        "photo_generation_scope_attempts",
        "photo_reference_feedback",
        "reality_touch",
        "recent_atrelay_contexts",
        "recent_prompt_injection_events",
        "recent_prompt_injections",
        "social_fact_sanitized_at",
        "screen_diary_context",
        "self_meal_log",
        "setup_guide_completed_at",
        "setup_guide_completed_version",
        "troubleshooting_suppressed_warning_types",
        "web_search_runtime",
        "daily_review_reports",
        "daily_review_active_guidance",
        "daily_review_last_attempt",
        "daily_review_completed_day",
        "daily_review_case_audit",
        "troubleshooting_test_results",
        "req036_capability_migration",
        "unified_person",
        "_req041_memory_scope_state",
        # Derived maintenance markers are persisted with their source section.
        "proactive_candidate_repeat_sanitized_at",
        "_req041_expression_promotion_operations",
        "_req041_group_reset_sagas",
        "_req041_private_memory",
        "_req041_persona_expression_profile",
        "_req041_persona_reset_saga",
    }
)

_FULL_SAVE_SCOPES = frozenset(
    {
        "startup_migration",
        "startup_maintenance",
        "explicit_reset",
        "shutdown_flush",
        "admin_import_export",
    }
)

_EVENT_DATA_SAVE_BATCH: ContextVar[dict[str, Any] | None] = ContextVar(
    "private_companion_event_data_save_batch",
    default=None,
)
_EVENT_DATA_SAVE_BATCH_ATTR = "_private_companion_event_data_save_batch"





from .core_store_cleanup_compact import CoreStoreCleanupCompactMixin
from .core_store_event_batch_startup import CoreStoreEventBatchStartupMixin
from .core_store_identity_merge import CoreStoreIdentityMergeMixin
from .core_store_new_store_defaults import CoreStoreNewStoreDefaultsMixin
from .core_store_private_user_state import CoreStorePrivateUserStateMixin
from .core_store_save_state_capture import CoreStoreSaveStateCaptureMixin
from .core_store_sync_io import CoreStoreSyncIoMixin
from .core_store_user_profile_scope import CoreStoreUserProfileScopeMixin
from .core_store_write_schedule import CoreStoreWriteScheduleMixin

class CoreStoreMixin(
    CoreStoreCleanupCompactMixin,
    CoreStoreEventBatchStartupMixin,
    CoreStoreIdentityMergeMixin,
    CoreStoreNewStoreDefaultsMixin,
    CoreStorePrivateUserStateMixin,
    CoreStoreSaveStateCaptureMixin,
    CoreStoreSyncIoMixin,
    CoreStoreUserProfileScopeMixin,
    CoreStoreWriteScheduleMixin,
):
    """配置、数据存储、用户/群组基础访问"""

import time  # re-export for tests patching core_store.time.monotonic
