# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = (
    ROOT / "pages" / "陪伴面板" / "app.js",
    ROOT / "pages" / "companion-panel" / "app.js",
)


class PortraitGlobalModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scripts = tuple(path.read_text(encoding="utf-8") for path in PANEL_SCRIPTS)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        cls.schema_setting = schema["basic_config"]["items"]["portrait_global_mode"]

    def test_both_mirrored_bundles_use_the_schema_backed_global_select(self) -> None:
        expected = list(zip(self.schema_setting["options"], self.schema_setting["labels"]))
        expected_literal = ", ".join(
            f'["{value}", "{label}"]' for value, label in expected
        )
        entry_pattern = re.compile(
            r"portrait_global_mode:\s*\{(?P<body>[^}]+)\},",
            re.MULTILINE,
        )

        entries = []
        for script in self.scripts:
            match = entry_pattern.search(script)
            self.assertIsNotNone(match)
            body = match.group("body")
            entries.append(body)
            self.assertIn('type: "select"', body)
            self.assertIn(f"options: [{expected_literal}]", body)
            self.assertNotIn("follow_global", body)

        self.assertEqual(entries[0], entries[1])

    def test_generic_select_renderer_preserves_feature_key_and_save_contract(self) -> None:
        for script in self.scripts:
            select_start = script.index('if (spec.type === "select")')
            select_end = script.index('if (spec.type === "provider")', select_start)
            renderer = script[select_start:select_end]
            self.assertIn('<select data-feature-param="${safeKey}"', renderer)
            self.assertIn('(spec.options || []).map(([optionValue, label])', renderer)

            payload_start = script.index("function collectFeatureDetailPayload")
            payload_end = script.index("function collectFeatureSwitchPayload", payload_start)
            payload = script[payload_start:payload_end]
            self.assertIn('querySelectorAll("[data-feature-param]")', payload)
            self.assertIn("assignParam(key, collectSettingValue(key, input));", payload)

            save_start = script.index("async function saveFeatureSwitchChanges")
            save_end = script.index("async function saveCurrentFeatureDetail", save_start)
            save = script[save_start:save_end]
            self.assertIn('postJson("/settings/update", payload)', save)


if __name__ == "__main__":
    unittest.main()
