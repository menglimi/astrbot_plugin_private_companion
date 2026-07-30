# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io
import tempfile
import unittest
import asyncio
import json
import zipfile
from pathlib import Path

from astrbot_plugin_private_companion.reaction_asset_library import ReactionAssetLibrary
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ReactionAssetLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.library = ReactionAssetLibrary(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_deduplicates_and_preserves_utf8_metadata(self) -> None:
        first = self.library.import_blobs(
            [("无语摊手.png", PNG_BYTES)],
            metadata={
                "tags": ["角色", "摊手"],
                "emotions": ["无语"],
                "intents": ["轻吐槽"],
                "scopes": ["private"],
            },
        )
        duplicate = self.library.import_blobs([("副本.png", PNG_BYTES)])

        self.assertEqual(1, first["imported"])
        self.assertEqual(0, duplicate["imported"])
        self.assertEqual(["副本.png"], duplicate["duplicates"])
        item = self.library.list_items()["items"][0]
        self.assertEqual("无语摊手", item["name"])
        self.assertIn("无语", item["emotions"])
        self.assertEqual(["private"], item["scopes"])
        self.assertTrue(Path(self.temp_dir.name, "reaction_expression_library", "catalog.json").read_text(encoding="utf-8").startswith("{"))

    def test_find_uses_own_catalog_and_respects_scope(self) -> None:
        item = self.library.import_blobs(
            [("无语摊手.png", PNG_BYTES)],
            metadata={"emotions": ["无语"], "intents": ["吐槽"], "scopes": ["private"]},
        )["items"][0]

        private_result = self.library.find("无语吐槽", scope="private")
        group_result = self.library.find("无语吐槽", scope="group")

        self.assertIsNotNone(private_result)
        self.assertEqual(f"pc-local:{item['id']}", private_result["image_id"])
        self.assertTrue(Path(private_result["path"]).is_file())
        self.assertIsNone(group_result)

    def test_update_usage_and_delete_are_persisted(self) -> None:
        item = self.library.import_blobs([("开心.png", PNG_BYTES)])["items"][0]
        result = self.library.update_items(
            [item["id"]],
            {"name": "开心庆祝", "tags": "庆祝，鼓掌", "scopes": ["private", "group"], "enabled": False},
        )
        self.assertEqual(1, result["updated"])
        self.assertTrue(self.library.mark_used(f"pc-local:{item['id']}"))
        updated = self.library.list_items()["items"][0]
        self.assertEqual("开心庆祝", updated["name"])
        self.assertFalse(updated["enabled"])
        self.assertEqual(1, updated["usage_count"])
        path = self.library._path_for(updated)

        deleted = self.library.delete_items([item["id"]])

        self.assertEqual(1, deleted["deleted"])
        self.assertFalse(path.exists())
        self.assertEqual(0, self.library.summary()["total"])

    def test_string_enabled_values_are_normalized(self) -> None:
        item = self.library.import_blobs(
            [("关闭.png", PNG_BYTES)],
            metadata={"enabled": "false"},
        )["items"][0]
        self.assertFalse(item["enabled"])

        result = self.library.update_items([item["id"]], {"enabled": "true"})
        self.assertEqual(1, result["updated"])
        self.assertTrue(self.library.list_items()["items"][0]["enabled"])

        result = self.library.update_items([item["id"]], {"enabled": "off"})
        self.assertEqual(1, result["updated"])
        self.assertFalse(self.library.list_items()["items"][0]["enabled"])

    def test_zip_import_rejects_path_traversal(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("../escape.png", PNG_BYTES)
        result = self.library.import_base64_payloads(
            [{"name": "unsafe.zip", "data": base64.b64encode(archive_bytes.getvalue()).decode("ascii")}]
        )

        self.assertEqual(0, result["imported"])
        self.assertIn("不安全路径", result["rejected"][0]["reason"])
        self.assertFalse(Path(self.temp_dir.name, "escape.png").exists())

    def test_rescan_indexes_manually_added_image(self) -> None:
        manual = self.library.images_dir / "手动导入-惊讶.png"
        manual.write_bytes(PNG_BYTES)

        result = self.library.rescan()

        self.assertEqual(1, result["scanned"])
        self.assertEqual(1, result["imported"])
        item = self.library.list_items()["items"][0]
        self.assertEqual(manual.name, item["stored_name"])
        self.assertEqual("rescan", item["source"])


class _RuntimeEvent:
    unified_msg_origin = "default:FriendMessage:10001"
    message_str = "来一张无语表情包"

    @staticmethod
    def get_sender_id() -> str:
        return "10001"

    @staticmethod
    def get_message_str() -> str:
        return "来一张无语表情包"

    @staticmethod
    def is_private_chat() -> bool:
        return True


class _RuntimeHarness(LlmToolActionsMixin):
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.data = {"users": {"10001": {}}}
        self._data_lock = asyncio.Lock()
        self.enabled = True
        self.enable_reaction_expression_experiment = True
        self.reaction_expression_private_enabled = True
        self.reaction_expression_group_enabled = False
        self.reaction_expression_trigger_probability = 1.0
        self.reaction_expression_cooldown_seconds = 0
        self.reaction_expression_low_latency_mode = True
        self.reaction_expression_candidate_limit = 6

    def _get_user(self, user_id: str):
        return self.data["users"].setdefault(user_id, {})

    @staticmethod
    def _save_data_sync() -> None:
        return None


class ReactionAssetRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_lookup_uses_plugin_owned_catalog_without_extra_provider(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = ReactionAssetLibrary(folder)
            imported = library.import_blobs(
                [("无语摊手.png", PNG_BYTES)],
                metadata={"emotions": ["无语"], "intents": ["吐槽"], "scopes": ["private"]},
            )
            harness = _RuntimeHarness(folder)

            raw = await harness._pc_find_reaction_image_impl(
                _RuntimeEvent(),
                query="无语吐槽",
                send=False,
                low_latency=True,
            )
            result = json.loads(raw)

            self.assertTrue(result["success"])
            self.assertTrue(result["found"])
            self.assertFalse(result["sent"])
            self.assertEqual(f"pc-local:{imported['items'][0]['id']}", result["image_id"])


if __name__ == "__main__":
    unittest.main()
