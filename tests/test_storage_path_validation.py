# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.storage.json_backend import JsonStoreBackend
from astrbot_plugin_private_companion.storage.sqlite_backend import SqliteStoreBackend
from astrbot_plugin_private_companion.storage.store_manager import StoreManager


class _StorePathHost(CoreStoreMixin):
    def __init__(self, root: Path, configured_path: str) -> None:
        self.data_dir = str(root)
        self.data_file = str(root / "companions.json")
        self.storage_backend = "sqlite"
        self.storage_sqlite_path = configured_path
        self.storage_sqlite_effective_path = ""
        self.data = {"users": {}, "groups": {}}

    @staticmethod
    def _ensure_store_defaults(data: dict) -> dict:
        return data

    @staticmethod
    def _new_store() -> dict:
        return {"users": {}, "groups": {}}

    def manager(self, backend: str, sqlite_path: Path) -> StoreManager:
        return StoreManager(
            backend_name=backend,
            data_file=self.data_file,
            sqlite_path=sqlite_path,
            ensure_defaults=self._ensure_store_defaults,
            new_store=self._new_store,
        )

    def install_manager(
        self,
        backend: str,
        sqlite_path: Path,
        data: dict,
    ) -> StoreManager:
        manager = self.manager(backend, sqlite_path)
        manager.backend.save_store(deepcopy(data))
        self.store_manager = manager
        self.storage_backend = backend
        self.storage_sqlite_path = str(sqlite_path) if backend == "sqlite" else ""
        self.storage_sqlite_effective_path = str(
            sqlite_path if backend == "sqlite" else self.data_file
        )
        self._storage_backend_applied = backend
        self._storage_sqlite_path_applied = self.storage_sqlite_path
        self.data = deepcopy(data)
        return manager


