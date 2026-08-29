from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
import importlib.util
import json
from pathlib import Path
import random
from types import SimpleNamespace
from typing import Any

import pytest

from astrbot_plugin_private_companion import lab_fixture_adapter as fixture_module
from astrbot_plugin_private_companion.lab_fixture_adapter import (
    CompanionLabFixtureAdapter,
    SCHEMA,
    register_companion_lab_fixture_adapter,
)
from astrbot_plugin_private_companion.plugin_identity import PLUGIN_ID
from astrbot_plugin_private_companion.relationship_policy import relationship_stage_for_score


ROOT = Path(__file__).resolve().parents[1]
LAB_FIXTURE_CONTRACT = (
    ROOT.parent.parent / "astrbot-test-lab" / "src" / "astrbot_test_lab" / "fixture_contract.py"
)


def _load_current_lab_fixture_contract():
    if not LAB_FIXTURE_CONTRACT.is_file():
        pytest.skip("adjacent astrbot-test-lab fixture contract is unavailable")
    spec = importlib.util.spec_from_file_location(
        "companion_lab_fixture_contract_v2",
        LAB_FIXTURE_CONTRACT,
    )
    assert spec is not None and spec.loader is not None
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    return contract


def _group_wakeup_method(name: str):
    tree = ast.parse((ROOT / "group_wakeup.py").read_text(encoding="utf-8"))
    mixin = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GroupWakeupMixin"
    )
    method = next(
        node
        for node in mixin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    namespace = {
        "Any": Any,
        "Mapping": Mapping,
        "_persona_value": lambda owner, key, default=None: getattr(owner, key, default),
        "_single_line": lambda value, limit: " ".join(str(value or "").split())[:limit],
        "_group_link_message_context": lambda value: (str(value or ""), False),
        "_now_ts": lambda: 1000.0,
        "_safe_int": lambda value, default=0, minimum=None, maximum=None: max(
            minimum if minimum is not None else int(value or default),
            min(maximum if maximum is not None else int(value or default), int(value or default)),
        ),
        "_safe_float": lambda value, default=0.0, minimum=None, maximum=None: max(
            minimum if minimum is not None else float(value or default),
            min(maximum if maximum is not None else float(value or default), float(value or default)),
        ),
        "random": random,
        "logger": SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / "group_wakeup.py"), "exec"), namespace)
    return namespace[name]


def _main_method(name: str):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    plugin = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
    )
    method = next(
        node
        for node in plugin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    method.decorator_list = []
    namespace = {
        "Any": Any,
        "AstrMessageEvent": object,
        "ProviderRequest": object,
        "PLACEMENT_DYNAMIC_SYSTEM": "dynamic_system",
        "runtime_persona_setting": lambda _owner, key, default=None: (
            True if key == "enable_custom_relationship_stage_policy" else default
        ),
        "content_intent_from_text": lambda _text: {"requested_content_tier": "normal"},
        "expression_decision_prompt": lambda projection: (
            f"stage={projection.get('relationship_stage', '')}"
        ),
        "build_expression_decision": lambda _source: SimpleNamespace(
            to_dict=lambda: {"relationship_stage": "fallback"}
        ),
        "_now_ts": lambda: 1000.0,
        "_single_line": lambda value, limit: str(value or "")[:limit],
        "logger": SimpleNamespace(debug=lambda *_args, **_kwargs: None),
    }
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / "main.py"), "exec"),
        namespace,
    )
    return namespace[name]


class _Event:
    def __init__(self, umo: str, actor_id: str) -> None:
        self.unified_msg_origin = umo
        self._actor_id = actor_id
        self.message_str = "请按当前关系自然回应"

    def get_sender_id(self) -> str:
        return self._actor_id


def _scope(umo: str = "lab:FriendMessage:actor-a", actor_id: str = "actor-a") -> dict:
    return {"effective_umo": umo, "effective_actor_id": actor_id}


