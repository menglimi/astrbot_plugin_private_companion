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
        self.assertEqual(html.count('id="pagePersonaSelect"'), 1)

    def test_persona_selector_is_stateful_and_protects_drafts(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        for marker in (
            'selectedPersonaId: ""',
            "function persistSelectedPagePersonaId(personaId)",
            "function loadSelectedPersonaOverview(personaId)",
            "function renderConfigPersonaSelector()",
            "function renderPagePersonaSelector()",
            "async function selectPagePersona(nextPersonaId, control = null)",
            "function resetPersonaScopedPageState()",
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
        self.assertGreaterEqual(script.count("setPersonaOperationBusy(true);"), 3)
        self.assertGreaterEqual(script.count("setPersonaOperationBusy(false);"), 3)
        run_action = script.split("async function runAction(", 1)[1].split(
            "\n}\n\nfunction configSavedValue", 1
        )[0]
        self.assertIn("setPersonaOperationBusy(true);", run_action)
        self.assertIn("setPersonaOperationBusy(false);", run_action)
        self.assertNotIn("const bindingControl = event.currentTarget;", script)

    def test_window_binding_crud_is_absent_and_legacy_notice_is_read_only(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        for marker in (
            "data-persona-window-edit",
            "data-persona-window-delete",
            'postJson("/persona/window-bindings',
            "function bindPersonaWindowBindingActions(root)",
        ):
            self.assertNotIn(marker, script)
        self.assertIn("function multiPersonaLegacyRoutingNotice()", script)
        self.assertIn("status.legacy_routing", script)

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
        self.assertNotIn("总开关关闭时保留并展示已有绑定", script)
        self.assertNotIn("data-persona-window-edit", script)
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
        self.assertIn('plugin_specific_persona_id</code> · 状态 ${primaryPersonaId ? "primary" : "missing"} · 路由权威 AstrBot', script)
        self.assertNotIn('<select name="multi_persona_primary_id"', script)
        self.assertNotIn('topologyPrimary?.addEventListener("change"', script)
        self.assertIn('${primary ? " disabled" : ""}', script)
        self.assertNotIn('const syncCreatePersonaOptions = () => {', script)
        self.assertIn('const configuredPersonaIds = new Set(normalizeMultiPersonaIds(state.multiPersona?.configured_profiles));', script)
        self.assertIn('configuredPersonaIds.has(id) ? "配置就绪" : enabledPersonaIds.has(id) ? "待创建配置"', script)
        self.assertIn('return id !== primaryPersonaId && !configuredPersonaIds.has(id);', script)
        self.assertIn('const configuredSourceIds = new Set(normalizeMultiPersonaIds(state.multiPersona?.configured_profiles));', script)
        self.assertIn('const candidates = copySourceRecords.filter((item) => String(item.id || "").trim() !== targetId);', script)
        self.assertIn('sourceSelect.disabled = createMode?.value !== "copy" || !candidates.length;', script)
        self.assertIn('if (mode === "copy" && sourcePersonaId === personaId)', script)
        self.assertNotIn('querySelectorAll("[data-common-persona-id]:checked") || [])\n        .map', script)
        self.assertIn('const createPersonaRecords = primaryMissing', script)
        self.assertIn('const createPersonaOptions = createPersonaRecords.length', script)
        self.assertIn('<select name="persona_id" required>${createPersonaOptions}</select>', script)
        self.assertIn('<option value="">暂无可选择人格</option>', script)
        self.assertNotIn('暂无已启用人格', script)
        self.assertIn('const personaBotNameMap = Object.fromEntries(', script)
        self.assertIn('${escapeHtml(String(item.id || "").trim())} · ${escapeHtml(personaBotName(item))}', script)
        self.assertIn('.filter((item) => configuredPersonaIds.has(String(item.id || "").trim()))', script)
        self.assertIn('const createPersonaRecords = primaryMissing', script)
        self.assertIn('data-persona-primary-setup', script)
        self.assertIn('settings: { plugin_specific_persona_id: personaId }', script)
        self.assertIn('source !== "独立资料" && source !== "插件配置"', script)
        self.assertNotIn("multi_persona_primary_id", script)

    def test_config_and_header_selectors_only_use_available_profile_records(self) -> None:
        html = (PRIMARY / "index.html").read_text(encoding="utf-8")
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        polish = (PRIMARY / "css" / "polish.css").read_text(encoding="utf-8")
        self.assertIn('id="pagePersonaSelector"', html)
        self.assertIn('id="pagePersonaSelect"', html)
        self.assertIn('function pagePersonaRecords()', script)
        self.assertIn('const available = new Set(normalizeMultiPersonaIds(state.multiPersona?.profiles));', script)
        self.assertIn('state.multiPersona?.profile_labels?.[id]', script)
        self.assertIn('const records = pagePersonaRecords();', script)
        self.assertIn('const records = enabled ? pagePersonaRecords() : [];', script)
        self.assertGreaterEqual(script.count('void selectPagePersona(event.currentTarget.value, event.currentTarget);'), 2)
        self.assertIn('await loadSelectedPersonaOverview(next);', script)
        self.assertIn('await ensureTabData(state.activeTab, true).catch(() => {});', script)
        self.assertIn('url.searchParams.set("_persona_id", selectedPersonaId);', script)
        self.assertIn('.page-persona-selector select', polish)
        self.assertIn('.folio-persona-context', polish)

    def test_topology_hides_astrbot_default_marker(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        self.assertIn("function personaTopologyLabel(personaOrId)", script)
        self.assertIn('const cleanLabel = label.replace(/\\s*（默认）\\s*$/, "").trim();', script)
        self.assertIn('replace(/\\s*（(?:默认|主人格)）\\s*/g, " ")', script)
        self.assertIn('replace(/\\s*（(?:默认|主人格|插件当前指定)）\\s*$/, "")', script)
        topology = script.split('const topologyPersonaChecks =', 1)[1].split('const configuredPersonaRecords', 1)[0]
        self.assertIn('personaTopologyLabel(item)', topology)
        self.assertNotIn('personaDisplayLabel(item)', topology)

    def test_restore_follow_is_inline_and_uses_three_second_button_confirmation(self) -> None:
        script = (PRIMARY / "app.js").read_text(encoding="utf-8")
        css = (PRIMARY / "app.css").read_text(encoding="utf-8")
        self.assertIn("const personaFollowConfirmTimers = new WeakMap();", script)
        self.assertIn("function consumePersonaFollowConfirmation(button)", script)
        self.assertIn('button.textContent = "点击确认";', script)
        self.assertIn("window.setTimeout(() => resetPersonaFollowConfirmation(button), 3000)", script)
        self.assertGreaterEqual(script.count("if (!consumePersonaFollowConfirmation(button)) return;"), 2)
        self.assertNotIn('window.confirm("当前人格还有未保存的配置，恢复跟随不会提交这些草稿。继续吗？")', script)
        feature_card = script.split("function featureSwitchItem(key)", 1)[1].split("function relationshipStageCapLabel", 1)[0]
        meta = feature_card.split('<div class="feature-switch-meta">', 1)[1].split("</div>", 1)[0]
        self.assertLess(meta.index("${sourceBadge}"), meta.index("${followButton}"))
        self.assertNotIn("</div>\n      ${followButton}", feature_card)
        self.assertIn(".persona-follow-button.is-confirming", css)
        self.assertIn("min-width: 68px;", css)


if __name__ == "__main__":
    unittest.main()
