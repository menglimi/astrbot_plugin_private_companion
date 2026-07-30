# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReactionExpressionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        page_root = ROOT / "pages" / "陪伴面板"
        cls.html = (page_root / "index.html").read_text(encoding="utf-8")
        cls.script = (page_root / "app.js").read_text(encoding="utf-8")
        cls.css = (page_root / "app.css").read_text(encoding="utf-8")
        cls.schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    def test_experiment_is_opt_in_and_keeps_group_scope_off(self) -> None:
        items = self.schema["experimental_motivation_config"]["items"]

        self.assertFalse(items["enable_reaction_expression_experiment"]["default"])
        self.assertTrue(items["reaction_expression_private_enabled"]["default"])
        self.assertFalse(items["reaction_expression_group_enabled"]["default"])
        for key in (
            "reaction_expression_private_enabled",
            "reaction_expression_group_enabled",
            "reaction_expression_trigger_probability",
            "reaction_expression_cooldown_seconds",
            "reaction_expression_low_latency_mode",
            "reaction_expression_candidate_limit",
        ):
            self.assertEqual(
                {"enable_reaction_expression_experiment": True},
                items[key]["condition"],
                key,
            )

    def test_grouped_settings_keep_hidden_flat_compatibility_entries(self) -> None:
        items = self.schema["experimental_motivation_config"]["items"]
        for key in (
            "enable_reaction_expression_experiment",
            "reaction_expression_private_enabled",
            "reaction_expression_group_enabled",
            "reaction_expression_trigger_probability",
            "reaction_expression_cooldown_seconds",
            "reaction_expression_low_latency_mode",
            "reaction_expression_candidate_limit",
        ):
            self.assertIn(key, self.schema)
            self.assertTrue(self.schema[key]["invisible"], key)
            self.assertEqual(items[key]["type"], self.schema[key]["type"], key)
            self.assertEqual(items[key]["default"], self.schema[key]["default"], key)

    def test_latency_and_frequency_defaults_are_bounded(self) -> None:
        items = self.schema["experimental_motivation_config"]["items"]

        self.assertTrue(items["reaction_expression_low_latency_mode"]["default"])
        self.assertEqual(0.2, items["reaction_expression_trigger_probability"]["default"])
        self.assertEqual({"min": 0, "max": 1, "step": 0.01}, items["reaction_expression_trigger_probability"]["slider"])
        self.assertEqual(180, items["reaction_expression_cooldown_seconds"]["default"])
        self.assertEqual(6, items["reaction_expression_candidate_limit"]["default"])
        self.assertEqual({"min": 1, "max": 16, "step": 1}, items["reaction_expression_candidate_limit"]["slider"])

    def test_panel_reuses_existing_experimental_navigation(self) -> None:
        self.assertIn('data-tab="experimental"', self.html)
        self.assertNotIn('data-tab="reaction-expression"', self.html)
        self.assertIn('"enable_reaction_expression_experiment",', self.script)
        self.assertIn('label: "表情表达实验"', self.script)
        self.assertIn('title: "适用会话"', self.script)
        self.assertIn('title: "触发节奏"', self.script)
        self.assertIn('title: "性能策略"', self.script)
        self.assertIn('theme: "expression"', self.script)
        self.assertIn(".exp-card-visual.expression", self.css)
        self.assertIn(".exp-research-hero.expression", self.css)

    def test_panel_exposes_compact_performance_controls(self) -> None:
        for key in (
            "reaction_expression_private_enabled",
            "reaction_expression_group_enabled",
            "reaction_expression_trigger_probability",
            "reaction_expression_cooldown_seconds",
            "reaction_expression_low_latency_mode",
            "reaction_expression_candidate_limit",
        ):
            self.assertIn(key, self.script)
        self.assertIn('reaction_expression_trigger_probability: { type: "number", min: 0, max: 100, step: 1 }', self.script)
        self.assertIn('reaction_expression_candidate_limit: { type: "number", min: 1, max: 16, step: 1 }', self.script)
        self.assertIn("低延迟模式不调用额外选图模型", self.script)
        self.assertIn("插件仍只执行一次图库检索", self.script)
        self.assertIn("overview?.reaction_expression", self.script)
        self.assertIn("缓存命中", self.script)
        self.assertIn("最近检索", self.script)
        self.assertIn("没有足够合适的候选时保持纯文字", self.script)
        self.assertIn('["模型调用", "仅主回复 1 次"]', self.script)
        self.assertIn("绝不会用图片替代正文", self.script)

    def test_panel_exposes_complete_owned_asset_library(self) -> None:
        for endpoint in (
            "/reaction_library/list",
            "/reaction_library/import",
            "/reaction_library/update",
            "/reaction_library/delete",
            "/reaction_library/rescan",
        ):
            self.assertIn(endpoint, self.script)
        for text in (
            "表情包素材库",
            "选择图片或 ZIP",
            "选择文件夹",
            "默认情绪",
            "沟通用途",
            "私聊 + 群聊",
            "重建索引",
            "批量导入",
        ):
            self.assertIn(text, self.script)
        for selector in (
            ".reaction-library-workspace",
            ".reaction-asset-grid",
            ".reaction-library-editor",
            ".reaction-import-dialog",
        ):
            self.assertIn(selector, self.css)
        self.assertIn('key === "enable_reaction_expression_experiment" ? renderReactionLibraryWorkspace()', self.script)


if __name__ == "__main__":
    unittest.main()
