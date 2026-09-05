"""Canonical configuration schema loading and grouped-leaf discovery."""
from __future__ import annotations
import copy, json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_SCHEMA_PATH = Path(__file__).with_name("_conf_schema.json")

@lru_cache(maxsize=4)
def _read_schema(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def load_config_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Load a detached schema snapshot; repeated lookups read the file once."""
    return copy.deepcopy(_read_schema(str(Path(path) if path is not None else _DEFAULT_SCHEMA_PATH)))

def grouped_schema_leaves(schema: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    def walk(items: Mapping[str, Any], group: str) -> None:
        for key, field in items.items():
            if not isinstance(field, dict): continue
            nested = field.get("items")
            if field.get("type") == "object" and isinstance(nested, dict) and nested:
                walk(nested, group)
            else:
                if key in result: raise ValueError(f"duplicate grouped schema key: {key}")
                result[str(key)] = {"key": str(key), "schema_group": group, "field": copy.deepcopy(field)}
    for group, node in schema.items():
        if isinstance(node, dict) and isinstance(node.get("items"), dict) and node["items"]:
            walk(node["items"], str(group))
    return result

__all__ = ["load_config_schema", "grouped_schema_leaves"]
