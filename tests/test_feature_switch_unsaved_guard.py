# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FeatureSwitchUnsavedGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")
        cls.page = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")

    def test_fixed_action_dock_has_separate_back_and_save_buttons(self) -> None:
        self.assertIn('id="featureSaveActions"', self.page)
        self.assertIn('id="featureBackBtn"', self.page)
        self.assertIn('id="saveFeaturesBtn"', self.page)
        self.assertIn(".feature-save-actions", self.style)
        self.assertIn("position: fixed", self.style)
        self.assertGreater(
            self.page.index('id="featureSaveActions"'),
            self.page.index("</main>"),
        )
        self.assertIn('id="featureSaveActions" hidden', self.page)
        self.assertIn('const onConfigPage = state.activeTab === "config";', self.script)
        self.assertIn("actions.hidden = !onConfigPage;", self.script)

    def test_dirty_state_guards_back_tab_switch_and_page_unload(self) -> None:
        self.assertIn("function hasUnsavedFeatureChanges()", self.script)
        self.assertIn("function hasUnsavedChanges()", self.script)
        self.assertIn("function hasUnsavedModuleFormChanges()", self.script)
        self.assertIn("function featureDetailFormSignature(root = document)", self.script)
        self.assertIn("current !== baseline.formSignature", self.script)
        self.assertIn("if (refreshFeatureDetailDirty())", self.script)
        self.assertIn("当前功能还有未保存的更改", self.script)
        self.assertIn("功能开关还有未保存的更改", self.script)
        self.assertIn("当前页面还有未保存的配置", self.script)
        self.assertIn("if (tabName !== state.activeTab && hasUnsavedChanges())", self.script)
        self.assertIn("if (!hasUnsavedChanges()) return;", self.script)
        self.assertIn('window.addEventListener("beforeunload"', self.script)

    def test_all_module_forms_participate_in_unsaved_guard(self) -> None:
        for form_id in (
            "roleplayProfileForm",
            "privateAliasForm",
            "quickModuleForm",
            "runtimeSettingsForm",
        ):
            self.assertIn(f'"{form_id}"', self.script)
        self.assertIn("classList.contains(\"is-dirty\")", self.script)
        self.assertIn("discardUnsavedModuleFormChanges();", self.script)

    def test_detail_back_uses_second_click_to_discard_draft(self) -> None:
        self.assertIn('"discard-feature-detail"', self.script)
        self.assertIn('"再次点击放弃更改"', self.script)
        self.assertIn("leaveFeatureDetail(button)", self.script)
        self.assertIn("leaveFeatureDetail(event.currentTarget)", self.script)

    def test_feature_detail_save_uses_unified_payload(self) -> None:
        self.assertIn("function collectFeatureSwitchPayload()", self.script)
        self.assertIn("Object.assign(features, detailPayload.features", self.script)
        self.assertIn("return saveFeatureSwitchChanges(control, successMessage)", self.script)

    def test_unchanged_detail_never_starts_a_save_request(self) -> None:
        save_block = self.script.split(
            'async function saveFeatureSwitchChanges(control = null, successMessage = "已保存功能开关") {',
            1,
        )[1].split("\n}\n\nasync function saveCurrentFeatureDetail", 1)[0]
        self.assertLess(
            save_block.index("if (!hasUnsavedFeatureChanges())"),
            save_block.index('postJson("/settings/update", payload)'),
        )
        self.assertIn("state.featureSaveInProgress = false;", save_block)
        self.assertIn("finally {", save_block)
        self.assertIn("promiseWithTimeout(", save_block)

    def test_persistence_failure_keeps_draft_and_baseline_retryable(self) -> None:
        save_block = self.script.split(
            'async function saveFeatureSwitchChanges(control = null, successMessage = "已保存功能开关") {',
            1,
        )[1].split("\n}\n\nasync function saveCurrentFeatureDetail", 1)[0]
        run_action_block = self.script.split(
            'async function runAction(action, successMessage = "", control = null, options = {}) {',
            1,
        )[1].split("\n}\n\nfunction actionResultPersisted", 1)[0]

        self.assertIn("if (actionResultPersisted(result))", save_block)
        self.assertIn("return actionResultPersisted(result);", save_block)
        self.assertIn("const persistenceFailed = Boolean(result && result.config_saved === false);", run_action_block)
        self.assertIn("if (reload && !persistenceFailed)", run_action_block)
        self.assertIn("!reload && !persistenceFailed", run_action_block)
        self.assertIn("result?.config_saved !== false", self.script)

    def test_module_forms_only_become_clean_after_persistent_save(self) -> None:
        module_submit_block = self.script.split(
            '["roleplayProfileForm", "privateAliasForm", "quickModuleForm", "runtimeSettingsForm"].forEach',
            1,
        )[1].split("\nbindSegmentedPreview();", 1)[0]
        self.assertLess(
            module_submit_block.index("if (!actionResultPersisted(saved)) return;"),
            module_submit_block.index("markModuleFormClean(form);"),
        )

    def test_footer_distinguishes_clean_dirty_and_saving_states(self) -> None:
        self.assertIn('busy ? "正在保存更改" : dirty ? "有未保存更改" : "更改已保存"', self.script)
        self.assertIn('button.textContent = busy ? "处理中..."', self.script)
        self.assertIn("button.disabled = busy || !dirty;", self.script)
        self.assertIn("if (backButton) backButton.disabled = busy;", self.script)


if __name__ == "__main__":
    unittest.main()
