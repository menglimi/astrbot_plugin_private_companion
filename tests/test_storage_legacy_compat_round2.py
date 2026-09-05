# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from astrbot_plugin_private_companion.helpers import _safe_float
from astrbot_plugin_private_companion.storage.json_backend import JsonStoreBackend
from astrbot_plugin_private_companion.storage.migration import migrate_json_to_backend_if_needed
from astrbot_plugin_private_companion.storage.sqlite_backend import SqliteStoreBackend
from astrbot_plugin_private_companion.storage.store_manager import StoreManager


def _new_store() -> dict:
    return {"users": {}, "settings": {"source": "default"}}


def _ensure_defaults(data: dict) -> dict:
    result = deepcopy(data)
    result.setdefault("users", {})
    result.setdefault("settings", {"source": "default"})
    return result


class StorageLegacyCompatibilityRound2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.json_path = self.root / "companions.json"
        self.sqlite_path = self.root / "companions.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def manager(self, backend: str) -> StoreManager:
        return StoreManager(
            backend_name=backend,
            data_file=self.json_path,
            sqlite_path=self.sqlite_path,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )

    @staticmethod
    def payload() -> dict:
        return {
            "users": {"42": {"name": "before", "opaque": None}},
            "settings": {"source": "legacy", "known": 1},
            "bookshelf_items": [],
            "future_section": {
                "nested": {"explicit_none": None, "items": [0, False, "0"]},
                "flag": "false",
            },
        }

    def test_json_unknown_top_level_section_survives_round_trip_restart(self) -> None:
        manager = self.manager("json")
        manager.save_store(self.payload())
        loaded = manager.load_initial_store()
        loaded["settings"]["known"] = 2
        manager.save_store(loaded)

        restarted = self.manager("json").load_initial_store()
        self.assertEqual(restarted["settings"]["known"], 2)
        self.assertEqual(restarted["future_section"], self.payload()["future_section"])

    def test_sqlite_unknown_top_level_section_survives_round_trip_restart(self) -> None:
        manager = self.manager("sqlite")
        manager.save_store(self.payload())
        loaded = manager.load_initial_store()
        loaded["settings"]["known"] = 2
        manager.save_store(loaded)

        restarted = self.manager("sqlite").load_initial_store()
        self.assertEqual(restarted["settings"]["known"], 2)
        self.assertEqual(restarted["future_section"], self.payload()["future_section"])

    def test_json_sqlite_json_sqlite_switch_preserves_data_and_revision(self) -> None:
        json_manager = self.manager("json")
        json_manager.save_store(self.payload())

        sqlite_manager = self.manager("sqlite")
        first_sqlite = sqlite_manager.load_initial_store()
        first_revision = sqlite_manager.next_revision()
        self.assertEqual(first_sqlite, _ensure_defaults(self.payload()))

        first_sqlite["users"]["42"]["name"] = "from-sqlite"
        sqlite_manager.save_store(first_sqlite)
        after_sqlite_save = sqlite_manager.next_revision()
        self.assertGreaterEqual(after_sqlite_save, first_revision)

        self.assertTrue(sqlite_manager.export_current_to_json(first_sqlite, force=True))
        json_restarted = self.manager("json")
        from_json = json_restarted.load_initial_store()
        self.assertEqual(from_json, first_sqlite)
        from_json["settings"]["known"] = 3
        json_restarted.save_store(from_json)

        # Model a configured JSON -> SQLite switch: carry the selected authority
        # into the existing target, as CoreStore._rebuild_store_manager does.
        sqlite_restarted = self.manager("sqlite")
        sqlite_restarted.save_snapshot(
            from_json,
            minimum_revision=sqlite_restarted.next_revision(),
            preserve_tombstones=True,
        )
        final = self.manager("sqlite").load_initial_store()
        self.assertEqual(final, from_json)
        self.assertEqual(final["future_section"], self.payload()["future_section"])
        self.assertGreaterEqual(sqlite_restarted.next_revision(), after_sqlite_save)

    def test_safe_float_and_raw_string_boolean_legacy_semantics(self) -> None:
        self.assertEqual(_safe_float("not-a-number", 7.5), 7.5)
        self.assertEqual(_safe_float(None, 2.5), 2.5)
        self.assertEqual(_safe_float("-4", 9.0), 0.0)
        self.assertTrue(bool("false"))
        self.assertTrue(bool("0"))
        self.assertFalse(bool(""))

    def test_json_migration_transaction_faults_fail_closed_and_leave_sqlite_uninitialized(self) -> None:
        source = self.payload()
        for fault_point in (
            "DELETE FROM store_sections",
            "INSERT INTO store_sections",
            "UPDATE store_meta SET meta_value='1'",
            "UPDATE store_meta SET meta_value=?",
            "COMMIT",
        ):
            with self.subTest(fault_point=fault_point):
                self.sqlite_path.unlink(missing_ok=True)
                JsonStoreBackend(self.json_path, _ensure_defaults, _new_store).save_store(source)
                backend = SqliteStoreBackend(self.sqlite_path, _ensure_defaults, _new_store)
                # Complete schema initialization first; inject only into the later
                # snapshot transaction that turns the target authoritative.
                backend.health_check(raise_on_error=True)
                original_connect = backend._connect

                class FaultConnection:
                    def __init__(self, connection):
                        self.connection = connection
                    def execute(self, sql, parameters=()):
                        if fault_point != "COMMIT" and fault_point in sql:
                            raise OSError("injected migration interruption")
                        return self.connection.execute(sql, parameters)
                    def executemany(self, sql, parameters):
                        if fault_point != "COMMIT" and fault_point in sql:
                            raise OSError("injected migration interruption")
                        return self.connection.executemany(sql, parameters)
                    def commit(self):
                        if fault_point == "COMMIT":
                            raise OSError("injected migration interruption")
                        return self.connection.commit()
                    def __getattr__(self, name):
                        return getattr(self.connection, name)

                backend._connect = lambda: FaultConnection(original_connect())
                with self.assertRaisesRegex(RuntimeError, "migration.*not durable"):
                    migrate_json_to_backend_if_needed(
                        backend,
                        JsonStoreBackend(self.json_path, _ensure_defaults, _new_store),
                        _new_store(),
                    )
                backend._connect = original_connect
                self.assertEqual(
                    JsonStoreBackend(self.json_path, _ensure_defaults, _new_store).load_store(),
                    source,
                )
                with self.assertRaisesRegex(Exception, "not initialized"):
                    backend.load_store()

    def test_sqlite_transaction_faults_rollback_to_previous_snapshot(self) -> None:
        backend = self.manager("sqlite").sqlite_backend
        original = self.payload()
        backend.save_store(original)
        before_revision = backend.next_revision()

        for fault_point in (
            "DELETE FROM store_sections",
            "INSERT INTO store_sections",
            "UPDATE store_meta SET meta_value='1'",
            "UPDATE store_meta SET meta_value=?",
            "COMMIT",
        ):
            class FaultConnection:
                def __init__(self, connection):
                    self.connection = connection
                def execute(self, sql, parameters=()):
                    if fault_point != "COMMIT" and fault_point in sql:
                        raise OSError("injected transaction interruption")
                    return self.connection.execute(sql, parameters)
                def executemany(self, sql, parameters):
                    if fault_point != "COMMIT" and fault_point in sql:
                        raise OSError("injected transaction interruption")
                    return self.connection.executemany(sql, parameters)
                def commit(self):
                    if fault_point == "COMMIT":
                        raise OSError("injected transaction interruption")
                    return self.connection.commit()
                def __getattr__(self, name):
                    return getattr(self.connection, name)

            original_connect = backend._connect
            real_connect = sqlite3.connect
            backend._connect = lambda rc=real_connect: FaultConnection(rc(str(self.sqlite_path), timeout=15.0))
            try:
                with self.assertRaisesRegex(OSError, "injected transaction interruption"):
                    backend.save_store({**original, "future_section": {"changed": True}})
            finally:
                backend._connect = original_connect
            self.assertEqual(backend.load_store(), original)
            self.assertEqual(backend.next_revision(), before_revision)


if __name__ == "__main__":
    unittest.main()
