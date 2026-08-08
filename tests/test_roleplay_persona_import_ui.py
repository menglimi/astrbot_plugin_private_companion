from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "陪伴面板"
HTML = (PAGE / "index.html").read_text(encoding="utf-8")
SCRIPT = (PAGE / "app.js").read_text(encoding="utf-8")
STYLE = (PAGE / "app.css").read_text(encoding="utf-8")


def _persona_import_markup() -> str:
    start = HTML.index('<details class="persona-import-panel">')
    end = HTML.index("</details>", start) + len("</details>")
    return HTML[start:end]


def test_persona_import_is_collapsed_and_clearly_named_by_default() -> None:
    markup = _persona_import_markup()

    assert markup.startswith('<details class="persona-import-panel">')
    assert "<details open" not in markup
    assert "快速从已有人格导入" in markup
    assert "主回复人格来源" not in markup
    assert '<summary tabindex="0">' in markup
    assert '<strong id="currentPersonaDisplay"' in markup


def test_persona_import_keeps_existing_control_contracts() -> None:
    markup = _persona_import_markup()

    assert "<fieldset" in markup
    assert "<legend>选择导入内容</legend>" in markup
    assert markup.count("data-roleplay-draft-scope=") == 3
    assert 'data-roleplay-draft-scope="persona" checked' in markup
    assert 'id="generateRoleplayDraftBtn"' in markup
    assert 'id="roleplayPersonaDraftPanel"' in markup


def test_persona_import_preview_preserves_state_and_reopens_disclosure() -> None:
    close_handler = SCRIPT.split(
        'panel.querySelector("[data-roleplay-draft-close]")', 1
    )[1].split("panel.querySelectorAll", 1)[0]

    assert 'panel.closest("details.persona-import-panel")' in SCRIPT
    assert "if (!state.roleplayPersonaDraft)" in SCRIPT
    assert "disclosure.open = true" in SCRIPT
    assert "panel.hidden = true" in close_handler
    assert "state.roleplayPersonaDraft = null" not in close_handler


def test_persona_import_has_keyboard_and_mobile_layout_styles() -> None:
    assert ".persona-import-panel > summary:focus-visible" in STYLE
    assert "@media (max-width: 860px)" in STYLE
    assert ".persona-draft-scope-options" in STYLE


def test_global_click_handler_uses_the_normalized_event_target() -> None:
    assert (
        'const refreshBindingsButton = element?.closest('
        '"[data-setup-guide-refresh-bindings]");'
    ) in SCRIPT
    assert 'const refreshBindingsButton = target.closest(' not in SCRIPT


def test_roleplay_has_multiple_gender_neutral_persona_presets() -> None:
    start = HTML.index('<section class="persona-card is-active"')
    end = HTML.index("</section>", start)
    markup = HTML[start:end]
    for preset in ("persona", "persona_male", "persona_female", "persona_nonhuman"):
        assert f'data-roleplay-example="{preset}"' in markup
    assert "const roleplayPersonaPresets" in SCRIPT
    assert '"gender": "未指定；使用角色名或 TA，避免自行猜测性别"' in SCRIPT
    assert 'kind === "persona" || kind.startsWith("persona_")' in SCRIPT
