# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
import asyncio
import json
import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.reaction_asset_library import ReactionAssetLibrary
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.private_image import PrivateImageMixin


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _gif_bytes(*, animated: bool) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    first = Image.new("RGBA", (3, 3), (255, 0, 0, 255))
    if animated:
        second = Image.new("RGBA", (3, 3), (0, 255, 0, 255))
        first.save(output, format="GIF", save_all=True, append_images=[second], duration=50, loop=0)
    else:
        first.save(output, format="GIF")
    return output.getvalue()


class _GifInputHarness(PrivateImageMixin):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.private_image_gif_max_frames = 4
        self.source_fallback_calls = 0

    def _private_image_source_bytes_if_gif(self, _source: str) -> bytes:
        return self.payload

    @staticmethod
    def _private_image_source_cache_key(_source: str) -> str:
        return "gif-test"

    def _private_image_source_to_model_url(self, _source: str) -> str:
        self.source_fallback_calls += 1
        return "data:image/gif;base64,unexpected"


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

    def test_import_queues_analysis_and_applies_structured_metadata(self) -> None:
        item = self.library.import_blobs([("001.png", PNG_BYTES)])["items"][0]

        self.assertEqual("pending", item["analysis_status"])
        self.assertEqual([item["id"]], [row["id"] for row in self.library.analysis_candidates()])
        self.assertEqual(1, self.library.mark_analysis_running([item["id"]]))
        self.assertEqual(
            [item["id"]],
            [
                row["id"]
                for row in self.library.analysis_candidates(statuses=("pending", "running"))
            ],
        )

        result = self.library.apply_analysis_results(
            [
                {
                    "id": item["id"],
                    "name": "无语看镜头",
                    "description": "角色无语地看向镜头",
                    "visible_text": "你认真的？",
                    "tags": ["角色", "看镜头"],
                    "emotions": ["无语"],
                    "intents": ["吐槽", "质疑"],
                }
            ],
            provider_id="vision-test",
        )

        self.assertEqual(1, result["completed"])
        analyzed = self.library.list_items()["items"][0]
        self.assertEqual("complete", analyzed["analysis_status"])
        self.assertEqual("无语看镜头", analyzed["name"])
        self.assertEqual("你认真的？", analyzed["visible_text"])
        self.assertIn("吐槽", analyzed["intents"])
        self.assertEqual("vision-test", analyzed["analysis_provider"])

    def test_catalog_cache_detects_atomic_rewrite_with_unchanged_mtime(self) -> None:
        item = self.library.import_blobs(
            [("停用自动识别.png", PNG_BYTES)],
            metadata={"auto_analyze": False},
        )["items"][0]
        catalog_path = self.library.catalog_path
        cached_mtime = catalog_path.stat().st_mtime_ns
        self.assertEqual("unprocessed", self.library.list_items()["items"][0]["analysis_status"])

        writer = ReactionAssetLibrary(self.temp_dir.name)
        self.assertEqual(1, writer.queue_analysis([item["id"]])["queued"])
        os.utime(catalog_path, ns=(cached_mtime, cached_mtime))

        refreshed = self.library.list_items()["items"][0]
        self.assertEqual("pending", refreshed["analysis_status"])

    def test_gif_analysis_uses_png_preview_but_preserves_original_asset(self) -> None:
        gif_data = _gif_bytes(animated=True)
        item = self.library.import_blobs([("开心.gif", gif_data)])["items"][0]

        preview = self.library.get_analysis_image_data(item["id"])
        stored = self.library.get_image_data(item["id"])

        self.assertIsNotNone(preview)
        self.assertTrue(preview["data_url"].startswith("data:image/png;base64,"))
        self.assertEqual("image/gif", stored["mime"])
        self.assertTrue(stored["data_url"].startswith("data:image/gif;base64,"))

    def test_provider_request_converts_static_and_animated_gif_to_png(self) -> None:
        for animated in (False, True):
            with self.subTest(animated=animated):
                harness = _GifInputHarness(_gif_bytes(animated=animated))
                request = SimpleNamespace(image_urls=["data:image/gif;base64,test"], images=[])

                replaced, dropped = harness._sanitize_provider_request_gif_inputs(request)

                self.assertEqual((1, 0), (replaced, dropped))
                self.assertGreaterEqual(len(request.image_urls), 1)
                self.assertTrue(all(url.startswith("data:image/png;base64,") for url in request.image_urls))
                self.assertEqual(0, harness.source_fallback_calls)

    def test_broken_gif_is_dropped_instead_of_sent_raw_to_provider(self) -> None:
        harness = _GifInputHarness(b"GIF89a-broken")
        request = SimpleNamespace(image_urls=["data:image/gif;base64,test"], images=[])

        replaced, dropped = harness._sanitize_provider_request_gif_inputs(request)

        self.assertEqual((0, 1), (replaced, dropped))
        self.assertEqual([], request.image_urls)
        self.assertEqual(0, harness.source_fallback_calls)

    def test_analysis_preserves_manual_fields_and_merges_generated_lists(self) -> None:
        item = self.library.import_blobs(
            [("角色.png", PNG_BYTES)],
            metadata={"tags": ["自定义角色"], "emotions": ["用户校准情绪"]},
        )["items"][0]

        self.library.update_items([item["id"]], {"name": "人工名称"})
        self.library.apply_analysis_results(
            [
                {
                    "id": item["id"],
                    "name": "模型名称",
                    "tags": ["摊手"],
                    "emotions": ["无语"],
                    "intents": ["吐槽"],
                }
            ]
        )

        analyzed = self.library.list_items()["items"][0]
        self.assertEqual("人工名称", analyzed["name"])
        self.assertEqual(["自定义角色", "摊手"], analyzed["tags"])
        self.assertEqual(["用户校准情绪", "无语"], analyzed["emotions"])

    def test_legacy_catalog_metadata_is_conservatively_protected(self) -> None:
        item_id = "legacy-item"
        stored_name = f"{item_id}.png"
        (self.library.images_dir / stored_name).write_bytes(PNG_BYTES)
        self.library.catalog_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "id": item_id,
                            "filename": "原文件.png",
                            "stored_name": stored_name,
                            "sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                            "name": "人工旧名称",
                            "tags": ["人工旧标签"],
                            "emotions": ["无语"],
                            "intents": ["吐槽"],
                            "scopes": ["private"],
                            "enabled": True,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        legacy = self.library.list_items()["items"][0]
        self.assertEqual("unprocessed", legacy["analysis_status"])
        self.assertTrue({"name", "tags", "emotions", "intents"} <= set(legacy["manual_fields"]))
        self.library.apply_analysis_results(
            [{"id": item_id, "name": "模型名称", "tags": ["新标签"], "emotions": ["惊讶"], "intents": ["接梗"]}]
        )
        analyzed = self.library.list_items()["items"][0]
        self.assertEqual("人工旧名称", analyzed["name"])
        self.assertIn("人工旧标签", analyzed["tags"])
        self.assertIn("新标签", analyzed["tags"])

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

    def test_find_uses_structured_candidate_queries_from_context(self) -> None:
        preferred = self.library.import_blobs(
            [("捂脸.png", PNG_BYTES)],
            metadata={"tags": ["捂脸"], "scopes": ["private"]},
        )["items"][0]

        result = self.library.find(
            "回应",
            context="候选检索表达：捂脸；轻松回应；当前语境：用户开了个玩笑",
            scope="private",
        )

        self.assertIsNotNone(result)
        self.assertEqual(f"pc-local:{preferred['id']}", result["image_id"])
        self.assertIn("捂脸", result["candidate_queries"])
        self.assertIn("捂脸", result["matched_queries"])

    def test_find_uses_local_semantic_equivalence_without_embedding(self) -> None:
        happy = self.library.import_blobs(
            [("开心.png", PNG_BYTES)],
            metadata={"tags": ["开心"], "scopes": ["private"]},
        )["items"][0]

        result = self.library.find("高兴", scope="private")

        self.assertIsNotNone(result)
        self.assertEqual(f"pc-local:{happy['id']}", result["image_id"])
        self.assertEqual("keyword_semantic", result["match_basis"])
        self.assertTrue(any(value.startswith("语义相近：") for value in result["matched_queries"]))

    def test_find_local_semantic_equivalence_matches_communication_intent(self) -> None:
        comfort = self.library.import_blobs(
            [("抱抱.png", PNG_BYTES)],
            metadata={"intents": ["抱抱"], "scopes": ["private"]},
        )["items"][0]

        result = self.library.find("想安慰一下", scope="private")

        self.assertIsNotNone(result)
        self.assertEqual(f"pc-local:{comfort['id']}", result["image_id"])
        self.assertEqual("keyword_semantic", result["match_basis"])

    def test_find_local_semantic_equivalence_respects_negation_and_topic(self) -> None:
        self.library.import_blobs(
            [("开心.png", PNG_BYTES)],
            metadata={"tags": ["开心"], "scopes": ["private"]},
        )

        self.assertIsNone(self.library.find("不开心", scope="private"))
        self.assertIsNone(self.library.find("查询天气", scope="private"))

    def test_find_softly_rotates_recently_used_equally_relevant_assets(self) -> None:
        first = self.library.import_blobs(
            [("开心一号.png", PNG_BYTES)],
            metadata={"tags": ["开心", "回应"], "scopes": ["private"]},
        )["items"][0]
        second = self.library.import_blobs(
            [("开心二号.png", PNG_BYTES + b"\x00")],
            metadata={"tags": ["开心", "回应"], "scopes": ["private"]},
        )["items"][0]

        initial = self.library.find("开心回应", scope="private")
        self.assertIsNotNone(initial)
        self.assertTrue(self.library.mark_used(initial["image_id"]))
        rotated = self.library.find("开心回应", scope="private")

        self.assertIsNotNone(rotated)
        self.assertNotEqual(initial["image_id"], rotated["image_id"])
        self.assertEqual(
            {f"pc-local:{first['id']}", f"pc-local:{second['id']}"},
            {initial["image_id"], rotated["image_id"]},
        )

    def test_find_uses_learned_asset_affinity_after_relevance_ties(self) -> None:
        preferred = self.library.import_blobs(
            [("开心偏好.png", PNG_BYTES)],
            metadata={"tags": ["开心", "回应"], "scopes": ["private"]},
        )["items"][0]
        other = self.library.import_blobs(
            [("开心普通.png", PNG_BYTES + b"\x01")],
            metadata={"tags": ["开心", "回应"], "scopes": ["private"]},
        )["items"][0]

        result = self.library.find(
            "开心回应",
            scope="private",
            selection_signature="intent-happy",
            selection_preferences={
                "intent_signature": "intent-happy",
                "assets": [
                    {
                        "key": f"pc-local:{preferred['id']}",
                        "score": 6,
                        "intent_score": 4,
                    },
                    {
                        "key": f"pc-local:{other['id']}",
                        "score": -4,
                        "intent_score": -2,
                    },
                ],
            },
        )

        self.assertIsNotNone(result)
        self.assertEqual(f"pc-local:{preferred['id']}", result["image_id"])
        self.assertGreater(result["preference_bias"], 0)

    def test_selection_revision_changes_after_delivery_without_changing_catalog_revision(self) -> None:
        item = self.library.import_blobs([("开心.png", PNG_BYTES)])["items"][0]
        catalog_revision = self.library.lookup_revision()
        selection_revision = self.library.selection_revision()

        self.assertTrue(self.library.mark_used(item["id"]))

        self.assertEqual(catalog_revision, self.library.lookup_revision())
        self.assertNotEqual(selection_revision, self.library.selection_revision())

    def test_lookup_revision_reuses_hot_cache_and_tracks_matching_edits(self) -> None:
        item = self.library.import_blobs(
            [("开心.png", PNG_BYTES)],
            metadata={"tags": ["开心"], "scopes": ["private"]},
        )["items"][0]

        first = self.library.lookup_revision()
        self.assertTrue(self.library.has_enabled_assets())
        with patch.object(self.library, "_load", wraps=self.library._load) as load:
            self.assertEqual(first, self.library.lookup_revision())
            self.assertTrue(self.library.has_enabled_assets())
            load.assert_not_called()

        # Usage counters do not affect matching, so the computed revision stays
        # stable even though the catalog file is persisted again.
        with patch.object(self.library, "_load", wraps=self.library._load) as load:
            self.assertTrue(self.library.mark_used(f"pc-local:{item['id']}"))
            self.assertEqual(first, self.library.lookup_revision())
            load.assert_called_once()

        self.library.update_items([item["id"]], {"tags": ["庆祝"]})
        self.assertNotEqual(first, self.library.lookup_revision())
        self.library.update_items([item["id"]], {"enabled": False})
        self.assertFalse(self.library.has_enabled_assets())

    def test_lookup_revision_detects_externally_deleted_image_after_ttl(self) -> None:
        item = self.library.import_blobs(
            [("开心.png", PNG_BYTES)],
            metadata={"tags": ["开心"], "scopes": ["private"]},
        )["items"][0]
        first = self.library.lookup_revision()
        self.assertTrue(self.library.has_enabled_assets())

        path = self.library._path_for(item)
        self.assertIsNotNone(path)
        path.unlink()
        self.library._lookup_revision_checked_at = 0.0

        self.assertNotEqual(first, self.library.lookup_revision())
        self.assertFalse(self.library.has_enabled_assets())

    def test_usage_write_does_not_hide_external_image_deletion(self) -> None:
        item = self.library.import_blobs([("开心.png", PNG_BYTES)])["items"][0]
        first = self.library.lookup_revision()
        path = self.library._path_for(item)
        self.assertIsNotNone(path)

        path.unlink()
        self.assertTrue(self.library.mark_used(item["id"]))

        self.assertNotEqual(first, self.library.lookup_revision())
        self.assertFalse(self.library.has_enabled_assets())

    def test_rescan_reports_duplicates_and_delete_keeps_locked_item(self) -> None:
        item = self.library.import_blobs([("已有.png", PNG_BYTES)])["items"][0]
        duplicate_path = self.library.images_dir / "目录重复.png"
        duplicate_path.write_bytes(PNG_BYTES)

        scan = self.library.rescan()

        self.assertEqual([duplicate_path.name], scan["duplicates"])
        self.assertEqual(0, scan["imported"])

        original_path = self.library._path_for(item)
        original_unlink = Path.unlink

        def fail_original(path, *args, **kwargs):
            if path == original_path:
                raise PermissionError("locked")
            return original_unlink(path, *args, **kwargs)

        try:
            Path.unlink = fail_original
            deleted = self.library.delete_items([item["id"]])
        finally:
            Path.unlink = original_unlink

        self.assertEqual(0, deleted["deleted"])
        self.assertEqual([item["id"]], deleted["failed"])
        self.assertEqual(1, self.library.summary()["total"])

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
    def _save_data_sync(**_kwargs) -> None:
        return None


