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
try:  # package import
    from .persona_config_part01 import (
        COMMON_GROUPS,
        COMMON_KEYS,
        CREATE_MODE_COPY,
        CREATE_MODE_DEFAULTS,
        CREATE_MODE_FOLLOW_PRIMARY,
        IDENTITY_KEYS,
        MODE_COPY,
        MODE_DEFAULTS,
        MODE_FOLLOW_PRIMARY,
        PERSONA_CONFIG_SCHEMA_VERSION,
        PERSONA_SETTINGS_KEY,
        PERSONA_SETTINGS_NEW_KEYS_BY_VERSION,
        PERSONA_SETTINGS_REVISION_KEY,
        PERSONA_SETTINGS_SCHEMA_VERSION,
        PERSONA_SETTINGS_VERSION_KEY,
        PRIMARY_AND_KEYS,
        PersonaConfigError,
        PersonaSettingNotAllowed,
        PersonaSettingsTypeError,
        SAFETY_OR_KEYS,
        SCOPE_MANIFEST_VERSION,
        SENSITIVE_NAME_PARTS,
        ScopeEntry,
        _MISSING,
        _is_restart_required,
        _is_sensitive,
        _is_side_effectful,
        _iter_grouped_leaves,
        _schema_default,
        build_scope_manifest,
        discover_grouped_schema_leaves,
        load_schema,
        manifest_to_scope_entries,
    )
except ImportError:  # direct test/import from the plugin directory
    from persona_config_part01 import (
        COMMON_GROUPS,
        COMMON_KEYS,
        CREATE_MODE_COPY,
        CREATE_MODE_DEFAULTS,
        CREATE_MODE_FOLLOW_PRIMARY,
        IDENTITY_KEYS,
        MODE_COPY,
        MODE_DEFAULTS,
        MODE_FOLLOW_PRIMARY,
        PERSONA_CONFIG_SCHEMA_VERSION,
        PERSONA_SETTINGS_KEY,
        PERSONA_SETTINGS_NEW_KEYS_BY_VERSION,
        PERSONA_SETTINGS_REVISION_KEY,
        PERSONA_SETTINGS_SCHEMA_VERSION,
        PERSONA_SETTINGS_VERSION_KEY,
        PRIMARY_AND_KEYS,
        PersonaConfigError,
        PersonaSettingNotAllowed,
        PersonaSettingsTypeError,
        SAFETY_OR_KEYS,
        SCOPE_MANIFEST_VERSION,
        SENSITIVE_NAME_PARTS,
        ScopeEntry,
        _MISSING,
        _is_restart_required,
        _is_sensitive,
        _is_side_effectful,
        _iter_grouped_leaves,
        _schema_default,
        build_scope_manifest,
        discover_grouped_schema_leaves,
        load_schema,
        manifest_to_scope_entries,
    )
try:  # package import
    from .persona_config_part02 import (
        _flatten_primary,
        _lookup,
        _require_bot_name,
        copy_from_primary_config,
        copy_persona_settings,
        create_persona_settings,
        default_persona_settings,
        detach_persona_settings,
        filter_cloneable_settings,
        load_scope_manifest,
        migrate_persona_settings,
        normalize_persona_settings,
        normalize_setting_value,
        resolve_effective_settings,
        resolve_persona_setting,
        runtime_persona_setting,
        schema_defaults,
        scope_manifest_document,
        validate_scope_manifest,
    )
except ImportError:  # direct test/import from the plugin directory
    from persona_config_part02 import (
        _flatten_primary,
        _lookup,
        _require_bot_name,
        copy_from_primary_config,
        copy_persona_settings,
        create_persona_settings,
        default_persona_settings,
        detach_persona_settings,
        filter_cloneable_settings,
        load_scope_manifest,
        migrate_persona_settings,
        normalize_persona_settings,
        normalize_setting_value,
        resolve_effective_settings,
        resolve_persona_setting,
        runtime_persona_setting,
        schema_defaults,
        scope_manifest_document,
        validate_scope_manifest,
    )
try:  # package import
    from .persona_config_part03 import (
        copy_settings,
        create_settings,
        detach_settings,
        get_scope_manifest,
        migrate_persona_profile,
        migrate_profile,
        resolve_setting,
        resolve_settings,
    )
except ImportError:  # direct test/import from the plugin directory
    from persona_config_part03 import (
        copy_settings,
        create_settings,
        detach_settings,
        get_scope_manifest,
        migrate_persona_profile,
        migrate_profile,
        resolve_setting,
        resolve_settings,
    )


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
