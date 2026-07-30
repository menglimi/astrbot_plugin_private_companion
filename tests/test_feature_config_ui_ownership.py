# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


def _javascript_literal(source: str, marker: str, opening: str) -> str:
    marker_index = source.index(marker)
    start = source.index(opening, marker_index + len(marker))
    closing = "}" if opening == "{" else "]"
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"未找到完整 JavaScript 常量: {marker}")


class FeatureConfigUiOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        setting_block = _javascript_literal(cls.script, "const featureSettingGroups =", "{")
        cls.setting_groups: dict[str, list[str]] = {}
        for match in re.finditer(r"^\s{2}([A-Za-z0-9_]+):\s*\[(.*?)\],$", setting_block, re.M | re.S):
            cls.setting_groups[match.group(1)] = re.findall(r'"([A-Za-z0-9_]+)"', match.group(2))

        section_block = _javascript_literal(cls.script, "const featureSettingSections =", "{")
        cls.section_setting_keys: set[str] = set()
        for match in re.finditer(r"keys:\s*\[(.*?)\]", section_block, re.S):
            cls.section_setting_keys.update(re.findall(r'"([A-Za-z0-9_]+)"', match.group(1)))

        feature_block = _javascript_literal(cls.script, "const featureGroups =", "[")
        cls.feature_group_keys: dict[str, list[str]] = {}
        for match in re.finditer(
            r'title:\s*"([^"]+)".*?keys:\s*\[(.*?)\]',
            feature_block,
            re.S,
        ):
            cls.feature_group_keys[match.group(1)] = re.findall(r'"([A-Za-z0-9_]+)"', match.group(2))
        grouped_keys: set[str] = set()
        for match in re.finditer(r"keys:\s*\[(.*?)\]", feature_block, re.S):
            grouped_keys.update(re.findall(r'"([A-Za-z0-9_]+)"', match.group(1)))

        embedded_block = _javascript_literal(cls.script, "const embeddedFeatureParentByKey =", "{")
        embedded_keys = set(re.findall(r"^\s{2}([A-Za-z0-9_]+):", embedded_block, re.M))
        cls.visible_feature_keys = (grouped_keys - embedded_keys) | {"enable_proactive_only_mode"}

        advanced_block = _javascript_literal(cls.script, "const setupGuideAdvancedItems =", "{")
        cls.advanced_group_keys: dict[str, list[str]] = {}
        for match in re.finditer(r"^\s{2}([A-Za-z0-9_]+):\s*\[(.*?)^\s{2}\],$", advanced_block, re.M | re.S):
            cls.advanced_group_keys[match.group(1)] = re.findall(
                r'^\s{6}key:\s*"([A-Za-z0-9_]+)"',
                match.group(2),
                re.M,
            )

    def test_visible_feature_details_do_not_duplicate_editable_settings(self) -> None:
        owners: dict[str, list[str]] = {}
        for feature_key in self.visible_feature_keys:
            for setting_key in self.setting_groups.get(feature_key, []):
                owners.setdefault(setting_key, []).append(feature_key)
        duplicates = {key: value for key, value in owners.items() if len(value) > 1}
        self.assertEqual(duplicates, {})

    def test_previously_missing_settings_have_one_primary_owner(self) -> None:
        expected = {
            "enable_proactive_chat_integration": "enable_proactive_only_mode",
            "proactive_chat_bridge_review_mode": "enable_proactive_only_mode",
            "proactive_chat_bridge_collision_window_seconds": "enable_proactive_only_mode",
            "default_enable_configured_targets": "enable_proactive_only_mode",
            "proactive_reply_context_hours": "enable_proactive_only_mode",
            "enable_proactive_decorating_hooks": "enable_proactive_only_mode",
            "enable_precise_platform_send": "enable_proactive_only_mode",
            "max_proactive_plan_lag_minutes": "enable_proactive_only_mode",
            "enable_daily_greetings": "enable_humanized_states",
            "greeting_idle_minutes": "enable_humanized_states",
            "allow_insomnia_night_message": "enable_humanized_states",
            "enable_enhanced_dreams": "enable_humanized_states",
            "dream_afterglow_mode": "enable_humanized_states",
            "enable_mixed_dream_themes": "enable_humanized_states",
            "enable_intimate_dream_theme": "enable_humanized_states",
            "dream_theme_candidates": "enable_humanized_states",
            "recall_message_cache_text_chars": "enable_recall_enhancement",
            "enable_context_image_captioning": "enable_private_image_self_recognition",
            "context_image_caption_max_items": "enable_private_image_self_recognition",
            "context_image_caption_timeout_seconds": "enable_private_image_self_recognition",
            "private_image_vision_provider_priority": "enable_private_image_self_recognition",
            "private_image_vision_custom_prompt": "enable_private_image_self_recognition",
            "private_image_vision_max_chars": "enable_private_image_self_recognition",
            "environment_perception_timezone": "enable_environment_perception",
            "enable_memory_companion_private_recall": "enable_livingmemory_integration",
            "forward_message_image_vision_timeout_seconds": "enable_forward_message_adaptation",
            "max_group_topic_threads": "enable_group_member_profiles",
            "group_episode_refresh_minutes": "enable_group_member_profiles",
            "group_slang_summary_minutes": "enable_group_member_profiles",
            "max_group_episodes": "enable_group_member_profiles",
            "max_group_relationship_edges": "enable_group_member_profiles",
            "external_image_download_proxy": "enable_photo_text_action",
            "external_image_download_use_environment_proxy": "enable_photo_text_action",
            "screen_peek_max_daily": "enable_screen_glance_action",
            "screen_peek_cooldown_minutes": "enable_screen_glance_action",
            "enable_goodnight_screen_check": "enable_screen_glance_action",
            "goodnight_screen_check_delay_minutes": "enable_screen_glance_action",
            "poke_action_max_times": "enable_poke_action",
            "poke_action_cooldown_minutes": "enable_poke_action",
            "voice_action_max_chars": "enable_voice_action",
        }
        for setting_key, expected_owner in expected.items():
            owners = [
                feature_key
                for feature_key in self.visible_feature_keys
                if setting_key in self.setting_groups.get(feature_key, [])
            ]
            self.assertEqual(owners, [expected_owner], setting_key)

    def test_parent_cards_only_edit_their_own_settings(self) -> None:
        self.assertEqual(
            self.setting_groups["enable_mai_style_integration"],
            [
                "default_style",
                "reply_style_prompt",
                "enable_persona_voice_channels",
                "persona_conversation_voice_prompt",
                "persona_creative_voice_prompt",
                "persona_planning_voice_prompt",
                "persona_inner_voice_prompt",
                "persona_proactive_voice_prompt",
            ],
        )
        self.assertEqual(self.setting_groups["enable_open_loop_tracking"], [])
        self.assertNotIn("enable_external_event_self_link", self.setting_groups["enable_web_exploration"])
        self.assertNotIn("external_event_self_link_probability", self.setting_groups["enable_web_exploration"])
        self.assertNotIn("external_event_self_link_cooldown_hours", self.setting_groups["enable_web_exploration"])

    def test_conditional_rerender_preserves_all_feature_form_drafts(self) -> None:
        self.assertIn("const preserveFeatureParamDraft = () =>", self.script)
        self.assertIn("function rememberFeatureParamDraft(control)", self.script)
        self.assertIn("state.featureDetailParamDraft", self.script)
        self.assertIn(
            'detailPage?.addEventListener("input", trackFeatureDetailChange, true);',
            self.script,
        )
        self.assertIn(
            'detailPage?.addEventListener("change", trackFeatureDetailChange, true);',
            self.script,
        )
        self.assertIn(
            'Object.entries(state.featureDetailParamDraft || {}).forEach(([key, value]) => assignParam(key, value));',
            self.script,
        )
        self.assertIn("preserveFeatureParamDraft();\n              renderFeatureSwitches();", self.script)

    def test_feature_flags_return_personal_goals_and_proactive_actions(self) -> None:
        plugin = SimpleNamespace(
            enable_personal_goals=True,
            enable_screen_glance_action=True,
            enable_goodnight_screen_check=True,
            enable_poke_action=False,
            enable_voice_action=True,
        )
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin
        api._screen_companion_available = lambda: False
        flags = api._feature_flags()
        self.assertTrue(flags["enable_personal_goals"])
        self.assertTrue(flags["enable_screen_glance_action"])
        self.assertTrue(flags["enable_goodnight_screen_check"])
        self.assertFalse(flags["enable_poke_action"])
        self.assertTrue(flags["enable_voice_action"])

    def test_reaction_expression_settings_are_visible_and_apply_to_runtime(self) -> None:
        plugin = SimpleNamespace(
            config={},
            enable_reaction_expression_experiment=False,
            reaction_expression_private_enabled=True,
            reaction_expression_group_enabled=False,
            reaction_expression_trigger_probability=0.2,
            reaction_expression_cooldown_seconds=180,
            reaction_expression_low_latency_mode=True,
            reaction_expression_candidate_limit=6,
        )
        api = PrivateCompanionPageApi(plugin)
        api._screen_companion_available = lambda: False

        flags = api._feature_flags()
        self.assertIn("enable_reaction_expression_experiment", flags)
        self.assertFalse(flags["enable_reaction_expression_experiment"])
        self.assertIn(
            "enable_reaction_expression_experiment",
            api._allowed_feature_keys(),
        )
        for key in (
            "reaction_expression_private_enabled",
            "reaction_expression_group_enabled",
            "reaction_expression_trigger_probability",
            "reaction_expression_cooldown_seconds",
            "reaction_expression_low_latency_mode",
            "reaction_expression_candidate_limit",
        ):
            self.assertIn(key, api._allowed_setting_keys())

        api._apply_config_value("enable_reaction_expression_experiment", True)
        probability = api._normalize_setting_value(
            "reaction_expression_trigger_probability", 20
        )
        candidate_limit = api._normalize_setting_value(
            "reaction_expression_candidate_limit", 99
        )
        api._apply_config_value(
            "reaction_expression_trigger_probability", probability
        )
        api._apply_config_value("reaction_expression_candidate_limit", candidate_limit)

        self.assertTrue(plugin.enable_reaction_expression_experiment)
        self.assertEqual(0.2, plugin.reaction_expression_trigger_probability)
        self.assertEqual(16, plugin.reaction_expression_candidate_limit)

    def test_proactive_chat_status_reports_installation_mode_and_last_sync(self) -> None:
        plugin = SimpleNamespace(
            enable_proactive_chat_integration=True,
            proactive_chat_bridge_review_mode="local",
            _proactive_chat_runtime_bridge=SimpleNamespace(
                status=lambda: {
                    "mode": "deep",
                    "mode_label": "深度联动",
                    "attached": True,
                    "version": "1.2.4",
                    "method_count": 6,
                    "methods": ["check_and_chat"],
                    "last_event": "平台发送已确认",
                    "counters": {"delivery_succeeded": 2},
                }
            ),
            _integrated_plugin_installed=lambda *names: "astrbot_plugin_proactive_chat" in names,
            _format_timestamp_elapsed=lambda value: f"elapsed:{int(value)}",
        )
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = plugin

        summary = api._proactive_chat_summary(
            {
                "users": {
                    "10001": {"proactive_chat_bridge_last_sent_at": 100.0},
                    "10002": {"proactive_chat_bridge_last_sent_at": 220.0},
                    "10003": {},
                }
            }
        )

        self.assertTrue(summary["installed"])
        self.assertTrue(summary["active"])
        self.assertTrue(summary["deep_active"])
        self.assertEqual("deep", summary["runtime_mode"])
        self.assertEqual(6, summary["runtime_method_count"])
        self.assertEqual("private", summary["scope"])
        self.assertEqual(2, summary["linked_user_count"])
        self.assertEqual("elapsed:220", summary["last_sent"])

    def test_review_features_are_grouped_by_actual_scope(self) -> None:
        common_keys = self.feature_group_keys["通用能力"]
        private_keys = self.feature_group_keys["私聊陪伴"]

        self.assertIn("enable_passive_response_review", common_keys)
        self.assertIn("enable_smart_silence", common_keys)
        self.assertNotIn("enable_passive_response_review", private_keys)
        self.assertNotIn("enable_smart_silence", private_keys)
        self.assertIn("enable_proactive_message_review", private_keys)
        self.assertNotIn("主动私聊", self.feature_group_keys)

        self.assertIn("enable_passive_response_review", self.advanced_group_keys["common"])
        self.assertIn("enable_smart_silence", self.advanced_group_keys["common"])
        self.assertNotIn("enable_proactive_message_review", self.advanced_group_keys["common"])
        self.assertIn("enable_proactive_message_review", self.advanced_group_keys["private"])
        self.assertNotIn("enable_proactive_message_review", self.advanced_group_keys["proactive"])

    def test_expression_learning_is_a_common_cross_channel_capability(self) -> None:
        common_keys = self.feature_group_keys["通用能力"]
        private_keys = self.feature_group_keys["私聊陪伴"]

        self.assertIn("enable_expression_learning", common_keys)
        self.assertNotIn("enable_expression_learning", private_keys)
        self.assertIn("enable_expression_learning", self.advanced_group_keys["common"])
        self.assertNotIn("enable_companion_memory", self.advanced_group_keys["common"])
        self.assertIn("enable_companion_memory", self.advanced_group_keys["private"])

        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        expression_item = schema["memory_habit_config"]["items"]["enable_expression_learning"]
        self.assertNotIn("condition", expression_item)

    def test_external_memory_feature_uses_neutral_public_identifier(self) -> None:
        self.assertIn(
            'enable_livingmemory_integration: "enable_external_memory_integration"',
            self.script,
        )
        self.assertIn('<small>${escapeHtml(featurePublicKey(key))}</small>', self.script)
        self.assertIn('data-feature-key="${escapeHtml(key)}"', self.script)

    def test_personal_goal_feature_and_settings_are_saveable(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None

        self.assertIn("enable_personal_goals", api._allowed_feature_keys())
        allowed_settings = api._allowed_setting_keys()
        self.assertIn("enable_personal_goal_auto_progress", allowed_settings)
        self.assertIn("personal_goal_share_cooldown_hours", allowed_settings)
        self.assertIn("personal_goal_stall_days", allowed_settings)

    def test_visible_feature_cards_are_saveable(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        missing = self.visible_feature_keys - api._allowed_feature_keys()

        self.assertEqual(missing, set())

    def test_all_returned_feature_flags_are_accepted_by_settings_update(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace()
        api._schema_key_index_cache = None
        api._screen_companion_available = lambda: False
        accepted_feature_keys = (
            api._allowed_feature_keys()
            | (api._schema_bool_keys() & api._allowed_setting_keys())
        )

        self.assertEqual(set(api._feature_flags()) - accepted_feature_keys, set())

    def test_feature_detail_parameters_are_saveable(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None
        detail_keys = set().union(*self.setting_groups.values(), self.section_setting_keys)
        saveable_keys = (
            api._allowed_feature_keys()
            | api._allowed_setting_keys()
            | api._allowed_provider_keys()
        )

        self.assertEqual(detail_keys - saveable_keys, set())


if __name__ == "__main__":
    unittest.main()
