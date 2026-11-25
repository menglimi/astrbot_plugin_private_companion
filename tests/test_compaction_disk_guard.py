"""Regression tests for full-VACUUM 2.2x free-disk-space guard and health report metadata."""

from __future__ import annotations

import asyncio
from collections import namedtuple
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests import astrbot_data_root  # noqa: F401
from astrbot_host.storage_api import StorageWebAPI
from companion_v2.execution.models import TaskError
from companion_v2.storage.compaction import (
    FULL_VACUUM_DISK_MARGIN_BYTES,
    FULL_VACUUM_DISK_MULTIPLIER,
    _maintain_sidecar_sqlite,
    compact_and_slim_databases,
    database_health_report,
    full_vacuum_required_bytes,
)
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.tuning_config import (
    clear_active_performance_plan,
    resolve_performance_plan,
    set_active_performance_plan,
)

_DiskUsage = namedtuple("_DiskUsage", ("total", "used", "free"))


class CompactionDiskGuardTests(unittest.TestCase):
    def setUp(self):
        clear_active_performance_plan()
        self.addCleanup(clear_active_performance_plan)

    def test_full_vacuum_rejected_when_free_disk_below_2_2x_threshold(self):
        with TemporaryDirectory(prefix="compaction-disk-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="disk-guard-inst"
        ) as runtime:

            main_size = runtime.database_path.stat().st_size
            required = full_vacuum_required_bytes(main_size)
            self.assertEqual(required, int(main_size * FULL_VACUUM_DISK_MULTIPLIER) + FULL_VACUUM_DISK_MARGIN_BYTES)

            low_free = _DiskUsage(total=required * 4, used=required * 4 - (required - 1024), free=required - 1024)
            with patch("companion_v2.storage.compaction.shutil.disk_usage", return_value=low_free):
                report = database_health_report(runtime)
                self.assertEqual(report["disk_free_bytes"], required - 1024)
                self.assertEqual(report["vacuum_required_bytes"], required)
                self.assertFalse(report["can_full_vacuum"])

                with self.assertRaises(TaskError) as caught:
                    compact_and_slim_databases(runtime, vacuum=True)
                self.assertEqual(caught.exception.code, "storage_insufficient_disk_for_vacuum")
                self.assertIn("2.2", str(caught.exception))

                # Online normal compaction (vacuum=False) still succeeds cleanly under low disk space!
                online = compact_and_slim_databases(runtime, vacuum=False)
                self.assertFalse(online["steps"]["vacuum"]["main"]["vacuumed"])

    def test_full_vacuum_succeeds_when_free_disk_meets_threshold(self):
        with TemporaryDirectory(prefix="compaction-disk-ok-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="disk-ok-inst"
        ) as runtime:

            main_size = runtime.database_path.stat().st_size
            required = full_vacuum_required_bytes(main_size)
            plenty = _DiskUsage(total=required * 10, used=required, free=required + 10 * 1024 * 1024)
            with patch("companion_v2.storage.compaction.shutil.disk_usage", return_value=plenty):
                report = database_health_report(runtime)
                self.assertTrue(report["can_full_vacuum"])

                result = compact_and_slim_databases(runtime, vacuum=True)
                self.assertTrue(result["steps"]["vacuum"]["main"]["vacuumed"])

    def test_health_report_uses_active_tuning_telemetry_ttl_days(self):
        plan = resolve_performance_plan({"profile": "eco"}, total_memory_bytes=2 * 1024 ** 3, cpu_count=2)
        set_active_performance_plan(plan)
        with TemporaryDirectory(prefix="compaction-ttl-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="ttl-inst"
        ) as runtime:
            report = database_health_report(runtime)
            self.assertEqual(report["telemetry_ttl_days"], 7)

    def test_full_vacuum_fails_closed_when_disk_usage_unavailable(self):
        with TemporaryDirectory(prefix="compaction-disk-none-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="disk-none-inst"
        ) as runtime:
            with patch("companion_v2.storage.compaction.shutil.disk_usage", side_effect=OSError("unavailable")):
                report = database_health_report(runtime)
                self.assertIsNone(report["disk_free_bytes"])
                self.assertFalse(report["can_full_vacuum"])

                with self.assertRaises(TaskError) as caught:
                    compact_and_slim_databases(runtime, vacuum=True)
                self.assertEqual(caught.exception.code, "storage_insufficient_disk_for_vacuum")
                self.assertIn("无法获取", str(caught.exception))

                # Online normal compaction (vacuum=False) still succeeds when disk_usage fails.
                online = compact_and_slim_databases(runtime, vacuum=False)
                self.assertFalse(online["steps"]["vacuum"]["main"]["vacuumed"])

    def test_sidecar_full_vacuum_rejected_when_sidecar_disk_insufficient(self):
        with TemporaryDirectory(prefix="compaction-sidecar-disk-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="sidecar-disk-inst"
        ) as runtime:
            sidecar_path = runtime.data_root / "semantic-catalog.sqlite3"
            conn = sqlite3.connect(str(sidecar_path))
            try:
                conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, payload BLOB)")
                payload = b"x" * 65536
                conn.executemany("INSERT INTO items (payload) VALUES (?)", [(payload,) for _ in range(40)])
                conn.commit()
            finally:
                conn.close()

            main_size = runtime.database_path.stat().st_size
            sidecar_size = sidecar_path.stat().st_size
            self.assertGreater(sidecar_size, main_size)
            main_req = full_vacuum_required_bytes(main_size)
            sidecar_req = full_vacuum_required_bytes(sidecar_size)
            self.assertGreater(sidecar_req, main_req)

            # Free space satisfies main DB, but NOT the larger sidecar DB!
            between_free = _DiskUsage(
                total=sidecar_req * 4,
                used=sidecar_req * 4 - (sidecar_req - 1024),
                free=sidecar_req - 1024,
            )
            self.assertGreaterEqual(between_free.free, main_req)
            with patch("companion_v2.storage.compaction.shutil.disk_usage", return_value=between_free):
                report = database_health_report(runtime)
                self.assertEqual(report["vacuum_required_bytes"], sidecar_req)
                self.assertFalse(report["can_full_vacuum"])

                with self.assertRaises(TaskError) as caught:
                    compact_and_slim_databases(runtime, vacuum=True)
                self.assertEqual(caught.exception.code, "storage_insufficient_disk_for_vacuum")
                self.assertIn("语义与表情库", str(caught.exception))

                with self.assertRaises(TaskError) as direct_caught:
                    _maintain_sidecar_sqlite(sidecar_path, db_label="语义与表情库", vacuum=True)
                self.assertEqual(direct_caught.exception.code, "storage_insufficient_disk_for_vacuum")

            # Also verify fail-closed directly on _maintain_sidecar_sqlite when disk_usage raises OSError.
            with patch("companion_v2.storage.compaction.shutil.disk_usage", side_effect=OSError("unavailable")):
                with self.assertRaises(TaskError) as none_caught:
                    _maintain_sidecar_sqlite(sidecar_path, db_label="语义与表情库", vacuum=True)
                self.assertEqual(none_caught.exception.code, "storage_insufficient_disk_for_vacuum")

    def test_full_vacuum_flushes_batched_writes_again_before_vacuum_and_enables_incremental(self):
        with TemporaryDirectory(prefix="compaction-flush2-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="flush2-inst"
        ) as runtime:
            main_size = runtime.database_path.stat().st_size
            required = full_vacuum_required_bytes(main_size)
            plenty = _DiskUsage(total=required * 10, used=required, free=required + 20 * 1024 * 1024)
            flush_spy = MagicMock(side_effect=runtime.flush_batched_writes)
            with (
                patch.object(runtime, "flush_batched_writes", flush_spy),
                patch("companion_v2.storage.compaction.shutil.disk_usage", return_value=plenty),
            ):
                result = compact_and_slim_databases(runtime, vacuum=True, enable_incremental_vacuum=True)
            self.assertTrue(result["steps"]["vacuum"]["main"]["vacuumed"])
            self.assertGreaterEqual(flush_spy.call_count, 2)
            self.assertEqual(result["after"]["databases"]["main"]["auto_vacuum"], "incremental")


class CompactionDiskGuardAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_compact_api_surfaces_disk_guard_error_code(self):
        with TemporaryDirectory(prefix="compaction-api-guard-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id="disk-api-inst"
        ) as runtime:

            controller = NS(
                config={},
                runtime=runtime,
                session=None,
                last_session=None,
                _control_lock=asyncio.Lock(),
                _terminated=False,
                data_root=Path(tmp),
            )
            api = StorageWebAPI(controller)
            low_free = _DiskUsage(total=1024 ** 3, used=1024 ** 3 - 4096, free=4096)
            req = NS(
                username="admin",
                query={},
                body=AsyncMock(return_value=json.dumps({"vacuum": True}).encode("utf-8")),
            )
            with (
                patch("astrbot_host.storage_api.request", req),
                patch("companion_v2.storage.compaction.shutil.disk_usage", return_value=low_free),
            ):
                resp = await api.compact()
            self.assertEqual(resp["status"], "error")
            self.assertEqual(resp["data"]["code"], "storage_insufficient_disk_for_vacuum")


if __name__ == "__main__":
    unittest.main()
