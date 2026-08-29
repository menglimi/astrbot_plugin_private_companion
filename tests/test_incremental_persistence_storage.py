# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.storage.json_backend import JsonStoreBackend
from astrbot_plugin_private_companion.storage.migration import (
    migrate_json_to_backend_if_needed,
)
from astrbot_plugin_private_companion.storage.sqlite_backend import (
    SqliteRevisionConflictError,
    SqliteSchemaError,
    SqliteStoreBackend,
    SqliteStoreNotInitializedError,
    SqliteUnsupportedSchemaError,
)
from astrbot_plugin_private_companion.storage.store_manager import StoreManager


def _new_store() -> dict:
    return {"users": {}, "settings": {"source": "default"}}


def _ensure_defaults(data: dict) -> dict:
    result = deepcopy(data)
    result.setdefault("users", {})
    result.setdefault("settings", {"source": "default"})
    return result


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _checksum(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class _StartupMaintenanceHarness(CoreStoreMixin):
    def __init__(
        self,
        manager: StoreManager,
        *,
        recovered_items: list[dict] | None = None,
        recovery_result: int = 0,
    ) -> None:
        self.store_manager = manager
        self.storage_backend = "sqlite"
        self.enable_store_control_tag_sanitization = True
        self.recovered_items = recovered_items
        self.recovery_result = recovery_result

    def _recover_bookshelf_items_from_local_pages_inplace(self, data: dict) -> int:
        if self.recovered_items is not None:
            data["bookshelf_items"] = deepcopy(self.recovered_items)
        return self.recovery_result


class _SqliteWriterHarness(CoreStoreMixin):
    def __init__(self, manager: StoreManager, data: dict) -> None:
        self.store_manager = manager
        self.storage_backend = "sqlite"
        self.data = data
        self.enable_multi_persona_mode = False
        self.enable_store_control_tag_sanitization = True
        self._data_save_task = None
        self._stop_event = asyncio.Event()


_V1_COLUMNS_FOR_TEST = (
    "section_name",
    "payload_json",
    "updated_at",
    "checksum",
    "schema_version",
)


class IncrementalPersistenceStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sqlite_path = self.root / "companions.db"
        self.json_path = self.root / "companions.json"
        self.backend = SqliteStoreBackend(
            self.sqlite_path,
            _ensure_defaults,
            _new_store,
        )
        self.json_backend = JsonStoreBackend(
            self.json_path,
            _ensure_defaults,
            _new_store,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _manager(self, backend_name: str = "sqlite") -> StoreManager:
        return StoreManager(
            backend_name=backend_name,
            data_file=self.json_path,
            sqlite_path=self.sqlite_path,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )

    def _create_v1(self, payload: dict, *, checksum: str = "") -> None:
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "CREATE TABLE store_sections ("
                "section_name TEXT PRIMARY KEY,"
                "payload_json TEXT NOT NULL,"
                "updated_at REAL NOT NULL,"
                "checksum TEXT DEFAULT '',"
                "schema_version INTEGER DEFAULT 1)"
            )
            connection.executemany(
                "INSERT INTO store_sections VALUES(?,?,?,?,1)",
                [
                    (name, json.dumps(value, ensure_ascii=False), 10.0, checksum)
                    for name, value in payload.items()
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def _row(self, section_name: str) -> tuple:
        connection = sqlite3.connect(self.sqlite_path)
        try:
            row = connection.execute(
                "SELECT payload_json,updated_at,checksum,schema_version,revision,is_deleted "
                "FROM store_sections WHERE section_name=?",
                (section_name,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise AssertionError(f"missing row: {section_name}")
        return row

    def _meta(self) -> dict[str, str]:
        connection = sqlite3.connect(self.sqlite_path)
        try:
            return dict(
                connection.execute("SELECT meta_key,meta_value FROM store_meta")
            )
        finally:
            connection.close()

    def test_v1_database_is_backed_up_and_migrated_to_v2_once(self) -> None:
        payload = {
            "users": {"42": {"name": "中文"}},
            "settings": {"source": "sqlite"},
        }
        self._create_v1(payload)

        self.assertEqual(payload, self.backend.load_store())

        connection = sqlite3.connect(self.sqlite_path)
        try:
            self.assertEqual(2, connection.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(store_sections)")
            }
        finally:
            connection.close()
        self.assertEqual(
            {
                "section_name",
                "payload_json",
                "updated_at",
                "checksum",
                "schema_version",
                "revision",
                "is_deleted",
            },
            columns,
        )
        self.assertEqual({"next_revision": "2", "initialized": "1"}, self._meta())
        for name, value in payload.items():
            row = self._row(name)
            self.assertEqual(value, json.loads(row[0]))
            self.assertEqual(_checksum(value), row[2])
            self.assertEqual((2, 1, 0), row[3:])

        backups = list(self.root.glob("companions.db.pre-v2-*.bak"))
        self.assertEqual(1, len(backups))
        backup = sqlite3.connect(backups[0])
        try:
            self.assertEqual(0, backup.execute("PRAGMA user_version").fetchone()[0])
            backup_columns = {
                row[1] for row in backup.execute("PRAGMA table_info(store_sections)")
            }
        finally:
            backup.close()
        self.assertNotIn("revision", backup_columns)

        self.assertEqual(payload, self.backend.load_store())
        self.assertEqual(1, len(list(self.root.glob("companions.db.pre-v2-*.bak"))))
        self.assertEqual(2, self.backend.next_revision())

    def test_empty_v1_store_remains_uninitialized_and_recovers_from_json(self) -> None:
        self._create_v1({})
        json_payload = {
            "users": {"42": {"name": "from-json"}},
            "settings": {"source": "json"},
        }
        self.json_backend.save_store(json_payload)

        loaded = migrate_json_to_backend_if_needed(
            self.backend,
            self.json_backend,
            _new_store(),
        )

        self.assertEqual(json_payload, loaded)
        self.assertEqual(json_payload, self.backend.load_store())
        self.assertEqual({"next_revision": "2", "initialized": "1"}, self._meta())
        self.assertEqual(1, len(list(self.root.glob("companions.db.pre-v2-*.bak"))))

    def test_invalid_v1_json_rolls_back_schema_migration(self) -> None:
        self._create_v1({"users": {}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET payload_json='{invalid' WHERE section_name='users'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            self.backend.load_store()

        connection = sqlite3.connect(self.sqlite_path)
        try:
            self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(store_sections)")
            }
            stored = connection.execute(
                "SELECT payload_json FROM store_sections WHERE section_name='users'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertNotIn("revision", columns)
        self.assertEqual("{invalid", stored)
        self.assertEqual(1, len(list(self.root.glob("companions.db.pre-v2-*.bak"))))

    def test_user_version_one_uses_the_same_transactional_migration(self) -> None:
        payload = {"users": {"42": {"name": "version-one"}}}
        self._create_v1(payload)
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(_ensure_defaults(payload), self.backend.load_store())

        connection = sqlite3.connect(self.sqlite_path)
        try:
            self.assertEqual(2, connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()
        self.assertEqual(1, len(list(self.root.glob("companions.db.pre-v2-*.bak"))))

    def test_negative_schema_marker_fails_closed_without_v1_migration(self) -> None:
        self._create_v1({"users": {"42": {"name": "untouched"}}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("PRAGMA user_version=-1")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "marker is invalid"):
            self.backend.load_store()

        connection = sqlite3.connect(self.sqlite_path)
        try:
            self.assertEqual(
                -1, connection.execute("PRAGMA user_version").fetchone()[0]
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(store_sections)")
            }
        finally:
            connection.close()
        self.assertNotIn("revision", columns)
        self.assertEqual([], list(self.root.glob("companions.db.pre-v2-*.bak")))

    def test_injected_migration_failure_leaves_the_complete_v1_table(self) -> None:
        payload = {"users": {"42": {"name": "untouched"}}}
        self._create_v1(payload)

        with (
            patch.object(
                self.backend,
                "_create_sections_table",
                side_effect=RuntimeError("injected migration failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected migration failure"),
        ):
            self.backend.load_store()

        connection = sqlite3.connect(self.sqlite_path)
        try:
            self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(
                _V1_COLUMNS_FOR_TEST,
                tuple(
                    row[1]
                    for row in connection.execute("PRAGMA table_info(store_sections)")
                ),
            )
            rows = connection.execute(
                "SELECT section_name,payload_json FROM store_sections"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [("users", json.dumps(payload["users"], ensure_ascii=False))], rows
        )
        self.assertEqual(1, len(list(self.root.glob("companions.db.pre-v2-*.bak"))))

    def test_invalid_nonempty_v1_checksum_rolls_back_schema_migration(self) -> None:
        self._create_v1({"users": {}}, checksum="0" * 64)

        with self.assertRaisesRegex(SqliteSchemaError, "checksum"):
            self.backend.load_store()

        connection = sqlite3.connect(self.sqlite_path)
        try:
            self.assertEqual(0, connection.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(store_sections)")
            }
        finally:
            connection.close()
        self.assertNotIn("revision", columns)

    def test_v1_raw_json_checksum_is_validated_before_canonical_migration(self) -> None:
        payload = {"users": {"z": 1, "a": "中文"}}
        self._create_v1(payload)
        raw_payload = json.dumps(
            payload["users"],
            ensure_ascii=False,
            indent=2,
        )
        raw_checksum = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET payload_json=?,checksum=? "
                "WHERE section_name='users'",
                (raw_payload, raw_checksum),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(_ensure_defaults(payload), self.backend.load_store())
        migrated = self._row("users")
        self.assertEqual(_canonical(payload["users"]), migrated[0])
        self.assertEqual(_checksum(payload["users"]), migrated[2])

        backup_path = next(self.root.glob("companions.db.pre-v2-*.bak"))
        backup = sqlite3.connect(backup_path)
        try:
            backed_up = backup.execute(
                "SELECT payload_json,checksum FROM store_sections "
                "WHERE section_name='users'"
            ).fetchone()
        finally:
            backup.close()
        self.assertEqual((raw_payload, raw_checksum), backed_up)

    def test_unknown_newer_schema_is_rejected_without_mutation(self) -> None:
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("CREATE TABLE future_data(value TEXT)")
            connection.execute("PRAGMA user_version=3")
            connection.commit()
        finally:
            connection.close()
        original = self.sqlite_path.read_bytes()

        with self.assertRaisesRegex(SqliteUnsupportedSchemaError, "version 3"):
            self.backend.health_check(raise_on_error=True)

        self.assertEqual(original, self.sqlite_path.read_bytes())
        self.assertEqual([], list(self.root.glob("companions.db.pre-v2-*.bak")))

    def test_v2_marker_with_v1_columns_fails_closed(self) -> None:
        self._create_v1({"users": {}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("PRAGMA user_version=2")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "v2 schema"):
            self.backend.load_store()

    def test_v2_missing_meta_and_invalid_next_revision_fail_closed(self) -> None:
        self.backend.save_store({"users": {}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("DROP TABLE store_meta")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "incomplete"):
            self.backend.load_store()

        self.sqlite_path.unlink()
        self.backend.save_store({"users": {}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_meta SET meta_value='0' WHERE meta_key='next_revision'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "next_revision"):
            self.backend.load_store()

    def test_v2_rejects_unexpected_tables_and_meta_keys(self) -> None:
        self.backend.save_store({"users": {}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "unexpected tables"):
            self.backend.load_store()

        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute("DROP TABLE unrelated")
            connection.execute("INSERT INTO store_meta VALUES('future_key','1')")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "meta keys"):
            self.backend.load_store()

    def test_v2_missing_column_and_bad_primary_key_fail_closed(self) -> None:
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "CREATE TABLE store_sections("
                "section_name TEXT NOT NULL PRIMARY KEY,payload_json TEXT NOT NULL,"
                "updated_at REAL NOT NULL,checksum TEXT NOT NULL,schema_version INTEGER NOT NULL,"
                "revision INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE store_meta(meta_key TEXT NOT NULL PRIMARY KEY,meta_value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO store_meta VALUES(?,?)",
                (("initialized", "0"), ("next_revision", "1")),
            )
            connection.execute("PRAGMA user_version=2")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "columns"):
            self.backend.load_store()

        self.sqlite_path.unlink()
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "CREATE TABLE store_sections("
                "section_name TEXT NOT NULL,payload_json TEXT NOT NULL,updated_at REAL NOT NULL,"
                "checksum TEXT NOT NULL DEFAULT '',schema_version INTEGER NOT NULL DEFAULT 2,"
                "revision INTEGER NOT NULL DEFAULT 0,is_deleted INTEGER NOT NULL DEFAULT 0,"
                "CONSTRAINT store_sections_checksum_sha256 CHECK(length(checksum)=64),"
                "CONSTRAINT store_sections_schema_v2 CHECK(schema_version=2),"
                "CONSTRAINT store_sections_positive_revision CHECK(revision>0),"
                "CONSTRAINT store_sections_deleted_flag CHECK(is_deleted IN (0,1)))"
            )
            connection.execute(
                "CREATE TABLE store_meta(meta_key TEXT NOT NULL PRIMARY KEY,meta_value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO store_meta VALUES(?,?)",
                (("initialized", "0"), ("next_revision", "1")),
            )
            connection.execute("PRAGMA user_version=2")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(SqliteSchemaError, "primary key"):
            self.backend.load_store()

    def test_v2_rejects_non_integer_storage_classes_for_integer_metadata(self) -> None:
        for column, value in (
            ("schema_version", 2.5),
            ("revision", 1.5),
            ("is_deleted", 0.5),
        ):
            with self.subTest(column=column):
                if self.sqlite_path.exists():
                    self.sqlite_path.unlink()
                self.backend.save_store({"users": {"value": "safe"}})
                connection = sqlite3.connect(self.sqlite_path)
                try:
                    connection.execute("PRAGMA ignore_check_constraints=ON")
                    connection.execute(
                        f"UPDATE store_sections SET {column}=? WHERE section_name='users'",
                        (value,),
                    )
                    connection.commit()
                finally:
                    connection.close()

                with self.assertRaisesRegex(SqliteSchemaError, "metadata is invalid"):
                    self.backend.load_store()

    def test_revision_outside_safe_sqlite_range_is_rejected_before_connect(
        self,
    ) -> None:
        with (
            patch.object(self.backend, "_connect") as connect,
            self.assertRaisesRegex(ValueError, "within SQLite range"),
        ):
            self.backend.save_sections(
                {"users": (((1 << 63) - 1), {"value": "too-large"})},
                {},
            )

        connect.assert_not_called()

    def test_incremental_write_only_changes_requested_section(self) -> None:
        payload = {
            "users": {"42": {"name": "before"}},
            "settings": {"source": "before"},
        }
        self.backend.save_store(payload)
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET updated_at=111.0 WHERE section_name='settings'"
            )
            connection.commit()
        finally:
            connection.close()
        settings_before = self._row("settings")

        confirmed = self.backend.save_sections(
            {"users": (2, {"42": {"name": "after"}})},
            {},
        )

        self.assertEqual({"users": 2}, confirmed)
        self.assertEqual(settings_before, self._row("settings"))
        users = self._row("users")
        self.assertEqual({"42": {"name": "after"}}, json.loads(users[0]))
        self.assertEqual(2, users[4])
        self.assertEqual(3, self.backend.next_revision())

    def test_legacy_v2_row_with_empty_checksum_is_upgraded_on_first_partial_write(
        self,
    ) -> None:
        self.backend.save_store({"users": {"value": "legacy"}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET checksum='' WHERE section_name='users'"
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual({"value": "legacy"}, self.backend.load_store()["users"])
        self.assertEqual(
            {"users": 2},
            self.backend.save_sections({"users": (2, {"value": "upgraded"})}, {}),
        )
        self.assertEqual(_checksum({"value": "upgraded"}), self._row("users")[2])

    def test_legacy_v2_empty_checksum_same_revision_is_upgraded_idempotently(
        self,
    ) -> None:
        payload = {"value": "legacy"}
        self.backend.save_store({"users": payload})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET checksum='',updated_at=333.0 "
                "WHERE section_name='users'"
            )
            connection.commit()
        finally:
            connection.close()
        before = self._row("users")

        self.assertEqual(
            {"users": 1},
            self.backend.save_sections({"users": (1, payload)}, {}),
        )

        after = self._row("users")
        self.assertEqual(_checksum(payload), after[2])
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[3], after[3])
        self.assertEqual(before[4:], after[4:])

    def test_legacy_v2_tombstone_empty_checksum_is_upgraded_idempotently(
        self,
    ) -> None:
        self.backend.save_store({"obsolete": {"value": "legacy"}})
        self.backend.save_sections({}, {"obsolete": 2})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET checksum='',updated_at=444.0 "
                "WHERE section_name='obsolete'"
            )
            connection.commit()
        finally:
            connection.close()

        self.assertNotIn("obsolete", self.backend.load_store())
        before = self._row("obsolete")
        self.assertEqual(
            {"obsolete": 2},
            self.backend.save_sections({}, {"obsolete": 2}),
        )

        after = self._row("obsolete")
        self.assertEqual(_checksum(None), after[2])
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[3:], after[3:])

    def test_same_payload_advances_revision_without_touching_payload_or_timestamp(
        self,
    ) -> None:
        payload = {"users": {"42": {"name": "same"}}}
        self.backend.save_store(payload)
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "UPDATE store_sections SET updated_at=222.0 WHERE section_name='users'"
            )
            connection.commit()
        finally:
            connection.close()
        before = self._row("users")

        self.assertEqual(
            {"users": 2},
            self.backend.save_sections({"users": (2, payload["users"])}, {}),
        )

        after = self._row("users")
        self.assertEqual(before[:4], after[:4])
        self.assertEqual((2, 0), after[4:])

    def test_equal_revision_retry_is_idempotent_but_conflicting_batch_rolls_back(
        self,
    ) -> None:
        self.backend.save_store({"users": {"value": 1}, "settings": {"value": 1}})
        self.backend.save_sections(
            {"users": (2, {"value": 2}), "settings": (2, {"value": 2})},
            {},
        )
        users_before = self._row("users")
        settings_before = self._row("settings")

        self.assertEqual(
            {"users": 2},
            self.backend.save_sections({"users": (2, {"value": 2})}, {}),
        )
        with self.assertRaises(SqliteRevisionConflictError):
            self.backend.save_sections(
                {"users": (2, {"value": "conflict"}), "settings": (3, {"value": 3})},
                {},
            )

        self.assertEqual(users_before, self._row("users"))
        self.assertEqual(settings_before, self._row("settings"))

    def test_lower_revision_is_rejected(self) -> None:
        self.backend.save_store({"users": {"value": 1}})
        self.backend.save_sections({"users": (2, {"value": 2})}, {})

        with self.assertRaises(SqliteRevisionConflictError):
            self.backend.save_sections({"users": (1, {"value": 3})}, {})

        self.assertEqual({"value": 2}, json.loads(self._row("users")[0]))

    def test_revision_retry_can_advance_an_older_row_after_other_section_progress(
        self,
    ) -> None:
        self.backend.save_store({"users": {"value": 1}, "settings": {"value": 1}})
        self.backend.save_sections({"settings": (3, {"value": 3})}, {})

        self.assertEqual(
            {"users": 2},
            self.backend.save_sections({"users": (2, {"value": "retry"})}, {}),
        )

        self.assertEqual({"value": "retry"}, json.loads(self._row("users")[0]))

    def test_restart_continues_from_persisted_next_revision(self) -> None:
        self.backend.save_store({"users": {"value": 1}, "settings": {"value": 1}})
        self.backend.save_sections({"users": (2, {"value": 2})}, {})
        restarted = SqliteStoreBackend(
            self.sqlite_path,
            _ensure_defaults,
            _new_store,
        )

        self.assertEqual(3, restarted.next_revision())
        self.assertEqual(
            {"settings": 3},
            restarted.save_sections({"settings": (3, {"value": 3})}, {}),
        )
        self.assertEqual(4, restarted.next_revision())

    def test_changed_deleted_overlap_is_rejected_before_opening_database(self) -> None:
        with (
            patch.object(self.backend, "_connect") as connect,
            self.assertRaises(ValueError),
        ):
            self.backend.save_sections({"users": (1, {})}, {"users": 1})

        connect.assert_not_called()

    def test_batch_database_failure_rolls_back_every_section(self) -> None:
        self.backend.save_store({"users": {"value": 1}, "settings": {"value": 1}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            connection.execute(
                "CREATE TRIGGER reject_settings_update BEFORE UPDATE ON store_sections "
                "WHEN NEW.section_name='settings' BEGIN "
                "SELECT RAISE(ABORT, 'settings rejected'); END"
            )
            connection.commit()
        finally:
            connection.close()
        users_before = self._row("users")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "settings rejected"):
            self.backend.save_sections(
                {"users": (2, {"value": 2}), "settings": (2, {"value": 2})},
                {},
            )

        self.assertEqual(users_before, self._row("users"))

    def test_tombstone_only_store_is_initialized_and_blocks_json_reimport(self) -> None:
        self.backend.save_store({"obsolete": {"source": "sqlite"}})
        self.backend.save_sections({}, {"obsolete": 2})
        self.json_backend.save_store({"obsolete": {"source": "json"}})

        restarted = SqliteStoreBackend(
            self.sqlite_path,
            _ensure_defaults,
            _new_store,
        )
        loaded = migrate_json_to_backend_if_needed(
            restarted,
            self.json_backend,
            _new_store(),
        )

        self.assertNotIn("obsolete", loaded)
        row = self._row("obsolete")
        self.assertEqual("null", row[0])
        self.assertEqual(_checksum(None), row[2])
        self.assertEqual((2, 1), row[4:])
        self.assertEqual("1", self._meta()["initialized"])
        self.assertEqual(3, restarted.next_revision())

    def test_tombstone_remains_absent_when_store_defaults_include_that_section(
        self,
    ) -> None:
        def new_store_with_secret() -> dict:
            return {"users": {}, "bookshelf_secret": {"password": "default"}}

        def ensure_defaults_with_secret(data: dict) -> dict:
            result = deepcopy(data)
            result.setdefault("users", {})
            result.setdefault("bookshelf_secret", {"password": "default"})
            return result

        backend = SqliteStoreBackend(
            self.sqlite_path,
            ensure_defaults_with_secret,
            new_store_with_secret,
        )
        backend.save_store({"users": {}, "bookshelf_secret": {"password": "persisted"}})
        backend.save_sections({}, {"bookshelf_secret": 2})

        loaded = backend.load_store()

        self.assertNotIn("bookshelf_secret", loaded)
        self.assertEqual({}, loaded["users"])
        self.assertEqual((2, 1), self._row("bookshelf_secret")[4:])

    def test_full_replace_removes_old_tombstones(self) -> None:
        self.backend.save_store({"obsolete": {"value": 1}})
        self.backend.save_sections({}, {"obsolete": 2})

        self.backend.save_store({"users": {"fresh": True}})

        connection = sqlite3.connect(self.sqlite_path)
        try:
            names = {
                row[0]
                for row in connection.execute("SELECT section_name FROM store_sections")
            }
        finally:
            connection.close()
        self.assertEqual({"users"}, names)

    def test_full_snapshot_can_commit_explicit_tombstones(self) -> None:
        self.backend.save_store(
            {"users": {"fresh": True}, "obsolete": {"value": "old"}}
        )

        persisted_revision = self.backend.save_snapshot(
            {"users": {"fresh": "latest"}},
            minimum_revision=7,
            deleted_sections={"obsolete": 8},
        )

        self.assertGreaterEqual(persisted_revision, 8)
        self.assertEqual({"fresh": "latest"}, self.backend.load_store()["users"])
        self.assertEqual((2, persisted_revision, 1), self._row("obsolete")[3:])

    def test_full_snapshot_can_preserve_existing_tombstones(self) -> None:
        self.backend.save_store(
            {"users": {"fresh": True}, "obsolete": {"value": "old"}}
        )
        self.backend.save_sections({}, {"obsolete": 2})

        persisted_revision = self.backend.save_snapshot(
            {"users": {"fresh": "latest"}},
            minimum_revision=3,
            preserve_tombstones=True,
        )

        self.assertGreaterEqual(persisted_revision, 3)
        self.assertEqual({"fresh": "latest"}, self.backend.load_store()["users"])
        self.assertEqual((2, persisted_revision, 1), self._row("obsolete")[3:])

    def test_v2_check_constraints_reject_legacy_five_column_writer(self) -> None:
        self.backend.save_store({"users": {"value": "safe"}})
        connection = sqlite3.connect(self.sqlite_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "REPLACE INTO store_sections("
                    "section_name,payload_json,updated_at,checksum,schema_version"
                    ") VALUES(?,?,?,?,1)",
                    (
                        "users",
                        "{}",
                        1.0,
                        "",
                    ),
                )
            connection.rollback()
        finally:
            connection.close()

        self.assertEqual({"value": "safe"}, self.backend.load_store()["users"])

    def test_store_manager_incremental_path_never_loads_or_exports_full_store(
        self,
    ) -> None:
        manager = self._manager("sqlite")
        manager.save_store({"users": {"value": 1}, "settings": {"value": 1}})

        with (
            patch.object(
                manager.backend, "load_store", side_effect=AssertionError("full load")
            ),
            patch.object(
                manager.json_backend,
                "save_store",
                side_effect=AssertionError("json export"),
            ),
        ):
            confirmed = manager.save_sections({"users": (2, {"value": 2})}, {})

        self.assertEqual({"users": 2}, confirmed)
        self.assertEqual(3, manager.next_revision())

    def test_sqlite_full_compatibility_mark_preserves_existing_tombstone(self) -> None:
        manager = self._manager("sqlite")
        manager.save_store(
            {
                "users": {"owner": {"name": "before"}},
                "settings": {"source": "sqlite"},
                "obsolete": {"must": "stay-deleted"},
            }
        )
        manager.save_sections({}, {"obsolete": 2})
        data = manager.load_initial_store()
        data["users"]["owner"]["name"] = "after"
        harness = _SqliteWriterHarness(manager, data)

        async def flush() -> None:
            harness._schedule_data_save(full_scope="admin_import_export", delay=0.0)
            await harness._flush_scheduled_data_save()

        asyncio.run(flush())

        self.assertEqual({"obsolete": 2}, manager.deleted_section_revisions(["obsolete"]))
        self.assertEqual(
            "after",
            manager.load_initial_store()["users"]["owner"]["name"],
        )

    def test_terminate_full_dirty_removes_live_missing_section(self) -> None:
        manager = self._manager("sqlite")
        manager.save_store(
            {
                "users": {"owner": {"name": "before"}},
                "obsolete": {"must": "be-removed"},
            }
        )
        data = manager.load_initial_store()
        harness = _SqliteWriterHarness(manager, data)
        harness._stop_event.set()

        async def flush() -> None:
            harness._schedule_data_save(full_scope="admin_import_export", delay=0.0)
            harness.data.pop("obsolete")
            await harness._flush_default_data_save_on_terminate()

        asyncio.run(flush())

        self.assertNotIn("obsolete", manager.load_initial_store())
        self.assertNotIn("obsolete", manager.deleted_section_revisions(["obsolete"]))

    def test_startup_cleanup_updates_only_changed_section_and_preserves_tombstone(
        self,
    ) -> None:
        manager = self._manager("sqlite")
        manager.save_store(
            {
                "users": {},
                "settings": {"source": "sqlite"},
                "memory": {"summary": "clean <bubble/> me"},
                "obsolete": {"must": "stay-deleted"},
            }
        )
        manager.save_sections({}, {"obsolete": 2})

        loaded = _StartupMaintenanceHarness(manager)._load_data_sync()

        self.assertEqual("clean me", loaded["memory"]["summary"])
        self.assertEqual((3, 0), self._row("memory")[4:])
        self.assertEqual((1, 0), self._row("settings")[4:])
        self.assertEqual((2, 1), self._row("obsolete")[4:])
        self.assertNotIn("obsolete", manager.load_initial_store())

    def test_startup_local_recovery_cannot_revive_bookshelf_tombstone(self) -> None:
        manager = self._manager("sqlite")
        manager.save_store(
            {
                "users": {},
                "settings": {"source": "sqlite"},
                "bookshelf_items": [{"key": "stored"}],
                "bookshelf_secret": {"password": "safe"},
                "bookshelf_store_revision": 1,
                "reading_archive_integration": {},
            }
        )
        manager.save_sections({}, {"bookshelf_items": 2})

        for recovered_items, recovery_result in (
            ([], 0),
            ([{"key": "local-page"}], 1),
        ):
            with self.subTest(recovery_result=recovery_result):
                loaded = _StartupMaintenanceHarness(
                    manager,
                    recovered_items=recovered_items,
                    recovery_result=recovery_result,
                )._load_data_sync()

                self.assertNotIn("bookshelf_items", loaded)
                self.assertEqual((2, 1), self._row("bookshelf_items")[4:])

    def test_startup_bookshelf_group_rewrites_tombstone_with_shared_revision(
        self,
    ) -> None:
        manager = self._manager("sqlite")
        manager.save_store(
            {
                "users": {},
                "settings": {"source": "sqlite"},
                "bookshelf_items": [],
                "bookshelf_secret": {"password": "delete-me"},
                "bookshelf_store_revision": 1,
                "reading_archive_integration": {},
            }
        )
        manager.save_sections({}, {"bookshelf_secret": 2})

        loaded = _StartupMaintenanceHarness(
            manager,
            recovered_items=[{"key": "local-page"}],
            recovery_result=1,
        )._load_data_sync()

        self.assertEqual([{"key": "local-page"}], loaded["bookshelf_items"])
        self.assertNotIn("bookshelf_secret", loaded)
        revisions = {
            self._row(section)[4]
            for section in (
                "bookshelf_items",
                "bookshelf_secret",
                "bookshelf_store_revision",
                "reading_archive_integration",
            )
        }
        self.assertEqual({3}, revisions)
        self.assertEqual(1, self._row("bookshelf_secret")[5])

    def test_bookshelf_recovery_preserves_unrelated_sqlite_tombstone(self) -> None:
        selected = {
            "users": {},
            "settings": {"source": "sqlite"},
            "bookshelf_items": [],
            "bookshelf_secret": {},
            "bookshelf_store_revision": 1,
            "reading_archive_integration": {},
            "obsolete": {"must": "stay-deleted"},
        }
        fallback = deepcopy(selected)
        fallback["settings"] = {"source": "json"}
        fallback["bookshelf_items"] = [
            {
                "key": "jm_album:42",
                "type": "jm_album",
                "album_id": "42",
                "title": "recovered",
                "pages": [],
            }
        ]
        fallback["bookshelf_store_revision"] = 2
        self.backend.save_store(selected)
        self.backend.save_sections({}, {"obsolete": 2})
        self.json_backend.save_store(fallback)

        loaded = self._manager("sqlite").load_initial_store()

        self.assertEqual(
            ["42"], [item["album_id"] for item in loaded["bookshelf_items"]]
        )
        obsolete = self._row("obsolete")
        self.assertEqual((2, 1), obsolete[4:])
        self.assertNotIn("obsolete", self.backend.load_store())

    def test_bookshelf_recovery_does_not_revive_its_own_section_tombstone(self) -> None:
        selected = {
            "users": {},
            "settings": {"source": "sqlite"},
            "bookshelf_items": [],
            "bookshelf_secret": {"password": "delete-me"},
            "bookshelf_store_revision": 1,
            "reading_archive_integration": {},
        }
        fallback = deepcopy(selected)
        fallback["bookshelf_items"] = [
            {
                "key": "jm_album:77",
                "type": "jm_album",
                "album_id": "77",
                "title": "fallback",
                "pages": [],
            }
        ]
        fallback["bookshelf_secret"] = {"password": "stale-secret"}
        fallback["bookshelf_store_revision"] = 2
        self.backend.save_store(selected)
        self.backend.save_sections({}, {"bookshelf_secret": 2})
        self.json_backend.save_store(fallback)

        loaded = self._manager("sqlite").load_initial_store()

        self.assertEqual(
            ["77"], [item["album_id"] for item in loaded["bookshelf_items"]]
        )
        self.assertNotIn("bookshelf_secret", loaded)
        tombstone = self._row("bookshelf_secret")
        self.assertEqual(1, tombstone[5])
        self.assertGreaterEqual(tombstone[4], 3)
        self.assertNotIn("bookshelf_secret", self.backend.load_store())

    def test_new_writes_use_canonical_compact_unicode_json(self) -> None:
        self.backend.save_store({"ordered": {"b": "中文", "a": 1}})

        row = self._row("ordered")
        self.assertEqual('{"a":1,"b":"中文"}', row[0])
        self.assertEqual(hashlib.sha256(row[0].encode("utf-8")).hexdigest(), row[2])

    def test_uninitialized_v2_schema_remains_distinct_from_committed_empty_store(
        self,
    ) -> None:
        sqlite3.connect(self.sqlite_path).close()

        with self.assertRaises(SqliteStoreNotInitializedError):
            self.backend.load_store()
        self.assertEqual({"next_revision": "1", "initialized": "0"}, self._meta())

        self.backend.save_store({})

        self.assertEqual(_new_store(), self.backend.load_store())
        self.assertEqual({"next_revision": "2", "initialized": "1"}, self._meta())


if __name__ == "__main__":
    unittest.main()
