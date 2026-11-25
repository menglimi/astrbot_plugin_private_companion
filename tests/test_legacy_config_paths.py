from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from astrbot_host.legacy_config import (
    V1_HOST_CONFIG_NAME,
    V1_PLUGIN_DATA_NAME,
    find_v1_data_root,
    v1_host_config_path,
    v1_plugin_config_path,
)
from astrbot_host.upgrade_source import preserve_before_schema


class LegacyConfigPathTests(unittest.TestCase):
    def test_nested_runtime_root_finds_v1_parent(self):
        with TemporaryDirectory() as temporary:
            data = Path(temporary)
            v1 = data / "plugin_data" / V1_PLUGIN_DATA_NAME
            runtime = v1 / "companion-7"
            runtime.mkdir(parents=True)
            (v1 / "companions.json").write_text("{}", encoding="utf-8")

            self.assertEqual(find_v1_data_root(runtime), (v1, "nested-parent"))

    def test_nested_runtime_root_finds_astrbot_config_files(self):
        with TemporaryDirectory() as temporary:
            data = Path(temporary)
            runtime = data / "plugin_data" / V1_PLUGIN_DATA_NAME / "companion-7"
            runtime.mkdir(parents=True)
            host_config = data / "config" / V1_HOST_CONFIG_NAME
            reality_config = data / "config" / "astrbot_plugin_reality_companion_config.json"
            host_config.parent.mkdir(parents=True)
            host_config.write_text(json.dumps({"version": 1}), encoding="utf-8")
            reality_config.write_text("{}", encoding="utf-8")

            self.assertEqual(v1_host_config_path(runtime), host_config)
            self.assertEqual(v1_plugin_config_path(runtime, "astrbot_plugin_reality_companion"), reality_config)


class LegacyConfigArchiveTests(unittest.TestCase):
    """A damaged upgrade archive must not turn the settings reader into a 500.

    ``v1_host_config_path`` is the entry every migration preview goes through, so
    an uncaught error here fails the whole "发现" request — the same damage that
    used to stop the plugin from loading also stopped the operator from even
    seeing why.  The fallback below is the live host file the hook already
    preserved; it never invents a source.
    """

    def _install(self, temporary, live):
        data = Path(temporary)
        runtime = data / "plugin_data" / V1_PLUGIN_DATA_NAME / "companion-7"
        runtime.mkdir(parents=True)
        host_config = data / "config" / V1_HOST_CONFIG_NAME
        host_config.parent.mkdir(parents=True)
        host_config.write_text(json.dumps(live), encoding="utf-8")
        return runtime, host_config

    def test_broken_archive_falls_back_to_the_live_host_file(self):
        legacy = {"basic_config": {"target_user_ids": ["123"]}}
        with TemporaryDirectory() as temporary:
            runtime, host_config = self._install(temporary, legacy)
            saved = preserve_before_schema(host_config.parent)
            self.assertIsNotNone(saved)
            saved.write_text("{}", encoding="utf-8")  # archive no longer verifies
            with self.assertLogs("astrbot_host.upgrade_source", level="WARNING"):
                self.assertEqual(v1_host_config_path(runtime), host_config)

    def test_intact_archive_still_wins_over_a_pruned_live_file(self):
        legacy = {"basic_config": {"target_user_ids": ["123"]}}
        with TemporaryDirectory() as temporary:
            runtime, host_config = self._install(temporary, legacy)
            saved = preserve_before_schema(host_config.parent)
            host_config.write_text(json.dumps({"performance": {}, "storage": {}}), encoding="utf-8")
            self.assertEqual(v1_host_config_path(runtime), saved)


class LegacyOverridePathNormalizationTests(unittest.TestCase):
    def test_normalize_v1_override_path_handles_wrappers_errors_markers_and_descend(self):
        from unittest.mock import patch
        from astrbot_host.legacy_api import _normalize_v1_override_path
        from companion_v2.execution.models import TaskError

        with self.assertRaises(TaskError) as empty_err:
            _normalize_v1_override_path("   ")
        self.assertEqual(empty_err.exception.code, "legacy_path_empty")

        with patch("astrbot_host.legacy_api.os.name", "posix"):
            with self.assertRaises(TaskError) as win_err:
                _normalize_v1_override_path(r"D:\AstrBot\data\plugin_data\astrbot_plugin_private_companion")
            self.assertEqual(win_err.exception.code, "legacy_path_windows_on_posix")

        with self.assertRaises(TaskError) as rel_err:
            _normalize_v1_override_path("relative/path/here")
        self.assertEqual(rel_err.exception.code, "legacy_path_not_absolute")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "does-not-exist"
            with self.assertRaises(TaskError) as missing_err:
                _normalize_v1_override_path(str(missing))
            self.assertEqual(missing_err.exception.code, "legacy_path_not_found")

            # Quoted path and file:// URI resolve cleanly.
            v1 = root / "data" / "plugin_data" / V1_PLUGIN_DATA_NAME
            v1.mkdir(parents=True)
            marker = v1 / "companions.json"
            marker.write_text("{}", encoding="utf-8")

            self.assertEqual(_normalize_v1_override_path(f'"{v1}"'), v1.resolve())
            self.assertEqual(_normalize_v1_override_path(v1.as_uri()), v1.resolve())
            # Marker file falls back to its containing directory.
            self.assertEqual(_normalize_v1_override_path(str(marker)), v1.resolve())
            # Auto-descend from plugin_data/ and data/ into astrbot_plugin_private_companion.
            self.assertEqual(_normalize_v1_override_path(str(root / "data" / "plugin_data")), v1.resolve())
            self.assertEqual(_normalize_v1_override_path(str(root / "data")), v1.resolve())

            # Non-marker regular file raises legacy_path_is_file.
            random_file = root / "notes.txt"
            random_file.write_text("hi", encoding="utf-8")
            with self.assertRaises(TaskError) as file_err:
                _normalize_v1_override_path(str(random_file))
            self.assertEqual(file_err.exception.code, "legacy_path_is_file")

    def test_memory_archive_tolerates_symlinked_ancestor_but_rejects_symlinked_db(self):
        import os
        import sqlite3
        from companion_v2.legacy.memory_archive import build_memory_database_plan
        from companion_v2.execution.models import TaskError

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_parent = root / "real_storage" / "astrbot_plugin_memory_companion"
            real_parent.mkdir(parents=True)
            db_path = real_parent / "memory_companion.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
            conn.commit()
            conn.close()

            link_parent = root / "sdcard_link"
            try:
                os.symlink(real_parent.parent, link_parent, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks require elevated privilege in this environment")

            via_link_dir = link_parent / "astrbot_plugin_memory_companion"
            plan = build_memory_database_plan(via_link_dir, db_path.name)
            self.assertEqual(plan.source_root, real_parent.resolve())

            symlinked_db = real_parent / "linked.db"
            os.symlink(db_path, symlinked_db)
            with self.assertRaises(TaskError):
                build_memory_database_plan(real_parent, symlinked_db.name)


if __name__ == "__main__":
    unittest.main()
