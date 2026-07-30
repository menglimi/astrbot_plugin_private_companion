# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.constants import PAGE_FONT_NAMES, PAGE_THEME_NAMES
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class PageFontOptionTests(unittest.TestCase):
    def test_server_accepts_every_supported_font(self) -> None:
        plugin = SimpleNamespace(config=None, page_font_family="original")
        api = PrivateCompanionPageApi(plugin)

        for font in PAGE_FONT_NAMES:
            with self.subTest(font=font):
                self.assertEqual(font, api._normalize_setting_value("page_font_family", font))
                api._apply_config_value("page_font_family", font)
                self.assertEqual(font, plugin.page_font_family)

        self.assertEqual("original", api._normalize_setting_value("page_font_family", "unknown"))

    def test_schema_and_frontend_expose_all_supported_fonts(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        schema_fonts = set(schema["page_font_family"]["options"])
        html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "陪伴面板" / "css" / "header-tools.css").read_text(encoding="utf-8")

        self.assertEqual(PAGE_FONT_NAMES, schema_fonts)
        for font in PAGE_FONT_NAMES:
            with self.subTest(font=font):
                self.assertIn(f'value="{font}"', html)
                self.assertIn(f'data-page-font="{font}"', css)

    def test_dark_theme_is_removed_and_legacy_value_falls_back_to_classic(self) -> None:
        plugin = SimpleNamespace(config=None, page_theme="dark")
        api = PrivateCompanionPageApi(plugin)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")

        self.assertNotIn("dark", PAGE_THEME_NAMES)
        self.assertNotIn("dark", schema["page_theme"]["options"])
        self.assertNotIn('{ value: "dark"', script)
        self.assertNotIn('data-theme="dark"', css)
        self.assertNotIn('"classic", "dark"', html)
        self.assertEqual("classic", api._normalize_setting_value("page_theme", "dark"))
        api._apply_config_value("page_theme", "dark")
        self.assertEqual("classic", plugin.page_theme)


if __name__ == "__main__":
    unittest.main()
