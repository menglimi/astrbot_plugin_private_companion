"""Automatic backup scheduling over real snapshots and the host lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_host.automatic_backup import AutomaticBackup
from astrbot_host.backup_api import backup_operation_lock
from astrbot_host.storage_api import storage_request_lock
from companion_v2.storage.backup import InstallationBackup
from companion_v2.storage.runtime import StorageRuntime


class AutomaticBackupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.runtime = StorageRuntime.open(data_root=root / "runtime", installation_id="auto-backup-test")
        self.addCleanup(self.runtime.close)
        self.runtime.backup_directory = root / "selected-backups"
        self.at = datetime(2026, 11, 18, tzinfo=timezone.utc)
        self.settings = {"auto_backup_enabled": True, "auto_backup_interval_minutes": 1,
                         "retention_count": 2, "retention_days": 90}
        self.controller = SimpleNamespace(runtime=self.runtime, _control_lock=asyncio.Lock(),
                                          _terminated=False, config={"storage": self.settings},
                                          now=lambda: self.at)
        self.scheduler = AutomaticBackup(self.controller)
        self.backup = InstallationBackup(self.runtime, now=self.controller.now)

    def snapshots(self):
        return self.backup.listing().unwrap()["snapshots"]

    def advance(self, minutes):
        self.at += timedelta(minutes=minutes)

    async def test_creates_restorable_snapshot_then_rotates_by_count(self):
        with self.runtime.transaction() as db:
            db.execute("INSERT INTO runtime_metadata(key, value) VALUES ('scheduled', 'yes')")
        await self.scheduler._tick()
        first, = self.snapshots()
        self.assertEqual(first["note"], "自动滚动备份")
        self.assertTrue(self.backup.preview_restore(snapshot_id=first["snapshot_id"]).is_success)
        await self.scheduler._tick()
        self.assertEqual(len(self.snapshots()), 1)
        for _ in range(2):
            self.advance(1)
            await self.scheduler._tick()
        self.assertEqual(len(self.snapshots()), 2)
        self.assertNotIn(first["snapshot_id"], {item["snapshot_id"] for item in self.snapshots()})

    async def test_restart_uses_manual_snapshot_and_catches_up_only_once(self):
        manual = self.backup.create(note="manual").unwrap()
        await self.scheduler._tick()
        self.assertEqual([item["snapshot_id"] for item in self.snapshots()], [manual["snapshot_id"]])
        self.advance(1440 * 2)
        self.settings["retention_days"] = 1
        # A new scheduler has no in-memory history; the backup directory is authoritative.
        restarted = AutomaticBackup(self.controller)
        await restarted._tick()
        await AutomaticBackup(self.controller)._tick()
        latest, = self.snapshots()
        self.assertEqual(latest["note"], "自动滚动备份")
        self.assertNotEqual(latest["snapshot_id"], manual["snapshot_id"])

    async def test_disabled_unloaded_busy_and_pending_restore_do_not_write(self):
        self.settings["auto_backup_enabled"] = False
        await self.scheduler._tick()
        self.settings["auto_backup_enabled"] = True
        self.controller.runtime = None
        await self.scheduler._tick()
        self.controller.runtime = self.runtime
        async with storage_request_lock(self.controller):
            await self.scheduler._tick()
        with backup_operation_lock(self.controller):
            await self.scheduler._tick()
        self.assertEqual(self.snapshots(), [])
        manual = self.backup.create().unwrap()
        self.assertTrue(self.backup.restore(snapshot_id=manual["snapshot_id"],
                                            confirmed_hash=manual["content_hash"]).is_success)
        before = self.snapshots()  # Staging a restore also creates a safety snapshot.
        self.assertTrue(self.backup.pending_path.is_file())
        self.advance(1)
        await self.scheduler._tick()
        self.assertEqual(self.snapshots(), before)

    async def test_failed_write_does_not_rotate_and_backs_off(self):
        self.backup.create().unwrap()
        self.advance(1)
        self.settings["retention_count"] = 1
        with patch.object(InstallationBackup, "_create", side_effect=OSError("test write failure")) as create:
            with self.assertLogs("astrbot_host.automatic_backup", level="WARNING"):
                await self.scheduler._tick()
                await self.scheduler._tick()
            create.assert_called_once()
        self.assertEqual(len(self.snapshots()), 1)
        self.scheduler._retry_at = 0
        await self.scheduler._tick()
        self.assertEqual(self.snapshots()[0]["note"], "自动滚动备份")

    async def test_rechecks_cached_schedule_after_clock_rolls_back_and_keeps_new_backup(self):
        self.settings["retention_count"] = 1
        await self.scheduler._tick()
        future, = self.snapshots()
        self.advance(-60)
        await self.scheduler._tick()
        recent = [item for item in self.snapshots() if item["snapshot_id"] != future["snapshot_id"]]
        self.assertEqual(len(recent), 1)
        self.assertTrue(self.backup.preview_restore(snapshot_id=recent[0]["snapshot_id"]).is_success)
        self.assertEqual(self.scheduler._cached_latest[0], recent[0]["snapshot_id"])
        await self.scheduler._tick()
        self.assertEqual(len(self.snapshots()), 2)

    async def test_deleting_cached_snapshot_reschedules_using_remaining_bundle(self):
        self.settings["auto_backup_interval_minutes"] = 60
        earlier = self.backup.create(note="earlier").unwrap()
        self.advance(10)
        latest = self.backup.create(note="latest").unwrap()
        await self.scheduler._tick()
        self.assertEqual(self.scheduler._cached_latest[0], latest["snapshot_id"])
        for item in (latest, earlier):
            self.assertTrue(self.backup.delete(snapshot_id=item["snapshot_id"],
                                               confirmed_hash=item["content_hash"], confirm=True).is_success)
            self.advance(1)
            await self.scheduler._tick()
            remaining, = self.snapshots()
            if item is latest:
                self.assertEqual(remaining["snapshot_id"], earlier["snapshot_id"])
            else:
                self.assertEqual(remaining["note"], "自动滚动备份")
                self.assertNotEqual(remaining["snapshot_id"], earlier["snapshot_id"])

    async def test_reports_partial_cleanup_without_losing_new_snapshot(self):
        old = self.backup.create(note="old").unwrap()
        self.settings["retention_count"] = 1
        self.advance(1)
        with patch("companion_v2.storage.management._remove_tree", return_value=None):
            with self.assertLogs("astrbot_host.automatic_backup", level="WARNING") as logs:
                await self.scheduler._tick()
        self.assertTrue(any("failed_count=1" in line for line in logs.output))
        self.assertEqual(len(self.snapshots()), 2)
        self.assertNotEqual(self.scheduler._cached_latest[0], old["snapshot_id"])

    async def test_close_drains_worker_without_blocking_event_loop(self):
        entered, release = Event(), Event()
        real_create = InstallationBackup.create

        def held_create(backup, **kwargs):
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError("test did not release backup")
            return real_create(backup, **kwargs)

        with patch.object(InstallationBackup, "create", held_create):
            self.scheduler.start().start()
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                closing = asyncio.create_task(self.scheduler.close())
                await asyncio.sleep(0)
                self.assertFalse(closing.done())
                async with self.controller._control_lock:
                    self.assertTrue(backup_operation_lock(self.controller).locked())
            finally:
                release.set()
                await asyncio.wait_for(self.scheduler.close(), timeout=10)
            await closing
        self.assertEqual(len(self.snapshots()), 1)
