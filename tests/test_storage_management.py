from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from astrbot_host.storage_location import (OWNER_NAME, PLUGIN_NAME, POINTER_NAME,
                                           prepare_storage_root, selected_backup_directory, selected_root)
from companion_v2.storage.backup import BACKUP_FORMAT, InstallationBackup, _policy_document
from companion_v2.storage.management import (cleanup_backups, cleanup_plan, copy_backup_snapshots,
                                             inventory, remove_transferred_backup_snapshots)
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def add_snapshot(root: Path, identifier: str, created_at: datetime, size: int) -> None:
    directory = root / "installations" / "main" / "runtime" / "backups"
    add_snapshot_to(directory, identifier, created_at, size)


def add_snapshot_to(directory: Path, identifier: str, created_at: datetime, size: int) -> None:
    """Write one complete bundle fixture: payload database plus its sidecar manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / f"companion-v2-{identifier}.bundle" / "payload" / "companion-v2.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"x" * size)
    (directory / f"companion-v2-{identifier}.json").write_text(json.dumps({
        "format": BACKUP_FORMAT, "snapshot_id": identifier,
        "created_at": created_at.isoformat(), "installation_id": "management-fixture",
        "schema_version": "companion-v2.storage.v1", "byte_size": size,
        "content_hash": "fixture", "note": "",
        "artifacts": [{"path": "companion-v2.sqlite3", "kind": "file", "bytes": size,
                       "sha256": hashlib.sha256(database.read_bytes()).hexdigest()}],
        "policy": _policy_document(),
    }), encoding="utf-8")


def bundle_path(directory: Path, identifier: str) -> Path:
    return directory / f"companion-v2-{identifier}.bundle"


class StorageManagementTests(unittest.TestCase):
    def test_usage_inventory_splits_database_backups_and_other_files(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            runtime = root / "profiles" / "main" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "companion-v2.sqlite3").write_bytes(b"a" * 10)
            (runtime / "companion-v2.sqlite3-wal").write_bytes(b"b" * 2)
            add_snapshot(root, "a" * 32, NOW, 5)
            (root / "misc.json").write_bytes(b"{}")
            backups = root / "installations" / "main" / "runtime" / "backups"
            sidecar_bytes = len((backups / ("companion-v2-" + "a" * 32 + ".json")).read_bytes())

            result = inventory(root)

            self.assertEqual(result["total_bytes"], 10 + 2 + 5 + sidecar_bytes + 2)
            self.assertEqual(result["categories"]["database"]["files"], 2)
            self.assertEqual(result["categories"]["backups"]["files"], 2)
            self.assertEqual(result["categories"]["other"]["files"], 1)
            self.assertIsNotNone(result["free_bytes"])

    def test_external_backup_directory_is_counted_and_managed(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary) / "runtime"
            external = Path(temporary) / "other-disk" / "snapshots"
            root.mkdir()
            add_snapshot_to(external, "a" * 32, NOW, 5)

            result = inventory(root, backup_directory=external)
            plan = cleanup_plan(root, retention_count=1, retention_days=90, now=NOW,
                                backup_directory=external)

            self.assertEqual(result["categories"]["backups"]["files"], 2)
            self.assertEqual(result["categories"]["backups"]["bytes"], result["total_bytes"])
            self.assertIsNotNone(result["backup_free_bytes"])
            self.assertEqual(plan["snapshot_count"], 1)

    def test_backup_directory_change_copies_verified_pairs_before_removing_source(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            source = base / "runtime" / "backups"
            destination = base / "external" / "snapshots"
            add_snapshot_to(source, "a" * 32, NOW, 5)

            transferred = copy_backup_snapshots(source, destination)

            self.assertEqual(len(transferred), 1)
            self.assertTrue(bundle_path(destination, "a" * 32).is_dir())
            self.assertTrue(bundle_path(source, "a" * 32).is_dir())
            self.assertEqual(remove_transferred_backup_snapshots(transferred, destination), 1)
            self.assertFalse(bundle_path(source, "a" * 32).exists())
            self.assertFalse((source / ("companion-v2-" + "a" * 32 + ".json")).exists())

    def test_backup_directory_must_be_absolute_and_separate(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary) / "runtime"
            self.assertEqual(selected_backup_directory(root, ""), root.resolve() / "backups")
            with self.assertRaises(ValueError):
                selected_backup_directory(root, "relative")
            with self.assertRaises(ValueError):
                selected_backup_directory(root, str(root / "nested"))

    def test_retention_protects_future_records_and_prefers_newly_created_tied_snapshot(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            previously_written, just_written, future = "a" * 32, "b" * 32, "c" * 32
            add_snapshot(root, previously_written, NOW, 16)
            add_snapshot(root, just_written, NOW, 20)
            add_snapshot(root, future, NOW + timedelta(days=2), 24)
            result = cleanup_backups(root, retention_count=1, retention_days=90, now=NOW,
                                     created_snapshot_id=just_written)
            self.assertEqual(result["snapshot_ids"], [previously_written])
            self.assertEqual(result["future_count"], 1)
            self.assertEqual(result["removed_count"], 1)
            self.assertFalse(result["failed"])
            plan = cleanup_plan(root, retention_count=1, retention_days=90, now=NOW)
            self.assertEqual(plan["snapshot_count"], 2)
            self.assertEqual(plan["remove_count"], 0)

    def test_retention_reports_failure_and_continues_cleaning_other_pairs(self):
        from companion_v2.storage.management import _remove_tree

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            newest, locked, removable = "a" * 32, "b" * 32, "c" * 32
            add_snapshot(root, newest, NOW, 16)
            add_snapshot(root, locked, NOW - timedelta(days=1), 20)
            add_snapshot(root, removable, NOW - timedelta(days=2), 24)

            def partial_remove(path):
                if locked not in path.name:
                    _remove_tree(path)

            with patch("companion_v2.storage.management._remove_tree", side_effect=partial_remove):
                result = cleanup_backups(root, retention_count=1, retention_days=90, now=NOW)
            self.assertEqual(result["remove_count"], 2)
            self.assertEqual(result["removed_count"], 1)
            self.assertEqual(result["failed_count"], 1)
            self.assertTrue(result["failed"])
            self.assertEqual(cleanup_plan(root, retention_count=1, retention_days=90, now=NOW)["snapshot_ids"],
                             [locked])

    def test_retention_combines_count_and_age_but_keeps_the_newest_snapshot(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            for index, age in enumerate((0, 2, 10, 40)):
                add_snapshot(root, f"{index + 1:032x}", NOW - timedelta(days=age), 100 + index)

            by_count = cleanup_plan(root, retention_count=2, retention_days=90, now=NOW)
            self.assertEqual(by_count["snapshot_count"], 4)
            self.assertEqual(by_count["remove_count"], 2)
            self.assertEqual(by_count["snapshot_ids"], [f"{3:032x}", f"{4:032x}"])

            by_age = cleanup_plan(root, retention_count=20, retention_days=5, now=NOW)
            self.assertEqual(by_age["snapshot_ids"], [f"{3:032x}", f"{4:032x}"])

            all_but_latest = cleanup_backups(root, retention_count=1, retention_days=1, now=NOW)
            self.assertEqual(all_but_latest["removed_count"], 3)
            self.assertTrue(bundle_path(root / "installations" / "main" / "runtime" / "backups",
                                        f"{1:032x}").is_dir())
            self.assertFalse(bundle_path(root / "installations" / "main" / "runtime" / "backups",
                                         f"{2:032x}").exists())

    def test_external_location_migrates_and_can_be_returned_to_default(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            default = base / "astrbot-data" / PLUGIN_NAME
            default.mkdir(parents=True)
            (default / "profiles" / "main" / "runtime").mkdir(parents=True)
            (default / "profiles" / "main" / "runtime" / "companion-v2.sqlite3").write_bytes(b"database")
            (default / "config.json").write_text("{}", encoding="utf-8")
            selected = base / "D-drive" / "AstrBotData"
            external = selected / PLUGIN_NAME

            actual = prepare_storage_root(default, {"storage": {"path": str(selected)}})

            self.assertEqual(actual, external.resolve())
            self.assertEqual((external / "config.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual((external / "profiles" / "main" / "runtime" /
                              "companion-v2.sqlite3").read_bytes(), b"database")
            self.assertTrue((external / OWNER_NAME).is_file())
            self.assertEqual([path.name for path in default.iterdir()], [POINTER_NAME])
            self.assertEqual(prepare_storage_root(default, {"storage": {"path": str(selected)}}), external)

            restored = prepare_storage_root(default, {"storage": {"path": ""}})

            self.assertEqual(restored, default.resolve())
            self.assertEqual((default / "config.json").read_text(encoding="utf-8"), "{}")
            self.assertFalse((default / POINTER_NAME).exists())
            self.assertFalse(external.exists())

    def test_conflicting_destination_does_not_touch_current_data(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            default = base / "astrbot-data" / PLUGIN_NAME
            default.mkdir(parents=True)
            source = default / "keep.json"
            source.write_text("keep", encoding="utf-8")
            selected = base / "other"
            destination = selected / PLUGIN_NAME
            destination.mkdir(parents=True)
            (destination / "unrelated.txt").write_text("untouched", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "already contains files"):
                prepare_storage_root(default, {"storage": {"path": str(selected)}})

            self.assertEqual(source.read_text(encoding="utf-8"), "keep")
            self.assertEqual((destination / "unrelated.txt").read_text(encoding="utf-8"), "untouched")

    def test_restart_finishes_cleanup_after_pointer_switch_was_interrupted(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            default = base / "astrbot-data" / PLUGIN_NAME
            external = base / "D-drive" / PLUGIN_NAME
            external.mkdir(parents=True)
            (external / OWNER_NAME).write_text(json.dumps({
                "format": "companion-v2.storage-location.v1", "source": str(default), "id": "test",
            }), encoding="utf-8")
            (external / "owned.json").write_text("external", encoding="utf-8")
            default.mkdir(parents=True)
            (default / "stale-copy.json").write_text("stale", encoding="utf-8")
            (default / POINTER_NAME).write_text(json.dumps({
                "format": "companion-v2.storage-location.v1", "path": str(external),
                "cleanup_source": str(default),
            }), encoding="utf-8")

            actual = prepare_storage_root(default, {"storage": {"path": str(external.parent)}})

            self.assertEqual(actual, external.resolve())
            self.assertFalse((default / "stale-copy.json").exists())
            self.assertEqual((default / POINTER_NAME).read_text(encoding="utf-8"),
                             json.dumps({"format": "companion-v2.storage-location.v1",
                                         "path": str(external.resolve())}, sort_keys=True))

    def test_cleanup_failure_after_a_successful_move_never_blocks_loading(self):
        """搬迁已完成时，旧根清理失败（深路径 / 权限）只能记日志，不能再抛到加载期。"""
        from unittest.mock import patch
        import shutil
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            default = base / "astrbot-data" / PLUGIN_NAME
            (default / "legacy-v1-archives" / "deep").mkdir(parents=True)
            (default / "legacy-v1-archives" / "deep" / "export.json").write_text("deep", encoding="utf-8")
            (default / "config.json").write_text("{}", encoding="utf-8")
            selected = base / "D-drive" / "AstrBotData"
            external = selected / PLUGIN_NAME
            real_rmtree = shutil.rmtree

            def flaky_rmtree(path, *args, **kwargs):
                if "legacy-v1-archives" in str(path):
                    raise FileNotFoundError(3, "系统找不到指定的路径", str(path))
                return real_rmtree(path, *args, **kwargs)

            with patch("astrbot_host.storage_location.shutil.rmtree", side_effect=flaky_rmtree):
                actual = prepare_storage_root(default, {"storage": {"path": str(selected)}})

            self.assertEqual(actual, external.resolve())
            self.assertEqual((external / "config.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual((external / "legacy-v1-archives" / "deep" / "export.json").read_text(encoding="utf-8"),
                             "deep")
            pointer = json.loads((default / POINTER_NAME).read_text(encoding="utf-8"))
            self.assertEqual(pointer["path"], str(external.resolve()))

    def test_storage_target_must_be_absolute_and_separate(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            default = Path(temporary) / PLUGIN_NAME
            with self.assertRaises(ValueError):
                selected_root(default, "relative")
            with self.assertRaises(ValueError):
                selected_root(default, str(default / "nested"))

    def test_legacy_pairs_are_still_copied_and_removed_as_one_unit(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            base = Path(temporary)
            source = base / "runtime" / "backups"
            destination = base / "external" / "snapshots"
            source.mkdir(parents=True)
            identifier = "b" * 32
            database = source / f"companion-v2-{identifier}.sqlite3"
            database.write_bytes(b"legacy-database")
            (source / f"companion-v2-{identifier}.json").write_text(json.dumps({
                "format": "companion-v2.installation-backup.v1", "snapshot_id": identifier,
                "created_at": NOW.isoformat(), "installation_id": "management-fixture",
                "schema_version": "companion-v2.storage.v1", "content_hash": "fixture",
            }), encoding="utf-8")

            transferred = copy_backup_snapshots(source, destination)

            self.assertEqual(len(transferred), 1)
            self.assertEqual((destination / f"companion-v2-{identifier}.sqlite3").read_bytes(),
                             b"legacy-database")
            self.assertEqual(remove_transferred_backup_snapshots(transferred, destination), 1)
            self.assertFalse(database.exists())
            self.assertFalse((source / f"companion-v2-{identifier}.json").exists())

    def test_new_snapshot_applies_saved_retention_policy(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary) / "runtime"
            runtime = StorageRuntime.open(data_root=root, installation_id="storage-retention-test")
            try:
                clock = [NOW]
                backup = InstallationBackup(runtime, now=lambda: clock[0],
                                            retention_count=1, retention_days=90)
                backup.create(note="first").unwrap()
                clock[0] += timedelta(minutes=1)
                backup.create(note="second").unwrap()
                self.assertEqual(len(list(backup.directory.glob("companion-v2-*.bundle"))), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
