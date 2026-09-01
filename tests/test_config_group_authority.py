# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot.core.config.astrbot_config import AstrBotConfig
import astrbot_plugin_private_companion.config_migration as config_migration
from astrbot_plugin_private_companion.config_migration import migrate_flat_config_into_schema_groups
from astrbot_plugin_private_companion.helpers import _flat_get, _safe_float
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.plugin_bootstrap import _normalize_photo_generation_scopes


ROOT = Path(__file__).resolve().parents[1]

PHOTO_SCOPE_LIMIT_KEYS = {
    "private_owner": "photo_generation_private_owner_max_daily",
    "private_friend": "photo_generation_private_friend_max_daily",
    "group": "photo_generation_group_max_daily",
    "proactive": "photo_generation_proactive_max_daily",
}


class ConfigGroupAuthorityTests(unittest.TestCase):
    def test_runtime_public_defaults_are_owned_by_schema_manifest(self):
        self.assertEqual(
            300,
            PrivateCompanionPlugin._cfg_int({}, "check_interval_seconds", 60, 30),
        )
        self.assertEqual(
            30.0,
            PrivateCompanionPlugin._cfg_float(
                {},
                "context_image_caption_timeout_seconds",
                8.0,
            ),
        )
        self.assertEqual(
            500,
            PrivateCompanionPlugin._cfg_int(
                {},
                "group_conversation_followup_seconds",
                120,
            ),
        )
        self.assertEqual(
            300,
            PrivateCompanionPlugin._cfg_int(
                {"check_interval_seconds": "invalid"},
                "check_interval_seconds",
                60,
                30,
            ),
        )
        self.assertEqual(
            17,
            PrivateCompanionPlugin._cfg_int({}, "_internal_future_limit", 17),
        )
        # Hidden legacy aliases are strings so blank can mean "not supplied".
        # They must not override the typed compatibility fallback used by the
        # runtime reader.
        self.assertEqual(
            800000,
            PrivateCompanionPlugin._cfg_int(
                {},
                "maintenance_token_soft_limit",
                800000,
            ),
        )
        self.assertEqual(
            220,
            PrivateCompanionPlugin._cfg_int(
                {},
                "creative_base_chars_per_hour",
                220,
                60,
                1200,
            ),
        )
        self.assertEqual(
            12,
            PrivateCompanionPlugin._cfg_int(
                {},
                "hot_trend_max_items",
                12,
                3,
                30,
            ),
        )

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
            "enable_passive_state_continuity_anchor": "humanized_state_config",
        }
        for key, expected_group in expected_groups.items():
            self.assertEqual(api._schema_group_for_key(key), expected_group, key)
            self.assertFalse(api._schema_item_for_key(key).get("invisible", False), key)

    def test_passive_continuity_anchor_is_default_off_with_dual_dependency(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        legacy = schema["enable_passive_state_continuity_anchor"]
        grouped = schema["humanized_state_config"]["items"][
            "enable_passive_state_continuity_anchor"
        ]

        self.assertFalse(legacy["default"])
        self.assertTrue(legacy["invisible"])
        self.assertFalse(grouped["default"])
        self.assertEqual(
            grouped["condition"],
            {
                "inject_passive_states": True,
                "enable_passive_state_delta_injection": True,
            },
        )
        self.assertIn("不超过 300 字", grouped["hint"])
        self.assertIn("缓存开销", grouped["hint"])

    def test_group_is_created_when_only_hidden_flat_compatibility_copy_existed(self):
        plugin = SimpleNamespace(config={})
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        api._schema_key_index_cache = None

        api._set_config_value("bot_name", "新名字")

        self.assertEqual(plugin.config["basic_config"]["bot_name"], "新名字")

    def test_proactive_intensity_preset_updates_group_flat_and_runtime_projection(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(
                config=config,
                proactive_intensity_preset="off",
                _normalize_proactive_intensity_preset=lambda value: (
                    str(value or "off").strip().lower()
                    if str(value or "off").strip().lower()
                    in {"off", "balanced", "high_private", "high_group", "live"}
                    else "off"
                ),
            )
            api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
            api.plugin = plugin
            api._schema_key_index_cache = None

            normalized = api._normalize_setting_value("proactive_intensity_preset", "live")
            api._apply_config_value("proactive_intensity_preset", normalized)

            self.assertEqual("live", config["proactive_intensity_preset"])
            self.assertEqual(
                "live",
                config["proactive_reach_config"]["proactive_intensity_preset"],
            )
            self.assertEqual("live", plugin.proactive_intensity_preset)
            self.assertEqual("live", api._runtime_settings()["proactive_intensity_preset"])
            self.assertTrue(asyncio.run(api._save_config_if_possible()))
            reloaded = AstrBotConfig(str(config_path), schema=schema)

        self.assertEqual("live", reloaded["proactive_intensity_preset"])
        self.assertEqual(
            "live",
            reloaded["proactive_reach_config"]["proactive_intensity_preset"],
        )

    def test_owner_group_projection_switches_are_page_writable_and_group_persistent(self):
        plugin = SimpleNamespace(
            config={},
            owner_group_relationship_projection=True,
            owner_group_interaction_projection=True,
        )
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        api._schema_key_index_cache = None

        self.assertIn("owner_group_relationship_projection", api._allowed_setting_keys())
        self.assertIn("owner_group_interaction_projection", api._allowed_setting_keys())

        api._apply_config_value("owner_group_relationship_projection", "关闭")
        api._apply_config_value("owner_group_interaction_projection", False)

        grouped = plugin.config["basic_config"]
        self.assertFalse(grouped["owner_group_relationship_projection"])
        self.assertFalse(grouped["owner_group_interaction_projection"])
        self.assertFalse(plugin.owner_group_relationship_projection)
        self.assertFalse(plugin.owner_group_interaction_projection)

        reloaded = json.loads(json.dumps(plugin.config, ensure_ascii=False))
        self.assertFalse(_flat_get(reloaded, "owner_group_relationship_projection", True))
        self.assertFalse(_flat_get(reloaded, "owner_group_interaction_projection", True))

    def test_global_portrait_mode_is_page_writable_and_group_persistent(self):
        plugin = SimpleNamespace(config={}, portrait_global_mode="disabled")
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        api._schema_key_index_cache = None

        self.assertIn("portrait_global_mode", api._allowed_setting_keys())
        self.assertIn("portrait_global_mode", api._runtime_settings())

        api._apply_config_value("portrait_global_mode", "learn_and_use")

        self.assertEqual("learn_and_use", plugin.portrait_global_mode)
        self.assertEqual("learn_and_use", plugin.config["basic_config"]["portrait_global_mode"])
        reloaded = json.loads(json.dumps(plugin.config, ensure_ascii=False))
        self.assertEqual("learn_and_use", _flat_get(reloaded, "portrait_global_mode"))

    def test_relationship_stage_provider_routes_are_page_writable_and_persistent(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(
                config=config,
                enable_relationship_stage_provider_routing=False,
                relationship_stage_provider_routes={},
            )
            api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
            api.plugin = plugin
            api._schema_key_index_cache = None

            self.assertIn(
                "enable_relationship_stage_provider_routing",
                api._allowed_feature_keys(),
            )
            self.assertIn(
                "relationship_stage_provider_routes",
                api._allowed_setting_keys(),
            )
            api._apply_config_value(
                "enable_relationship_stage_provider_routing",
                True,
            )
            api._apply_config_value(
                "relationship_stage_provider_routes",
                {
                    "close": "test-lab-real-gemini",
                    "intimate": "test-lab-missing-provider-fixture",
                    "unknown": "ignored-provider",
                    "distant": "invalid/provider",
                },
            )

            self.assertTrue(asyncio.run(api._save_config_if_possible()))
            reloaded = AstrBotConfig(str(config_path), schema=schema)

        self.assertTrue(
            _flat_get(
                reloaded,
                "enable_relationship_stage_provider_routing",
                False,
            )
        )
        persisted_routes = _flat_get(
            reloaded,
            "relationship_stage_provider_routes",
            {},
        )
        self.assertEqual("test-lab-real-gemini", persisted_routes["close"])
        self.assertEqual(
            "test-lab-missing-provider-fixture",
            persisted_routes["intimate"],
        )
        self.assertFalse(
            any(
                provider_id
                for stage, provider_id in persisted_routes.items()
                if stage not in {"close", "intimate"}
            )
        )
        self.assertEqual(
            {
                "close": "test-lab-real-gemini",
                "intimate": "test-lab-missing-provider-fixture",
            },
            plugin.relationship_stage_provider_routes,
        )

    def test_profile_portrait_and_relationship_defaults_are_enabled_for_new_configurations(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        items = schema["basic_config"]["items"]

        self.assertTrue(items["enable_auto_user_profile_creation"]["default"])
        self.assertEqual("learn_and_use", items["portrait_global_mode"]["default"])
        self.assertTrue(items["enable_custom_relationship_stage_policy"]["default"])

        bootstrap = (ROOT / "plugin_bootstrap.py").read_text(encoding="utf-8")
        self.assertIn(
            'self._cfg_bool(c, "enable_auto_user_profile_creation", True)',
            bootstrap,
        )
        self.assertIn(
            'self._cfg_str(c, "portrait_global_mode", "learn_and_use", "learn_and_use")',
            bootstrap,
        )
        self.assertIn(
            'self._cfg_bool(c, "enable_custom_relationship_stage_policy", True)',
            bootstrap,
        )

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

    def test_migration_fault_restores_detached_pre_migration_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {
                "daily_token_limit": 1500000,
                "basic_config": {"future_field": {"nested": [1, 2, 3]}},
            }
            expected = json.loads(json.dumps(config))

            def fail_after_mutation(root, _schema_map, _legacy_sources):
                root["basic_config"]["future_field"]["nested"].append(4)
                root["partial_migration"] = True
                raise OSError("fault injection")

            with patch.object(
                config_migration,
                "_migrate_qweather_config",
                side_effect=fail_after_mutation,
            ):
                changed = migrate_flat_config_into_schema_groups(
                    config,
                    schema_path=self._schema_file(folder),
                    save=False,
                )

        self.assertEqual(changed, 0)
        self.assertEqual(config, expected)

    def test_synchronous_save_failure_rolls_back_all_migrated_values(self):
        class FailingConfig:
            def __init__(self, data):
                self.data = data

            def save_config(self):
                raise OSError("read-only configuration store")

        with tempfile.TemporaryDirectory() as folder:
            raw = {
                "daily_token_limit": 1500000,
                "basic_config": {"future_field": "keep"},
            }
            expected = json.loads(json.dumps(raw))
            config = FailingConfig(raw)
            changed = migrate_flat_config_into_schema_groups(
                config,
                schema_path=self._schema_file(folder),
                save=True,
            )

        self.assertEqual(changed, 0)
        self.assertEqual(config.data, expected)

    def test_real_astrbot_config_snapshot_excludes_runtime_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            schema_path = self._schema_file(folder)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            config.clear()
            config.update(
                {
                    "daily_token_limit": 1500000,
                    "basic_config": {
                        "future_field": {"nested": [1, 2, 3]},
                    },
                }
            )

            changed = migrate_flat_config_into_schema_groups(
                config,
                schema_path=schema_path,
                save=True,
            )
            persisted = json.loads(config_path.read_text(encoding="utf-8-sig"))

        self.assertGreaterEqual(changed, 1)
        self.assertEqual(config["basic_config"]["daily_token_limit"], 1500000)
        self.assertEqual(
            config["basic_config"]["future_field"],
            {"nested": [1, 2, 3]},
        )
        self.assertEqual(persisted["basic_config"]["daily_token_limit"], 1500000)
        self.assertEqual(
            persisted["basic_config"]["future_field"],
            {"nested": [1, 2, 3]},
        )

    def test_forward_image_timeout_legacy_default_is_upgraded(self):
        config = {
            "forward_message_image_vision_timeout_seconds": 6.0,
            "forward_message_config": {
                "forward_message_image_vision_timeout_seconds": 6.0,
            },
        }

        changed = migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertGreater(changed, 0)
        self.assertEqual(config["forward_message_image_vision_timeout_seconds"], 60.0)
        self.assertEqual(
            config["forward_message_config"]["forward_message_image_vision_timeout_seconds"],
            60.0,
        )

    def test_forward_image_timeout_explicit_value_is_not_migrated(self):
        config = {
            "forward_message_image_vision_timeout_seconds": 180.0,
            "forward_message_config": {
                "forward_message_image_vision_timeout_seconds": 180.0,
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(config["forward_message_image_vision_timeout_seconds"], 180.0)
        self.assertEqual(
            config["forward_message_config"]["forward_message_image_vision_timeout_seconds"],
            180.0,
        )

    def test_legacy_relationship_policy_switch_is_migrated_once_to_enabled_system(self):
        config = {
            "enable_custom_relationship_stage_policy": False,
            "basic_config": {"enable_custom_relationship_stage_policy": False},
        }

        changed = migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertGreaterEqual(changed, 1)
        self.assertTrue(config["enable_custom_relationship_stage_policy"])
        self.assertTrue(config["basic_config"]["enable_custom_relationship_stage_policy"])
        self.assertEqual(1, config["_relationship_switch_semantics_version"])

        # Once the migration marker exists, an administrator's explicit
        # disable is preserved on later startup.
        config["enable_custom_relationship_stage_policy"] = False
        config["basic_config"]["enable_custom_relationship_stage_policy"] = False
        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )
        self.assertFalse(config["enable_custom_relationship_stage_policy"])
        self.assertFalse(config["basic_config"]["enable_custom_relationship_stage_policy"])

    def test_explicit_relationship_master_switch_remains_authoritative(self):
        config = {
            "enable_custom_relationship_stage_policy": True,
            "basic_config": {"enable_custom_relationship_stage_policy": True},
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertTrue(config["enable_custom_relationship_stage_policy"])
        self.assertTrue(config["basic_config"]["enable_custom_relationship_stage_policy"])

    def test_legacy_zero_command_photo_quota_is_migrated_once_to_unlimited(self):
        config = {
            "command_photo_generation_max_daily": 0,
            "photo_action_config": {"command_photo_generation_max_daily": 0},
        }

        changed = migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertGreater(changed, 0)
        self.assertEqual(-1, config["command_photo_generation_max_daily"])
        self.assertEqual(
            -1,
            config["photo_action_config"]["command_photo_generation_max_daily"],
        )
        self.assertEqual(1, config["_command_photo_quota_semantics_version"])

        # After the one-time upgrade, zero is an explicit administrator disable.
        config["command_photo_generation_max_daily"] = 0
        config["photo_action_config"]["command_photo_generation_max_daily"] = 0
        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(0, config["command_photo_generation_max_daily"])
        self.assertEqual(
            0,
            config["photo_action_config"]["command_photo_generation_max_daily"],
        )

    def test_legacy_photo_scope_values_migrate_to_independent_daily_limits(self):
        cases = (
            (
                "grouped-all",
                {
                    "photo_action_config": {
                        "photo_generation_allowed_scopes": [
                            "private_owner",
                            "private_friend",
                            "group",
                            "proactive",
                        ],
                    },
                },
                {scope: -1 for scope in PHOTO_SCOPE_LIMIT_KEYS},
            ),
            (
                "flat-subset",
                {"photo_generation_allowed_scopes": ["private_owner", "group"]},
                {
                    "private_owner": -1,
                    "private_friend": 0,
                    "group": -1,
                    "proactive": 0,
                },
            ),
            (
                "grouped-empty-keeps-explicit-disable",
                {"photo_action_config": {"photo_generation_allowed_scopes": []}},
                {scope: 0 for scope in PHOTO_SCOPE_LIMIT_KEYS},
            ),
            (
                "missing-keeps-default-open-behavior",
                {},
                {scope: -1 for scope in PHOTO_SCOPE_LIMIT_KEYS},
            ),
            (
                "delimited-string",
                {"photo_generation_allowed_scopes": "private_friend, proactive"},
                {
                    "private_owner": 0,
                    "private_friend": -1,
                    "group": 0,
                    "proactive": -1,
                },
            ),
            (
                "json-string",
                {
                    "photo_action_config": {
                        "photo_generation_allowed_scopes": '["private_owner", "proactive"]',
                    },
                },
                {
                    "private_owner": -1,
                    "private_friend": 0,
                    "group": 0,
                    "proactive": -1,
                },
            ),
        )

        for name, config, expected in cases:
            with self.subTest(name=name):
                changed = migrate_flat_config_into_schema_groups(
                    config,
                    schema_path=ROOT / "_conf_schema.json",
                    save=False,
                )

                self.assertGreater(changed, 0)
                self.assertEqual(
                    1,
                    config["_photo_generation_scope_quota_semantics_version"],
                )
                group = config["photo_action_config"]
                for scope, key in PHOTO_SCOPE_LIMIT_KEYS.items():
                    self.assertEqual(expected[scope], config[key], key)
                    self.assertEqual(expected[scope], group[key], f"grouped {key}")

    def test_existing_photo_scope_daily_limits_are_not_overwritten_by_migration(self):
        expected = {
            "photo_generation_private_owner_max_daily": 3,
            "photo_generation_private_friend_max_daily": 0,
            "photo_generation_group_max_daily": 17,
            "photo_generation_proactive_max_daily": -1,
        }
        config = {
            **expected,
            "photo_generation_allowed_scopes": [
                "private_owner",
                "private_friend",
                "group",
                "proactive",
            ],
            "photo_action_config": {
                **expected,
                "photo_generation_allowed_scopes": [
                    "private_owner",
                    "private_friend",
                    "group",
                    "proactive",
                ],
            },
        }

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(1, config["_photo_generation_scope_quota_semantics_version"])
        for key, value in expected.items():
            self.assertEqual(value, config[key], key)
            self.assertEqual(value, config["photo_action_config"][key], f"grouped {key}")

    def test_photo_scope_upgrade_migrates_after_astrbot_injects_new_defaults(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        expected = {
            "photo_generation_private_owner_max_daily": -1,
            "photo_generation_private_friend_max_daily": 0,
            "photo_generation_group_max_daily": -1,
            "photo_generation_proactive_max_daily": 0,
        }

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "photo_action_config": {
                            "photo_generation_allowed_scopes": [
                                "private_owner",
                                "group",
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config = AstrBotConfig(str(config_path), schema=schema)

            for key in PHOTO_SCOPE_LIMIT_KEYS.values():
                self.assertEqual(-1, config["photo_action_config"][key], key)

            migrate_flat_config_into_schema_groups(
                config,
                schema_path=ROOT / "_conf_schema.json",
                save=False,
            )

        self.assertEqual(1, config["_photo_generation_scope_quota_semantics_version"])
        for key, value in expected.items():
            self.assertEqual(value, config[key], key)
            self.assertEqual(value, config["photo_action_config"][key], f"grouped {key}")

    def test_photo_scope_quota_migration_marker_preserves_later_admin_values(self):
        config = {"photo_generation_allowed_scopes": ["private_owner"]}
        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        expected = {
            "photo_generation_private_owner_max_daily": 0,
            "photo_generation_private_friend_max_daily": 4,
            "photo_generation_group_max_daily": -1,
            "photo_generation_proactive_max_daily": 9,
        }
        config.update(expected)
        config["photo_action_config"].update(expected)
        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(1, config["_photo_generation_scope_quota_semantics_version"])
        for key, value in expected.items():
            self.assertEqual(value, config[key], key)
            self.assertEqual(value, config["photo_action_config"][key], f"grouped {key}")

    def test_command_photo_quota_migrates_flat_or_grouped_legacy_zero(self):
        configs = (
            ("flat", {"command_photo_generation_max_daily": 0}),
            ("grouped", {"photo_action_config": {"command_photo_generation_max_daily": 0}}),
        )

        for source, config in configs:
            with self.subTest(source=source):
                migrate_flat_config_into_schema_groups(
                    config,
                    schema_path=ROOT / "_conf_schema.json",
                    save=False,
                )
                self.assertEqual(-1, config["command_photo_generation_max_daily"])
                if source == "grouped":
                    self.assertEqual(
                        -1,
                        config["photo_action_config"]["command_photo_generation_max_daily"],
                    )
                self.assertEqual(1, config["_command_photo_quota_semantics_version"])

    def test_command_photo_quota_marks_new_semantics_without_a_legacy_value(self):
        config = {}

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertEqual(1, config["_command_photo_quota_semantics_version"])

        config["command_photo_generation_max_daily"] = 0
        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )
        self.assertEqual(0, config["command_photo_generation_max_daily"])

    def test_command_photo_quota_config_surface_uses_negative_one_for_unlimited(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        item = schema["photo_action_config"]["items"]["command_photo_generation_max_daily"]

        self.assertEqual(-1, item["default"])
        self.assertEqual(-1, item["slider"]["min"])
        self.assertIn("-1 表示不限量", item["hint"])
        self.assertIn("0 表示不允许", item["hint"])

        api = PrivateCompanionPageApi(None)
        self.assertEqual(-1, api._normalize_setting_value("command_photo_generation_max_daily", -5))
        self.assertEqual(0, api._normalize_setting_value("command_photo_generation_max_daily", 0))
        self.assertEqual(100, api._normalize_setting_value("command_photo_generation_max_daily", 200))

        scope_items = schema["photo_action_config"]["items"]
        for key in PHOTO_SCOPE_LIMIT_KEYS.values():
            scope_item = scope_items[key]
            self.assertEqual(-1, scope_item["default"], key)
            self.assertEqual(-1, scope_item["slider"]["min"], key)
            self.assertEqual(100, scope_item["slider"]["max"], key)
            self.assertEqual(-1, api._normalize_setting_value(key, -5), key)
            self.assertEqual(0, api._normalize_setting_value(key, 0), key)
            self.assertEqual(100, api._normalize_setting_value(key, 200), key)
            self.assertEqual(-1, api._normalize_setting_value(key, None), key)

        legacy_scope = scope_items["photo_generation_allowed_scopes"]
        self.assertTrue(legacy_scope["invisible"])
        marker = schema["_photo_generation_scope_quota_semantics_version"]
        self.assertTrue(marker["invisible"])
        self.assertEqual(0, marker["default"])

        scripts = [
            (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8"),
            (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8"),
        ]
        for script in scripts:
            self.assertIn(
                'keys: ["enable_user_requested_photo_generation", "photo_generation_private_owner_max_daily", "photo_generation_private_friend_max_daily", "photo_generation_group_max_daily", "command_photo_generation_max_daily", "photo_generation_proactive_max_daily"]',
                script,
            )
            self.assertNotIn('photo_generation_allowed_scopes: { type: "photo-scopes" }', script)
            self.assertIn(
                'command_photo_generation_max_daily: { type: "number", min: -1, max: 100, step: 1 }',
                script,
            )
            for key in PHOTO_SCOPE_LIMIT_KEYS.values():
                self.assertIn(
                    f'{key}: {{ type: "number", min: -1, max: 100, step: 1 }}',
                    script,
                )
            self.assertIn('placeholder: "-1（不限量）"', script)

    def test_empty_photo_scope_selection_remains_explicitly_disabled(self):
        api = PrivateCompanionPageApi(None)
        self.assertEqual([], api._normalize_setting_value("photo_generation_allowed_scopes", []))
        self.assertEqual([], api._normalize_setting_value("photo_generation_allowed_scopes", ""))
        self.assertEqual(
            ["private_owner", "group"],
            api._normalize_setting_value(
                "photo_generation_allowed_scopes",
                ["private_owner", "invalid", "group", "private_owner"],
            ),
        )

        all_scopes = ["private_owner", "private_friend", "group", "proactive"]
        self.assertEqual(all_scopes, _normalize_photo_generation_scopes(None))
        self.assertEqual([], _normalize_photo_generation_scopes([]))
        self.assertEqual(
            ["private_owner", "private_friend"],
            _normalize_photo_generation_scopes("private_owner\nprivate_friend"),
        )
        self.assertEqual(
            ["private_owner", "private_friend"],
            _normalize_photo_generation_scopes(r"private_owner\nprivate_friend"),
        )
        self.assertEqual(
            ["private_owner", "group"],
            _normalize_photo_generation_scopes('["private_owner", "group"]'),
        )
        self.assertEqual(
            ["private_owner", "group"],
            _normalize_photo_generation_scopes("private_owner,group"),
        )

        config = {
            "photo_generation_allowed_scopes": all_scopes,
            "photo_action_config": {"photo_generation_allowed_scopes": []},
        }
        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )
        self.assertEqual([], config["photo_action_config"]["photo_generation_allowed_scopes"])
        self.assertEqual([], config["photo_generation_allowed_scopes"])

    def test_removed_owner_companion_switch_stays_absent(self):
        source = (ROOT / "plugin_bootstrap.py").read_text(encoding="utf-8")
        page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")
        self.assertNotIn("owner_companion_enabled", source)
        self.assertNotIn('"owner_companion_enabled"', page_api)
        self.assertIn(
            'self.default_proactive_enabled = self._cfg_bool(c, "default_proactive_enabled", False)',
            source,
        )
        self.assertGreaterEqual(page_api.count('"proactive_private_enabled"'), 2)

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
        # Split the synthetic value so Lab packaging cannot mistake it for a credential.
        api_key = "01234567" + "89abcdef0123456789abcdef"
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

    def test_group_bot_name_wakeup_flat_value_migrates_to_group(self):
        config = {
            "enable_group_bot_name_wakeup": False,
            "group_wakeup_config": {},
        }

        changed = migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertGreaterEqual(changed, 1)
        self.assertFalse(config["enable_group_bot_name_wakeup"])
        self.assertFalse(config["group_wakeup_config"]["enable_group_bot_name_wakeup"])

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

    def test_removed_connector_keys_remain_unknown_without_activating_replacement(self):
        legacy_values = {
            "enable_jm_cosmos_integration": True,
            "enable_jm_cosmos_boredom_read": True,
            "jm_cosmos_min_interval_hours": 36,
            "jm_cosmos_max_photo_count": 48,
            "jm_cosmos_share_probability": 0.42,
            "jm_cosmos_default_keywords": "剧情,日常",
            "jm_cosmos_blocked_tags": "长篇",
            "JM_COSMOS_VISION_PROVIDER_ID": "legacy-reading-vision",
        }
        config = dict(legacy_values)
        config["legacy_compat_config"] = dict(legacy_values)

        migrate_flat_config_into_schema_groups(
            config,
            schema_path=ROOT / "_conf_schema.json",
            save=False,
        )

        self.assertNotIn("private_reading_config", config)
        self.assertFalse(
            config.get("reading_archive_config", {}).get(
                "enable_reading_archive_integration",
                False,
            )
        )
        self.assertNotIn(
            "PRIVATE_READING_VISION_PROVIDER_ID",
            config.get("model_assignment_config", {}),
        )
        # These fields no longer have executable semantics, but config
        # round-trips must not silently discard unknown data.
        for key, value in legacy_values.items():
            self.assertEqual(config[key], value)
            self.assertEqual(config["legacy_compat_config"][key], value)

        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        legacy_schema = schema["legacy_compat_config"]["items"]
        for key in legacy_values:
            self.assertNotIn(key, schema)
            self.assertNotIn(key, legacy_schema)

    def test_5100_settings_survive_real_config_save_and_reload(self):
        changed_values = {
            "external_link_share_cooldown_hours": 0,
            "enable_creative_cover_generation": True,
            "enable_proactive_chat_integration": False,
            "proactive_chat_bridge_review_mode": "follow_proactive_review",
            "proactive_chat_bridge_collision_window_seconds": 150,
            "proactive_generation_history_limit": 42,
            "proactive_history_context_mode": "expanded",
            "proactive_history_recent_raw_count": 12,
            "proactive_history_max_chars": 8000,
            "proactive_review_history_limit": 64,
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
            "proactive_generation_history_limit": 20,
            "proactive_history_context_mode": "compact",
            "proactive_history_recent_raw_count": 8,
            "proactive_history_max_chars": 6000,
            "proactive_review_history_limit": 30,
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

    def test_photo_scope_daily_limits_survive_real_config_save_and_reload(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        expected = {
            "photo_generation_private_owner_max_daily": 8,
            "photo_generation_private_friend_max_daily": 0,
            "photo_generation_group_max_daily": 25,
            "photo_generation_proactive_max_daily": 100,
        }

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)

            for key, value in expected.items():
                normalized = api._normalize_setting_value(key, value)
                api._apply_config_value(key, normalized, expected)
                self.assertEqual(normalized, getattr(plugin, key), key)
            self.assertTrue(asyncio.run(api._save_config_if_possible()))

            reloaded = AstrBotConfig(str(config_path), schema=schema)

        for key, value in expected.items():
            self.assertEqual(value, reloaded["photo_action_config"][key], key)
            self.assertEqual(value, _flat_get(reloaded, key), f"effective {key}")

    def test_migration_markers_survive_real_config_save_and_reload(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        markers = (
            "_command_photo_quota_semantics_version",
            "_photo_generation_scope_quota_semantics_version",
        )

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            for marker in markers:
                config[marker] = 1
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)

            self.assertTrue(asyncio.run(api._save_config_if_possible()))
            reloaded = AstrBotConfig(str(config_path), schema=schema)

        for marker in markers:
            self.assertEqual(1, reloaded[marker], marker)

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
