# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _AgnesHarness(ProactiveMessageMixin):
    external_image_api_platform = "auto"
    external_image_api_base_url = "https://apihub.agnes-ai.com/v1"
    external_image_api_key = "test-key"
    external_image_api_model = "agnes-image-2.1-flash"
    external_image_api_size = "1K"
    external_image_api_ratio = ""
    external_image_api_timeout_seconds = 180
    external_image_api_custom_headers = ""

    @staticmethod
    def _normalize_external_image_api_platform(value):
        return PrivateCompanionPlugin._normalize_external_image_api_platform(value)

    @staticmethod
    def _extract_json_payload(text):
        return json.loads(text)

    async def _save_external_generated_image(self, image_bytes, *, session_key, ext):
        self.saved_image = (image_bytes, session_key, ext)
        return "C:/temp/agnes-result.png"


class _FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self):
        return json.dumps(self.payload)


class _FakeSession:
    def __init__(self, capture, response_payload, **_kwargs):
        self.capture = capture
        self.response_payload = response_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, endpoint, *, headers, json):
        self.capture.update({"endpoint": endpoint, "headers": headers, "json": json})
        return _FakeResponse(self.response_payload)


class AgnesImageApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.harness = _AgnesHarness()

    def test_platform_and_endpoint_are_detected_from_official_host(self) -> None:
        self.assertEqual(self.harness._resolved_external_image_api_platform(), "agnes")
        self.assertEqual(
            self.harness._external_image_endpoint(),
            "https://apihub.agnes-ai.com/v1/images/generations",
        )
        self.assertEqual(
            self.harness._external_image_endpoint_candidates(),
            ["https://apihub.agnes-ai.com/v1/images/generations"],
        )
        self.harness.external_image_api_platform = "openai"
        self.assertEqual(self.harness._resolved_external_image_api_platform(), "agnes")

    def test_endpoint_normalization_preserves_agnes_ratio(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        endpoint = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "auto",
                "base_url": "https://apihub.agnes-ai.com/v1/images/generations",
                "model": "agnes-image-2.1-flash",
                "ratio": "2:3",
            }
        )
        self.assertEqual(endpoint["platform"], "agnes")
        self.assertEqual(endpoint["ratio"], "2:3")

    def test_size_tier_and_ratio_follow_agnes_rules(self) -> None:
        self.assertEqual(self.harness._agnes_image_size_and_ratio("2K@16:9", ""), ("2K", "16:9"))
        self.assertEqual(
            self.harness._agnes_image_size_and_ratio("1024x1024", "vertical book cover, 2:3 composition"),
            ("1K", "2:3"),
        )
        self.assertEqual(self.harness._agnes_image_size_and_ratio("1024x768", ""), ("1024x768", ""))

    async def test_dispatch_keeps_reference_on_agnes_generations_route(self) -> None:
        captured = {}

        async def run_agnes(prompt_text, **kwargs):
            captured.update({"prompt": prompt_text, **kwargs})
            return "image.png", "ok"

        self.harness._run_agnes_photo_generation = run_agnes
        result = await self.harness._run_external_photo_generation_once(
            "edit this image",
            session_key="dispatch-test",
            reference_image_path="reference.png",
            image_size="2K@16:9",
        )
        self.assertEqual(result, ("image.png", "ok"))
        self.assertEqual(captured["reference_image_path"], "reference.png")
        self.assertEqual(captured["image_size"], "2K@16:9")

    async def test_reference_image_uses_generations_json_extra_body(self) -> None:
        capture = {}
        image_payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + (b"generated-image" * 20)).decode("ascii")
        response_payload = {"data": [{"url": None, "b64_json": image_payload}]}

        def session_factory(**kwargs):
            return _FakeSession(capture, response_payload, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "reference.png"
            reference.write_bytes(b"reference-image")
            with patch("aiohttp.ClientSession", new=session_factory):
                path, note = await self.harness._run_agnes_photo_generation(
                    "Keep the same character and change the background",
                    session_key="agnes-test",
                    reference_image_path=str(reference),
                    image_size="1K@2:3",
                )

        self.assertEqual(path, "C:/temp/agnes-result.png")
        self.assertIn("已使用参考图", note)
        self.assertTrue(capture["endpoint"].endswith("/v1/images/generations"))
        payload = capture["json"]
        self.assertEqual(payload["model"], "agnes-image-2.1-flash")
        self.assertEqual(payload["size"], "1K")
        self.assertEqual(payload["ratio"], "2:3")
        self.assertNotIn("response_format", payload)
        self.assertNotIn("tags", payload)
        self.assertEqual(payload["extra_body"]["response_format"], "url")
        self.assertEqual(len(payload["extra_body"]["image"]), 1)
        self.assertTrue(payload["extra_body"]["image"][0].startswith("data:image/png;base64,"))

    async def test_long_reference_path_is_preserved_through_dispatch_and_agnes(self) -> None:
        capture = {}
        seen_paths: list[str] = []
        image_payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + (b"generated-image" * 20)).decode("ascii")
        response_payload = {"data": [{"b64_json": image_payload}]}
        long_reference = "C:/reference/" + ("nested folder/" * 22) + "persona  original.png"

        async def reference_to_data_url(path: str) -> str:
            seen_paths.append(path)
            return "data:image/png;base64," + base64.b64encode(b"reference").decode("ascii")

        def session_factory(**kwargs):
            return _FakeSession(capture, response_payload, **kwargs)

        self.harness._reference_image_to_data_url = reference_to_data_url
        with patch("aiohttp.ClientSession", new=session_factory):
            path, note = await self.harness._run_external_photo_generation_once(
                "Keep the same character",
                session_key="agnes-long-path",
                reference_image_path=long_reference,
            )

        self.assertGreater(len(long_reference), 260)
        self.assertEqual(seen_paths, [long_reference])
        self.assertEqual(path, "C:/temp/agnes-result.png")
        self.assertIn("已使用参考图", note)


if __name__ == "__main__":
    unittest.main()
