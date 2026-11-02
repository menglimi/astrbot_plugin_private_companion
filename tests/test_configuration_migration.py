"""Focused contracts for the audited, broad V1 configuration converter."""

from __future__ import annotations

import csv
import json
from contextlib import closing
from pathlib import Path
import unittest

from companion_v2.configuration.models import ConfigLayer
from companion_v2.configuration.registry import FieldDefinition, FieldRegistry, ValueKind
from companion_v2.legacy.configuration_migration import (
    configuration_audit_sources,
    configuration_rules,
    configuration_exclusions,
    convert_account_configuration,
    convert_configuration,
    convert_plugin_configuration,
    convert_semantic_configuration,
    unmapped_configuration_differences,
    plugin_configuration_defaults,
    plugin_configuration_rules,
    semantic_configuration_rules,
)


def rule(source, source_type, target, confidence="high"):
    return {"source": source, "source_type": source_type, "target": target, "confidence": confidence}


def plugin_rule(source, source_type, target, transform, contract, confidence="high"):
    return {"source": source, "source_type": source_type, "target": target,
            "transform": transform, "contract": contract, "confidence": confidence}


def registry(field_id, kind, default, **bounds):
    group = field_id.rpartition(".")[0]
    return FieldRegistry(version="configuration-migration.test", fields=(
        FieldDefinition(field_id, group, group, kind, default, **bounds),
    ))


def segmentation_registry():
    group = "conversation.presentation"
    definitions = (
        ("enabled", ValueKind.BOOLEAN, False, {}),
        ("threshold", ValueKind.INTEGER, 500, {"minimum": 1, "maximum": 65536}),
        ("min_segment_chars", ValueKind.INTEGER, 5, {"minimum": 1, "maximum": 65536}),
        ("max_segments", ValueKind.INTEGER, 5, {"minimum": 1, "maximum": 64}),
        ("part_gap_mode", ValueKind.TEXT, "fixed", {}),
        ("part_gap_ms", ValueKind.INTEGER, 0, {"minimum": 0, "maximum": 60000}),
        ("part_gap_min_ms", ValueKind.INTEGER, 1500, {"minimum": 100, "maximum": 60000}),
        ("part_gap_max_ms", ValueKind.INTEGER, 3500, {"minimum": 100, "maximum": 60000}),
        ("part_gap_log_base", ValueKind.WEIGHT, 1.8, {"minimum": 1.1, "maximum": 5.0}),
    )
    return FieldRegistry(version="configuration-migration.segmentation", fields=tuple(
        FieldDefinition(f"{group}.{name}", group, group, kind, default, **bounds)
        for name, kind, default, bounds in definitions
    ))


