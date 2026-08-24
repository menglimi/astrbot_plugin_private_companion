# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    legacy_photo_generation_scope_limits,
    normalize_photo_generation_scope_limit,
)

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


def migrate_flat_config_into_schema_groups(
    config: Any,
    *,
    schema_path: Path,
    logger: Any | None = None,
    save: bool = True,
) -> int:
    """Copy legacy flat config values into the new AstrBot schema groups."""
    try:
        return _migrate_flat_config_into_schema_groups(config, schema_path=schema_path, logger=logger, save=save)
    except Exception as exc:
        if logger is not None:
            logger.warning("[PrivateCompanion] 配置分组迁移失败，已跳过且不影响插件加载: %s", _single_line(exc, 160))
        return 0


def _migrate_flat_config_into_schema_groups(
    config: Any,
    *,
    schema_path: Path,
    logger: Any | None = None,
    save: bool = True,
) -> int:
    root = _config_root_mapping(config)
    if not isinstance(root, dict):
        return 0
    schema_map = _schema_group_items(schema_path, logger=logger)
    if not schema_map:
        return 0

    changed: list[str] = []
    legacy_group = root.get("legacy_compat_config")
    legacy_sources = [root]
    if isinstance(legacy_group, dict):
        legacy_sources.append(legacy_group)

    relationship_switch_changes = _migrate_relationship_switch_semantics(root, schema_map)
    changed.extend(relationship_switch_changes)

    photo_scope_quota_changes = _migrate_photo_scope_quota_semantics(root, schema_map)
    changed.extend(photo_scope_quota_changes)

    command_photo_quota_changes = _migrate_command_photo_quota_semantics(root, schema_map)
    changed.extend(command_photo_quota_changes)

    # 参考图目录升级需要先于通用的“分组值优先”处理。AstrBot 会为新版
    # 分组补上空默认值；这不代表用户主动清空，不能覆盖仍然非空的旧字段。
    photo_reference_changes = _preserve_legacy_photo_reference_config(
        root,
        schema_map,
        legacy_sources,
    )
    changed.extend(photo_reference_changes)

    # Resolve the weather provider and shared QWeather credentials before the
    # generic group-authority pass, while both raw grouped and flat values are
    # still available for the explicit-choice checks.
    weather_changes = _migrate_qweather_config(root, schema_map, legacy_sources)
    changed.extend(weather_changes)

    for key, (old_default, new_default) in LEGACY_DEFAULT_VALUE_MIGRATIONS.items():
        item = schema_map.get(key) or {}
        group = root.get(str(item.get("group") or ""))
        if not isinstance(group, dict) or key not in group or key not in root:
            continue
        flat_value = _coerce_schema_value(root.get(key), item)
        grouped_value = _coerce_schema_value(group.get(key), item)
        if flat_value != old_default or grouped_value != old_default:
            continue
        migrated_value = _coerce_schema_value(new_default, item)
        root[key] = migrated_value
        group[key] = migrated_value
        changed.append(f"{key}~legacy-default")

    for key, item in schema_map.items():
        if key == "provider_config_mode":
            continue
        if key not in root:
            continue
        group_key = str(item.get("group") or "")
        group = root.get(group_key)
        if isinstance(group, dict) and key in group:
            # The grouped value is what AstrBot's official config page exposes.
            # Once it exists it must be authoritative, including when the user
            # intentionally changes a setting back to its schema default.
            grouped_value = group.get(key)
            visible_value = _coerce_schema_value(grouped_value, item)
            if grouped_value != visible_value:
                group[key] = visible_value
                changed.append(f"{key}~schema-type")
            if root.get(key) != visible_value:
                root[key] = visible_value
                changed.append(f"{key}~group-authority")
            continue
        old_value = root.get(key)
        if old_value == item.get("default"):
            continue
        if _copy_into_schema_group(root, schema_map, key, old_value):
            changed.append(key)

    if _migrate_legacy_group_access_mode(root, schema_map):
        changed.append("require_target_group->group_access_mode")
    action_changes = _migrate_legacy_proactive_actions(root, schema_map, legacy_sources)
    changed.extend(action_changes)

    for old_key, new_keys in LEGACY_KEY_ALIASES.items():
        for source in legacy_sources:
            if old_key not in source:
                continue
            old_value = source.get(old_key)
            if _is_empty(old_value):
                continue
            for new_key in new_keys:
                if _copy_into_schema_group(root, schema_map, new_key, old_value):
                    changed.append(f"{old_key}->{new_key}")
                # Keep the hidden flat compatibility copy synchronized as well.
                # Without this, AstrBot may persist an empty default at the root
                # while integrations that read the flat key miss the migrated
                # credential (the grouped value remains authoritative for _flat_get).
                if old_key == "weather_alert_api_key":
                    target_item = schema_map.get(new_key) or {}
                    target_group = root.get(str(target_item.get("group") or ""))
                    if isinstance(target_group, dict) and new_key in target_group:
                        normalized = _coerce_schema_value(target_group.get(new_key), target_item)
                        if root.get(new_key) != normalized:
                            root[new_key] = normalized
                            changed.append(f"{new_key}~compat-sync")

    if _ensure_provider_config_mode(root, schema_map):
        changed.append("provider_config_mode~mode-infer")
    added_compat_defaults = _ensure_flat_schema_compat_defaults(root, schema_map)
    if added_compat_defaults:
        changed.extend(f"{key}~compat-default" for key in added_compat_defaults)
    removed_section_keys = _cleanup_legacy_section_markers(root)
    if removed_section_keys:
        changed.extend(f"{key}~section-cleanup" for key in removed_section_keys)
    roleplay_hint_changes = _migrate_legacy_roleplay_image_hint(root, schema_map)
    changed.extend(roleplay_hint_changes)

    # 旧别名键只负责迁移；仍在 schema 中登记的 flat 兼容键重置为默认值，
    # 避免 AstrBot 每次启动都反复补齐并刷屏。
    removed_legacy_keys: list[str] = []
    cleanup_keys = (
        set(LEGACY_KEY_ALIASES)
        | OBSOLETE_CONFIG_KEYS
        | {LEGACY_PROACTIVE_ACTIONS_KEY, "require_target_group"}
    )
    for old_key in cleanup_keys:
        item = schema_map.get(old_key)
        if old_key in root:
            if item:
                default_value = _coerce_schema_value(item.get("default"), item)
                if root.get(old_key) != default_value:
                    root[old_key] = default_value
                    removed_legacy_keys.append(old_key)
            else:
                root.pop(old_key, None)
                removed_legacy_keys.append(old_key)
        if isinstance(legacy_group, dict) and old_key in legacy_group:
            if item and str(item.get("group") or "") == "legacy_compat_config":
                default_value = _coerce_schema_value(item.get("default"), item)
                if legacy_group.get(old_key) != default_value:
                    legacy_group[old_key] = default_value
                    if old_key not in removed_legacy_keys:
                        removed_legacy_keys.append(old_key)
            else:
                legacy_group.pop(old_key, None)
                if old_key not in removed_legacy_keys:
                    removed_legacy_keys.append(old_key)
        if old_key in OBSOLETE_CONFIG_KEYS:
            for container in root.values():
                if isinstance(container, dict) and old_key in container:
                    container.pop(old_key, None)
                    if old_key not in removed_legacy_keys:
                        removed_legacy_keys.append(old_key)
    if removed_legacy_keys:
        changed.extend(f"{key}~cleanup" for key in removed_legacy_keys)

    if not changed:
        return 0
    if logger is not None:
        logger.info("[PrivateCompanion] 已将旧版扁平配置迁移到新版分组配置: %s 项", len(changed))
    if save:
        _save_config_after_schema_migration(config, logger=logger)
    return len(changed)


