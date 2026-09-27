# -*- coding: utf-8 -*-
"""plugin_bootstrap 拆分件 part04：插件引导初始化（机械搬移，行为不变）。

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
from .plugin_bootstrap_part01 import _legacy_photo_scene_preset_names
from .plugin_bootstrap_shared import logger



def _initialize_photo_and_expression_config(self: Any, c: Any) -> None:
    self._external_image_download_session = None
    self._external_image_download_session_lock = None
    self._external_image_download_session_trust_env = None
    self.enable_backup_external_image_api = self._cfg_bool(c, "enable_backup_external_image_api", False)
    self.backup_external_image_api_platform = self._cfg_str(
        c, "backup_external_image_api_platform", "auto", "auto"
    ).strip().lower()
    self.backup_external_image_api_base_url = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "")
    self.backup_external_image_api_key = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_KEY", "")
    self.backup_external_image_api_model = self._cfg_str(c, "BACKUP_EXTERNAL_IMAGE_API_MODEL", "")
    self.backup_external_image_api_size = self._cfg_str(c, "backup_external_image_api_size", "1024x1024", "1024x1024")
    self.backup_external_image_api_timeout_seconds = self._cfg_int(c, "backup_external_image_api_timeout_seconds", 180, 20, 600)
    self.backup_external_image_api_custom_headers = self._cfg_str(c, "backup_external_image_api_custom_headers", "")
    raw_external_image_endpoints = self._cfg_raw(c, "external_image_api_endpoints", [])
    self.external_image_api_endpoints = (
        [dict(item) for item in raw_external_image_endpoints if isinstance(item, dict)]
        if isinstance(raw_external_image_endpoints, list)
        else []
    )
    self.photo_generation_prompt_format = self._normalize_photo_generation_prompt_format(
        self._cfg_str(c, "photo_generation_prompt_format", "traditional", "traditional")
    )
    self.photo_generation_style = self._cfg_str(c, "photo_generation_style", "真实", "真实")
    self.photo_generation_style_custom_prompt = self._cfg_str(c, "photo_generation_style_custom_prompt", "")
    self.photo_generation_negative_prompt_mode = self._normalize_photo_generation_negative_prompt_mode(
        self._cfg_str(c, "photo_generation_negative_prompt_mode", "safe_default", "safe_default")
    )
    self.photo_generation_negative_prompt = self._cfg_str(
        c, "photo_generation_negative_prompt", ""
    )
    self.photo_generation_text2img_negative_prompt = self._cfg_str(
        c, "photo_generation_text2img_negative_prompt", ""
    )
    self.photo_generation_selfie_negative_prompt = self._cfg_str(
        c, "photo_generation_selfie_negative_prompt", ""
    )
    self.photo_generation_edit_negative_prompt = self._cfg_str(
        c, "photo_generation_edit_negative_prompt", ""
    )
    self.photo_generation_fixed_prompt = self._cfg_str(c, "photo_generation_fixed_prompt", "")
    self.photo_generation_text2img_fixed_prompt = self._cfg_str(
        c, "photo_generation_text2img_fixed_prompt", ""
    )
    self.photo_generation_selfie_fixed_prompt = self._cfg_str(
        c, "photo_generation_selfie_fixed_prompt", ""
    )
    self.photo_generation_edit_fixed_prompt = self._cfg_str(
        c, "photo_generation_edit_fixed_prompt", ""
    )
    self.photo_generation_scene_presets = self._cfg_raw(c, "photo_generation_scene_presets", "")
    self.enable_bot_relationship_network = self._cfg_bool(c, "enable_bot_relationship_network", False)
    self.bot_relationship_cards = self._normalize_bot_relationship_cards(
        self._cfg_raw(c, "bot_relationship_cards", [])
    )
    raw_reference_catalog = self._cfg_raw(c, "photo_reference_catalog", [])
    raw_reference_catalog_version = self._cfg_raw(c, "photo_reference_catalog_version", 0)
    raw_reference_catalog_user_cleared = self._cfg_bool(
        c,
        "photo_reference_catalog_user_cleared",
        False,
    )
    # This is a compatibility projection only. Scene-preset interpretation and
    # ongoing catalog ownership now live in Image Companion.
    legacy_preset_names = _legacy_photo_scene_preset_names(self.photo_generation_scene_presets)
    loaded_reference_catalog = load_catalog(
        raw_reference_catalog,
        catalog_version=raw_reference_catalog_version,
        legacy_persona=self.photo_persona_reference_image_path,
        legacy_library=self.photo_reference_library,
        user_cleared=raw_reference_catalog_user_cleared,
        preset_names=legacy_preset_names,
    )
    self.photo_reference_catalog = loaded_reference_catalog.references
    self.photo_reference_catalog_read_only = loaded_reference_catalog.read_only
    try:
        self.photo_reference_catalog_version = int(raw_reference_catalog_version or 0)
    except (TypeError, ValueError):
        self.photo_reference_catalog_version = 0
    self.photo_reference_catalog_user_cleared = raw_reference_catalog_user_cleared
    self._startup_photo_reference_catalog_migration_pending = False
    for warning in loaded_reference_catalog.warnings:
        logger.warning("参考图目录加载警告: %s", warning)
    if loaded_reference_catalog.needs_persist:
        self.photo_reference_catalog_read_only = True
        try:
            serialized_reference_catalog = validate_and_serialize(
                loaded_reference_catalog.references,
                preset_names=legacy_preset_names,
            )
            if _set_into_config(c, "photo_reference_catalog", serialized_reference_catalog):
                _set_into_config(c, "photo_reference_catalog_user_cleared", False)
                self.photo_reference_catalog_user_cleared = False
                self._startup_photo_reference_catalog_migration_pending = True
                self._startup_config_migration_changes += 1
            else:
                logger.error("参考图目录迁移无法写入配置，启动期间继续使用旧配置的只读内存投影")
        except Exception as exc:
            logger.error(
                "参考图目录迁移失败，启动期间继续使用旧配置的只读内存投影: %s",
                _single_line(exc, 180),
                exc_info=True,
            )
    self.enable_daily_outfit_photo = self._cfg_bool(c, "enable_daily_outfit_photo", False)
    self.enable_creative_cover_generation = self._cfg_bool(c, "enable_creative_cover_generation", False)
    self.daily_outfit_photo_prompt = self._cfg_str(c, "daily_outfit_photo_prompt", "")
    self.daily_outfit_rotation_days = self._cfg_int(c, "daily_outfit_rotation_days", 10, 1, 30)
    self.enable_wardrobe = self._cfg_bool(c, "enable_wardrobe", True)
    self.wardrobe_tendency = self._cfg_str(c, "wardrobe_tendency", "")
    wardrobe_items_raw = self._cfg_raw(c, "wardrobe_items", [])
    self.wardrobe_items = normalize_wardrobe_items(wardrobe_items_raw)
    self.enable_wardrobe_prompt = self._cfg_bool(c, "enable_wardrobe_prompt", True)
    self.wardrobe_prompt_max_items = self._cfg_int(
        c, "wardrobe_prompt_max_items", WARDROBE_PROMPT_MAX_ITEMS, 1, WARDROBE_MAX_ITEMS
    )
    self.wardrobe_image_max_count = self._cfg_int(c, "wardrobe_image_max_count", 3, 1, 8)
    self.wardrobe_image_prompt = normalize_wardrobe_image_prompt(
        self._cfg_str(c, "wardrobe_image_prompt", "")
    )
    self.wardrobe_vision_provider_id = self._cfg_str(c, "WARDROBE_VISION_PROVIDER_ID", "")
    self.wardrobe_outfit_mode = self._cfg_str(c, "wardrobe_outfit_mode", "select").strip().lower()
    if self.wardrobe_outfit_mode not in {"inventory", "select"}:
        self.wardrobe_outfit_mode = "select"
    self.wardrobe_outfit_rotation_days = self._cfg_int(c, "wardrobe_outfit_rotation_days", 7, 1, 30)
    self.enable_wardrobe_outfit_generate = self._cfg_bool(
        c, "enable_wardrobe_outfit_generate", False
    )
    self.wardrobe_outfit_provider_id = self._cfg_str(c, "WARDROBE_OUTFIT_PROVIDER_ID", "")
    self.wardrobe_outfits = normalize_wardrobe_outfits(
        self._cfg_raw(c, "wardrobe_outfits", [])
    )
    # 这两个键此前只进了 schema 与面板白名单，没有落成实例属性。单人格（默认）下
    # get_persona_setting 取不到属性就直接返回调用方默认值 —— 症状是「面板点保存当场
    # 生效，重启后静默失效」，衣柜接管与渐进披露都不会真正启用。
    self.wardrobe_injection_detail = self._cfg_str(c, "wardrobe_injection_detail", "full")
    self.wardrobe_photo_source = self._cfg_str(c, "wardrobe_photo_source", "builtin")
    self.enable_natural_language_photo_generation = self._cfg_bool(c, "enable_natural_language_photo_generation", False)
    self.natural_language_photo_generation_mode = self._cfg_str(
        c,
        "natural_language_photo_generation_mode",
        "tool_first",
        "tool_first",
    ).strip().lower()
    if self.natural_language_photo_generation_mode not in {"tool_first", "rule_fast", "off"}:
        self.natural_language_photo_generation_mode = "tool_first"
    self.enable_user_requested_photo_generation = self._cfg_bool(
        c,
        "enable_user_requested_photo_generation",
        True,
    )
    self.allow_generate_photo_on_reaction_turns = self._cfg_bool(
        c,
        "allow_generate_photo_on_reaction_turns",
        False,
    )
    self.command_photo_generation_max_daily = self._cfg_int(c, "command_photo_generation_max_daily", -1, -1, 100)
    self.photo_generation_trace_max_size_kb = self._cfg_int(
        c,
        "photo_generation_trace_max_size_kb",
        0,
        0,
        102400,
    )
    self.photo_generation_trace_backup_count = self._cfg_int(
        c,
        "photo_generation_trace_backup_count",
        5,
        0,
        20,
    )
    self.natural_language_photo_generation_max_daily = self._cfg_int(c, "natural_language_photo_generation_max_daily", 2, 0, 100)
    raw_natural_photo_extra = _flat_get(c, "natural_language_photo_extra_prompt", None)
    self.natural_language_photo_extra_prompt = (
        DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
        if raw_natural_photo_extra is None
        else str(raw_natural_photo_extra).strip()
    )
    self.enable_weather_context = self._cfg_bool(c, "enable_weather_context", True)
    self.weather_source = self._cfg_str(c, "weather_source", "qweather").lower()
    if self.weather_source not in {"qweather", "openweathermap", "amap", "openmeteo"}:
        self.weather_source = "qweather"
    self.weather_api_key = self._cfg_str(c, "weather_api_key", "")
    self.weather_city = self._cfg_str(c, "weather_city", "")
    self.weather_amap_api_key = self._cfg_str(c, "weather_amap_api_key", "")
    self.weather_amap_city = self._cfg_str(c, "weather_amap_city", "")
    raw_weather_location = _flat_get(c, "weather_location", "")
    self.weather_location = str(raw_weather_location or "").strip()
    self.weather_lat = self._cfg_float(c, "weather_lat", 0.0, -90.0)
    self.weather_lon = self._cfg_float(c, "weather_lon", 0.0, -180.0)
    self.weather_refresh_minutes = self._cfg_int(c, "weather_refresh_minutes", 90, 10, 720)
    self.enable_weather_alerts = self._cfg_bool(c, "enable_weather_alerts", False)
    configured_weather_host = self._cfg_str(c, "weather_api_host", "").rstrip("/")
    configured_weather_token = self._cfg_str(c, "weather_token", "")
    legacy_weather_alert_host = self._cfg_str(c, "weather_alert_api_host", "").rstrip("/")
    legacy_weather_alert_token = self._cfg_str(c, "weather_alert_api_key", "")
    configured_alert_token = self._cfg_str(c, "weather_alert_token", "")
    # The generic QWeather fields are shared by ordinary weather and
    # alerts.  Keep the old alert names as read-time fallbacks so an
    # existing installation does not need to be reconfigured at once.
    self.weather_api_host = configured_weather_host or legacy_weather_alert_host
    self.weather_token = configured_weather_token or configured_alert_token or legacy_weather_alert_token
    self.weather_alert_api_host = legacy_weather_alert_host or self.weather_api_host
    self.weather_alert_token = configured_alert_token or configured_weather_token or legacy_weather_alert_token
    # Expose the old attribute as a runtime alias for integrations written
    # against the early api_key draft; the persisted field remains token.
    self.weather_alert_api_key = (
        legacy_weather_alert_token or self.weather_alert_token
    )
    self.weather_alert_refresh_minutes = self._cfg_int(c, "weather_alert_refresh_minutes", 10, 5, 60)
    self.weather_alert_min_severity = self._normalize_weather_alert_min_severity(
        self._cfg_str(c, "weather_alert_min_severity", "blue", "blue")
    )
    self.enable_environment_change_proactive = self._cfg_bool(c, "enable_environment_change_proactive", True)
    self.environment_change_check_minutes = self._cfg_int(c, "environment_change_check_minutes", 10, 5, 60)
    self.environment_change_cooldown_minutes = self._cfg_int(c, "environment_change_cooldown_minutes", 90, 20, 360)
    self.enable_yesterday_screen_diary_context = self._cfg_bool(c, "enable_yesterday_screen_diary_context", True)
    self.screen_diary_context_max_chars = self._cfg_int(c, "screen_diary_context_max_chars", 700, 200, 1600)
    self.detail_enhancement_lead_minutes = self._cfg_int(c, "detail_enhancement_lead_minutes", 3, 0, 180)
    self.enable_daily_diary = self._cfg_bool(c, "enable_daily_diary", True)
    self.daily_diary_time = self._cfg_str(c, "daily_diary_time", "23:10")
    self.daily_diary_form = self._cfg_str(c, "daily_diary_form", "auto")
    self.daily_diary_length = self._cfg_str(c, "daily_diary_length", "standard")
    self.daily_diary_creativity = self._cfg_str(c, "daily_diary_creativity", "balanced")
    self.daily_diary_custom_direction = self._cfg_str(c, "daily_diary_custom_direction", "")
    self.daily_diary_generate_share_seed = self._cfg_bool(c, "daily_diary_generate_share_seed", True)
    self.max_diary_entries = self._cfg_int(c, "max_diary_entries", 14, 1, 60)
    self.enable_daily_review = self._cfg_bool(c, "enable_daily_review", True)
    self.daily_review_time = self._cfg_str(c, "daily_review_time", "04:00")
    self.daily_review_retention_days = self._cfg_int(c, "daily_review_retention_days", 30, 3, 180)
    self.daily_review_auto_apply_guidance = self._cfg_bool(c, "daily_review_auto_apply_guidance", True)
    self.enable_daily_case_review_experiment = self._cfg_bool(
        c, "enable_daily_case_review_experiment", False
    )
    self.important_date_lookahead_days = self._cfg_int(c, "important_date_lookahead_days", 7, 0, 60)
    legacy_actions = self._parse_action_list(self._cfg_raw(c, "enabled_proactive_actions", None))
    legacy_photo_enabled = "photo_text" in legacy_actions if legacy_actions else True
    legacy_screen_enabled = "screen_peek" in legacy_actions if legacy_actions else False
    legacy_poke_enabled = "poke" in legacy_actions if legacy_actions else False
    legacy_voice_enabled = "voice" in legacy_actions if legacy_actions else False
    self.enable_photo_text_action = self._cfg_bool(
        c, "enable_photo_text_action", bool(self._cfg_raw(c, "allow_photo_text_action", legacy_photo_enabled))
    )
    self.photo_generation_allowed_scopes = {}
    for scope, key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items():
        limit = self._cfg_int(c, key, -1, -1, 100)
        setattr(self, key, limit)
        self.photo_generation_allowed_scopes[scope] = limit
    self.enable_photo_reference_image = self._cfg_bool(c, "enable_photo_reference_image", False)
    self.enable_group_nsfw_private_fallback = self._cfg_bool(c, "enable_group_nsfw_private_fallback", False)
    review_mode = self._cfg_str(c, "group_nsfw_image_review_mode", "single").strip().lower()
    self.group_nsfw_image_review_mode = review_mode if review_mode in {"single", "dual"} else "single"
    review_sensitivity = self._cfg_str(c, "group_nsfw_image_review_sensitivity", "balanced").strip().lower()
    self.group_nsfw_image_review_sensitivity = (
        review_sensitivity if review_sensitivity in {"relaxed", "balanced", "strict"} else "balanced"
    )
    self.group_nsfw_image_review_min_confidence = min(
        1.0,
        self._cfg_float(c, "group_nsfw_image_review_min_confidence", 0.7, 0.0),
    )
    self.group_nsfw_image_review_timeout_seconds = min(
        30.0,
        self._cfg_float(c, "group_nsfw_image_review_timeout_seconds", 8.0, 3.0),
    )
    self.group_nsfw_image_review_max_dimension = self._cfg_int(
        c, "group_nsfw_image_review_max_dimension", 1280, 0, 4096
    )
    review_failure_action = self._cfg_str(c, "group_nsfw_image_review_failure_action", "private").strip().lower()
    self.group_nsfw_image_review_failure_action = (
        review_failure_action if review_failure_action in {"private", "block"} else "private"
    )
    self.group_nsfw_image_review_custom_prompt = self._cfg_str(
        c, "group_nsfw_image_review_custom_prompt", ""
    )[:1200]
    self.enable_screen_glance_action = self._cfg_bool(
        c, "enable_screen_glance_action", bool(self._cfg_raw(c, "allow_screen_peek_action", legacy_screen_enabled))
    )
    self.enable_poke_action = self._cfg_bool(
        c, "enable_poke_action", bool(self._cfg_raw(c, "allow_poke_action", legacy_poke_enabled))
    )
    self.enable_voice_action = self._cfg_bool(
        c, "enable_voice_action", bool(self._cfg_raw(c, "allow_voice_action", legacy_voice_enabled))
    )
    self.enable_qq_presence_sync = self._cfg_bool(c, "enable_qq_presence_sync", True)
    self.enable_qq_custom_presence_sync = self._cfg_bool(c, "enable_qq_custom_presence_sync", False)
    self.poke_action_max_times = self._cfg_int(c, "poke_action_max_times", 1, 1, 3)
    self.poke_action_cooldown_minutes = self._cfg_int(c, "poke_action_cooldown_minutes", 30, 0, 1440)
    self.voice_action_max_chars = self._cfg_int(c, "voice_action_max_chars", 30, 6, 80)
    self.photo_action_max_daily = self._cfg_int(c, "photo_action_max_daily", 1, 0, 5)
    self.proactive_photo_text_probability = self._cfg_int(c, "proactive_photo_text_probability", 18, 0, 100) / 100
    self.screen_peek_max_daily = self._cfg_int(c, "screen_peek_max_daily", 1, 0, 5)
    self.screen_peek_cooldown_minutes = self._cfg_int(c, "screen_peek_cooldown_minutes", 240, 0, 1440)
    self.enable_goodnight_screen_check = self._cfg_bool(c, "enable_goodnight_screen_check", False)
    self.goodnight_screen_check_delay_minutes = self._cfg_int(
        c, "goodnight_screen_check_delay_minutes", 45, 1, 180
    )
    self.enable_unanswered_screen_peek_followup = self._cfg_bool(c, "enable_unanswered_screen_peek_followup", True)
    self.unanswered_screen_peek_after_minutes = self._cfg_int(c, "unanswered_screen_peek_after_minutes", 45, 10, 240)
    self.unanswered_screen_peek_cooldown_minutes = self._cfg_int(c, "unanswered_screen_peek_cooldown_minutes", 180, 30, 1440)
    self.enable_mai_style_integration = self._cfg_bool(c, "enable_mai_style_integration", True)
    self.enable_companion_memory = self._cfg_bool(c, "enable_companion_memory", True)
    self.enable_expression_learning = self._cfg_bool(c, "enable_expression_learning", True)
    self.expression_learning_mode = self._cfg_str(c, "expression_learning_mode", "balanced", "balanced").lower()
    if self.expression_learning_mode not in {"light", "balanced", "aggressive"}:
        self.expression_learning_mode = "balanced"
    self.expression_private_learning_source_mode = self._cfg_str(
        c, "expression_private_learning_source_mode", "owner", "owner"
    ).lower()
    if self.expression_private_learning_source_mode not in {"owner", "selected", "all"}:
        self.expression_private_learning_source_mode = "owner"
    self.expression_private_learning_source_ids = self._cfg_raw(c, "expression_private_learning_source_ids", [])
    self.expression_group_learning_source_mode = self._cfg_str(
        c, "expression_group_learning_source_mode", "disabled", "disabled"
    ).lower()
    if self.expression_group_learning_source_mode not in {"disabled", "selected", "all"}:
        self.expression_group_learning_source_mode = "disabled"
    self.expression_group_learning_source_ids = self._cfg_raw(c, "expression_group_learning_source_ids", [])
