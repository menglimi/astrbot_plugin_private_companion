from __future__ import annotations

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
