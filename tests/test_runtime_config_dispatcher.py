# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_private_companion.runtime_config_dispatcher import (
    dispatch_runtime_config_effects,
)


def test_global_runtime_effects_share_one_dispatch_boundary() -> None:
    calls: list[tuple[str, Any]] = []

    class Adapter:
        def _schedule_body_monitor_integration_toggle(self, enabled: bool) -> None:
            calls.append(("body", enabled))

        def _create_page_background_task(self, awaitable: Any, *, label: str) -> None:
            awaitable.close()
            calls.append(("kick", label))

        @staticmethod
        def _config_overlay(values: dict[str, Any]) -> dict[str, Any]:
            return {"overlay": dict(values)}

    plugin = SimpleNamespace(
        config={},
        data={"daily_review_case_audit": [{"old": True}]},
        _prepare_multi_persona_transition=lambda enabled: calls.append(
            ("persona_transition", enabled)
        ),
        _rebuild_store_manager=lambda **kwargs: calls.append(("store", kwargs)),
        _load_tts_enhancement_config=lambda config: calls.append(("tts", config)),
        _schedule_data_save=lambda **kwargs: calls.append(("data", kwargs)),
    )

    async def kick() -> None:
        return None

    plugin._kick_proactive_loop_once = kick
    dispatch_runtime_config_effects(
        plugin,
        {
            "enable_body_monitor_integration": True,
            "enable_multi_persona_mode": True,
            "max_daily_messages": 4,
            "storage_backend": "sqlite",
            "tts_generation_mode": "tool",
            "enable_daily_case_review_experiment": False,
        },
        source="test",
        adapter=Adapter(),
        apply_plain_values=True,
    )

    assert plugin.enable_body_monitor_integration is True
    assert plugin.enable_multi_persona_mode is True
    assert plugin.data["daily_review_case_audit"] == []
    assert [name for name, _value in calls].count("body") == 1
    assert [name for name, _value in calls].count("persona_transition") == 1
    assert [name for name, _value in calls].count("kick") == 1
    assert [name for name, _value in calls].count("store") == 1
    assert [name for name, _value in calls].count("tts") == 1
    assert [name for name, _value in calls].count("data") == 1


def test_timezone_runtime_effect_receives_previous_and_current_timezone() -> None:
    calls: list[tuple[str, Any]] = []

    class Adapter:
        def _create_page_background_task(self, awaitable: Any, *, label: str) -> None:
            awaitable.close()
            calls.append(("kick", label))

    async def kick() -> None:
        return None

    plugin = SimpleNamespace(
        config={},
        environment_perception_timezone="Asia/Shanghai",
        _invalidate_timezone_derived_state=lambda previous, current: calls.append(
            ("invalidate", (previous, current))
        ),
        _kick_proactive_loop_once=kick,
    )

    dispatch_runtime_config_effects(
        plugin,
        {"environment_perception_timezone": "Asia/Tokyo"},
        source="test",
        adapter=Adapter(),
        apply_plain_values=True,
    )

    assert ("invalidate", ("Asia/Shanghai", "Asia/Tokyo")) in calls
    assert [name for name, _value in calls].count("kick") == 1


def test_persona_runtime_effects_use_same_dispatch_boundary() -> None:
    calls: list[tuple[str, Any]] = []

    async def kick() -> None:
        return None

    def create(awaitable: Any, *, label: str) -> object:
        awaitable.close()
        calls.append(("kick", label))
        return object()

    plugin = SimpleNamespace(
        config={},
        _sanitize_persona_id=lambda value: str(value),
        _reset_persona_prompt_caches=lambda pid: calls.append(("reset", pid)),
        _activate_persona_id=lambda pid, **_kwargs: f"token:{pid}",
        _deactivate_persona_for_event=lambda token: calls.append(
            ("deactivate", token)
        ),
        _refresh_expression_voice_profile=lambda: calls.append(("voice", True)),
        _import_worldbook_entries_from_sources=lambda: False,
        _kick_proactive_loop_once=kick,
        _create_lifecycle_background_task=create,
    )
    dispatch_runtime_config_effects(
        plugin,
        {
            "bot_name": None,
            "worldbook_config_paths": None,
            "max_daily_messages": None,
        },
        scope="persona",
        persona_id="alt",
        source="test",
    )

    assert ("reset", "alt") in calls
    assert ("voice", True) in calls
    assert any(name == "kick" for name, _value in calls)


def test_legacy_entrypoints_are_effect_free_adapters() -> None:
    root = Path(__file__).resolve().parents[1]
    expectations = {
        "page_api.py": "_apply_config_value",
        "command_handlers.py": "_companion_manual_apply_config_value",
        "main.py": "_apply_persona_setting_hot_effects",
    }
    forbidden = {
        "_schedule_body_monitor_integration_toggle",
        "_prepare_multi_persona_transition",
        "_kick_proactive_loop_once",
        "_rebuild_store_manager",
        "_load_tts_enhancement_config",
        "_schedule_data_save",
    }
    for filename, function_name in expectations.items():
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        )
        calls = {
            node.func.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        assert calls.isdisjoint(forbidden)
        if function_name != "_apply_config_value":
            assert any(
                isinstance(node, ast.Call)
                and (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "dispatch_runtime_config_effects"
                )
                for node in ast.walk(function)
            )
