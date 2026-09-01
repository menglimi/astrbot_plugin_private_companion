"""Persona-scoped configuration primitives.

This module deliberately has no dependency on the plugin runtime.  The main
plugin can use these functions while holding its data lock and is responsible
for persistence, cache invalidation, and activation of a persona context.

The grouped entries in ``_conf_schema.json`` are the authority for this
module.  AstrBot currently also exposes legacy flat aliases in that file;
those aliases are intentionally ignored when constructing the manifest so a
setting is represented exactly once.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping


PERSONA_SETTINGS_SCHEMA_VERSION = 5
PERSONA_CONFIG_SCHEMA_VERSION = PERSONA_SETTINGS_SCHEMA_VERSION
SCOPE_MANIFEST_VERSION = 1
PERSONA_SETTINGS_KEY = "persona_settings"
PERSONA_SETTINGS_VERSION_KEY = "persona_settings_schema_version"
PERSONA_SETTINGS_REVISION_KEY = "persona_settings_revision"
PERSONA_SETTINGS_NEW_KEYS_BY_VERSION: dict[int, tuple[str, ...]] = {
    2: ("enable_group_bot_name_wakeup",),
    3: ("enable_qq_official_segmented_reply", "intercept_astrbot_group_context"),
    4: (
        "group_scene_recent_max_chars",
        "enable_llm_controlled_segmenting",
        "enable_segmented_plugin_rules",
    ),
    5: ("enable_user_requested_photo_generation",),
}

MODE_FOLLOW_PRIMARY = "follow_primary"
MODE_DEFAULTS = "defaults"
MODE_COPY = "copy"
CREATE_MODE_FOLLOW_PRIMARY = MODE_FOLLOW_PRIMARY
CREATE_MODE_DEFAULTS = MODE_DEFAULTS
CREATE_MODE_COPY = MODE_COPY


class PersonaConfigError(ValueError):
    """Raised when a persona setting payload cannot be safely interpreted."""


class PersonaSettingsTypeError(PersonaConfigError):
    """Raised for a profile whose ``persona_settings`` is not an object."""


class PersonaSettingNotAllowed(PersonaConfigError):
    """Raised when a common setting is submitted as a persona override."""


@dataclass(frozen=True)
class ScopeEntry:
    """Normalized metadata for one grouped schema leaf.

    ``default`` is copied when exposed through :func:`build_scope_manifest`; it
    is therefore safe for callers to modify returned manifest dictionaries.
    """

    key: str
    schema_group: str
    field_type: str
    default: Any
    scope: str
    cloneable: bool
    inherit_primary: bool
    identity: bool
    required: bool
    new_key_default: Any
    sensitive: bool
    hot_apply: bool
    restart_required: bool
    side_effect: str
    safety_merge: str
    ui_location: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "schema_group": self.schema_group,
            "type": self.field_type,
            "default": copy.deepcopy(self.default),
            "scope": self.scope,
            "cloneable": self.cloneable,
            "inherit_primary": self.inherit_primary,
            "identity": self.identity,
            "required": self.required,
            "new_key_default": copy.deepcopy(self.new_key_default),
            "sensitive": self.sensitive,
            "hot_apply": self.hot_apply,
            "restart_required": self.restart_required,
            "side_effect": self.side_effect,
            "safety_merge": self.safety_merge,
            "ui_location": self.ui_location,
        }


# These groups are shared infrastructure or account-level integrations.  All
# other groups default to persona scope; the key-level exceptions below keep
# mixed groups explicit without maintaining a fragile key-by-key allowlist.
COMMON_GROUPS = frozenset(
    {
        "balance_awareness_config",
        "external_memory_config",
        "qzone_config",
        "voice_playback_config",
        "presence_sync_config",
    }
)

COMMON_KEYS = frozenset(
    {
        # Topology, adapter scope, storage, and standalone WebUI.
        "enable_p4_b_legacy_score_isolation",
        "bot_scope_mode",
        "bot_scope_ids",
        "plugin_specific_persona_id",
        "enable_multi_persona_mode",
        "multi_persona_ids",
        "storage_backend",
        "storage_sqlite_path",
        "enable_standalone_webui",
        "standalone_webui_host",
        "standalone_webui_port",
        "standalone_webui_access_token",
        "standalone_webui_session_ttl_hours",
        "enable_store_control_tag_sanitization",
        # Global token ceilings and delivery/account mappings.
        "daily_token_limit",
        "enable_daily_token_soft_limit",
        "daily_token_soft_limit",
        "target_user_ids",
        "private_user_aliases",
        "private_user_delivery_aliases",
        "target_platform",
        "environment_perception_timezone",
        "enable_group_relationship_affinity",
        "group_relationship_affinity_allowlist",
        "group_relationship_daily_net_cap",
        "group_relationship_window_minutes",
        "group_relationship_window_absolute_cap",
        "group_relationship_person_daily_absolute_cap",
        "group_relationship_scope_daily_absolute_cap",
        "relationship_event_window_minutes",
        "relationship_positive_event_cap",
        "relationship_negative_event_cap",
        "relationship_positive_daily_cap",
        "relationship_decay_grace_days",
        "relationship_decay_early_per_day",
        "relationship_decay_middle_per_day",
        "relationship_decay_late_per_day",
        "enable_relationship_stage_provider_routing",
        "relationship_stage_provider_routes",
        "deeply_distant",
        "strongly_distant",
        "distant",
        "acquaintance",
        "familiar",
        "close",
        "intimate",
        "deeply_bonded",
        "owner_exclusive",
        # Model replacement policy is shared; provider selection remains a
        # persona setting so different personas can choose different tasks.
        "provider_config_mode",
        "enable_deepseek_peak_replacement",
        "model_replacement_scope",
        "model_replacement_rules",
        "enable_sensitive_model_replacement",
        "SENSITIVE_REPLACEMENT_PROVIDER_ID",
        "sensitive_replacement_keywords",
        "DEEPSEEK_PEAK_REPLACEMENT_PROVIDER_ID",
        "deepseek_peak_windows",
        "deepseek_peak_timezone",
        "deepseek_peak_match_keywords",
        # Shared external service contracts and credentials. Persona behaviour
        # may decide whether/when to use them, but it cannot replace process
        # endpoints or secrets.
        "WEB_EXPLORATION_API_BASE_URL",
        "WEB_EXPLORATION_API_KEY",
        "WEB_EXPLORATION_API_MODEL",
        "custom_photo_tool_name",
        "custom_photo_tool_prompt_param",
        "custom_photo_tool_kind_param",
        "custom_photo_tool_reference_param",
        "custom_photo_tool_extra_params",
        "COMFYUI_TEXT2IMG_WORKFLOW_NAME",
        "COMFYUI_SELFIE_WORKFLOW_NAME",
        "COMFYUI_PHOTO_WORKFLOW_NAME",
        "EXTERNAL_IMAGE_API_BASE_URL",
        "EXTERNAL_IMAGE_API_KEY",
        "EXTERNAL_IMAGE_API_MODEL",
        "external_image_api_timeout_seconds",
        "external_image_api_custom_headers",
        "external_image_download_proxy",
        "external_image_download_use_environment_proxy",
        "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
        "BACKUP_EXTERNAL_IMAGE_API_KEY",
        "BACKUP_EXTERNAL_IMAGE_API_MODEL",
        "backup_external_image_api_timeout_seconds",
        "backup_external_image_api_custom_headers",
        "external_image_api_endpoints",
        # Interception/bridge target and physical device controls.
        "enable_reply_interception_forward",
        "reply_interception_forward_target_umo",
        "reply_interception_forward_plugin_blocks",
        "reply_interception_forward_rewrites",
        "reply_interception_forward_proactive_blocks",
        "enable_atrelay_tools",
        "enable_cross_user_memory_bridge",
        "cross_user_memory_owner_only",
        "atrelay_require_worldbook_first",
        "atrelay_member_cache_minutes",
        "atrelay_sensitive_confirm",
        "enable_atrelay_llm_rewrite",
        "atrelay_default_relay_style",
        "atrelay_multi_target_limit",
        "max_group_recent_messages",
        "max_group_slang_terms",
        "max_group_topic_threads",
        "group_episode_refresh_minutes",
        "group_slang_summary_minutes",
        "max_group_episodes",
        "max_group_relationship_edges",
        "enable_tts_local_playback",
        "enable_experimental_bluetooth_wakeup",
        "enable_reality_touch_camera",
        # Weather/API credentials and physical location can be shared by the
        # process.  A persona can still control weather behaviour switches.
        "weather_api_host",
        "weather_token",
        "weather_amap_api_key",
        "weather_api_key",
        "weather_alert_api_host",
        "weather_alert_token",
        "reality_touch_camera_index",
        "reality_touch_camera_capture_timeout_seconds",
        "reality_touch_camera_analysis_timeout_seconds",
        "reality_touch_camera_min_interval_seconds",
        "tts_local_playback_volume",
        "tts_live_subtitle_url",
        "tts_local_playback_min_interval_seconds",
        # Legacy global timezone and shared maintenance ceiling.
        "timezone",
        "enable_maintenance_token_saver",
        "maintenance_token_soft_limit",
    }
)

# Per-persona safety values may only narrow the primary policy.  Capability
# switches require both policies to allow them; guard/consent switches remain
# enabled when either policy requires them.
PRIMARY_AND_KEYS = frozenset(
    {
        "enable_relationship_content_tiers",
        "enable_flirt_content_tier",
        "enable_group_nsfw_private_fallback",
    }
)

SAFETY_OR_KEYS = frozenset(
    {
        "enable_group_member_safety",
        "enable_group_privacy_guard",
        "enable_group_third_party_portrait_guard",
    }
)

# Identity is still persona-owned, but it cannot silently inherit or be copied
# from another persona.  ``bot_name`` is required for all newly-created
# profiles; old sparse profiles are repaired by the host migration layer.
IDENTITY_KEYS = frozenset(
    {
        "bot_name",
        "default_nickname",
        "default_style",
        "reply_style_prompt",
        "enable_persona_voice_channels",
        "persona_conversation_voice_prompt",
        "persona_creative_voice_prompt",
        "persona_planning_voice_prompt",
        "persona_inner_voice_prompt",
        "persona_proactive_voice_prompt",
        "worldview_adaptation_mode",
        "worldview_adaptation_prompt",
        "schedule_persona_prompt",
        "schedule_worldview_prompt",
        "roleplay_user_profile_prompt",
        "roleplay_knowledge_source_ids",
        "worldbook_config_paths",
        "group_repeat_interrupt_image_path",
        "photo_persona_reference_image_path",
        "photo_reference_library",
        "photo_reference_catalog",
        "photo_reference_catalog_version",
        "photo_reference_catalog_user_cleared",
        "photo_structured_reference_assets",
        "owned_reaction_assets",
        "bot_relationship_cards",
        "photo_generation_fixed_prompt",
        "photo_generation_text2img_fixed_prompt",
        "photo_generation_selfie_fixed_prompt",
        "photo_generation_edit_fixed_prompt",
        "tts_mimo_voice_name",
        "tts_mimo_style_prompt",
        "tts_voice_language",
        "tts_fishaudio_model",
        "tts_fishaudio_emotion_mode",
        "tts_extra_prompt",
        "main_user_mention_voice_prompt",
    }
)

SENSITIVE_NAME_PARTS = (
    "api_key",
    "apikey",
    "access_token",
    "cookie",
    "password",
    "secret",
)

_MISSING = object()


def _schema_default(field: Mapping[str, Any]) -> Any:
    if "default" in field:
        return copy.deepcopy(field["default"])
    field_type = str(field.get("type") or "")
    if field_type == "bool":
        return False
    if field_type == "int":
        return 0
    if field_type == "float":
        return 0.0
    if field_type in {"list", "template_list"}:
        return []
    if field_type == "object":
        return {}
    return ""


def _iter_grouped_leaves(schema: Mapping[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(key, group, field)`` for all canonical grouped leaves."""

    def walk(mapping: Mapping[str, Any], group: str) -> Iterator[tuple[str, str, dict[str, Any]]]:
        for key, node in mapping.items():
            if not isinstance(node, dict):
                continue
            # ``items`` on an object node denotes nested schema fields.  A
            # template_list's ``templates`` is a value template, not a schema
            # path, and remains one leaf (the list itself).
            items = node.get("items")
            if node.get("type") == "object" and isinstance(items, dict) and items:
                yield from walk(items, group)
                continue
            yield str(key), group, node

    for group, node in schema.items():
        if not isinstance(node, dict):
            continue
        items = node.get("items")
        if not isinstance(items, dict) or not items:
            continue
        yield from walk(items, str(group))