class RuleTableTests(unittest.TestCase):
    def _ledger_rows(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "V1_V2_CONFIG_RESOLVED_20260928.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_generated_table_covers_the_audited_registry_mappings(self):
        rules = configuration_rules()

        self.assertEqual(len(rules), 371)
        self.assertEqual(sum(item["confidence"] == "high" for item in rules), 304)
        self.assertEqual(sum(item["confidence"] == "medium" for item in rules), 67)
        self.assertEqual(len({item["source"] for item in rules}), len(rules))

        expected = {row["v1_key"] for row in self._ledger_rows()
                    if row["resolved_kind"] == "registry"
                    and row["verified_verdict"] in {"already-in-v2", "renamed-equivalent"}
                    and row["verified_confidence"] in {"high", "medium"}}
        specialized = {
            "basic_config.enable_environment_perception",
            "legacy_compat_config.segmented_proactive_image_strategy",
            "legacy_compat_config.segmented_proactive_voice_strategy",
            "legacy_compat_config.segmented_proactive_at_strategy",
            "message_debounce_config.enable_message_debounce",
            "message_debounce_config.enable_semantic_message_debounce",
            "message_debounce_config.text_message_debounce_seconds",
            "message_debounce_config.semantic_message_debounce_seconds",
            "message_debounce_config.text_message_debounce_max_wait_seconds",
        }
        self.assertEqual({item["source"] for item in rules}, (expected | {f"humanized_state_config.advanced_cycle_{phase}_{suffix}"
                             for phase in ("follicular", "pre_ovulation", "ovulation", "luteal")
                             for suffix in ("days", "prompt", "mood", "energy")}) - specialized
                         - {"voice_action_config.tts_message_scope", "basic_config.owner_exclusive_proactive_limit"})
        self.assertEqual({item["source"] for item in configuration_exclusions()}, {
            "emotion_relationship_config.enable_smart_silence",
            "basic_config.owner_exclusive_proactive_limit",
            "voice_action_config.tts_message_scope",
        })

    def test_group_topic_thread_settings_migrate_to_the_group_window(self):
        group = "conversation.group_participation"
        fields = FieldRegistry(version="configuration-migration.group-topics", fields=(
            FieldDefinition(group + ".enable_group_topic_threads", group, group,
                            ValueKind.BOOLEAN, True),
            FieldDefinition(group + ".group_topic_thread_max", group, group,
                            ValueKind.INTEGER, 12, minimum=3, maximum=40),
        ))
        sources = {"group_slang_topic_config": {"enable_group_topic_threads": False},
                   "relay_bridge_config": {"max_group_topic_threads": 8}}
        result = convert_configuration(sources, registry=fields, rules=configuration_rules())

        self.assertEqual(result.changes, {
            group + ".enable_group_topic_threads": False,
            group + ".group_topic_thread_max": 8,
        })

    def test_audit_inventory_covers_every_ledger_source_once(self):
        sources = configuration_audit_sources()
        ledger = {row["v1_key"] for row in self._ledger_rows()}
        self.assertEqual(len(sources), len(ledger))
        self.assertEqual({item["source"] for item in sources}, ledger)
        self.assertEqual(len({item["source"] for item in sources}), len(sources))

    def test_native_image_api_queue_is_audited_as_v2_owned(self):
        by_source = {item["source"]: item for item in configuration_audit_sources()}
        endpoints = by_source["photo_action_config.external_image_api_endpoints"]
        self.assertEqual(endpoints["resolved_kind"], "external")
        self.assertEqual(endpoints["target"], "image.output.endpoints")
        self.assertEqual(endpoints["verdict"], "implemented-elsewhere")
        self.assertIn("V2 原生 Image 拓展", endpoints["summary"])

        headers = by_source["photo_action_config.external_image_api_custom_headers"]
        self.assertEqual(headers["verdict"], "implemented-elsewhere")
        self.assertEqual(headers["target"], "image.output.endpoints[].custom_headers")
        self.assertEqual(by_source["photo_action_config.backup_external_image_api_custom_headers"]["target"],
                         "image.output.endpoints[].custom_headers")
        self.assertEqual(by_source["photo_action_config.external_image_download_proxy"]["target"],
                         "image.output.network.download_proxy")
        self.assertEqual(by_source["photo_action_config.external_image_download_use_environment_proxy"]["target"],
                         "image.output.network.use_environment_proxy")

    def test_removed_relationship_proactive_limit_is_deliberately_not_migrated(self):
        source = "basic_config.owner_exclusive_proactive_limit"
        self.assertNotIn(source, {item["source"] for item in configuration_rules()})
        exclusion = next(item for item in configuration_exclusions() if item["source"] == source)
        self.assertIn("关系级额度", exclusion["reason"])
        audit = next(item for item in configuration_audit_sources() if item["source"] == source)
        self.assertEqual((audit["target"], audit["verdict"]), ("", "not-migrated-by-design"))

    def test_new_image_and_mood_fields_have_audited_targets(self):
        by_source = {item["source"]: item for item in configuration_rules()}
        self.assertEqual(by_source["message_debounce_config.enable_private_image_self_recognition"]["target"],
                         "conversation.presentation.enable_private_image_self_recognition")
        self.assertEqual(by_source["group_observation_config.enable_group_image_understanding"]["target"],
                         "conversation.presentation.image_understanding_group_enabled")
        self.assertEqual(by_source["experimental_motivation_config.enable_emotion_simulation"]["target"],
                         "role.state.mood_enabled")
        self.assertNotIn("emotion_relationship_config.enable_smart_silence", by_source)

    def test_nested_legacy_paths_are_read_and_reported(self):
        source = "basic_config.relationship_stage_provider_routes.close"
        differences = unmapped_configuration_differences({
            "basic_config": {"relationship_stage_provider_routes": {"close": "legacy-provider"}},
        })
        item = next(item for item in differences if item.source_key == source)
        self.assertEqual(item.kind, "not_migrated")
        self.assertEqual(item.field_id, source)

    def test_recent_configuration_mappings_have_their_narrow_v2_targets(self):
        registry_rules = {item["source"]: item for item in configuration_rules()}
        self.assertEqual(registry_rules["basic_config.enable_holiday_perception"]["target"],
                         "conversation.perception.holiday_enabled")
        self.assertEqual(registry_rules["basic_config.holiday_country"]["target"],
                         "conversation.perception.holiday_country")
        semantic_rules = {item["source"]: item for item in semantic_configuration_rules()}
        self.assertEqual(semantic_rules["group_slang_topic_config.enable_group_slang_web_search"]["target"],
                         "semantic.slang_web_search_enabled")

    def test_holiday_switch_and_region_are_applied_to_live_perception_fields(self):
        from companion_v2.conversation.perception_settings import configuration_fields

        fields = FieldRegistry(version="migration-perception", fields=configuration_fields())
        rules = tuple(item for item in configuration_rules()
                      if item["source"] in {
                          "basic_config.enable_holiday_perception", "basic_config.holiday_country"})
        result = convert_configuration({"basic_config": {
            "enable_holiday_perception": False, "holiday_country": "WEEKEND_ONLY",
        }}, registry=fields, rules=rules)

        self.assertEqual(result.changes, {
            "conversation.perception.holiday_enabled": False,
            "conversation.perception.holiday_country": "WEEKEND_ONLY",
        })

    def test_environment_master_switch_preserves_v1_disabled_behavior(self):
        from companion_v2.conversation.perception_settings import configuration_fields

        fields = FieldRegistry(version="migration-perception-master", fields=configuration_fields())
        result = convert_configuration({"basic_config": {
            "enable_environment_perception": False,
            "enable_platform_perception": True,
            "enable_model_perception": True,
        }}, registry=fields)

        self.assertEqual({
            target: result.changes[target]
            for target in (
                "conversation.perception.calendar_enabled",
                "conversation.perception.platform_enabled",
                "conversation.perception.model_route_enabled",
                "conversation.perception.configured_modalities_enabled",
            )
        }, {
            "conversation.perception.calendar_enabled": False,
            "conversation.perception.platform_enabled": False,
            "conversation.perception.model_route_enabled": False,
            "conversation.perception.configured_modalities_enabled": False,
        })
        self.assertEqual(
            {item.kind for item in result.differences if item.source_key ==
             "basic_config.enable_environment_perception"},
            {"inactive_source"},
        )

    def test_environment_master_switch_keeps_v2_child_preferences_when_enabled(self):
        from companion_v2.conversation.perception_settings import configuration_fields

        fields = FieldRegistry(version="migration-perception-master-enabled", fields=configuration_fields())
        result = convert_configuration({"basic_config": {
            "enable_environment_perception": True,
            "enable_platform_perception": False,
            "enable_model_perception": False,
        }}, registry=fields)

        self.assertEqual(result.changes["conversation.perception.platform_enabled"], False)
        self.assertEqual(result.changes["conversation.perception.model_route_enabled"], False)
        self.assertNotIn("conversation.perception.calendar_enabled", result.changes)
        self.assertEqual(result.changes["conversation.perception.configured_modalities_enabled"], False)

    def test_model_perception_switch_covers_the_split_modalities_field(self):
        from companion_v2.conversation.perception_settings import configuration_fields

        fields = FieldRegistry(version="migration-model-perception", fields=configuration_fields())
        result = convert_configuration({"basic_config": {
            "enable_model_perception": False,
        }}, registry=fields)

        self.assertEqual(result.changes["conversation.perception.model_route_enabled"], False)
        self.assertEqual(result.changes["conversation.perception.configured_modalities_enabled"], False)

    def test_audit_differences_explain_unmapped_values_without_exposing_them(self):
        config = {"forward_message_config": {"forward_message_image_vision_timeout_seconds": 37},
                  "reactive_poke_config": {"reactive_poke_back_prompts": ["private prompt"]},
                  "emotion_relationship_config": {"enable_smart_silence": True}}
        differences = unmapped_configuration_differences(config)
        by_source = {item.source_key: item for item in differences}
        expected = {
            "forward_message_config.forward_message_image_vision_timeout_seconds",
            "reactive_poke_config.reactive_poke_back_prompts",
            "emotion_relationship_config.enable_smart_silence",
        }
        self.assertEqual(set(by_source), expected)
        self.assertEqual(by_source["emotion_relationship_config.enable_smart_silence"].kind, "not_migrated")
        self.assertIn("独立阻塞步骤", by_source[
            "forward_message_config.forward_message_image_vision_timeout_seconds"].summary)
        self.assertNotIn("private prompt", json.dumps([item.document() for item in differences], ensure_ascii=False))

    def test_partial_image_and_state_mappings_are_explained(self):
        image = next(item for item in configuration_rules()
                     if item["source"] == "message_debounce_config.enable_private_image_self_recognition")
        state = next(item for item in configuration_rules()
                     if item["source"] == "humanized_state_config.enable_humanized_states")
        image_result = convert_configuration(
            {"message_debounce_config": {"enable_private_image_self_recognition": False}},
            registry=registry(image["target"], ValueKind.BOOLEAN, True), rules=(image,),
        )
        state_result = convert_configuration(
            {"humanized_state_config": {"enable_humanized_states": False}},
            registry=registry(state["target"], ValueKind.BOOLEAN, True), rules=(state,),
        )

        self.assertFalse(image_result.changes[image["target"]])
        self.assertIn("partial_mapping", {item.kind for item in image_result.differences})
        self.assertFalse(state_result.changes[state["target"]])
        self.assertIn("partial_mapping", {item.kind for item in state_result.differences})

    def test_recent_targets_exist_in_the_live_domain_field_contracts(self):
        from companion_v2.conversation.reply_settings import configuration_fields as reply_fields
        from companion_v2.conversation.segmentation import SegmentationPolicy
        from companion_v2.role_work.state.configuration import configuration_fields as state_fields

        runtime_registry = FieldRegistry(version="configuration-migration.live-fields",
            fields=(*reply_fields(SegmentationPolicy(enabled=False), True, 1.5), *state_fields()))
        rules = {item["source"]: item for item in configuration_rules()}
        sources = (
            "message_debounce_config.enable_private_image_self_recognition",
            "group_observation_config.enable_group_image_understanding",
            "experimental_motivation_config.enable_emotion_simulation",
        )
        config = {
            "message_debounce_config": {"enable_private_image_self_recognition": False},
            "group_observation_config": {"enable_group_image_understanding": False},
            "experimental_motivation_config": {"enable_emotion_simulation": True},
        }
        migration = convert_configuration(config, registry=runtime_registry,
            rules=tuple(rules[source] for source in sources))

        self.assertEqual(migration.changes, {
            "conversation.presentation.enable_private_image_self_recognition": False,
            "conversation.presentation.image_understanding_group_enabled": False,
            "role.state.mood_enabled": True,
        })
        for field_id, value in migration.changes.items():
            self.assertEqual(runtime_registry.fields[field_id].validate(value), value)

    def test_plugin_rules_point_to_schema_fields_with_explicit_contracts(self):
        rules = plugin_configuration_rules()
        schema = json.loads((Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(encoding="utf-8"))

        self.assertEqual(len(rules), 48)
        for rule in rules:
            section, _, name = rule["target"].partition(".")
            self.assertIn(name, schema[section]["items"], rule["target"])
            self.assertEqual(rule["contract"]["type"], schema[section]["items"][name]["type"])

    def test_tts_message_scope_is_reported_without_changing_the_v2_proactive_voice_switch(self):
        source = "voice_action_config.tts_message_scope"
        migration = convert_configuration({
            "voice_action_config": {"tts_message_scope": "replies_and_proactive"},
        }, registry=registry("proactive.contact.voice_action_enabled", ValueKind.BOOLEAN, False))
        self.assertEqual(migration.changes, {})
        self.assertEqual(len(migration.differences), 1)
        self.assertEqual(migration.differences[0].kind, "not_migrated")
        self.assertEqual(migration.differences[0].source_key, source)
        self.assertIn("主动语音使用独立授权策略", migration.differences[0].summary)

    def test_weather_context_is_reserved_for_the_weather_converter(self):
        migration = convert_configuration({
            "weather_config": {"enable_weather_context": True},
        }, registry=registry("proactive.contact.weather_checkin_enabled", ValueKind.BOOLEAN, False))
        self.assertNotIn("proactive.contact.weather_checkin_enabled", migration.changes)

    def test_semantic_rules_point_to_store_settings_with_explicit_contracts(self):
        from companion_v2.storage.semantic import DEFAULTS, INTEGER_LIMITS, PROBABILITY_FIELDS, SETTING_OPTIONS

        rules = semantic_configuration_rules()
        self.assertEqual(len(rules), 18)
        expected = {row["v1_key"] for row in self._ledger_rows()
                    if row["resolved_kind"] == "semantic-config"}
        self.assertEqual({item["source"] for item in rules}, expected)
        for rule in rules:
            group, _, field = rule["target"].partition(".")
            self.assertEqual(group, "semantic")
            self.assertIn(field, DEFAULTS)
            self.assertEqual(rule["contract"]["default"], DEFAULTS[field])
            if field in INTEGER_LIMITS:
                self.assertEqual((rule["contract"]["minimum"], rule["contract"]["maximum"]),
                                 INTEGER_LIMITS[field])
            if field in PROBABILITY_FIELDS:
                self.assertEqual((rule["contract"]["minimum"], rule["contract"]["maximum"]), (0, 1))
            if field in SETTING_OPTIONS:
                self.assertEqual(tuple(rule["contract"]["options"]), SETTING_OPTIONS[field])

    def test_plugin_mapping_exclusions_require_deliberate_reclassification(self):
        eligible = {row["v1_key"] for row in self._ledger_rows()
                    if row["resolved_kind"] == "plugin-config"
                    and row["verified_verdict"] in {"already-in-v2", "renamed-equivalent"}
                    and row["verified_confidence"] in {"high", "medium"}}
        actual = {item["source"] for item in plugin_configuration_rules()} - {"model_assignment_config."+name for name in ("enable_llm_streaming", "model_replacement_rules", "enable_sensitive_model_replacement", "SENSITIVE_REPLACEMENT_PROVIDER_ID")}
        account_configuration = {
            "basic_config.bot_scope_mode",
            "basic_config.bot_scope_ids",
            "basic_config.plugin_specific_persona_id",
            "basic_config.enable_multi_persona_mode",
            "basic_config.multi_persona_ids",
            "basic_config.target_user_ids",
            "group_observation_config.enable_group_companion",
            "group_observation_config.group_whitelist_ids",
            "legacy_compat_config.target_group_ids",
        }
        manual_review = {
            "model_fallback_overrides",
            "model_token_limit_overrides",
            "task_prompt_overrides",
        }

        self.assertEqual(eligible - actual, manual_review | account_configuration)
        self.assertIn("forward_message_config.forward_message_max_messages", actual)

    def test_account_migration_rules_cover_the_audited_identity_settings(self):
        eligible = {row["v1_key"] for row in self._ledger_rows()
                    if row["resolved_kind"] == "plugin-config"
                    and row["verified_verdict"] in {"already-in-v2", "renamed-equivalent"}
                    and row["verified_confidence"] in {"high", "medium"}}
        handled = {
            "basic_config.bot_scope_mode", "basic_config.bot_scope_ids",
            "basic_config.plugin_specific_persona_id", "basic_config.enable_multi_persona_mode",
            "basic_config.multi_persona_ids", "basic_config.target_user_ids",
            "group_observation_config.enable_group_companion",
            "group_observation_config.group_whitelist_ids", "legacy_compat_config.target_group_ids",
        }

        self.assertTrue(handled.issubset(eligible))


class BehaviorConfigurationMigrationTests(unittest.TestCase):
    def test_migrated_diary_enables_its_runtime_without_enabling_auto_sleep(self):
        result = self.convert({"schedule_detail_config": {"enable_daily_diary": True},
                               "legacy_compat_config": {"timezone": ""}})
        self.assertTrue(result.changes["role.life.diary_enabled"])
        self.assertTrue(result.changes["role.life.enabled"])
        self.assertNotIn("role.life.sleep_enabled", result.changes)
        self.assertNotIn("role.life.timezone", result.changes)
        disabled = self.convert({"schedule_detail_config": {"enable_daily_diary": False}})
        self.assertFalse(disabled.changes["role.life.enabled"])

    def test_image_operation_policy_does_not_erase_memory_capabilities(self):
        for operations in ([], ["image.generate"]):
            result = convert_plugin_configuration({"operation_access": {"public_operations": operations}})
            self.assertNotIn("multi_host.capabilities", result.changes)
            self.assertTrue(any(item.source_key == "operation_access.public_operations"
                                and item.kind == "manual_mapping" for item in result.differences))

    def convert(self, config):
        from companion_v2.expression.configuration import configuration_fields as expression_fields
        from companion_v2.proactive.configuration import configuration_fields as contact_fields
        from companion_v2.role_work.state.configuration import configuration_fields as state_fields
        from companion_v2.role_work.life.configuration import configuration_fields as life_fields
        from companion_v2.conversation.media.quota import configuration_fields as quota_fields
        from companion_v2.conversation.reply_settings import configuration_fields as reply_fields
        from companion_v2.conversation.segmentation import SegmentationPolicy

        fields = FieldRegistry(version="migration.behavior", fields=(
            *expression_fields(), *contact_fields(), *state_fields(), *life_fields(), *quota_fields(),
            *reply_fields(SegmentationPolicy(enabled=False), True, 1.5),
            FieldDefinition("model.proactive.judge.provider", "model.proactive.judge", "model_gateway",
                            ValueKind.TEXT, "default")))
        return convert_configuration(config, registry=fields)

    def test_quiet_hours_preserve_the_complete_overnight_range(self):
        result = self.convert({"basic_config": {"quiet_hours": "4:00-08:30"}})
        self.assertEqual({key: value for key, value in result.changes.items()
                          if key.startswith("proactive.contact.quiet_")}, {
            "proactive.contact.quiet_enabled": True,
            "proactive.contact.quiet_start": "04:00",
            "proactive.contact.quiet_end": "08:30"})

    def test_invalid_quiet_range_never_writes_a_partial_schedule(self):
        result = self.convert({"basic_config": {"quiet_hours": "23:00-25:00"}})
        self.assertFalse(any(key.startswith("proactive.contact.quiet_") for key in result.changes))
        self.assertIn("invalid_value", {item.kind for item in result.differences})
        disabled = self.convert({"basic_config": {"quiet_hours": ""}})
        self.assertIs(disabled.changes["proactive.contact.quiet_enabled"], False)

    def test_legacy_text_cleanup_migrates_into_the_presentation_fields(self):
        from companion_v2.conversation.reply_settings import configuration_fields
        from companion_v2.conversation.segmentation import SegmentationPolicy

        result = self.convert({"legacy_compat_config": {
            "enable_segmented_proactive_reply": True,
            "enable_segmented_proactive_content_cleanup": True,
            "segmented_proactive_content_cleanup_scope": "all",
            "segmented_proactive_content_cleanup_rule": "[\\n。？！]",
            "segmented_proactive_content_cleanup_words": ["。", "，"]}})
        changes = result.changes
        defaults = {field.field_id: field.default
                    for field in configuration_fields(SegmentationPolicy(enabled=False), True, 1.5)}
        self.assertTrue(defaults["conversation.presentation.content_cleanup_enabled"])
        self.assertIs(changes["conversation.presentation.content_cleanup_enabled"], True)
        self.assertEqual(changes["conversation.presentation.content_cleanup_scope"], "all")
        self.assertEqual(changes["conversation.presentation.content_cleanup_rule"], "[\\n。？！]")
        self.assertEqual(json.loads(changes["conversation.presentation.content_cleanup_words"]), ["。", "，"])
        self.assertNotIn("conversation.presentation.content_cleanup_words",
                         {item.field_id for item in result.differences if item.kind == "invalid_value"})

    def test_expression_settings_use_their_corresponding_live_fields(self):
        result = self.convert({"memory_habit_config": {
            "expression_learning_mode": "aggressive",
            "expression_group_learning_daily_batch_limit": 6,
            "expression_group_learning_min_new_messages": 20}})
        self.assertEqual(result.changes, {
            "expression.learning.learning_mode": "aggressive",
            "expression.learning.group_daily_batch_limit": 6,
            "expression.learning.group_new_messages": 20})

    def test_expression_capacity_preserves_approved_and_pending_room(self):
        source = "memory_habit_config.max_learned_expression_items"
        for previous in (60, 240):
            result = self.convert({"memory_habit_config": {"max_learned_expression_items": previous}})
            self.assertEqual(result.changes["expression.learning.active_signal_limit"], previous * 2)
            self.assertEqual([item.kind for item in result.differences if item.source_key == source],
                             ["partial_mapping"],
                             "a hand-mapped capacity must not also be reported as an unmapped setting")
        audit = next(item for item in configuration_audit_sources() if item["source"] == source)
        self.assertEqual(audit["target"], "expression.learning.active_signal_limit")

    def test_selected_group_learning_is_never_expanded_to_all_groups(self):
        for mode, enabled in (("selected", False), ("disabled", False), ("all", True)):
            with self.subTest(mode=mode):
                result = self.convert({"memory_habit_config": {
                    "expression_group_learning_source_mode": mode}})
                self.assertIs(result.changes["expression.learning.group_learning_enabled"], enabled)
        disabled = self.convert({"memory_habit_config": {
            "enable_expression_learning": False, "expression_group_learning_source_mode": "all"}})
        self.assertIs(disabled.changes["expression.learning.group_learning_enabled"], False)

    def test_distinct_old_limits_never_overwrite_the_contact_send_limit(self):
        result = self.convert({"basic_config": {"default_proactive_daily_limit": 0},
            "proactive_reach_config": {"max_daily_messages": 8},
            "proactive_generation_config": {"proactive_persona_judge_max_daily": 200},
            "recall_config": {"recall_message_cache_max_items": 300},
            "memory_habit_config": {"meal_care_max_daily": 2}})
        self.assertEqual(result.changes["proactive.contact.max_contacts_24h"], 8)
        self.assertNotIn("conversation.recall.transcribe_max_items", result.changes)
        self.assertNotIn("proactive.contact.enable_llm_proactive_persona_judge", result.changes)

    def test_share_priority_and_creative_fraction_do_not_conflict_with_general_probability(self):
        result = self.convert({"daily_creative_config": {
            "proactive_share_probability": 25, "creative_share_probability": 0.28},
            "proactive_reach_config": {"proactive_share_priority": 48}})
        self.assertEqual(result.changes["proactive.contact.proactive_share_probability"], 25)
        fraction = self.convert({"daily_creative_config": {"creative_share_probability": 0.28}})
        self.assertEqual(fraction.changes["proactive.contact.proactive_share_probability"], 28)

    def test_natural_cycle_and_explicit_intensity_remain_independent(self):
        for enabled, expected in ((True, 3), (False, 0)):
            with self.subTest(enabled=enabled):
                result = self.convert({"humanized_state_config": {
                    "advanced_cycle_start_offset": 0, "inject_passive_states": enabled,
                    "humanized_state_intensity": 3}})
                self.assertEqual(result.changes["role.state.cycle_initialization"], "natural")
                self.assertEqual(result.changes["role.state.humanized_state_intensity"], expected)

    def test_supported_components_keep_their_relative_order(self):
        result = self.convert({"legacy_compat_config": {"segmented_proactive_component_order":
            ["voice", "other", "image", "reaction", "text", "voice", "at"]}})
        self.assertEqual(json.loads(result.changes["conversation.presentation.component_order"]),
                         ["voice", "image", "text", "at"])
        invalid = self.convert({"legacy_compat_config": {
            "segmented_proactive_component_order": ["voice", "unknown"]}})
        self.assertNotIn("conversation.presentation.component_order", invalid.changes)

    def test_duplicate_split_words_remain_valid_for_the_actual_segmenter(self):
        from companion_v2.conversation.segmentation import SegmentationPolicy

        result = convert_plugin_configuration({"legacy_compat_config": {
            "segmented_proactive_split_words": ["~", "，", "~", "，", "。"]}})
        words = result.changes["segmentation.split_words"]
        SegmentationPolicy(split_words=words)
        self.assertEqual(json.loads(words), ["~", "，", "。"])

    def test_empty_compatibility_actions_do_not_disable_explicit_current_switches(self):
        result = self.convert({
            "screen_action_config": {"enable_screen_glance_action": True},
            "poke_action_config": {"enable_poke_action": True},
            "voice_action_config": {"enable_voice_action": True},
            "legacy_compat_config": {"enabled_proactive_actions": "", "allow_screen_peek_action": "",
                                     "allow_poke_action": "", "allow_voice_action": ""}})
        for name in ("screen_glance", "poke_action", "voice_action"):
            self.assertIs(result.changes["proactive.contact." + name + "_enabled"], True)

    def test_style_channels_preserve_nonempty_text_instead_of_colliding(self):
        result = self.convert({"basic_config": {"default_style": "轻声", "reply_style_prompt": "自然接话",
            "persona_conversation_voice_prompt": "偶尔停顿", "persona_planning_voice_prompt": "留些空闲",
            "persona_creative_voice_prompt": "用角色的感受书写"},
            "life_schedule_config": {"daily_plan_prompt": ""},
            "schedule_detail_config": {"daily_diary_creativity": "balanced", "daily_diary_custom_direction": ""}})
        self.assertEqual(result.changes["role.state.planning_prompt"], "留些空闲")
        self.assertEqual(result.changes["role.life.diary_style_prompt"], "用角色的感受书写")
        for text in ("轻声", "自然接话", "偶尔停顿"):
            self.assertIn(text, result.changes["conversation.presentation.style_prompt"])
        self.assertIn("自然接话", result.changes["proactive.contact.style_prompt"])
        self.assertNotIn("conflict", {item.kind for item in result.differences})

    def test_empty_task_provider_stays_unset_for_the_task_default(self):
        result = self.convert({"proactive_generation_config": {
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": ""}})
        self.assertNotIn("model.proactive.judge.provider", result.changes)

    def test_disabled_review_does_not_override_the_active_persona_judge_or_its_model(self):
        result = self.convert({"proactive_generation_config": {
            "enable_llm_proactive_persona_judge": True, "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": "judge"},
            "emotion_relationship_config": {"enable_proactive_message_review": False},
            "model_assignment_config": {"RESPONSE_REVIEW_PROVIDER_ID": "review", "SMART_SILENCE_PROVIDER_ID": "silence"}})
        self.assertIs(result.changes["proactive.contact.enable_llm_proactive_persona_judge"], True)
        self.assertEqual(result.changes["model.proactive.judge.provider"], "judge")
        self.assertNotIn("conflict", {item.kind for item in result.differences})

    def test_image_limits_keep_the_old_unlimited_sentinel_and_effective_limit(self):
        for global_limit, user_limit, expected in ((-1, 1, 1), (-1, 0, -1), (0, 1, 0), (3, 1, 1)):
            with self.subTest(global_limit=global_limit, user_limit=user_limit):
                result = self.convert({"photo_action_config": {
                    "photo_generation_proactive_max_daily": global_limit, "photo_action_max_daily": user_limit}})
                self.assertEqual(result.changes["conversation.media_quota.proactive_max_daily"], expected)

    def test_image_history_capacity_uses_the_supported_bound_instead_of_falling_back_to_default(self):
        result = self.convert({"message_debounce_config": {"context_image_caption_max_items": 12}})
        self.assertEqual(result.changes["conversation.presentation.context_image_caption_max_items"], 8)
        self.assertIn("partial_mapping", {item.kind for item in result.differences})

    def test_development_memory_budget_does_not_override_the_formal_context_budget(self):
        fields = registry("memory.context_items", ValueKind.INTEGER, 3, minimum=0, maximum=16)
        result = convert_configuration({"external_memory_config": {
            "enable_new_framework_dev_e2e": True, "new_framework_dev_e2e_max_memory_items": 3,
            "memory_companion_context_top_k": 5}}, registry=fields)
        self.assertEqual(result.changes["memory.context_items"], 5)
        self.assertNotIn("conflict", {item.kind for item in result.differences})


class AccountConfigurationMigrationTests(unittest.TestCase):
    @staticmethod
    def accounts():
        return [
            {"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-100",
             "persona_roles": {"persona-a": "role-100-a", "persona-b": "role-100-b",
                               "[%None]": "role-100-default", "": "role-100-default"}},
            {"platform_id": "qq-main", "account_id": "101", "bot_id": "bot-101",
             "persona_roles": {"persona-a": "role-101-a", "persona-b": "role-101-b",
                               "[%None]": "role-101-default", "": "role-101-default"}},
        ]

    def test_scope_targets_groups_and_loaded_personas_are_mapped_precisely(self):
        result = convert_account_configuration({
            "basic_config": {
                "bot_scope_mode": "allowlist",
                "bot_scope_ids": ["100"],
                "target_user_ids": ["200", "invalid-id"],
                "plugin_specific_persona_id": "persona-b",
                "enable_multi_persona_mode": True,
                "multi_persona_ids": ["persona-a", "persona-missing"],
            },
            "group_observation_config": {
                "enable_group_companion": True,
                "group_access_mode": "whitelist",
                "group_whitelist_ids": ["300"],
            },
            "legacy_compat_config": {"target_group_ids": ["301"]},
        }, accounts=self.accounts())

        self.assertEqual(result.changes["multi_host.targets"][0]["account_id"], "100")
        self.assertEqual(result.changes["multi_host.targets"][0]["participant_id"], "200")
        self.assertEqual(len(result.changes["multi_host.targets"]), 1)
        self.assertEqual([item["group_id"] for item in result.changes["multi_host.group_targets"]], ["300"])
        self.assertEqual(result.changes["multi_host.personas"], [
            {"bot_id": "bot-100", "persona_id": "persona-b", "role_id": "role-100-b"},
            {"bot_id": "bot-100", "persona_id": "persona-a", "role_id": "role-100-a"},
        ])
        self.assertIn("basic_config.plugin_specific_persona_id", result.sources["multi_host.personas"])
        self.assertIn("basic_config.multi_persona_ids", result.sources["multi_host.personas"])
        self.assertNotIn("multi_host.enabled", result.changes)
        self.assertIn("group_precedence", {item.kind for item in result.differences})
        self.assertIn("invalid_value", {item.kind for item in result.differences})
        self.assertIn("unavailable_persona", {item.kind for item in result.differences})

    def test_unknown_scope_mode_and_unloaded_persona_do_not_create_routes(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "unknown", "target_user_ids": ["200"],
                             "plugin_specific_persona_id": "not-loaded"},
        }, accounts=self.accounts())

        self.assertEqual(result.changes, {})
        self.assertIn("invalid_value", {item.kind for item in result.differences})
        self.assertIn("unavailable", {item.kind for item in result.differences})

    def test_one_private_target_maps_to_the_single_target_identity(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "allowlist", "bot_scope_ids": ["100"],
                             "target_user_ids": ["200"], "plugin_specific_persona_id": "persona-a"},
        }, accounts=self.accounts())

        self.assertEqual(result.changes["target.platform_id"], "qq-main")
        self.assertEqual(result.changes["target.account_id"], "100")
        self.assertEqual(result.changes["target.participant_id"], "200")
        self.assertEqual(result.changes["target.role_id"], "role-100-a")
        self.assertEqual(result.changes["target.persona_id"], "persona-a")

    def test_group_blacklist_and_disabled_group_companion_are_not_widened(self):
        base = {"basic_config": {"bot_scope_mode": "all"},
                "group_observation_config": {"enable_group_companion": True,
                                              "group_access_mode": "blacklist",
                                              "group_whitelist_ids": ["300"]}}
        blacklisted = convert_account_configuration(base, accounts=self.accounts())
        self.assertNotIn("multi_host.group_targets", blacklisted.changes)
        self.assertIn("unsupported_mode", {item.kind for item in blacklisted.differences})

        base["group_observation_config"]["group_access_mode"] = "whitelist"
        base["group_observation_config"]["enable_group_companion"] = False
        disabled = convert_account_configuration(base, accounts=self.accounts())
        self.assertNotIn("multi_host.group_targets", disabled.changes)
        self.assertIn("disabled_source", {item.kind for item in disabled.differences})

        legacy_blacklist = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all"},
            "group_observation_config": {"enable_group_companion": True,
                                          "group_whitelist_ids": ["300"]},
            "legacy_compat_config": {"require_target_group": False},
        }, accounts=self.accounts())
        self.assertNotIn("multi_host.group_targets", legacy_blacklist.changes)
        self.assertIn("unsupported_mode", {item.kind for item in legacy_blacklist.differences})

    def test_legacy_group_target_alias_fills_an_empty_new_whitelist(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "plugin_specific_persona_id": "persona-a"},
            "group_observation_config": {"enable_group_companion": True,
                                          "group_whitelist_ids": []},
            "legacy_compat_config": {"target_group_ids": ["301"]},
        }, accounts=self.accounts())

        self.assertEqual({item["group_id"] for item in result.changes["multi_host.group_targets"]}, {"301"})

    def test_bot_denylist_excludes_matching_accounts(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "denylist", "bot_scope_ids": ["100"],
                             "target_user_ids": ["200"], "plugin_specific_persona_id": "persona-a"},
        }, accounts=self.accounts())

        self.assertEqual({item["account_id"] for item in result.changes["multi_host.targets"]}, {"101"})

    def test_multi_platform_accounts_and_personas_map_from_one_configuration(self):
        accounts = [
            {"platform_id": "qq-onebot", "account_id": "100", "bot_id": "bot-qq",
             "persona_roles": {"persona-a": "role-qq-a", "persona-b": "role-qq-b"}},
            {"platform_id": "other-onebot", "account_id": "200", "bot_id": "bot-other",
             "persona_roles": {"persona-a": "role-other-a", "persona-b": "role-other-b"}},
        ]
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["300"],
                             "enable_multi_persona_mode": True,
                             "multi_persona_ids": ["persona-a", "persona-b"]},
        }, accounts=accounts)

        self.assertEqual({item["platform_id"] for item in result.changes["multi_host.targets"]},
                         {"qq-onebot", "other-onebot"})
        self.assertEqual({(item["bot_id"], item["persona_id"]) for item in
                          result.changes["multi_host.personas"]},
                         {("bot-qq", "persona-a"), ("bot-qq", "persona-b"),
                          ("bot-other", "persona-a"), ("bot-other", "persona-b")})

    def test_ambiguous_allowlist_id_does_not_fan_out_across_platforms(self):
        accounts = [
            {"platform_id": "qq-onebot", "account_id": "100", "bot_id": "bot-qq",
             "persona_roles": {"persona-a": "role-qq-a"}},
            {"platform_id": "other-onebot", "account_id": "100", "bot_id": "bot-other",
             "persona_roles": {"persona-a": "role-other-a"}},
        ]
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "allowlist", "bot_scope_ids": ["100"],
                             "target_user_ids": ["300"], "plugin_specific_persona_id": "persona-a"},
        }, accounts=accounts)

        self.assertNotIn("multi_host.targets", result.changes)
        self.assertIn("ambiguous_account", {item.kind for item in result.differences})

    def test_missing_scope_with_multiple_accounts_is_not_guessed(self):
        result = convert_account_configuration({"basic_config": {"target_user_ids": ["200"]}},
                                               accounts=self.accounts())

        self.assertEqual(result.changes, {})
        self.assertIn("needs_input", {item.kind for item in result.differences})

    def test_missing_persona_selection_never_expands_the_loaded_catalog(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["200"]},
        }, accounts=self.accounts())

        self.assertEqual(result.changes, {})
        self.assertIn("needs_input", {item.kind for item in result.differences})

    def test_authoritative_default_persona_maps_only_that_persona_and_its_role(self):
        accounts = self.accounts()[:1]
        accounts[0]["default_persona_id"] = "persona-b"
        accounts[0]["default_role_id"] = "unused-no-persona-role"
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["200"]},
        }, accounts=accounts)

        self.assertEqual(result.changes["multi_host.personas"], [
            {"bot_id": "bot-100", "persona_id": "persona-b", "role_id": "role-100-b"},
        ])
        self.assertEqual(result.changes["target.persona_id"], "persona-b")
        self.assertEqual(result.changes["target.role_id"], "role-100-b")

    def test_single_private_target_preselects_only_one_unambiguous_persona_mapping(self):
        accounts = self.accounts()[:1]
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["200"],
                             "enable_multi_persona_mode": True,
                             "multi_persona_ids": ["persona-b"]},
        }, accounts=accounts)

        self.assertEqual(result.changes["target.persona_id"], "persona-b")
        self.assertEqual(result.changes["target.role_id"], "role-100-b")

    def test_ambiguous_multi_persona_mapping_does_not_preselect_a_single_target(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["200"],
                             "enable_multi_persona_mode": True,
                             "multi_persona_ids": ["persona-a", "persona-b"]},
        }, accounts=self.accounts()[:1])

        self.assertIn("multi_host.personas", result.changes)
        self.assertNotIn("target.persona_id", result.changes)
        self.assertNotIn("target.role_id", result.changes)

    def test_authoritative_no_persona_binding_keeps_the_default_role(self):
        accounts = self.accounts()[:1]
        accounts[0]["default_persona_id"] = ""
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["200"]},
        }, accounts=accounts)

        self.assertEqual(result.changes["multi_host.personas"], [
            {"bot_id": "bot-100", "persona_id": "[%None]", "role_id": "role-100-default"},
        ])
        self.assertEqual(result.changes["target.persona_id"], "")
        self.assertEqual(result.changes["target.role_id"], "role-100-default")

    def test_primary_persona_in_the_multi_list_is_not_mapped_twice(self):
        result = convert_account_configuration({
            "basic_config": {"bot_scope_mode": "all", "target_user_ids": ["200"],
                             "plugin_specific_persona_id": "persona-a", "enable_multi_persona_mode": True,
                             "multi_persona_ids": ["persona-a", "persona-b"]},
        }, accounts=self.accounts()[:1])

        self.assertEqual([row["persona_id"] for row in result.changes["multi_host.personas"]],
                         ["persona-a", "persona-b"])


class SemanticStoreMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_existing_settings_as_explicit(self):
        import sqlite3
        from tempfile import TemporaryDirectory

        from companion_v2.storage.semantic import SemanticStore

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantic-catalog.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection as db:
                    db.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
                    db.execute("INSERT INTO settings VALUES (1, 7, ?)",
                               (json.dumps({"reactions_enabled": False, "timeout_seconds": 12}),))
                    db.execute("PRAGMA user_version=4")

            store = SemanticStore(Path(temporary))
            snapshot = store.configuration_snapshot()

            self.assertEqual(snapshot["revision"], 7)
            self.assertFalse(snapshot["values"]["reactions_enabled"])
            self.assertEqual(snapshot["values"]["timeout_seconds"], 12)
            self.assertEqual(snapshot["explicit"], {"reactions_enabled", "timeout_seconds"})
            with store.db() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)

    def test_newly_configured_settings_are_marked_explicit(self):
        from tempfile import TemporaryDirectory

        from companion_v2.storage.semantic import SemanticStore

        with TemporaryDirectory() as temporary:
            store = SemanticStore(Path(temporary))
            before = store.configuration_snapshot()
            store.configure({"reactions_enabled": False}, before["revision"])

            after = store.configuration_snapshot()
            self.assertFalse(after["values"]["reactions_enabled"])
            self.assertIn("reactions_enabled", after["explicit"])


