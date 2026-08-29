from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_plugin_page_paths_keep_their_utf8_names() -> None:
    pages_root = ROOT / "pages"
    suspicious = [
        path.relative_to(ROOT).as_posix()
        for path in pages_root.rglob("*")
        if "?" in path.name or "\ufffd" in path.name
    ]

    assert suspicious == []
    assert (pages_root / "陪伴面板" / "index.html").is_file()
    assert (pages_root / "陪伴面板" / "app.js").is_file()
    assert (pages_root / "companion-panel" / "index.html").is_file()
    assert (pages_root / "companion-panel" / "app.js").is_file()


def test_ascii_plugin_page_mirror_matches_utf8_page() -> None:
    pages_root = ROOT / "pages"
    utf8_page = pages_root / "陪伴面板"
    ascii_page = pages_root / "companion-panel"

    utf8_files = sorted(
        path.relative_to(utf8_page).as_posix()
        for path in utf8_page.rglob("*")
        if path.is_file()
    )
    ascii_files = sorted(
        path.relative_to(ascii_page).as_posix()
        for path in ascii_page.rglob("*")
        if path.is_file()
    )

    assert ascii_files == utf8_files
    for rel_path in utf8_files:
        assert (ascii_page / rel_path).read_bytes() == (utf8_page / rel_path).read_bytes()


def test_ascii_plugin_page_has_localized_title() -> None:
    i18n_root = ROOT / ".astrbot-plugin" / "i18n"
    zh_cn = json.loads((i18n_root / "zh-CN.json").read_text(encoding="utf-8"))
    en_us = json.loads((i18n_root / "en-US.json").read_text(encoding="utf-8"))

    assert zh_cn["pages"]["companion-panel"]["title"] == "陪伴面板"
    assert zh_cn["pages"]["陪伴面板"]["title"] == "陪伴面板"
    assert en_us["pages"]["companion-panel"]["title"] == "Companion Panel"


def test_metadata_declares_only_the_ascii_plugin_page_alias() -> None:
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")

    assert "pages:" in metadata
    assert metadata.count("  - name: companion-panel") == 1
    assert "  - name: 陪伴面板" not in metadata
    assert "    title: 陪伴面板" in metadata


def test_plugin_page_token_compat_shim_is_scoped_to_companion_pages() -> None:
    source = (ROOT / "integration_status.py").read_text(encoding="utf-8")

    assert "target_ttl_seconds = 6 * 60 * 60" in source
    assert 'token_plugin_name == "astrbot_plugin_private_companion"' in source
    assert 'request_plugin_name == "astrbot_plugin_private_companion"' in source
    assert 'page_aliases = {"陪伴面板", "companion-panel"}' in source


def test_first_setup_proactive_test_exposes_save_and_test_progress() -> None:
    source = (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")

    assert 'status === "applying"' in source
    assert '正在保存当前配置，保存完成后会自动预约主动消息链路测试。' in source
    assert 'state.setupGuideProactiveTest = { status: "applying"' in source
    assert '首次配置仍在保存，请稍候再测试' in source
