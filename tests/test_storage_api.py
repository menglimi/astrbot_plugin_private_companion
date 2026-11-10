from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.storage_api import PREFIX, StorageWebAPI
from companion_v2.storage.backup import BACKUP_FORMAT, InstallationBackup, LEGACY_BACKUP_FORMAT


def _write_v1_snapshot(directory, identifier, created_at, installation_id):
    """Write a real 7.0.7 snapshot: one flat ``.sqlite3`` database plus its sidecar.

    The management paths under test only inspect the pair layout, but a fixture that
    claims the legacy format should still look like what 7.0.7 wrote: a restorable
    SQLite database carrying the installation identity, a matching byte size and a
    real ``content_hash``. Writing arbitrary bytes under a v1 label would let a
    digest/schema regression pass unnoticed.
    """
    from companion_v2.storage.schema import SCHEMA_VERSION

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / f"companion-v2-{identifier}.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE runtime_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany("INSERT INTO runtime_metadata (key, value) VALUES (?, ?)",
                               (("installation_id", installation_id), ("schema_version", SCHEMA_VERSION)))
        connection.commit()
    finally:
        connection.close()
    sidecar = directory / f"companion-v2-{identifier}.json"
    sidecar.write_text(json.dumps({
        "format": LEGACY_BACKUP_FORMAT, "snapshot_id": identifier,
        "created_at": created_at.isoformat(), "installation_id": installation_id,
        "schema_version": SCHEMA_VERSION, "byte_size": database.stat().st_size,
        "content_hash": hashlib.sha256(database.read_bytes()).hexdigest(), "note": "",
    }), encoding="utf-8")
    return database


def _create_v2_bundle(source_root, directory, installation_id):
    """Create one real, minimal v2 bundle with the product writer.

    The bundle is written directly into the backup directory (nothing is copied into
    the fixture) and the open runtime is returned so the test can ask the product to
    verify the same bytes again after a backup-directory migration.
    """
    from companion_v2.storage.runtime import StorageRuntime

    runtime = StorageRuntime.open(data_root=Path(source_root), installation_id=installation_id)
    try:
        manifest = InstallationBackup(
            runtime, now=lambda: datetime(2026, 10, 7, 12, tzinfo=timezone.utc),
            directory=Path(directory)).create(note="storage-api fixture").unwrap()
    except BaseException:
        runtime.close()
        raise
    return runtime, manifest


class Request:
    username = "dashboard-admin"
    query = {}
    payload = b""

    async def body(self):
        return self.payload


class StorageApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-v2-storage-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.default_root = self.root / "plugin-data" / "astrbot_plugin_private_companion"
        self.default_root.mkdir(parents=True)
        self.installation_id = "installation-main"
        self.profile_root = self.default_root / "installations" / "main"
        self.runtime_root = self.profile_root / "runtime"
        self.config = {"storage": {"path": "", "backup_path": "", "retention_count": 10, "retention_days": 90}}
        self.controller = NS(data_root=self.default_root, default_data_root=self.default_root,
            config=self.config, runtime=None, _control_lock=asyncio.Lock(),
            profile_root=self.profile_root, settings=NS(installation_id=self.installation_id),
            save_plugin_config=self.save, now=lambda: datetime.now(timezone.utc))
        self.api = StorageWebAPI(self.controller)
        self.request = Request()

    def save(self, candidate):
        self.config.clear()
        self.config.update(candidate)

    async def test_cache_action_clears_only_regenerable_caches(self):
        cleared = []

        class Application:
            def clear_caches(self):
                cleared.append(True)
                return {"image_narration": 2, "diagnostics": 1}

        self.controller.session = NS(application=Application())
        self.request.payload = json.dumps({"action": "cache"}).encode("utf-8")
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["cache_result"],
                         {"image_narration": 2, "diagnostics": 1})
        self.assertEqual(cleared, [True])

    async def test_cache_action_rejects_storage_fields(self):
        self.controller.session = NS(application=NS(clear_caches=lambda: {}))
        self.request.payload = json.dumps({"action": "cache", "path": "D:\\elsewhere"}).encode("utf-8")
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()
        self.assertEqual(response["status"], "error")

    async def test_status_reports_current_usage_and_policy(self):
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.status()
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["active_root"], str(self.default_root.resolve()))
        self.assertEqual(response["data"]["policy"], {"retention_count": 10, "retention_days": 90})
        self.assertEqual({method for route, _, methods, _ in self.api.routes()
                          if route == PREFIX for method in methods}, {"GET", "POST"})

    async def test_save_persists_external_path_and_bounded_policy(self):
        selected = self.root / "D-drive" / "AstrBotData"
        backup = self.root / "E-drive" / "Snapshots"
        self.request.payload = json.dumps({"action": "save", "path": str(selected),
            "backup_path": str(backup), "retention_count": 5, "retention_days": 30}).encode()
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()
        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["data"]["restart_required"])
        self.assertEqual(self.config["storage"]["path"], str(selected))
        self.assertEqual(self.config["storage"]["backup_path"], str(backup))
        self.assertEqual(self.config["storage"]["retention_count"], 5)

    async def test_save_preserves_host_configuration_runtime_locks(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        config_path = self.root / "host-config.json"
        self.config = AstrBotConfig(str(config_path), default_config=self.config)
        self.controller.config = self.config

        def save(candidate):
            self.config.clear()
            self.config.update(candidate)
            self.config.save_config()

        self.controller.save_plugin_config = save
        selected = self.root / "external-runtime"
        self.request.payload = json.dumps({"action": "save", "path": str(selected),
            "backup_path": "", "retention_count": 10, "retention_days": 90}).encode()
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()

        self.assertEqual(response["status"], "ok", response)
        self.assertTrue(response["data"]["restart_required"])
        saved = json.loads(config_path.read_text(encoding="utf-8-sig"))
        self.assertEqual(saved["storage"]["path"], str(selected))
        self.assertIs(self.controller.config, self.config)

    async def test_invalid_path_does_not_change_configuration(self):
        self.request.payload = json.dumps({"action": "save", "path": "relative",
            "backup_path": "", "retention_count": 5, "retention_days": 30}).encode()
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()
        self.assertEqual(response["status"], "error")
        self.assertEqual(self.config["storage"]["path"], "")

    async def test_confirmed_cleanup_removes_only_snapshots_outside_policy(self):
        backup_dir = self.runtime_root / "backups"
        ids = [f"{index:032x}" for index in (1, 2)]
        for index, identifier in enumerate(ids):
            _write_v1_snapshot(backup_dir, identifier,
                               datetime(2026, 10, 7, index, tzinfo=timezone.utc),
                               self.installation_id)
        self.config["storage"]["retention_count"] = 1
        self.request.payload = b'{"action":"clean"}'

        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["cleanup_result"]["removed_count"], 1)
        self.assertTrue((backup_dir / f"companion-v2-{ids[1]}.sqlite3").is_file())
        self.assertFalse((backup_dir / f"companion-v2-{ids[0]}.sqlite3").exists())

    async def test_saving_backup_path_moves_existing_snapshots_and_updates_inventory(self):
        old = self.runtime_root / "backups"
        new = self.root / "external-snapshots"
        identifier = "1" * 32
        database = _write_v1_snapshot(old, identifier,
                                      datetime(2026, 10, 7, tzinfo=timezone.utc),
                                      self.installation_id)
        self.request.payload = json.dumps({"action": "save", "path": "", "backup_path": str(new),
            "retention_count": 10, "retention_days": 90}).encode()

        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(self.config["storage"]["backup_path"], str(new))
        installation_key = hashlib.sha256(self.installation_id.encode("utf-8")).hexdigest()
        destination = new / "installations" / installation_key / "backups"
        self.assertTrue((destination / database.name).is_file())
        self.assertFalse(database.exists())
        self.assertEqual(response["data"]["inventory"]["categories"]["backups"]["files"], 2)

    async def test_v2_bundle_inventory_and_backup_path_migration_keep_the_payload(self):
        local = self.runtime_root / "backups"
        runtime, manifest = _create_v2_bundle(self.root / "v2-bundle-source", local,
                                              self.installation_id)
        self.addCleanup(runtime.close)
        snapshot_id = manifest["snapshot_id"]
        self.assertEqual(manifest["format"], BACKUP_FORMAT)
        bundle = local / f"companion-v2-{snapshot_id}.bundle"
        sidecar = local / f"companion-v2-{snapshot_id}.json"
        self.assertTrue((bundle / "payload" / "companion-v2.sqlite3").is_file())
        self.assertTrue(sidecar.is_file())
        # A bundle has one file per declared artifact plus its sidecar; deriving the
        # expected inventory count keeps this honest if the minimal runtime grows.
        expected_files = len(manifest["artifacts"]) + 1

        with patch("astrbot_host.storage_api.request", self.request):
            status = await self.api.status()
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["data"]["inventory"]["categories"]["backups"]["files"], expected_files)
        self.assertEqual(status["data"]["cleanup_preview"]["snapshot_count"], 1)
        self.assertEqual(status["data"]["cleanup_preview"]["incomplete_count"], 0)

        external = self.root / "external-snapshots"
        self.request.payload = json.dumps({"action": "save", "path": "", "backup_path": str(external),
            "retention_count": 10, "retention_days": 90}).encode()
        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()

        self.assertEqual(response["status"], "ok")
        installation_key = hashlib.sha256(self.installation_id.encode("utf-8")).hexdigest()
        destination = external / "installations" / installation_key / "backups"
        moved_bundle = destination / bundle.name
        self.assertTrue((moved_bundle / "payload" / "companion-v2.sqlite3").is_file())
        self.assertTrue((destination / sidecar.name).is_file())
        self.assertFalse(bundle.exists())
        self.assertFalse(sidecar.exists())
        self.assertEqual(response["data"]["inventory"]["categories"]["backups"]["files"], expected_files)
        self.assertEqual(response["data"]["cleanup_preview"]["snapshot_count"], 1)

        # The moved bytes must still be a complete v2 bundle, not merely present.
        inspected = InstallationBackup(runtime, directory=destination).inspect(snapshot_id=snapshot_id)
        self.assertTrue(inspected.is_success, inspected.failure)
        verified = inspected.unwrap()
        self.assertEqual(verified["manifest"]["content_hash"], manifest["content_hash"])
        self.assertEqual(verified["restore_scope"], "complete_installation")
        self.assertTrue(verified["restorable"])

    async def test_external_backup_inventory_and_cleanup_are_isolated_by_installation(self):
        external = self.root / "shared-snapshots"
        active_key = hashlib.sha256(self.installation_id.encode("utf-8")).hexdigest()
        other_id = "installation-other"
        other_key = hashlib.sha256(other_id.encode("utf-8")).hexdigest()
        active = external / "installations" / active_key / "backups"
        other = external / "installations" / other_key / "backups"
        active_ids = ["2" * 32, "3" * 32]
        for index, identifier in enumerate(active_ids):
            _write_v1_snapshot(active, identifier,
                               datetime(2026, 10, 7, index, tzinfo=timezone.utc),
                               self.installation_id)
        other_id_snapshot = "4" * 32
        _write_v1_snapshot(other, other_id_snapshot,
                           datetime(2026, 10, 7, tzinfo=timezone.utc), other_id)
        self.config["storage"].update(backup_path=str(external), retention_count=1)

        with patch("astrbot_host.storage_api.request", self.request):
            status = await self.api.status()
            self.request.payload = b'{"action":"clean"}'
            response = await self.api.update()

        self.assertEqual(status["data"]["inventory"]["categories"]["backups"]["files"], 6)
        self.assertEqual(status["data"]["cleanup_preview"]["snapshot_count"], 2)
        self.assertEqual(response["data"]["cleanup_result"]["removed_count"], 1)
        self.assertFalse((active / f"companion-v2-{active_ids[0]}.sqlite3").exists())
        self.assertTrue((active / f"companion-v2-{active_ids[1]}.sqlite3").is_file())
        self.assertTrue((other / f"companion-v2-{other_id_snapshot}.sqlite3").is_file())

    async def test_failed_config_save_keeps_original_backup_pairs(self):
        old = self.runtime_root / "backups"
        identifier = "5" * 32
        database = _write_v1_snapshot(old, identifier,
                                      datetime(2026, 10, 7, tzinfo=timezone.utc),
                                      self.installation_id)
        new = self.root / "external-snapshots"
        self.controller.save_plugin_config = lambda _: (_ for _ in ()).throw(OSError("write failed"))
        self.request.payload = json.dumps({"action": "save", "path": "", "backup_path": str(new),
            "retention_count": 10, "retention_days": 90}).encode()

        with patch("astrbot_host.storage_api.request", self.request):
            response = await self.api.update()

        self.assertEqual(response["status"], "error")
        self.assertTrue(database.is_file())
        self.assertEqual(self.config["storage"]["backup_path"], "")


if __name__ == "__main__":
    unittest.main()
