# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from quart import Quart

from astrbot_plugin_private_companion.page_api import PILImage, PrivateCompanionPageApi


class ImageCachePluginStub:
    def __init__(self, data_dir: str, cache: dict[str, dict]) -> None:
        self.data_dir = data_dir
        self.data = {"private_image_vision_cache": cache}
        self._data_lock = asyncio.Lock()
        self.save_count = 0

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_count += 1


class ImageCacheBatchPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.app = Quart(__name__)

    async def test_thumbnail_is_small_webp_and_reused(self) -> None:
        if PILImage is None:
            self.skipTest("Pillow 不可用")
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = ImageCachePluginStub(temp_dir, {})
            api = PrivateCompanionPageApi(plugin)
            source_dir = Path(temp_dir) / "private_image_cache_previews"
            source_dir.mkdir(parents=True)
            source = source_dir / "sample.png"
            PILImage.new("RGB", (640, 320), (20, 120, 220)).save(source)

            first = await api._get_or_create_image_cache_thumbnail("sample", source)
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(first.suffix, ".webp")
            self.assertTrue(first.is_file())
            first_mtime = first.stat().st_mtime_ns
            with PILImage.open(first) as thumbnail:
                self.assertLessEqual(max(thumbnail.size), 160)

            second = await api._get_or_create_image_cache_thumbnail("sample", source)
            self.assertEqual(second, first)
            self.assertEqual(first.stat().st_mtime_ns, first_mtime)

    async def test_preview_lookup_rejects_path_outside_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = Path(temp_dir) / "outside.png"
            outside.write_bytes(b"not-an-image")
            plugin = ImageCachePluginStub(str(Path(temp_dir) / "data"), {})
            api = PrivateCompanionPageApi(plugin)

            result = api._image_cache_preview_file("missing", {"preview_path": str(outside)})

            self.assertIsNone(result)

    async def test_thumbnail_endpoint_returns_displayable_data_url(self) -> None:
        if PILImage is None:
            self.skipTest("Pillow 不可用")
        with tempfile.TemporaryDirectory() as temp_dir:
            preview_dir = Path(temp_dir) / "private_image_cache_previews"
            preview_dir.mkdir(parents=True)
            source = preview_dir / "sample.png"
            PILImage.new("RGB", (320, 180), (40, 80, 160)).save(source)
            plugin = ImageCachePluginStub(
                temp_dir,
                {"sample": {"preview_path": str(source)}},
            )
            api = PrivateCompanionPageApi(plugin)

            async with self.app.test_request_context("/?key=sample"):
                result = await api.get_image_cache_thumbnail_data()

            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["mime"], "image/webp")
            self.assertTrue(result["data"]["data_url"].startswith("data:image/webp;base64,"))
            self.assertGreater(len(result["data"]["data_url"]), 40)
            self.assertEqual(plugin.save_count, 1)

    async def test_bulk_delete_removes_previews_and_thumbnails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            preview_dir = Path(temp_dir) / "private_image_cache_previews"
            thumbnail_dir = preview_dir / ".thumbnails"
            thumbnail_dir.mkdir(parents=True)
            first_preview = preview_dir / "first.jpg"
            second_preview = preview_dir / "second.png"
            first_thumb = thumbnail_dir / "first.webp"
            second_thumb = thumbnail_dir / "second.webp"
            for path in (first_preview, second_preview, first_thumb, second_thumb):
                path.write_bytes(b"image")

            cache = {
                "first": {"preview_path": str(first_preview)},
                "second": {"preview_path": str(second_preview)},
                "keep": {"text": "保留"},
            }
            plugin = ImageCachePluginStub(temp_dir, cache)
            api = PrivateCompanionPageApi(plugin)
            payload = {"keys": ["first", "second", "missing", "first"], "confirm": True}

            async with self.app.test_request_context("/", method="POST", json=payload):
                result = await api.bulk_delete_image_cache_items()

            self.assertTrue(result["success"])
            self.assertEqual(result["data"]["removed"], 2)
            self.assertEqual(result["data"]["removed_keys"], ["first", "second"])
            self.assertEqual(result["data"]["missing_keys"], ["missing"])
            self.assertEqual(result["data"]["remaining"], 1)
            self.assertEqual(plugin.save_count, 1)
            self.assertEqual(set(cache), {"keep"})
            for path in (first_preview, second_preview, first_thumb, second_thumb):
                self.assertFalse(path.exists())

    async def test_bulk_delete_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = {"first": {"text": "保留"}}
            plugin = ImageCachePluginStub(temp_dir, cache)
            api = PrivateCompanionPageApi(plugin)

            async with self.app.test_request_context(
                "/", method="POST", json={"keys": ["first"], "confirm": False}
            ):
                result = await api.bulk_delete_image_cache_items()

            self.assertFalse(result["success"])
            self.assertIn("confirm=true", result["error"])
            self.assertIn("first", cache)
            self.assertEqual(plugin.save_count, 0)


if __name__ == "__main__":
    unittest.main()
