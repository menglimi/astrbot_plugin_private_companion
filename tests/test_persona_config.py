from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.persona_config import (
    PERSONA_SETTINGS_SCHEMA_VERSION,
    build_scope_manifest,
    copy_persona_settings,
    create_persona_settings,
    default_persona_settings,
    detach_persona_settings,
    discover_grouped_schema_leaves,
    load_schema,
    migrate_persona_profile,
    resolve_effective_settings,
    resolve_persona_setting,
)


ROOT = Path(__file__).resolve().parents[1]


class PersonaConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_schema(ROOT / "_conf_schema.json")
        cls.manifest = build_scope_manifest(cls.schema)

    def test_manifest_covers_canonical_grouped_942_leaves(self) -> None:
        self.assertEqual(4, PERSONA_SETTINGS_SCHEMA_VERSION)
        leaves = discover_grouped_schema_leaves(self.schema)
        self.assertEqual(len(leaves), 942)
        self.assertEqual(set(leaves), set(self.manifest))
        required_fields = {
            "scope",
            "cloneable",
            "inherit_primary",
            "identity",
            "required",
            "new_key_default",
            "sensitive",
            "hot_apply",
            "restart_required",
            "side_effect",
            "ui_location",
        }
        for key, entry in self.manifest.items():
            self.assertEqual(key, entry["key"])
            self.assertTrue(required_fields.issubset(entry), key)
        self.assertEqual(self.manifest["bot_name"]["scope"], "persona")
        self.assertTrue(self.manifest["bot_name"]["identity"])
        self.assertFalse(self.manifest["bot_name"]["inherit_primary"])
        prompt_entry = self.manifest["llm_controlled_segmenting_prompt"]
        self.assertEqual("persona", prompt_entry["scope"])
        self.assertTrue(prompt_entry["cloneable"])
        self.assertEqual("", prompt_entry["default"])

    def test_segmenting_prompt_distinguishes_follow_empty_and_custom(self) -> None:
        primary = {"llm_controlled_segmenting_prompt": "主人格提示"}
        self.assertEqual(
            "",
            default_persona_settings(
                self.schema,
                manifest=self.manifest,
            )["llm_controlled_segmenting_prompt"],
        )
        self.assertEqual(
            "主人格提示",
            resolve_persona_setting(
                "llm_controlled_segmenting_prompt",
                {},
                primary,
                manifest=self.manifest,
            ),
        )
        self.assertEqual(
            "",
            resolve_persona_setting(
                "llm_controlled_segmenting_prompt",
                {"llm_controlled_segmenting_prompt": ""},
                primary,
                manifest=self.manifest,
            ),
        )
        self.assertEqual(self.manifest["storage_backend"]["scope"], "common")
        self.assertEqual(self.manifest["plugin_specific_persona_id"]["scope"], "common")
        self.assertFalse(self.manifest["plugin_specific_persona_id"]["cloneable"])
        self.assertFalse(self.manifest["plugin_specific_persona_id"]["inherit_primary"])
        self.assertFalse(self.manifest["plugin_specific_persona_id"]["identity"])
        self.assertNotIn("multi_persona_primary_id", self.manifest)
        self.assertEqual(self.manifest["quiet_hours"]["scope"], "persona")
        self.assertEqual(self.manifest["enable_group_bot_name_wakeup"]["scope"], "persona")
        self.assertTrue(self.manifest["enable_group_bot_name_wakeup"]["default"])
        self.assertEqual(self.manifest["enable_qq_official_segmented_reply"]["scope"], "persona")
        self.assertFalse(self.manifest["enable_qq_official_segmented_reply"]["default"])
        self.assertEqual(self.manifest["intercept_astrbot_group_context"]["scope"], "persona")
        self.assertTrue(self.manifest["intercept_astrbot_group_context"]["default"])
        self.assertEqual(
            self.manifest["enable_relationship_stage_provider_routing"]["scope"],
            "common",
        )
        for stage_key in (
            "deeply_distant",
            "strongly_distant",
            "distant",
            "acquaintance",
            "familiar",
            "close",
            "intimate",
            "deeply_bonded",
            "owner_exclusive",
        ):
            self.assertEqual(self.manifest[stage_key]["scope"], "common")
        self.assertEqual(self.manifest["enable_group_history_injection"]["scope"], "persona")
        self.assertTrue(self.manifest["enable_group_history_injection"]["default"])

    def test_missing_setting_follows_primary_but_falsy_values_are_explicit(self) -> None:
        primary = {
            "quiet_hours": "23:00-08:30",
            "enable_proactive_burst": True,
            "max_daily_messages": 8,
            "target_user_ids": ["primary"],
        }
        self.assertEqual(
            resolve_persona_setting("quiet_hours", {}, primary, manifest=self.manifest),
            "23:00-08:30",
        )
        own = {
            "quiet_hours": "",
            "enable_proactive_burst": False,
            "max_daily_messages": 0,
        }
        for key, expected in own.items():
            self.assertEqual(
                resolve_persona_setting(key, own, primary, manifest=self.manifest),
                expected,
            )
        # Common keys never become persona overrides, even when an old profile
        # contains one accidentally.
        self.assertEqual(
            resolve_persona_setting(
                "storage_backend",
                {"storage_backend": "sqlite"},
                {"storage_backend": "json"},
                manifest=self.manifest,
            ),
            "json",
        )
        self.assertIsNone(
            resolve_persona_setting(
                "unknown_legacy_key",
                {"unknown_legacy_key": "persona"},
                {"unknown_legacy_key": "primary"},
                manifest=self.manifest,
            )
        )

    def test_grouped_primary_value_precedes_legacy_flat_alias(self) -> None:
        primary = {
            "basic_config": {"quiet_hours": "grouped"},
            "quiet_hours": "legacy",
        }
        self.assertEqual(
            resolve_persona_setting("quiet_hours", {}, primary, manifest=self.manifest),
            "grouped",
        )

    def test_persona_safety_values_can_only_tighten_primary_policy(self) -> None:
        primary = {
            "enable_relationship_content_tiers": False,
            "enable_group_privacy_guard": True,
        }
        attempted_relaxation = {
            "enable_relationship_content_tiers": True,
            "enable_group_privacy_guard": False,
        }
        self.assertFalse(
            resolve_persona_setting(
                "enable_relationship_content_tiers",
                attempted_relaxation,
                primary,
                manifest=self.manifest,
            )
        )
        self.assertTrue(
            resolve_persona_setting(
                "enable_group_privacy_guard",
                attempted_relaxation,
                primary,
                manifest=self.manifest,
            )
        )
        stricter = {
            "enable_relationship_content_tiers": False,
            "enable_group_privacy_guard": True,
        }
        permissive_primary = {
            "enable_relationship_content_tiers": True,
            "enable_group_privacy_guard": False,
        }
        self.assertFalse(
            resolve_persona_setting(
                "enable_relationship_content_tiers",
                stricter,
                permissive_primary,
                manifest=self.manifest,
            )
        )
        self.assertTrue(
            resolve_persona_setting(
                "enable_group_privacy_guard",
                stricter,
                permissive_primary,
                manifest=self.manifest,
            )
        )

    def test_raw_copy_filters_common_and_identity_without_resolving(self) -> None:
        source = {
            "bot_name": "来源人格",
            "quiet_hours": "",
            "enable_proactive_burst": False,
            "storage_backend": "sqlite",
            "plugin_specific_persona_id": "source-astrbot-id",
            "unknown_legacy_key": 123,
        }
        copied = copy_persona_settings(source, bot_name="新人格", manifest=self.manifest)
        self.assertEqual(copied["bot_name"], "新人格")
        self.assertEqual(copied["quiet_hours"], "")
        self.assertFalse(copied["enable_proactive_burst"])
        self.assertNotIn("storage_backend", copied)
        self.assertNotIn("plugin_specific_persona_id", copied)
        self.assertNotIn("unknown_legacy_key", copied)

    def test_default_creation_only_contains_persona_scope(self) -> None:
        settings = default_persona_settings(manifest=self.manifest)
        self.assertGreater(len(settings), 500)
        self.assertNotIn("storage_backend", settings)
        self.assertNotIn("multi_persona_ids", settings)
        self.assertIn("bot_name", settings)
        self.assertEqual(settings["bot_name"], "小星")
        self.assertTrue(settings["enable_group_history_injection"])
        created = create_persona_settings(
            "defaults",
            bot_name="默认人格",
            manifest=self.manifest,
        )
        self.assertEqual(created["bot_name"], "默认人格")

    def test_detach_materializes_effective_values_and_preserves_identity(self) -> None:
        primary = {
            "quiet_hours": "23:00-08:30",
            "enable_proactive_burst": True,
            "max_daily_messages": 8,
        }
        own = {"bot_name": "独立人格", "quiet_hours": "01:00-09:00"}
        detached = detach_persona_settings(own, primary, manifest=self.manifest)
        self.assertEqual(detached["bot_name"], "独立人格")
        self.assertEqual(detached["quiet_hours"], "01:00-09:00")
        self.assertEqual(detached["enable_proactive_burst"], True)
        self.assertEqual(detached["max_daily_messages"], 8)
        self.assertNotIn("storage_backend", detached)

    def test_legacy_sparse_migration_does_not_fill_historical_keys(self) -> None:
        legacy = {
            "users": {"u1": {"name": "用户"}},
            "persona_settings": None,
        }
        migrated = migrate_persona_profile(
            legacy,
            manifest=self.manifest,
            legacy_bot_name="旧人格",
        )
        self.assertEqual(migrated["users"], legacy["users"])
        self.assertEqual(
            migrated["persona_settings"],
            {
                "bot_name": "旧人格",
                "enable_group_bot_name_wakeup": True,
                "enable_qq_official_segmented_reply": False,
                "intercept_astrbot_group_context": True,
                "group_scene_recent_max_chars": 4000,
                "enable_llm_controlled_segmenting": False,
                "enable_segmented_plugin_rules": True,
            },
        )
        self.assertEqual(
            migrated["persona_settings_schema_version"], PERSONA_SETTINGS_SCHEMA_VERSION
        )
        self.assertEqual(migrated["persona_settings_revision"], 0)
        # A future version explicitly lists new keys; only those keys are
        # materialized, while old missing keys retain follow-primary semantics.
        migrated_v5 = migrate_persona_profile(
            migrated,
            manifest=self.manifest,
            target_version=5,
            new_keys_by_version={5: ["quiet_hours"]},
        )
        self.assertEqual(
            migrated_v5["persona_settings"]["quiet_hours"],
            self.manifest["quiet_hours"]["new_key_default"],
        )
        self.assertNotIn("max_daily_messages", migrated_v5["persona_settings"])

    def test_v1_profile_materializes_new_defaults_during_current_migration(self) -> None:
        migrated = migrate_persona_profile(
            {
                "persona_settings": {"bot_name": "次人格"},
                "persona_settings_schema_version": 1,
                "persona_settings_revision": 4,
            },
            manifest=self.manifest,
        )

        self.assertTrue(migrated["persona_settings"]["enable_group_bot_name_wakeup"])
        self.assertFalse(migrated["persona_settings"]["enable_qq_official_segmented_reply"])
        self.assertTrue(migrated["persona_settings"]["intercept_astrbot_group_context"])
        self.assertEqual(4000, migrated["persona_settings"]["group_scene_recent_max_chars"])
        self.assertFalse(migrated["persona_settings"]["enable_llm_controlled_segmenting"])
        self.assertTrue(migrated["persona_settings"]["enable_segmented_plugin_rules"])
        self.assertEqual(PERSONA_SETTINGS_SCHEMA_VERSION, migrated["persona_settings_schema_version"])
        self.assertEqual(4, migrated["persona_settings_revision"])

    def test_v2_profile_materializes_qq_official_segmented_opt_in_as_disabled(self) -> None:
        migrated = migrate_persona_profile(
            {
                "persona_settings": {
                    "bot_name": "次人格",
                    "enable_group_bot_name_wakeup": True,
                },
                "persona_settings_schema_version": 2,
                "persona_settings_revision": 5,
            },
            manifest=self.manifest,
        )

        self.assertFalse(migrated["persona_settings"]["enable_qq_official_segmented_reply"])
        self.assertEqual(4000, migrated["persona_settings"]["group_scene_recent_max_chars"])
        self.assertFalse(migrated["persona_settings"]["enable_llm_controlled_segmenting"])
        self.assertTrue(migrated["persona_settings"]["enable_segmented_plugin_rules"])
        self.assertEqual(PERSONA_SETTINGS_SCHEMA_VERSION, migrated["persona_settings_schema_version"])
        self.assertEqual(5, migrated["persona_settings_revision"])

    def test_v3_profile_materializes_all_v4_persona_defaults(self) -> None:
        migrated = migrate_persona_profile(
            {
                "persona_settings": {"bot_name": "次人格"},
                "persona_settings_schema_version": 3,
                "persona_settings_revision": 6,
            },
            manifest=self.manifest,
        )

        self.assertEqual(4000, migrated["persona_settings"]["group_scene_recent_max_chars"])
        self.assertFalse(migrated["persona_settings"]["enable_llm_controlled_segmenting"])
        self.assertTrue(migrated["persona_settings"]["enable_segmented_plugin_rules"])
        self.assertEqual(4, migrated["persona_settings_schema_version"])
        self.assertEqual(6, migrated["persona_settings_revision"])

    def test_existing_empty_persona_settings_gets_identity_and_new_v2_key(self) -> None:
        migrated = migrate_persona_profile(
            {"users": {}, "persona_settings": {}},
            manifest=self.manifest,
            persona_id="existing-alt",
        )
        self.assertEqual(
            migrated["persona_settings"],
            {
                "bot_name": "existing-alt",
                "enable_group_bot_name_wakeup": True,
                "enable_qq_official_segmented_reply": False,
                "intercept_astrbot_group_context": True,
                "group_scene_recent_max_chars": 4000,
                "enable_llm_controlled_segmenting": False,
                "enable_segmented_plugin_rules": True,
            },
        )
        self.assertEqual(
            migrated["persona_settings_schema_version"],
            PERSONA_SETTINGS_SCHEMA_VERSION,
        )

    def test_migration_rejects_invalid_settings_without_mutating_input(self) -> None:
        legacy = {"users": {"u1": {}}, "persona_settings": ["invalid"]}
        snapshot = copy.deepcopy(legacy)
        with self.assertRaises(ValueError):
            migrate_persona_profile(legacy, manifest=self.manifest)
        self.assertEqual(legacy, snapshot)


if __name__ == "__main__":
    unittest.main()