def _payload(**overrides) -> dict:
    payload = {
        "relationship_score": 650,
        "relationship_role": "friend",
        "relationship_mode": "normal",
        "positive_stage_cap_key": "deeply_bonded",
        "previous_stage_key": "",
        "interaction_band": "relaxed",
        "group_interest_words": ["LAB_TOPIC_ALPHA"],
        "group_interest_min_probability": 1.0,
    }
    payload.update(overrides)
    return payload


def test_adapter_is_exactly_scoped_derived_and_released() -> None:
    adapter = CompanionLabFixtureAdapter()
    assert adapter.fixture_schemas == (SCHEMA,)
    assert adapter.fixture_capabilities == (
        "final_projection",
        "residual_projection",
    )
    capability = object()
    adapter.prepare_fixture("run-a", SCHEMA, _scope(), _payload(), capability)

    source = {
        "user_id": "actor-a",
        "relationship_score": -900,
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
    }
    matching = _Event("lab:FriendMessage:actor-a", "actor-a")
    projected = adapter.overlay_relationship_view(matching, source)

    assert projected is not source
    assert source["relationship_score"] == -900
    assert projected["relationship_score"] == 650
    assert projected["relationship_role"] == "friend"
    assert projected["current_interaction"]["expression_band"] == "relaxed"
    assert adapter.overlay_relationship_view(
        _Event("lab:FriendMessage:actor-b", "actor-b"), source
    ) is source
    assert adapter.overlay_relationship_view(
        _Event("lab:FriendMessage:actor-a", "actor-b"), source
    ) is source

    projection = adapter.describe_applied_fixture("run-a")
    projection_text = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    assert len(projection_text.encode("utf-8")) < 4096
    assert projection["relationship"]["stage_key"] == "close"
    assert projection["observations"]["relationship_view_count"] == 1
    assert projection["group_wakeup"]["interest_word_count"] == 1
    assert "actor-a" not in str(projection)
    assert "lab:FriendMessage" not in str(projection)
    assert "LAB_TOPIC_ALPHA" not in str(projection)
    assert adapter.describe_released_fixture("run-a") == {
        "active": True,
        "residual_count": 1,
        "residual_status": "present",
    }

    adapter.release_fixture("run-a")
    adapter.release_fixture("run-a")
    assert adapter.describe_released_fixture("run-a") == {
        "active": False,
        "residual_count": 0,
        "residual_status": "clear",
    }
    assert adapter.overlay_relationship_view(matching, source) is source
    with pytest.raises(KeyError):
        adapter.describe_applied_fixture("run-a")


def test_adapter_rejects_serialized_capability_and_invalid_input_before_mutation() -> None:
    adapter = CompanionLabFixtureAdapter()
    with pytest.raises(PermissionError):
        adapter.prepare_fixture("run-a", SCHEMA, _scope(), _payload(), {"forged": True})
    with pytest.raises(ValueError, match="LAB_"):
        adapter.prepare_fixture(
            "run-a",
            SCHEMA,
            _scope(),
            _payload(group_interest_words=["ordinary-production-word"]),
            object(),
        )
    with pytest.raises(ValueError, match="unsupported"):
        adapter.prepare_fixture(
            "run-a",
            SCHEMA,
            _scope(),
            {**_payload(), "unexpected": True},
            object(),
        )
    with pytest.raises(ValueError, match="8 KiB"):
        adapter.prepare_fixture(
            "run-a",
            SCHEMA,
            _scope(),
            _payload(group_interest_words=[f"LAB_{'A' * 9000}"]),
            object(),
        )
    with pytest.raises(KeyError):
        adapter.describe_applied_fixture("run-a")