class ValueConversionTests(unittest.TestCase):
    def test_grouped_value_wins_over_a_conflicting_flat_compatibility_value(self):
        result = convert_configuration(
            {"settings": {"enabled": True}, "settings.enabled": False},
            registry=registry("settings.enabled", ValueKind.BOOLEAN, False),
            rules=(rule("settings.enabled", "bool", "settings.enabled"),),
        )

        self.assertEqual(result.changes, {"settings.enabled": True})
        self.assertEqual([item.kind for item in result.differences], ["group_precedence"])

    def test_conflicting_aliases_are_reported_and_not_arbitrarily_overwritten(self):
        result = convert_configuration(
            {"old_a": {"value": True}, "old_b": {"value": False}},
            registry=registry("target.settings.enabled", ValueKind.BOOLEAN, False),
            rules=(rule("old_a.value", "bool", "target.settings.enabled"),
                   rule("old_b.value", "bool", "target.settings.enabled")),
        )

        self.assertEqual(result.changes, {})
        self.assertEqual([item.kind for item in result.differences], ["conflict"])
        self.assertIn("old_a.value", result.differences[0].summary)
        self.assertIn("old_b.value", result.differences[0].summary)


class WindowedSegmentationMigrationTests(unittest.TestCase):
    def component_registry(self):
        fields = (
            FieldDefinition("conversation.presentation.image_position", "conversation.presentation",
                            "conversation.presentation", ValueKind.TEXT, "after_text"),
            FieldDefinition("conversation.presentation.voice_position", "conversation.presentation",
                            "conversation.presentation", ValueKind.TEXT, "after_media"),
        )
        return FieldRegistry(version="configuration-migration.components", fields=fields)

    def test_inline_media_strategies_map_to_v2_component_positions(self):
        result = convert_configuration({"legacy_compat_config": {
            "segmented_proactive_image_strategy": "inline",
            "segmented_proactive_voice_strategy": "inline",
        }}, registry=self.component_registry())

        self.assertEqual(result.changes, {
            "conversation.presentation.image_position": "after_text",
            "conversation.presentation.voice_position": "after_text",
        })
        self.assertNotIn("conflict", {item.kind for item in result.differences})

    def test_separate_and_cross_part_media_strategies_are_reported_without_guessing(self):
        for strategy in ("separate", "previous", "next"):
            with self.subTest(strategy=strategy):
                result = convert_configuration({"legacy_compat_config": {
                    "segmented_proactive_image_strategy": strategy,
                }}, registry=self.component_registry())
                self.assertEqual(result.changes, {})
                self.assertEqual([item.kind for item in result.differences], ["strategy_not_representable"])

    def test_inline_at_strategy_is_already_covered_by_v2_default_order(self):
        result = convert_configuration({"legacy_compat_config": {
            "segmented_proactive_at_strategy": "inline",
        }}, registry=self.component_registry())

        self.assertEqual(result.changes, {})
        self.assertEqual([item.kind for item in result.differences], ["already_represented"])

    def test_profile_defaults_do_not_conflict_with_the_global_master_switch(self):
        result = convert_configuration({"legacy_compat_config": {
            "enable_segmented_proactive_reply": False,
            "enable_segmented_proactive_chat_profiles": True,
            "segmented_proactive_private_enabled": True,
            "segmented_proactive_group_enabled": True,
            "segmented_proactive_threshold": 500,
            "segmented_proactive_private_threshold": 320,
            "segmented_proactive_group_threshold": 680,
        }}, registry=segmentation_registry(), window_kind="private")

        self.assertFalse(result.changes["conversation.presentation.enabled"])
        self.assertEqual(result.changes["conversation.presentation.threshold"], 320)
        self.assertNotIn("conflict", {item.kind for item in result.differences})

        group = convert_configuration({"legacy_compat_config": {
            "enable_segmented_proactive_reply": False,
            "enable_segmented_proactive_chat_profiles": True,
            "segmented_proactive_private_enabled": True,
            "segmented_proactive_group_enabled": True,
            "segmented_proactive_threshold": 500,
            "segmented_proactive_private_threshold": 320,
            "segmented_proactive_group_threshold": 680,
        }}, registry=segmentation_registry(), window_kind="group")
        self.assertFalse(group.changes["conversation.presentation.enabled"])
        self.assertEqual(group.changes["conversation.presentation.threshold"], 680)

    def test_private_and_group_profile_values_migrate_to_the_selected_window(self):
        config = {"legacy_compat_config": {
            "enable_segmented_proactive_reply": True,
            "enable_segmented_proactive_chat_profiles": True,
            "segmented_proactive_private_enabled": False,
            "segmented_proactive_group_enabled": True,
            "segmented_proactive_threshold": 500,
            "segmented_proactive_private_threshold": 300,
            "segmented_proactive_group_threshold": 700,
            "segmented_proactive_private_min_segment_chars": 4,
            "segmented_proactive_group_min_segment_chars": 9,
            "segmented_proactive_private_max_segments": 2,
            "segmented_proactive_group_max_segments": 6,
        }}

        private = convert_configuration(config, registry=segmentation_registry(), window_kind="private")
        group = convert_configuration(config, registry=segmentation_registry(), window_kind="group")
        self.assertFalse(private.changes["conversation.presentation.enabled"])
        self.assertEqual(private.changes["conversation.presentation.threshold"], 300)
        self.assertEqual(private.changes["conversation.presentation.min_segment_chars"], 4)
        self.assertEqual(private.changes["conversation.presentation.max_segments"], 2)
        self.assertTrue(group.changes["conversation.presentation.enabled"])
        self.assertEqual(group.changes["conversation.presentation.threshold"], 700)
        self.assertEqual(group.changes["conversation.presentation.min_segment_chars"], 9)
        self.assertEqual(group.changes["conversation.presentation.max_segments"], 6)
        self.assertNotIn("conflict", {item.kind for item in private.differences + group.differences})

    def test_legacy_chat_scope_and_global_settings_apply_when_profiles_are_off(self):
        config = {"legacy_compat_config": {
            "enable_segmented_proactive_reply": True,
            "enable_segmented_proactive_chat_profiles": False,
            "segmented_proactive_chat_scope": "private",
            "segmented_proactive_threshold": 425,
            "segmented_proactive_private_threshold": 300,
            "segmented_proactive_group_threshold": 800,
        }}

        private = convert_configuration(config, registry=segmentation_registry(), window_kind="private")
        group = convert_configuration(config, registry=segmentation_registry(), window_kind="group")
        self.assertTrue(private.changes["conversation.presentation.enabled"])
        self.assertFalse(group.changes["conversation.presentation.enabled"])
        self.assertEqual(private.changes["conversation.presentation.threshold"], 425)
        self.assertEqual(group.changes["conversation.presentation.threshold"], 425)

    def test_profile_interval_seconds_map_to_v2_random_spacing_fields(self):
        result = convert_configuration({"legacy_compat_config": {
            "enable_segmented_proactive_reply": True,
            "enable_segmented_proactive_chat_profiles": True,
            "segmented_proactive_private_interval_method": "random",
            "segmented_proactive_private_interval_min": 0.4,
            "segmented_proactive_private_interval_max": 1.25,
        }}, registry=segmentation_registry(), window_kind="private")

        self.assertEqual(result.changes["conversation.presentation.part_gap_mode"], "random")
        self.assertEqual(result.changes["conversation.presentation.part_gap_min_ms"], 400)
        self.assertEqual(result.changes["conversation.presentation.part_gap_max_ms"], 1250)
        self.assertNotIn("conversation.presentation.part_gap_ms", result.changes)
        self.assertNotIn("conflict", {item.kind for item in result.differences})

    def test_missing_interval_method_uses_v1_log_default_if_spacing_values_exist(self):
        result = convert_configuration({"legacy_compat_config": {
            "enable_segmented_proactive_chat_profiles": True,
            "segmented_proactive_group_interval_min": 0.8,
            "segmented_proactive_group_interval_max": 2.2,
        }}, registry=segmentation_registry(), window_kind="group")

        self.assertEqual(result.changes["conversation.presentation.part_gap_mode"], "log")
        self.assertEqual(result.changes["conversation.presentation.part_gap_min_ms"], 800)
        self.assertEqual(result.changes["conversation.presentation.part_gap_max_ms"], 2200)

    def test_invalid_window_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            convert_configuration({}, registry=segmentation_registry(), window_kind="all")

    def test_integer_percent_probability_is_converted_to_a_fraction(self):
        result = convert_configuration(
            {"old": {"chance": 72}},
            registry=registry("target.settings.chance", ValueKind.PROBABILITY, 0.5),
            rules=(rule("old.chance", "int", "target.settings.chance"),),
        )

        self.assertEqual(result.changes, {"target.settings.chance": 0.72})

    def test_duration_units_are_converted_when_both_fields_name_their_units(self):
        result = convert_configuration(
            {"old": {"interval_minutes": 3}},
            registry=registry("target.settings.interval_seconds", ValueKind.INTEGER, 60, minimum=60, maximum=600),
            rules=(rule("old.interval_minutes", "int", "target.settings.interval_seconds"),),
        )

        self.assertEqual(result.changes, {"target.settings.interval_seconds": 180})

    def test_json_list_values_are_encoded_for_json_text_fields(self):
        result = convert_configuration(
            {"old": {"words": ["别复读", "先确认"]}},
            registry=registry("target.settings.words", ValueKind.TEXT, "[]"),
            rules=({**rule("old.words", "list", "target.settings.words", "medium"), "list_format": "json"},),
        )

        self.assertEqual(json.loads(result.changes["target.settings.words"]), ["别复读", "先确认"])
        self.assertEqual(result.confidence["target.settings.words"], "medium")

    def test_knowledge_source_ids_use_the_consumers_delimited_format(self):
        source_rule = next(item for item in configuration_rules() if item["target"] == "knowledge.source_ids")
        result = convert_configuration(
            {"life_schedule_config": {"roleplay_knowledge_source_ids": ["lore-a", "lore-b"]}},
            registry=registry("knowledge.source_ids", ValueKind.TEXT, "", maximum_chars=4000),
            rules=(source_rule,),
        )

        self.assertEqual(result.changes["knowledge.source_ids"], "lore-a\nlore-b")
        from companion_v2.knowledge.configuration import _configured_list
        self.assertEqual(_configured_list(result.changes["knowledge.source_ids"], limit=8),
                         ("lore-a", "lore-b"))

    def test_plugin_configuration_converts_percentages_and_boolean_modes(self):
        result = convert_plugin_configuration({
            "voice_action_config": {"tts_trigger_probability": 72},
            "auto_voice_config": {"auto_voice_enabled": True},
        }, rules=(
            plugin_rule("voice_action_config.tts_trigger_probability", "int",
                        "voice_reply.probability", "percent",
                        {"type": "float", "minimum": 0, "maximum": 1}),
            plugin_rule("auto_voice_config.auto_voice_enabled", "bool",
                        "voice_reply.trigger_mode", "bool_to_voice_trigger",
                        {"type": "string", "options": ["always", "request", "adaptive"]}),
        ))

        self.assertEqual(result.changes, {
            "voice_reply.probability": 0.72,
            "voice_reply.trigger_mode": "always",
        })

    def test_weather_source_maps_to_the_global_plugin_setting(self):
        result = convert_plugin_configuration({
            "weather_config": {"weather_source": "qweather"},
        })

        self.assertEqual(result.changes, {"weather.provider": "qweather"})
        self.assertEqual(result.sources["weather.provider"], ("weather_config.weather_source",))
        self.assertEqual(plugin_configuration_defaults()["weather.provider"], "auto")

    def test_defaults_include_host_fields_without_automatic_legacy_rules(self):
        defaults = plugin_configuration_defaults()
        self.assertEqual(defaults["runtime.quiet_seconds"], 0)
        self.assertEqual(defaults["runtime.maximum_wait_seconds"], 0)

    def test_v1_global_full_probability_preserves_automatic_voice_when_auto_switch_is_off(self):
        source = {"voice_action_config": {"tts_frequency_control_mode": "global", "tts_trigger_probability": 100,
                  "tts_private_trigger_probability": -1, "tts_group_trigger_probability": -1},
                  "auto_voice_config": {"auto_voice_enabled": False}}
        converted = convert_plugin_configuration(source)
        self.assertEqual(converted.changes["voice_reply.trigger_mode"], "always")
        self.assertEqual(converted.changes["voice_reply.private_probability"], -1.0)
        self.assertEqual(converted.changes["voice_reply.group_probability"], -1.0)
        self.assertIn("voice_action_config.tts_frequency_control_mode", converted.sources["voice_reply.trigger_mode"])
        source["voice_action_config"]["tts_frequency_control_mode"] = "legacy"
        self.assertEqual(convert_plugin_configuration(source).changes["voice_reply.trigger_mode"], "request")

    def test_v1_voice_probability_force_respects_the_window_override(self):
        source = {"voice_action_config": {"tts_frequency_control_mode": "global", "tts_trigger_probability": 100,
                  "tts_private_trigger_probability": -1, "tts_group_trigger_probability": 20},
                  "auto_voice_config": {"auto_voice_enabled": False}}
        self.assertEqual(convert_plugin_configuration(source).changes["voice_reply.trigger_mode"], "always")
        self.assertEqual(convert_plugin_configuration(source, window_kind="private").changes["voice_reply.trigger_mode"], "always")
        self.assertEqual(convert_plugin_configuration(source, window_kind="group").changes["voice_reply.trigger_mode"], "always")
        source["voice_action_config"]["tts_group_trigger_probability"] = 0
        self.assertEqual(convert_plugin_configuration(source, window_kind="group").changes["voice_reply.trigger_mode"], "request")

    def test_weather_source_aliases_are_normalized_and_unsupported_sources_are_explained(self):
        for source in ("open-meteo", "open_meteo", "openmeteo"):
            with self.subTest(source=source):
                result = convert_plugin_configuration({"weather_config": {"weather_source": source}})
                self.assertEqual(result.changes, {"weather.provider": "open-meteo"})

        unsupported = convert_plugin_configuration({
            "weather_config": {"weather_source": "openweathermap"},
        })
        self.assertEqual(unsupported.changes, {})
        self.assertEqual(unsupported.differences[0].kind, "invalid_value")
        self.assertIn("没有与此旧天气来源等价", unsupported.differences[0].summary)

    def test_qweather_host_and_credential_map_to_host_only_weather_settings(self):
        credential = "a" * 32
        result = convert_plugin_configuration({
            "weather_config": {
                "weather_source": "qweather",
                "weather_api_host": "api.qweatherapi.com",
                "weather_token": credential,
            },
        })

        self.assertEqual(result.changes, {
            "weather.api_host": "api.qweatherapi.com",
            "weather.credential": credential,
            "weather.provider": "qweather",
        })
        self.assertEqual(plugin_configuration_defaults()["weather.api_host"], "")
        self.assertEqual(plugin_configuration_defaults()["weather.credential"], "")

    def test_plugin_configuration_accepts_reviewed_legacy_number_formats(self):
        rules = {item["source"]: item for item in plugin_configuration_rules()}
        result = convert_plugin_configuration({
            "forward_message_config": {"forward_message_max_messages": 300,
                                        "forward_message_parse_nested": False},
        }, rules=(rules["forward_message_config.forward_message_max_messages"],
                 rules["forward_message_config.forward_message_parse_nested"]))

        self.assertEqual(result.changes, {
            "runtime.forward_max_nodes": 300,
            "runtime.forward_parse_nested": False,
        })
        self.assertEqual(result.confidence["runtime.forward_max_nodes"], "high")
        self.assertEqual(result.confidence["runtime.forward_parse_nested"], "high")

        for invalid in (0, 301):
            overflow = convert_plugin_configuration({
                "forward_message_config": {"forward_message_max_messages": invalid},
            }, rules=(rules["forward_message_config.forward_message_max_messages"],))
            self.assertNotIn("runtime.forward_max_nodes", overflow.changes)
            self.assertIn("invalid_value", {item.kind for item in overflow.differences})

    def test_poke_model_probability_complements_v1_preset_probability_and_enables_lane(self):
        result = convert_plugin_configuration({
            "reactive_poke_config": {
                "reactive_poke_normal_reply_probability": 0.25,
                "reactive_poke_back_times": 3,
            },
        })
        self.assertEqual(result.changes["poke.model_reply_probability"], 0.75)
        self.assertTrue(result.changes["poke.model_reply_enabled"])
        self.assertEqual(result.changes["poke.back_times"], 3)

    def test_poke_back_times_outside_v2_ceiling_is_reported(self):
        result = convert_plugin_configuration({
            "reactive_poke_config": {"reactive_poke_back_times": 10},
        })
        self.assertNotIn("poke.back_times", result.changes)
        self.assertIn("invalid_value", {item.kind for item in result.differences})

    def test_all_preset_reply_probability_keeps_model_lane_disabled(self):
        result = convert_plugin_configuration({
            "reactive_poke_config": {"reactive_poke_normal_reply_probability": 1.0},
        })
        self.assertEqual(result.changes["poke.model_reply_probability"], 0)
        self.assertFalse(result.changes["poke.model_reply_enabled"])

    def test_core_user_voice_mention_fields_keep_their_narrow_scope(self):
        result = convert_plugin_configuration({
            "auto_voice_config": {
                "main_user_voice_probability": 60,
                "main_user_mention_voice_probability": 85,
                "main_user_mention_voice_keywords": "摸摸\n抱抱",
                "main_user_mention_voice_prompt": "声音轻一点。",
            },
        })
        self.assertNotIn("voice_reply.primary_mention_probability", convert_plugin_configuration({
            "auto_voice_config": {"main_user_voice_probability": 60},
        }).changes)
        self.assertEqual(result.changes["voice_reply.primary_mention_probability"], 0.85)
        self.assertEqual(result.changes["voice_reply.primary_mention_keywords"], "摸摸\n抱抱")

    def test_legacy_core_user_voice_inherit_sentinel_is_not_written_as_negative_probability(self):
        result = convert_plugin_configuration({
            "auto_voice_config": {"main_user_mention_voice_probability": -1},
        })
        self.assertNotIn("voice_reply.primary_mention_probability", result.changes)
        self.assertIn("invalid_value", {item.kind for item in result.differences})

    def test_plugin_mapping_conflicts_and_invalid_enum_values_are_reported(self):
        result = convert_plugin_configuration({
            "voice_action_config": {"tts_session_min_interval_seconds": 30,
                                     "tts_private_min_interval_seconds": 45,
                                     "tts_generation_mode": "unrecognized"},
        }, rules=(
            plugin_rule("voice_action_config.tts_session_min_interval_seconds", "float",
                        "voice_reply.cooldown_seconds", "integer",
                        {"type": "int", "minimum": 0, "maximum": 86400}),
            plugin_rule("voice_action_config.tts_private_min_interval_seconds", "float",
                        "voice_reply.cooldown_seconds", "integer",
                        {"type": "int", "minimum": 0, "maximum": 86400}),
            plugin_rule("voice_action_config.tts_generation_mode", "string",
                        "voice_reply.generation_mode", "identity",
                        {"type": "string", "options": ["plain", "model_tag"]}),
        ))

        self.assertEqual(result.changes, {})
        self.assertEqual({item.kind for item in result.differences}, {"conflict", "invalid_value"})

    def test_semantic_settings_preserve_the_global_provider_without_promoting_the_reaction_provider(self):
        result = convert_semantic_configuration({
            "model_assignment_config": {"EMBEDDING_PROVIDER_ID": "embed-a"},
            "experimental_motivation_config": {
                "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID": "embed-b",
                "reaction_expression_trigger_probability": 0.65,
                "reaction_expression_cooldown_seconds": 240,
                "reaction_expression_embedding_timeout_ms": 5000,
            },
            "relay_bridge_config": {"group_slang_summary_minutes": 20},
            "group_slang_topic_config": {"enable_group_slang_learning": False},
        })

        self.assertEqual(result.changes, {
            "semantic.embedding_provider": "embed-a",
            "semantic.reactions_trigger_probability": 0.65,
            "semantic.reactions_cooldown_seconds": 240,
            "semantic.timeout_seconds": 5,
            "semantic.learning_interval_seconds": 1200,
            "semantic.slang_learning": False,
        })
        self.assertEqual([item.kind for item in result.differences], ["partial_mapping"])
        self.assertEqual(result.differences[0].source_key,
                         "experimental_motivation_config.REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID")

    def test_semantic_mapping_rejects_values_that_v2_cannot_represent(self):
        result = convert_semantic_configuration({
            "experimental_motivation_config": {"reaction_expression_embedding_timeout_ms": 0},
            "relay_bridge_config": {"group_slang_summary_minutes": 2000},
        })

        self.assertEqual(result.changes, {})
        self.assertEqual({item.kind for item in result.differences}, {"invalid_value"})

    def test_out_of_range_values_are_explained_instead_of_written(self):
        result = convert_configuration(
            {"old": {"limit": 101}},
            registry=registry("target.settings.limit", ValueKind.INTEGER, 10, minimum=0, maximum=100),
            rules=(rule("old.limit", "int", "target.settings.limit"),),
        )

        self.assertEqual(result.changes, {})
        self.assertEqual(result.differences[0].kind, "invalid_value")

    def test_missing_runtime_field_is_reported_as_unavailable(self):
        result = convert_configuration(
            {"old": {"limit": 2}}, registry=registry("other.settings.enabled", ValueKind.BOOLEAN, False),
            rules=(rule("old.limit", "int", "target.settings.limit"),),
        )

        self.assertEqual(result.changes, {})
        self.assertEqual(result.differences[0].kind, "unavailable")


