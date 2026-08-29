# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


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
                self.assertNotEqual(
                    original,
                    api._image_api_endpoint_test_key({**endpoint, field: value}),
                )

    def test_status_summary_does_not_resync_runtime_configuration(self) -> None:
        plugin = SimpleNamespace(
            _external_image_api_endpoint_queue=lambda **_: [],
        )
        api = PrivateCompanionPageApi(plugin)
        api._sync_photo_generation_runtime_config = lambda: self.fail(
            "状态查询不应改写运行时配置"
        )
        self.assertEqual(api._troubleshooting_image_api_endpoints(), [])

    def test_companion_does_not_restore_image_http_execution_helpers(self) -> None:
        forbidden = {
            "_external_image_api_error_note",
            "_external_image_api_is_transient_status",
            "_external_image_download_failure_is_retryable",
            "_materialize_external_image_value",
            "_run_external_photo_generation_once",
            "_run_external_photo_generation_serial",
        }

        self.assertTrue(all(not hasattr(ProactiveMessageMixin, name) for name in forbidden))
        source = inspect.getsource(
            __import__(
                "astrbot_plugin_private_companion.proactive_message",
                fromlist=["ProactiveMessageMixin"],
            )
        )
        self.assertNotIn("astrbot_plugin_image_companion.image_runtime", source)
        self.assertNotIn("_install_external_image_runtime_compatibility", source)
        endpoint_test_source = inspect.getsource(
            PrivateCompanionPageApi._run_image_api_endpoint_test
        )
        self.assertNotIn("_run_external_photo_generation_with_endpoint", endpoint_test_source)

    async def test_queue_wait_and_endpoint_execution_have_separate_budgets(self) -> None:
        events: list[str] = []
        lock = RecordingLock(events)

        async def runner(endpoint, prompt):
            self.assertTrue(lock.acquired)
            events.append("runner")
            return {"image_path": "", "message": "接口未返回图片"}

        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = SimpleNamespace(
                _external_image_api_runtime_lock=lock,
                _normalize_external_image_api_endpoint=lambda item, index=0: dict(item),
                _image_companion_test_endpoint=runner,
                data_dir=temp_dir,
            )
            api = PrivateCompanionPageApi(plugin)
            original_wait_for = asyncio.wait_for
            timeouts: list[int] = []

            async def record_wait_for(awaitable, timeout):
                timeouts.append(int(timeout))
                return await awaitable

            with patch(
                "astrbot_plugin_private_companion.page_api.asyncio.wait_for",
                new=record_wait_for,
            ):
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
