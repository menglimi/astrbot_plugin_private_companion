from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class AstrBot4274DatabaseCompatTests(unittest.IsolatedAsyncioTestCase):
    async def test_framework_db_timeout_does_not_cancel_or_duplicate_query(self) -> None:
        owner = object.__new__(DailyStateMixin)
        release = asyncio.Event()
        calls = 0

        async def query() -> str:
            nonlocal calls
            calls += 1
            await release.wait()
            return "persona"

        with self.assertRaises(asyncio.TimeoutError):
            await owner._await_framework_db_query("persona:test", query, timeout=0.01)

        pending = owner._framework_db_query_tasks["persona:test"]
        self.assertFalse(pending.cancelled())

        waiter = asyncio.create_task(
            owner._await_framework_db_query("persona:test", query, timeout=1.0)
        )
        await asyncio.sleep(0)
        self.assertEqual(calls, 1)
        release.set()
        self.assertEqual(await waiter, "persona")


def test_wal_candidates_only_include_private_companion_owned_databases(
    tmp_path: Path,
) -> None:
    plugin = object.__new__(PrivateCompanionPlugin)
    own_db = tmp_path / "companions.db"
    own_db.touch()
    profiles = tmp_path / "persona_profiles"
    profiles.mkdir()
    profile_db = profiles / "persona-a.db"
    profile_db.touch()
    plugin.data_dir = str(tmp_path)
    plugin.storage_sqlite_effective_path = str(own_db)
    plugin._persona_profiles_dir = str(profiles)
    plugin.context = SimpleNamespace(conversation_manager=object())

    assert plugin._sqlite_wal_candidate_paths() == [own_db, profile_db]
    assert not hasattr(plugin, "_install_sqlite_wal_engine_hooks")


def test_wal_candidates_skip_astrbot_shared_database_paths(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    plugin_dir = data_root / "plugin_data" / "astrbot_plugin_private_companion"
    plugin_dir.mkdir(parents=True)
    core_db = data_root / "data_v4.db"
    core_db.touch()
    own_db = plugin_dir / "companions.db"
    own_db.touch()
    plugin = object.__new__(PrivateCompanionPlugin)
    plugin.data_dir = str(plugin_dir)
    plugin.storage_sqlite_effective_path = str(core_db)
    plugin._persona_profiles_dir = ""

    assert plugin._sqlite_wal_candidate_paths() == []
