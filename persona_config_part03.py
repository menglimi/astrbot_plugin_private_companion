# -*- coding: utf-8 -*-
"""persona_config 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 persona_config.py，仅调整模块级依赖的导入来源。
"""
import copy
from typing import Any, Callable, Iterable, Iterator, Mapping

try:  # package import
    from .persona_config_part01 import (
        PERSONA_SETTINGS_KEY,
        PERSONA_SETTINGS_REVISION_KEY,
        PERSONA_SETTINGS_SCHEMA_VERSION,
        PERSONA_SETTINGS_VERSION_KEY,
        PersonaConfigError,
    )
except ImportError:  # direct test/import from the plugin directory
    from persona_config_part01 import (
        PERSONA_SETTINGS_KEY,
        PERSONA_SETTINGS_REVISION_KEY,
        PERSONA_SETTINGS_SCHEMA_VERSION,
        PERSONA_SETTINGS_VERSION_KEY,
        PersonaConfigError,
    )
try:  # package import
    from .persona_config_part02 import (
        copy_persona_settings,
        create_persona_settings,
        detach_persona_settings,
        load_scope_manifest,
        migrate_persona_settings,
        resolve_effective_settings,
        resolve_persona_setting,
    )
except ImportError:  # direct test/import from the plugin directory
    from persona_config_part02 import (
        copy_persona_settings,
        create_persona_settings,
        detach_persona_settings,
        load_scope_manifest,
        migrate_persona_settings,
        resolve_effective_settings,
        resolve_persona_setting,
    )


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
