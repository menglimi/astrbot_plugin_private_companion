# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ROOT / "pages" / "companion-panel"
LOCALIZED = ROOT / "pages" / "陪伴面板"


class ConfigPageLifecycleFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (PRIMARY / "index.html").read_text(encoding="utf-8")
        cls.script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        cls.css = (PRIMARY / "css" / "polish.css").read_text(encoding="utf-8")

    def test_both_panel_bundles_remain_identical(self) -> None:
        for relative in ("index.html", "app.js", "css/polish.css"):
            self.assertEqual(
                (PRIMARY / relative).read_text(encoding="utf-8"),
                (LOCALIZED / relative).read_text(encoding="utf-8"),
            )

    def test_config_page_has_section_navigation_and_two_level_filters(self) -> None:
        for marker in (
            'class="config-page-intro"',
            'data-config-section-target="config-common-settings"',
            'data-config-section-target="config-runtime-settings"',
            'data-config-section-target="config-feature-settings"',
            'id="featureDomainFilters"',
            'id="featureStageFilters"',
            'id="featureStatusFilters"',
            'aria-live="polite"',
            "config=multi-persona-summary-v2",
        ):
            self.assertIn(marker, self.html)

    def test_feature_lifecycle_supports_all_message_stages_and_background(self) -> None:
        for marker in (
            '{ id: "before", label: "接收与准备", compact: "发送前" }',
            '{ id: "during", label: "生成与发送", compact: "发送时" }',
            '{ id: "after", label: "发送后沉淀", compact: "发送后" }',
            '{ id: "background", label: "后台运行", compact: "后台" }',
            '"enable_message_debounce"',
            '"enable_passive_response_review"',
            '"enable_expression_learning"',
            '"enable_humanized_states"',
            "function featureStagesForKey(key)",
            "function renderFeatureFilterControls(groups, allKeys, visibleCount)",
            'data-feature-stage="${escapeHtml(stage)}"',
            'data-feature-filter-reset',
        ):
            self.assertIn(marker, self.script)

    def test_filters_are_composable_and_keep_detail_navigation(self) -> None:
        for marker in (
            'featureMatchesDomainFilter(key)',
            'state.featureStageFilter !== "all"',
            'featureMatchesStatusFilter(key)',
            'featureMatchesQueryFilter(key, query)',
            'filterWorkspace.hidden = inFeatureDetail',
            'featureDetailPage(state.selectedFeatureKey)',
            'target.scrollIntoView({ behavior: "smooth", block: "start" })',
        ):
            self.assertIn(marker, self.script)

    def test_image_plugin_adds_a_cross_domain_capability_filter(self) -> None:
        for marker in (
            '...(anyImageGeneratorInstalled() ? [{ title: "image_generation", label: "生图", capability: true }] : [])',
            'enable_photo_text_action: "主动/用户生图"',
            'enable_qzone_integration: "空间配图"',
            'enable_creative_writing: "作品封面"',
            'function featureMatchesDomainFilter(key, domain = state.featureDomainFilter)',
            'state.featureDomainFilter === "image_generation" ? imageGenerationFeatureUse(key) : ""',
            'class="feature-image-use"',
            'if (!anyImageInstalled && state.featureDomainFilter === "image_generation")',
        ):
            self.assertIn(marker, self.script)
        self.assertIn(".feature-domain-filters button.is-capability", self.css)
        self.assertIn(".feature-stage-tags > .feature-image-use", self.css)

    def test_layout_has_focus_mobile_and_overflow_guards(self) -> None:
        for marker in (
            "#panel-config .config-section-nav button:focus-visible",
            "#panel-config .feature-filter-options button:focus-visible",
            "#panel-config .feature-filter-workspace[hidden]",
            "#panel-config .feature-filter-empty",
            "@media (max-width: 620px)",
            "overflow-x: auto",
            "grid-template-columns: repeat(3, minmax(0, 1fr))",
        ):
            self.assertIn(marker, self.css)


if __name__ == "__main__":
    unittest.main()
