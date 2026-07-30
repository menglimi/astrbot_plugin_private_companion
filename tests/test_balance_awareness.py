# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.balance_awareness import BalanceAwarenessMixin


class _ConfigContext:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config


class _BalanceHarness(BalanceAwarenessMixin):
    def __init__(self, config):
        self.context = _ConfigContext(config)
        self.data = {"balance_awareness": {}}
        self.llm_provider_id = "primary/model"
        self.mai_style_provider_id = ""
        self.response_review_provider_id = ""
        self.proactive_persona_judge_provider_id = ""
        self.fast_response_provider_id = ""
        self.balance_json_path = ""
        self.balance_total_json_path = ""
        self.balance_used_json_path = ""
        self.balance_value_divisor = 1
        self.balance_request_timeout_seconds = 10
        self.requests = []

    async def _balance_request_json(self, url, headers):
        self.requests.append((url, headers))
        if url.endswith("/user/balance"):
            return {
                "balance_infos": [
                    {"currency": "CNY", "total_balance": "12.50"},
                ]
            }
        raise RuntimeError("unsupported endpoint")


class _UnsupportedBalanceHarness(_BalanceHarness):
    def __init__(self, config):
        super().__init__(config)
        self.enable_balance_awareness = True
        self.balance_api_url = ""
        self.balance_api_key = ""
        self.balance_check_interval_minutes = 60
        self.fetch_count = 0

    async def _fetch_balance_snapshot(self):
        self.fetch_count += 1
        raise RuntimeError("未从 primary 找到受支持的余额查询接口")


class BalanceAwarenessTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _config():
        return {
            "provider_sources": [
                {
                    "id": "secondary",
                    "enable": True,
                    "provider": "openai",
                    "provider_type": "chat_completion",
                    "api_base": "https://relay.example/v1",
                    "key": "secondary-secret",
                },
                {
                    "id": "primary",
                    "enable": True,
                    "provider": "deepseek",
                    "provider_type": "chat_completion",
                    "api_base": "https://api.deepseek.example/v1",
                    "key": "primary-secret",
                },
            ],
            "provider_settings": {"default_provider_id": "secondary/model"},
        }

    async def test_auto_query_prefers_active_provider_and_extracts_nested_balance(self):
        harness = _BalanceHarness(self._config())
        snapshot = await harness._fetch_auto_balance_snapshot()
        self.assertEqual(snapshot["amount"], 12.5)
        self.assertEqual(snapshot["source_id"], "primary")
        self.assertEqual(snapshot["query_mode"], "auto")
        self.assertEqual(harness.requests[0][0], "https://api.deepseek.example/user/balance")
        self.assertEqual(harness.requests[0][1]["Authorization"], "Bearer primary-secret")

    async def test_auto_query_never_moves_provider_key_to_another_origin(self):
        harness = _BalanceHarness(self._config())
        sources = harness._balance_auto_provider_sources()
        for source in sources:
            base_host = source["api_base"].split("/", 3)[2]
            for endpoint in harness._balance_auto_endpoint_urls(source):
                self.assertEqual(endpoint.split("/", 3)[2], base_host)

    async def test_normal_third_party_provider_order_keeps_default_after_plugin_provider(self):
        harness = _BalanceHarness(self._config())
        sources = harness._balance_auto_provider_sources()
        self.assertEqual([source["id"] for source in sources[:2]], ["primary", "secondary"])

    async def test_unsupported_auto_query_uses_long_backoff(self):
        harness = _UnsupportedBalanceHarness(self._config())

        with patch("astrbot_plugin_private_companion.balance_awareness._now_ts", return_value=1000.0):
            await harness._maybe_refresh_balance_awareness()
            await harness._maybe_refresh_balance_awareness()

        state = harness.data["balance_awareness"]
        self.assertEqual(harness.fetch_count, 1)
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertEqual(state["next_check_at"], 1000.0 + 12 * 3600.0)
        self.assertTrue(state["config_signature"])

        with patch("astrbot_plugin_private_companion.balance_awareness._now_ts", return_value=1000.0 + 12 * 3600.0):
            await harness._maybe_refresh_balance_awareness()

        self.assertEqual(harness.fetch_count, 2)
        self.assertEqual(state["consecutive_failures"], 2)
        self.assertEqual(state["next_check_at"], 1000.0 + 12 * 3600.0 + 24 * 3600.0)

    async def test_balance_config_change_bypasses_previous_backoff(self):
        harness = _UnsupportedBalanceHarness(self._config())
        with patch("astrbot_plugin_private_companion.balance_awareness._now_ts", return_value=1000.0):
            await harness._maybe_refresh_balance_awareness()

        harness.balance_api_url = "https://balance.example/api"
        with patch("astrbot_plugin_private_companion.balance_awareness._now_ts", return_value=2000.0):
            await harness._maybe_refresh_balance_awareness()

        state = harness.data["balance_awareness"]
        self.assertEqual(harness.fetch_count, 2)
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertEqual(state["next_check_at"], 2300.0)


if __name__ == "__main__":
    unittest.main()
