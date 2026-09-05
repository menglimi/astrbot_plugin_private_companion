"""Single runtime configuration lookup boundary."""
from __future__ import annotations
from typing import Any
from .persona_config import resolve_persona_setting

def lookup_runtime_setting(owner: Any, key: str, default: Any = None) -> Any:
    config = getattr(owner, "config", {}) or {}
    persona_id = getattr(owner, "active_persona_id", "")
    profiles = config.get("persona_settings", {}) if isinstance(config, dict) else {}
    overlay = profiles.get(persona_id, {}) if isinstance(profiles, dict) else {}
    primary = profiles.get("primary", {}) if isinstance(profiles, dict) else {}
    manifest = getattr(owner, "persona_scope_manifest", {}) or {}
    if key in overlay:
        return overlay[key]
    if overlay or primary or manifest:
        # The canonical manifest rejects unknown keys.  Hosts may still expose
        # a sparse test/integration overlay before bootstrap has attached it;
        # preserve presence-based lookup without falling back to attributes.
        if key in primary:
            return primary[key]
        if manifest:
            return resolve_persona_setting(key, overlay, primary, manifest=manifest, default=default)
    return getattr(owner, key, default)

__all__ = ["lookup_runtime_setting"]
