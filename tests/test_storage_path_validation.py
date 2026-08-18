# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.core_store import CoreStoreMixin


class _StorePathHost(CoreStoreMixin):
    def __init__(self, root: Path, configured_path: str) -> None:
        self.data_dir = str(root)
        self.data_file = str(root / "companions.json")
        self.storage_backend = "sqlite"
        self.storage_sqlite_path = configured_path

    @staticmethod
    def _ensure_store_defaults(data: dict) -> dict:
        return data

    @staticmethod
    def _new_store() -> dict:
        return {"users": {}, "groups": {}}


class StoragePathValidationTests(unittest.TestCase):
    def test_directory_configuration_falls_back_to_default_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configured = root / "configured-directory"
            configured.mkdir()
            host = _StorePathHost(root, str(configured))

            host._rebuild_store_manager()

            self.assertEqual(str(root / "companions.db"), host.storage_sqlite_effective_path)
            self.assertEqual(root / "companions.db", host.store_manager.sqlite_backend.db_path)

    def test_uncreatable_parent_falls_back_without_opening_directory_as_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent_file = root / "parent-file"
            parent_file.write_text("not a directory", encoding="utf-8")
            host = _StorePathHost(root, str(parent_file / "companions.db"))

            host._rebuild_store_manager()

            self.assertEqual(str(root / "companions.db"), host.storage_sqlite_effective_path)


if __name__ == "__main__":
    unittest.main()
