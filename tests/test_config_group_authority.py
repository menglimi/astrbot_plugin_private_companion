# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot_plugin_private_companion.config_migration import migrate_flat_config_into_schema_groups
from astrbot_plugin_private_companion.helpers import _flat_get, _safe_float
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class ConfigGroupAuthorityTests(unittest.TestCase):
    def test_safe_float_supports_optional_maximum(self):
        self.assertEqual(_safe_float("200", 12.0, 1.0, 168.0), 168.0)
        self.assertEqual(_safe_float("0.5", 12.0, 1.0, 168.0), 1.0)
        self.assertEqual(_safe_float("200", 12.0, 1.0), 200.0)

    def test_public_grouped_schema_item_wins_over_hidden_flat_compatibility_copy(self):
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None

        expected_groups = {
            "bot_name": "basic_config",
            "daily_token_limit": "basic_config",
            "MAI_STYLE_PROVIDER_ID": "model_assignment_config",
            "weather_api_host": "weather_config",
            "weather_token": "weather_config",
        }
        for key, expected_group in expected_groups.items():
            self.assertEqual(api._schema_group_for_key(key), expected_group, key)
            self.assertFalse(api._schema_item_for_key(key).get("invisible", False), key)

    def test_group_is_created_when_only_hidden_flat_compatibility_copy_existed(self):
        plugin = SimpleNamespace(config={})
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        api._schema_key_index_cache = None

        api._set_config_value("bot_name", "新名字")

        self.assertEqual(plugin.config["basic_config"]["bot_name"], "新名字")

    @staticmethod
    def _schema_file(folder: str) -> Path:
        path = Path(folder) / "schema.json"
        path.write_text(
            json.dumps(
                {
                    "basic_config": {
                        "type": "object",
                        "items": {
                            "daily_token_limit": {"type": "int", "default": 1000000},
                            "enable_daily_token_soft_limit": {"type": "bool", "default": True},
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_visible_group_value_wins_over_stale_flat_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {
                "daily_token_limit": 1500000,
                "enable_daily_token_soft_limit": False,
                "basic_config": {
                    "daily_token_limit": 1000000,
                    "enable_daily_token_soft_limit": True,
                },
            }
            changed = migrate_flat_config_into_schema_groups(
                config, schema_path=self._schema_file(folder), save=False
            )
        self.assertGreater(changed, 0)
        self.assertEqual(config["daily_token_limit"], 1000000)
        self.assertTrue(config["enable_daily_token_soft_limit"])

    def test_legacy_flat_value_still_migrates_when_group_value_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {"daily_token_limit": 1500000, "basic_config": {}}
            migrate_flat_config_into_schema_groups(
                config, schema_path=self._schema_file(folder), save=False
            )
        self.assertEqual(config["basic_config"]["daily_token_limit"], 1500000)

    def test_empty_new_reference_defaults_preserve_nonempty_legacy_fields(self):
        config = {
            "photo_persona_reference_image_path": "C:/images/persona.png",
            "photo_reference_library": ["C:/images/home.png || 居家服"],
            "photo_reference_catalog": [],
            "photo_reference_catalog_version": 1,
            "photo_action_config": {
                "photo_persona_reference_image_path": "",
                "photo_reference_library": [],
                "photo_reference_catalog": [],
                "photo_reference_catalog_version": 1,
                "photo_reference_catalog_user_cleared": False,
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        grouped = config["photo_action_config"]
        self.assertEqual(grouped["photo_persona_reference_image_path"], "C:/images/persona.png")
        self.assertEqual(grouped["photo_reference_library"], ["C:/images/home.png || 居家服"])
        self.assertEqual(config["photo_persona_reference_image_path"], "C:/images/persona.png")
        self.assertEqual(config["photo_reference_library"], ["C:/images/home.png || 居家服"])

    def test_explicit_reference_clear_keeps_new_empty_values_authoritative(self):
        config = {
            "photo_persona_reference_image_path": "C:/images/persona.png",
            "photo_reference_library": ["C:/images/home.png || 居家服"],
            "photo_reference_catalog": [],
            "photo_reference_catalog_version": 1,
            "photo_reference_catalog_user_cleared": True,
            "photo_action_config": {
                "photo_persona_reference_image_path": "",
                "photo_reference_library": [],
                "photo_reference_catalog": [],
                "photo_reference_catalog_version": 1,
                "photo_reference_catalog_user_cleared": True,
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(config["photo_persona_reference_image_path"], "")
        self.assertEqual(config["photo_reference_library"], [])

    def test_flat_canonical_reference_catalog_survives_empty_group_default(self):
        canonical = [
            {
                "id": "persona",
                "kind": "persona",
                "source": "C:/images/persona.png",
                "reference_roles": ["identity"],
                "outfit_lock_default": False,
            }
        ]
        config = {
            "photo_reference_catalog": canonical,
            "photo_reference_catalog_version": 1,
            "photo_action_config": {
                "photo_reference_catalog": [],
                "photo_reference_catalog_version": 0,
                "photo_reference_catalog_user_cleared": False,
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(config["photo_action_config"]["photo_reference_catalog"], canonical)
        self.assertEqual(config["photo_reference_catalog"], canonical)

    def test_catalog_save_records_and_clears_explicit_empty_intent(self):
        plugin = SimpleNamespace(config={})
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        api._schema_key_index_cache = None

        api._apply_config_value("photo_reference_catalog", [])
        self.assertTrue(plugin.photo_reference_catalog_user_cleared)
        self.assertTrue(_flat_get(plugin.config, "photo_reference_catalog_user_cleared"))

        api._apply_config_value(
            "photo_reference_catalog",
            [
                {
                    "id": "persona",
                    "kind": "persona",
                    "source": "C:/images/persona.png",
                    "reference_roles": ["identity"],
                    "outfit_lock_default": False,
                }
            ],
        )
        self.assertFalse(plugin.photo_reference_catalog_user_cleared)
        self.assertFalse(_flat_get(plugin.config, "photo_reference_catalog_user_cleared"))

    def test_weather_alert_api_key_alias_migrates_to_credential_field(self):
        api_key = "0123456789abcdef0123456789abcdef"
        config = {
            "weather_alert_api_key": api_key,
            "weather_config": {},
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(api_key, config["weather_config"]["weather_token"])
        self.assertEqual(api_key, config["weather_token"])
        self.assertEqual("", config["weather_config"].get("weather_alert_token", ""))
        self.assertEqual("", config["weather_alert_token"])
        self.assertNotIn("weather_alert_api_key", config)

    def test_group_wakeup_textareas_are_normalized_for_astrbot_list_validation(self):
        values = {
            "group_wakeup_direct_words": "星缘\n缘缘",
            "group_wakeup_owner_direct_words": "小暗号；专属称呼",
            "group_wakeup_context_words": "机器人,bot",
            "group_wakeup_interest_keywords": "摄影、音乐",
        }
        config = {
            **values,
            "group_wakeup_config": dict(values),
        }

        changed = migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        expected = {
            "group_wakeup_direct_words": ["星缘", "缘缘"],
            "group_wakeup_owner_direct_words": ["小暗号", "专属称呼"],
            "group_wakeup_context_words": ["机器人", "bot"],
            "group_wakeup_interest_keywords": ["摄影", "音乐"],
        }
        self.assertGreaterEqual(changed, len(expected))
        for key, value in expected.items():
            self.assertEqual(value, config["group_wakeup_config"][key], key)
            self.assertEqual(value, config[key], f"{key} legacy copy")

    def test_legacy_review_master_switch_migrates_to_independent_switches(self):
        config = {
            "enable_response_self_review": False,
            "enable_passive_response_review": True,
            "enable_proactive_message_review": True,
            "response_review_mode": "full",
            "passive_review_mode": "severe_only",
            "emotion_relationship_config": {
                "enable_response_self_review": False,
                "response_review_mode": "full",
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        grouped = config["emotion_relationship_config"]
        self.assertFalse(grouped["enable_passive_response_review"])
        self.assertFalse(grouped["enable_proactive_message_review"])
        self.assertEqual(grouped["passive_review_mode"], "full")

    def test_obsolete_flat_and_grouped_fields_are_removed(self):
        config = {
            "enable_persona_standardization_experiment": True,
            "enable_llm_timer_scheduling": False,
            "ai_daily_check_window": "07:30-12:30",
            "ai_daily_check_interval_minutes": 40,
            "news_config": {
                "ai_daily_check_window": "06:00-08:00",
                "ai_daily_check_interval_minutes": 10,
            },
            "legacy_compat_config": {
                "enable_llm_timer_scheduling": False,
                "ai_daily_check_window": "06:00-08:00",
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        for key in (
            "enable_persona_standardization_experiment",
            "enable_llm_timer_scheduling",
            "ai_daily_check_window",
            "ai_daily_check_interval_minutes",
        ):
            self.assertNotIn(key, config)
            self.assertNotIn(key, config.get("news_config", {}))
            self.assertNotIn(key, config.get("legacy_compat_config", {}))

    def test_5100_settings_survive_real_config_save_and_reload(self):
        changed_values = {
            "external_link_share_cooldown_hours": 0,
            "enable_creative_cover_generation": True,
            "enable_proactive_chat_integration": False,
            "proactive_chat_bridge_review_mode": "follow_proactive_review",
            "proactive_chat_bridge_collision_window_seconds": 150,
            "expression_private_learning_source_mode": "selected",
            "expression_private_learning_source_ids": ["100000001", "20002"],
            "expression_group_learning_source_mode": "selected",
            "expression_group_learning_source_ids": ["30003"],
            "expression_private_application_mode": "selected",
            "expression_private_application_user_ids": ["100000001"],
            "expression_group_application_mode": "selected",
            "expression_group_application_ids": ["30003", "40004"],
        }
        default_values = {
            "external_link_share_cooldown_hours": 72,
            "enable_creative_cover_generation": False,
            "enable_proactive_chat_integration": True,
            "proactive_chat_bridge_review_mode": "local",
            "proactive_chat_bridge_collision_window_seconds": 90,
            "expression_private_learning_source_mode": "owner",
            "expression_private_learning_source_ids": [],
            "expression_group_learning_source_mode": "disabled",
            "expression_group_learning_source_ids": [],
            "expression_private_application_mode": "all",
            "expression_private_application_user_ids": [],
            "expression_group_application_mode": "all",
            "expression_group_application_ids": [],
        }
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)

            self._apply_save_and_assert_roundtrip(
                api,
                plugin,
                config_path,
                schema,
                changed_values,
            )
            self._apply_save_and_assert_roundtrip(
                api,
                plugin,
                config_path,
                schema,
                default_values,
            )

    def _apply_save_and_assert_roundtrip(
        self,
        api: PrivateCompanionPageApi,
        plugin: SimpleNamespace,
        config_path: Path,
        schema: dict,
        expected: dict,
    ) -> None:
        for key, value in expected.items():
            normalized = api._normalize_setting_value(key, value)
            api._apply_config_value(key, normalized, expected)
            self.assertEqual(getattr(plugin, key), normalized, key)

        self.assertTrue(asyncio.run(api._save_config_if_possible()))
        reloaded = AstrBotConfig(str(config_path), schema=schema)
        plugin.config = reloaded

        for key, value in expected.items():
            normalized = api._normalize_setting_value(key, value)
            group_key = api._schema_group_for_key(key)
            self.assertTrue(group_key, key)
            self.assertEqual(reloaded[key], normalized, f"{key} flat copy")
            self.assertEqual(reloaded[group_key][key], normalized, f"{key} grouped copy")
            self.assertEqual(_flat_get(reloaded, key), normalized, f"{key} reload")


if __name__ == "__main__":
    unittest.main()