def _migrate_relationship_switch_semantics(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
) -> list[str]:
    """Preserve the pre-v6.0.7 meaning of the relationship switch once.

    The old key was a policy *selection* toggle whose default was ``false``;
    both values still left the built-in relationship ledger active.  The new
    release uses the same persisted key as a total enable/disable switch.
    Existing values therefore need a one-time normalization to ``true``.
    A private marker prevents a user's later explicit disable from being
    rewritten on every startup.
    """

    marker = root.get(_RELATIONSHIP_SWITCH_MIGRATION_MARKER)
    if marker == _RELATIONSHIP_SWITCH_MIGRATION_VERSION:
        return []
    key = "enable_custom_relationship_stage_policy"
    item = schema_map.get(key)
    if not isinstance(item, dict):
        return []
    group_key = str(item.get("group") or "")
    group = root.get(group_key) if group_key else None
    has_legacy_value = key in root or (isinstance(group, dict) and key in group)
    if not has_legacy_value:
        # A brand-new config will receive the schema default later; there is
        # no legacy choice to migrate and no marker to persist.
        return []

    changed: list[str] = []
    if root.get(key) is not True:
        root[key] = True
        changed.append(f"{key}~relationship-switch-v1")
    if isinstance(group, dict) and key in group and group.get(key) is not True:
        group[key] = True
        changed.append(f"{group_key}.{key}~relationship-switch-v1")
    if root.get(_RELATIONSHIP_SWITCH_MIGRATION_MARKER) != _RELATIONSHIP_SWITCH_MIGRATION_VERSION:
        root[_RELATIONSHIP_SWITCH_MIGRATION_MARKER] = _RELATIONSHIP_SWITCH_MIGRATION_VERSION
        changed.append(f"{_RELATIONSHIP_SWITCH_MIGRATION_MARKER}~set")
    return changed


