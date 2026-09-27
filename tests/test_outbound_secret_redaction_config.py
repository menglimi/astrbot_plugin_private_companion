# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

from tests.module_source_index import host_sources


class OutboundSecretRedactionConfigTests(unittest.TestCase):
    def test_schema_defaults_redaction_to_enabled(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        item = schema["basic_config"]["items"]["enable_outbound_secret_redaction"]
        self.assertEqual(item["type"], "bool")
        self.assertTrue(item["default"])
        domains = schema["basic_config"]["items"]["outbound_secret_redaction_trusted_domains"]
        self.assertEqual("list", domains["type"])
        self.assertEqual([], domains["default"])

    def test_final_send_guard_is_configurable_in_both_panels(self) -> None:
        # 最终发送守卫已随出站守卫域拆到 main_outbound_guard.py，
        # 故在「宿主 + 全部域模块」源码上断言，语义不变。
        source = "\n".join(path.read_text(encoding="utf-8") for path in host_sources(ROOT, "main"))
        expected_guard = 'getattr(self, "enable_outbound_secret_redaction", True)'
        self.assertIn(expected_guard, source)
        for relative in ("pages/companion-panel/app.js", "pages/陪伴面板/app.js"):
            panel = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("enable_outbound_secret_redaction", panel)
            self.assertIn("发送前敏感凭据脱敏", panel)


if __name__ == "__main__":
    unittest.main()
