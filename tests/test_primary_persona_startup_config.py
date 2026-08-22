from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.helpers import _flat_get
from astrbot_plugin_private_companion.plugin_bootstrap import (
    _initialize_primary_persona_config,
    _validate_primary_persona_runtime,
)


class _PersistableConfig:
    def __init__(self, data: dict) -> None:
        self.data = data

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def save_config(self) -> None:
        pass


class _Harness:
    def __init__(self, *, multi_enabled: bool) -> None:
        self.enable_multi_persona_mode = multi_enabled

    @staticmethod
    def _cfg_str(config, key: str, default: str = "", fallback: str = "") -> str:
        return str(_flat_get(config, key, default) or "").strip() or fallback

    @staticmethod
    def _sanitize_persona_id(value) -> str:
        return str(value or "").strip()


class PrimaryPersonaStartupConfigTests(unittest.TestCase):
    def test_plugin_specific_is_authoritative_and_legacy_mismatch_is_recorded(self) -> None:
        config = _PersistableConfig(
            {
                "basic_config": {
                    "plugin_specific_persona_id": "main",
                    "multi_persona_primary_id": "legacy-main",
                },
                "multi_persona_primary_id": "legacy-flat",
            }
        )
        plugin = _Harness(multi_enabled=True)

        cleanup_pending = _initialize_primary_persona_config(plugin, config)

        self.assertTrue(cleanup_pending)
        self.assertEqual(plugin.plugin_specific_persona_id, "main")
        self.assertFalse(hasattr(plugin, "multi_persona_primary_id"))
        self.assertEqual(plugin._legacy_multi_persona_primary_id_candidate, "legacy-main")
        self.assertEqual(
            plugin._multi_persona_primary_id_mismatch,
            {"authoritative": "main", "legacy_candidate": "legacy-main"},
        )
        self.assertFalse(plugin._multi_persona_primary_requires_configuration)
        self.assertNotIn("multi_persona_primary_id", config.data)
        self.assertNotIn("multi_persona_primary_id", config.data["basic_config"])

    def test_legacy_value_is_only_a_candidate_when_authoritative_id_is_empty(self) -> None:
        config = _PersistableConfig(
            {
                "basic_config": {"plugin_specific_persona_id": ""},
                "multi_persona_primary_id": "legacy-main",
            }
        )
        plugin = _Harness(multi_enabled=True)

        cleanup_pending = _initialize_primary_persona_config(plugin, config)

        self.assertFalse(cleanup_pending)
        self.assertEqual(plugin.plugin_specific_persona_id, "")
        self.assertFalse(hasattr(plugin, "multi_persona_primary_id"))
        self.assertEqual(plugin._legacy_multi_persona_primary_id_candidate, "legacy-main")
        self.assertEqual(plugin._multi_persona_primary_id_mismatch, {})
        self.assertTrue(plugin._multi_persona_primary_requires_configuration)
        self.assertEqual(config.data["multi_persona_primary_id"], "legacy-main")

    def test_legacy_key_is_not_removed_without_existing_persistence_support(self) -> None:
        config = {
            "basic_config": {"plugin_specific_persona_id": "main"},
            "multi_persona_primary_id": "legacy-main",
        }
        plugin = _Harness(multi_enabled=False)

        cleanup_pending = _initialize_primary_persona_config(plugin, config)

        self.assertFalse(cleanup_pending)
        self.assertEqual(plugin.plugin_specific_persona_id, "main")
        self.assertFalse(hasattr(plugin, "multi_persona_primary_id"))
        self.assertEqual(config["multi_persona_primary_id"], "legacy-main")

    def test_deleted_astrbot_primary_keeps_requested_multi_mode_disabled(self) -> None:
        plugin = _Harness(multi_enabled=True)
        plugin._multi_persona_enable_requested = True
        plugin.plugin_specific_persona_id = "deleted"
        plugin._astrbot_persona_exists = lambda persona_id: False

        invalid = _validate_primary_persona_runtime(plugin)

        self.assertTrue(invalid)
        self.assertTrue(plugin._multi_persona_primary_invalid)
        self.assertFalse(plugin.enable_multi_persona_mode)

    def test_existing_astrbot_primary_preserves_requested_multi_mode(self) -> None:
        plugin = _Harness(multi_enabled=True)
        plugin._multi_persona_enable_requested = True
        plugin.plugin_specific_persona_id = "main"
        plugin._astrbot_persona_exists = lambda persona_id: persona_id == "main"

        invalid = _validate_primary_persona_runtime(plugin)

        self.assertFalse(invalid)
        self.assertTrue(plugin.enable_multi_persona_mode)


if __name__ == "__main__":
    unittest.main()
