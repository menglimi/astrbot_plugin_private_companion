from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from astrbot_plugin_private_companion.persona_config import load_scope_manifest
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


ROOT = Path(__file__).resolve().parents[1]


class _MemoryHarness(UserMemoryMixin):
    enable_emotion_simulation = True
    enable_expression_learning = True
    enable_relationship_state_machine = True
    max_companion_memory_items = 36

    def __init__(self, overrides: dict[str, Any]) -> None:
        self.overrides = overrides

    def persona_setting(self, key: str, default: Any = None) -> Any:
        return self.overrides.get(key, getattr(self, key, default))


def test_memory_and_affect_paths_use_persona_overrides() -> None:
    harness = _MemoryHarness(
        {
            "enable_emotion_simulation": False,
            "enable_expression_learning": False,
            "enable_relationship_state_machine": False,
            "max_companion_memory_items": 1,
        }
    )
    user = {
        "companion_memory": {
            "items": [
                {"text": "first", "weight": 1},
                {"text": "second", "weight": 2},
            ]
        }
    }

    assert [item["text"] for item in harness._cleanup_companion_memory_items(user)] == ["second"]

    expression_owner: dict[str, Any] = {}
    harness._update_expression_profile_from_message(expression_owner, "好呀")
    assert "expression_profile" not in expression_owner

    interaction_owner: dict[str, Any] = {}
    harness._settle_current_interaction_from_intent(
        interaction_owner,
        {"intent": "intimacy", "confidence": 0.9},
    )
    assert "current_interaction" not in interaction_owner


def test_runtime_mixins_do_not_bypass_persona_setting_resolver() -> None:
    manifest = load_scope_manifest()
    manifest_aliases = {
        key.casefold(): key
        for key in manifest
        if key.isupper() and key.endswith("_PROVIDER_ID")
    }

    def manifest_key(runtime_key: str) -> str:
        return manifest_aliases.get(runtime_key.casefold(), runtime_key)

    targets = {
        "user_memory.py": "UserMemoryMixin",
        "memory_companion_adapter.py": "MemoryCompanionAdapterMixin",
        "daily_state_tick.py": "DailyStateTickMixin",
    }
    violations: list[str] = []
    invalid_resolver_calls: list[str] = []

    for filename, class_name in targets.items():
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"), filename=filename)
        owner = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        for node in ast.walk(owner):
            key = ""
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                key = node.attr
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr"}
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self"
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                key = node.args[1].value
            if key and manifest.get(manifest_key(key), {}).get("scope") == "persona":
                violations.append(f"{filename}:{node.lineno}:{key}")

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "runtime_persona_setting"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                ):
                    resolver_key = node.args[1].value
                    if manifest.get(manifest_key(resolver_key), {}).get("scope") != "persona":
                        invalid_resolver_calls.append(f"{filename}:{node.lineno}:{resolver_key}")

    assert violations == []
    assert invalid_resolver_calls == []
