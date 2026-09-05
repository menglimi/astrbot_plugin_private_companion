"""Schema-derived configuration defaults."""
from __future__ import annotations
import copy
from typing import Any, Mapping
from .config_schema import grouped_schema_leaves, load_config_schema

def field_default(field: Mapping[str, Any]) -> Any:
    if "default" in field: return copy.deepcopy(field["default"])
    return {"bool": False, "int": 0, "float": 0.0, "list": [], "template_list": [], "object": ""}.get(str(field.get("type") or ""), "")

def schema_defaults(schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    leaves = grouped_schema_leaves(schema if schema is not None else load_config_schema())
    return {key: field_default(item["field"]) for key, item in leaves.items()}

__all__ = ["field_default", "schema_defaults"]
