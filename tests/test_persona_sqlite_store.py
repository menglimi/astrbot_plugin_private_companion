# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.persona_sqlite_store import (
    PersonaSqliteStoreError,
    PersonaSqliteStoreRegistry,
    load_persona_sqlite_store,
)


def _new_store() -> dict:
    return {
        "users": {},
        "groups": {},
        "persona_settings": {},
    }


def _ensure_defaults(data: dict) -> dict:
    result = dict(data)
    for key, value in _new_store().items():
        result.setdefault(key, value)
    return result


class PersonaSqliteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.legacy = self.root / "alt.json"
        self.sqlite = self.root / "alt.sqlite3"
        self.registry = PersonaSqliteStoreRegistry()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _load(self):
        return load_persona_sqlite_store(
            persona_id="alt",
            legacy_json_path=self.legacy,
            sqlite_path=self.sqlite,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
            registry=self.registry,
        )

    def _write_legacy(self, payload: object) -> None:
        self.legacy.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_valid_legacy_json_is_normalized_verified_and_removed(self) -> None:
        self._write_legacy({"users": {"用户": {"name": "璃"}}})

        handle = self._load()

        self.assertEqual("legacy_json", handle.source)
        self.assertEqual("璃", handle.data["users"]["用户"]["name"])
        self.assertEqual({}, handle.data["groups"])
        self.assertFalse(self.legacy.exists())
        self.assertTrue(self.sqlite.exists())
        self.assertEqual(handle.data, handle.manager.backend.load_store())
        self.assertEqual("sqlite", handle.manager.backend.backend_name())

    def test_existing_database_is_replaced_when_legacy_json_still_exists(self) -> None:
        first = self._load()
        stale = _ensure_defaults({"users": {"old": {"name": "旧数据"}}})
        first.manager.backend.save_store(stale)
        self._write_legacy({"users": {"new": {"name": "新数据"}}})

        migrated = self._load()

        self.assertNotIn("old", migrated.data["users"])
        self.assertEqual("新数据", migrated.data["users"]["new"]["name"])
        self.assertFalse(self.legacy.exists())

    def test_invalid_json_does_not_replace_existing_database(self) -> None:
        handle = self._load()
        existing = _ensure_defaults({"users": {"keep": {"name": "保留"}}})
        handle.manager.backend.save_store(existing)
        self.legacy.write_text("{invalid", encoding="utf-8")

        with self.assertRaisesRegex(PersonaSqliteStoreError, "parse_legacy_json"):
            self._load()

        self.assertTrue(self.legacy.exists())
        self.assertEqual(existing, handle.manager.backend.load_store())

    def test_non_object_duplicate_key_and_nan_sources_fail_strictly(self) -> None:
        samples = (
            '["not-an-object"]',
            '{"users": {}, "users": {"lost": true}}',
            '{"score": NaN}',
        )
        for source in samples:
            with self.subTest(source=source):
                self.legacy.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(PersonaSqliteStoreError, "parse_legacy_json"):
                    self._load()
                self.assertTrue(self.legacy.exists())
                self.assertFalse(self.sqlite.exists())

    def test_sqlite_write_failure_retains_legacy_and_existing_content(self) -> None:
        handle = self._load()
        existing = _ensure_defaults({"users": {"keep": True}})
        handle.manager.backend.save_store(existing)
        self._write_legacy({"users": {"replace": True}})

        with (
            patch.object(handle.manager.backend, "save_store", side_effect=RuntimeError("write failed")),
            self.assertRaisesRegex(PersonaSqliteStoreError, "write_sqlite"),
        ):
            self._load()

        self.assertTrue(self.legacy.exists())
        self.assertEqual(existing, handle.manager.backend.load_store())

    def test_normalization_failure_happens_before_existing_database_is_written(self) -> None:
        handle = self._load()
        existing = _ensure_defaults({"users": {"keep": True}})
        handle.manager.backend.save_store(existing)
        self._write_legacy({"users": {"replace": True}})

        def fail_normalization(_payload: dict) -> dict:
            raise RuntimeError("injected normalization failure")

        separate_registry = PersonaSqliteStoreRegistry()
        with self.assertRaisesRegex(PersonaSqliteStoreError, "parse_legacy_json"):
            load_persona_sqlite_store(
                persona_id="alt",
                legacy_json_path=self.legacy,
                sqlite_path=self.sqlite,
                ensure_defaults=fail_normalization,
                new_store=_new_store,
                registry=separate_registry,
            )

        self.assertTrue(self.legacy.exists())
        self.assertEqual(existing, handle.manager.backend.load_store())

    def test_readback_mismatch_retains_legacy_for_retry(self) -> None:
        self._write_legacy({"users": {"expected": True}})
        manager = self.registry.manager_for(
            legacy_json_path=self.legacy,
            sqlite_path=self.sqlite,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )
        real_load = manager.backend.load_store
        calls = 0

        def mismatched_load():
            nonlocal calls
            calls += 1
            if calls == 1:
                return _ensure_defaults({"users": {"unexpected": True}})
            return real_load()

        with (
            patch.object(manager.backend, "load_store", side_effect=mismatched_load),
            self.assertRaisesRegex(PersonaSqliteStoreError, "verify_sqlite"),
        ):
            self._load()

        self.assertTrue(self.legacy.exists())
        self.assertEqual(True, real_load()["users"]["expected"])

    def test_health_and_quick_check_failures_retain_legacy(self) -> None:
        for method in ("health", "quick"):
            with self.subTest(method=method):
                self._write_legacy({"users": {method: True}})
                manager = self.registry.manager_for(
                    legacy_json_path=self.legacy,
                    sqlite_path=self.sqlite,
                    ensure_defaults=_ensure_defaults,
                    new_store=_new_store,
                )
                if method == "health":
                    target = patch.object(
                        manager,
                        "health_check",
                        return_value={"ok": False, "error": "injected"},
                    )
                else:
                    target = patch(
                        "astrbot_plugin_private_companion.persona_sqlite_store._sqlite_quick_check",
                        side_effect=RuntimeError("injected quick failure"),
                    )
                with target, self.assertRaisesRegex(PersonaSqliteStoreError, "verify_sqlite"):
                    self._load()
                self.assertTrue(self.legacy.exists())

    def test_delete_failure_is_fail_closed_and_next_load_retries(self) -> None:
        self._write_legacy({"users": {"retry": {"name": "再次迁移"}}})
        original_unlink = Path.unlink
        failed = False

        def fail_once(path: Path, *args, **kwargs):
            nonlocal failed
            if path == self.legacy and not failed:
                failed = True
                raise PermissionError("injected delete failure")
            return original_unlink(path, *args, **kwargs)

        with (
            patch.object(Path, "unlink", autospec=True, side_effect=fail_once),
            self.assertRaisesRegex(PersonaSqliteStoreError, "remove_legacy_json"),
        ):
            self._load()

        self.assertTrue(self.legacy.exists())
        retried = self._load()
        self.assertEqual("再次迁移", retried.data["users"]["retry"]["name"])
        self.assertFalse(self.legacy.exists())

    def test_prepare_failure_keeps_legacy_json_for_retry(self) -> None:
        self._write_legacy({"users": {"schema": True}})

        def fail_prepare(_payload: dict) -> dict:
            raise ValueError("unsupported persona schema")

        with self.assertRaisesRegex(PersonaSqliteStoreError, "parse_legacy_json"):
            load_persona_sqlite_store(
                persona_id="alt",
                legacy_json_path=self.legacy,
                sqlite_path=self.sqlite,
                ensure_defaults=_ensure_defaults,
                new_store=_new_store,
                registry=self.registry,
                prepare_payload=fail_prepare,
            )

        self.assertTrue(self.legacy.exists())
        self.assertFalse(self.sqlite.exists())

        retried = self._load()
        self.assertTrue(retried.data["users"]["schema"])
        self.assertFalse(self.legacy.exists())

    def test_no_json_loads_existing_database_or_initializes_new_database(self) -> None:
        initialized = self._load()
        self.assertEqual("initialized", initialized.source)
        initialized.data["users"]["saved"] = True
        initialized.manager.backend.save_store(initialized.data)

        loaded = self._load()

        self.assertEqual("sqlite", loaded.source)
        self.assertTrue(loaded.data["users"]["saved"])

    def test_caller_resolved_unicode_and_dangerous_names_are_not_rederived(self) -> None:
        legacy = self.root / "璃%2F..%2FCON?.json"
        sqlite = self.root / "璃%2F..%2FCON?.sqlite3"
        legacy.write_text('{"users":{"用户":{"name":"璃"}}}', encoding="utf-8")

        handle = load_persona_sqlite_store(
            persona_id="璃/../CON?",
            legacy_json_path=legacy,
            sqlite_path=sqlite,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )

        self.assertEqual(sqlite, handle.manager.sqlite_path)
        self.assertTrue(sqlite.exists())
        self.assertFalse(legacy.exists())

    def test_registry_reuses_manager_and_rejects_conflicting_registration(self) -> None:
        first = self.registry.manager_for(
            legacy_json_path=self.legacy,
            sqlite_path=self.sqlite,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )
        second = self.registry.manager_for(
            legacy_json_path=self.legacy,
            sqlite_path=self.sqlite,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )
        self.assertIs(first, second)

        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.registry.manager_for(
                legacy_json_path=self.root / "different.json",
                sqlite_path=self.sqlite,
                ensure_defaults=_ensure_defaults,
                new_store=_new_store,
            )


if __name__ == "__main__":
    unittest.main()
