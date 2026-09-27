# -*- coding: utf-8 -*-
"""persona_config 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 persona_config.py，仅调整模块级依赖的导入来源。
"""
import copy
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

try:  # package import
    from .persona_config_part01 import (
        MODE_COPY,
        MODE_DEFAULTS,
        MODE_FOLLOW_PRIMARY,
        PERSONA_SETTINGS_KEY,
        PERSONA_SETTINGS_NEW_KEYS_BY_VERSION,
        PERSONA_SETTINGS_SCHEMA_VERSION,
        PersonaConfigError,
        PersonaSettingNotAllowed,
        PersonaSettingsTypeError,
        SCOPE_MANIFEST_VERSION,
        _MISSING,
        build_scope_manifest,
        discover_grouped_schema_leaves,
        load_schema,
    )
except ImportError:  # direct test/import from the plugin directory
    from persona_config_part01 import (
        MODE_COPY,
        MODE_DEFAULTS,
        MODE_FOLLOW_PRIMARY,
        PERSONA_SETTINGS_KEY,
        PERSONA_SETTINGS_NEW_KEYS_BY_VERSION,
        PERSONA_SETTINGS_SCHEMA_VERSION,
        PersonaConfigError,
        PersonaSettingNotAllowed,
        PersonaSettingsTypeError,
        SCOPE_MANIFEST_VERSION,
        _MISSING,
        build_scope_manifest,
        discover_grouped_schema_leaves,
        load_schema,
    )


