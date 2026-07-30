# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BookshelfBrowserHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        page_root = ROOT / "pages" / "陪伴面板"
        cls.script = (page_root / "app.js").read_text(encoding="utf-8")
        cls.css = (page_root / "app.css").read_text(encoding="utf-8")
        cls.html = (page_root / "index.html").read_text(encoding="utf-8")

    def test_history_item_uses_stable_two_row_content(self) -> None:
        self.assertIn('class="browser-history-meta"', self.script)
        self.assertIn('aria-current="${isCurrent ? "true" : "false"}"', self.script)
        self.assertIn("grid-template-rows: auto minmax(2.7em, auto);", self.css)
        self.assertIn("min-height: 68px;", self.css)
        self.assertIn("-webkit-line-clamp: 2;", self.css)

    def test_history_fix_busts_cached_script_and_stylesheet(self) -> None:
        self.assertIn("./app.css?v=", self.html)
        self.assertIn("./app.js?v=", self.html)


if __name__ == "__main__":
    unittest.main()
