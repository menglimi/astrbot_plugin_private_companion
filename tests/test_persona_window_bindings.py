from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from astrbot_plugin_private_companion.persona_window_bindings import (
    BindingRevisionConflict,
    BindingRuntimeSnapshot,
    BindingStoreError,
    BindingStoreValidationError,
    PersonaWindowBindingStore,
    STORE_VERSION,
    normalize_window,
)


class PersonaWindowBindingStoreTests(unittest.TestCase):
    def test_missing_store_and_v2_save_are_versioned_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "persona_window_bindings.json"
            store = PersonaWindowBindingStore(path, clock=lambda: 123.5)

            missing = store.load()
            self.assertEqual(STORE_VERSION, missing.version)
            self.assertEqual(0, missing.revision)
            self.assertEqual({}, missing.bindings)
            self.assertEqual("missing", missing.source_format)

            saved = store.save({"QBot4012710235:GroupMessage:group-1": "alt"})
            self.assertEqual(1, saved.revision)
            self.assertEqual(123.5, saved.updated_at)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "version",
                    "revision",
                    "updated_at",
                    "bindings",
                },
                set(payload),
            )
            self.assertEqual("alt", payload["bindings"]["QBot4012710235:GroupMessage:group-1"])

    def test_plain_mapping_and_v1_envelope_migrate_without_losing_revision(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "persona_window_bindings.json"
            window = "QBot4012710235:GroupMessage:group-1"

            path.write_text(json.dumps({window: "alt"}), encoding="utf-8")
            store = PersonaWindowBindingStore(path, clock=lambda: 100.0)
            plain = store.load()
            self.assertEqual("plain", plain.source_format)
            self.assertTrue(plain.needs_migration)
            self.assertEqual({window: "alt"}, plain.bindings)
            migrated = store.load(migrate=True)
            self.assertFalse(migrated.needs_migration)
            self.assertEqual(0, migrated.revision)
            self.assertEqual("v2", migrated.source_format)
            self.assertEqual(STORE_VERSION, json.loads(path.read_text())["version"])

            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "revision": 7,
                        "updated_at": 88,
                        "bindings": {window: "main"},
                    }
                ),
                encoding="utf-8",
            )
            v1 = store.load(migrate=True)
            self.assertEqual(7, v1.revision)
            self.assertEqual({window: "main"}, v1.bindings)
            self.assertEqual(STORE_VERSION, json.loads(path.read_text())["version"])

    def test_config_and_persisted_mappings_are_distinct_and_persisted_wins(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = PersonaWindowBindingStore(Path(root) / "bindings.json")
            store.save({"window": "persisted"})
            snapshot = store.capture_runtime(
                {"window": "config", "config-only": "main"},
                claims={"window": "persisted"},
            )
            self.assertEqual("config", snapshot.config_bindings["window"])
            self.assertEqual("persisted", snapshot.persisted_bindings["window"])
            self.assertEqual("persisted", snapshot.effective_bindings["window"])
            self.assertEqual("main", snapshot.effective_bindings["config-only"])

    def test_umo_is_opaque_single_line_and_colons_are_preserved(self) -> None:
        umo = "QBot4012710235:GroupMessage:group-openid-with:colon"
        self.assertEqual(umo, normalize_window(umo))
        self.assertEqual("", normalize_window("  "))
        self.assertEqual("", normalize_window("a\nb"))
        self.assertEqual("", normalize_window("a\rb"))
        self.assertEqual("", normalize_window("x" * 241))

        with tempfile.TemporaryDirectory() as root:
            store = PersonaWindowBindingStore(Path(root) / "bindings.json")
            state = store.save({umo: "alt"})
            self.assertEqual({umo: "alt"}, state.bindings)

    def test_upsert_plan_updates_both_sources_and_clears_runtime_conflict_state(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = PersonaWindowBindingStore(Path(root) / "bindings.json", clock=lambda: 99)
            store.save({"window": "old"})
            snapshot = store.capture_runtime(
                {"window": "old"},
                claims={"window": "old"},
                conflicts={"window": {"current": "old"}},
                passive_cache={"window": {"stale": True}},
            )
            plan = store.plan_upsert(snapshot, "window", "new", expected_revision=1)
            self.assertTrue(plan.changed)
            self.assertEqual("old", plan.before.config_bindings["window"])
            self.assertEqual("new", plan.after.config_bindings["window"])
            self.assertEqual("new", plan.after.persisted_bindings["window"])
            self.assertEqual("new", plan.after.claims["window"])
            self.assertNotIn("window", plan.after.conflicts)
            self.assertNotIn("window", plan.after.passive_cache)
            self.assertEqual(2, plan.after.revision)
            self.assertEqual(1, plan.rollback_persisted_state.revision)
            self.assertEqual({"window": "old"}, plan.rollback_persisted_state.bindings)
            persisted = store.persist_plan(plan)
            self.assertEqual(2, persisted.revision)
            self.assertEqual({"window": "new"}, store.load().bindings)

    def test_delete_plan_removes_binding_and_metadata_without_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = PersonaWindowBindingStore(Path(root) / "bindings.json")
            store.save({"window": "alt", "other": "main"})
            snapshot = store.capture_runtime(
                {"window": "alt", "other": "main"},
                claims={"window": "alt", "other": "main"},
                conflicts={"window": {"reason": "conflict"}},
                passive_cache={"window": {"cached": True}},
            )
            plan = store.plan_delete(snapshot, "window", expected_revision=1)
            self.assertTrue(plan.changed)
            self.assertNotIn("window", plan.after.config_bindings)
            self.assertNotIn("window", plan.after.persisted_bindings)
            self.assertNotIn("window", plan.after.claims)
            self.assertNotIn("window", plan.after.conflicts)
            self.assertNotIn("window", plan.after.passive_cache)
            self.assertNotIn("window", plan.after.effective_bindings)
            self.assertNotIn("window", plan.persisted_state.bindings)
            self.assertNotIn("excluded_windows", plan.persisted_state.to_payload())

    def test_revision_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = PersonaWindowBindingStore(Path(root) / "bindings.json")
            store.save({"window": "main"})
            with self.assertRaises(BindingRevisionConflict):
                store.save({"window": "alt"}, expected_revision=0)
            snapshot = store.capture_runtime({"window": "main"})
            with self.assertRaises(BindingRevisionConflict):
                store.plan_delete(snapshot, "window", expected_revision=0)

    def test_invalid_json_is_not_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bindings.json"
            path.write_text("not json", encoding="utf-8")
            store = PersonaWindowBindingStore(path)
            with self.assertRaises(BindingStoreError):
                store.load()
            self.assertEqual("not json", path.read_text(encoding="utf-8"))

    def test_snapshot_is_independent_and_validation_rejects_invalid_mutation(self) -> None:
        snapshot = BindingRuntimeSnapshot.capture(
            {"window": "main"},
            {"window": "alt"},
            claims={"window": "alt"},
        )
        clone = snapshot.clone()
        clone.config_bindings["window"] = "changed"
        clone.claims["window"] = "changed"
        self.assertEqual("main", snapshot.config_bindings["window"])
        self.assertEqual("alt", snapshot.claims["window"])

        with tempfile.TemporaryDirectory() as root:
            store = PersonaWindowBindingStore(Path(root) / "bindings.json")
            with self.assertRaises(BindingStoreValidationError):
                store.plan_upsert(snapshot, "bad\nwindow", "main")
            with self.assertRaises(BindingStoreValidationError):
                store.plan_delete(snapshot, "", expected_revision=0)


if __name__ == "__main__":
    unittest.main()