def _migrate_command_photo_quota_semantics(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
) -> list[str]:
    """Upgrade the old unlimited zero once without rewriting future disables."""
    if root.get(_COMMAND_PHOTO_QUOTA_MIGRATION_MARKER) == _COMMAND_PHOTO_QUOTA_MIGRATION_VERSION:
        return []

    key = "command_photo_generation_max_daily"
    item = schema_map.get(key)
    if not isinstance(item, dict):
        return []
    group_key = str(item.get("group") or "")
    group = root.get(group_key) if group_key else None
    has_grouped_value = isinstance(group, dict) and key in group
    has_flat_value = key in root
    changed: list[str] = []
    raw_value = group.get(key) if has_grouped_value else root.get(key) if has_flat_value else None
    if (has_grouped_value or has_flat_value) and _coerce_schema_value(raw_value, item) == 0:
        migrated_value = _coerce_schema_value(-1, item)
        if root.get(key) != migrated_value:
            root[key] = migrated_value
            changed.append(f"{key}~quota-semantics-v1")
        if isinstance(group, dict) and group.get(key) != migrated_value:
            group[key] = migrated_value
            changed.append(f"{group_key}.{key}~quota-semantics-v1")

    root[_COMMAND_PHOTO_QUOTA_MIGRATION_MARKER] = _COMMAND_PHOTO_QUOTA_MIGRATION_VERSION
    changed.append(f"{_COMMAND_PHOTO_QUOTA_MIGRATION_MARKER}~set")
    return changed


def _migrate_photo_scope_quota_semantics(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
) -> list[str]:
    """Convert the legacy scope allow-list into independent daily quotas once."""
    if root.get(_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER) == _PHOTO_SCOPE_QUOTA_MIGRATION_VERSION:
        return []

    legacy_key = "photo_generation_allowed_scopes"
    legacy_item = schema_map.get(legacy_key) or {}
    legacy_group_key = str(legacy_item.get("group") or "photo_action_config")
    group = root.get(legacy_group_key)
    if not isinstance(group, dict):
        group = {}
        root[legacy_group_key] = group

    if legacy_key in group:
        raw_legacy = group.get(legacy_key)
    elif legacy_key in root:
        raw_legacy = root.get(legacy_key)
    else:
        raw_legacy = None
    legacy_limits = legacy_photo_generation_scope_limits(raw_legacy)

    # AstrBot adds newly introduced grouped fields with their schema defaults
    # before plugin startup.  Four default ``-1`` values therefore still mean
    # "migrate the legacy list".  A non-default new value, however, proves the
    # quota form has already been persisted and must remain authoritative even
    # if an older AstrBot build discarded the private migration marker.
    existing_limits: dict[str, int] = {}
    has_nondefault_new_value = False
    for scope, key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items():
        item = schema_map.get(key) or {}
        group_key = str(item.get("group") or "photo_action_config")
        target_group = root.get(group_key)
        if isinstance(target_group, dict) and key in target_group:
            raw_value = target_group.get(key)
        elif key in root:
            raw_value = root.get(key)
        else:
            continue
        value = normalize_photo_generation_scope_limit(raw_value)
        existing_limits[scope] = value
        default_value = normalize_photo_generation_scope_limit(item.get("default", -1))
        if value != default_value:
            has_nondefault_new_value = True

    limits = (
        {
            scope: existing_limits.get(scope, legacy_limits.get(scope, -1))
            for scope in PHOTO_GENERATION_SCOPE_LIMIT_KEYS
        }
        if has_nondefault_new_value
        else legacy_limits
    )

    changed: list[str] = []
    for scope, key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items():
        item = schema_map.get(key)
        if not isinstance(item, dict):
            continue
        value = normalize_photo_generation_scope_limit(limits.get(scope, -1))
        group_key = str(item.get("group") or "photo_action_config")
        target_group = root.get(group_key)
        if not isinstance(target_group, dict):
            target_group = {}
            root[group_key] = target_group
        if root.get(key) != value:
            root[key] = value
            changed.append(f"{key}~scope-quota-v1")
        if target_group.get(key) != value:
            target_group[key] = value
            changed.append(f"{group_key}.{key}~scope-quota-v1")

    root[_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER] = _PHOTO_SCOPE_QUOTA_MIGRATION_VERSION
    changed.append(f"{_PHOTO_SCOPE_QUOTA_MIGRATION_MARKER}~set")
    return changed


