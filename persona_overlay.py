"""Persona overlay resolution, independent from persistence and runtime."""
from __future__ import annotations
import copy
from typing import Any, Mapping

def resolve_overlay_setting(key: str, overlay: Mapping[str, Any], primary: Mapping[str, Any], manifest: Mapping[str, Mapping[str, Any]], default: Any = None) -> Any:
    if key in overlay: return copy.deepcopy(overlay[key])
    if key in primary and manifest.get(key, {}).get("inherit_primary", True): return copy.deepcopy(primary[key])
    entry = manifest.get(key)
    return copy.deepcopy(entry.get("default", default)) if entry else copy.deepcopy(default)

__all__ = ["resolve_overlay_setting"]
