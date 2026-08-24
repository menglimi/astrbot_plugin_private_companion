# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GroupContextSettingsTests(unittest.TestCase):
    def test_group_scene_recent_limit_is_explicit_context_setting(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        legacy = schema["group_scene_recent_limit"]
        item = schema["group_scene_config"]["items"]["group_scene_recent_limit"]

        self.assertEqual(20, legacy["default"])
        self.assertEqual("群聊上下文消息数", item["description"])
        self.assertEqual(20, item["default"])
        self.assertEqual({"min": 2, "max": 100, "step": 1}, item["slider"])
        self.assertEqual(
            {"enable_group_context_injection": True},
            item["condition"],
        )

    def test_both_panels_place_context_limit_after_storage_limit(self) -> None:
        expected_field = (
            '{ key: "group_scene_recent_limit", type: "number", '
            'label: "群聊上下文消息数", placeholder: "20", min: 2, max: 100'
        )
        for relative in ("pages/companion-panel/app.js", "pages/陪伴面板/app.js"):
            script = (ROOT / relative).read_text(encoding="utf-8")
            section = script.split('title: "群聊回复理解"', 1)[1].split("],", 1)[0]
            storage_index = section.index('key: "max_group_recent_messages"')
            context_index = section.index('key: "group_scene_recent_limit"')
            intercept_index = section.index('key: "intercept_astrbot_group_context"')

            self.assertLess(storage_index, context_index)
            self.assertLess(context_index, intercept_index)
            self.assertIn(expected_field, section)


if __name__ == "__main__":
    unittest.main()