def _preserve_legacy_photo_reference_config(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> list[str]:
    """Keep non-empty legacy reference fields when the canonical catalog was lost."""
    catalog_item = schema_map.get("photo_reference_catalog") or {}
    group_key = str(catalog_item.get("group") or "")
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}

    user_cleared = any(
        _coerce_bool(source.get("photo_reference_catalog_user_cleared"))
        for source in (group, *legacy_sources)
        if isinstance(source, dict) and "photo_reference_catalog_user_cleared" in source
    )
    if user_cleared:
        return []

    changed: list[str] = []
    flat_catalog = next(
        (
            source.get("photo_reference_catalog")
            for source in legacy_sources
            if isinstance(source, dict) and not _is_empty(source.get("photo_reference_catalog"))
        ),
        None,
    )
    if flat_catalog is not None and _is_empty(group.get("photo_reference_catalog")):
        if group_key and root.get(group_key) is not group:
            root[group_key] = group
        group["photo_reference_catalog"] = _coerce_schema_value(flat_catalog, catalog_item)
        changed.append("photo_reference_catalog~flat-canonical-preserve")

    if not _is_empty(group.get("photo_reference_catalog")):
        return changed

    for key in ("photo_persona_reference_image_path", "photo_reference_library"):
        item = schema_map.get(key)
        if not item:
            continue
        legacy_value = next(
            (
                source.get(key)
                for source in legacy_sources
                if isinstance(source, dict) and not _is_empty(source.get(key))
            ),
            None,
        )
        if legacy_value is None:
            continue
        target_group_key = str(item.get("group") or "")
        target_group = root.get(target_group_key)
        if not isinstance(target_group, dict):
            target_group = {}
            root[target_group_key] = target_group
        if _is_empty(target_group.get(key)):
            target_group[key] = _coerce_schema_value(legacy_value, item)
            changed.append(f"{key}~legacy-reference-preserve")
    return changed


