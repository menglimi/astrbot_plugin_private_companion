# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ROOT / "pages" / "companion-panel"
LOCALIZED = ROOT / "pages" / "陪伴面板"


class MultiPersonaConfigUiTests(unittest.TestCase):
    def test_panel_assets_stay_byte_identical(self) -> None:
        for relative in ("index.html", "app.js", "css/polish.css"):
            self.assertEqual(
                (PRIMARY / relative).read_bytes(),
                (LOCALIZED / relative).read_bytes(),
                relative,
            )

    def test_config_order_has_single_persona_context(self) -> None:
        html = (PRIMARY / "index.html").read_text(encoding="utf-8")
        order = [
            html.index('id="config-common-settings"'),
            html.index('id="configPersonaSelector"'),
            html.index('id="proactiveOnlyModeCard"'),
            html.index('id="configPersonaStats"'),
            html.index('id="config-persona-settings"'),
        ]
        self.assertEqual(order, sorted(order))
        self.assertEqual(html.count('id="configPersonaSelect"'), 1)
        self.assertEqual(html.count('id="configStats"'), 1)

    def test_persona_selector_is_stateful_and_protects_drafts(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        for marker in (
            'selectedPersonaId: ""',
            "function persistSelectedPagePersonaId(personaId)",
            "function loadSelectedPersonaOverview(personaId)",
            "function renderConfigPersonaSelector()",
            "if (hasUnsavedChanges())",
            "discardAllFeatureChanges();",
            "discardUnsavedModuleFormChanges();",
            "discardUnsavedTtsProviderChanges();",
            "state.personaSelectionRequestSeq",
            "personaOperationBusyCount: 0",
            "function setPersonaOperationBusy(busy)",
            "select.disabled = Number(state.personaOperationBusyCount || 0) > 0;",
        ):
            self.assertIn(marker, script)

    def test_persona_operations_lock_the_single_selector(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        self.assertGreaterEqual(script.count("setPersonaOperationBusy(true);"), 5)
        self.assertGreaterEqual(script.count("setPersonaOperationBusy(false);"), 5)
        run_action = script.split("async function runAction(", 1)[1].split(
            "\n}\n\nfunction configSavedValue", 1
        )[0]
        self.assertIn("setPersonaOperationBusy(true);", run_action)
        self.assertIn("setPersonaOperationBusy(false);", run_action)
        self.assertIn("const bindingControl = event.currentTarget;", script)
        self.assertIn("bindingControl.disabled = false;", script)

    def test_window_binding_rows_support_edit_delete_and_auto_rebind_copy(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        for marker in (
            'data-persona-window-edit="${escapeHtml(windowKey)}"',
            'data-persona-window-delete="${escapeHtml(windowKey)}"',
            'postJson("/persona/window-bindings/delete"',
            'postJson("/persona/window-bindings"',
            "删除后将恢复自动识别",
            "function bindPersonaWindowBindingActions(root)",
        ):
            self.assertIn(marker, script)

    def test_multi_persona_management_uses_dedicated_feature_detail_workspace(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        self.assertIn('const personaMigrationCardHtml = "";', script)
        self.assertIn("function renderPersonaConfigManagement()", script)
        self.assertIn("function multiPersonaFeatureDetailPage()", script)
        self.assertIn("function setMultiPersonaModeDraft(enabled)", script)
        self.assertIn('state.selectedFeatureKey === "enable_multi_persona_mode"', script)
        self.assertIn("if (multiPersonaDetail) renderPersonaConfigManagement();", script)
        self.assertIn('data-common-multi-persona-toggle ${enabled ? "checked" : ""}', script)
        self.assertIn('detailPage?.querySelector("[data-common-multi-persona-toggle]")', script)
        self.assertIn('event.target?.closest?.(".persona-management-feature-view")', script)
        self.assertEqual(
            script.count('querySelector("[data-migrate-submit]")?.addEventListener'),
            1,
        )
        self.assertNotIn('aria-label="页面查看人格">${options.map', script)
        for marker in (
            'value="follow_primary"',
            'value="defaults"',
            'value="copy"',
            'postJson("/persona/config/create"',
            'postJson("/persona/config/detach-preview"',
            'postJson("/persona/config/detach-apply"',
        ):
            self.assertIn(marker, script)

    def test_common_summary_is_switch_only_and_targets_lower_management(self) -> None:
        html = (PRIMARY / "index.html").read_text(encoding="utf-8")
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        polish = (PRIMARY / "css" / "polish.css").read_text(encoding="utf-8")
        self.assertIn('id="multiPersonaModeSummary"', html)
        self.assertGreaterEqual(html.count("config=multi-persona-summary-v2"), 2)
        self.assertNotIn('id="config-persona-management"', html)
        self.assertIn("function renderMultiPersonaModeSummary()", script)
        self.assertIn("function multiPersonaModeDraftEnabled()", script)
        self.assertIn('data-common-multi-persona-summary', script)
        self.assertIn('state.selectedFeatureKey = "enable_multi_persona_mode";', script)
        self.assertIn('document.getElementById("config-feature-settings")?.scrollIntoView', script)
        self.assertNotIn('data-common-multi-persona-detail', script)
        self.assertNotIn('if (!enabled) {\n    root.innerHTML', script)
        self.assertNotIn('if (!modeRequested) return "";', script)
        self.assertIn('enable_multi_persona_mode: multiPersonaModeDraftEnabled()', script)
        self.assertIn("总开关关闭时保留并展示已有绑定", script)
        self.assertIn('data-persona-window-edit="${escapeHtml(windowKey)}" data-persona-window-target-id="${escapeHtml(personaId)}" ${modeRequested ? "" : "disabled"}', script)
        self.assertIn("#panel-config .multi-persona-mode-summary.on .feature-toggle-visual", polish)
        self.assertIn("#panel-config .multi-persona-mode-summary.dirty", polish)
        self.assertIn('class="config-persona-top-row"', html)
        self.assertIn('id="proactiveOnlyModeCard"', html)
        top_row = html.split('class="config-persona-top-row"', 1)[1].split('config-persona-stats', 1)[0]
        self.assertLess(top_row.index('id="proactiveOnlyModeCard"'), top_row.index('id="multiPersonaModeSummary"'))
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", polish)
        self.assertIn("grid-column: auto;", polish)
        self.assertIn("#panel-config .config-persona-top-row .proactive-mode-settings-row", polish)

        management = script.split("function renderPersonaConfigManagement()", 1)[1].split(
            "\nfunction multiPersonaFeatureDetailPage", 1
        )[0]
        self.assertNotIn('data-common-multi-persona-toggle', management)
        self.assertNotIn('查看详细说明', management)
        self.assertIn('type="checkbox" data-common-persona-id', script)
        self.assertIn('class="persona-topology-switch"', script)
        self.assertIn('const primary = id === primaryPersonaId;', script)
        self.assertIn('const requestedPrimaryPersonaId = String(settings.plugin_specific_persona_id || "").trim();', script)
        self.assertIn('<option value="" disabled hidden selected>未选择</option>', script)
        self.assertIn('topologyPrimary?.addEventListener("change"', script)
        self.assertIn('input.disabled = isPrimary;', script)
        self.assertIn('const syncCreatePersonaOptions = () => {', script)
        self.assertIn('return id && id !== primary && enabledIds.has(id);', script)
        self.assertIn('const createPersonaRecords = enabledPersonaRecords.filter', script)
        self.assertIn('const createPersonaOptions = createPersonaRecords.length', script)
        self.assertIn('<select name="persona_id" required>${createPersonaOptions}</select>', script)
        self.assertIn('const personaBotNameMap = Object.fromEntries(', script)
        self.assertIn('sourceSelect.disabled = !copying;', script)
        self.assertIn('${escapeHtml(String(item.id || "").trim())} · ${escapeHtml(personaBotName(item))}', script)
        self.assertIn('const createdPersonaIds = new Set(normalizeMultiPersonaIds(state.multiPersona?.profiles));', script)


if __name__ == "__main__":
    unittest.main()
