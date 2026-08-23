# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATHS = (
    ROOT / "pages" / "companion-panel" / "app.js",
    ROOT / "pages" / "陪伴面板" / "app.js",
)


def _panel_scripts() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in PANEL_PATHS]


def test_multi_persona_panel_scripts_remain_exact_mirrors():
    assert PANEL_PATHS[0].read_bytes() == PANEL_PATHS[1].read_bytes()


def test_persona_display_label_preserves_label_and_full_id():
    for script in _panel_scripts():
        helper_start = script.index("function personaDisplayLabel(")
        helper_end = script.index("function cleanInterjectionText(", helper_start)
        helper = script[helper_start:helper_end]

        assert 'const rawId = String(input?.id ?? personaOrId ?? "");' in helper
        assert (
            'const label = String(persona.label || persona.name || "").trim();'
            in helper
        )
        assert 'const cleanLabel = label.replace(/\\s*（默认）\\s*$/, "").trim();' in helper
        assert 'display = `${cleanLabel} · ${id}`;' in helper
        assert 'plugin_specific_persona_id: personaId' in script
        assert 'state.multiPersona = {' in script


def test_persona_switch_refreshes_bot_name_and_invalidates_worldbook_requests():
    for script in _panel_scripts():
        assert 'subtitle.textContent = `${overview.plugin?.bot_name || "Private Companion"} · 总览已加载`;' in script
        reset = script.split("function resetPersonaScopedPageState()", 1)[1].split(
            "async function selectPagePersona", 1
        )[0]
        assert "state.worldbookLivingMemory = {};" in reset
        assert "state.worldbookLivingMemoryRequestSeq += 1;" in reset
        loader = script.split("async function loadWorldbookLivingMemory", 1)[1].split(
            "async function handleWorldbookMemberAction", 1
        )[0]
        assert "const personaId = selectedPagePersonaId();" in loader
        assert "selectedPagePersonaId() !== personaId" in loader


def test_multi_persona_selectors_keep_raw_id_as_value_and_use_shared_label():
    for script in _panel_scripts():
        # Page selector, migration selectors, primary selector, and persona checkboxes
        # all submit the untouched ID while delegating visible text to one helper.
        assert script.count('value="${escapeHtml(String(item.id ?? ""))}"') >= 4
        assert script.count("personaDisplayLabel(item)") >= 4
        assert "personaDisplayLabel(id)" in script
        assert "personaDisplayLabel(item, { includeSource: true })" in script

        # Keep the previous label-only rendering from returning in persona controls.
        assert "escapeHtml(item.label || item.id)" not in script
        assert "escapeHtml(item.label || item.name || item.id)" not in script


def test_persona_migration_only_uses_saved_configured_profiles():
    for script in _panel_scripts():
        migration = script.split("function multiPersonaMigrationDetailCard()", 1)[1].split(
            "function bodyMonitorFeatureDetailCard", 1
        )[0]
        assert "state.multiPersona?.configured_profiles" in migration
        assert "state.multiPersona?.enabled_ids" in migration
        assert "已启用且已建立配置的人格" in migration


def test_plugin_routing_controls_and_api_calls_are_removed():
    for script in _panel_scripts():
        for marker in (
            "multi_persona_primary_id",
            "multi_persona_window_bindings",
            "/persona/window-bindings",
            "/persona/switch",
            "data-persona-window-edit",
            "data-persona-window-delete",
            "data-persona-window-bind",
            "function bindPersonaWindowBindingActions",
            "function ensureMultiPersonaBindingProfile",
        ):
            assert marker not in script


def test_primary_persona_is_read_only_and_owned_by_astrbot():
    for script in _panel_scripts():
        assert "function astrBotPersonaRecords()" in script
        assert 'source !== "独立资料" && source !== "插件配置"' in script
        assert "function configuredPrimaryPersonaId()" in script
        assert "plugin_specific_persona_id</code> · 状态 ${primaryPersonaId ? \"primary\" : \"missing\"} · 路由权威 AstrBot" in script
        assert '<select name="multi_persona_primary_id"' not in script
        topology = script.split('querySelector("[data-common-persona-topology]")', 1)[1].split(
            "const topologyForm", 1
        )[0]
        assert "multi_persona_primary_id:" not in topology


def test_missing_primary_setup_is_two_phase_and_legacy_routing_is_read_only():
    for script in _panel_scripts():
        assert 'data-persona-primary-setup' in script
        assert 'settings: { plugin_specific_persona_id: personaId }' in script
        primary_save = script.index('settings: { plugin_specific_persona_id: personaId }')
        mode_save = script.index('enable_multi_persona_mode: true', primary_save)
        assert primary_save < mode_save
        assert "请先在多人格设置中补充 AstrBot 有效主人格" in script
        assert "function multiPersonaLegacyRoutingNotice()" in script
        assert "status.legacy_routing" in script
        assert "旧插件窗口绑定已停用" in script
        assert "插件不会再读取或修改这些绑定" in script


def test_config_creation_uses_persisted_topology_and_detach_requires_real_config():
    for script in _panel_scripts():
        assert "state.multiPersona?.configured_profiles" in script
        assert 'const syncCreatePersonaOptions = () => {' not in script
        assert 'querySelectorAll("[data-common-persona-id]:checked") || [])\n        .map' not in script
        assert 'const candidates = copySourceRecords.filter((item) => String(item.id || "").trim() !== targetId);' in script
        assert 'if (mode === "copy" && sourcePersonaId === personaId)' in script
        assert '.filter((item) => configuredPersonaIds.has(String(item.id || "").trim()))' in script


def test_one_global_persona_state_drives_header_and_config_selectors():
    for script in _panel_scripts():
        assert script.count('selectedPersonaId: ""') == 1
        assert "function pagePersonaRecords()" in script
        assert "function renderPagePersonaSelector()" in script
        assert "async function selectPagePersona(nextPersonaId, control = null)" in script
        assert script.count("void selectPagePersona(event.currentTarget.value, event.currentTarget);") >= 2
        assert 'url.searchParams.set("_persona_id", selectedPersonaId);' in script
        assert 'body: JSON.stringify({ ...payload, _persona_id: personaId })' in script


def test_topology_default_marker_and_follow_confirmation_are_unambiguous():
    for script in _panel_scripts():
        assert "function personaTopologyLabel(personaOrId)" in script
        assert 'personaTopologyLabel(item)' in script
        assert "function consumePersonaFollowConfirmation(button)" in script
        assert 'button.textContent = "点击确认";' in script
        assert "window.setTimeout(() => resetPersonaFollowConfirmation(button), 3000)" in script