def scope_manifest_document(
    schema: Mapping[str, Any] | None = None,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a serializable, explicitly versioned manifest document."""

    if manifest is None:
        manifest = build_scope_manifest(schema if schema is not None else load_schema())
    return {
        "schema_version": SCOPE_MANIFEST_VERSION,
        "settings_count": len(manifest),
        "settings": copy.deepcopy(dict(manifest)),
    }


def validate_scope_manifest(
    manifest: Mapping[str, Mapping[str, Any]],
    *,
    schema: Mapping[str, Any] | None = None,
) -> None:
    """Raise when a manifest is incomplete, duplicated, or malformed."""

    if schema is not None:
        expected = discover_grouped_schema_leaves(schema)
        if set(manifest) != set(expected):
            missing = sorted(set(expected) - set(manifest))
            extra = sorted(set(manifest) - set(expected))
            raise PersonaConfigError(f"scope manifest coverage mismatch missing={missing[:5]} extra={extra[:5]}")
    for key, entry in manifest.items():
        if not isinstance(entry, Mapping) or entry.get("key") != key:
            raise PersonaConfigError(f"invalid scope manifest entry: {key}")
        if entry.get("scope") not in {"common", "persona"}:
            raise PersonaConfigError(f"invalid scope for {key}: {entry.get('scope')}")
        if entry.get("identity") and entry.get("inherit_primary"):
            raise PersonaConfigError(f"identity setting inherits primary: {key}")
        if entry.get("identity") and entry.get("cloneable"):
            raise PersonaConfigError(f"identity setting is cloneable: {key}")


def load_scope_manifest(schema_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    return build_scope_manifest(load_schema(schema_path))


def schema_defaults(
    schema: Mapping[str, Any] | None = None,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    include_common: bool = True,
    include_identity: bool = True,
    normalizer: Callable[[str, Any, Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Return normalized defaults for canonical grouped keys.

    ``include_common=False`` is used by the default-persona creation flow.
    ``normalizer`` is intentionally injectable so runtime code can apply its
    existing dependent default rules without constructing a plugin instance.
    """

    if manifest is None:
        manifest = build_scope_manifest(schema if schema is not None else load_schema())
    values: dict[str, Any] = {}
    for key, entry in manifest.items():
        if not include_common and entry.get("scope") == "common":
            continue
        if not include_identity and entry.get("identity"):
            continue
        value = copy.deepcopy(entry.get("new_key_default", entry.get("default")))
        if normalizer is not None:
            value = normalizer(key, value, entry)
        values[key] = value
    return values


def default_persona_settings(
    schema: Mapping[str, Any] | None = None,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    normalizer: Callable[[str, Any, Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Create the complete independent persona portion of a fresh install."""

    return schema_defaults(
        schema,
        manifest=manifest,
        include_common=False,
        include_identity=True,
        normalizer=normalizer,
    )


def _lookup(mapping: Any, key: str) -> Any:
    if isinstance(mapping, Mapping):
        # Grouped schema values take precedence over legacy flat aliases, as
        # AstrBot's own config helper does.  This matters during migration when
        # both locations temporarily exist.
        for value in mapping.values():
            if isinstance(value, Mapping):
                found = _lookup(value, key)
                if found is not _MISSING:
                    return found
        if key in mapping:
            return mapping[key]
    for attr in ("data", "config"):
        target = getattr(mapping, attr, None)
        if isinstance(target, Mapping):
            found = _lookup(target, key)
            if found is not _MISSING:
                return found
    getter = getattr(mapping, "get", None)
    if callable(getter):
        try:
            value = getter(key, _MISSING)
        except Exception:
            value = _MISSING
        if value is not _MISSING:
            return value
    return _MISSING


def resolve_persona_setting(
    key: str,
    persona_settings: Mapping[str, Any] | None,
    primary_config: Any,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    default: Any = _MISSING,
) -> Any:
    """Resolve one key with presence-based sparse inheritance semantics."""

    if manifest is None:
        manifest = load_scope_manifest()
    entry = manifest.get(key)
    settings = persona_settings if isinstance(persona_settings, Mapping) else {}
    if entry is None:
        # Unknown/legacy keys are intentionally not a runtime configuration
        # surface.  They may remain on disk for recovery but never override
        # the canonical schema.
        return copy.deepcopy(default) if default is not _MISSING else None
    if entry is not None and entry.get("scope") == "common":
        own = _lookup(primary_config, key)
        if own is not _MISSING:
            return copy.deepcopy(own)
    elif entry is not None and entry.get("identity"):
        if key in settings:
            return copy.deepcopy(settings[key])
        if default is not _MISSING:
            return copy.deepcopy(default)
        return copy.deepcopy(entry.get("default"))
    elif key in settings:
        persona_value = copy.deepcopy(settings[key])
        merge_policy = entry.get("safety_merge")
        if merge_policy in {"primary_and_persona", "primary_or_persona"}:
            inherited = _lookup(primary_config, key)
            if inherited is _MISSING:
                inherited = entry.get("default")
            if merge_policy == "primary_and_persona":
                return bool(inherited) and bool(persona_value)
            return bool(inherited) or bool(persona_value)
        return persona_value
    inherited = _lookup(primary_config, key)
    if inherited is not _MISSING:
        return copy.deepcopy(inherited)
    if default is not _MISSING:
        return copy.deepcopy(default)
    if entry is not None:
        return copy.deepcopy(entry.get("default"))
    return None


def resolve_effective_settings(
    persona_settings: Mapping[str, Any] | None,
    primary_config: Any,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    include_common: bool = False,
    include_identity: bool = True,
) -> dict[str, Any]:
    """Resolve a deterministic settings view for one persona."""

    if manifest is None:
        manifest = load_scope_manifest()
    result: dict[str, Any] = {}
    for key, entry in manifest.items():
        if not include_common and entry.get("scope") == "common":
            continue
        if not include_identity and entry.get("identity"):
            continue
        result[key] = resolve_persona_setting(
            key,
            persona_settings,
            primary_config,
            manifest=manifest,
        )
    return result


def runtime_persona_setting(owner: Any, key: str, default: Any = None) -> Any:
    """Read one setting through the active persona accessor when available.

    Args:
        owner: Plugin or mixin host that owns the setting.
        key: Canonical configuration key.
        default: Fallback used when the host has no attribute.

    Returns:
        The effective setting value for the current runtime persona.
    """
    getter = getattr(owner, "persona_setting", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            pass
    return getattr(owner, key, default)


def normalize_setting_value(key: str, value: Any, entry: Mapping[str, Any]) -> Any:
    """Apply conservative schema type/option normalization to one value."""

    field_type = str(entry.get("type") or "")
    default = copy.deepcopy(entry.get("new_key_default", entry.get("default")))
    options = entry.get("options")
    if isinstance(options, list) and value not in options:
        value = copy.deepcopy(default)
    if field_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on", "enable", "enabled", "是", "开启", "开"}:
                return True
            if lowered in {"false", "0", "no", "off", "disable", "disabled", "否", "关闭", "关", ""}:
                return False
        return default if isinstance(default, bool) else bool(value)
    if field_type == "int":
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if field_type == "float":
        if isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if field_type in {"list", "template_list"}:
        return copy.deepcopy(value) if isinstance(value, list) else copy.deepcopy(default if isinstance(default, list) else [])
    if field_type == "object":
        return copy.deepcopy(value) if isinstance(value, dict) else copy.deepcopy(default if isinstance(default, dict) else {})
    if field_type in {"string", "text"}:
        return value if isinstance(value, str) else ("" if value is None else str(value))
    return copy.deepcopy(value)


def normalize_persona_settings(
    settings: Mapping[str, Any] | None,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    preserve_unknown: bool = True,
    reject_common: bool = False,
) -> dict[str, Any]:
    """Normalize known values while retaining sparse presence and unknown data."""

    if settings is None:
        return {}
    if not isinstance(settings, Mapping):
        raise PersonaSettingsTypeError("persona_settings must be an object")
    if manifest is None:
        manifest = load_scope_manifest()
    output: dict[str, Any] = {}
    for key, value in settings.items():
        entry = manifest.get(str(key))
        if entry is None:
            if preserve_unknown:
                output[str(key)] = copy.deepcopy(value)
            continue
        if entry.get("scope") == "common":
            if reject_common:
                raise PersonaSettingNotAllowed(f"common setting cannot be overridden: {key}")
            if preserve_unknown:
                output[key] = copy.deepcopy(value)
            continue
        output[key] = normalize_setting_value(key, value, entry)
    return output


def filter_cloneable_settings(
    settings: Mapping[str, Any] | None,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Filter a raw profile to persona keys allowed to be copied."""

    if manifest is None:
        manifest = load_scope_manifest()
    if not isinstance(settings, Mapping):
        return {}
    return {
        key: copy.deepcopy(value)
        for key, value in settings.items()
        if key in manifest and manifest[key].get("scope") == "persona" and manifest[key].get("cloneable")
    }


def copy_persona_settings(
    source_settings: Mapping[str, Any] | None,
    *,
    bot_name: str,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Raw-copy a persona's overrides, excluding identity/common fields."""

    _require_bot_name(bot_name)
    source = source_settings
    if source is not None and not isinstance(source, Mapping):
        source = _flatten_primary(source)
    if isinstance(source, Mapping) and isinstance(source.get(PERSONA_SETTINGS_KEY), Mapping):
        source = source[PERSONA_SETTINGS_KEY]
    copied = filter_cloneable_settings(source, manifest=manifest)
    # The new identity is supplied by the caller, never inherited.
    copied["bot_name"] = bot_name
    return copied


def _flatten_primary(primary_config: Any) -> dict[str, Any]:
    if isinstance(primary_config, Mapping):
        grouped: dict[str, Any] = {}
        flat: dict[str, Any] = {}
        for key, value in primary_config.items():
            if isinstance(value, Mapping):
                grouped.update(_flatten_primary(value))
            else:
                flat[str(key)] = copy.deepcopy(value)
        result = grouped
        for key, value in flat.items():
            result.setdefault(key, value)
        return result
    data = getattr(primary_config, "data", None)
    if isinstance(data, Mapping):
        return _flatten_primary(data)
    config = getattr(primary_config, "config", None)
    if isinstance(config, Mapping):
        return _flatten_primary(config)
    return {}


def copy_from_primary_config(
    primary_config: Any,
    *,
    bot_name: str,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Copy persona-owned values from the primary config snapshot."""

    _require_bot_name(bot_name)
    return copy_persona_settings(
        _flatten_primary(primary_config),
        bot_name=bot_name,
        manifest=manifest,
    )


def _require_bot_name(bot_name: Any) -> str:
    value = str(bot_name or "").strip()
    if not value:
        raise PersonaConfigError("bot_name is required for a new persona")
    return value


def create_persona_settings(
    mode: str,
    *,
    bot_name: str,
    primary_config: Any | None = None,
    source_settings: Mapping[str, Any] | None = None,
    schema: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    normalizer: Callable[[str, Any, Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Create one profile's raw ``persona_settings`` in one of three modes."""

    bot_name = _require_bot_name(bot_name)
    normalized_mode = str(mode or "").strip().lower()
    if manifest is None:
        manifest = build_scope_manifest(schema if schema is not None else load_schema())
    if normalized_mode in {MODE_FOLLOW_PRIMARY, "follow", "inherit", "primary"}:
        return {"bot_name": bot_name}
    if normalized_mode in {MODE_DEFAULTS, "default", "fresh"}:
        settings = default_persona_settings(
            schema,
            manifest=manifest,
            normalizer=normalizer,
        )
        settings["bot_name"] = bot_name
        return settings
    if normalized_mode in {MODE_COPY, "clone"}:
        if source_settings is None:
            source_settings = primary_config
        return copy_persona_settings(source_settings, bot_name=bot_name, manifest=manifest)
    raise PersonaConfigError(f"unknown persona creation mode: {mode}")


def detach_persona_settings(
    persona_settings: Mapping[str, Any] | None,
    primary_config: Any,
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Materialize the current effective persona values into a standalone copy."""

    if manifest is None:
        manifest = load_scope_manifest()
    existing = normalize_persona_settings(persona_settings, manifest=manifest, preserve_unknown=False)
    result: dict[str, Any] = {
        key: copy.deepcopy(value)
        for key, value in existing.items()
        if key in manifest and manifest[key].get("identity")
    }
    effective = resolve_effective_settings(
        persona_settings,
        primary_config,
        manifest=manifest,
        include_common=False,
        include_identity=False,
    )
    for key, value in effective.items():
        entry = manifest[key]
        if entry.get("cloneable"):
            result[key] = copy.deepcopy(value)
    return result


def migrate_persona_settings(
    settings: Any,
    *,
    stored_version: int | None = None,
    target_version: int = PERSONA_SETTINGS_SCHEMA_VERSION,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    new_keys_by_version: Mapping[int, Iterable[str]] | None = None,
    persona_id: str | None = None,
    legacy_bot_name: str | None = None,
) -> tuple[dict[str, Any], int, bool]:
    """Migrate a profile settings object without filling legacy sparse keys.

    The third return value indicates whether the payload was originally absent
    (legacy sparse).  Future schema versions may provide explicit key lists in
    ``new_keys_by_version``; only those newly introduced keys are materialized.
    """

    if manifest is None:
        manifest = load_scope_manifest()
    if new_keys_by_version is None:
        new_keys_by_version = PERSONA_SETTINGS_NEW_KEYS_BY_VERSION
    legacy_sparse = settings is None
    if settings is None:
        result: dict[str, Any] = {}
    elif not isinstance(settings, Mapping):
        raise PersonaSettingsTypeError("persona_settings must be an object")
    else:
        result = copy.deepcopy(dict(settings))
    old_version = int(stored_version or 0)
    if old_version < 0 or old_version > int(target_version):
        raise PersonaConfigError(f"unsupported persona settings version: {stored_version}")
    if not str(result.get("bot_name") or "").strip():
        fallback_name = legacy_bot_name or persona_id
        if fallback_name:
            result["bot_name"] = _require_bot_name(fallback_name)
    for version in range(max(1, old_version + 1), int(target_version) + 1):
        for key in (new_keys_by_version or {}).get(version, ()):
            key = str(key)
            entry = manifest.get(key)
            if entry is None or entry.get("scope") != "persona" or key in result:
                continue
            result[key] = copy.deepcopy(entry.get("new_key_default", entry.get("default")))
    return result, int(target_version), legacy_sparse