def test_concurrent_runs_are_isolated_and_same_scope_is_rejected() -> None:
    adapter = CompanionLabFixtureAdapter()
    adapter.prepare_fixture("run-a", SCHEMA, _scope(), _payload(), object())
    adapter.prepare_fixture(
        "run-b",
        SCHEMA,
        _scope("lab:GroupMessage:group-b", "actor-b"),
        _payload(relationship_score=-600),
        object(),
    )
    assert adapter.overlay_relationship_view(
        _Event("lab:GroupMessage:group-b", "actor-b"), {}
    )["relationship_score"] == -600
    with pytest.raises(RuntimeError, match="already active"):
        adapter.prepare_fixture("run-c", SCHEMA, _scope(), _payload(), object())
    adapter.release_fixture("run-a")
    assert adapter.describe_applied_fixture("run-b")["active"] is True


def test_optional_registration_uses_only_the_injected_gate(monkeypatch) -> None:
    capability = object()
    registrations: dict[str, tuple[object, object]] = {}

    def register(plugin_id, adapter, registered_capability):
        assert registered_capability is capability
        registrations[plugin_id] = (adapter, registered_capability)

    fake_module = SimpleNamespace(
        establish_fixture_capability=lambda: capability,
        fixture_capability_is_valid=lambda candidate: candidate is capability,
        register_fixture_adapter=register,
    )
    monkeypatch.setattr(
        fixture_module.importlib,
        "import_module",
        lambda name: fake_module if name == "astrbot_test_lab_fixture" else None,
    )

    adapter = register_companion_lab_fixture_adapter()

    assert adapter is registrations[PLUGIN_ID][0]
    assert all(value is not capability for value in adapter.__dict__.values())

    missing = ModuleNotFoundError("missing Lab fixture gate")
    missing.name = "astrbot_test_lab_fixture"
    monkeypatch.setattr(
        fixture_module.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(missing),
    )
    assert register_companion_lab_fixture_adapter() is None


def test_registration_rejects_invalid_or_legacy_gate_and_closes_on_failure(
    monkeypatch,
) -> None:
    capability = object()
    register_called = False

    def should_not_register(*_args):
        nonlocal register_called
        register_called = True

    invalid_gate = SimpleNamespace(
        establish_fixture_capability=lambda: capability,
        fixture_capability_is_valid=lambda _candidate: False,
        register_fixture_adapter=should_not_register,
    )
    monkeypatch.setattr(
        fixture_module.importlib,
        "import_module",
        lambda _name: invalid_gate,
    )
    with pytest.raises(PermissionError, match="invalid Test Lab fixture capability"):
        register_companion_lab_fixture_adapter()
    assert register_called is False

    legacy_gate = SimpleNamespace(
        establish_fixture_capability=lambda: capability,
        fixture_capability_is_valid=lambda candidate: candidate is capability,
        register_fixture_adapter=lambda _plugin_id, _adapter: None,
    )
    monkeypatch.setattr(
        fixture_module.importlib,
        "import_module",
        lambda _name: legacy_gate,
    )
    with pytest.raises(TypeError):
        register_companion_lab_fixture_adapter()

    captured: dict[str, CompanionLabFixtureAdapter] = {}

    def failing_register(_plugin_id, adapter, _capability):
        captured["adapter"] = adapter
        raise RuntimeError("synthetic registration failure")

    failing_gate = SimpleNamespace(
        establish_fixture_capability=lambda: capability,
        fixture_capability_is_valid=lambda candidate: candidate is capability,
        register_fixture_adapter=failing_register,
    )
    monkeypatch.setattr(
        fixture_module.importlib,
        "import_module",
        lambda _name: failing_gate,
    )
    with pytest.raises(RuntimeError, match="synthetic registration failure"):
        register_companion_lab_fixture_adapter()
    assert captured["adapter"]._closed is True
    assert captured["adapter"].describe_released_fixture("run-never-applied") == {
        "active": False,
        "residual_count": 0,
        "residual_status": "clear",
    }


