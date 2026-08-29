"""Pure cross-plugin P4/P6 regression without an OPS runtime dependency."""
from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from copy import deepcopy
from pathlib import Path


COMPANION_ROOT = Path(__file__).resolve().parents[1]
_MEMORY_ROOT_CANDIDATES = (
    Path(os.environ["ASTRBOT_MEMORY_PLUGIN_ROOT"])
    if os.environ.get("ASTRBOT_MEMORY_PLUGIN_ROOT")
    else COMPANION_ROOT / ".missing-memory-root",
    COMPANION_ROOT.parent / "memory",
    COMPANION_ROOT.parents[1] / "astrbot_plugin_memory_companion-main",
)
MEMORY_ROOT = next(
    (
        path
        for path in _MEMORY_ROOT_CANDIDATES
        if (path / "core" / "bridge.py").is_file()
    ),
    _MEMORY_ROOT_CANDIDATES[0],
)


def _register_package_alias(name: str, root: Path) -> None:
    package = sys.modules.get(name)
    if package is None:
        package = types.ModuleType(name)
        package.__path__ = [str(root)]
        sys.modules[name] = package


_register_package_alias("chat_companion_p4_p6", COMPANION_ROOT)
_register_package_alias("chat_memory_p4_p6", MEMORY_ROOT)

companion_manifest = importlib.import_module("chat_companion_p4_p6.p6_four_package_manifest")
companion_projection = importlib.import_module("chat_companion_p4_p6.p6_readonly_projection")
reply_temperature = importlib.import_module("chat_companion_p4_p6.reply_temperature")
memory_manifest = importlib.import_module("chat_memory_p4_p6.core.p6_four_package_manifest")
memory_projection = importlib.import_module("chat_memory_p4_p6.core.p6_readonly_projection")
memory_coordination = importlib.import_module("chat_memory_p4_p6.core.coordination_status")
memory_bridge = importlib.import_module("chat_memory_p4_p6.core.bridge")


def _manifest_set() -> dict[str, dict[str, str]]:
    return {
        package_id: {
            "schema": companion_manifest.FOUR_PACKAGE_MANIFEST_SCHEMA,
            "package_id": package_id,
            "manifest_version": "1.0",
            "package_fingerprint": "a" * 64,
            "compatibility_fingerprint": "b" * 64,
        }
        for package_id in companion_manifest.FOUR_PACKAGE_IDS
    }


class _HashCollisionHostileValue:
    def __init__(self) -> None:
        self.hash_calls = 0
        self.eq_calls = 0
        self.str_calls = 0

    def __hash__(self) -> int:
        self.hash_calls += 1
        return hash("ops.p6.four_package_manifest.v1")

    def __eq__(self, _other: object) -> bool:
        self.eq_calls += 1
        raise AssertionError("hostile manifest value must not be compared")

    def __str__(self) -> str:
        self.str_calls += 1
        raise AssertionError("hostile manifest value must not be stringified")


class _HashCollisionHostileKey:
    def __init__(self, field: str) -> None:
        self._field = field
        self.hash_calls = 0
        self.eq_calls = 0
        self.str_calls = 0

    def __hash__(self) -> int:
        self.hash_calls += 1
        return hash(self._field)

    def __eq__(self, _other: object) -> bool:
        self.eq_calls += 1
        raise AssertionError("hostile key must not be compared")

    def __str__(self) -> str:
        self.str_calls += 1
        raise AssertionError("hostile key must not be stringified")


