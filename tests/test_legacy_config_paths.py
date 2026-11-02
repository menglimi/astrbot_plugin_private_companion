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


if __name__ == "__main__":
    unittest.main()
