# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StorageSafetyConfigPageTests(unittest.TestCase):
    def test_extension_page_exposes_and_saves_safety_toggle(self) -> None:
        html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="storageSafetyCleanupToggle"', html)
        self.assertIn("数据安全清理", html)
        self.assertIn("settings.enable_store_control_tag_sanitization", script)
        self.assertIn("enable_store_control_tag_sanitization: safetyEnabled", script)
        self.assertIn("数据安全清理：<b>", script)


if __name__ == "__main__":
    unittest.main()