def test_current_lab_fixture_v2_contract_is_compatible(monkeypatch) -> None:
    contract = _load_current_lab_fixture_contract()
    assert contract.FIXTURE_CONTRACT_VERSION == 2
    monkeypatch.setattr(
        fixture_module.importlib,
        "import_module",
        lambda name: contract if name == "astrbot_test_lab_fixture" else None,
    )

    adapter = register_companion_lab_fixture_adapter()
    assert adapter is not None
    capability = contract.establish_fixture_capability()
    assert contract.fixture_capability_is_valid(capability)
    assert all(value is not capability for value in adapter.__dict__.values())
    assert contract.registered_fixture_adapters(capability) == (
        {
            "plugin_id": PLUGIN_ID,
            "contract_version": 2,
            "schemas": [SCHEMA],
            "capabilities": ["final_projection", "residual_projection"],
            "final_projection": True,
            "residual_projection": True,
            "release_idempotent": True,
        },
    )
    with pytest.raises(PermissionError, match="invalid Test Lab fixture capability"):
        contract.register_fixture_adapter("companion-forged", adapter, object())

    applied = contract.prepare_registered_fixture(
        PLUGIN_ID,
        "run-contract-v2",
        SCHEMA,
        _scope(),
        _payload(),
        capability,
    )
    assert applied["active"] is True
    assert applied["schema"] == SCHEMA
    assert contract.describe_registered_fixture(
        PLUGIN_ID,
        "run-contract-v2",
        capability,
        phase="final",
    )["relationship"]["stage_key"] == "close"
    assert contract.release_registered_fixture(
        PLUGIN_ID,
        "run-contract-v2",
        capability,
    ) is True
    assert contract.describe_registered_fixture(
        PLUGIN_ID,
        "run-contract-v2",
        capability,
        phase="residual",
    ) == {
        "active": False,
        "residual_count": 0,
        "residual_status": "clear",
    }


class _WakeupHarness:
    enable_group_wakeup_enhancement = True
    enable_group_wakeup_question = False
    enable_group_wakeup_cold_group = False
    enable_group_bot_name_wakeup = False
    group_wakeup_direct_words: list[str] = []
    group_wakeup_owner_direct_words: list[str] = []
    group_wakeup_context_words: list[str] = []
    group_wakeup_interest_keywords: list[str] = []
    group_wakeup_interest_probability = 0.0
    group_wakeup_generated_keyword_limit = 8
    group_wakeup_cooldown_seconds = 0
    group_wakeup_fatigue_limit = 100
    group_wakeup_fatigue_decay_minutes = 10

    def __init__(self, adapter: CompanionLabFixtureAdapter) -> None:
        self._lab_fixture_adapter = adapter
        self.data = {}

    _evaluate_group_wakeup = _group_wakeup_method("_evaluate_group_wakeup")

    @staticmethod
    def _parse_text_list_config(value, *, limit=40):
        return list(value or [])[:limit] if isinstance(value, list) else []

    @staticmethod
    def _group_sender_is_primary_user(_sender_id):
        return False

    @staticmethod
    def _configured_group_owner_direct_wakeup_words():
        return []

    @staticmethod
    def _configured_group_direct_wakeup_words():
        return []

    @staticmethod
    def _text_contains_wakeup_word(text, word):
        return str(word).casefold() in str(text).casefold()

    @staticmethod
    def _group_wakeup_question_signal(_text):
        return {}

    @staticmethod
    def _group_wakeup_cold_group_signal(_group, _text, _now):
        return {}

    @staticmethod
    def _group_wakeup_interest_words(_group):
        return []

    @staticmethod
    def _group_high_intensity_state(_group):
        return {"active": False}

    @staticmethod
    def _group_wakeup_fatigue(_group):
        return {}

    @staticmethod
    def _group_wakeup_probability_context(_group, _scene, probability, _kind):
        return probability, {}

    @staticmethod
    def _group_wakeup_strength(_kind, _group, _scene):
        return "normal"

    @staticmethod
    def _group_wakeup_topic_interest_weight(group, word, *, sender_id, text, group_id):
        return {"multiplier": 1.0, "score": 0.0, "reason": ""}

    @staticmethod
    def _select_worldbook_member_profiles_for_group(group, *, sender_id, text):
        return []