class ChatMemoryP4P6IntegrationTests(unittest.TestCase):
    def test_manifest_contract_is_identical_and_fails_closed(self) -> None:
        self.assertEqual(companion_manifest.manifest_contract(), memory_manifest.manifest_contract())
        manifests = _manifest_set()
        self.assertEqual("verified", companion_manifest.verify_four_package_manifests(manifests)["status"])
        self.assertEqual("verified", memory_manifest.verify_four_package_manifests(manifests)["status"])

        extended = deepcopy(manifests)
        extended["memory"]["unexpected"] = "not-allowed"
        missing = deepcopy(manifests)
        del missing["peiban"]
        conflicting = deepcopy(manifests)
        conflicting["ops_archive"]["compatibility_fingerprint"] = "c" * 64
        for invalid in (extended, missing, conflicting):
            self.assertEqual("unverifiable", companion_manifest.verify_four_package_manifests(invalid)["status"])
            self.assertEqual("unverifiable", memory_manifest.verify_four_package_manifests(invalid)["status"])

    def test_manifest_hostile_schema_and_package_id_values_fail_closed(self) -> None:
        for field_name in ("schema", "package_id"):
            manifests = _manifest_set()
            manifests["memory"][field_name] = _HashCollisionHostileValue()
            self.assertEqual(
                "unverifiable",
                companion_manifest.verify_four_package_manifests(manifests)["status"],
            )
            self.assertEqual(
                "unverifiable",
                memory_manifest.verify_four_package_manifests(manifests)["status"],
            )

    def test_companion_p6_dto_is_consumable_and_extensions_fail_closed(self) -> None:
        self.assertEqual(
            companion_projection.P6_READONLY_STATUS_SCHEMA,
            memory_projection.P6_READONLY_STATUS_SCHEMA,
        )
        self.assertEqual(
            companion_projection.P6_READONLY_STATUS_FINGERPRINT,
            memory_projection.P6_READONLY_STATUS_FINGERPRINT,
        )
        dto = companion_projection.build_p6_readonly_status(
            {"profiles": 3, "identity_links": 2, "audit_events": 1, "operations": 4},
        )
        consumed = memory_coordination.project_p6_status(dto)
        self.assertEqual("ready", consumed["health"])
        self.assertEqual({"profiles": 3, "identity_links": 2, "audit_events": 1, "operations": 4}, consumed["counts"])

        malformed = dict(dto)
        malformed["extra"] = "not-allowed"
        malformed_projection = memory_coordination.project_p6_status(malformed)
        self.assertEqual("unverifiable", malformed_projection["health"])
        self.assertNotIn("extra", malformed_projection)

    def test_peek_relationship_phase_filters_failures_and_untrusted_fields(self) -> None:
        class BrokenPlugin:
            def _peek_relationship_phase(self, _context):
                raise RuntimeError("bridge failure")

        class ExtendedPlugin:
            def _peek_relationship_phase(self, _context):
                return {
                    "observed": True,
                    "phase": "close",
                    "momentum_band": "rising",
                    "touch_count": 7,
                    "person_id": "not-allowed",
                    "momentum": 0.9,
                }

        fallback = {"observed": False, "phase": "unknown", "momentum_band": "unknown"}
        self.assertEqual(fallback, memory_bridge.MemoryCompanionBridge(BrokenPlugin()).peek_relationship_phase(session_id="s"))
        projection = memory_bridge.MemoryCompanionBridge(ExtendedPlugin()).peek_relationship_phase(session_id="s")
        self.assertEqual({"observed": True, "phase": "close", "momentum_band": "rising", "touch_count": 7}, projection)
        self.assertEqual({"observed", "phase", "momentum_band", "touch_count"}, set(projection))

        hostile_key = _HashCollisionHostileKey("observed")
        hostile_value = _HashCollisionHostileValue()
        hostile_result = {hostile_key: hostile_value}
        hash_calls_before_peek = hostile_key.hash_calls
        projection = memory_bridge.MemoryCompanionBridge(
            types.SimpleNamespace(_peek_relationship_phase=lambda _context: hostile_result)
        ).peek_relationship_phase(session_id="s")
        self.assertEqual(fallback, projection)
        self.assertEqual(hash_calls_before_peek, hostile_key.hash_calls)
        self.assertEqual(0, hostile_key.eq_calls)
        self.assertEqual(0, hostile_key.str_calls)
        self.assertEqual(0, hostile_value.hash_calls)
        self.assertEqual(0, hostile_value.eq_calls)
        self.assertEqual(0, hostile_value.str_calls)
        self.assertNotIn("hostile", repr(projection))

    def test_companion_adapter_relationship_phase_uses_fixed_allowlists(self) -> None:
        astrbot = types.ModuleType("astrbot")
        api = types.ModuleType("astrbot.api")
        api.logger = types.SimpleNamespace(debug=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        sys.modules.setdefault("astrbot", astrbot)
        sys.modules.setdefault("astrbot.api", api)
        package = types.ModuleType("chat_companion_memory_adapter_test")
        package.__path__ = [str(COMPANION_ROOT)]
        sys.modules.setdefault("chat_companion_memory_adapter_test", package)
        adapter = importlib.import_module(
            "chat_companion_memory_adapter_test.memory_companion_adapter"
        ).MemoryCompanionAdapterMixin

        class Host(adapter):
            def __init__(self, result: object) -> None:
                self.result = result

            def _memory_companion_bridge(self):
                return types.SimpleNamespace(
                    peek_relationship_phase=lambda **kwargs: self.result,
                )

        valid = Host({
            "observed": True,
            "phase": "deeply_bonded",
            "momentum_band": "steady",
            "touch_count": 300,
            "person_id": "sensitive-person",
            "momentum": 0.9,
        })._memory_companion_peek_relationship_phase()
        self.assertEqual(
            {"observed": True, "phase": "deeply_bonded", "momentum_band": "steady", "touch_count": 256, "status": "observed"},
            valid,
        )

        invalid_results = (
            {"observed": True, "phase": "unknown", "momentum_band": "steady", "person_id": "secret"},
            {"observed": True, "phase": "close", "momentum_band": "accelerating", "momentum": "secret"},
            {"observed": True, "phase": "close\nsecret", "momentum_band": "rising", "person_id": "secret"},
            {"observed": "true", "phase": "close", "momentum_band": "rising", "person_id": "secret"},
        )
        for result in invalid_results:
            projection = Host(result)._memory_companion_peek_relationship_phase()
            self.assertEqual(
                {"observed": False, "phase": "unknown", "momentum_band": "unknown", "touch_count": 0, "status": "not_observed"},
                projection,
            )
            self.assertNotIn("secret", projection.values())

        class HostileMapping(dict):
            def get(self, *args, **kwargs):
                raise AssertionError("untrusted bridge mapping must not be read")

        self.assertEqual(
            {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "invalid"},
            Host(HostileMapping())._memory_companion_peek_relationship_phase(),
        )

        hostile_key = _HashCollisionHostileKey("observed")
        hostile_value = _HashCollisionHostileValue()
        hostile_result = {hostile_key: hostile_value}
        hash_calls_before_peek = hostile_key.hash_calls
        projection = Host(hostile_result)._memory_companion_peek_relationship_phase()
        self.assertEqual(
            {"observed": False, "phase": "unknown", "momentum_band": "unknown", "status": "invalid"},
            projection,
        )
        self.assertEqual(hash_calls_before_peek, hostile_key.hash_calls)
        self.assertEqual(0, hostile_key.eq_calls)
        self.assertEqual(0, hostile_key.str_calls)
        self.assertEqual(0, hostile_value.hash_calls)
        self.assertEqual(0, hostile_value.eq_calls)
        self.assertEqual(0, hostile_value.str_calls)
        self.assertNotIn("hostile", repr(projection))

        for phase in ("acquaintance", "familiar", "close", "intimate", "deeply_bonded"):
            for momentum_band in ("rising", "cooling", "steady"):
                projection = Host({
                    "observed": True,
                    "phase": phase,
                    "momentum_band": momentum_band,
                    "touch_count": 1,
                })._memory_companion_peek_relationship_phase()
                self.assertEqual(phase, projection["phase"])
                self.assertEqual(momentum_band, projection["momentum_band"])
                self.assertTrue(projection["observed"])

    def test_reply_temperature_obeys_p4_cap_and_context_can_only_lower(self) -> None:
        baseline = reply_temperature.compose_reply_temperature(
            "neutral", energy=100, mood="happy", schedule="free", context="thanks"
        )
        bounded = reply_temperature.compose_reply_temperature(
            "neutral", energy=100, mood="happy", schedule="busy meeting", context="please stop"
        )
        self.assertEqual("neutral", baseline["tier"])
        self.assertEqual("neutral", baseline["cap_tier"])
        self.assertLessEqual(baseline["score"], 0.45)
        self.assertLessEqual(bounded["score"], baseline["score"])
        self.assertEqual("neutral", bounded["cap_tier"])
        self.assertNotIn("please stop", repr(bounded))


if __name__ == "__main__":
    unittest.main()