def discover_grouped_schema_leaves(schema: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return canonical grouped schema fields indexed by their flat key.

    The function raises on duplicate grouped leaf names: silently choosing one
    would make resolver precedence depend on JSON ordering.
    """

    result: dict[str, dict[str, Any]] = {}
    for key, group, field in _iter_grouped_leaves(schema):
        if key in result:
            raise PersonaConfigError(f"duplicate grouped schema key: {key}")
        result[key] = {
            "key": key,
            "schema_group": group,
            "field": copy.deepcopy(field),
        }
    return result


def _is_sensitive(key: str, field: Mapping[str, Any]) -> bool:
    if bool(field.get("password")) or bool(field.get("sensitive")):
        return True
    lower = key.lower()
    return any(part in lower for part in SENSITIVE_NAME_PARTS) or lower.endswith("_token")


def _is_restart_required(key: str, group: str) -> bool:
    return key in {
        "storage_backend",
        "storage_sqlite_path",
        "enable_standalone_webui",
        "standalone_webui_host",
        "standalone_webui_port",
        "standalone_webui_access_token",
        "standalone_webui_session_ttl_hours",
    } or group in {"voice_playback_config"}


def _is_side_effectful(group: str) -> str:
    if group in COMMON_GROUPS:
        return "shared"
    if group in {"qzone_config", "presence_sync_config", "voice_playback_config"}:
        return "shared"
    return "none"


def build_scope_manifest(schema: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Build the versioned scope manifest for all grouped schema leaves."""

    if schema is None:
        schema = load_schema()
    leaves = discover_grouped_schema_leaves(schema)
    manifest: dict[str, dict[str, Any]] = {}
    for key, item in leaves.items():
        field = item["field"]
        group = item["schema_group"]
        is_identity = key in IDENTITY_KEYS
        is_common = group in COMMON_GROUPS or key in COMMON_KEYS
        # Identity keys remain persona-owned, even if a future group is put in
        # COMMON_GROUPS.  Their no-inherit/no-copy contract is independent of
        # ordinary scope.
        scope = "persona" if is_identity else ("common" if is_common else "persona")
        default = _schema_default(field)
        manifest[key] = {
            "key": key,
            "schema_group": group,
            "type": str(field.get("type") or "string"),
            "options": copy.deepcopy(field.get("options")) if isinstance(field.get("options"), list) else None,
            "default": copy.deepcopy(default),
            "scope": scope,
            "cloneable": bool(scope == "persona" and not is_identity),
            "inherit_primary": bool(scope == "persona" and not is_identity),
            "identity": is_identity,
            "required": key == "bot_name",
            "new_key_default": copy.deepcopy(default),
            "sensitive": _is_sensitive(key, field),
            "hot_apply": not _is_restart_required(key, group),
            "restart_required": _is_restart_required(key, group),
            "side_effect": _is_side_effectful(group),
            "safety_merge": (
                "primary_and_persona"
                if key in PRIMARY_AND_KEYS
                else "primary_or_persona"
                if key in SAFETY_OR_KEYS
                else "replace"
            ),
            "ui_location": "common" if scope == "common" else group,
        }
    return manifest


def manifest_to_scope_entries(manifest: Mapping[str, Mapping[str, Any]]) -> dict[str, ScopeEntry]:
    """Convert a dictionary manifest to typed entries for integrations."""

    return {
        key: ScopeEntry(
            key=key,
            schema_group=str(value.get("schema_group") or ""),
            field_type=str(value.get("type") or "string"),
            default=copy.deepcopy(value.get("default")),
            scope=str(value.get("scope") or "persona"),
            cloneable=bool(value.get("cloneable")),
            inherit_primary=bool(value.get("inherit_primary")),
            identity=bool(value.get("identity")),
            required=bool(value.get("required")),
            new_key_default=copy.deepcopy(value.get("new_key_default")),
            sensitive=bool(value.get("sensitive")),
            hot_apply=bool(value.get("hot_apply")),
            restart_required=bool(value.get("restart_required")),
            side_effect=str(value.get("side_effect") or "none"),
            safety_merge=str(value.get("safety_merge") or "replace"),
            ui_location=str(value.get("ui_location") or "persona"),
        )
        for key, value in manifest.items()
    }


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


def load_schema(schema_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(schema_path) if schema_path is not None else Path(__file__).with_name("_conf_schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


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


def migrate_persona_profile(
    profile: Mapping[str, Any],
    *,
    manifest: Mapping[str, Mapping[str, Any]] | None = None,
    target_version: int = PERSONA_SETTINGS_SCHEMA_VERSION,
    new_keys_by_version: Mapping[int, Iterable[str]] | None = None,
    persona_id: str | None = None,
    legacy_bot_name: str | None = None,
) -> dict[str, Any]:
    """Return a migrated profile copy while preserving all life-data fields."""

    if not isinstance(profile, Mapping):
        raise PersonaConfigError("persona profile must be an object")
    result = copy.deepcopy(dict(profile))
    settings, version, _legacy_sparse = migrate_persona_settings(
        result.get(PERSONA_SETTINGS_KEY),
        stored_version=result.get(PERSONA_SETTINGS_VERSION_KEY),
        target_version=target_version,
        manifest=manifest,
        new_keys_by_version=new_keys_by_version,
        persona_id=persona_id,
        legacy_bot_name=legacy_bot_name,
    )
    result[PERSONA_SETTINGS_KEY] = settings
    result[PERSONA_SETTINGS_VERSION_KEY] = version
    try:
        revision = int(result.get(PERSONA_SETTINGS_REVISION_KEY) or 0)
    except (TypeError, ValueError):
        revision = 0
    result[PERSONA_SETTINGS_REVISION_KEY] = max(0, revision)
    return result


# Friendly aliases for integration code and tests.
get_scope_manifest = load_scope_manifest
resolve_setting = resolve_persona_setting
resolve_settings = resolve_effective_settings
create_settings = create_persona_settings
copy_settings = copy_persona_settings
detach_settings = detach_persona_settings
migrate_profile = migrate_persona_profile


__all__ = [
    "COMMON_GROUPS",
    "COMMON_KEYS",
    "CREATE_MODE_COPY",
    "CREATE_MODE_DEFAULTS",
    "CREATE_MODE_FOLLOW_PRIMARY",
    "IDENTITY_KEYS",
    "MODE_COPY",
    "MODE_DEFAULTS",
    "MODE_FOLLOW_PRIMARY",
    "PERSONA_CONFIG_SCHEMA_VERSION",
    "PERSONA_SETTINGS_KEY",
    "PERSONA_SETTINGS_NEW_KEYS_BY_VERSION",
    "PERSONA_SETTINGS_REVISION_KEY",
    "PERSONA_SETTINGS_SCHEMA_VERSION",
    "PERSONA_SETTINGS_VERSION_KEY",
    "SCOPE_MANIFEST_VERSION",
    "PRIMARY_AND_KEYS",
    "SAFETY_OR_KEYS",
    "PersonaConfigError",
    "PersonaSettingNotAllowed",
    "PersonaSettingsTypeError",
    "ScopeEntry",
    "build_scope_manifest",
    "copy_from_primary_config",
    "copy_persona_settings",
    "create_persona_settings",
    "default_persona_settings",
    "detach_persona_settings",
    "discover_grouped_schema_leaves",
    "filter_cloneable_settings",
    "load_schema",
    "load_scope_manifest",
    "manifest_to_scope_entries",
    "migrate_persona_profile",
    "migrate_persona_settings",
    "normalize_persona_settings",
    "normalize_setting_value",
    "resolve_effective_settings",
    "resolve_persona_setting",
    "runtime_persona_setting",
    "schema_defaults",
    "scope_manifest_document",
    "validate_scope_manifest",
]
