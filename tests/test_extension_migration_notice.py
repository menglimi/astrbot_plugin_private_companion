# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class ExtensionMigrationNoticeEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        class PluginStub:
            def __init__(self) -> None:
                self.data: dict = {}
                self._data_lock = asyncio.Lock()
                self.save_count = 0

            def _save_data_sync(self, **_kwargs) -> None:
                self.save_count += 1

        self.app = Quart(__name__)
        self.plugin = PluginStub()
        self.api = PrivateCompanionPageApi(self.plugin)

    async def test_dismissal_survives_page_reload_and_can_be_restored(self) -> None:
        async with self.app.test_request_context("/extension-migration-notice"):
            initial = await self.api.get_extension_migration_notice()
        self.assertFalse(initial["data"]["dismissed"])

        async with self.app.test_request_context(
            "/extension-migration-notice/update",
            method="POST",
            json={"version": "6.2.2", "dismissed": True},
        ):
            saved = await self.api.update_extension_migration_notice()
        self.assertTrue(saved["data"]["dismissed"])

        reloaded_api = PrivateCompanionPageApi(self.plugin)
        async with self.app.test_request_context("/extension-migration-notice"):
            persisted = await reloaded_api.get_extension_migration_notice()
        self.assertTrue(persisted["data"]["dismissed"])
        self.assertEqual(1, self.plugin.save_count)

        async with self.app.test_request_context(
            "/extension-migration-notice/update",
            method="POST",
            json={"version": "6.2.2", "dismissed": False},
        ):
            restored = await self.api.update_extension_migration_notice()
        self.assertFalse(restored["data"]["dismissed"])

        async with self.app.test_request_context("/extension-migration-notice"):
            current = await self.api.get_extension_migration_notice()
        self.assertFalse(current["data"]["dismissed"])
        self.assertEqual(2, self.plugin.save_count)

    async def test_unknown_notice_version_is_rejected(self) -> None:
        async with self.app.test_request_context(
            "/extension-migration-notice/update",
            method="POST",
            json={"version": "9.9.9", "dismissed": True},
        ):
            result = await self.api.update_extension_migration_notice()
        self.assertFalse(result["success"])
        self.assertEqual(400, result.http_status)


if __name__ == "__main__":
    unittest.main()
