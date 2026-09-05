# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


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
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        expected_guard = 'getattr(self, "enable_outbound_secret_redaction", True)'
        self.assertIn(expected_guard, source)
        for relative in ("pages/companion-panel/app.js", "pages/陪伴面板/app.js"):
            panel = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("enable_outbound_secret_redaction", panel)
            self.assertIn("发送前敏感凭据脱敏", panel)


if __name__ == "__main__":
    unittest.main()