def _migrate_qweather_config(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> list[str]:
    """Migrate shared QWeather credentials and infer the weather provider.

    The active schema uses ``weather_api_host``/``weather_token`` for both
    current weather and alerts.  Older releases used alert-specific names, so
    those fields remain valid fallbacks.  Provider inference only applies when
    no explicit provider survives; old OpenWeather/Amap credentials then keep
    their original behavior, while otherwise new configs use QWeather.
    """

    changed: list[str] = []
    for target, fallbacks in _QWEATHER_GENERIC_FALLBACKS.items():
        value = _first_weather_configured_value(root, schema_map, (target, *fallbacks), legacy_sources)
        if value is None:
            continue
        changed.extend(_write_weather_schema_value(root, schema_map, target, value))
        for legacy_key in fallbacks:
            changed.extend(
                _clear_weather_compatibility_value(
                    root,
                    schema_map,
                    legacy_key,
                    legacy_sources,
                )
            )

    source_item = schema_map.get("weather_source")
    if not source_item:
        return changed
    resolved = _resolve_weather_source(root, schema_map, legacy_sources)
    if not resolved:
        return changed
    changed.extend(
        _write_weather_schema_value(
            root,
            schema_map,
            "weather_source",
            resolved,
            force=_is_empty_legacy_openweather_default(root, schema_map, legacy_sources),
        )
    )
    return changed


def _clear_weather_compatibility_value(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    key: str,
    legacy_sources: list[dict[str, Any]],
) -> list[str]:
    """Clear a consumed hidden alias so an intentional new-field reset sticks."""

    item = schema_map.get(key) or {}
    default = _coerce_schema_value(item.get("default", ""), item) if item else ""
    changed: list[str] = []
    group = _weather_schema_group(root, schema_map, key)
    if isinstance(group, dict) and key in group and group.get(key) != default:
        group[key] = default
        changed.append(f"{key}~qweather-alias-cleanup")
    for source in legacy_sources:
        if key in source and source.get(key) != default:
            source[key] = default
            changed.append(f"{key}~compat-cleanup")
    return changed


def _is_empty_legacy_openweather_default(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> bool:
    group = _weather_schema_group(root, schema_map, "weather_source")
    group_value = _normalize_weather_source(group.get("weather_source")) if isinstance(group, dict) else ""
    root_value = _normalize_weather_source(root.get("weather_source"))
    return (
        group_value == "openweathermap"
        and root_value in {"", "openweathermap"}
        and _infer_legacy_weather_source(root, schema_map, legacy_sources) == QWEATHER_DEFAULT_SOURCE
    )


def _resolve_weather_source(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> str:
    """Return the effective provider without overwriting visible choices."""

    item = schema_map.get("weather_source") or {}
    group = _weather_schema_group(root, schema_map, "weather_source")
    group_present = isinstance(group, dict) and "weather_source" in group
    root_present = "weather_source" in root
    group_value = _normalize_weather_source(group.get("weather_source")) if group_present else ""
    root_value = _normalize_weather_source(root.get("weather_source")) if root_present else ""
    schema_default = _normalize_weather_source(item.get("default")) or QWEATHER_DEFAULT_SOURCE
    inferred = _infer_legacy_weather_source(root, schema_map, legacy_sources)

    # Any visible grouped value is authoritative, including an intentional
    # reset to the QWeather default.  The one exception is the old
    # OpenWeatherMap default when both grouped and flat copies agree but no
    # OpenWeather-specific setting was ever configured; that is an inherited
    # default rather than a useful provider choice.
    if group_present and group_value:
        if (
            group_value == "openweathermap"
            and root_value in {"", "openweathermap"}
            and inferred == QWEATHER_DEFAULT_SOURCE
        ):
            return QWEATHER_DEFAULT_SOURCE
        return group_value
    if root_value and root_value != QWEATHER_DEFAULT_SOURCE:
        if root_value == "openweathermap" and inferred == QWEATHER_DEFAULT_SOURCE:
            return QWEATHER_DEFAULT_SOURCE
        return root_value
    if group_present and not group_value and root_value:
        return root_value
    if root_present and root_value:
        # A root-only QWeather value can be a hidden default from an older
        # config writer.  Legacy provider credentials are a stronger signal.
        if inferred != QWEATHER_DEFAULT_SOURCE and schema_default == QWEATHER_DEFAULT_SOURCE:
            return inferred
        return root_value
    return inferred


def _infer_legacy_weather_source(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> str:
    """Infer a pre-QWeather source from its provider-specific fields."""

    amap_keys = ("weather_amap_api_key", "weather_amap_city")
    openweather_keys = ("weather_api_key", "weather_city")
    if _has_weather_configured_value(root, schema_map, amap_keys, legacy_sources):
        return "amap"
    if _has_weather_configured_value(root, schema_map, openweather_keys, legacy_sources):
        return "openweathermap"
    return QWEATHER_DEFAULT_SOURCE


def _first_weather_configured_value(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    keys: tuple[str, ...],
    legacy_sources: list[dict[str, Any]],
) -> Any:
    """Find the first non-empty value, preferring the visible group."""

    for key in keys:
        for value in _weather_config_values(root, schema_map, key, legacy_sources):
            if not _is_empty(value):
                return value
    return None


def _has_weather_configured_value(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    keys: tuple[str, ...],
    legacy_sources: list[dict[str, Any]],
) -> bool:
    return _first_weather_configured_value(root, schema_map, keys, legacy_sources) is not None


def _weather_config_values(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    key: str,
    legacy_sources: list[dict[str, Any]],
) -> list[Any]:
    values: list[Any] = []
    group = _weather_schema_group(root, schema_map, key)
    if isinstance(group, dict) and key in group:
        values.append(group.get(key))
    if key in root:
        values.append(root.get(key))
    for source in legacy_sources[1:]:
        if key in source:
            values.append(source.get(key))
    return values


def _weather_schema_group(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, Any] | None:
    item = schema_map.get(key) or {}
    group_key = str(item.get("group") or "")
    group = root.get(group_key) if group_key else None
    return group if isinstance(group, dict) else None


def _write_weather_schema_value(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    key: str,
    value: Any,
    *,
    force: bool = False,
) -> list[str]:
    """Write a migrated value to its group and synchronized flat copy."""

    item = schema_map.get(key)
    if not item:
        return []
    normalized = _coerce_schema_value(value, item)
    default = _coerce_schema_value(item.get("default"), item)
    group_key = str(item.get("group") or "")
    if not group_key:
        return []
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}
        root[group_key] = group

    changed: list[str] = []
    existing = group.get(key)
    if force or key not in group or _is_empty(existing) or existing == default:
        if existing != normalized:
            group[key] = normalized
            changed.append(f"{key}~qweather-migrate")
    else:
        # The visible grouped value remains authoritative once configured.
        normalized = _coerce_schema_value(existing, item)

    if root.get(key) != normalized:
        root[key] = normalized
        changed.append(f"{key}~compat-sync")
    return changed


def _normalize_weather_source(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _WEATHER_SOURCE_ALIASES.get(text, text if text in _WEATHER_SOURCE_VALUES else "")


def _ensure_provider_config_mode(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> bool:
    item = schema_map.get("provider_config_mode")
    if not item:
        return False
    group_key = str(item.get("group") or "")
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}
        root[group_key] = group

    root_mode = _normalize_provider_config_mode_value(root.get("provider_config_mode"))
    group_mode = _normalize_provider_config_mode_value(group.get("provider_config_mode"))
    quick_keys = (
        "FAST_RESPONSE_PROVIDER_ID",
        "COMPLEX_REASONING_PROVIDER_ID",
        "CREATIVE_MODEL_PROVIDER_ID",
        "PLUGIN_VISION_PROVIDER_ID",
    )
    has_quick_provider = _has_any_configured_provider(root, group, quick_keys)
    has_precision_provider = _has_any_configured_provider(root, group, PRECISION_PROVIDER_MODE_KEYS)
    explicit = ""
    if group_mode and root_mode and group_mode != root_mode:
        # Official AstrBot config pages save the visible schema group first.
        # When it disagrees with the hidden flat compatibility key, prefer what
        # the user can actually see and just sync the hidden copy afterward.
        explicit = group_mode
    elif group_mode:
        if not root_mode and group_mode == "quick" and has_precision_provider and not has_quick_provider:
            explicit = ""
        else:
            explicit = group_mode
    elif root_mode:
        explicit = root_mode
    if explicit:
        changed = False
        if group.get("provider_config_mode") != explicit:
            group["provider_config_mode"] = explicit
            changed = True
        if root.get("provider_config_mode") != explicit:
            root["provider_config_mode"] = explicit
            changed = True
        return changed

    inferred = "precision" if has_precision_provider else "quick"
    group["provider_config_mode"] = inferred
    root["provider_config_mode"] = inferred
    return True


def _normalize_provider_config_mode_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "quick": "quick",
        "fast": "quick",
        "simple": "quick",
        "快速": "quick",
        "快速配置": "quick",
        "precision": "precision",
        "precise": "precision",
        "advanced": "precision",
        "detail": "precision",
        "detailed": "precision",
        "精准": "precision",
        "精准配置": "precision",
        "分流": "precision",
        "分流模型": "precision",
    }
    return aliases.get(text, "")


def _has_any_configured_provider(
    root: dict[str, Any],
    mode_group: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    for key in keys:
        if str(mode_group.get(key) or "").strip():
            return True
        if str(root.get(key) or "").strip():
            return True
        for value in root.values():
            if isinstance(value, dict) and str(value.get(key) or "").strip():
                return True
    return False


def _cleanup_flat_schema_item_keys(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    removed: list[str] = []
    for key in list(root.keys()):
        if key not in schema_map:
            continue
        item = schema_map.get(key) or {}
        group_key = str(item.get("group") or "")
        if not group_key:
            continue
        group = root.get(group_key)
        if not isinstance(group, dict):
            group = {}
            root[group_key] = group
        if key not in group and root.get(key) != item.get("default"):
            group[key] = _coerce_schema_value(root.get(key), item)
        root.pop(key, None)
        removed.append(key)
    return removed


def _ensure_flat_schema_compat_defaults(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    added: list[str] = []
    for key, item in schema_map.items():
        if key in root:
            continue
        group_key = str(item.get("group") or "")
        if not group_key:
            continue
        root[key] = _coerce_schema_value(item.get("default"), item)
        added.append(key)
    return added


def _cleanup_legacy_section_markers(root: dict[str, Any]) -> list[str]:
    removed: list[str] = []
    for key in list(root.keys()):
        if str(key).startswith("_section_"):
            root.pop(key, None)
            removed.append(str(key))
    return removed


def _migrate_legacy_roleplay_image_hint(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    image_item = schema_map.get("private_image_self_recognition_hint") or {}
    image_group_key = str(image_item.get("group") or "")
    profile_item = schema_map.get("roleplay_user_profile_prompt") or {}
    profile_group_key = str(profile_item.get("group") or "")
    if not image_group_key or not profile_group_key:
        return []
    image_group = root.get(image_group_key)
    if not isinstance(image_group, dict):
        return []
    raw_hint = str(image_group.get("private_image_self_recognition_hint") or "").strip()
    if not raw_hint:
        return []
    split = _split_legacy_roleplay_image_hint(raw_hint)
    user_text = "\n".join(split["user"]).strip()
    image_text = "\n".join(split["image"]).strip()
    if not user_text:
        return []
    profile_group = root.get(profile_group_key)
    if not isinstance(profile_group, dict):
        profile_group = {}
        root[profile_group_key] = profile_group
    old_profile = str(profile_group.get("roleplay_user_profile_prompt") or "").strip()
    new_profile = _append_unique_text(old_profile, user_text)
    changed: list[str] = []
    if new_profile != old_profile:
        profile_group["roleplay_user_profile_prompt"] = new_profile[:2000]
        changed.append("private_image_self_recognition_hint->roleplay_user_profile_prompt")
    if image_text != raw_hint:
        image_group["private_image_self_recognition_hint"] = image_text[:1200]
        changed.append("private_image_self_recognition_hint~user-profile-cleanup")
    return changed


def _split_legacy_roleplay_image_hint(text: str) -> dict[str, list[str]]:
    user_lines: list[str] = []
    image_lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _looks_like_user_profile_line(line):
            user_lines.append(raw_line.strip())
        else:
            image_lines.append(raw_line.strip())
    return {"user": user_lines, "image": image_lines}


def _looks_like_user_profile_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(token in lower for token in ("user profile", "user_profile", "master profile")):
        return True
    if re.search(r"(对用户的称呼|用户[：:的]|主人[：:的]|主人的|用户性别|用户生日|用户年龄|用户职业|用户身份|用户资料|用户设定|用户画像|用户偏好|用户边界|用户关系|用户称呼|称呼用户|如何称呼用户|与用户关系|和用户关系|彼此关系|相处方式|关系补充|是角色的XX|与角色的相处方式)", text):
        return True
    label = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    if label in {"称呼", "昵称", "性别", "生日", "年龄", "职业", "专业", "身份", "关系", "边界", "偏好", "是角色的XX", "与角色的相处方式", "其他补充信息"}:
        return True
    if re.search(r"(专业是|职业是|生日是|性别是|称呼.*主人|叫.*主人|把用户|用户是|主人是)", text):
        return True
    return False


def _append_unique_text(existing: str, addition: str) -> str:
    existing = str(existing or "").strip()
    addition_lines = [line.strip() for line in str(addition or "").splitlines() if line.strip()]
    if not addition_lines:
        return existing
    if not existing:
        return "\n".join(addition_lines)
    existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
    existing_set = set(existing_lines)
    merged = list(existing_lines)
    for line in addition_lines:
        if line not in existing_set:
            merged.append(line)
            existing_set.add(line)
    return "\n".join(merged)


def _migrate_legacy_group_access_mode(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> bool:
    raw = root.get("require_target_group")
    legacy_group = root.get("legacy_compat_config")
    if raw is None and isinstance(legacy_group, dict):
        raw = legacy_group.get("require_target_group")
    if raw is None:
        return False
    require_target_group = _coerce_bool(raw)
    mode = "whitelist" if require_target_group else "blacklist"
    return _copy_into_schema_group(root, schema_map, "group_access_mode", mode)


def _migrate_legacy_proactive_actions(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> list[str]:
    raw = _first_present_value(legacy_sources, LEGACY_PROACTIVE_ACTIONS_KEY)
    actions = _parse_legacy_action_list(raw)
    if not actions:
        return []
    changed: list[str] = []
    for action, new_key in LEGACY_PROACTIVE_ACTION_FLAG_KEYS.items():
        enabled = action in actions
        if _copy_into_schema_group(root, schema_map, new_key, enabled):
            changed.append(f"{LEGACY_PROACTIVE_ACTIONS_KEY}->{new_key}")
    return changed


def _first_present_value(sources: list[dict[str, Any]], key: str) -> Any:
    for source in sources:
        if key in source:
            return source.get(key)
    return None


def _parse_legacy_action_list(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            raw_parts = parsed
        else:
            raw_parts = re.split(r"[,\s、;；|]+", text)
    elif isinstance(raw, list):
        raw_parts = raw
    elif isinstance(raw, tuple | set):
        raw_parts = list(raw)
    else:
        raw_parts = []
    actions: set[str] = set()
    for part in raw_parts:
        text = str(part or "").strip()
        if not text:
            continue
        for action in text.split("+"):
            action = action.strip()
            if action:
                actions.add(action)
    return actions


def _config_root_mapping(config: Any) -> dict[str, Any] | None:
    if isinstance(config, dict):
        return config
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict):
            return target
    return None


def _copy_into_schema_group(root: dict[str, Any], schema_map: dict[str, dict[str, Any]], key: str, value: Any) -> bool:
    item = schema_map.get(key)
    if not item:
        return False
    default = item.get("default")
    value = _coerce_schema_value(value, item)
    if value == default:
        return False
    group_key = str(item.get("group") or "")
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}
        root[group_key] = group
    group_value = group.get(key)
    should_copy = key not in group or group_value == default
    if not should_copy and _is_empty(group_value) and not _is_empty(value):
        should_copy = True
    if not should_copy:
        return False
    group[key] = value
    return True


def _schema_group_items(schema_path: Path, *, logger: Any | None = None) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    try:
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        if logger is not None:
            logger.debug("[PrivateCompanion] 读取配置 schema 用于分组迁移失败: %s", exc)
        return mapping
    if not isinstance(raw, dict):
        return mapping
    for group_key, group in raw.items():
        if not isinstance(group, dict) or group.get("type") != "object":
            continue
        items = group.get("items")
        if not isinstance(items, dict):
            continue
        for key, item in items.items():
            if isinstance(item, dict):
                copied = dict(item)
                copied["group"] = str(group_key)
                mapping[str(key)] = copied
    return mapping


def _coerce_schema_value(value: Any, item: dict[str, Any]) -> Any:
    item_type = str(item.get("type") or "")
    if item_type == "bool":
        return _coerce_bool(value)
    if item_type == "int":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return item.get("default")
    if item_type == "float":
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return item.get("default")
        slider = item.get("slider")
        if (
            isinstance(slider, dict)
            and float(slider.get("max", 0) or 0) <= 1.0
            and parsed > 1.0
            and ("probability" in str(item.get("description") or "").lower() or "概率" in str(item.get("description") or ""))
        ):
            parsed /= 100.0
        return parsed
    if item_type == "list":
        if isinstance(value, list):
            return value
        text = str(value or "").strip()
        if not text:
            return []
        return [part.strip() for part in re.split(r"[\n,，、;；]+", text) if part.strip()]
    if item_type in {"string", "text"}:
        return str(value or "")
    return value


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
            return False
    return bool(value)


def _save_config_after_schema_migration(config: Any, *, logger: Any | None = None) -> None:
    def schedule(result: Any) -> bool:
        if not (asyncio.iscoroutine(result) or hasattr(result, "__await__")):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(result, "close", None)
            if callable(close):
                close()
            if logger is not None:
                logger.debug("[PrivateCompanion] config migration async save skipped: no event loop")
            return True
        tasks = getattr(config, "_private_companion_config_save_tasks", None)
        if not isinstance(tasks, set):
            tasks = set()
            try:
                setattr(config, "_private_companion_config_save_tasks", tasks)
            except Exception:
                tasks = None
        task = loop.create_task(result, name="private-companion-config-save")
        if isinstance(tasks, set):
            tasks.add(task)

        def consume(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                if logger is not None:
                    logger.warning("[PrivateCompanion] config migration async save failed: %s", _single_line(exc, 160))
            finally:
                if isinstance(tasks, set):
                    tasks.discard(done_task)

        task.add_done_callback(consume)
        return True

    for method_name in ("save_config", "save", "save_conf"):
        save = getattr(config, method_name, None)
        if not callable(save):
            continue
        try:
            _ensure_config_parent_dir(config, logger=logger)
            result = save()
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                try:
                    schedule(result)
                except RuntimeError:
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    if logger is not None:
                        logger.debug("[PrivateCompanion] 配置分组迁移已写入运行态，当前无事件循环可异步保存")
            return
        except TypeError:
            continue
        except FileNotFoundError as exc:
            if _ensure_config_parent_dir(config, error=exc, logger=logger):
                try:
                    result = save()
                    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                        try:
                            schedule(result)
                        except RuntimeError:
                            close = getattr(result, "close", None)
                            if callable(close):
                                close()
                    return
                except Exception as retry_exc:
                    if logger is not None:
                        logger.warning("[PrivateCompanion] 重试保存配置分组迁移结果失败: %s", _single_line(retry_exc, 160))
                    return
            if logger is not None:
                logger.warning("[PrivateCompanion] 保存配置分组迁移结果失败: %s", _single_line(exc, 160))
            return
        except Exception as exc:
            if logger is not None:
                logger.warning("[PrivateCompanion] 保存配置分组迁移结果失败: %s", _single_line(exc, 160))
            return


def _ensure_config_parent_dir(
    config: Any,
    *,
    error: BaseException | None = None,
    logger: Any | None = None,
) -> bool:
    paths: list[str] = []
    for attr in (
        "path",
        "file",
        "filepath",
        "file_path",
        "config_path",
        "_path",
        "_file",
        "_filepath",
        "_file_path",
        "_config_path",
    ):
        try:
            value = getattr(config, attr, None)
        except Exception:
            value = None
        if value:
            paths.append(str(value))
    if isinstance(config, dict):
        for key in ("path", "file", "filepath", "file_path", "config_path"):
            value = config.get(key)
            if value:
                paths.append(str(value))
    if error is not None:
        match = re.search(r"['\"]([^'\"]+?\.tmp)['\"]", str(error))
        if match:
            paths.append(match.group(1))
    changed = False
    for raw in paths:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.endswith(".tmp"):
            parent = Path(text).expanduser().parent
        else:
            candidate = Path(text).expanduser()
            parent = candidate if text.endswith(("/", "\\")) else candidate.parent
        if not str(parent):
            continue
        try:
            parent.mkdir(parents=True, exist_ok=True)
            changed = True
        except Exception as exc:
            if logger is not None:
                logger.debug("[PrivateCompanion] 创建配置目录失败: %s", _single_line(exc, 160))
    return changed


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _single_line(text: Any, limit: int = 80) -> str:
    return " ".join(str(text or "").split())[:limit]
