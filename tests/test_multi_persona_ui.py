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
        assert 'display = `${label} · ${id}`;' in helper


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


def test_window_binding_uses_available_personas_before_profile_save():
    for script in _panel_scripts():
        assert "function multiPersonaBindingIds(root = document)" in script
        assert "const available = (state.roleplayPersonas || [])" in script
        assert "async function ensureMultiPersonaBindingProfile(personaId" in script
        assert 'enable_multi_persona_mode: true' in script
        assert 'await ensureMultiPersonaBindingProfile(personaId, detailPage);' in script
        assert "const bindingEditable = modeRequested && bindingIds.length > 0;" in script
        assert 'data-persona-window-target ${bindingEditable ? "" : "disabled"}' in script


def test_window_binding_stays_visible_while_editing_requires_enabled_mode():
    for script in _panel_scripts():
        migration_start = script.index("function multiPersonaMigrationDetailCard()")
        migration_end = script.index("function bodyMonitorFeatureDetailCard()", migration_start)
        migration = script[migration_start:migration_end]
        assert 'if (!modeRequested) return "";' not in migration
        assert "总开关关闭时保留并展示已有绑定" in migration
        assert 'data-persona-window-bind ${bindingEditable ? "" : "disabled"}' in migration

        ensure_start = script.index("async function ensureMultiPersonaBindingProfile(")
        ensure_end = script.index("const MULTI_PERSONA_MIGRATION_KEYS", ensure_start)
        ensure = script[ensure_start:ensure_end]
        assert "const returnedIds = saved?.settings?.multi_persona_ids;" in ensure
        assert "const savedModeEnabled = toBool(saved?.settings?.enable_multi_persona_mode);" in ensure
        assert "|| !savedModeEnabled" in ensure
        assert "!savedIds.includes(pid)" in ensure
        assert "服务器未确认该人格已加入多人格列表" in ensure
        assert 'state.featureDetailBaseline?.key === "enable_multi_persona_mode"' in ensure
        assert "persistedSettings.multi_persona_ids" in ensure
        assert "toBool(persistedSettings.enable_multi_persona_mode)" in ensure


def test_conflicting_window_binding_migrates_and_clears_cache_before_switch():
    for script in _panel_scripts():
        assert "const MULTI_PERSONA_MIGRATION_KEYS = Object.freeze([" in script
        assert "source_persona_id: sourcePersonaId" in script
        assert "migrate_keys: [...MULTI_PERSONA_MIGRATION_KEYS]" in script
        assert "result.migrated?.cache_cleared" in script
        assert "取消后可选择仅清理缓存并切换" in script
        assert "不迁移资料，仅清理原人格和目标人格缓存后切换窗口绑定吗" in script
        assert "result.cache_cleared" in script
        assert 'if (result.conflict) {' in script
        assert "窗口绑定已变化，请重新确认后再切换" in script
        assert "该窗口已绑定其他人格，确认改为当前选择？" not in script


def test_window_binding_rows_offer_persistent_unbind_action():
    for script in _panel_scripts():
        assert 'data-persona-window-unbind="${escapeHtml(windowKey)}"' in script
        assert 'postJson("/persona/unbind", { window_key: windowKey })' in script
        assert "现有人格资料和聊天记录不会删除" in script
        assert "delete nextBindings[windowKey]" in script
