from __future__ import annotations

from pathlib import Path

import pytest

from astrbot_plugin_private_companion.config_schema import load_config_schema, grouped_schema_leaves
from astrbot_plugin_private_companion.config_defaults import schema_defaults
from astrbot_plugin_private_companion.config_validation import validate_setting
from astrbot_plugin_private_companion.persona_overlay import resolve_overlay_setting
from astrbot_plugin_private_companion.runtime_lookup import lookup_runtime_setting


ROOT = Path(__file__).resolve().parents[1]


def test_schema_loader_has_one_canonical_path_and_no_magic_leaf_count():
    schema = load_config_schema(ROOT / "_conf_schema.json")
    leaves = grouped_schema_leaves(schema)
    assert leaves
    assert len(leaves) == len(set(leaves))


def test_defaults_are_schema_owned_and_deep_copied():
    schema = load_config_schema(ROOT / "_conf_schema.json")
    first = schema_defaults(schema)
    first["photo_reference_library"].append("changed")
    second = schema_defaults(schema)
    assert "changed" not in second["photo_reference_library"]


def test_validation_rejects_bad_values_without_coercion():
    field = {"type": "int", "default": 3, "min": 1, "max": 10}
    assert validate_setting("limit", 7, field) == 7
    with pytest.raises(ValueError):
        validate_setting("limit", "7", field)
    with pytest.raises(ValueError):
        validate_setting("limit", 99, field)


def test_overlay_preserves_explicit_false_and_empty_values():
    manifest = {"enabled": {"default": True}, "prompt": {"default": "base"}}
    assert resolve_overlay_setting("enabled", {"enabled": False}, {}, manifest) is False
    assert resolve_overlay_setting("prompt", {"prompt": ""}, {"prompt": "primary"}, manifest) == ""


def test_runtime_lookup_uses_one_resolver_before_host_attribute():
    class Host:
        config = {"persona_settings": {"p": {"limit": 9}}}
        active_persona_id = "p"
        limit = 1

    assert lookup_runtime_setting(Host(), "limit", 0) == 9
    assert lookup_runtime_setting(Host(), "missing", 4) == 4
