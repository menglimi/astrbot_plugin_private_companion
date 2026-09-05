"""Strict configuration value validation."""
from __future__ import annotations
from typing import Any, Mapping

def validate_setting(key: str, value: Any, field: Mapping[str, Any]) -> Any:
    kind = str(field.get("type") or "")
    valid = {"bool": bool, "int": int, "float": (int, float), "str": str, "string": str, "list": list, "template_list": list, "object": dict}.get(kind)
    if valid is not None and (not isinstance(value, valid) or (kind == "int" and isinstance(value, bool))):
        raise ValueError(f"invalid type for {key}: expected {kind}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in field and value < field["min"]: raise ValueError(f"{key} below minimum")
        if "max" in field and value > field["max"]: raise ValueError(f"{key} above maximum")
    options = field.get("options")
    if options and value not in options: raise ValueError(f"invalid option for {key}")
    return value

__all__ = ["validate_setting"]
