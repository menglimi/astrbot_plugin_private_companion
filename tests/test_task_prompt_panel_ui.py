# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_ROOTS = (
    ROOT / "pages" / "陪伴面板",
    ROOT / "pages" / "companion-panel",
)


class TaskPromptPanelUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assets = {
            panel: {
                "html": (panel / "index.html").read_text(encoding="utf-8"),
                "script": (panel / "app.js").read_text(encoding="utf-8"),
                "css": (panel / "app.css").read_text(encoding="utf-8"),
            }
            for panel in PANEL_ROOTS
        }

    def test_mirrored_panel_assets_stay_in_lockstep(self) -> None:
        primary = self.assets[PANEL_ROOTS[0]]
        mirror = self.assets[PANEL_ROOTS[1]]
        self.assertEqual(primary, mirror)

    def test_html_separates_complete_builtin_prompt_from_editable_override(self) -> None:
        for panel, assets in self.assets.items():
            html = assets["html"]
            preview = html.index('id="promptsBuiltinPrompt"')
            editor = html.index('id="promptsTextarea"')
            self.assertLess(preview, editor, panel)
            self.assertIn('class="prompts-builtin-preview"', html)
            self.assertIn('完整内置任务模型提示词', html)
            self.assertNotIn('内置提示词（模板预览）', html)
            self.assertNotIn('稳定规则预览', html)
            self.assertIn('id="promptsBuiltinPromptDynamic"', html)
            self.assertIn('class="prompts-readonly-badge"', html)
            self.assertIn('aria-labelledby="promptsBuiltinPromptLabel"', html)
            self.assertIn('aria-readonly="true"', html)
            self.assertIn('class="prompts-textarea-field"', html)

    def test_script_keeps_builtin_prompt_out_of_saved_draft(self) -> None:
        for panel, assets in self.assets.items():
            script = assets["script"]
            self.assertIn("builtin_prompt", script, panel)
            self.assertIn("builtin_prompt_dynamic", script, panel)
            self.assertIn("normalizeTaskPromptBuiltinText", script, panel)
            self.assertIn("完整内置任务模型提示词", script, panel)
            self.assertNotIn("模板预览", script, panel)
            self.assertNotIn("稳定规则预览", script, panel)
            self.assertIn('$("#promptsBuiltinPrompt")', script, panel)
            self.assertIn("builtinPrompt.textContent = builtinPromptText", script, panel)
            self.assertIn('state.taskPromptDraft = String(selected?.custom_prompt || "")', script, panel)
            self.assertNotRegex(
                script,
                r"promptsTextarea[^\n]{0,180}builtin_prompt",
                msg=f"内置提示词不应写入可保存文本框：{panel}",
            )

    def test_css_has_scrollable_complete_prompt_and_mobile_layout_rules(self) -> None:
        for panel, assets in self.assets.items():
            css = assets["css"]
            self.assertIn(".prompts-builtin-preview", css, panel)
            self.assertIn(".prompts-builtin-prompt", css, panel)
            self.assertRegex(css, r"\.prompts-builtin-prompt\s*\{[\s\S]*?overflow\s*:\s*auto", panel)
            self.assertRegex(css, r"\.prompts-builtin-prompt\s*\{[\s\S]*?white-space\s*:\s*pre-wrap", panel)
            self.assertRegex(css, r"@media\s*\(max-width:\s*520px\)[\s\S]*?prompts-builtin-preview", panel)
            self.assertIn("min-width: 0", css[css.index(".prompts-builtin-preview") : css.index(".prompts-builtin-preview") + 600], panel)


if __name__ == "__main__":
    unittest.main()
