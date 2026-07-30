# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class TogetherTokenPageTests(unittest.TestCase):
    @staticmethod
    def _api() -> PrivateCompanionPageApi:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace(
            daily_token_limit=0,
            daily_token_soft_limit=0,
            enable_daily_token_soft_limit=False,
            enable_balance_awareness=False,
            balance_api_url="",
        )
        return api

    def test_runtime_bridge_exposes_together_usage(self) -> None:
        bridge = SimpleNamespace(
            get_token_usage_summary=lambda: {
                "available": True,
                "display_name": "我会和你在一起",
                "totals": {"calls": 2, "total_tokens": 88},
            }
        )
        module = SimpleNamespace(
            PLUGIN_NAME="astrbot_plugin_together_companion",
            get_together_companion_bridge=lambda: bridge,
        )
        with patch.dict(sys.modules, {"astrbot_plugin_together_companion.main": module}):
            payload = self._api()._token_stats_payload({})["together_plugin"]

        self.assertTrue(payload["installed"])
        self.assertTrue(payload["available"])
        self.assertEqual(88, payload["totals"]["total_tokens"])

    def test_runtime_discovery_does_not_trigger_lazy_module_imports(self) -> None:
        class LazyModule(ModuleType):
            def __getattr__(self, name: str):
                if name == "PLUGIN_NAME":
                    raise ModuleNotFoundError("No module named 'torch'", name="torch")
                raise AttributeError(name)

        lazy_module = LazyModule("transformers.models.aria")
        with patch.dict(sys.modules, {"transformers.models.aria": lazy_module}):
            usage = self._api()._together_plugin_token_usage_raw()

        self.assertFalse(usage["available"])
        self.assertFalse(usage["installed"])

    def test_optional_together_stats_failure_does_not_break_page_payloads(self) -> None:
        api = self._api()

        def fail_usage():
            raise ModuleNotFoundError("No module named 'torchvision'", name="torchvision")

        api._together_plugin_token_usage_raw = fail_usage
        overview = api._token_overview_payload({})
        details = api._token_stats_payload({})

        self.assertFalse(overview["together_plugin"]["available"])
        self.assertFalse(details["together_plugin"]["available"])
        self.assertIn("不影响陪伴面板", overview["together_plugin"]["reason"])

    def test_missing_plugin_is_marked_hidden(self) -> None:
        api = self._api()
        api._together_plugin_token_usage_raw = lambda: {
            "available": False,
            "installed": False,
            "display_name": "我会和你在一起",
        }

        payload = api._token_stats_payload({})["together_plugin"]

        self.assertFalse(payload["installed"])

    def test_source_button_is_hidden_by_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        page = (root / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        script = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-token-source="together" hidden', page)
        self.assertIn("stats.together_plugin?.installed !== true", script)


if __name__ == "__main__":
    unittest.main()
