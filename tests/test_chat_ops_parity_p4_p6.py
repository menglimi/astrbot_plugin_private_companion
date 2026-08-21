from __future__ import annotations

import ast
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from p4_affinity_confinement import (  # noqa: E402
    P4_RUNTIME_AUTHORITY,
    P4_RUNTIME_STATE_SCHEMA,
    apply_legacy_relationship_delta,
    validate_runtime_state,
)
from p4_b1b_guards import (  # noqa: E402
    HOST_OWNER_CAPABILITY_UNAVAILABLE_CODE,
    REVIEW_PRODUCER_UNAVAILABLE_CODE,
    reject_unverified_owner_capability,
    reject_unverified_review_producer,
)
from p4_live_runtime import decide_live_request  # noqa: E402
from p4_runtime_gate import SAFE_CONFINEMENT_REPLY, apply_confinement_gate, build_warmth_projection  # noqa: E402
from p6_four_package_manifest import FOUR_PACKAGE_IDS, FOUR_PACKAGE_MANIFEST_SCHEMA, verify_four_package_manifests  # noqa: E402
from p6_readonly_projection import build_p6_readonly_status  # noqa: E402
from persona_config import runtime_persona_setting  # noqa: E402
from relationship_ledger import apply_relationship_event, migrate_legacy_relationship_score  # noqa: E402
from unified_person_registry import UnifiedPersonRegistry  # noqa: E402


IDENTITY = {
    "companion_instance_id": "companion",
    "bot_account_id": "qq:bot",
    "adapter_instance_id": "qq:main",
    "subject_namespace": "qq:user",
    "platform_subject_id": "u-1",
}


def live_state(**changes: object) -> dict[str, str]:
    result = {
        "schema_version": P4_RUNTIME_STATE_SCHEMA,
        "authority": P4_RUNTIME_AUTHORITY,
        "confinement_state": "none",
        "confinement_until": "",
        "warmth": "warm",
    }
    result.update(changes)
    return result


def _load_p4_live_state_for_event() -> object:
    """Compile only the chat-side lookup method without importing AstrBot."""
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = deepcopy(next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_p4_live_state_for_event"
    ))
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            method,
        ],
        type_ignores=[],
    )
    namespace = {
        "_single_line": lambda value, limit: value[:limit] if type(value) is str else "",
    }
    exec(compile(ast.fix_missing_locations(module), str(ROOT / "main.py"), "exec"), namespace)
    return namespace["_p4_live_state_for_event"]


def _load_p4_relationship_event_settlement() -> object:
    """Compile the central score-write gate without importing AstrBot."""
    source = (ROOT / "core_store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CoreStoreMixin"
    )
    method = deepcopy(next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "_apply_relationship_event"
    ))
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            method,
        ],
        type_ignores=[],
    )
    namespace = {
        "Any": object,
        "apply_relationship_event": apply_relationship_event,
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
        "runtime_persona_setting": runtime_persona_setting,
    }
    exec(compile(ast.fix_missing_locations(module), str(ROOT / "core_store.py"), "exec"), namespace)
    return namespace["_apply_relationship_event"]


