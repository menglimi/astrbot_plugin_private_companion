# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class CreativeCoverUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        page_root = ROOT / "pages" / "陪伴面板"
        cls.script = (page_root / "app.js").read_text(encoding="utf-8")
        cls.css = (page_root / "app.css").read_text(encoding="utf-8")
        cls.html = (page_root / "index.html").read_text(encoding="utf-8")
        cls.page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")

    def test_creative_cover_uses_bridge_readable_data_endpoint(self) -> None:
        self.assertIn('("/creative/project/cover_data", self.get_creative_project_cover_data', self.page_api)
        self.assertIn('url.pathname.endsWith("/creative/project/cover")', self.script)
        self.assertIn('return `/creative/project/cover_data${url.search}`;', self.script)
        self.assertIn('endpoint.startsWith("/creative/project/cover_data")', self.script)

    def test_creative_detail_and_reader_hydrate_cover_images(self) -> None:
        detail_marker = 'if (book.kind === "creative" && state.bookshelfPage === "detail") {'
        reader_marker = 'if (state.bookshelfPage === "reader" && book.kind === "creative") {'
        detail_block = self.script.split(detail_marker, 1)[1].split("return;", 1)[0]
        reader_block = self.script.split(reader_marker, 1)[1].split("return;", 1)[0]
        self.assertIn("void hydrateBookshelfImages(panel);", detail_block)
        self.assertIn("void hydrateBookshelfImages(panel);", reader_block)

    def test_creative_cover_has_click_and_keyboard_preview(self) -> None:
        self.assertIn('data-bookshelf-cover-preview disabled', self.script)
        self.assertIn('id="bookshelfCoverPreview"', self.html)
        self.assertIn('data-bookshelf-cover-preview-close', self.html)
        self.assertIn('event.key === "Escape"', self.script)
        self.assertIn("function bindBookshelfCoverPreviewDismissal()", self.script)
        self.assertIn('closeButton?.addEventListener("click"', self.script)
        self.assertIn('document.addEventListener("keydown", (event) => {', self.script)
        self.assertIn('}, true);', self.script)
        self.assertIn(".bookshelf-cover-preview", self.css)
        self.assertIn("cursor: zoom-in;", self.css)
        self.assertIn("touch-action: manipulation;", self.css)


class CreativeCoverDataEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_data_endpoint_returns_bridge_safe_data_url(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cover = Path(folder) / "cover.png"
            raw = b"\x89PNG\r\n\x1a\ncreative-cover-test"
            cover.write_bytes(raw)
            api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
            api.plugin = SimpleNamespace(
                _data_lock=asyncio.Lock(),
                data={"creative_projects": [{"id": "project-1", "cover_path": str(cover)}]},
            )
            app = Quart(__name__)

            async with app.test_request_context("/?id=project-1"):
                result = await api.get_creative_project_cover_data()

            self.assertTrue(result["success"])
            payload = result["data"]
            self.assertEqual(payload["mime"], "image/png")
            encoded = payload["data_url"].split(",", 1)[1]
            self.assertEqual(base64.b64decode(encoded), raw)


if __name__ == "__main__":
    unittest.main()
