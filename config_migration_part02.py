# -*- coding: utf-8 -*-
"""config_migration_part02：从 config_migration.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（18 个函数 / 415 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 config_migration.py re-export，接口不变。
"""
from __future__ import annotations

from .config_migration_part01 import (
    _coerce_bool,
    _coerce_schema_value,
    _first_present_value,
    _normalize_weather_source,
    _parse_legacy_action_list,
    _weather_config_values,
    _weather_schema_group,
)
from .config_migration_shared import (
    Any,
    LEGACY_PROACTIVE_ACTIONS_KEY,
    LEGACY_PROACTIVE_ACTION_FLAG_KEYS,
    Path,
    QWEATHER_DEFAULT_SOURCE,
    _COMMAND_PHOTO_QUOTA_MIGRATION_MARKER,
    _COMMAND_PHOTO_QUOTA_MIGRATION_VERSION,
    _QWEATHER_GENERIC_FALLBACKS,
    re,
)


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
                logger.debug("创建配置目录失败: %s", _single_line(exc, 160))
    return changed

def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})

def _single_line(text: Any, limit: int = 80) -> str:
    return " ".join(str(text or "").split())[:limit]