class StoragePathValidationTests(unittest.TestCase):
    def test_missing_primary_stores_with_backend_marker_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            host = _StorePathHost(root, "")
            manager = host.manager("sqlite", root / "companions.db")
            host._write_storage_backend_state("sqlite", str(manager.sqlite_path))

            with self.assertRaisesRegex(RuntimeError, "既有安装证据"):
                host._assert_primary_store_startup_safe(manager)

    def test_missing_primary_stores_with_plugin_artifact_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            host = _StorePathHost(root, "")
            manager = host.manager("json", root / "companions.db")
            (root / "req041_relationship.db").touch()

            with self.assertRaisesRegex(RuntimeError, "req041_relationship.db"):
                host._assert_primary_store_startup_safe(manager)

    def test_empty_data_directory_remains_valid_first_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            host = _StorePathHost(root, "")
            manager = host.manager("sqlite", root / "companions.db")

            host._assert_primary_store_startup_safe(manager)
            self.assertFalse(manager.json_backend.exists())
            self.assertFalse(manager.sqlite_backend.exists())

    def test_primary_json_store_keeps_only_restart_tail_with_store_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            host = _StorePathHost(root, "")
            host.storage_backend = "json"
            host.store_manager = host.manager("json", root / "unused.db")
            host._data_save_revision = 0
            host._data_default = {
                "groups": {
                    "room": {
                        "recent_messages": [
                            {"sender_id": "user", "text": str(index)}
                            for index in range(15)
                        ],
                        "recent_bot_replies": [
                            {"sender_id": "bot", "text": str(index)}
                            for index in range(15)
                        ],
                    }
                }
            }
            host.data = host._data_default

            host._write_data_snapshot_sync(deepcopy(host.data))
            stored = host.store_manager.backend.load_store()

            self.assertEqual(12, len(stored["groups"]["room"]["recent_messages"]))
            self.assertEqual(12, len(stored["groups"]["room"]["recent_bot_replies"]))
            self.assertEqual(15, len(host.data["groups"]["room"]["recent_messages"]))

    def test_startup_imports_newer_legacy_json_into_existing_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sqlite_path = root / "companions.db"
            old_sqlite = {"users": {"owner": {"name": "august-4"}}, "groups": {}}
            new_json = {
                "users": {"owner": {"name": "august-22"}},
                "groups": {"room": {"name": "new"}},
            }
            JsonStoreBackend(root / "companions.json", lambda data: data, lambda: {}).save_store(new_json)
            StoreManager(
                backend_name="sqlite",
                data_file=root / "companions.json",
                sqlite_path=sqlite_path,
                ensure_defaults=lambda data: data,
                new_store=lambda: {},
            ).backend.save_store(old_sqlite)
            os.utime(root / "companions.json", (sqlite_path.stat().st_mtime + 2,)*2)

            host = _StorePathHost(root, str(sqlite_path))
            host._rebuild_store_manager()

            self.assertEqual(new_json, host.store_manager.backend.load_store())

    def test_startup_honors_persisted_json_backend_switch_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sqlite_path = root / "companions.db"
            old_sqlite = {"users": {"owner": {"name": "old"}}, "groups": {}}
            new_json = {"users": {"owner": {"name": "json"}}, "groups": {}}
            manager = StoreManager(
                backend_name="sqlite",
                data_file=root / "companions.json",
                sqlite_path=sqlite_path,
                ensure_defaults=lambda data: data,
                new_store=lambda: {},
            )
            manager.backend.save_store(old_sqlite)
            (root / "companions.json").write_text(json.dumps(new_json), encoding="utf-8")
            host = _StorePathHost(root, str(sqlite_path))
            host._write_storage_backend_state("json", "")

            host._rebuild_store_manager()

            self.assertEqual(new_json, host.store_manager.backend.load_store())

    def test_directory_configuration_falls_back_to_default_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configured = root / "configured-directory"
            configured.mkdir()
            host = _StorePathHost(root, str(configured))

            host._rebuild_store_manager()

            self.assertEqual(
                str(root / "companions.db"), host.storage_sqlite_effective_path
            )
            self.assertEqual(
                root / "companions.db", host.store_manager.sqlite_backend.db_path
            )

    def test_uncreatable_parent_falls_back_without_opening_directory_as_database(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent_file = root / "parent-file"
            parent_file.write_text("not a directory", encoding="utf-8")
            host = _StorePathHost(root, str(parent_file / "companions.db"))

            host._rebuild_store_manager()

            self.assertEqual(
                str(root / "companions.db"), host.storage_sqlite_effective_path
            )

    def test_switch_from_sqlite_authority_overwrites_stale_json_and_backs_it_up(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sqlite_path = root / "authority.db"
            stale = {"users": {"owner": {"name": "stale-json"}}, "groups": {}}
            authoritative = {
                "users": {"owner": {"name": "sqlite-authority"}},
                "groups": {"room": {"name": "kept"}},
            }
            json_path = root / "companions.json"
            json_path.write_text(
                json.dumps(stale, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            stale_bytes = json_path.read_bytes()
            host = _StorePathHost(root, str(sqlite_path))
            old_manager = host.install_manager("sqlite", sqlite_path, authoritative)

            host.storage_backend = "json"
            host.storage_sqlite_path = ""
            host._rebuild_store_manager(reload_data=True)

            self.assertIsNot(old_manager, host.store_manager)
            self.assertEqual(authoritative, host.data)
            self.assertEqual(
                authoritative, json.loads(json_path.read_text(encoding="utf-8"))
            )
            backups = sorted(root.glob("companions.json.before-switch-*.bak"))
            self.assertEqual(1, len(backups))
            self.assertEqual(stale_bytes, backups[0].read_bytes())

    def test_switch_between_sqlite_files_copies_authority_and_backs_up_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "source.db"
            target_path = root / "target.db"
            source_data = {"users": {"owner": {"name": "source"}}, "groups": {}}
            target_data = {"users": {"owner": {"name": "target-old"}}, "groups": {}}
            host = _StorePathHost(root, str(source_path))
            host.install_manager("sqlite", source_path, source_data)
            target_manager = host.manager("sqlite", target_path)
            target_manager.backend.save_store(target_data)
            target_before = SqliteStoreBackend(
                target_path, host._ensure_store_defaults, host._new_store
            ).load_store()

            host.storage_backend = "sqlite"
            host.storage_sqlite_path = str(target_path)
            host._rebuild_store_manager(reload_data=True)

            self.assertEqual(source_data, host.data)
            self.assertEqual(source_data, host.store_manager.backend.load_store())
            self.assertEqual(1, len(list(root.glob("target.db.before-switch-*.bak"))))
            backup_path = next(root.glob("target.db.before-switch-*.bak"))
            self.assertEqual(
                target_before,
                SqliteStoreBackend(
                    backup_path, host._ensure_store_defaults, host._new_store
                ).load_store(),
            )

    def test_failed_target_write_restores_target_and_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "source.db"
            target_path = root / "companions.json"
            source_data = {"users": {"owner": {"name": "source"}}, "groups": {}}
            target_path.write_text(
                '{"users":{"owner":{"name":"old"}}}', encoding="utf-8"
            )
            original_target = target_path.read_bytes()
            host = _StorePathHost(root, str(source_path))
            old_manager = host.install_manager("sqlite", source_path, source_data)
            host.storage_backend = "json"
            host.storage_sqlite_path = str(root / "attempted.db")

            with (
                patch.object(
                    JsonStoreBackend, "save_store", side_effect=OSError("write failed")
                ),
                self.assertRaises(OSError),
            ):
                host._rebuild_store_manager(reload_data=True)

            self.assertIs(old_manager, host.store_manager)
            self.assertEqual(source_data, host.data)
            self.assertEqual("sqlite", host.storage_backend)
            self.assertEqual(str(source_path), host.storage_sqlite_path)
            self.assertEqual(str(source_path), host.storage_sqlite_effective_path)
            self.assertEqual(original_target, target_path.read_bytes())

    def test_failed_target_readback_restores_target_and_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "source.db"
            target_path = root / "companions.json"
            source_data = {"users": {"owner": {"name": "source"}}, "groups": {}}
            target_path.write_text(
                '{"users":{"owner":{"name":"old"}}}', encoding="utf-8"
            )
            original_target = target_path.read_bytes()
            host = _StorePathHost(root, str(source_path))
            old_manager = host.install_manager("sqlite", source_path, source_data)
            host.storage_backend = "json"
            host.storage_sqlite_path = str(root / "attempted.db")
            original_load_store = JsonStoreBackend.load_store
            target_reads = 0

            def fail_target_readback(backend: JsonStoreBackend) -> dict:
                nonlocal target_reads
                if backend.data_file == target_path:
                    target_reads += 1
                    if target_reads > 1:
                        raise OSError("read failed")
                return original_load_store(backend)

            with (
                patch.object(
                    JsonStoreBackend,
                    "load_store",
                    autospec=True,
                    side_effect=fail_target_readback,
                ),
                self.assertRaises(OSError),
            ):
                host._rebuild_store_manager(reload_data=True)

            self.assertIs(old_manager, host.store_manager)
            self.assertEqual(source_data, host.data)
            self.assertEqual("sqlite", host.storage_backend)
            self.assertEqual(str(source_path), host.storage_sqlite_path)
            self.assertEqual(str(source_path), host.storage_sqlite_effective_path)
            self.assertEqual(original_target, target_path.read_bytes())

    def test_non_object_target_readback_is_rejected_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "source.db"
            target_path = root / "companions.json"
            source_data = {"users": {"owner": {"name": "source"}}, "groups": {}}
            target_path.write_text(
                '{"users":{"owner":{"name":"old"}}}', encoding="utf-8"
            )
            original_target = target_path.read_bytes()
            host = _StorePathHost(root, str(source_path))
            old_manager = host.install_manager("sqlite", source_path, source_data)
            host.storage_backend = "json"
            host.storage_sqlite_path = str(root / "attempted.db")
            original_load_store = JsonStoreBackend.load_store
            target_reads = 0

            def return_non_object_on_readback(backend: JsonStoreBackend) -> object:
                nonlocal target_reads
                if backend.data_file == target_path:
                    target_reads += 1
                    if target_reads > 1:
                        return []
                return original_load_store(backend)

            with (
                patch.object(
                    JsonStoreBackend,
                    "load_store",
                    autospec=True,
                    side_effect=return_non_object_on_readback,
                ),
                self.assertRaisesRegex(RuntimeError, "non-object store"),
            ):
                host._rebuild_store_manager(reload_data=True)

            self.assertIs(old_manager, host.store_manager)
            self.assertEqual(source_data, host.data)
            self.assertEqual("sqlite", host.storage_backend)
            self.assertEqual(str(source_path), host.storage_sqlite_path)
            self.assertEqual(original_target, target_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
