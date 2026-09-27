# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path

from tests.module_source_index import page_api_source_text, file_family_source_text


ROOT = Path(__file__).resolve().parents[1]


class OpenRouterConfigUiTests(unittest.TestCase):
    def test_page_setting_normalizer_accepts_openrouter_aliases(self) -> None:
        # 拆分后 alias 块落在 page_api_settings_part02.py，只读 page_api_settings.py 宿主会漏掉。
        source = file_family_source_text(ROOT, "page_api_settings.py")
        alias_block = source.split('if key in {"external_image_api_platform", "backup_external_image_api_platform"}', 1)[1].split(
            "return _SETTING_UNHANDLED", 1
        )[0]
        for alias in ("openrouter", "open-router", "open_router", "openrouter.ai"):
            with self.subTest(alias=alias):
                self.assertIn(f'"{alias}": "openrouter"', alias_block)
        self.assertIn('"openrouter"', alias_block)

    def test_manual_command_platform_choices_include_openrouter(self) -> None:
        # 拆分后 platform spec 落在 command_handlers_cm_snapshot_config.py，用 family 全文搜索。
        source = file_family_source_text(ROOT, "command_handlers.py")
        for key in ("external_image_api_platform", "backup_external_image_api_platform"):
            spec = source.split(f'"{key}": {{', 1)[1].split(
                '"backup_external_image_api_timeout_seconds"', 1
            )[0]
            with self.subTest(key=key):
                self.assertIn('"openrouter"', spec)
                for alias in ("open-router", "open_router", "openrouter.ai"):
                    self.assertIn(f'"{alias}": "openrouter"', spec)

    def test_runtime_endpoint_summary_uses_openrouter_label(self) -> None:
        source = page_api_source_text(ROOT)
        labels = source.split("platform_labels = {", 1)[1].split("}", 1)[0]
        self.assertIn('"openrouter": "OpenRouter"', labels)

    def test_404_is_classified_as_endpoint_mismatch_before_network(self) -> None:
        source = page_api_source_text(ROOT)
        rules = source.split("rules = [", 1)[1]
        endpoint_rule = rules.index('"endpoint_mismatch"')
        network_rule = rules.index('"network"')
        self.assertLess(endpoint_rule, network_rule)
        endpoint_block = rules[endpoint_rule:network_rule]
        for message, needle in (
            ("HTTP 404: endpoint not found", "http 404"),
            ("未找到生图接口", "未找到生图接口"),
            ("端点不匹配：请检查 URL", "端点不匹配"),
        ):
            with self.subTest(message=message):
                self.assertIn(needle.lower(), rules.lower())
        self.assertIn('"端点不匹配"', endpoint_block)
        self.assertIn("False", endpoint_block)

    def test_both_panel_variants_expose_openrouter_options_and_hints(self) -> None:
        chinese = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        ascii_panel = (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(chinese, ascii_panel)
        for script in (chinese, ascii_panel):
            self.assertIn('["openrouter", "OpenRouter"]', script)
            self.assertIn('"open-router": "openrouter"', script)
            self.assertIn('"open_router": "openrouter"', script)
            self.assertIn('"openrouter.ai": "openrouter"', script)
            self.assertIn("input_references", script)

    def test_schema_and_runtime_whitelist_document_openrouter(self) -> None:
        schema = (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        page_api = page_api_source_text(ROOT)
        self.assertIn("auto、openai、openrouter", schema)
        self.assertIn('"openrouter": "OpenRouter"', page_api)
        self.assertIn('"auto", "openai", "openrouter"', page_api)


if __name__ == "__main__":
    unittest.main()
