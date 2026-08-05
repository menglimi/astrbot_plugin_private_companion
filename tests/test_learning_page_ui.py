# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = ROOT / "pages" / "陪伴面板"


class LearningPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
        cls.script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
        cls.css = (PAGE_ROOT / "app.css").read_text(encoding="utf-8")
        cls.polish_css = (PAGE_ROOT / "css" / "polish.css").read_text(encoding="utf-8")

    def test_learning_page_uses_three_secondary_destinations(self) -> None:
        self.assertIn('id="panel-learning"', self.html)
        self.assertIn('id="learningSummary" class="learning-summary-board" role="tablist" aria-label="学习工作台导航"', self.html)
        self.assertNotIn('class="learning-subnav"', self.html)
        self.assertNotIn(".learning-subnav", self.polish_css)
        self.assertIn('data-learning-section="skills"', self.script)
        self.assertIn('data-learning-section="expressions"', self.script)
        self.assertIn('data-learning-section="social"', self.script)
        self.assertIn('data-learning-review-shortcut', self.script)
        self.assertIn('class="learning-summary-card learning-summary-expression-card"', self.script)
        self.assertIn("关系网", self.script)
        self.assertIn("技能", self.script)
        self.assertIn("表达", self.script)
        self.assertIn('class="learning-page-status">审核后使用</span>', self.html)

    def test_relationship_page_is_merged_without_duplicate_top_level_navigation(self) -> None:
        nav = self.html.split('<nav class="annotations"', 1)[1].split("</nav>", 1)[0]
        learning_panel = self.html.split('id="panel-learning"', 1)[1].split('id="panel-group"', 1)[0]
        self.assertNotIn('data-tab="worldbook"', nav)
        self.assertNotIn('id="panel-worldbook"', self.html)
        self.assertIn('id="worldbookSummary"', learning_panel)
        self.assertIn('id="worldbookMembers"', learning_panel)
        self.assertIn('id="worldbookGroups"', learning_panel)
        self.assertIn('id="worldbookImportBtn"', learning_panel)
        self.assertIn('const opensSocialLearning = tabName === "worldbook";', self.script)
        self.assertIn('state.learningSection = "social";', self.script)

    def test_behavior_habits_are_owned_by_private_detail(self) -> None:
        learning_panel = self.html.split('id="panel-learning"', 1)[1].split('id="panel-group"', 1)[0]
        private_renderer = self.script.split("async function renderUserDetail", 1)[1].split(
            "function renderPrivateDialogueEpisodes", 1
        )[0]
        learning_renderer = self.script.split("function renderLearning()", 1)[1].split(
            "function renderUsers", 1
        )[0]
        self.assertNotIn("行为习惯", learning_panel)
        self.assertNotIn("learningUserDetail", learning_panel)
        self.assertIn("renderPrivateBehaviorHabits(detail)", private_renderer)
        self.assertIn("renderPrivateDialogueEpisodes(detail)", private_renderer)
        self.assertIn("行为习惯", self.script.split("function renderPrivateBehaviorHabits", 1)[1].split(
            "function userWorldbookBlock", 1
        )[0])
        self.assertIn("data-private-learning-clear", self.script)
        self.assertIn("clear_behavior_habits: true", self.script)
        self.assertNotIn('clear_learning: true', private_renderer)
        self.assertNotIn("renderLearningUserDetail", self.script)
        self.assertNotIn("renderLearningUserRows", self.script)
        self.assertNotIn("renderPrivateBehaviorHabits", learning_renderer)

    def test_expression_review_has_a_dedicated_master_detail_workspace(self) -> None:
        self.assertIn('id="expressionReviewWorkspace"', self.html)
        self.assertIn('data-expression-view="library"', self.html)
        self.assertIn('data-expression-view="review"', self.html)
        review_renderer = self.script.split("function renderExpressionReviewWorkspace()", 1)[1].split(
            "function expressionLibraryMoreButton", 1
        )[0]
        self.assertIn('class="expression-review-layout"', review_renderer)
        self.assertIn('class="expression-review-queue"', review_renderer)
        self.assertIn('class="expression-review-detail"', review_renderer)
        self.assertIn("expressionReviewQueueItem", review_renderer)
        self.assertIn("expressionRuleGroupItem(selectedItem.rule, true, true)", review_renderer)
        self.assertIn('data-expression-action="approve_rule_group"', self.script)
        self.assertIn('data-expression-action="reject_rule_group"', self.script)
        self.assertIn("一次处理整组，完成后自动进入下一组", review_renderer)
        self.assertIn("ArrowDown", review_renderer)
        self.assertIn("ArrowUp", review_renderer)

    def test_expression_library_only_displays_approved_rules(self) -> None:
        renderer = self.script.split("function renderExpressionLibraryView()", 1)[1].split(
            "function expressionReviewRuleKey", 1
        )[0]
        self.assertIn("pending_rules: []", renderer)
        self.assertNotIn("data-expression-library-status", renderer)
        self.assertIn("data-open-expression-review", renderer)
        self.assertIn("state.expressionLibraryFilter", renderer)
        self.assertIn("state.expressionLibraryType", renderer)
        self.assertIn("state.expressionLibraryQuery", renderer)

    def test_expression_rules_use_combined_family_cards_with_flat_fallback(self) -> None:
        self.assertIn("function normalizeExpressionRuleGroup", self.script)
        self.assertIn("function expressionRuleGroups", self.script)
        self.assertIn('const groupKey = pending ? "pending_rule_groups" : "rule_groups";', self.script)
        self.assertIn("function expressionRuleGroupItem", self.script)
        self.assertIn('class="expression-rule-components"', self.script)
        self.assertIn("expressionRuleComponent(group.style_rule", self.script)
        self.assertIn("expressionRuleComponent(group.grammar_rule", self.script)
        self.assertIn('data-expression-rule-family-id', self.script)
        self.assertIn('data-expression-action="delete_rule_group"', self.script)
        self.assertIn("function expressionRuleGroupEditor", self.script)
        self.assertIn("function expressionRuleEditorComponent", self.script)
        self.assertIn("data-expression-rule-editor", self.script)
        self.assertIn('expression_action: "update_rule_group"', self.script)
        self.assertIn("支持片段、来源、证据数量和反馈统计保持只读", self.script)
        self.assertIn("适用范围、反馈、证据和管理操作默认收起", self.script)
        self.assertIn(".expression-rule-components", self.css)
        self.assertIn(".expression-rule-editor", self.css)

    def test_expression_library_keeps_secondary_details_collapsed(self) -> None:
        scope_renderer = self.script.split("function renderExpressionScopeManager()", 1)[1].split(
            "function renderLearningSummary()", 1
        )[0]
        library_renderer = self.script.split("function renderExpressionLibraryBlock", 1)[1].split(
            "function expressionPendingRuleItem", 1
        )[0]
        self.assertNotIn("expression-voice-actions", scope_renderer)
        self.assertNotIn("expression-rule-overview", library_renderer)
        self.assertIn('class="expression-rule-details"', self.script)
        self.assertIn("范围、证据与管理", self.script)
        self.assertIn("expressionSourceBadge(group, !pending)", self.script)
        self.assertIn(".expression-rule-group-list {\n  grid-template-columns: 1fr;", self.css)

    def test_expression_samples_are_unified_and_source_attributed(self) -> None:
        self.assertIn('aria-label="统一表达学习库"', self.html)
        self.assertIn('data-expression-library-filter="${value}"', self.script)
        self.assertIn("expressionLibrarySourceText(item)", self.script)
        self.assertIn("expressionSourceBadge(item)", self.script)
        self.assertIn('postJson("/expression-library/update"', self.script)
        self.assertIn("data-expression-source-type", self.script)
        self.assertIn("data-expression-source-id", self.script)
        self.assertIn('data-expression-budget-key="expression_group_learning_daily_batch_limit"', self.script)
        self.assertIn('data-expression-budget-key="expression_group_learning_min_new_messages"', self.script)
        self.assertIn("可复用表达", self.script)
        self.assertIn("支持片段", self.script)
        self.assertIn("与人格/事实边界冲突，不会自动使用", self.script)

    def test_observations_remain_non_injecting_secondary_material(self) -> None:
        self.assertIn("这不是情境表达或语法习惯", self.script)
        self.assertIn("未归纳观察", self.script)
        self.assertIn("const patternTitle = item?.pattern_label || sceneText;", self.script)
        self.assertIn("<b>${escapeHtml(patternTitle)}</b>", self.script)
        self.assertIn("const observationCount = samples.length;", self.script)
        self.assertIn("data-expression-sample-archive", self.script)
        self.assertIn("不会直接改变 Bot 的说法", self.script)

    def test_secondary_tabs_are_keyboard_accessible(self) -> None:
        self.assertIn('role="tablist" aria-label="学习工作台导航"', self.html)
        self.assertIn("function bindLearningNavigation()", self.script)
        self.assertIn('["ArrowLeft", "ArrowRight", "Home", "End"]', self.script)
        self.assertIn('button.setAttribute("aria-selected", selected ? "true" : "false")', self.script)
        self.assertIn('button.tabIndex = selected ? 0 : -1;', self.script)
        self.assertIn(".learning-summary-expression-card > button:focus-visible", self.polish_css)

    def test_review_workspace_is_responsive(self) -> None:
        self.assertIn(".expression-review-layout", self.polish_css)
        self.assertIn("grid-template-columns: minmax(260px, .72fr) minmax(0, 1.6fr);", self.polish_css)
        mobile = self.polish_css.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".expression-review-layout", mobile)
        self.assertIn("grid-template-columns: 1fr;", mobile)
        self.assertIn("overflow-x: auto;", mobile)
        self.assertIn(".learning-summary-expression-card", self.polish_css)
        narrow = self.polish_css.split("@media (max-width: 560px)", 1)[1]
        self.assertIn(".learning-summary-expression-card", narrow)
        self.assertIn("grid-column: 1 / -1;", narrow)
        self.assertIn(".expression-review-batch-bar", self.polish_css)
        self.assertIn(".expression-review-select-toggle", self.polish_css)

    def test_learning_page_assets_use_current_cache_versions(self) -> None:
        self.assertIn('./app.css?v=20260804-reference-guided-dialog-v6', self.html)
        self.assertIn('./css/polish.css?v=20260804-expression-batch-review-v1', self.html)
        self.assertIn(
            './app.js?v=20260806-reference-guided-busy-release-v2',
            self.html,
        )
        self.assertIn(
            './js/panels/qzone-panel.js?v=20260731-qzone-platform-support-v1',
            self.html,
        )

    def test_compact_folio_overrides_follow_legacy_cover_rules(self) -> None:
        marker = "/* Keep the compact utility header after all legacy folio rules"
        legacy_marker = "/* Keep unified-timeline overrides after the base life-desk component rules. */"
        self.assertEqual(self.polish_css.count(marker), 1)
        self.assertGreater(self.polish_css.index(marker), self.polish_css.index(legacy_marker))
        compact_tail = self.polish_css[self.polish_css.index(marker):]
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto auto;", compact_tail)

    def test_panel_hides_unstyled_markup_until_css_and_app_are_ready(self) -> None:
        self.assertIn('id="panel-asset-guard"', self.html)
        self.assertIn('html:not(.pc-assets-ready) body > :not(.asset-load-fallback)', self.html)
        self.assertIn('--pc-panel-css-ready: 1;', self.css)
        self.assertIn('document.documentElement.dataset.pcAppLoaded = "1";', self.script)
        self.assertIn('window.dispatchEvent(new Event("pc-panel-app-ready"));', self.script)
        self.assertNotIn('class="vitruvian"', self.html)
        self.assertIn('id="dailyOutfitLogo" class="daily-outfit-logo" alt=""', self.html)


if __name__ == "__main__":
    unittest.main()
