# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class ImageDiagnosticHarness(ProactiveMessageMixin):
    external_image_api_key = "draft-api-secret-123456"
    external_image_api_custom_headers = "X-Gateway-Token: header-secret-abcdef"
    external_image_api_base_url = "https://example.test/v1"
    external_image_api_model = "gpt-image-2"
    external_image_api_size = "1024x1024"
    external_image_api_ratio = ""
    external_image_api_timeout_seconds = 30
    external_image_api_endpoints: list[dict[str, object]] = []
    external_image_api_platform = "openai"
    config: dict[str, object] = {}

    @staticmethod
    def _extract_json_payload(text: str) -> object:
        return json.loads(text)

    async def _save_external_generated_image(self, image_bytes: bytes, *, session_key: str, ext: str) -> str:
        self.saved_image = (image_bytes, session_key, ext)
        return "C:/temp/private-companion-result.png"


class RecordingLock:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.acquired = False

    async def acquire(self) -> bool:
        self.events.append("acquire")
        self.acquired = True
        return True

    def release(self) -> None:
        self.events.append("release")
        self.acquired = False


class SequenceResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


class SequenceSession:
    def __init__(self, responses: list[SequenceResponse], calls: list[dict[str, object]], **_kwargs) -> None:
        self.responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, endpoint: str, **kwargs):
        self.calls.append({"endpoint": endpoint, **kwargs})
        return self.responses.pop(0)


