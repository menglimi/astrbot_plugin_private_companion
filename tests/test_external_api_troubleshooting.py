# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class _ExternalApiPluginHarness:
    def __init__(self) -> None:
        self.config = {}
        self.data = {
            "daily_weather": {"prompt": "正式天气缓存"},
            "qweather_location": {"location_id": "live-location"},
            "balance_awareness": {"auto_source_id": "live-provider"},
            "web_search_runtime": {"tavily": {"retry_after": 123}},
        }
        self._data_lock = asyncio.Lock()
        self.save_calls = 0

        self.weather_source = "qweather"
        self.weather_api_host = "https://saved.weather.example"
        self.weather_token = "saved-weather-token"
        self.weather_location = "已保存地点"
        self.weather_api_key = ""
        self.weather_city = ""
        self.weather_amap_api_key = ""
        self.weather_amap_city = ""
        self.weather_lat = 0.0
        self.weather_lon = 0.0

        self.balance_api_url = "https://saved.balance.example/balance"
        self.balance_api_key = "saved-ba" + "lance-key"
        self.balance_api_auth_header = "Authorization"
        self.balance_api_auth_scheme = "Bearer"
        self.balance_api_custom_headers = ""
        self.balance_json_path = ""
        self.balance_total_json_path = ""
        self.balance_used_json_path = ""
        self.balance_value_divisor = 1.0
        self.balance_currency_label = "元"
        self.balance_request_timeout_seconds = 10.0

        self.web_exploration_api_base_url = ""
        self.web_exploration_api_key = ""
        self.web_exploration_api_model = ""
        self.web_exploration_max_results = 6
        self._last_web_search_error = "live-error"
        self._format_timestamp_elapsed = lambda _value: "刚刚"

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_calls += 1

    async def _fetch_own_weather_prompt(self):
        self.data["daily_weather"]["prompt"] = "测试副本天气"
        self.data["qweather_location"] = {"location_id": "test-location"}
        self._save_data_sync()
        if self.weather_location == "空结果":
            return {"prompt": "", "source": ""}
        if self.weather_location == "抛出异常":
            raise RuntimeError(f"Authorization: Bearer {self.weather_token}")
        return {
            "prompt": "当前天气 晴，约 26°C。",
            "source": "qweather",
            "location_label": self.weather_location,
        }

    async def _fetch_balance_snapshot(self):
        if "error" in self.balance_api_url:
            header_secret = self.balance_api_custom_headers.split(":", 1)[-1].strip()
            raise RuntimeError(
                f"HTTP 401 {self.balance_api_url} api_key={self.balance_api_key} header={header_secret}"
            )
        if self.balance_api_url:
            return {
                "query_mode": "manual",
                "source_id": "custom",
                "endpoint_path": "/balance",
                "amount": 12.5,
                "total": 100.0,
                "used": 87.5,
                "remaining_percent": 12.5,
            }
        return {
            "query_mode": "auto",
            "source_id": "provider-a",
            "endpoint_path": "/user/balance",
            "amount": 8.0,
            "total": None,
            "used": None,
            "remaining_percent": None,
        }

    def _balance_safe_error(self, exc: Exception) -> str:
        text = str(exc)
        if self.balance_api_key:
            text = text.replace(self.balance_api_key, "***")
        if self.balance_api_url:
            text = text.replace(self.balance_api_url, "<余额接口>")
        return text

    def _custom_web_exploration_search_configured(self) -> bool:
        return bool(self.web_exploration_api_base_url)

    def _astrbot_any_web_search_available(self) -> bool:
        return True

    def _pick_available_web_search_umo(self, _preferred: str = "") -> str:
        return ""

    async def _run_astrbot_web_search(self, query: str, *, umo: str = "", topic: str = "general", usage: str = "general"):
        self.data["web_search_runtime"] = {"test": {"retry_after": 999}}
        self._save_data_sync()
        if query == "空结果":
            self._last_web_search_error = (
                f"HTTP 429 {self.web_exploration_api_base_url}?token={self.web_exploration_api_key}"
            )
            return []
        provider = "custom_web_exploration" if self.web_exploration_api_base_url else "tavily"
        return [
            {
                "title": "第一条结果",
                "snippet": f"{topic}/{usage} 返回的摘要",
                "url": "https://result.example/private?token=hidden",
                "provider": provider,
            },
            {
                "title": "第二条结果",
                "snippet": "另一条摘要",
                "url": "https://result.example/2",
                "provider": provider,
            },
        ]


class ExternalApiTroubleshootingBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = _ExternalApiPluginHarness()
        self.api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        self.api.plugin = self.plugin
        self.api._schema_key_index_cache = None

    async def test_weather_uses_unsaved_settings_without_mutating_live_state(self) -> None:
        live_data = deepcopy(self.plugin.data)
        result = await self.api._run_external_api_test(
            "weather_api",
            {
                "settings": {
                    "weather_source": "qweather",
                    "weather_api_host": "https://temporary.weather.example",
                    "weather_token": "temporary-weather-token",
                    "weather_location": "北京",
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "qweather")
        self.assertEqual(result["location_label"], "北京")
        self.assertEqual(self.plugin.weather_location, "已保存地点")
        self.assertEqual(self.plugin.data, live_data)
        self.assertEqual(self.plugin.save_calls, 0)

    async def test_weather_empty_result_and_exception_are_safe_failures(self) -> None:
        empty = await self.api._run_external_api_test(
            "weather_api",
            {"settings": {"weather_location": "空结果"}},
        )
        self.assertFalse(empty["ok"])
        self.assertIn("未返回有效", empty["error"])

        secret = "temporary-weather-secret"
        failed = await self.api._run_external_api_test(
            "weather_api",
            {"settings": {"weather_location": "抛出异常", "weather_token": secret}},
        )
        self.assertFalse(failed["ok"])
        self.assertNotIn(secret, repr(failed))
        self.assertIn("密钥已隐藏", repr(failed))

    async def test_balance_manual_and_auto_modes_return_bounded_fields(self) -> None:
        manual = await self.api._run_external_api_test(
            "balance_api",
            {
                "settings": {
                    "balance_api_url": "https://temporary.balance.example/balance",
                    "balance_currency_label": "元",
                }
            },
        )
        self.assertTrue(manual["ok"])
        self.assertEqual(manual["query_mode"], "manual")
        self.assertEqual(manual["endpoint_path"], "/balance")
        self.assertEqual(manual["amount"], 12.5)

        automatic = await self.api._run_external_api_test(
            "balance_api",
            {"settings": {"balance_api_url": ""}},
        )
        self.assertTrue(automatic["ok"])
        self.assertEqual(automatic["query_mode"], "auto")
        self.assertEqual(automatic["source_id"], "provider-a")

    async def test_balance_failure_redacts_temporary_key_headers_and_url(self) -> None:
        api_key = "temporar" + "y-balance-key"
        header_secret = "temporary-header-secret"
        url = "https://error.balance.example/balance?token=temporary-url-secret"
        result = await self.api._run_external_api_test(
            "balance_api",
            {
                "settings": {
                    "balance_api_url": url,
                    "balance_api_key": api_key,
                    "balance_api_custom_headers": f"X-Private: {header_secret}",
                }
            },
        )

        rendered = repr(result)
        self.assertFalse(result["ok"])
        self.assertNotIn(api_key, rendered)
        self.assertNotIn(header_secret, rendered)
        self.assertNotIn("temporary-url-secret", rendered)

    async def test_search_uses_custom_or_astrbot_provider_and_isolates_cooldown(self) -> None:
        live_data = deepcopy(self.plugin.data)
        custom = await self.api._run_external_api_test(
            "web_search",
            {
                "query": "北京今天有什么新闻",
                "settings": {
                    "WEB_EXPLORATION_API_BASE_URL": "https://search.example/v1/search",
                    "WEB_EXPLORATION_API_KEY": "temporary-search-key",
                    "WEB_EXPLORATION_API_MODEL": "search-model",
                },
            },
        )
        self.assertTrue(custom["ok"])
        self.assertEqual(custom["provider"], "custom_web_exploration")
        self.assertEqual(custom["result_count"], 2)
        self.assertEqual(len(custom["result_preview"]), 2)
        self.assertNotIn("url", repr(custom["result_preview"]).lower())
        self.assertEqual(self.plugin.data, live_data)
        self.assertEqual(self.plugin._last_web_search_error, "live-error")

        fallback = await self.api._run_external_api_test(
            "web_search",
            {"query": "普通搜索", "settings": {"WEB_EXPLORATION_API_BASE_URL": ""}},
        )
        self.assertTrue(fallback["ok"])
        self.assertEqual(fallback["provider"], "tavily")

    async def test_search_empty_result_keeps_cooldown_hint_but_hides_credentials(self) -> None:
        secret = "temporary-search-key"
        result = await self.api._run_external_api_test(
            "web_search",
            {
                "query": "空结果",
                "settings": {
                    "WEB_EXPLORATION_API_BASE_URL": "https://search.example/v1/search",
                    "WEB_EXPLORATION_API_KEY": secret,
                },
            },
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["result_count"], 0)
        self.assertNotIn(secret, repr(result))
        self.assertIn("429", result["error"])

    def test_all_types_keep_their_public_diagnostic_contract(self) -> None:
        samples = {
            "weather_api": {"provider": "qweather", "source": "qweather", "location_label": "北京"},
            "balance_api": {
                "query_mode": "manual",
                "source_id": "custom",
                "endpoint_path": "/balance?secret=hidden",
                "amount": 0,
                "total": 100,
                "used": 100,
                "remaining_percent": 0,
                "currency_label": "元",
            },
            "web_search": {
                "provider": "tavily",
                "result_count": 1,
                "result_preview": [{"title": "标题", "snippet": "摘要", "url": "https://hidden.example"}],
            },
        }
        for test_type, fields in samples.items():
            with self.subTest(test_type=test_type):
                result = self.api._diagnostic_envelope(
                    {"ok": True, "type": test_type, "request_id": "012345abcdef", **fields},
                    test_type=test_type,
                )
                self.assertEqual(result["type"], test_type)
                self.assertTrue(result["test_id"].startswith(f"diag_{test_type}_"))
                if test_type == "balance_api":
                    self.assertEqual(result["amount"], 0.0)
                    self.assertEqual(result["endpoint_path"], "/balance")
                if test_type == "web_search":
                    self.assertNotIn("url", repr(result["result_preview"]).lower())

    async def test_unified_endpoint_dispatches_and_persists_all_three_envelopes(self) -> None:
        payloads = {
            "weather_api": {
                "type": "weather_api",
                "settings": {
                    "weather_source": "qweather",
                    "weather_api_host": "https://temporary.weather.example",
                    "weather_token": "temporary-weather-token",
                    "weather_location": "北京",
                },
            },
            "balance_api": {
                "type": "balance_api",
                "settings": {"balance_api_url": "https://temporary.balance.example/balance"},
            },
            "web_search": {
                "type": "web_search",
                "query": "北京今天有什么新闻",
                "settings": {"WEB_EXPLORATION_API_BASE_URL": ""},
            },
        }
        for test_type, payload in payloads.items():
            with self.subTest(test_type=test_type):
                fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))
                with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
                    response = await self.api.run_troubleshooting_test()
                result = response["data"]
                self.assertTrue(response["success"])
                self.assertTrue(result["ok"])
                self.assertEqual(result["type"], test_type)
                self.assertEqual(result["test_status"], "passed")
                self.assertTrue(result["test_id"].startswith(f"diag_{test_type}_"))
                stored = self.plugin.data["troubleshooting_test_results"][test_type]
                self.assertEqual(stored["type"], test_type)
                self.assertEqual(stored["test_id"], result["test_id"])

    async def test_unified_endpoint_redacts_unsaved_secret_from_outer_failure(self) -> None:
        secret = "temporary-outer-failure-secret"
        payload = {
            "type": "balance_api",
            "settings": {
                "balance_api_url": "https://balance.example/user/balance?token=private",
                "balance_api_key": secret,
            },
        }
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))
        failure = RuntimeError(f"Authorization: Bearer {secret}")
        with (
            patch("astrbot_plugin_private_companion.page_api.request", fake_request),
            patch.object(self.api, "_run_external_api_test", AsyncMock(side_effect=failure)),
            patch("astrbot_plugin_private_companion.page_api.logger.warning") as warning,
        ):
            response = await self.api.run_troubleshooting_test()

        self.assertFalse(response["data"]["ok"])
        self.assertNotIn(secret, repr(response))
        self.assertNotIn(secret, repr(warning.call_args))
        self.assertIn("密钥已隐藏", repr(response))


class ExternalApiTroubleshootingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "companion-panel" / "app.js").read_text(encoding="utf-8")

    def test_panel_variants_remain_byte_identical(self) -> None:
        ascii_panel = ROOT / "pages" / "companion-panel"
        chinese_panel = ROOT / "pages" / "陪伴面板"
        for relative in ("app.js", "app.css", "index.html"):
            self.assertEqual((ascii_panel / relative).read_bytes(), (chinese_panel / relative).read_bytes(), relative)

    def test_three_buttons_are_declared_in_the_expected_sections(self) -> None:
        weather = self.script.split('title: "天气上下文"', 1)[1].split('title: "高级定位与兼容来源"', 1)[0]
        balance = self.script.split('title: "余额与补给"', 1)[1].split('enable_private_image_self_recognition:', 1)[0]
        search = self.script.split('title: "自定义搜索接口"', 1)[1].split('enable_qzone_integration:', 1)[0]

        self.assertIn('externalApiTest: { type: "weather_api", label: "测试天气 API" }', weather)
        self.assertIn('externalApiTest: { type: "balance_api", label: "测试余额接口" }', balance)
        self.assertIn('externalApiTest: { type: "web_search", label: "测试搜索接口" }', search)
        for test_type in ("weather_api", "balance_api", "web_search"):
            self.assertIn(f'data-external-api-test="{test_type}"', self.script)

    def test_request_reads_current_dom_values_and_uses_a_field_whitelist(self) -> None:
        helper = self.script.split("const externalApiTestSettingKeys", 1)[1].split("const PHOTO_API_PLATFORM_OPTIONS", 1)[0]
        self.assertLess(helper.index("root?.querySelector"), helper.index("featureDetailParamDraft"))
        self.assertLess(helper.index("featureDetailParamDraft"), helper.index("overview?.settings"))
        self.assertIn("collectSettingValue(key, control)", helper)
        for key in (
            "weather_token",
            "weather_location",
            "balance_api_custom_headers",
            "balance_json_path",
            "WEB_EXPLORATION_API_BASE_URL",
            "WEB_EXPLORATION_API_KEY",
        ):
            self.assertIn(f'"{key}"', helper)
        self.assertIn('postJson("/troubleshooting/test", payload)', helper)
        self.assertIn('payload.usage = "web_exploration"', helper)

    def test_every_outcome_uses_busy_toast_and_unified_diagnostic_dialog(self) -> None:
        handler = self.script.split("async function runExternalApiTest", 1)[1].split("const PHOTO_API_PLATFORM_OPTIONS", 1)[0]
        self.assertIn("setActionBusy(button, true)", handler)
        self.assertIn("setActionBusy(button, false)", handler)
        self.assertGreaterEqual(handler.count("showToast("), 2)
        self.assertGreaterEqual(handler.count("showTestDiagnosticDialog("), 2)

    def test_reentrant_clicks_are_ignored_until_the_request_finishes(self) -> None:
        handler = self.script.split("async function runExternalApiTest", 1)[1].split("const PHOTO_API_PLATFORM_OPTIONS", 1)[0]
        guard = 'button?.dataset?.externalApiTestBusy === "1"'
        acquire = 'button.dataset.externalApiTestBusy = "1"'
        release = "delete button.dataset.externalApiTestBusy"
        self.assertIn(guard, handler)
        self.assertIn(acquire, handler)
        self.assertIn(release, handler)
        self.assertLess(handler.index(guard), handler.index('postJson("/troubleshooting/test", payload)'))
        self.assertLess(handler.index(acquire), handler.index("setActionBusy(button, true)"))
        self.assertGreater(handler.index(release), handler.index("setActionBusy(button, false)"))

    def test_dialog_displays_external_api_summary_fields_and_zero_values(self) -> None:
        facts = self.script.split("function testDiagnosticFacts", 1)[1].split("function testDiagnosticReportText", 1)[0]
        for label in ("数据来源", "地点", "查询方式", "来源标识", "接口路径", "余额", "剩余比例", "结果数量"):
            self.assertIn(f'["{label}"', facts)
        self.assertIn('hasValue("amount")', facts)
        self.assertIn("external-api-result-preview", self.script)

    def test_active_search_and_group_slang_share_the_same_runtime_entry(self) -> None:
        news = (ROOT / "news_exploration.py").read_text(encoding="utf-8")
        group = (ROOT / "group_observation.py").read_text(encoding="utf-8")
        self.assertIn("async def _run_astrbot_web_search", news)
        self.assertIn('getattr(self, "_run_astrbot_web_search", None)', group)
        self.assertIn("results = await searcher(", group)


if __name__ == "__main__":
    unittest.main()
