# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot_plugin_private_companion.helpers import _flat_get
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class SegmentedConfigSaveTests(unittest.TestCase):
    def test_frontend_treats_persisted_feature_settings_as_authoritative(self) -> None:
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        block = re.search(
            r"const settingBackedFeatureKeys = \[(.*?)\n\s*\];",
            script,
            re.S,
        )

        self.assertIsNotNone(block)
        self.assertIn('"enable_segmented_proactive_reply"', block.group(1))
        self.assertIn("Object.keys(draft).forEach((key) =>", script)
        self.assertIn("draft[key] = toBool(settings[key]);", script)

    def test_object_replacement_rules_have_reversible_textarea_format(self) -> None:
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function featureTextareaValue(key, value)", script)
        self.assertIn('key === "segmented_proactive_content_replacements"', script)
        self.assertIn("item.from ?? item.old ?? item.source", script)
        self.assertIn("escapeHtml(featureTextareaValue(key, value))", script)

    def test_runtime_overview_prefers_persisted_segmented_values(self) -> None:
        persisted = {
            "message_debounce_config": {
                "enable_message_debounce": True,
            },
            "legacy_compat_config": {
                "enable_segmented_proactive_reply": True,
                "segmented_proactive_scope": "all_llm",
                "segmented_proactive_chat_scope": "private",
                "segmented_proactive_threshold": 420,
                "segmented_proactive_min_segment_chars": 6,
                "segmented_proactive_max_segments": 5,
                "segmented_proactive_send_as_forward": True,
                "segmented_proactive_split_mode": "words",
                "segmented_proactive_regex": "test-regex",
                "segmented_proactive_split_words": ["。", "！"],
                "enable_segmented_proactive_content_cleanup": True,
                "segmented_proactive_content_cleanup_scope": "trailing",
                "segmented_proactive_content_cleanup_rule": "[。]",
                "segmented_proactive_content_cleanup_words": ["。"],
                "enable_segmented_proactive_content_replacement": True,
                "segmented_proactive_content_replacements": ["旧称 => 新称"],
                "segmented_proactive_interval_method": "random",
                "segmented_proactive_interval_min": 2.0,
                "segmented_proactive_interval_max": 4.0,
                "segmented_proactive_log_base": 2.2,
            }
        }
        plugin = SimpleNamespace(
            config=persisted,
            enable_message_debounce=False,
            enable_segmented_proactive_reply=False,
            segmented_proactive_scope="proactive_only",
            segmented_proactive_chat_scope="all",
            segmented_proactive_threshold=500,
        )
        api = PrivateCompanionPageApi(plugin)

        settings = api._runtime_settings()

        for key, value in persisted["legacy_compat_config"].items():
            self.assertEqual(settings[key], value, key)
        self.assertTrue(settings["enable_message_debounce"])

    def test_segmented_settings_survive_real_save_and_reload(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        expected = {
            "enable_segmented_proactive_reply": True,
            "segmented_proactive_scope": "all_llm",
            "segmented_proactive_chat_scope": "private",
            "segmented_proactive_threshold": 420,
            "segmented_proactive_min_segment_chars": 6,
            "segmented_proactive_max_segments": 5,
            "segmented_proactive_send_as_forward": True,
            "segmented_proactive_split_mode": "words",
            "segmented_proactive_split_words": ["。", "！", "<newline>"],
            "enable_segmented_proactive_content_cleanup": True,
            "segmented_proactive_content_cleanup_scope": "trailing",
            "segmented_proactive_content_cleanup_words": ["。", "<newline>"],
            "enable_segmented_proactive_content_replacement": True,
            "segmented_proactive_content_replacements": ["旧称 => 新称"],
            "segmented_proactive_interval_method": "random",
            "segmented_proactive_interval_min": 2.0,
            "segmented_proactive_interval_max": 4.0,
            "segmented_proactive_log_base": 2.2,
        }

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)

            normalized_values = {}
            for key, value in expected.items():
                normalized = api._normalize_setting_value(key, value)
                normalized_values[key] = normalized
                api._apply_config_value(key, normalized, normalized_values)
                self.assertEqual(getattr(plugin, key), normalized, key)

            self.assertTrue(asyncio.run(api._save_config_if_possible()))
            reloaded = AstrBotConfig(str(config_path), schema=schema)

            for key, normalized in normalized_values.items():
                group_key = api._schema_group_for_key(key)
                self.assertTrue(group_key, key)
                self.assertEqual(reloaded[group_key][key], normalized, f"{key} grouped")
                self.assertEqual(_flat_get(reloaded, key), normalized, f"{key} reload")


if __name__ == "__main__":
    unittest.main()