class SkillGrowthScheduleMigrationTests(unittest.TestCase):
    target = "role.growth.skills.schedule_influence_strength"
    master_switch = "role.growth.skills.enabled"

    def convert(self, config):
        return convert_configuration(
            config,
            registry=registry(self.target, ValueKind.INTEGER, 0, minimum=0, maximum=3),
        )

    def test_disabled_v1_switch_migrates_to_zero_and_ignores_saved_strength(self):
        result = self.convert({"skill_growth_config": {
            "enable_skill_growth_schedule_influence": False,
            "skill_growth_schedule_influence_strength": 0.8,
        }})

        self.assertEqual(result.changes[self.target], 0)
        self.assertEqual(result.confidence[self.target], "high")
        self.assertIn("inactive_source", {item.kind for item in result.differences})

    def test_fractional_v1_strength_is_quantized_to_the_v2_tier(self):
        result = self.convert({"skill_growth_config": {
            "enable_skill_growth_schedule_influence": True,
            "skill_growth_schedule_influence_strength": 0.35,
        }})

        self.assertEqual(result.changes[self.target], 2)
        self.assertEqual(result.confidence[self.target], "medium")
        self.assertIn("quantized", {item.kind for item in result.differences})

    def test_enabled_v1_switch_uses_its_default_strength_when_missing(self):
        result = self.convert({"skill_growth_config": {
            "enable_skill_growth_schedule_influence": True,
        }})

        self.assertEqual(result.changes[self.target], 2)
        self.assertEqual(result.confidence[self.target], "medium")
        self.assertIn("derived_default", {item.kind for item in result.differences})

    def test_quantization_is_reported_in_the_upper_tier_too(self):
        result = self.convert({"skill_growth_config": {
            "enable_skill_growth_schedule_influence": True,
            "skill_growth_schedule_influence_strength": 0.9,
        }})

        self.assertEqual(result.changes[self.target], 3)
        self.assertIn("quantized", {item.kind for item in result.differences})

    def test_exact_endpoint_strength_does_not_report_quantization(self):
        result = self.convert({"skill_growth_config": {
            "enable_skill_growth_schedule_influence": True,
            "skill_growth_schedule_influence_strength": 1.0,
        }})

        self.assertEqual(result.changes[self.target], 3)
        self.assertNotIn("quantized", {item.kind for item in result.differences})

    def test_out_of_range_v1_strength_is_reported_and_not_migrated(self):
        for strength in (-0.01, 1.01):
            with self.subTest(strength=strength):
                result = self.convert({"skill_growth_config": {
                    "enable_skill_growth_schedule_influence": True,
                    "skill_growth_schedule_influence_strength": strength,
                }})

                self.assertNotIn(self.target, result.changes)
                self.assertIn("invalid_value", {item.kind for item in result.differences})

    def test_legacy_master_switch_maps_to_the_v2_automatic_consumption_switch(self):
        migration_rules = tuple(item for item in configuration_rules()
                                if item["target"] == self.master_switch)
        result = convert_configuration(
            {"skill_growth_config": {"enable_skill_growth_simulation": False}},
            registry=registry(self.master_switch, ValueKind.BOOLEAN, True),
            rules=migration_rules,
        )

        self.assertEqual(result.changes, {self.master_switch: False})
        self.assertEqual(result.confidence[self.master_switch], "medium")

    def test_missing_legacy_master_switch_keeps_the_v2_default(self):
        migration_rules = tuple(item for item in configuration_rules()
                                if item["target"] == self.master_switch)
        result = convert_configuration(
            {"skill_growth_config": {}},
            registry=registry(self.master_switch, ValueKind.BOOLEAN, True),
            rules=migration_rules,
        )

        self.assertEqual(result.changes, {})