class P4P6ParityTests(unittest.TestCase):
    class _HashCollisionKey:
        def __init__(self, field: str = "schema_version") -> None:
            self._field = field
            self.hash_calls = 0
            self.eq_calls = 0
            self.str_calls = 0

        def __hash__(self) -> int:
            self.hash_calls += 1
            return hash(self._field)

        def __eq__(self, other: object) -> bool:
            self.eq_calls += 1
            return False

        def __str__(self) -> str:
            self.str_calls += 1
            return "hostile-key"

    class _HostileSentinel:
        __hash__ = object.__hash__

        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"hostile sentinel was accessed through {name}")

        def __str__(self) -> str:
            raise AssertionError("hostile sentinel was stringified")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("hostile sentinel was compared")

    class _HostileValue:
        def __hash__(self) -> int:
            raise AssertionError("hostile value was hashed")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("hostile value was compared")

        def __str__(self) -> str:
            raise AssertionError("hostile value was stringified")

    class _HostileLegacyScore:
        def __int__(self) -> int:
            raise AssertionError("hostile legacy score was coerced")

        def __str__(self) -> str:
            raise AssertionError("hostile legacy score was stringified")

    def test_effect_ledger_is_replayable_idempotent_and_corruption_safe(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        person_id = registry.create_or_link(IDENTITY, operation_id="create-1")["person_id"]
        event = {"event_id": "event-1", "occurred_at": "2026-07-31T12:00:00Z", "kind": "prepare", "shadow_only": True}
        first = registry.record_p4_effect_event(person_id, event, operation_id="op-1")
        repeated = registry.record_p4_effect_event(person_id, event, operation_id="op-1")
        conflict = registry.record_p4_effect_event(person_id, {**event, "kind": "other"}, operation_id="op-2")
        self.assertTrue(first["ok"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual("p4_effect_event_id_conflict", conflict["code"])
        store["unified_person"]["p4_effect"]["people"][person_id]["events"][0]["event_fingerprint"] = "broken"
        self.assertEqual("p4_effect_corrupt", registry.read_p4_effect_state(person_id)["code"])

    def test_effect_ledger_rejects_hostile_keys_and_optional_values_without_hooks(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        person_id = registry.create_or_link(IDENTITY, operation_id="create-hostile")["person_id"]
        base = {"event_id": "hostile-event", "occurred_at": "2026-07-31T12:00:00Z", "kind": "prepare"}
        hostile = self._HostileSentinel()
        hostile_key_result = registry.record_p4_effect_event(
            person_id,
            {**base, hostile: "not-reached"},
            operation_id="hostile-key",
        )
        hostile_value_result = registry.record_p4_effect_event(
            person_id,
            {**base, "authority": hostile},
            operation_id="hostile-value",
        )
        self.assertEqual("invalid_p4_effect_event", hostile_key_result["code"])
        self.assertEqual("invalid_p4_effect_event", hostile_value_result["code"])

    def test_effect_ledger_rejects_free_text_without_mutating_any_store_state(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        person_id = registry.create_or_link(IDENTITY, operation_id="create-structured")["person_id"]
        before_root = deepcopy(store["unified_person"])
        fields = ("event_id", "kind", "source_kind", "target_kind", "authority", "reason_code", "safe_reference")
        for field in fields:
            event = {
                "event_id": "event-structured",
                "occurred_at": "2026-07-31T12:00:00Z",
                "kind": "prepare",
            }
            event[field] = "this is free text"
            result = registry.record_p4_effect_event(person_id, event, operation_id=f"free-text-{field}")
            self.assertEqual("invalid_p4_effect_event", result["code"], field)
            self.assertEqual(before_root, store["unified_person"], field)

    def test_effect_ledger_rejects_non_ascii_and_invalid_timestamps_without_mutation(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        person_id = registry.create_or_link(IDENTITY, operation_id="create-time")["person_id"]
        before_root = deepcopy(store["unified_person"])
        for suffix, field, value in (
            ("unicode", "kind", "准备"),
            ("bad-date", "occurred_at", "2026-02-30T12:00:00Z"),
            ("no-zone", "occurred_at", "2026-07-31T12:00:00"),
        ):
            event = {"event_id": f"event-{suffix}", "occurred_at": "2026-07-31T12:00:00Z", "kind": "prepare"}
            event[field] = value
            result = registry.record_p4_effect_event(person_id, event, operation_id=f"reject-{suffix}")
            self.assertEqual("invalid_p4_effect_event", result["code"])
            self.assertEqual(before_root, store["unified_person"])

    def test_effect_ledger_accepts_structured_tokens_and_offset_timestamp(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        person_id = registry.create_or_link(IDENTITY, operation_id="create-valid")["person_id"]
        result = registry.record_p4_effect_event(
            person_id,
            {
                "event_id": "event-2",
                "occurred_at": "2026-07-31T12:00:00+00:00",
                "kind": "prepare",
                "source_kind": "companion",
                "target_kind": "person",
                "authority": "runtime",
                "reason_code": "preparation_only",
                "safe_reference": "ref-2",
                "shadow_only": True,
            },
            operation_id="valid-structured",
        )
        self.assertTrue(result["ok"])
        self.assertIn("valid-structured", store["unified_person"]["p4_effect"]["operations"])

    def test_p4_b1b_guards_reject_hostile_authority_candidates_without_reading_them(self) -> None:
        hostile = self._HostileSentinel()
        for guard, code in (
            (reject_unverified_owner_capability, HOST_OWNER_CAPABILITY_UNAVAILABLE_CODE),
            (reject_unverified_review_producer, REVIEW_PRODUCER_UNAVAILABLE_CODE),
        ):
            result = guard(hostile)
            self.assertEqual({"accepted": False, "live_effect_permitted": False, "code": code}, result)

    def test_missing_identity_merge_split_api_fails_closed_without_ledger_transition(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        source = registry.create_or_link(IDENTITY, operation_id="create-source")["person_id"]
        target = registry.create_or_link({**IDENTITY, "platform_subject_id": "u-2"}, operation_id="create-target")["person_id"]
        registry.record_p4_effect_event(
            source,
            {"event_id": "transition-event", "occurred_at": "2026-07-31T12:00:00Z", "kind": "prepare", "shadow_only": True},
            operation_id="transition-event-op",
        )
        before = deepcopy(store["unified_person"]["p4_effect"])
        for action in ("merge", "split"):
            result = registry.guard_p4_effect_person_transition(action, source, target, operation_id=f"{action}-op")
            self.assertEqual("p4_effect_transition_unsupported", result["code"])
            self.assertEqual(before, store["unified_person"]["p4_effect"])

    def test_live_gate_fails_closed_only_when_a_state_is_present(self) -> None:
        self.assertEqual("现在只能进行简短、尊重且安全的交流。", SAFE_CONFINEMENT_REPLY)
        boundaries = {
            "guarded": "请保持尊重、简短的交流。",
            "neutral": "保持尊重、自然交流。",
            "warm": "保持尊重与分寸。",
            "close": "保持尊重与分寸。",
        }
        for warmth, boundary in boundaries.items():
            self.assertEqual({"tier": warmth, "boundary": boundary}, build_warmth_projection(live_state(warmth=warmth)))
        self.assertEqual({"tier": "guarded", "boundary": boundaries["guarded"]}, build_warmth_projection({}))
        self.assertEqual("skip", decide_live_request(None)["decision"])
        active = live_state(confinement_state="active", confinement_until="2099-01-01T00:00:00Z")
        self.assertEqual({"tier": "guarded", "boundary": boundaries["guarded"]}, build_warmth_projection(active))
        result = apply_confinement_gate({"prompt": "secret", "tools": ["x"]}, {"authorized": True}, active)
        self.assertEqual("block", result["decision"])
        self.assertEqual(SAFE_CONFINEMENT_REPLY, result["reply_template"])
        self.assertEqual({"prompt": "cleared", "tool": "cleared", "external": "cleared"}, result["context_disposition"])
        self.assertEqual("block", decide_live_request({"forged": True})["decision"])

    def test_main_live_state_lookup_skips_group_before_identity_resolution(self) -> None:
        method = _load_p4_live_state_for_event()

        class GroupEvent:
            def is_private_chat(self) -> bool:
                return False

        class FakeSelf:
            resolver_calls = 0

            def resolve_unified_person_for_event(self, event: object) -> dict[str, str]:
                self.resolver_calls += 1
                raise AssertionError("group chat must not resolve a Unified Person")

        fake_self = FakeSelf()
        self.assertIsNone(method(fake_self, GroupEvent()))
        self.assertEqual(0, fake_self.resolver_calls)

    def test_main_live_state_lookup_skips_unresolved_private_before_state_read(self) -> None:
        method = _load_p4_live_state_for_event()

        class PrivateEvent:
            def is_private_chat(self) -> bool:
                return True

        class FakeSelf:
            resolver_calls = 0
            read_calls = 0

            def resolve_unified_person_for_event(self, event: object) -> dict[str, str]:
                self.resolver_calls += 1
                return {"state": "unresolved"}

            def read_p4_live_state(self, person_id: str) -> dict[str, object]:
                self.read_calls += 1
                raise AssertionError("unresolved private chat must not read P4 state")

        fake_self = FakeSelf()
        self.assertIsNone(method(fake_self, PrivateEvent()))
        self.assertEqual(1, fake_self.resolver_calls)
        self.assertEqual(0, fake_self.read_calls)

    def test_main_live_state_lookup_fails_closed_only_for_resolved_private_read_failure(self) -> None:
        method = _load_p4_live_state_for_event()

        class PrivateEvent:
            def is_private_chat(self) -> bool:
                return True

        class FakeSelf:
            read_calls = 0

            def resolve_unified_person_for_event(self, event: object) -> dict[str, str]:
                return {"state": "resolved", "person_id": "person-1"}

            def read_p4_live_state(self, person_id: str) -> dict[str, object]:
                self.read_calls += 1
                self.assertEqual("person-1", person_id)
                return {"ok": False, "code": "p4_live_state_corrupt"}

            def assertEqual(self, left: object, right: object) -> None:
                if left != right:
                    raise AssertionError(f"{left!r} != {right!r}")

        fake_self = FakeSelf()
        self.assertEqual({"_p4_live_invalid": True}, method(fake_self, PrivateEvent()))
        self.assertEqual(1, fake_self.read_calls)

    def test_live_state_registry_is_internal_and_operation_idempotent(self) -> None:
        store: dict = {}
        registry = UnifiedPersonRegistry(store)
        person_id = registry.create_or_link(IDENTITY, operation_id="create-live")["person_id"]
        active = live_state(confinement_state="active", confinement_until="2099-01-01T00:00:00Z")
        first = registry.record_p4_live_state(person_id, active, operation_id="live-1")
        replay = registry.record_p4_live_state(person_id, active, operation_id="live-1")
        conflict = registry.record_p4_live_state(person_id, live_state(), operation_id="live-1")
        self.assertTrue(first["ok"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual("operation_id_conflict", conflict["code"])
        self.assertEqual(active, registry.read_p4_live_state(person_id)["state"])

    def test_legacy_score_is_compatible_by_default_and_isolatable(self) -> None:
        for current, delta, expected in (
            (2, 3, 5),
            ("5", 1, 6),
            (" 5 ", 1, 6),
            (-9, 1, 1),
            (5.9, 1, 6),
            ("not-a-number", 1, 1),
            (float("inf"), 1, 1),
            (float("nan"), 1, 1),
            (self._HostileLegacyScore(), 1, 1),
        ):
            with self.subTest(current=current):
                user = {"relationship_score": current}
                self.assertTrue(apply_legacy_relationship_delta(user, delta, isolate=False))
                self.assertEqual(expected, user["relationship_score"])

        isolated = {"relationship_score": " 5 ", "unchanged": True}
        before_isolation = deepcopy(isolated)
        self.assertFalse(apply_legacy_relationship_delta(isolated, 9, isolate=True))
        self.assertEqual(before_isolation, isolated)

        strict_delta = {"relationship_score": "5"}
        self.assertFalse(apply_legacy_relationship_delta(strict_delta, True, isolate=False))
        self.assertEqual({"relationship_score": "5"}, strict_delta)

    def test_central_relationship_settlement_honors_explicit_p4_isolation(self) -> None:
        method = _load_p4_relationship_event_settlement()

        class Host:
            enable_custom_relationship_stage_policy = True
            relationship_positive_daily_cap = 12
            relationship_event_window_minutes = 30
            relationship_positive_event_cap = 4
            relationship_negative_event_cap = 12
            relationship_positive_stage_cap_key = "deeply_bonded"
            environment_perception_timezone = "Asia/Shanghai"

            def __init__(self, isolated: bool) -> None:
                self.enable_p4_b_legacy_score_isolation = isolated
                self.save_calls = 0

            def _schedule_data_save(self, *_args, **_kwargs) -> None:
                self.save_calls += 1

        isolated_host = Host(True)
        isolated_user = {"user_id": "person-1", "relationship_score": 55, "relationship_ledger": []}
        isolated_before = deepcopy(isolated_user)
        isolated = method(
            isolated_host,
            isolated_user,
            2,
            reason_code="test",
            event_id="event-1",
            now=1_700_000_000.0,
        )
        self.assertEqual("p4_legacy_score_isolated", isolated["code"])
        self.assertFalse(isolated["changed"])
        self.assertEqual(isolated_before, isolated_user)
        self.assertEqual(0, isolated_host.save_calls)

        compatible_host = Host(False)
        compatible_user = {"user_id": "person-2", "relationship_score": 0, "relationship_ledger": []}
        compatible = method(
            compatible_host,
            compatible_user,
            2,
            reason_code="test",
            event_id="event-2",
            now=1_700_000_000.0,
        )
        self.assertTrue(compatible["changed"])
        self.assertGreater(compatible_user["relationship_score"], 0)
        self.assertEqual(1, compatible_host.save_calls)

    def test_p4_and_p6_enum_checks_reject_hostile_values_before_hooks(self) -> None:
        for field in ("schema_version", "authority", "confinement_state", "warmth"):
            state = live_state(**{field: self._HostileValue()})
            self.assertEqual("invalid", validate_runtime_state(state))
        hostile = self._HostileValue()
        self.assertEqual("unverifiable", build_p6_readonly_status({}, health=hostile)["health"])
        self.assertEqual("invalid_reason_code", build_p6_readonly_status({}, reason_code=hostile)["reason_code"])

    def test_p4_runtime_rejects_hash_collision_hostile_keys_before_hooks(self) -> None:
        hostile = self._HashCollisionKey()
        self.assertEqual("invalid", validate_runtime_state({hostile: "not-reached"}))

    def test_p6_projection_rejects_hash_collision_hostile_keys_before_hooks(self) -> None:
        hostile = self._HashCollisionKey("profiles")
        status = {hostile: 1}
        hash_calls_before_projection = hostile.hash_calls

        projection = build_p6_readonly_status(status)

        self.assertEqual("unverifiable", projection["health"])
        self.assertEqual("registry_status_unavailable", projection["reason_code"])
        self.assertEqual(
            {"profiles": 0, "identity_links": 0, "audit_events": 0, "operations": 0},
            projection["counts"],
        )
        self.assertEqual(hash_calls_before_projection, hostile.hash_calls)
        self.assertEqual(0, hostile.eq_calls)
        self.assertEqual(0, hostile.str_calls)

    def test_p6_manifest_and_projection_are_exact_and_bounded(self) -> None:
        manifests = {
            package_id: {
                "schema": FOUR_PACKAGE_MANIFEST_SCHEMA,
                "package_id": package_id,
                "manifest_version": "1.0",
                "package_fingerprint": "a" * 64,
                "compatibility_fingerprint": "b" * 64,
            }
            for package_id in FOUR_PACKAGE_IDS
        }
        self.assertEqual("verified", verify_four_package_manifests(manifests)["status"])
        manifests["unexpected"] = {}
        self.assertEqual("unverifiable", verify_four_package_manifests(manifests)["status"])
        projection = build_p6_readonly_status({"profiles": 2, "identity_links": -1, "audit_events": "bad", "operations": 3})
        self.assertEqual({"profiles": 2, "identity_links": 0, "audit_events": 0, "operations": 3}, projection["counts"])
        self.assertEqual({"schema_version", "source_plugin", "contract_fingerprint", "health", "reason_code", "counts"}, set(projection))

    def test_relationship_panel_source_cannot_return_sensitive_contract_fields(self) -> None:
        source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_relationship_panel")
        string_constants = {node.value for node in ast.walk(function) if isinstance(node, ast.Constant) and type(node.value) is str}
        forbidden = {"person_id", "p4_effect", "p4_live", "attestation", "raw_prompt", "memory_content", "group_overlay"}
        self.assertTrue(forbidden.isdisjoint(string_constants))
        self.assertIn("relationship_basis", string_constants)
        self.assertIn("memory_phase", string_constants)
        self.assertIn("expression_decision", string_constants)

    def test_extension_api_does_not_expose_p4_effect_or_live_authority(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        extension_api = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionExtensionAPI"
        )
        public_methods = {
            node.name for node in extension_api.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        forbidden = {"read_p4_effect_state", "read_p4_live_state", "record_p4_effect_event"}
        self.assertTrue(forbidden.isdisjoint(public_methods))
        self.assertIn("get_p6_readonly_status", public_methods)

    def test_bridge_peek_degrades_for_missing_unsupported_invalid_and_exceptional_bridges(self) -> None:
        # The plugin host is unavailable in the bundled test runtime.  Stub
        # only its logger namespace; no bridge is loaded or called externally.
        astrbot = types.ModuleType("astrbot")
        api = types.ModuleType("astrbot.api")
        api.logger = types.SimpleNamespace(debug=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        sys.modules.setdefault("astrbot", astrbot)
        sys.modules.setdefault("astrbot.api", api)
        package = types.ModuleType("chat_companion_test_package")
        package.__path__ = [str(ROOT)]
        sys.modules.setdefault("chat_companion_test_package", package)
        mixin = importlib.import_module("chat_companion_test_package.memory_companion_adapter").MemoryCompanionAdapterMixin

        class Host(mixin):
            def __init__(self, bridge: object) -> None:
                self.bridge = bridge

            def _memory_companion_bridge(self):
                return self.bridge

        self.assertEqual("unavailable", Host(None)._memory_companion_peek_relationship_phase()["status"])
        self.assertEqual("unsupported", Host(object())._memory_companion_peek_relationship_phase()["status"])
        self.assertEqual("invalid", Host(types.SimpleNamespace(peek_relationship_phase=lambda **kwargs: "bad"))._memory_companion_peek_relationship_phase()["status"])
        def fail(**kwargs):
            raise RuntimeError("bridge failure")
        self.assertEqual("unavailable", Host(types.SimpleNamespace(peek_relationship_phase=fail))._memory_companion_peek_relationship_phase()["status"])


if __name__ == "__main__":
    unittest.main()
