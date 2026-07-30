# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class BalancePageTests(unittest.TestCase):
    def _api(self) -> PrivateCompanionPageApi:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace(
            enable_balance_awareness=True,
            balance_api_url="https://secret.example/balance",
            balance_api_key="secret-key",
            balance_currency_label="元",
            daily_token_limit=0,
            daily_token_soft_limit=0,
            enable_daily_token_soft_limit=False,
        )
        return api

    def test_balance_switch_is_exposed_as_feature(self) -> None:
        api = self._api()
        self.assertIn("enable_balance_awareness", api._allowed_feature_keys())
        self.assertTrue(api._feature_flags()["enable_balance_awareness"])

    def test_token_payload_contains_safe_balance_snapshot(self) -> None:
        api = self._api()
        payload = api._token_stats_payload(
            {},
            {
                "amount": 8.5,
                "total": 100,
                "remaining_percent": 8.5,
                "tier": "low",
                "last_check_at": 1000,
                "last_success_at": 1000,
                "next_check_at": 2000,
            },
        )
        balance = payload["balance"]
        self.assertTrue(balance["available"])
        self.assertEqual(balance["amount"], 8.5)
        self.assertEqual(balance["currency_label"], "元")
        self.assertEqual(balance["tier"], "low")
        serialized = repr(payload)
        self.assertNotIn("secret.example", serialized)
        self.assertNotIn("secret-key", serialized)

    def test_token_payload_reports_auto_discovery_without_manual_url(self) -> None:
        api = self._api()
        api.plugin.balance_api_url = ""
        api.plugin._balance_auto_discovery_available = lambda: True
        balance = api._balance_status_payload(
            {
                "amount": 12.5,
                "tier": "normal",
                "last_success_at": 1000,
                "query_mode": "auto",
                "auto_source_id": "provider-a",
            }
        )
        self.assertTrue(balance["configured"])
        self.assertFalse(balance["manual_configured"])
        self.assertTrue(balance["auto_discovery_available"])
        self.assertEqual(balance["query_mode"], "auto")
        self.assertEqual(balance["source_label"], "provider-a")


if __name__ == "__main__":
    unittest.main()
