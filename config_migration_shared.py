# -*- coding: utf-8 -*-
"""config_migration 域的跨模块共享件（import 绑定 + 模块级常量）。

由 tmp/split4/mod_split.py 从 config_migration.py 机械抽取：
10 条 import 语句 + 18 个模块级常量，逐字节原样。
宿主 config_migration.py 与各 config_migration_partNN.py 均从本模块 import，
本模块不 import 任何同族模块（叶子模块，杜绝循环 import）。
"""

from __future__ import annotations

import asyncio

from copy import deepcopy

import json

import os

import re

from pathlib import Path

from typing import Any

from .photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    legacy_photo_generation_scope_limits,
    normalize_photo_generation_scope_limit,
)

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

LEGACY_PROACTIVE_ACTIONS_KEY = "enabled_proactive_actions"

# These fields were removed from the active configuration surface.  Keep the
# cleanup here so existing configs do not retain stale root or grouped copies.
OBSOLETE_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "enable_persona_standardization_experiment",
        "enable_llm_timer_scheduling",
        "ai_daily_check_window",
        "ai_daily_check_interval_minutes",
    }
)

LEGACY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "target_group_ids": ("group_whitelist_ids",),
    "timezone": ("environment_perception_timezone",),
    "enable_maintenance_token_saver": ("enable_daily_token_soft_limit",),
    "maintenance_token_soft_limit": ("daily_token_soft_limit",),
    "DIARY_PROVIDER_ID": ("DREAM_DIARY_PROVIDER_ID",),
    "DREAM_PROVIDER_ID": ("DREAM_DIARY_PROVIDER_ID",),
    "COMFYUI_PHOTO_WORKFLOW_NAME": ("COMFYUI_TEXT2IMG_WORKFLOW_NAME", "COMFYUI_SELFIE_WORKFLOW_NAME"),
    "allow_photo_text_action": ("enable_photo_text_action",),
    "allow_screen_peek_action": ("enable_screen_glance_action",),
    "allow_poke_action": ("enable_poke_action",),
    "allow_voice_action": ("enable_voice_action",),
    # 和风天气预警早期草案使用 api_key 命名；统一迁移到凭据字段，
    # 读取层会按格式选择 JWT 或 API Key，避免升级后已配置的凭据失效。
    "weather_alert_api_key": ("weather_alert_token",),
    "creative_base_chars_per_hour": ("creative_chars_per_session",),
    "enable_hot_trend_sources": ("enable_news_daily_hot_read",),
    "hot_trend_sources": ("news_hot_sources",),
    "hot_trend_max_items": ("news_hot_max_items",),
    "enable_reading_archive_integration": ("enable_reading_archive_integration",),
    "enable_reading_archive_boredom_read": ("enable_reading_archive_boredom_read",),
    "reading_archive_min_interval_hours": ("reading_archive_min_interval_hours",),
    "reading_archive_max_photo_count": ("reading_archive_max_photo_count",),
    "reading_archive_share_probability": ("reading_archive_share_probability",),
    "reading_archive_default_keywords": ("reading_archive_default_keywords",),
    "reading_archive_blocked_tags": ("reading_archive_blocked_tags",),
    "READING_ARCHIVE_VISION_PROVIDER_ID": ("READING_ARCHIVE_VISION_PROVIDER_ID",),
    "reading_archive_vision_enabled": ("enable_reading_archive_vision",),
    "reading_archive_comments_enabled": ("enable_reading_archive_page_comments",),
    "reading_archive_rating_enabled": ("enable_reading_archive_rating",),
    "auto_japanese_voice_enabled": ("auto_voice_enabled",),
    "auto_japanese_voice_full_conversion_enabled": ("auto_voice_full_conversion_enabled",),
    "auto_japanese_voice_probability": ("auto_voice_probability",),
    "auto_japanese_voice_max_chars": ("auto_voice_max_chars",),
    "auto_japanese_voice_cooldown_seconds": ("auto_voice_cooldown_seconds",),
    "auto_japanese_voice_admin_probability": ("main_user_voice_probability",),
    "admin_mention_keyword_voice_keywords": ("main_user_mention_voice_keywords",),
    "admin_mention_keyword_voice_probability": ("main_user_mention_voice_probability",),
    "admin_mention_keyword_voice_prompt": ("main_user_mention_voice_prompt",),
    "enable_response_self_review": ("enable_passive_response_review", "enable_proactive_message_review"),
    "response_review_mode": ("passive_review_mode",),
}

# Independent from the legacy LivingMemory compatibility switch.
MEMORY_COMPANION_BRIDGE_KEY = "enable_memory_companion_bridge"

