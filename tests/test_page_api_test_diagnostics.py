# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import struct
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class PageApiTestDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        self.api.plugin = SimpleNamespace(
            config={"API_KEY": "sk-runtime-secret-123456789"},
            _format_timestamp_elapsed=lambda _value: "刚刚",
        )
        self.api._schema_key_index_cache = None

    def test_success_result_gets_stable_diagnostic_contract(self) -> None:
        finished = time.time()
        result = self.api._finalize_test_diagnostics(
            "screen_peek",
            {"ok": True, "detail": "识屏返回有效摘要"},
            finished - 0.15,
            finished_at=finished,
        )

        self.assertEqual(result["diagnostic_version"], 1)
        self.assertEqual(result["test_status"], "passed")
        self.assertEqual(result["request_id"], result["trace_id"])
        self.assertEqual(len(result["request_id"]), 12)
        self.assertGreaterEqual(result["elapsed_ms"], 100)
        self.assertEqual(result["steps"][0]["status"], "ok")
        self.assertEqual(result["diagnostic_entries"][-1]["level"], "ok")
        self.assertEqual(result["error_code"], "")

    def test_failure_is_classified_and_secrets_are_redacted_everywhere(self) -> None:
        secret = "sk-runtime-secret-123456789"
        result = self.api._finalize_test_diagnostics(
            "image_api_endpoint",
            {
                "ok": False,
                "error": f"等待释放队列后排队超时；Authorization: Bearer {secret}",
                "warnings": [f"不要输出 {secret}"],
                "steps": [
                    {
                        "name": "等待队列",
                        "status": "error",
                        "detail": f"queue timeout api_key={secret}",
                    }
                ],
            },
            time.time() - 1,
        )
        sanitized = self.api._sanitize_troubleshooting_test_result(result)

        self.assertEqual(result["error_code"], "queue_timeout")
        self.assertEqual(result["error_category"], "队列等待超时")
        self.assertTrue(result["retryable"])
        self.assertTrue(result["suggestion"])
        self.assertNotIn(secret, repr(result))
        self.assertNotIn(secret, repr(sanitized))
        self.assertEqual(sanitized["request_id"], result["request_id"])
        self.assertTrue(sanitized["diagnostic_entries"])
        self.assertEqual(sanitized["steps"][0]["elapsed_ms"], 0)

    def test_image_api_404_is_classified_as_endpoint_mismatch(self) -> None:
        for message in (
            "HTTP 404: connection reached the service but the route was not found",
            "未找到生图接口",
            "端点不匹配：请核对请求 URL",
        ):
            with self.subTest(message=message):
                result = self.api._classify_test_failure(
                    "image_api_endpoint",
                    {"ok": False, "error": message},
                )
                self.assertEqual(result["error_code"], "endpoint_mismatch")
                self.assertEqual(result["error_category"], "端点不匹配")
                self.assertFalse(result["retryable"])
                self.assertIn("请求 URL", result["suggestion"])

    def test_legacy_history_gets_repeatable_request_id_and_diagnostics(self) -> None:
        data = {
            "users": {},
            "troubleshooting_test_results": {
                "tts_generation": {
                    "type": "tts_generation",
                    "ok": False,
                    "title": "TTS 生成与投递测试",
                    "error": "测试语音投递失败",
                    "elapsed_ms": 320,
                    "ran_at": 12345.0,
                }
            },
        }

        first = self.api._troubleshooting_test_results(data)["tts_generation"]
        second = self.api._troubleshooting_test_results(data)["tts_generation"]

        self.assertEqual(first["request_id"], second["request_id"])
        self.assertEqual(first["test_status"], "failed")
        self.assertEqual(first["error_code"], "delivery")
        self.assertTrue(first["steps"])
        self.assertTrue(first["diagnostic_entries"])

    async def test_model_provider_failure_returns_safe_actionable_details(self) -> None:
        secret = "sk-provider-secret-987654321"
        self.api.plugin._llm_call = AsyncMock(
            side_effect=RuntimeError(f"connection timeout Authorization: Bearer {secret}")
        )
        fake_request = SimpleNamespace(
            get_json=AsyncMock(return_value={"provider_id": "provider-a"})
        )

        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            response = await self.api.test_provider()

        result = response["data"]
        self.assertTrue(response["success"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "timeout")
        self.assertTrue(result["request_id"])
        self.assertTrue(result["diagnostic_entries"])
        self.assertNotIn(secret, repr(result))

    async def test_visual_provider_test_uses_real_image_input(self) -> None:
        provider = SimpleNamespace(
            text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="正常"))
        )
        self.api.plugin._private_image_provider_by_id = lambda _provider_id: provider
        self.api.plugin._provider_supports_image = lambda _provider: True
        self.api.plugin._private_image_provider_timeout_seconds = lambda *_args: 0.0
        fake_request = SimpleNamespace(
            get_json=AsyncMock(
                return_value={
                    "key": "PLUGIN_VISION_PROVIDER_ID",
                    "provider_id": "vision-provider",
                }
            )
        )

        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            response = await self.api.test_provider()

        result = response["data"]
        self.assertTrue(result["ok"])
        self.assertEqual("图片输入", result["steps"][0]["name"])
        call_kwargs = provider.text_chat.await_args.kwargs
        self.assertEqual(1, len(call_kwargs["image_urls"]))
        image_url = call_kwargs["image_urls"][0]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        raw = base64.b64decode(image_url.partition(",")[2])
        width, height = struct.unpack(">II", raw[16:24])
        self.assertGreater(width, 10)
        self.assertGreater(height, 10)

    async def test_shared_embedding_provider_uses_vector_connection_test(self) -> None:
        provider = SimpleNamespace(get_embedding=AsyncMock())
        self.api._embedding_provider_for_test = AsyncMock(return_value=provider)
        self.api.plugin._reaction_embedding_vector = AsyncMock(return_value=[0.6, 0.8])
        fake_request = SimpleNamespace(
            get_json=AsyncMock(
                return_value={
                    "key": "EMBEDDING_PROVIDER_ID",
                    "provider_id": "embedding-provider",
                }
            )
        )

        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            response = await self.api.test_provider()

        result = response["data"]
        self.assertTrue(result["ok"])
        self.assertEqual("向量生成", result["steps"][0]["name"])
        self.assertEqual("Embedding Provider 已返回有效向量", result["detail"])
        self.api.plugin._reaction_embedding_vector.assert_awaited_once_with(
            provider,
            "开心 安慰 抱抱 表情语义测试",
        )

    def test_empty_timeout_error_gets_actionable_visual_message(self) -> None:
        text = self.api._visual_call_error_text(asyncio.TimeoutError(), timeout=12)

        self.assertEqual("视觉模型图片请求超时（12 秒）", text)