def test_group_interest_fixture_uses_the_production_wakeup_path() -> None:
    adapter = CompanionLabFixtureAdapter()
    adapter.prepare_fixture(
        "run-group",
        SCHEMA,
        _scope("lab:GroupMessage:group-a", "actor-a"),
        _payload(),
        object(),
    )
    harness = _WakeupHarness(adapter)
    event = _Event("lab:GroupMessage:group-a", "actor-a")

    wakeup = harness._evaluate_group_wakeup(
        {},
        event=event,
        scene={"talking_to": "group", "trigger": "normal"},
        sender_id="actor-a",
        sender_name="测试用户",
        text="我们继续聊 LAB_TOPIC_ALPHA 的进展",
        group_id="group-a",
    )

    assert wakeup["type"] == "interest"
    assert wakeup["reason"] == "interest_keyword"
    assert wakeup["probability"] == 1.0
    assert adapter.describe_applied_fixture("run-group")["observations"][
        "group_setting_read_count"
    ] == 1


class _ExpressionHarness:
    enabled = True
    inject_unified_relationship_expression = _main_method(
        "inject_unified_relationship_expression"
    )

    def __init__(self, adapter: CompanionLabFixtureAdapter) -> None:
        self._lab_fixture_adapter = adapter
        self.data = {
            "users": {
                "actor-a": {
                    "user_id": "actor-a",
                    "relationship_score": -900,
                    "relationship_role": "friend",
                    "relationship_mode": "normal",
                }
            }
        }
        self.captured: dict[str, Any] = {}

    @staticmethod
    def _safe_event_is_private(_event):
        return True

    @staticmethod
    def _safe_event_sender_id(event):
        return event.get_sender_id()

    @staticmethod
    def _private_user_id_for_event(event):
        return event.get_sender_id()

    def _lab_fixture_relationship_view(self, event, user):
        return self._lab_fixture_adapter.overlay_relationship_view(event, user)

    def _build_expression_decision_for_user(self, user, **kwargs):
        projection = relationship_stage_for_score(
            user.get("relationship_score", 0),
            previous_stage_key=user.get("relationship_phase_key", ""),
        )
        self.captured = {"user": user, "kwargs": kwargs}
        return SimpleNamespace(
            to_dict=lambda: {
                "relationship_stage": projection["phase"]["key"],
                "relationship_score": user.get("relationship_score"),
                "interaction_band": user.get("current_interaction", {}).get(
                    "expression_band"
                ),
            }
        )

    def _append_turn_prompt_fragment_by_position(self, req, marker, content, **kwargs):
        self.captured["prompt_fragment"] = {
            "marker": marker,
            "content": content,
            "kwargs": kwargs,
        }


def test_relationship_fixture_reaches_the_production_expression_hook() -> None:
    adapter = CompanionLabFixtureAdapter()
    adapter.prepare_fixture("run-expression", SCHEMA, _scope(), _payload(), object())
    harness = _ExpressionHarness(adapter)
    event = _Event("lab:FriendMessage:actor-a", "actor-a")
    req = SimpleNamespace(system_prompt="")

    asyncio.run(harness.inject_unified_relationship_expression(event, req))

    projection = req._private_companion_expression_decision
    assert projection["relationship_score"] == 650
    assert projection["relationship_stage"] == "close"
    assert projection["interaction_band"] == "relaxed"
    assert harness.captured["kwargs"]["_authoritative_relationship_view"] is True
    assert harness.data["users"]["actor-a"]["relationship_score"] == -900
    assert "stage=close" in harness.captured["prompt_fragment"]["content"]
