# -*- coding: utf-8 -*-
"""persona_config 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 persona_config.py，仅调整模块级依赖的导入来源。
"""
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping


PERSONA_SETTINGS_SCHEMA_VERSION = 9

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
    6: (
        "enable_wardrobe",
        "wardrobe_tendency",
        "enable_wardrobe_prompt",
        "wardrobe_prompt_max_items",
        "wardrobe_image_max_count",
        "WARDROBE_VISION_PROVIDER_ID",
        "wardrobe_items",
    ),
    7: ("wardrobe_image_prompt",),
    8: (
        "wardrobe_outfit_mode",
        "wardrobe_outfit_rotation_days",
        "enable_wardrobe_outfit_generate",
        "WARDROBE_OUTFIT_PROVIDER_ID",
        "wardrobe_outfits",
    ),
    # 衣柜接管与渐进披露：与 v6/v7/v8 一样，新键要在升版时给已存在的人格物化默认值，
    # 否则副人格只会「跟随主人格」，面板上也看不到自己的这一项。
    9: (
        "wardrobe_injection_detail",
        "wardrobe_photo_source",
    ),
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
        "enable_llm_streaming",
        "background_llm_request_max_attempts",
        "model_request_max_attempts_overrides",
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


def load_schema(schema_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(schema_path) if schema_path is not None else Path(__file__).with_name("_conf_schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


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