class TestDiagnosticUiTests(unittest.TestCase):
    def test_both_panel_variants_expose_the_same_diagnostic_ui(self) -> None:
        chinese = ROOT / "pages" / "陪伴面板"
        ascii_panel = ROOT / "pages" / "companion-panel"
        for relative in ("app.js", "app.css", "index.html", "js/panels/provider-tree.js"):
            left = (chinese / relative).read_text(encoding="utf-8", errors="strict")
            right = (ascii_panel / relative).read_text(encoding="utf-8", errors="strict")
            self.assertEqual(left, right, relative)

        script = (chinese / "app.js").read_text(encoding="utf-8", errors="strict")
        styles = (chinese / "app.css").read_text(encoding="utf-8", errors="strict")
        provider_tree = (chinese / "js" / "panels" / "provider-tree.js").read_text(
            encoding="utf-8", errors="strict"
        )
        self.assertIn("function showTestDiagnosticDialog", script)
        self.assertIn("function troubleshootingDiagnosticCategoryLabel", script)
        self.assertIn('authorization: "认证或权限未通过"', script)
        self.assertIn('provider: "Provider 未返回有效结果"', script)
        self.assertIn('data-test-result-source="troubleshooting"', script)
        self.assertIn('data-test-result-source="image-api"', script)
        self.assertIn('data-test-result-source="tts-provider"', script)
        self.assertIn('class="proactive-diagnostic-value"', script)
        self.assertIn(".test-diagnostic-dialog", styles)
        self.assertIn(".proactive-diagnostic dl {\n  display: grid;\n  grid-template-columns: minmax(0, 1fr);", styles)
        self.assertIn("word-break: break-word;", styles)
        self.assertIn('button.dataset.testResultSource = "provider"', provider_tree)


if __name__ == "__main__":
    unittest.main()