LEGACY_PROACTIVE_ACTION_FLAG_KEYS: dict[str, str] = {
    "photo_text": "enable_photo_text_action",
    "screen_peek": "enable_screen_glance_action",
    "screen_glance": "enable_screen_glance_action",
    "poke": "enable_poke_action",
    "voice": "enable_voice_action",
}

PRECISION_PROVIDER_MODE_KEYS: tuple[str, ...] = (
    "MAI_STYLE_PROVIDER_ID",
    "DAILY_PLAN_PROVIDER_ID",
    "DETAIL_ENHANCEMENT_PROVIDER_ID",
    "DREAM_DIARY_PROVIDER_ID",
    "CREATIVE_PROVIDER_ID",
    "CREATIVE_OUTLINE_PROVIDER_ID",
    "CREATIVE_REVIEW_PROVIDER_ID",
    "VOICE_PROMPT_PROVIDER_ID",
    "tts_conversion_provider_id",
    "PHOTO_PROMPT_PROVIDER_ID",
    "NARRATION_PROVIDER_ID",
    "HISTORY_SUMMARY_PROVIDER_ID",
    "RESPONSE_REVIEW_PROVIDER_ID",
    "SMART_SILENCE_PROVIDER_ID",
    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
    "TROUBLESHOOTING_PROVIDER_ID",
    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
    "REST_WAKEUP_PROVIDER_ID",
    "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
    "EMOTION_JUDGEMENT_PROVIDER_ID",
    "COMPANION_MEMORY_PROVIDER_ID",
    "DIALOGUE_EPISODE_PROVIDER_ID",
    "GROUP_INTERJECT_PROVIDER_ID",
    "GROUP_EPISODE_PROVIDER_ID",
    "GROUP_SLANG_PROVIDER_ID",
    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
    "FORWARD_MESSAGE_PROVIDER_ID",
    "NEWS_PROVIDER_ID",
    "WEB_EXPLORATION_PROVIDER_ID",
)

# QWeather is the default for new weather configurations.  Keep the old
# provider values valid so an explicit legacy choice continues to work.
QWEATHER_DEFAULT_SOURCE = "qweather"

_WEATHER_SOURCE_ALIASES: dict[str, str] = {
    "qweather": "qweather",
    "q-weather": "qweather",
    "q weather": "qweather",
    "和风": "qweather",
    "和风天气": "qweather",
    "openweathermap": "openweathermap",
    "open-weather-map": "openweathermap",
    "openmeteo": "openmeteo",
    "open-meteo": "openmeteo",
    "amap": "amap",
    "高德": "amap",
    "高德地图": "amap",
}

_WEATHER_SOURCE_VALUES = frozenset(_WEATHER_SOURCE_ALIASES.values())

_QWEATHER_GENERIC_FALLBACKS: dict[str, tuple[str, ...]] = {
    "weather_api_host": ("weather_alert_api_host",),
    "weather_token": ("weather_alert_token", "weather_alert_api_key"),
}

# Only migrate values that were persisted identically in both the legacy flat
# copy and the visible schema group. A disagreement means one side may contain
# a deliberate user choice, so the normal group-authority rules handle it.
LEGACY_DEFAULT_VALUE_MIGRATIONS: dict[str, tuple[Any, Any]] = {
    "forward_message_image_vision_timeout_seconds": (6.0, 60.0),
}

# v6.0.7 repurposed the old custom-stage-policy switch as the relationship
# system master switch.  In older releases ``false`` meant "use the built-in
# policy", so treating that value as a hard off switch would silently disable
# relationship accounting for existing installations.  Keep a private,
# one-time marker outside the public schema so a later explicit dashboard
# change to ``false`` remains authoritative.
_RELATIONSHIP_SWITCH_MIGRATION_MARKER = "_relationship_switch_semantics_version"

_RELATIONSHIP_SWITCH_MIGRATION_VERSION = 1

# v6.0.9 changes the user-request photo quota from ``0 = unlimited`` to
# ``-1 = unlimited`` and ``0 = disabled``. Keep this one-shot so a later
# explicit administrator choice of zero remains authoritative.
_COMMAND_PHOTO_QUOTA_MIGRATION_MARKER = "_command_photo_quota_semantics_version"

_COMMAND_PHOTO_QUOTA_MIGRATION_VERSION = 1

# v6.1.2 replaces the scope allow-list with four independent daily quotas.
_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER = "_photo_generation_scope_quota_semantics_version"

_PHOTO_SCOPE_QUOTA_MIGRATION_VERSION = 1