class _EmbeddingProvider:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        self.last_texts: list[str] = []

    async def get_embeddings(self, texts: list[str]):
        self.last_texts = list(texts)
        return {
            "data": [
                {"embedding": [float(index + 1), 1.0]}
                for index, _text in enumerate(texts)
            ]
        }


class _EmbeddingContext:
    def __init__(self, providers: dict[str, _EmbeddingProvider]) -> None:
        self.providers = providers

    def get_embedding_provider_by_id(self, provider_id: str):
        return self.providers.get(provider_id)


class ReactionAssetRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_cached_library_does_not_shadow_runtime_accessor(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            harness = _RuntimeHarness(folder)

            first = harness._reaction_asset_library()
            second = harness._reaction_asset_library()

            self.assertIs(first, second)
            self.assertTrue(callable(harness._reaction_asset_library))
            self.assertIs(first, harness._reaction_asset_library_instance)

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

    async def test_runtime_lookup_cache_isolated_by_selection_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = ReactionAssetLibrary(folder)
            preferred = library.import_blobs(
                [("开心偏好.png", PNG_BYTES)],
                metadata={"tags": ["开心", "回应"], "scopes": ["private"]},
            )["items"][0]
            other = library.import_blobs(
                [("开心普通.png", PNG_BYTES + b"\x01")],
                metadata={"tags": ["开心", "回应"], "scopes": ["private"]},
            )["items"][0]
            harness = _RuntimeHarness(folder)

            def preferences(positive_id: str, negative_id: str) -> dict[str, object]:
                return {
                    "intent_signature": "intent-happy",
                    "assets": [
                        {
                            "key": f"pc-local:{positive_id}",
                            "score": 8,
                            "intent_score": 4,
                        },
                        {
                            "key": f"pc-local:{negative_id}",
                            "score": -8,
                            "intent_score": -4,
                        },
                    ],
                }

            preferred_lookup = json.loads(
                await harness._pc_find_reaction_image_impl(
                    _RuntimeEvent(),
                    query="开心回应",
                    send=False,
                    low_latency=True,
                    selection_preferences=preferences(preferred["id"], other["id"]),
                    selection_signature="intent-happy",
                )
            )
            other_preferences = preferences(other["id"], preferred["id"])
            other_lookup = json.loads(
                await harness._pc_find_reaction_image_impl(
                    _RuntimeEvent(),
                    query="开心回应",
                    send=False,
                    low_latency=True,
                    selection_preferences=other_preferences,
                    selection_signature="intent-happy",
                )
            )
            cached_other_lookup = json.loads(
                await harness._pc_find_reaction_image_impl(
                    _RuntimeEvent(),
                    query="开心回应",
                    send=False,
                    low_latency=True,
                    selection_preferences=other_preferences,
                    selection_signature="intent-happy",
                )
            )

            self.assertEqual(
                f"pc-local:{preferred['id']}",
                preferred_lookup["image_id"],
            )
            self.assertEqual(f"pc-local:{other['id']}", other_lookup["image_id"])
            self.assertFalse(preferred_lookup["cache_hit"])
            self.assertFalse(other_lookup["cache_hit"])
            self.assertTrue(cached_other_lookup["cache_hit"])

    async def test_embedding_provider_inheritance_and_reaction_override(self) -> None:
        shared = _EmbeddingProvider("embedding-shared")
        override = _EmbeddingProvider("embedding-reaction")
        harness = _RuntimeHarness("")
        harness.context = _EmbeddingContext(
            {shared.provider_id: shared, override.provider_id: override}
        )
        harness.embedding_provider_id = shared.provider_id
        harness.reaction_expression_embedding_provider_id = ""

        shared_provider, shared_id = await harness._shared_embedding_provider()
        inherited_provider, inherited_id = await harness._reaction_embedding_provider()

        self.assertIs(shared, shared_provider)
        self.assertEqual(shared.provider_id, shared_id)
        self.assertIs(shared, inherited_provider)
        self.assertEqual(shared.provider_id, inherited_id)

        harness.reaction_expression_embedding_provider_id = override.provider_id
        reaction_provider, reaction_id = await harness._reaction_embedding_provider()

        self.assertIs(override, reaction_provider)
        self.assertEqual(override.provider_id, reaction_id)

    async def test_embedding_batch_accepts_openai_style_data_rows(self) -> None:
        provider = _EmbeddingProvider("embedding-shared")
        harness = _RuntimeHarness("")

        vectors = await harness._reaction_embedding_vectors(provider, ["开心", "难过"])

        self.assertEqual(2, len(vectors))
        self.assertAlmostEqual(1.0, sum(value * value for value in vectors[0]))
        self.assertAlmostEqual(1.0, sum(value * value for value in vectors[1]))

    async def test_embedding_input_is_bounded_for_small_context_servers(self) -> None:
        provider = _EmbeddingProvider("embedding-shared")
        harness = _RuntimeHarness("")
        long_text = "标签；" * 600

        vector = await harness._reaction_embedding_vector(provider, long_text)

        self.assertTrue(vector)
        self.assertLessEqual(len(provider.last_texts[0]), 480)


if __name__ == "__main__":
    unittest.main()
