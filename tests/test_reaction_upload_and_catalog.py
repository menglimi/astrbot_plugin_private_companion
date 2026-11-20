"""Regression tests for reaction upload metadata, ordering, validation, stats, and group scope."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from PIL import Image

from astrbot_host.media import inspect_image
from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.media.models import MAX_IMAGE_BYTES
from companion_v2.execution.models import TaskError
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.storage.semantic import SemanticStore
from tests import test_astrbot_staging_host as support
from tests.animation_support import animation_bytes
from tests.group_memory_management_support import GroupMemoryManagementFixture
from tests.test_semantic_host_api import SemanticRequests
from tests.test_semantic_integration import SemanticPort


def _png_bytes(index: int = 0) -> bytes:
    """Create a tiny deterministic unique PNG image for each index."""
    color = (index % 251, (index * 37) % 251, (index * 73) % 251)
    output = io.BytesIO()
    image = Image.new("RGB", (16, 16), color)
    image.putpixel((index % 16, (index // 16) % 16), ((index * 19) % 255, 255 - (index % 255), 128))
    image.save(output, format="PNG")
    return output.getvalue()


def _b64_png(index: int = 0) -> str:
    return base64.b64encode(_png_bytes(index)).decode("ascii")


class ReactionUploadAndCatalogAPITests(SemanticRequests, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-reaction-upload-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes(
            "4.27.2", support.FakeEvent, support.FakeProvider,
            NS, NS, support.FakeReverseApi,
        )
        self.controller = support.AstrBotStagingController(
            self.native, support.host_config(), Path(temporary.name),
            native_types=types, now=lambda: support.NOW,
        )
        self.addAsyncCleanup(self.controller.terminate)
        self.selection = {
            "target_id": self.controller.settings.profile_id,
            "role_id": self.controller.settings.role_id,
        }
        self.register_semantic()

    async def _start(self):
        (await self.controller.initialize_profile()).unwrap()
        (await self.controller.start()).unwrap()
        port = SemanticPort()
        self.controller.session.application.semantics.gateway.port = port
        return self.controller.session.application.semantics, port

    async def test_uploaded_source_filename_is_not_pinned_and_vision_analysis_marks_analyzed(self):
        _service, port = await self._start()
        port.metadata_result = {
            "name": "开心拍手",
            "description": "小猫开心拍手",
            "visible_text": "好耶",
            "tags": "开心、拍手",
            "emotions": "欢喜",
            "intents": "庆祝、附和",
        }
        imported = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(1),
            metadata={"source_filename": r"C:\Users\demo\Pictures\IMG_8492.gif"},
        )
        self.assertEqual(imported["status"], "ok", imported)
        self.assertTrue(imported["data"]["created"])
        item = imported["data"]["item"]
        self.assertEqual(item["payload"]["name"], "IMG_8492.gif")
        self.assertEqual(item["payload"]["manual_fields"], [])
        self.assertEqual(item["payload"]["analysis"], "pending")

        before = await self.semantic_request(self.selection, kind="reaction")
        self.assertEqual(before["data"]["catalog"]["reaction"]["total"], 1)
        self.assertEqual(before["data"]["catalog"]["reaction"]["pending"], 1)
        self.assertEqual(before["data"]["catalog"]["reaction"]["recognized"], 0)

        analyzed = await self.semantic_request(self.selection, action="analyze", id=item["id"])
        self.assertEqual(analyzed["status"], "ok", analyzed)
        after_item = analyzed["data"]["item"]
        self.assertEqual(after_item["payload"]["name"], "开心拍手")
        self.assertEqual(after_item["payload"]["description"], "小猫开心拍手")
        self.assertEqual(after_item["payload"]["visible_text"], "好耶")
        self.assertEqual(after_item["payload"]["tags"], "开心、拍手")
        self.assertEqual(after_item["payload"]["intents"], "庆祝、附和")
        self.assertEqual(after_item["payload"]["manual_fields"], [])
        self.assertEqual(after_item["payload"]["analysis"], "analyzed")

        after = await self.semantic_request(self.selection, kind="reaction")
        self.assertEqual(after["data"]["catalog"]["reaction"]["pending"], 0)
        self.assertEqual(after["data"]["catalog"]["reaction"]["recognized"], 1)

    async def test_explicit_manual_name_and_editor_changes_stay_protected_on_analyze(self):
        service, port = await self._start()
        port.metadata_result = {
            "name": "模型生成名",
            "description": "模型画面说明",
            "visible_text": "",
            "tags": "模型标签",
            "emotions": "开心",
            "intents": "互动",
        }
        # 1. Explicit metadata.name remains manual for API backward compatibility.
        explicit = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(2),
            metadata={"name": "人工指定名"},
        )
        explicit_item = explicit["data"]["item"]
        self.assertEqual(explicit_item["payload"]["manual_fields"], ["name"])
        explicit_analyzed = await self.semantic_request(
            self.selection, action="analyze", id=explicit_item["id"],
        )
        self.assertEqual(explicit_analyzed["data"]["item"]["payload"]["name"], "人工指定名")
        self.assertEqual(explicit_analyzed["data"]["item"]["payload"]["description"], "模型画面说明")
        self.assertEqual(explicit_analyzed["data"]["item"]["payload"]["analysis"], "manual")

        # 2. Toggling enabled on an uploaded item without changing text keeps analysis='pending'
        #    and manual_fields=[], so analyze() still fills name and marks 'analyzed'.
        uploaded = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(3),
            metadata={"source_filename": "raw_upload.png"},
        )
        up_item = uploaded["data"]["item"]
        toggled = await self.semantic_request(
            self.selection, action="edit", kind="reaction",
            id=up_item["id"], revision=up_item["revision"],
            values={"name": "raw_upload.png", "description": "", "visible_text": "",
                    "tags": "", "emotions": "", "intents": ""},
            enabled=False,
        )
        self.assertEqual(toggled["data"]["item"]["payload"]["manual_fields"], [])
        self.assertEqual(toggled["data"]["item"]["payload"]["analysis"], "pending")
        up_analyzed = await self.semantic_request(
            self.selection, action="analyze", id=up_item["id"],
        )
        self.assertEqual(up_analyzed["data"]["item"]["payload"]["name"], "模型生成名")
        self.assertEqual(up_analyzed["data"]["item"]["payload"]["analysis"], "analyzed")

        # 3. Editing only tags on a pending upload keeps it pending until description exists,
        #    and protects tags while still letting analyze() replace the raw upload filename.
        tagged_upload = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(4),
            metadata={"source_filename": "needs_desc.png"},
        )
        tag_item = tagged_upload["data"]["item"]
        edited = await self.semantic_request(
            self.selection, action="edit", kind="reaction",
            id=tag_item["id"], revision=tag_item["revision"],
            values={"name": "needs_desc.png", "description": "", "visible_text": "",
                    "tags": "用户标签", "emotions": "", "intents": ""},
            enabled=True,
        )
        self.assertEqual(edited["data"]["item"]["payload"]["manual_fields"], ["tags"])
        self.assertEqual(edited["data"]["item"]["payload"]["analysis"], "pending")
        tag_analyzed = await self.semantic_request(
            self.selection, action="analyze", id=tag_item["id"],
        )
        self.assertEqual(tag_analyzed["data"]["item"]["payload"]["name"], "模型生成名")
        self.assertEqual(tag_analyzed["data"]["item"]["payload"]["tags"], "用户标签")
        self.assertEqual(tag_analyzed["data"]["item"]["payload"]["analysis"], "manual")

        # 4. Legacy row without manual_fields key still protects its non-empty fields.
        owner = service.ensure_reaction_catalog(self.controller.session.profile.target)
        legacy = await self.semantic_request(
            self.selection, action="import", content=_b64_png(5),
            metadata={"name": "旧人工名", "description": "旧说明"},
        )
        legacy_row = service.store.get(owner, "reaction", legacy["data"]["item"]["id"])
        legacy_payload = dict(legacy_row["payload"])
        legacy_payload.pop("manual_fields", None)
        legacy_payload["analysis"] = "manual"
        service.store.put(owner, "reaction", legacy_row["id"], legacy_payload,
                          expected=legacy_row["revision"], enabled=True)
        legacy_analyzed = await self.semantic_request(
            self.selection, action="analyze", id=legacy_row["id"],
        )
        self.assertEqual(legacy_analyzed["data"]["item"]["payload"]["name"], "旧人工名")
        self.assertEqual(legacy_analyzed["data"]["item"]["payload"]["description"], "旧说明")
        self.assertEqual(legacy_analyzed["data"]["item"]["payload"]["tags"], "模型标签")

    async def test_source_filename_sanitizes_paths_and_rejects_bad_types(self):
        await self._start()
        for idx, (raw_name, expected_name) in enumerate((
            ("../../secret/subdir/smile.webp", "smile.webp"),
            (".", "待标注表情"),
            ("..", "待标注表情"),
            ("\x00\x01\r\n", "待标注表情"),
            ("", "待标注表情"),
            (None, "待标注表情"),
        ), start=10):
            with self.subTest(raw_name=raw_name):
                res = await self.semantic_request(
                    self.selection, action="import",
                    content=_b64_png(idx),
                    metadata={"source_filename": raw_name},
                )
                self.assertEqual(res["status"], "ok", res)
                self.assertEqual(res["data"]["item"]["payload"]["name"], expected_name)
                self.assertEqual(res["data"]["item"]["payload"]["manual_fields"], [])

        invalid = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(99),
            metadata={"source_filename": 12345},
        )
        self.assertEqual(invalid["data"]["code"], "semantic_request_invalid")

    async def test_newly_uploaded_stickers_surface_first_across_24_item_pages(self):
        await self._start()
        uploaded_ids = []
        for idx in range(26):
            res = await self.semantic_request(
                self.selection, action="import",
                content=_b64_png(100 + idx),
                metadata={"source_filename": f"sticker-{idx}.png"},
            )
            self.assertEqual(res["status"], "ok", res)
            self.assertTrue(res["data"]["created"])
            uploaded_ids.append(res["data"]["item"]["id"])

        # Pick a 27th image whose SHA-256 sorts past the 24th alphabetical ID.
        alpha_cutoff = sorted(uploaded_ids)[23]
        tail_idx = 200
        while True:
            raw = _png_bytes(tail_idx)
            digest = inspect_image(raw, 0).descriptor.content_hash
            if digest > alpha_cutoff and digest not in uploaded_ids:
                break
            tail_idx += 1
        latest = await self.semantic_request(
            self.selection, action="import",
            content=base64.b64encode(_png_bytes(tail_idx)).decode("ascii"),
            metadata={"source_filename": "newest-high-hash.png"},
        )
        newest_id = latest["data"]["item"]["id"]
        uploaded_ids.append(newest_id)

        page1 = await self.semantic_request(self.selection, kind="reaction")
        self.assertEqual(page1["status"], "ok", page1)
        p1_ids = [row["id"] for row in page1["data"]["items"]]
        self.assertEqual(len(p1_ids), 24)
        self.assertEqual(p1_ids[0], newest_id)
        self.assertEqual(p1_ids, list(reversed(uploaded_ids[-24:])))
        next_after = page1["data"]["next_after"]
        self.assertIsInstance(next_after, str)
        self.assertTrue(next_after.startswith("r:"))

        page2 = await self.semantic_request(self.selection, kind="reaction", after=next_after)
        self.assertEqual(page2["status"], "ok", page2)
        p2_ids = [row["id"] for row in page2["data"]["items"]]
        self.assertEqual(p2_ids, list(reversed(uploaded_ids[:3])))
        self.assertIsNone(page2["data"]["next_after"])
        self.assertEqual(len(set(p1_ids + p2_ids)), 27)

        for bad_cursor in ("not-a-cursor", "r:", "r:0", "r:-1", "r:abc"):
            with self.subTest(bad_cursor=bad_cursor):
                bad = await self.semantic_request(self.selection, kind="reaction", after=bad_cursor)
                self.assertEqual(bad["data"]["code"], "semantic_request_invalid")

    async def test_empty_zero_byte_and_malformed_uploads_return_structured_errors(self):
        await self._start()
        # Missing content (e.g. old JS JSON.stringify stripping undefined) -> host_image_invalid
        missing_content = await self.semantic_request(
            self.selection, action="import", metadata={"source_filename": "empty.png"},
        )
        self.assertEqual(missing_content["data"]["code"], "host_image_invalid")

        for label, content in (
            ("empty_str", ""),
            ("whitespace", "   "),
            ("none", None),
            ("invalid_base64", "not!valid!base64"),
            ("non_image_bytes", base64.b64encode(b"plain text, not an image").decode("ascii")),
        ):
            with self.subTest(label=label):
                res = await self.semantic_request(
                    self.selection, action="import",
                    content=content,
                    metadata={"source_filename": f"{label}.png"},
                )
                self.assertEqual(res["data"]["code"], "host_image_invalid")

        # Oversized base64 string (> 5 MiB decoded) -> host_image_limit
        oversized = await self.semantic_request(
            self.selection, action="import",
            content="A" * (((MAX_IMAGE_BYTES + 2) // 3 * 4) + 4),
            metadata={"source_filename": "huge.png"},
        )
        self.assertEqual(oversized["data"]["code"], "host_image_limit")

        # Schema type mismatches remain semantic_request_invalid
        wrong_content_type = await self.semantic_request(
            self.selection, action="import", content=12345, metadata={},
        )
        self.assertEqual(wrong_content_type["data"]["code"], "semantic_request_invalid")
        wrong_metadata_type = await self.semantic_request(
            self.selection, action="import", content=_b64_png(1), metadata="not-a-dict",
        )
        self.assertEqual(wrong_metadata_type["data"]["code"], "semantic_request_invalid")

        # Direct inspect_image(b"", 0) also reports host_image_invalid rather than host_image_limit.
        with self.assertRaises(TaskError) as caught:
            inspect_image(b"", 0)
        self.assertEqual(caught.exception.code, "host_image_invalid")

    async def test_catalog_recognition_stats_reflect_live_rows_not_monotonic_collection_totals(self):
        service, _port = await self._start()
        owner = service.ensure_reaction_catalog(self.controller.session.profile.target)

        # Seed monotonic lifetime collection totals far larger than the live catalog.
        for index in range(12):
            service.store.record_reaction_collection(
                owner, f"priv-{index}", "private_capture", "pending_confirmation",
                at=1000.0 + index,
            )
        for index in range(8):
            service.store.record_reaction_collection(
                owner, f"grp-{index}", "group_capture", "pending_confirmation",
                at=2000.0 + index,
            )

        # 2 auto-reviewed stickers awaiting confirmation (enabled=False), 1 analyzed, 1 pending.
        confirm_ids = []
        for idx in (30, 31):
            image = inspect_image(_png_bytes(idx), 0)
            item, _created = service.reactions.import_image_with_status(
                owner, image,
                {"name": f"自动收录{idx}", "description": "挥手表情", "intents": "打招呼"},
                collection_source="private_capture", auto_reviewed=True, enabled=False,
            )
            payload = {**item["payload"], "collection_state": "awaiting_confirmation"}
            updated = service.store.put(owner, "reaction", item["id"], payload,
                                        expected=item["revision"], enabled=False)
            confirm_ids.append(updated)

        analyzed_res = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(32),
            metadata={"name": "已标注", "description": "拥抱安慰"},
        )
        self.assertEqual(analyzed_res["status"], "ok")
        pending_res = await self.semantic_request(
            self.selection, action="import",
            content=_b64_png(33),
            metadata={"source_filename": "pending.png"},
        )
        self.assertEqual(pending_res["status"], "ok")

        status = await self.semantic_request(self.selection, kind="reaction")
        reaction_stats = status["data"]["catalog"]["reaction"]
        self.assertEqual(reaction_stats["total"], 4)
        self.assertEqual(reaction_stats["enabled"], 2)
        self.assertEqual(reaction_stats["disabled"], 2)
        self.assertEqual(reaction_stats["pending"], 1)
        self.assertEqual(reaction_stats["recognized"], 3)
        self.assertEqual(reaction_stats["awaiting_confirmation"], 2)
        # Lifetime totals stay intact in reaction_collection.totals
        totals = status["data"]["reaction_collection"]["totals"]
        self.assertEqual(totals["private_capture"]["pending_confirmation"], 12)
        self.assertEqual(totals["group_capture"]["pending_confirmation"], 8)

        # Confirming one awaiting_confirmation sticker decrements awaiting_confirmation
        # without clobbering its auto_reviewed analysis status.
        first_confirm = confirm_ids[0]
        confirmed = await self.semantic_request(
            self.selection, action="edit", kind="reaction",
            id=first_confirm["id"], revision=first_confirm["revision"],
            values={k: first_confirm["payload"][k] for k in
                    ("name", "description", "visible_text", "tags", "emotions", "intents")},
            enabled=True,
        )
        self.assertEqual(confirmed["status"], "ok", confirmed)
        self.assertEqual(confirmed["data"]["item"]["payload"]["collection_state"], "confirmed")
        self.assertEqual(confirmed["data"]["item"]["payload"]["analysis"], "auto_reviewed")

        after_confirm = await self.semantic_request(self.selection, kind="reaction")
        stats_after = after_confirm["data"]["catalog"]["reaction"]
        self.assertEqual(stats_after["total"], 4)
        self.assertEqual(stats_after["enabled"], 3)
        self.assertEqual(stats_after["recognized"], 3)
        self.assertEqual(stats_after["awaiting_confirmation"], 1)
        self.assertEqual(stats_after["pending"], 1)


class GroupPreBatchReactionAPITests(SemanticRequests, GroupMemoryManagementFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.register_semantic()

    async def test_group_role_reaction_catalog_works_before_first_group_batch_while_slang_stays_blocked(self):
        await self.start_host()
        port = SemanticPort()
        port.metadata_result = {
            "name": "群前上传表情",
            "description": "招手打招呼",
            "visible_text": "哈喽",
            "tags": "打招呼",
            "emotions": "友好",
            "intents": "问候",
        }
        self.app.semantics.gateway.port = port
        selection = {"target_id": "group-a", "role_id": "role-a"}

        # Before any group message arrives: selection_ready is False, reaction_ready is True.
        initial = await self.semantic_request(selection, kind="reaction")
        self.assertEqual(initial["status"], "ok", initial)
        self.assertFalse(initial["data"]["selection_ready"])
        self.assertTrue(initial["data"]["reaction_ready"])
        self.assertEqual(initial["data"]["scope_kind"], "group")
        self.assertEqual(initial["data"]["catalog"]["reaction"]["total"], 0)
        self.assertNotIn("slang", initial["data"]["catalog"])

        # Role-owned reaction import, list, detail, analyze, edit, search all work before first batch.
        imported = await self.semantic_request(
            selection, action="import",
            content=_b64_png(50),
            metadata={"source_filename": "before-group-msg.png"},
        )
        self.assertEqual(imported["status"], "ok", imported)
        self.assertTrue(imported["data"]["created"])
        item = imported["data"]["item"]
        self.assertEqual(item["payload"]["manual_fields"], [])
        self.assertEqual(item["payload"]["analysis"], "pending")

        listed = await self.semantic_request(selection, kind="reaction")
        self.assertEqual([row["id"] for row in listed["data"]["items"]], [item["id"]])
        self.assertEqual(listed["data"]["catalog"]["reaction"]["total"], 1)
        self.assertEqual(listed["data"]["catalog"]["reaction"]["pending"], 1)

        detail = await self.semantic_request(selection, kind="reaction", id=item["id"])
        self.assertEqual(detail["status"], "ok", detail)
        self.assertEqual(detail["data"]["item"]["id"], item["id"])
        self.assertTrue(detail["data"]["image_url"].startswith("data:image/png;base64,"))

        analyzed = await self.semantic_request(selection, action="analyze", id=item["id"])
        self.assertEqual(analyzed["status"], "ok", analyzed)
        self.assertEqual(analyzed["data"]["item"]["payload"]["name"], "群前上传表情")
        self.assertEqual(analyzed["data"]["item"]["payload"]["analysis"], "analyzed")

        edited = await self.semantic_request(
            selection, action="edit", kind="reaction",
            id=item["id"], revision=analyzed["data"]["item"]["revision"],
            values={"tags": "群聊问候"}, enabled=True,
        )
        self.assertEqual(edited["status"], "ok", edited)
        self.assertEqual(edited["data"]["item"]["payload"]["tags"], "群聊问候")

        searched = await self.semantic_request(
            selection, action="search", kind="reaction", query="群前上传表情",
        )
        self.assertEqual(searched["status"], "ok", searched)
        self.assertEqual([row["id"] for row in searched["data"]["items"]], [item["id"]])

        # Group-owned slang and reindex MUST stay blocked with semantic_group_input_required.
        slang_list = await self.semantic_request(selection, kind="slang")
        self.assertEqual(slang_list["status"], "ok", slang_list)
        self.assertFalse(slang_list["data"]["selection_ready"])
        self.assertEqual(slang_list["data"]["items"], [])

        slang_search = await self.semantic_request(
            selection, action="search", kind="slang", query="接头暗号",
        )
        self.assertEqual(slang_search["data"]["code"], "semantic_group_input_required")

        slang_edit = await self.semantic_request(
            selection, action="edit", kind="slang",
            id="slang-1", revision=1,
            values={"term": "暗号", "meaning": "含义", "usage": "用法"}, enabled=True,
        )
        self.assertEqual(slang_edit["data"]["code"], "semantic_group_input_required")

        reindex = await self.semantic_request(selection, action="reindex")
        self.assertEqual(reindex["data"]["code"], "semantic_group_input_required")

        # Global scope and unconfigured roles still cannot import reactions.
        global_import = await self.semantic_request(
            {"global": True}, action="import", content=_b64_png(51), metadata={},
        )
        self.assertEqual(global_import["data"]["code"], "semantic_target_unavailable")

        bad_role = await self.semantic_request(
            {"target_id": "group-a", "role_id": "unknown-role"},
            action="import", content=_b64_png(51), metadata={},
        )
        self.assertEqual(bad_role["data"]["code"], "semantic_target_unavailable")

        # Delete works before first batch as well.
        temp_import = await self.semantic_request(
            selection, action="import", content=_b64_png(52), metadata={"source_filename": "del.png"},
        )
        deleted = await self.semantic_request(
            selection, action="delete", kind="reaction",
            id=temp_import["data"]["item"]["id"],
            revision=temp_import["data"]["item"]["revision"],
        )
        self.assertEqual(deleted["status"], "ok", deleted)
        self.assertTrue(deleted["data"]["deleted"])

        # After the first group message arrives, the same role-owned sticker stays visible.
        await self.conversation(remember=False, wording="大家好呀")
        after_batch = await self.semantic_request(selection, kind="reaction")
        self.assertTrue(after_batch["data"]["selection_ready"])
        self.assertTrue(after_batch["data"]["reaction_ready"])
        self.assertEqual([row["id"] for row in after_batch["data"]["items"]], [item["id"]])

        # Non-core operators remain strictly denied on group reaction imports.
        operator = dashboard_operator(self.controller.runtime, "admin", at=self.clock.now())
        policy = ObjectAccessPolicy(self.controller.runtime)
        policy.set_tier(operator.subject, AuthorizationTier.IMPORTANT,
                        expected_revision=policy.revision, observed_at=self.clock.now())
        denied = await self.semantic_request(
            {"target_id": "group-b", "role_id": "role-a"},
            action="import", content=_b64_png(53), metadata={},
        )
        self.assertEqual(denied["data"]["code"], "core_access_required")


class GifContainerAndStoreRecencyTests(unittest.TestCase):
    def test_gif_padding_accepted_while_truncation_and_zero_flood_rejected(self):
        gif = animation_bytes("GIF", count=3)
        self.assertEqual(gif[-1], 0x3B)

        # Trailing padding after 0x3B trailer
        trailing = inspect_image(gif + b"\x00\x00trailing", 0)
        self.assertEqual(trailing.descriptor.frame_count, 3)

        # Extra 0x00 block-terminator padding before 0x3B trailer
        padded_before_trailer = inspect_image(gif[:-1] + b"\x00\x00\x3b", 0)
        self.assertEqual(padded_before_trailer.descriptor.frame_count, 3)

        # Extra 0x00 between image descriptor blocks
        second_frame_pos = gif.index(b"\x2c", gif.index(b"\x2c") + 1)
        padded_between = inspect_image(gif[:second_frame_pos] + b"\x00" + gif[second_frame_pos:], 0)
        self.assertEqual(padded_between.descriptor.frame_count, 3)

        # Truncated right after 0x00 (missing 0x3B trailer) still rejected
        with self.assertRaises(TaskError) as caught:
            inspect_image(gif[:-1] + b"\x00", 0)
        self.assertEqual(caught.exception.code, "host_image_invalid")

        # Excessive consecutive 0x00 zero-flood rejected before Pillow scans it
        with self.assertRaises(TaskError) as caught:
            inspect_image(gif[:-1] + (b"\x00" * 65) + b"\x3b", 0)
        self.assertEqual(caught.exception.code, "host_image_invalid")

    def test_store_list_recent_page_and_promote_preserve_recency_without_schema_bump(self):
        with TemporaryDirectory() as temporary:
            store = SemanticStore(Path(temporary))
            with store.db() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            for ident in ("ffff", "aaaa", "cccc"):
                store.put("legacy", "reaction", ident, {"name": ident, "text": ident})

            # Existing list() remains ordered by id ASC for full-table scanners.
            self.assertEqual([row["id"] for row in store.list("legacy", "reaction")],
                             ["aaaa", "cccc", "ffff"])

            # list_recent_page() orders newest-first with deterministic keyset pagination.
            first, cursor = store.list_recent_page("legacy", "reaction", limit=2)
            self.assertEqual([row["id"] for row in first], ["cccc", "aaaa"])
            self.assertIsNotNone(cursor)
            second, end_cursor = store.list_recent_page("legacy", "reaction", after=cursor, limit=2)
            self.assertEqual([row["id"] for row in second], ["ffff"])
            self.assertIsNone(end_cursor)

            # promote_reaction_owner preserves chronological order when copying to role owner.
            self.assertTrue(store.promote_reaction_owner("legacy", "role-owner"))
            promoted, _ = store.list_recent_page("role-owner", "reaction", limit=10)
            self.assertEqual([row["id"] for row in promoted], ["cccc", "aaaa", "ffff"])
