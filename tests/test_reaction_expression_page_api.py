# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot_plugin_private_companion.helpers import _flat_get
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]
REACTION_SETTING_KEYS = {
    "reaction_expression_private_enabled",
    "reaction_expression_group_enabled",
    "reaction_expression_trigger_probability",
    "reaction_expression_cooldown_seconds",
    "reaction_expression_low_latency_mode",
    "reaction_expression_candidate_limit",
}


class ReactionExpressionPageApiTests(unittest.TestCase):
    def test_feature_and_subsettings_are_exposed_by_page_contract(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None
        api.plugin = SimpleNamespace(enable_reaction_expression_experiment=True)
        api._screen_companion_available = lambda: False

        self.assertIn(
            "enable_reaction_expression_experiment",
            api._allowed_feature_keys(),
        )
        self.assertTrue(
            api._feature_flags()["enable_reaction_expression_experiment"]
        )
        self.assertTrue(REACTION_SETTING_KEYS <= api._allowed_setting_keys())

        settings = api._runtime_settings()
        self.assertTrue(REACTION_SETTING_KEYS <= set(settings))

    def test_runtime_summary_uses_real_counters_and_aggregates_recent_outcomes(self) -> None:
        plugin = SimpleNamespace(
            enable_reaction_expression_experiment=True,
            _reaction_expression_runtime={
                "attempts": "7",
                "offers": -2,
                "lookups": 4,
                "cache_hits": "2",
                "sent": 1,
                "skipped": 3,
                "last_reason": "cooldown\nignored",
                "last_latency_ms": -5,
                "total_lookup_ms": 123.456,
            },
        )
        api = PrivateCompanionPageApi(plugin)
        data = {
            "users": {
                "10001": {
                    "reaction_expression": {
                        "recent_outcomes": [
                            {"at": 10, "status": "sent", "reason": "delivered", "image_key": "private-a"},
                            {"at": 20, "status": "skipped", "reason": "cooldown", "image_key": "private-b"},
                            {"at": 30, "status": "skipped", "reason": "cooldown", "image_key": "private-c"},
                        ],
                        "preference": {"positive_count": 2, "negative_count": 1},
                    }
                },
                "10002": {
                    "reaction_expression": {
                        "recent_outcomes": [None],
                        "preference": {"positive_count": 1, "negative_count": 4},
                    }
                },
            }
        }

        summary = api._reaction_expression_runtime_summary(data)

        self.assertEqual(
            summary["runtime"],
            {
                "attempts": 7,
                "offers": 0,
                "lookups": 4,
                "cache_hits": 2,
                "sent": 1,
                "skipped": 3,
                "last_latency_ms": 0.0,
                "total_lookup_ms": 123.46,
                "last_reason": "cooldown ignored",
            },
        )
        self.assertEqual(summary["recent"]["tracked_user_count"], 2)
        self.assertEqual(summary["recent"]["attempt_count"], 3)
        self.assertEqual(summary["recent"]["sent_count"], 1)
        self.assertEqual(summary["recent"]["skipped_count"], 2)
        self.assertEqual(summary["recent"]["skip_reasons"], {"cooldown": 2})
        self.assertEqual(summary["recent"]["last_activity_at"], 30.0)
        self.assertEqual(summary["recent"]["positive_feedback_count"], 3)
        self.assertEqual(summary["recent"]["negative_feedback_count"], 5)
        self.assertNotIn("private-a", json.dumps(summary, ensure_ascii=False))


class ReactionExpressionPageApiSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_update_normalizes_saves_and_returns_reaction_settings(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        payload = {
            "features": {"enable_reaction_expression_experiment": "true"},
            "settings": {
                "reaction_expression_private_enabled": "false",
                "reaction_expression_group_enabled": "yes",
                "reaction_expression_trigger_probability": 180,
                "reaction_expression_cooldown_seconds": 9999,
                "reaction_expression_low_latency_mode": "off",
                "reaction_expression_candidate_limit": 99,
            },
        }
        expected = {
            "enable_reaction_expression_experiment": True,
            "reaction_expression_private_enabled": False,
            "reaction_expression_group_enabled": True,
            "reaction_expression_trigger_probability": 1.0,
            "reaction_expression_cooldown_seconds": 3600,
            "reaction_expression_low_latency_mode": False,
            "reaction_expression_candidate_limit": 16,
        }

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)
            api.get_overview = AsyncMock(
                return_value={"success": True, "data": {"features": {}, "settings": {}}}
            )
            fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))

            with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
                result = await api.update_settings()

            self.assertTrue(result["success"])
            self.assertTrue(result["data"]["config_saved"])
            self.assertEqual(result["data"]["changed"], expected)
            self.assertTrue(
                result["data"]["features"]["enable_reaction_expression_experiment"]
            )
            for key in REACTION_SETTING_KEYS:
                self.assertEqual(result["data"]["settings"][key], expected[key], key)
                self.assertEqual(getattr(plugin, key), expected[key], key)

            reloaded = AstrBotConfig(str(config_path), schema=schema)
            for key, value in expected.items():
                self.assertEqual(_flat_get(reloaded, key), value, key)


class ReactionLibraryPageApiTests(unittest.IsolatedAsyncioTestCase):
    PNG_BYTES = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    async def test_import_list_update_preview_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plugin = SimpleNamespace(data_dir=folder)
            api = PrivateCompanionPageApi(plugin)
            encoded = base64.b64encode(self.PNG_BYTES).decode("ascii")
            import_request = SimpleNamespace(
                get_json=AsyncMock(
                    return_value={
                        "files": [{"name": "开心.png", "data": encoded}],
                        "metadata": {"emotions": ["开心"], "scopes": ["private"]},
                    }
                )
            )
            with patch("astrbot_plugin_private_companion.page_api.request", import_request):
                imported = await api.import_reaction_library()
            self.assertTrue(imported["success"])
            self.assertEqual(1, imported["data"]["imported"])
            item_id = imported["data"]["items"][0]["id"]

            list_request = SimpleNamespace(args={"q": "开心", "status": "enabled", "scope": "private", "page": "1", "page_size": "20"})
            with patch("astrbot_plugin_private_companion.page_api.request", list_request):
                listed = await api.list_reaction_library()
            self.assertEqual(1, listed["data"]["total"])
            self.assertEqual(item_id, listed["data"]["items"][0]["id"])

            update_request = SimpleNamespace(
                get_json=AsyncMock(return_value={"ids": [item_id], "changes": {"intents": "庆祝", "enabled": False}})
            )
            with patch("astrbot_plugin_private_companion.page_api.request", update_request):
                updated = await api.update_reaction_library()
            self.assertEqual(1, updated["data"]["updated"])

            preview_request = SimpleNamespace(args={"id": item_id})
            with patch("astrbot_plugin_private_companion.page_api.request", preview_request):
                preview = await api.get_reaction_library_image_data()
            self.assertTrue(preview["data"]["data_url"].startswith("data:image/png;base64,"))

            delete_request = SimpleNamespace(
                get_json=AsyncMock(return_value={"ids": [item_id], "confirm": True})
            )
            with patch("astrbot_plugin_private_companion.page_api.request", delete_request):
                deleted = await api.delete_reaction_library()
            self.assertEqual(1, deleted["data"]["deleted"])


if __name__ == "__main__":
    unittest.main()
