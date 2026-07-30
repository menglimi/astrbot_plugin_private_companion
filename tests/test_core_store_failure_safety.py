# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.core_store import CoreStoreMixin


class _AsyncConfig:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.awaited = False

    async def save_config(self) -> None:
        await asyncio.sleep(0)
        self.awaited = True
        if self.fail:
            raise OSError("配置目录不可写")


class _CoreHarness(CoreStoreMixin):
    def __init__(self) -> None:
        self.config = _AsyncConfig()
        self.store_manager = None
        self._data_save_task = None
        self._data_save_dirty = False
        self._stop_event = asyncio.Event()

    @staticmethod
    def _new_store() -> dict:
        return {"users": {}}


class CoreStoreFailureSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_config_save_is_awaited(self) -> None:
        harness = _CoreHarness()

        saved = await harness._save_config_if_possible()

        self.assertTrue(saved)
        self.assertTrue(harness.config.awaited)

    async def test_async_config_save_failure_is_reported(self) -> None:
        harness = _CoreHarness()
        harness.config = _AsyncConfig(fail=True)

        saved = await harness._save_config_if_possible()

        self.assertFalse(saved)
        self.assertTrue(harness.config.awaited)

    async def test_flush_does_not_reschedule_dirty_write_while_stopping(self) -> None:
        harness = _CoreHarness()
        harness._data_save_dirty = True
        harness._stop_event.set()

        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=0.2)

        self.assertIsNone(harness._data_save_task)
        self.assertTrue(harness._data_save_dirty)

    def test_store_manager_failure_does_not_fall_back_to_stale_json(self) -> None:
        harness = _CoreHarness()
        harness.store_manager = SimpleNamespace(
            load_initial_store=lambda: (_ for _ in ()).throw(OSError("database is locked"))
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "companions.json"
            data_file.write_text('{"users": {"42": {"name": "stale"}}}', encoding="utf-8")
            harness.data_file = str(data_file)

            with self.assertRaisesRegex(OSError, "database is locked"):
                harness._load_data_sync()

    def test_existing_invalid_direct_json_is_not_replaced_with_defaults(self) -> None:
        harness = _CoreHarness()
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "companions.json"
            data_file.write_text('{"users": ', encoding="utf-8")
            harness.data_file = str(data_file)

            with self.assertRaises(Exception):
                harness._load_data_sync()

            self.assertEqual('{"users": ', data_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
