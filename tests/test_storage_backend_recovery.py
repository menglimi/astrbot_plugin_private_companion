# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.storage.json_backend import JsonStoreBackend
from astrbot_plugin_private_companion.storage.migration import migrate_json_to_backend_if_needed
from astrbot_plugin_private_companion.storage.sqlite_backend import (
    SqliteStoreBackend,
    SqliteStoreNotInitializedError,
)


def _new_store() -> dict:
    return {"users": {}, "settings": {"source": "default"}}


def _ensure_defaults(data: dict) -> dict:
    result = deepcopy(data)
    result.setdefault("users", {})
    result.setdefault("settings", {"source": "default"})
    return result


class StorageBackendRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.json_path = self.root / "companions.json"
        self.sqlite_path = self.root / "companions.db"
        self.json_backend = JsonStoreBackend(
            self.json_path,
            _ensure_defaults,
            _new_store,
        )
        self.sqlite_backend = SqliteStoreBackend(
            self.sqlite_path,
            _ensure_defaults,
            _new_store,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_json_store_still_returns_defaults(self) -> None:
        self.assertEqual(self.json_backend.load_store(), _new_store())

    def test_existing_invalid_json_is_not_treated_as_empty_store(self) -> None:
        original = b'{"users": '
        self.json_path.write_bytes(original)

        with self.assertRaises(json.JSONDecodeError):
            self.json_backend.load_store()

        self.assertEqual(self.json_path.read_bytes(), original)

    def test_existing_non_object_json_is_rejected(self) -> None:
        self.json_path.write_text("[]", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "root must be an object"):
            self.json_backend.load_store()

    def test_missing_sqlite_store_still_returns_defaults(self) -> None:
        self.assertEqual(self.sqlite_backend.load_store(), _new_store())
        self.assertFalse(self.sqlite_path.exists())

    def test_existing_locked_sqlite_error_is_not_treated_as_empty_store(self) -> None:
        self.sqlite_path.touch()

        with (
            patch.object(
                self.sqlite_backend,
                "_connect",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"),
        ):
            self.sqlite_backend.load_store()

    def test_empty_sqlite_store_has_distinct_uninitialized_error(self) -> None:
        sqlite3.connect(self.sqlite_path).close()

        with self.assertRaises(SqliteStoreNotInitializedError):
            self.sqlite_backend.load_store()

    def test_invalid_sqlite_section_aborts_the_whole_load(self) -> None:
        payload = {"users": {"42": {"name": "saved"}}, "settings": {"source": "sqlite"}}
        self.sqlite_backend.save_store(payload)
        conn = sqlite3.connect(self.sqlite_path)
        try:
            conn.execute(
                "UPDATE store_sections SET payload_json = ? WHERE section_name = ?",
                ("{invalid", "users"),
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(ValueError, "section payload is invalid: users"):
            self.sqlite_backend.load_store()

        conn = sqlite3.connect(self.sqlite_path)
        try:
            stored = conn.execute(
                "SELECT payload_json FROM store_sections WHERE section_name = ?",
                ("users",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(stored, ("{invalid",))

    def test_interrupted_empty_sqlite_migration_recovers_from_json(self) -> None:
        payload = {"users": {"42": {"name": "from-json"}}, "settings": {"source": "json"}}
        self.json_backend.save_store(payload)
        sqlite3.connect(self.sqlite_path).close()

        loaded = migrate_json_to_backend_if_needed(
            self.sqlite_backend,
            self.json_backend,
            _new_store(),
        )

        self.assertEqual(loaded, payload)
        self.assertEqual(self.sqlite_backend.load_store(), payload)

    def test_empty_sqlite_does_not_hide_corrupt_json_recovery_source(self) -> None:
        original = b'{"users": '
        self.json_path.write_bytes(original)
        sqlite3.connect(self.sqlite_path).close()

        with (
            patch.object(self.sqlite_backend, "initialize_empty_store") as initialize,
            self.assertRaises(json.JSONDecodeError),
        ):
            migrate_json_to_backend_if_needed(
                self.sqlite_backend,
                self.json_backend,
                _new_store(),
            )

        initialize.assert_not_called()
        self.assertEqual(self.json_path.read_bytes(), original)
        with self.assertRaises(SqliteStoreNotInitializedError):
            self.sqlite_backend.load_store()

    def test_empty_sqlite_recovery_write_failure_keeps_json_payload_live(self) -> None:
        payload = {"users": {"42": {"name": "from-json"}}, "settings": {"source": "json"}}
        self.json_backend.save_store(payload)
        sqlite3.connect(self.sqlite_path).close()

        with patch.object(
            self.sqlite_backend,
            "initialize_empty_store",
            side_effect=OSError("disk is read-only"),
        ):
            loaded = migrate_json_to_backend_if_needed(
                self.sqlite_backend,
                self.json_backend,
                _new_store(),
            )

        self.assertEqual(loaded, payload)
        self.assertEqual(self.json_backend.load_store(), payload)
        with self.assertRaises(SqliteStoreNotInitializedError):
            self.sqlite_backend.load_store()

    def test_valid_existing_sqlite_store_is_not_replaced_by_json(self) -> None:
        sqlite_payload = {"users": {"42": {"name": "sqlite"}}, "settings": {"source": "sqlite"}}
        json_payload = {"users": {"42": {"name": "json"}}, "settings": {"source": "json"}}
        self.sqlite_backend.save_store(sqlite_payload)
        self.json_backend.save_store(json_payload)

        loaded = migrate_json_to_backend_if_needed(
            self.sqlite_backend,
            self.json_backend,
            _new_store(),
        )

        self.assertEqual(loaded, sqlite_payload)
        self.assertEqual(self.json_backend.load_store(), json_payload)

    def test_nonempty_sqlite_read_error_does_not_trigger_json_overwrite(self) -> None:
        json_payload = {"users": {"42": {"name": "json"}}, "settings": {"source": "json"}}
        self.json_backend.save_store(json_payload)
        self.sqlite_path.touch()

        with (
            patch.object(
                self.sqlite_backend,
                "load_store",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            patch.object(self.sqlite_backend, "initialize_empty_store") as initialize,
            self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"),
        ):
            migrate_json_to_backend_if_needed(
                self.sqlite_backend,
                self.json_backend,
                _new_store(),
            )

        initialize.assert_not_called()
        self.assertEqual(self.json_backend.load_store(), json_payload)

    def test_empty_sqlite_without_json_is_initialized_with_defaults(self) -> None:
        sqlite3.connect(self.sqlite_path).close()

        loaded = migrate_json_to_backend_if_needed(
            self.sqlite_backend,
            self.json_backend,
            _new_store(),
        )

        self.assertEqual(loaded, _new_store())
        self.assertEqual(self.sqlite_backend.load_store(), _new_store())


if __name__ == "__main__":
    unittest.main()