class ImageApiEndpointManagementTests(unittest.IsolatedAsyncioTestCase):
    def test_result_key_tracks_every_request_affecting_field(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        endpoint = {
            "name": "主用",
            "enabled": True,
            "platform": "openai",
            "base_url": "https://example.test/v1",
            "api_key": "secret-key",
            "model": "image-model",
            "size": "1024x1024",
            "timeout_seconds": 180,
            "custom_headers": "X-Region: cn",
        }
        original = api._image_api_endpoint_test_key(endpoint)
        renamed = api._image_api_endpoint_test_key({**endpoint, "name": "重命名"})
        self.assertEqual(original, renamed)
        for field, value in (
            ("enabled", False),
            ("size", "768x1344"),
            ("ratio", "2:3"),
            ("timeout_seconds", 240),
            ("custom_headers", "X-Region: us"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(original, api._image_api_endpoint_test_key({**endpoint, field: value}))

    def test_status_summary_does_not_resync_runtime_configuration(self) -> None:
        plugin = SimpleNamespace(
            _external_image_api_endpoint_queue=lambda **_: [],
        )
        api = PrivateCompanionPageApi(plugin)
        api._sync_photo_generation_runtime_config = lambda: self.fail("状态查询不应改写运行时配置")
        self.assertEqual(api._troubleshooting_image_api_endpoints(), [])

    def test_diagnostics_redact_query_keys_api_keys_and_custom_headers(self) -> None:
        harness = ImageDiagnosticHarness()
        note = harness._external_image_api_error_note(
            500,
            "Bearer draft-api-secret-123456 header-secret-abcdef",
            endpoint="https://example.test/v1/images/generations?token=query-secret-987654",
        )
        for secret in (
            "draft-api-secret-123456",
            "header-secret-abcdef",
            "query-secret-987654",
        ):
            self.assertNotIn(secret, note)
        self.assertIn("[密钥已隐藏]", note)

    def test_transient_upstream_status_has_retryable_diagnostic(self) -> None:
        harness = ImageDiagnosticHarness()

        self.assertTrue(harness._external_image_api_is_transient_status(500))
        self.assertTrue(harness._external_image_api_is_transient_status("502"))
        self.assertFalse(harness._external_image_api_is_transient_status(400))
        self.assertGreater(harness._external_image_api_transient_retry_delay(503), 0)
        self.assertEqual(0.0, harness._external_image_api_transient_retry_delay(401))

        note = harness._external_image_api_error_note(502, "image generation failed")

        self.assertIn("上游生图服务临时失败", note)
        self.assertIn("自动短暂重试一次", note)

    async def test_generation_accepts_data_uri_in_url_field(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = b"\x89PNG\r\n\x1a\nissue-60-generation"
        data_uri = "data:image/png;base64," + base64.b64encode(generated).decode("ascii")
        responses = [SequenceResponse(200, {"data": [{"url": data_uri}]})]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        with patch("aiohttp.ClientSession", new=session_factory):
            path, note = await harness._run_external_photo_generation_once(
                "a short test prompt",
                session_key="data-uri-generation",
            )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertEqual(note, "ok")
        self.assertEqual(harness.saved_image, (generated, "data-uri-generation", ".png"))
        self.assertEqual(len(calls), 1)

    async def test_reference_edit_accepts_data_uri_in_url_field(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = b"RIFF" + (13).to_bytes(4, "little") + b"WEBPissue-60-edit"
        data_uri = "data:image/webp;base64," + base64.b64encode(generated).decode("ascii")
        responses = [SequenceResponse(200, {"data": [{"url": data_uri}]})]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            reference.write_bytes(b"reference-image")
            with patch("aiohttp.ClientSession", new=session_factory):
                path, note = await harness._run_external_photo_generation_once(
                    "keep the same person",
                    session_key="data-uri-edit",
                    reference_image_path=str(reference),
                )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertIn("已使用本地人设参考图", note)
        self.assertEqual(harness.saved_image, (generated, "data-uri-edit", ".webp"))
        self.assertEqual(len(calls), 1)

    async def test_generation_falls_back_to_url_when_b64_json_is_invalid(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = b"\x89PNG\r\n\x1a\nissue-60-fallback"
        data_uri = "data:image/png;base64," + base64.b64encode(generated).decode("ascii")
        responses = [
            SequenceResponse(
                200,
                {"data": [{"b64_json": "not!valid!", "url": data_uri}]},
            )
        ]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        with patch("aiohttp.ClientSession", new=session_factory):
            path, note = await harness._run_external_photo_generation_once(
                "a short test prompt",
                session_key="invalid-b64-fallback",
            )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertEqual(note, "ok")
        self.assertEqual(harness.saved_image, (generated, "invalid-b64-fallback", ".png"))

    async def test_inline_image_rejects_invalid_base64_and_oversize_payload(self) -> None:
        harness = ImageDiagnosticHarness()

        path, note = await harness._materialize_external_image_value(
            "data:image/png;base64,not!valid!",
            session_key="invalid-inline",
        )
        self.assertEqual(path, "")
        self.assertIn("base64 数据无效", note)

        with patch("astrbot_plugin_private_companion.proactive_message._EXTERNAL_IMAGE_MAX_BYTES", 8):
            path, note = await harness._materialize_external_image_value(
                "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nX").decode("ascii"),
                session_key="oversize-inline",
            )
        self.assertEqual(path, "")
        self.assertIn("数据过大", note)

    async def test_generation_retries_transient_upstream_error_then_succeeds(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = base64.b64encode(b"\x89PNG\r\n\x1a\ngenerated-image").decode("ascii")
        responses = [
            SequenceResponse(502, "image generation failed"),
            SequenceResponse(200, {"data": [{"b64_json": generated}]}),
        ]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        async def no_sleep(_delay: float) -> None:
            return None

        with patch("aiohttp.ClientSession", new=session_factory), patch(
            "astrbot_plugin_private_companion.proactive_message.asyncio.sleep",
            new=no_sleep,
        ):
            path, note = await harness._run_external_photo_generation_once(
                "a short test prompt",
                session_key="retry-generation",
            )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertIn("重试成功", note)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["endpoint"], "https://example.test/v1/images/generations")
        self.assertEqual(calls[1]["endpoint"], calls[0]["endpoint"])
        self.assertEqual(calls[1]["json"]["model"], "gpt-image-2")

    async def test_reference_edit_retries_transient_upstream_error_and_keeps_image(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = base64.b64encode(b"\x89PNG\r\n\x1a\nedited-image").decode("ascii")
        responses = [
            SequenceResponse(500, "do request failed"),
            SequenceResponse(200, {"data": [{"b64_json": generated}]}),
        ]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        async def no_sleep(_delay: float) -> None:
            return None

        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            reference.write_bytes(b"reference-image")
            with patch("aiohttp.ClientSession", new=session_factory), patch(
                "astrbot_plugin_private_companion.proactive_message.asyncio.sleep",
                new=no_sleep,
            ):
                path, note = await harness._run_external_photo_generation_once(
                    "keep the same person",
                    session_key="retry-edit",
                    reference_image_path=str(reference),
                )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertIn("已使用本地人设参考图", note)
        self.assertIn("重试成功", note)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0]["endpoint"].endswith("/images/edits"))
        self.assertTrue(calls[1]["endpoint"].endswith("/images/edits"))
        second_form = calls[1]["data"]
        self.assertIsNotNone(second_form)
        self.assertEqual(len(getattr(second_form, "_fields", [])), 4)

    async def test_queue_wait_and_endpoint_execution_have_separate_budgets(self) -> None:
        events: list[str] = []
        lock = RecordingLock(events)

        async def runner(endpoint, prompt, **kwargs):
            self.assertTrue(lock.acquired)
            events.append("runner")
            return "", "接口未返回图片"

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = SimpleNamespace(
                _external_image_api_runtime_lock=lock,
                _normalize_external_image_api_endpoint=lambda item, index=0: dict(item),
                _run_external_photo_generation_with_endpoint=runner,
                data_dir=temp_dir,
            )
            api = PrivateCompanionPageApi(plugin)
            original_wait_for = asyncio.wait_for
            timeouts: list[int] = []

            async def record_wait_for(awaitable, timeout):
                timeouts.append(int(timeout))
                return await awaitable

            with patch("astrbot_plugin_private_companion.page_api.asyncio.wait_for", new=record_wait_for):
                result = await api._run_image_api_endpoint_test(
                    {
                        "endpoint_index": 0,
                        "endpoint": {
                            "name": "测试 API",
                            "enabled": True,
                            "platform": "openai",
                            "base_url": "https://example.test/v1",
                            "api_key": "secret-key",
                            "model": "image-model",
                            "size": "1024x1024",
                            "timeout_seconds": 20,
                        },
                    }
                )
            self.assertIs(original_wait_for, asyncio.wait_for)

        self.assertEqual(timeouts, [70, 70])
        self.assertEqual(events, ["acquire", "runner", "release"])
        self.assertFalse(result["ok"])

    async def test_test_artifacts_are_scoped_deleted_and_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "generated_photos"
            root.mkdir()
            current = root / "private_companion_troubleshooting_current.png"
            stale = root / "private_companion_troubleshooting_stale.png"
            fresh = root / "private_companion_troubleshooting_fresh.png"
            outside = Path(temp_dir) / "private_companion_troubleshooting_outside.png"
            for path in (current, stale, fresh, outside):
                path.write_bytes(b"image")
            old_time = time.time() - 7200
            os.utime(stale, (old_time, old_time))

            api = PrivateCompanionPageApi(SimpleNamespace(data_dir=temp_dir))
            self.assertTrue(await api._cleanup_image_api_test_artifact(current))
            self.assertFalse(current.exists())
            self.assertFalse(await api._cleanup_image_api_test_artifact(outside))
            self.assertTrue(outside.exists())
            self.assertEqual(await api._prune_stale_image_api_test_artifacts(), 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())

    async def test_endpoint_test_history_is_capped(self) -> None:
        save_calls = 0

        def save() -> None:
            nonlocal save_calls
            save_calls += 1

        plugin = SimpleNamespace(
            _data_lock=asyncio.Lock(),
            data={"troubleshooting_test_results": {"tts_generation": {"ran_at": 1}}},
            _save_data_sync=save,
        )
        api = PrivateCompanionPageApi(plugin)
        for index in range(30):
            await api._remember_troubleshooting_test_result(
                f"image_api_endpoint_{index:02d}",
                {"type": "image_api_endpoint", "ok": True, "ran_at": float(index + 1)},
            )

        results = plugin.data["troubleshooting_test_results"]
        endpoint_keys = [key for key in results if key.startswith("image_api_endpoint_")]
        self.assertEqual(len(endpoint_keys), 24)
        self.assertNotIn("image_api_endpoint_00", results)
        self.assertIn("image_api_endpoint_29", results)
        self.assertIn("tts_generation", results)
        self.assertEqual(save_calls, 30)


if __name__ == "__main__":
    unittest.main()
