# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.group_prompt_context import group_prompt_context_history_count
from astrbot_plugin_private_companion.page_api_settings import PageSettingNormalizerMixin


ROOT = Path(__file__).resolve().parents[1]


class GroupContextSettingsTests(unittest.TestCase):
    def test_group_scene_limits_are_explicit_context_settings(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        legacy = schema["group_scene_recent_limit"]
        item = schema["group_scene_config"]["items"]["group_scene_recent_limit"]
        legacy_chars = schema["group_scene_recent_max_chars"]
        chars_item = schema["group_scene_config"]["items"]["group_scene_recent_max_chars"]
        history_item = schema["group_scene_config"]["items"]["enable_group_history_injection"]

        self.assertEqual(20, legacy["default"])
        self.assertEqual("群聊上下文消息数", item["description"])
        self.assertEqual(20, item["default"])
        self.assertEqual({"min": 2, "max": 100, "step": 1}, item["slider"])
        self.assertEqual(
            {"enable_group_context_injection": True},
            item["condition"],
        )
        self.assertEqual(4000, legacy_chars["default"])
        self.assertEqual("群聊上下文最大注入字数", chars_item["description"])
        self.assertEqual(4000, chars_item["default"])
        self.assertEqual({"min": 500, "max": 20000, "step": 500}, chars_item["slider"])
        self.assertEqual({"enable_group_context_injection": True}, chars_item["condition"])
        self.assertTrue(history_item["default"])
        self.assertEqual({"enable_group_context_injection": True}, history_item["condition"])

    def test_context_message_limit_is_normalized_when_saved(self) -> None:
        normalizer = PageSettingNormalizerMixin()

        self.assertEqual(2, normalizer._normalize_page_runtime_setting("group_scene_recent_limit", 0))
        self.assertEqual(2, normalizer._normalize_page_runtime_setting("group_scene_recent_limit", -10))
        self.assertEqual(100, normalizer._normalize_page_runtime_setting("group_scene_recent_limit", 101))
        self.assertEqual(20, normalizer._normalize_page_runtime_setting("group_scene_recent_limit", "invalid"))

    def test_history_switch_controls_structured_timeline(self) -> None:
        group = {
            "recent_messages": [{"ts": 90, "sender_id": "u2", "text": "历史"}],
            "recent_bot_replies": [{"ts": 95, "text": "回复"}],
        }

        for enabled, expected_count in ((True, 2), (False, 0)):
            plugin = SimpleNamespace(
                persona_setting=lambda key, default=None, enabled=enabled: (
                    enabled if key == "enable_group_history_injection" else default
                ),
                _resolve_group_current_message_for_prompt=lambda *_args, **_kwargs: {
                    "ts": 100,
                    "sender_id": "u1",
                    "text": "当前",
                },
                _effective_group_history_limit=lambda: 80,
                data={"users": {}},
                _is_target_private_user=lambda *_args, **_kwargs: False,
                _group_display_name_address_conflict=lambda *_args, **_kwargs: False,
                _group_high_intensity_state=lambda *_args, **_kwargs: {},
                _format_group_slang_meanings_for_prompt=lambda *_args, **_kwargs: "",
                _filtered_group_recent_messages=lambda value: value["recent_messages"],
                _environment_fromtimestamp=datetime.fromtimestamp,
                _effective_plugin_persona_id=lambda: "bot",
                _format_livingmemory_guidance=lambda *_args, **_kwargs: "",
                _memory_companion_should_defer_prompt_section=lambda *_args, **_kwargs: True,
            )

            context = GroupObservationMixin._format_group_passive_reply_context_for_prompt(
                plugin,
                group,
                "u1",
                "当前",
            )
            self.assertEqual(expected_count, group_prompt_context_history_count(context))

    def test_both_panels_place_context_limit_after_storage_limit(self) -> None:
        expected_field = (
            '{ key: "group_scene_recent_limit", type: "number", '
            'label: "群聊上下文消息数", placeholder: "20", min: 2, max: 100'
        )
        for relative in ("pages/companion-panel/app.js", "pages/陪伴面板/app.js"):
            script = (ROOT / relative).read_text(encoding="utf-8")
            section = script.split('title: "群聊回复理解"', 1)[1].split("],", 1)[0]
            storage_index = section.index('key: "max_group_recent_messages"')
            history_toggle_index = section.index('key: "enable_group_history_injection"')
            context_index = section.index('key: "group_scene_recent_limit"')
            chars_index = section.index('key: "group_scene_recent_max_chars"')
            intercept_index = section.index('key: "intercept_astrbot_group_context"')

            self.assertLess(storage_index, history_toggle_index)
            self.assertLess(history_toggle_index, context_index)
            self.assertLess(context_index, chars_index)
            self.assertLess(chars_index, intercept_index)
            self.assertIn(expected_field, section)
            self.assertIn(
                '{ key: "group_scene_recent_max_chars", type: "number", '
                'label: "群聊上下文最大注入字数", placeholder: "4000", min: 500, max: 20000',
                section,
            )


if __name__ == "__main__":
    unittest.main()
