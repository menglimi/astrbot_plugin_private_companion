from __future__ import annotations

import importlib
import os
import sys
import types
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PEIBAN_ROOT = ROOT.parents[1]
_MEMORY_ROOT_CANDIDATES = (
    Path(os.environ["ASTRBOT_MEMORY_PLUGIN_ROOT"])
    if os.environ.get("ASTRBOT_MEMORY_PLUGIN_ROOT")
    else ROOT / ".missing-memory-root",
    ROOT.parent / "memory",
    PEIBAN_ROOT / "astrbot_plugin_memory_companion-main",
)
MEMORY_ROOT = next(
    (
        path
        for path in _MEMORY_ROOT_CANDIDATES
        if (path / "core" / "bridge.py").is_file()
    ),
    _MEMORY_ROOT_CANDIDATES[0],
)


def _load_memory_package():
    package_name = "c2_dual_memory"
    package = types.ModuleType(package_name)
    package.__path__ = [str(MEMORY_ROOT)]
    sys.modules[package_name] = package
    core = types.ModuleType(f"{package_name}.core")
    core.__path__ = [str(MEMORY_ROOT / "core")]
    sys.modules[core.__name__] = core
    return importlib.import_module(f"{package_name}.core.bridge")


from context_orchestration import build_context
from person_context_contract import build_identity_key, empty_person_store
from unified_person_registry import UnifiedPersonRegistry


IDENTITY = {
    "companion_instance_id": "chat-companion",
    "bot_account_id": "qq:bot-1",
    "adapter_instance_id": "qq:adapter-1",
    "subject_namespace": "qq:user",
    "platform_subject_id": "user-42",
}


def test_companion_projection_is_consumed_by_memory_bridge_without_cross_domain_write():
    store = {"unified_person": empty_person_store()}
    registry = UnifiedPersonRegistry(store)
    created = registry.create_or_link(
        IDENTITY,
        profile={"display_name": "Alice", "affinity_score": 10},
        operation_id="c2-dual-create",
    )
    assert created["state"] == "resolved"
    person_id = created["person_id"]
    identity_key = build_identity_key(IDENTITY)
    projection = registry.read_projection(person_id)
    p3 = build_context(
        persona={"companion_instance_id": "chat-companion"},
        runtime={"platform": "qq", "scope": "private"},
        person={"person_id": person_id, "projection_revision": projection["projection_revision"]},
        scene={"scope": "private", "group_scope": ""},
    )
    p3["person_id"] = person_id
    p3["scope"] = "private"
    before = deepcopy(store)

    bridge_module = _load_memory_package()
    bridge = bridge_module.MemoryCompanionBridge(types.SimpleNamespace())
    person_result = bridge.consume_person_projection(
        projection,
        expected_identity_key=identity_key,
        expected_person_id=person_id,
    )
    context_result = bridge.consume_context_projection(
        p3,
        expected_person_id=person_id,
        expected_scope="private",
    )

    assert person_result["state"] == "resolved"
    assert context_result["state"] == "ready"
    assert "display_name" not in person_result["projection_ref"]
    assert store == before


def test_group_overlay_stays_scoped_and_memory_rejects_wrong_scope():
    store = {"unified_person": empty_person_store()}
    registry = UnifiedPersonRegistry(store)
    created = registry.create_or_link(IDENTITY, operation_id="c2-group-create")
    person_id = created["person_id"]
    first = registry.upsert_group_overlay(person_id, "qq:group:one", {"tone": "warm"}, operation_id="g1")
    second = registry.upsert_group_overlay(person_id, "qq:group:two", {"tone": "quiet"}, operation_id="g2")
    assert first["ok"] and second["ok"]
    assert registry.read_group_overlay(person_id, "qq:group:one")["overlay"]["tone"] == "warm"
    assert registry.read_group_overlay(person_id, "qq:group:two")["overlay"]["tone"] == "quiet"
    assert registry.read_group_overlay(person_id, "qq:group:missing") is None
