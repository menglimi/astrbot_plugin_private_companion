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
from astrbot_plugin_private_companion.storage.store_manager import StoreManager, reconcile_bookshelf_payload


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

    def _store_manager(self, backend_name: str) -> StoreManager:
        return StoreManager(
            backend_name=backend_name,
            data_file=self.json_path,
            sqlite_path=self.sqlite_path,
            ensure_defaults=_ensure_defaults,
            new_store=_new_store,
        )

    @staticmethod
    def _legacy_bookshelf_payload(
        *album_ids: str,
        revision: int = 0,
        deleted_ids: list[str] | None = None,
    ) -> dict:
        payload = _new_store()
        payload["bookshelf_items"] = [
            {
                "key": f"archive_item:{album_id}",
                "type": "legacy_archive_item",
                "album_id": album_id,
                "title": f"album-{album_id}",
                "pages": [],
            }
            for album_id in album_ids
        ]
        payload["reading_archive_integration"] = {
            "deleted_album_ids": list(deleted_ids or [])
        }
        payload["bookshelf_secret"] = {"password": "kept-secret"}
        payload["bookshelf_store_revision"] = revision
        return payload

    def test_missing_json_store_still_returns_defaults(self) -> None:
        self.assertEqual(self.json_backend.load_store(), _new_store())

    def test_managers_for_same_store_share_save_lock(self) -> None:
        first = self._store_manager("json")
        second = self._store_manager("json")

        self.assertIs(first._store_lock, second._store_lock)

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

    def test_existing_empty_sqlite_without_json_fails_closed(self) -> None:
        sqlite3.connect(self.sqlite_path).close()

        with self.assertRaises(SqliteStoreNotInitializedError):
            migrate_json_to_backend_if_needed(
                self.sqlite_backend,
                self.json_backend,
                _new_store(),
            )

        with self.assertRaises(SqliteStoreNotInitializedError):
            self.sqlite_backend.load_store()

    def test_missing_sqlite_and_json_still_allows_first_install(self) -> None:
        loaded = migrate_json_to_backend_if_needed(
            self.sqlite_backend,
            self.json_backend,
            _new_store(),
        )

        self.assertEqual(loaded, _new_store())
        self.assertEqual(self.sqlite_backend.load_store(), _new_store())

    def test_empty_sqlite_section_recovers_nonempty_json_bookshelf(self) -> None:
        self.sqlite_backend.save_store(self._legacy_bookshelf_payload())
        self.json_backend.save_store(self._legacy_bookshelf_payload("1001"))

        manager = self._store_manager("sqlite")
        loaded = manager.load_initial_store()

        self.assertEqual([item["album_id"] for item in loaded["bookshelf_items"]], ["1001"])
        self.assertEqual(
            [item["album_id"] for item in self.sqlite_backend.load_store()["bookshelf_items"]],
            ["1001"],
        )

    def test_empty_json_recovers_nonempty_sqlite_bookshelf(self) -> None:
        self.json_backend.save_store(self._legacy_bookshelf_payload())
        self.sqlite_backend.save_store(self._legacy_bookshelf_payload("2002"))

        manager = self._store_manager("json")
        loaded = manager.load_initial_store()

        self.assertEqual([item["album_id"] for item in loaded["bookshelf_items"]], ["2002"])
        self.assertEqual(
            [item["album_id"] for item in self.json_backend.load_store()["bookshelf_items"]],
            ["2002"],
        )

    def test_empty_save_cannot_overwrite_existing_bookshelf_without_tombstone(self) -> None:
        manager = self._store_manager("json")
        self.json_backend.save_store(self._legacy_bookshelf_payload("3003"))
        incoming = self._legacy_bookshelf_payload()

        manager.save_store(incoming)

        self.assertEqual([item["album_id"] for item in incoming["bookshelf_items"]], ["3003"])
        self.assertEqual(
            [item["album_id"] for item in self.json_backend.load_store()["bookshelf_items"]],
            ["3003"],
        )

    def test_legacy_delete_metadata_is_opaque_and_cannot_delete_items(self) -> None:
        manager = self._store_manager("json")
        self.json_backend.save_store(
            self._legacy_bookshelf_payload("4004", revision=10)
        )
        incoming = self._legacy_bookshelf_payload(revision=11, deleted_ids=["4004"])

        manager.save_store(incoming)

        stored = self.json_backend.load_store()
        self.assertEqual([item["album_id"] for item in stored["bookshelf_items"]], ["4004"])
        self.assertEqual(
            stored["reading_archive_integration"]["deleted_album_ids"],
            ["4004"],
        )

    def test_snapshot_path_preserves_opaque_legacy_archive_payload(self) -> None:
        manager = self._store_manager("json")
        self.json_backend.save_store(
            self._legacy_bookshelf_payload(revision=20, deleted_ids=["5005"])
        )
        stale_snapshot = self._legacy_bookshelf_payload("5005", revision=19)

        manager.save_snapshot(stale_snapshot)

        stored = self.json_backend.load_store()
        self.assertEqual([item["album_id"] for item in stored["bookshelf_items"]], ["5005"])
        self.assertEqual(stored["bookshelf_store_revision"], 20)

    def test_equal_revision_legacy_delete_metadata_is_preserved_not_executed(self) -> None:
        preferred = self._legacy_bookshelf_payload("6006", revision=30)
        fallback = self._legacy_bookshelf_payload(
            revision=30,
            deleted_ids=["6006"],
        )

        reconciled, changed, _recovered = reconcile_bookshelf_payload(preferred, fallback)

        self.assertTrue(changed)
        self.assertEqual(
            [item["album_id"] for item in reconciled["bookshelf_items"]],
            ["6006"],
        )
        self.assertEqual(
            reconciled["reading_archive_integration"]["deleted_album_ids"],
            ["6006"],
        )

    def test_invalid_bookshelf_structures_are_not_coerced_to_empty_lists(self) -> None:
        manager = self._store_manager("json")
        existing = _new_store()
        existing["bookshelf_items"] = {"disk": "legacy"}
        existing["reading_archive_integration"] = "legacy-state"
        self.json_backend.save_store(existing)
        incoming = _new_store()
        incoming["bookshelf_items"] = {"memory": "legacy"}
        incoming["reading_archive_integration"] = "memory-state"

        manager.save_store(incoming)

        stored = self.json_backend.load_store()
        self.assertEqual(stored["bookshelf_items"], {"memory": "legacy"})
        self.assertEqual(stored["reading_archive_integration"], "memory-state")

    def test_empty_fallback_does_not_replace_unknown_legacy_structures(self) -> None:
        preferred = _new_store()
        preferred["bookshelf_items"] = {"legacy": "keep"}
        preferred["reading_archive_integration"] = "legacy-state"
        fallback = _new_store()
        fallback["bookshelf_items"] = []
        fallback["reading_archive_integration"] = {}

        reconciled, changed, recovered = reconcile_bookshelf_payload(preferred, fallback)

        self.assertFalse(changed)
        self.assertEqual(recovered, 0)
        self.assertEqual(reconciled["bookshelf_items"], {"legacy": "keep"})
        self.assertEqual(reconciled["reading_archive_integration"], "legacy-state")

    def test_legacy_delete_metadata_never_removes_explicit_manual_item(self) -> None:
        preferred = self._legacy_bookshelf_payload(revision=35, deleted_ids=["same"])
        preferred["bookshelf_items"] = [
            {
                "type": "manual_album",
                "album_id": "same",
                "title": "手工相册",
                "pages": [],
            }
        ]

        reconciled, _changed, _recovered = reconcile_bookshelf_payload(
            preferred,
            self._legacy_bookshelf_payload(),
        )

        self.assertEqual(reconciled["bookshelf_items"], preferred["bookshelf_items"])

    def test_legacy_deleted_title_metadata_is_opaque(self) -> None:
        preferred = self._legacy_bookshelf_payload(revision=36)
        preferred["bookshelf_items"] = [
            {
                "key": "",
                "title": "旧书名",
                "pages": [],
                "source": "bookshelf_upgrade_recovered",
            }
        ]
        preferred["reading_archive_integration"] = {
            "deleted_titles": [" 旧书名 "]
        }

        reconciled, changed, _recovered = reconcile_bookshelf_payload(
            preferred,
            self._legacy_bookshelf_payload(),
        )

        self.assertTrue(changed)
        self.assertEqual(reconciled["bookshelf_items"], preferred["bookshelf_items"])

    def test_fallback_items_never_displace_eighty_authoritative_items(self) -> None:
        preferred = self._legacy_bookshelf_payload(
            *(f"p{index}" for index in range(80)),
            revision=40,
        )
        fallback = self._legacy_bookshelf_payload(
            *(f"old{index}" for index in range(5)),
            revision=40,
        )

        reconciled, _changed, recovered = reconcile_bookshelf_payload(preferred, fallback)

        self.assertEqual(recovered, 0)
        self.assertEqual(
            [item["album_id"] for item in reconciled["bookshelf_items"]],
            [f"p{index}" for index in range(80)],
        )


if __name__ == "__main__":
    unittest.main()
