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
    def __init__(
        self,
        status: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.headers = dict(headers or {})

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

    def test_transient_upstream_status_warns_without_billable_retry(self) -> None:
        harness = ImageDiagnosticHarness()

        self.assertTrue(harness._external_image_api_is_transient_status(500))
        self.assertTrue(harness._external_image_api_is_transient_status("502"))
        self.assertFalse(harness._external_image_api_is_transient_status(400))
        self.assertEqual(0.0, harness._external_image_api_transient_retry_delay(503))
        self.assertEqual(0.0, harness._external_image_api_transient_retry_delay(401))

        note = harness._external_image_api_error_note(502, "image generation failed")

        self.assertIn("上游生图服务临时失败", note)
        self.assertIn("可能产生费用", note)
        self.assertIn("不会自动重新提交", note)

    def test_download_retry_classifier_skips_deterministic_failures(self) -> None:
        harness = ImageDiagnosticHarness()

        self.assertTrue(harness._external_image_download_failure_is_retryable("下载在线图片结果超时"))
        self.assertTrue(harness._external_image_download_failure_is_retryable("下载图片失败：HTTP 503"))
        self.assertTrue(harness._external_image_download_failure_is_retryable("Server disconnected"))
        self.assertFalse(harness._external_image_download_failure_is_retryable("图片地址为空"))
        self.assertFalse(harness._external_image_download_failure_is_retryable("下载图片过大（超过 32 MB）"))
        self.assertFalse(harness._external_image_download_failure_is_retryable("下载图片失败：HTTP 403"))

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

    async def test_gpt_image_two_submits_multiple_reference_images(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = base64.b64encode(b"\x89PNG\r\n\x1a\nmulti-reference").decode("ascii")
        responses = [SequenceResponse(200, {"data": [{"b64_json": generated}]})]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            bot_reference = Path(temp_dir) / "bot.png"
            role_reference = Path(temp_dir) / "sister.webp"
            bot_reference.write_bytes(b"bot-reference")
            role_reference.write_bytes(b"role-reference")
            with patch("aiohttp.ClientSession", new=session_factory):
                path, note = await harness._run_external_photo_generation_once(
                    "Bot and her sister in one coherent photo",
                    session_key="multi-reference-edit",
                    reference_image_path=str(bot_reference),
                    reference_image_paths=(str(role_reference),),
                )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertIn("已使用 2 张参考图", note)
        self.assertEqual(len(calls), 1)
        form = calls[0]["data"]
        field_names = [field[0].get("name") for field in getattr(form, "_fields", [])]
        self.assertEqual(field_names.count("image[]"), 2)
        self.assertNotIn("image", field_names)

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

    async def test_generated_url_download_retries_before_backup_generation(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        responses = [
            SequenceResponse(200, {"data": [{"url": "https://cdn.example.test/generated.png"}]}),
        ]
        endpoints = [
            {
                "name": "主用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://primary.example.test/v1",
                "api_key": "primary-key",
                "model": "gpt-image-2",
                "size": "1024x1024",
                "timeout_seconds": 30,
                "custom_headers": "",
            },
            {
                "name": "备用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://backup.example.test/v1",
                "api_key": "backup-key",
                "model": "gpt-image-2",
                "size": "1024x1024",
                "timeout_seconds": 30,
                "custom_headers": "",
            },
        ]
        harness._external_image_api_endpoint_queue = lambda **_kwargs: endpoints

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        download_attempts: list[tuple[str, str]] = []

        async def download_once(url: str, *, session_key: str) -> tuple[str, str]:
            download_attempts.append((url, session_key))
            if len(download_attempts) == 1:
                return "", "下载在线图片结果超时（30 秒内未完成）"
            return "C:/temp/retried-download.png", "ok"

        async def no_sleep(_delay: float) -> None:
            return None

        harness._download_external_image_url_once = download_once
        with patch("aiohttp.ClientSession", new=session_factory), patch(
            "astrbot_plugin_private_companion.proactive_message.asyncio.sleep",
            new=no_sleep,
        ):
            path, note = await harness._run_external_photo_generation_serial(
                "a short test prompt",
                session_key="download-retry-primary",
            )

        self.assertEqual(path, "C:/temp/retried-download.png")
        self.assertIn("主用", note)
        self.assertEqual(len(download_attempts), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["endpoint"], "https://primary.example.test/v1/images/generations")

    async def test_generated_url_download_failure_does_not_charge_backup_generation(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        responses = [
            SequenceResponse(200, {"data": [{"url": "https://cdn.example.test/generated.png"}]}),
        ]
        endpoints = [
            {
                "name": "主用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://primary.example.test/v1",
                "api_key": "primary-key",
                "model": "gpt-image-2",
                "size": "1024x1024",
                "timeout_seconds": 30,
                "custom_headers": "",
            },
            {
                "name": "备用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://backup.example.test/v1",
                "api_key": "backup-key",
                "model": "gpt-image-2",
                "size": "1024x1024",
                "timeout_seconds": 30,
                "custom_headers": "",
            },
        ]
        harness._external_image_api_endpoint_queue = lambda **_kwargs: endpoints

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        download_attempts: list[tuple[str, str]] = []

        async def download_once(url: str, *, session_key: str) -> tuple[str, str]:
            download_attempts.append((url, session_key))
            return "", "下载在线图片结果超时（30 秒内未完成）"

        async def no_sleep(_delay: float) -> None:
            return None

        harness._download_external_image_url_once = download_once
        with patch("aiohttp.ClientSession", new=session_factory), patch(
            "astrbot_plugin_private_companion.proactive_message.asyncio.sleep",
            new=no_sleep,
        ):
            path, note = await harness._run_external_photo_generation_serial(
                "a short test prompt",
                session_key="download-retry-backup",
            )

        self.assertEqual(path, "")
        self.assertIn("生成已完成但图片结果取回失败", note)
        self.assertIn("主用", note)
        self.assertEqual(len(download_attempts), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["endpoint"], "https://primary.example.test/v1/images/generations")

    async def test_request_level_502_still_falls_back_to_backup_endpoint(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        generated = base64.b64encode(b"\x89PNG\r\n\x1a\nbackup-image").decode("ascii")
        responses = [
            SequenceResponse(502, "primary unavailable"),
            SequenceResponse(200, {"data": [{"b64_json": generated}]}),
        ]
        endpoints = [
            {
                "name": "主用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://primary.example.test/v1",
                "api_key": "primary-key",
                "model": "gpt-image-2",
                "size": "1024x1024",
                "timeout_seconds": 30,
                "custom_headers": "",
            },
            {
                "name": "备用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://backup.example.test/v1",
                "api_key": "backup-key",
                "model": "gpt-image-2",
                "size": "1024x1024",
                "timeout_seconds": 30,
                "custom_headers": "",
            },
        ]
        harness._external_image_api_endpoint_queue = lambda **_kwargs: endpoints

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        async def no_sleep(_delay: float) -> None:
            return None

        with patch("aiohttp.ClientSession", new=session_factory), patch(
            "astrbot_plugin_private_companion.proactive_message.asyncio.sleep",
            new=no_sleep,
        ):
            path, note = await harness._run_external_photo_generation_serial(
                "a short test prompt",
                session_key="request-fallback",
            )

        self.assertEqual(path, "C:/temp/private-companion-result.png")
        self.assertIn("备用", note)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            [call["endpoint"] for call in calls],
            [
                "https://primary.example.test/v1/images/generations",
                "https://backup.example.test/v1/images/generations",
            ],
        )

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

    async def test_generation_does_not_resubmit_after_transient_upstream_error(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        responses = [
            SequenceResponse(502, "image generation failed"),
        ]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        with patch("aiohttp.ClientSession", new=session_factory):
            path, note = await harness._run_external_photo_generation_once(
                "a short test prompt",
                session_key="retry-generation",
            )

        self.assertEqual(path, "")
        self.assertIn("HTTP 502", note)
        self.assertIn("不会自动重新提交", note)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["endpoint"], "https://example.test/v1/images/generations")

    async def test_reference_edit_does_not_resubmit_after_gateway_error(self) -> None:
        harness = ImageDiagnosticHarness()
        calls: list[dict[str, object]] = []
        responses = [
            SequenceResponse(500, "do request failed"),
        ]

        def session_factory(**kwargs):
            return SequenceSession(responses, calls, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            reference.write_bytes(b"reference-image")
            with patch("aiohttp.ClientSession", new=session_factory):
                path, note = await harness._run_external_photo_generation_once(
                    "keep the same person",
                    session_key="retry-edit",
                    reference_image_path=str(reference),
                )

        self.assertEqual(path, "")
        self.assertIn("HTTP 500", note)
        self.assertIn("可能产生费用", note)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["endpoint"].endswith("/images/edits"))
        first_form = calls[0]["data"]
        self.assertIsNotNone(first_form)
        self.assertEqual(len(getattr(first_form, "_fields", [])), 4)

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

        def save(**_kwargs) -> None:
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