class ModelPolicyMigrationTests(unittest.TestCase):
    def test_routes_refusal_timeouts_streaming_and_cycle_preferences_reach_registered_fields(self):
        from companion_v2.model_gateway.routing import configuration_fields
        from companion_v2.model_gateway.models import ModelTaskProfile
        from companion_v2.execution.models import Lane
        profile=ModelTaskProfile('calendar.plan',Lane.FOREGROUND,'normal','Reply','',100)
        fields=FieldRegistry(version='migration-model',fields=configuration_fields((profile,)))
        source={'model_assignment_config':{'enable_llm_streaming':True,
            'model_replacement_rules':[{'name':'travel','keywords':['旅行'],'provider_id':'secondary'}],
            'model_replacement_scope':'conversation','enable_sensitive_model_replacement':True,
            'SENSITIVE_REPLACEMENT_PROVIDER_ID':'secondary','sensitive_replacement_keywords':['旧拒答词']},
            'model_timeout_overrides':{'calendar.plan':90}}
        migrated=convert_configuration(source,registry=fields)
        self.assertEqual(migrated.changes['model.calendar.plan.timeout_seconds'],90)
        self.assertEqual(convert_plugin_configuration(source).changes['model.fallback_provider_id'],'secondary')
        self.assertIn('旅行',convert_plugin_configuration(source).changes['model.replacement_rules'])
        self.assertTrue(convert_plugin_configuration(source).changes['model.streaming'])
        field='role.state.cycle_follicular_days'
        state=convert_configuration({'humanized_state_config':{'advanced_cycle_follicular_days':9}},
            registry=registry(field,ValueKind.INTEGER,7,minimum=1,maximum=90))
        self.assertEqual(state.changes[field],9)


if __name__ == "__main__":
    unittest.main()
