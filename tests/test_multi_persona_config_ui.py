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

    def test_multi_persona_management_is_not_rendered_in_feature_detail(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        self.assertIn('const personaMigrationCardHtml = "";', script)
        self.assertIn("function renderPersonaConfigManagement()", script)
        self.assertIn("Configuration and life-data migration are managed once in Common", script)
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


if __name__ == "__main__":
    unittest.main()
