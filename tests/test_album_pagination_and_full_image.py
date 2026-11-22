"""Tests for output album pagination, total count, thumbnail cache, and full-image API."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from companion_v2.conversation.media.generated import ALBUM_MAX_LIMIT, GeneratedAlbumImage, GeneratedImages
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.references.management import (
    ALBUM_LIMIT,
    ALBUM_THUMBNAIL_DIRNAME,
    ALBUM_THUMBNAIL_SIZE,
    ReferenceMaterialManagement,
    _clear_album_thumbnail_memory_cache,
    _prune_thumbnail_disk_cache,
)
from companion_v2.storage.generated_album import album_filename


def _make_image(color: str, *, fmt: str = "PNG", size: tuple[int, int] = (48, 32)) -> bytes:
    stream = BytesIO()
    mode = "RGBA" if fmt == "PNG" else "RGB"
    Image.new(mode, size, color).save(stream, format=fmt)
    return stream.getvalue()


def _album_item(
    image_id: str,
    content: bytes,
    *,
    created_at: str = "2026-10-12T04:05:06+00:00",
    action: str = "image.generate",
    mime_type: str = "image/png",
    width: int = 48,
    height: int = 32,
    position: int = 0,
) -> GeneratedAlbumImage:
    descriptor = ImageDescriptor(
        position, mime_type, width, height, len(content), hashlib.sha256(content).hexdigest(),
    )
    return GeneratedAlbumImage(image_id, created_at, action, descriptor, content)


class _MultiQuery(dict):
    def __init__(self, pairs=()):
        super().__init__()
        self._lists: dict[str, list[str]] = {}
        for key, value in pairs:
            self._lists.setdefault(key, []).append(value)
            self[key] = value

    def getlist(self, key: str) -> list[str]:
        return list(self._lists.get(key, ()))


def _outcome_response_stub(result, *, message=""):
    if result.failure is not None:
        return {
            "status": "error",
            "message": result.failure.code,
            "reason": result.failure.reason,
            "data": {"code": result.failure.code},
        }
    return {"status": "ok", "message": message, "data": result.unwrap()}


def _import_image_material_api():
    """Import ImageMaterialsWebAPI with a minimal astrbot.api.web shim when SDK is absent."""
    try:
        from astrbot_host.image_material_api import ImageMaterialsWebAPI
        from astrbot_host import web_api as _real_web_api  # noqa: F401
        return ImageMaterialsWebAPI, None
    except ModuleNotFoundError:
        keys = ("astrbot", "astrbot.api", "astrbot.api.web", "astrbot_host.web_api", "astrbot_host.image_material_api")
        saved = {name: sys.modules.get(name) for name in keys}
        astrbot_mod = types.ModuleType("astrbot")
        astrbot_mod.__path__ = []
        api_mod = types.ModuleType("astrbot.api")
        api_mod.__path__ = []
        web_mod = types.ModuleType("astrbot.api.web")
        web_mod.request = SimpleNamespace(username="", query=_MultiQuery())
        astrbot_mod.api = api_mod
        api_mod.web = web_mod
        web_api_stub = types.ModuleType("astrbot_host.web_api")
        web_api_stub.outcome_response = _outcome_response_stub
        sys.modules["astrbot"] = astrbot_mod
        sys.modules["astrbot.api"] = api_mod
        sys.modules["astrbot.api.web"] = web_mod
        sys.modules["astrbot_host.web_api"] = web_api_stub
        sys.modules.pop("astrbot_host.image_material_api", None)
        from astrbot_host.image_material_api import ImageMaterialsWebAPI
        return ImageMaterialsWebAPI, saved


def _restore_modules(saved):
    if saved is None:
        return
    for name, original in saved.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original


class GeneratedAlbumLimitTests(unittest.TestCase):
    def test_album_page_allows_limit_up_to_60_and_rejects_out_of_bounds(self):
        fake_messages = SimpleNamespace(runtime=SimpleNamespace(), authorize=lambda ctx, at: None)
        generated = GeneratedImages(fake_messages)
        for bad in (0, -1, 61, True, False, "24"):
            with self.assertRaises(TaskError) as error:
                generated.album_page(SimpleNamespace(), datetime.now(timezone.utc), limit=bad)
            self.assertEqual(error.exception.code, "generated_album_limit_invalid")
        self.assertEqual(ALBUM_MAX_LIMIT, 60)

    def test_album_count_counts_active_legacy_and_native_rows_excluding_hidden_and_revoked(self):
        import json
        import sqlite3
        from companion_v2.conversation.native_tool_results import PREFIX as NATIVE_PREFIX
        from companion_v2.execution.models import digest, encode
        from companion_v2.foundation.refs import SourceRef, VersionRef
        from companion_v2.storage.runtime import StorageRuntime

        with tempfile.TemporaryDirectory(prefix="companion-v2-album-count-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary), installation_id="inst-1")
            try:
                at = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
                scope = ScopeRef("inst-1", "onebot", ScopeKind.PRIVATE, "private-a")
                context = SimpleNamespace(
                    key="ctx-count-1",
                    scope=scope,
                    role_binding=SimpleNamespace(
                        scope=scope,
                        role=SubjectRef("inst-1", SubjectKind.BOT, "role-a"),
                    ),
                )
                fake_messages = SimpleNamespace(runtime=runtime, authorize=lambda ctx, when: None)
                generated = GeneratedImages(fake_messages)

                def insert_source(conn, source_id, *, state="active", lineage="lin-1", rev=1):
                    conn.execute(
                        """INSERT INTO sources(
                               source_id, lineage_id, source_revision, owner, installation_id, platform,
                               scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                               issued_at, expires_at, recipients_json, content_hash, payload_json,
                               state, governance_revision, created_at, updated_at
                           ) VALUES(?, ?, ?, 'test', 'inst-1', 'onebot', 'private', 'private-a',
                                    'bot-1', 'acc-1', 'conv-1', 'sess-1', ?, NULL, '[]', 'h', '{}', ?, 1, ?, ?)""",
                        (source_id, lineage, rev, at.isoformat(), state, at.isoformat(), at.isoformat()),
                    )

                def insert_asset(conn, asset_id, *, state="active", lineage="lin-1", rev=1):
                    conn.execute(
                        """INSERT INTO assets(
                               asset_id, lineage_id, asset_revision, owner, installation_id, platform,
                               scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                               recipients_json, content_hash, state, governance_revision, created_at, updated_at
                           ) VALUES(?, ?, ?, 'test', 'inst-1', 'onebot', 'private', 'private-a',
                                    'bot-1', 'acc-1', 'conv-1', 'sess-1', '[]', 'h', ?, 1, ?, ?)""",
                        (asset_id, lineage, rev, state, at.isoformat(), at.isoformat()),
                    )

                seed_conn = sqlite3.connect(str(runtime.database_path))
                try:
                    seed_conn.execute("PRAGMA foreign_keys = OFF")
                    # 1. Legacy batch with 2 active images (one will be hidden) + 1 revoked image.
                    seed_conn.execute(
                        """INSERT INTO conversation_batches(
                               batch_id, origin_key, context_json, role_instruction, state, revision, created_at, contributions_json
                           ) VALUES('batch-1', ?, '{}', '', 'ready', 1, ?, '{}')""",
                        (context.key, at.isoformat()),
                    )
                    seed_conn.execute(
                        """INSERT INTO extension_tool_invocations(
                               invocation_id, parent_task_id, tool_call_id, batch_id, task_id, action,
                               descriptor_json, provider_json, arguments_json, state, effect, created_at
                           ) VALUES('legacy-inv-1', 't-1', 'tc-1', 'batch-1', 'ext-t-1', 'image.generate',
                                    '{}', '{}', '{}', 'succeeded', 'committed', ?)""",
                        (at.isoformat(),),
                    )
                    parent_id = "generated-images-" + digest("legacy-inv-1")
                    insert_source(seed_conn, parent_id, lineage=parent_id, rev=1)
                    for pos, src_state in ((0, "active"), (1, "active"), (2, "revoked")):
                        src_id = f"legacy-src-{pos}"
                        asset_id = f"legacy-asset-{pos}"
                        insert_source(seed_conn, src_id, state=src_state)
                        insert_asset(seed_conn, asset_id, state="active")
                        seed_conn.execute(
                            """INSERT INTO conversation_image_payloads VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (parent_id, parent_id, 1, pos, src_id, "lin-1", 1, asset_id, "lin-1", 1, "{}", b"raw"),
                        )

                    # 2. Native operation using real production $.source object format (2 active + 1 revoked).
                    prod_native_parent = SourceRef(
                        "native-parent-prod-1", "native.image", scope, VersionRef("native-parent-prod-1", 1), at
                    )
                    insert_source(
                        seed_conn,
                        prod_native_parent.source_id,
                        lineage=prod_native_parent.version.lineage_id,
                        rev=1,
                    )
                    for pos, asset_state in ((0, "active"), (1, "active"), (2, "revoked")):
                        src_id = f"native-prod-src-{pos}"
                        asset_id = f"native-prod-asset-{pos}"
                        insert_source(seed_conn, src_id, state="active")
                        insert_asset(seed_conn, asset_id, state=asset_state)
                        seed_conn.execute(
                            """INSERT INTO conversation_image_payloads VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                prod_native_parent.source_id,
                                prod_native_parent.version.lineage_id,
                                1,
                                pos,
                                src_id,
                                "lin-1",
                                1,
                                asset_id,
                                "lin-1",
                                1,
                                "{}",
                                b"raw",
                            ),
                        )
                    seed_conn.execute(
                        """INSERT INTO execution_tasks(
                               task_id, origin_key, kind, lane, dedupe_key, request_hash, revision,
                               state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                               created_at, updated_at, record_json
                           ) VALUES('native-task-prod-1', ?, 'native', 'foreground', 'dedupe-prod-1', 'req-h', 1,
                                    'succeeded', 1, 1, 'att-1', ?, ?, ?, ?, '{}')""",
                        (context.key, at.isoformat(), at.isoformat(), at.isoformat(), at.isoformat()),
                    )
                    prod_meta_doc = {
                        "invocation_id": "native-prod-inv-1",
                        "task_id": "native-task-prod-1",
                        "origin_key": context.key,
                        "action": "image.generate",
                        "state": "succeeded",
                        "source": json.loads(encode(prod_native_parent)),
                        "images": [{"position": 0}, {"position": 1}, {"position": 2}],
                    }
                    seed_conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                        (NATIVE_PREFIX + "native-prod-inv-1", json.dumps(prod_meta_doc)),
                    )

                    # 3. Native operation using fallback result_source_json string format (1 active image).
                    native_parent = SourceRef("native-parent-1", "native.image", scope, VersionRef("native-parent-1", 1), at)
                    insert_source(seed_conn, native_parent.source_id, lineage=native_parent.version.lineage_id, rev=1)
                    insert_source(seed_conn, "native-src-0", state="active")
                    insert_asset(seed_conn, "native-asset-0", state="active")
                    seed_conn.execute(
                        """INSERT INTO conversation_image_payloads VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (native_parent.source_id, native_parent.version.lineage_id, 1, 0,
                         "native-src-0", "lin-1", 1, "native-asset-0", "lin-1", 1, "{}", b"raw"),
                    )
                    seed_conn.execute(
                        """INSERT INTO execution_tasks(
                               task_id, origin_key, kind, lane, dedupe_key, request_hash, revision,
                               state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                               created_at, updated_at, record_json
                           ) VALUES('native-task-1', ?, 'native', 'foreground', 'dedupe-1', 'req-h', 1,
                                    'succeeded', 1, 1, 'att-1', ?, ?, ?, ?, '{}')""",
                        (context.key, at.isoformat(), at.isoformat(), at.isoformat(), at.isoformat()),
                    )
                    meta_doc = {
                        "invocation_id": "native-inv-1",
                        "task_id": "native-task-1",
                        "origin_key": context.key,
                        "action": "image.edit",
                        "state": "succeeded",
                        "result_source_json": encode(native_parent),
                        "images": [{"position": 0}],
                    }
                    seed_conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                        (NATIVE_PREFIX + "native-inv-1", json.dumps(meta_doc)),
                    )
                    seed_conn.commit()
                finally:
                    seed_conn.close()

                # 2 active legacy + 2 active prod native ($.source) + 1 active fallback native = 5 (revoked excluded).
                self.assertEqual(generated.album_count(context, at), 5)
                # Hiding one legacy item + one prod $.source native item + one fallback native item drops count accurately.
                self.assertEqual(
                    generated.album_count(context, at, hidden_ids={"generated-image:native-prod-inv-1:1"}),
                    4,
                )
                self.assertEqual(
                    generated.album_count(
                        context,
                        at,
                        hidden_ids={
                            "generated-image:legacy-inv-1:1",
                            "generated-image:native-prod-inv-1:0",
                            "generated-image:native-inv-1:0",
                        },
                    ),
                    2,
                )
            finally:
                runtime.close()

    def test_album_count_reads_production_native_source_object_without_result_source_json(self):
        import json
        import sqlite3
        from companion_v2.conversation.native_tool_results import PREFIX as NATIVE_PREFIX
        from companion_v2.execution.models import encode
        from companion_v2.foundation.refs import SourceRef, VersionRef
        from companion_v2.storage.runtime import StorageRuntime

        with tempfile.TemporaryDirectory(prefix="companion-v2-album-count-prod-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary), installation_id="inst-1")
            try:
                at = datetime(2026, 11, 18, 13, 9, 56, tzinfo=timezone.utc)
                scope = ScopeRef("inst-1", "onebot", ScopeKind.PRIVATE, "private-prod")
                context = SimpleNamespace(
                    key="ctx-prod-source-1",
                    scope=scope,
                    role_binding=SimpleNamespace(
                        scope=scope,
                        role=SubjectRef("inst-1", SubjectKind.BOT, "role-prod"),
                    ),
                )
                fake_messages = SimpleNamespace(runtime=runtime, authorize=lambda ctx, when: None)
                generated = GeneratedImages(fake_messages)

                seed_conn = sqlite3.connect(str(runtime.database_path))
                try:
                    seed_conn.execute("PRAGMA foreign_keys = OFF")
                    for idx in range(3):
                        inv_id = f"native-tool-prod-{idx}"
                        task_id = f"native-task-{idx}"
                        parent = SourceRef(f"prod-parent-{idx}", "native.image", scope, VersionRef(f"prod-parent-{idx}", 1), at)
                        src_id = f"prod-src-{idx}"
                        asset_id = f"prod-asset-{idx}"
                        for sid, lineage in ((parent.source_id, parent.version.lineage_id), (src_id, "lin-prod")):
                            seed_conn.execute(
                                """INSERT INTO sources(
                                       source_id, lineage_id, source_revision, owner, installation_id, platform,
                                       scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                                       issued_at, expires_at, recipients_json, content_hash, payload_json,
                                       state, governance_revision, created_at, updated_at
                                   ) VALUES(?, ?, 1, 'native.image', 'inst-1', 'onebot', 'private', 'private-prod',
                                            'bot-1', 'acc-1', 'conv-1', 'sess-1', ?, NULL, '[]', 'h', '{}', 'active', 1, ?, ?)""",
                                (sid, lineage, at.isoformat(), at.isoformat(), at.isoformat()),
                            )
                        seed_conn.execute(
                            """INSERT INTO assets(
                                   asset_id, lineage_id, asset_revision, owner, installation_id, platform,
                                   scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                                   recipients_json, content_hash, state, governance_revision, created_at, updated_at
                               ) VALUES(?, 'lin-prod', 1, 'native.image', 'inst-1', 'onebot', 'private', 'private-prod',
                                        'bot-1', 'acc-1', 'conv-1', 'sess-1', '[]', 'h', 'active', 1, ?, ?)""",
                            (asset_id, at.isoformat(), at.isoformat()),
                        )
                        seed_conn.execute(
                            """INSERT INTO conversation_image_payloads VALUES(?, ?, 1, 0, ?, 'lin-prod', 1, ?, 'lin-prod', 1, '{}', ?)""",
                            (parent.source_id, parent.version.lineage_id, src_id, asset_id, b"raw"),
                        )
                        seed_conn.execute(
                            """INSERT INTO execution_tasks(
                                   task_id, origin_key, kind, lane, dedupe_key, request_hash, revision,
                                   state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                                   created_at, updated_at, record_json
                               ) VALUES(?, ?, 'native', 'foreground', ?, 'req-h', 1,
                                        'succeeded', 1, 1, 'att-1', ?, ?, ?, ?, '{}')""",
                            (task_id, context.key, f"dedupe-{idx}", at.isoformat(), at.isoformat(), at.isoformat(), at.isoformat()),
                        )
                        # Exact production runtime_metadata schema: nested "source" object, no "result_source_json".
                        doc = {
                            "invocation_id": inv_id,
                            "task_id": task_id,
                            "origin_key": context.key,
                            "action": "image.generate",
                            "state": "succeeded",
                            "source": json.loads(encode(parent)),
                            "images": [{"position": 0}],
                        }
                        self.assertNotIn("result_source_json", doc)
                        seed_conn.execute(
                            "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                            (NATIVE_PREFIX + inv_id, json.dumps(doc)),
                        )
                    seed_conn.commit()
                finally:
                    seed_conn.close()

                self.assertEqual(generated.album_count(context, at), 3)
                self.assertEqual(
                    generated.album_count(context, at, hidden_ids={"generated-image:native-tool-prod-1:0"}),
                    2,
                )
            finally:
                runtime.close()


class ReferenceAlbumPaginationAndCacheTests(unittest.TestCase):
    def setUp(self):
        _clear_album_thumbnail_memory_cache()

    def tearDown(self):
        _clear_album_thumbnail_memory_cache()

    def _build_manager(self, data_root: Path, items: list[GeneratedAlbumImage], *, hidden_ids=()):
        class FakeGeneratedImages:
            def __init__(self, entries):
                self.entries = list(entries)
                self.page_calls = []
                self.image_calls = []
                self.count_calls = []

            def album_page(self, context, at, *, limit=24, cursor=None):
                self.page_calls.append((cursor, limit))
                start = int(cursor.removeprefix("cursor-idx-")) if cursor else 0
                batch = tuple(self.entries[start:start + limit])
                next_cursor = f"cursor-idx-{start + limit}" if start + limit < len(self.entries) else None
                return {"items": batch, "next_cursor": next_cursor}

            def album_count(self, context, at, *, hidden_ids=()):
                self.count_calls.append(set(hidden_ids))
                return sum(1 for item in self.entries if item.image_id not in hidden_ids)

            def album_image(self, context, at, image_id):
                self.image_calls.append(image_id)
                for entry in self.entries:
                    if entry.image_id == image_id:
                        return entry
                raise TaskError("generated_image_missing", "the selected album image is unavailable")

        generated_images = FakeGeneratedImages(items)
        runtime = SimpleNamespace(data_root=data_root, installation_id="inst-1")
        application = SimpleNamespace(
            runtime=runtime,
            messages=SimpleNamespace(generated_images=generated_images),
        )
        manager = ReferenceMaterialManagement(application)
        manager._admit = lambda operator, context, at: "owner-1"
        manager._hidden_album_ids = lambda context: set(hidden_ids)
        context = SimpleNamespace(
            key="ctx-1",
            scope=ScopeRef("inst-1", "onebot", ScopeKind.PRIVATE, "private-a"),
            role_binding=SimpleNamespace(role=SubjectRef("inst-1", SubjectKind.BOT, "role-a")),
        )
        return manager, context, generated_images

    def test_album_pagination_limit_total_and_item_metadata(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-album-page-") as temporary:
            data_root = Path(temporary)
            png_a = _make_image("#336699", fmt="PNG", size=(64, 48))
            jpg_b = _make_image("#994433", fmt="JPEG", size=(80, 60))
            hidden_c = _make_image("#555555", fmt="PNG", size=(40, 40))
            entries = [
                _album_item("generated-image:inv-1:0", png_a, action="image.generate", mime_type="image/png", width=64, height=48),
                _album_item("generated-image:inv-hidden:0", hidden_c, action="image.generate", mime_type="image/png", width=40, height=40),
                _album_item("generated-image:inv-2:0", jpg_b, action="image.edit", mime_type="image/jpeg", width=80, height=60),
            ]
            manager, context, generated = self._build_manager(
                data_root, entries, hidden_ids={"generated-image:inv-hidden:0"},
            )
            at = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)

            first = manager.album(SimpleNamespace(), context, at=at)
            self.assertEqual(generated.page_calls[-1], (None, ALBUM_LIMIT))
            self.assertEqual(first["total"], 2)
            self.assertEqual(len(first["items"]), 2)
            item0, item1 = first["items"]
            self.assertEqual(item0["filename"], album_filename(
                entries[0].image_id, entries[0].created_at, entries[0].action, png_a, mime_type="image/png",
            ))
            self.assertEqual(item0["mime_type"], "image/png")
            self.assertEqual(item0["size_bytes"], len(png_a))
            self.assertEqual((item0["width"], item0["height"]), (64, 48))
            self.assertTrue(item0["thumbnail"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(item1["mime_type"], "image/jpeg")
            self.assertEqual(item1["size_bytes"], len(jpg_b))
            self.assertTrue(item1["filename"].endswith(".jpg"))

            # Explicit page sizes up to 60 are forwarded; invalid limits fail cleanly.
            manager.album(SimpleNamespace(), context, at=at, limit=48)
            self.assertEqual(generated.page_calls[-1], (None, 48))
            manager.album(SimpleNamespace(), context, at=at, limit=60)
            self.assertEqual(generated.page_calls[-1], (None, 60))
            for bad in (0, 61, True, "24"):
                with self.assertRaises(TaskError) as error:
                    manager.album(SimpleNamespace(), context, at=at, limit=bad)
                self.assertEqual(error.exception.code, "generated_album_limit_invalid")

    def test_thumbnail_cache_reuses_memory_and_disk_by_content_hash_and_recovers_corrupt_disk(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-thumb-cache-") as temporary:
            data_root = Path(temporary)
            png = _make_image("#227755", fmt="PNG", size=(96, 64))
            content_hash = hashlib.sha256(png).hexdigest()
            entries = [
                _album_item("generated-image:inv-cache-1:0", png),
                _album_item("generated-image:inv-cache-2:0", png),
            ]
            manager, context, _ = self._build_manager(data_root, entries)
            at = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)

            render_calls = 0
            original_render = ReferenceMaterialManagement._render_album_thumbnail

            def counting_render(payload, *, size=ALBUM_THUMBNAIL_SIZE):
                nonlocal render_calls
                render_calls += 1
                return original_render(payload, size=size)

            with patch.object(ReferenceMaterialManagement, "_render_album_thumbnail", side_effect=counting_render):
                first = manager.album(SimpleNamespace(), context, at=at)
                # Two cards with identical content_hash only decode once.
                self.assertEqual(render_calls, 1)
                self.assertEqual(first["items"][0]["thumbnail"], first["items"][1]["thumbnail"])

                # Second call hits memory LRU with zero Pillow decodes.
                second = manager.album(SimpleNamespace(), context, at=at)
                self.assertEqual(render_calls, 1)
                self.assertEqual(second["items"][0]["thumbnail"], first["items"][0]["thumbnail"])

                # Clearing memory LRU hits disk cache with zero Pillow decodes.
                _clear_album_thumbnail_memory_cache()
                cache_file = data_root / "media" / ALBUM_THUMBNAIL_DIRNAME / f"{content_hash}-{ALBUM_THUMBNAIL_SIZE}.jpg"
                self.assertTrue(cache_file.is_file())
                third = manager.album(SimpleNamespace(), context, at=at)
                self.assertEqual(render_calls, 1)
                self.assertEqual(third["items"][0]["thumbnail"], first["items"][0]["thumbnail"])

                # Corrupted disk file is rejected and regenerated safely.
                _clear_album_thumbnail_memory_cache()
                cache_file.write_bytes(b"not-a-jpeg")
                fourth = manager.album(SimpleNamespace(), context, at=at)
                self.assertEqual(render_calls, 2)
                self.assertEqual(fourth["items"][0]["thumbnail"], first["items"][0]["thumbnail"])

            # Bounded disk cache pruning evicts oldest entries.
            cache_dir = data_root / "media" / ALBUM_THUMBNAIL_DIRNAME
            for idx in range(6):
                (cache_dir / f"{idx:064x}-{ALBUM_THUMBNAIL_SIZE}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([idx]))
            _prune_thumbnail_disk_cache(cache_dir, cap=4)
            remaining = [p for p in cache_dir.iterdir() if p.suffix == ".jpg"]
            self.assertLessEqual(len(remaining), 4)

    def test_album_image_returns_lossless_original_and_enforces_hidden_and_missing(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-full-img-") as temporary:
            data_root = Path(temporary)
            png = _make_image("#1144aa", fmt="PNG", size=(120, 90))
            hidden_png = _make_image("#aa4411", fmt="PNG", size=(40, 40))
            entries = [
                _album_item("generated-image:inv-full:0", png, width=120, height=90),
                _album_item("generated-image:inv-hidden:0", hidden_png, width=40, height=40),
            ]
            manager, context, generated = self._build_manager(
                data_root, entries, hidden_ids={"generated-image:inv-hidden:0"},
            )
            at = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)

            result = manager.album_image(SimpleNamespace(), context, at=at, image_id="generated-image:inv-full:0")
            self.assertEqual(generated.image_calls, ["generated-image:inv-full:0"])
            self.assertEqual(result["image_id"], "generated-image:inv-full:0")
            self.assertEqual(result["filename"], album_filename(
                entries[0].image_id, entries[0].created_at, entries[0].action, png, mime_type="image/png",
            ))
            self.assertEqual(result["mime_type"], "image/png")
            self.assertEqual((result["width"], result["height"]), (120, 90))
            self.assertEqual(result["size_bytes"], len(png))
            self.assertEqual(result["created_at"], entries[0].created_at)
            self.assertEqual(result["action"], "image.generate")
            self.assertTrue(result["data_url"].startswith("data:image/png;base64,"))
            decoded = base64.b64decode(result["data_url"].split(",", 1)[1], validate=True)
            self.assertEqual(decoded, png)

            # Hidden image is rejected.
            with self.assertRaises(TaskError) as hidden_error:
                manager.album_image(SimpleNamespace(), context, at=at, image_id="generated-image:inv-hidden:0")
            self.assertEqual(hidden_error.exception.code, "generated_image_missing")

            # Unknown or invalid image_id returns generated_image_missing.
            for missing_id in ("", "generated-image:does-not-exist:0", "x" * 181):
                with self.assertRaises(TaskError) as missing_error:
                    manager.album_image(SimpleNamespace(), context, at=at, image_id=missing_id)
                self.assertEqual(missing_error.exception.code, "generated_image_missing")


class ImageMaterialsWebAPIRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_album_and_album_image_routes_validate_query_auth_and_payloads(self):
        ImageMaterialsWebAPI, saved = _import_image_material_api()
        try:
            with tempfile.TemporaryDirectory(prefix="companion-v2-api-route-") as temporary:
                data_root = Path(temporary)
                png = _make_image("#225588", fmt="PNG", size=(64, 64))
                item = _album_item("generated-image:route-1:0", png, width=64, height=64)

                class FakeManager:
                    def __init__(self):
                        self.album_calls = []
                        self.image_calls = []

                    def album(self, operator, context, *, at, cursor=None, limit=ALBUM_LIMIT):
                        self.album_calls.append((cursor, limit))
                        return {"items": [{"image_id": item.image_id, "filename": "sample.png"}],
                                "next_cursor": None, "total": 1}

                    def album_image(self, operator, context, *, at, image_id):
                        self.image_calls.append(image_id)
                        if image_id != item.image_id:
                            raise TaskError("generated_image_missing", "the selected album image is unavailable")
                        return {
                            "image_id": item.image_id,
                            "filename": album_filename(item.image_id, item.created_at, item.action, png, mime_type="image/png"),
                            "mime_type": "image/png",
                            "width": 64,
                            "height": 64,
                            "size_bytes": len(png),
                            "created_at": item.created_at,
                            "action": item.action,
                            "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                        }

                fake_manager = FakeManager()
                runtime = SimpleNamespace(data_root=data_root)
                context = SimpleNamespace(scope=SimpleNamespace(kind=ScopeKind.PRIVATE))
                session = SimpleNamespace(
                    profile=SimpleNamespace(target=context),
                    application=SimpleNamespace(runtime=runtime),
                )
                controller = SimpleNamespace(
                    _control_lock=asyncio.Lock(),
                    _terminated=False,
                    now=lambda: datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc),
                    runtime=runtime,
                    session=session,
                )
                api = ImageMaterialsWebAPI(controller)

                # Routes expose GET /album and GET /album/image.
                route_map = {path: methods for path, _, methods, _ in api.routes()}
                self.assertEqual(route_map["/astrbot_plugin_private_companion/image-materials/album"], ["GET"])
                self.assertEqual(route_map["/astrbot_plugin_private_companion/image-materials/album/image"], ["GET"])

                def authorize_material_access(username, session, runtime, context, at):
                    return SimpleNamespace(), context, at, fake_manager

                # 1. Valid limit query parameter reaches manager.album.
                with patch("astrbot_host.image_material_api.request",
                           SimpleNamespace(username="admin", query=_MultiQuery([("limit", "48")]))), \
                     patch.object(api, "_authorize_material_access", side_effect=authorize_material_access):
                    res = await api.album()
                self.assertEqual(res["status"], "ok")
                self.assertEqual(fake_manager.album_calls[-1], (None, 48))
                self.assertEqual(res["data"]["total"], 1)

                # 2. Out-of-range or non-numeric limit is rejected cleanly.
                for bad_limit in ("0", "61", "abc", "-5", "9" * 6000):
                    with patch("astrbot_host.image_material_api.request",
                               SimpleNamespace(username="admin", query=_MultiQuery([("limit", bad_limit)]))):
                        bad_res = await api.album()
                    self.assertEqual(bad_res["status"], "error")
                    self.assertEqual(bad_res["data"]["code"], "generated_album_limit_invalid")

                # 3. Unknown query keys on /album are rejected.
                with patch("astrbot_host.image_material_api.request",
                           SimpleNamespace(username="admin", query=_MultiQuery([("extra", "1")]))):
                    extra_res = await api.album()
                self.assertEqual(extra_res["status"], "error")
                self.assertEqual(extra_res["data"]["code"], "image_material_request_invalid")

                # 4. GET /album/image returns full original payload with filename.
                with patch("astrbot_host.image_material_api.request",
                           SimpleNamespace(username="admin", query=_MultiQuery([("image_id", item.image_id)]))), \
                     patch.object(api, "_authorize_material_access", side_effect=authorize_material_access):
                    full_res = await api.album_image()
                self.assertEqual(full_res["status"], "ok")
                self.assertEqual(full_res["data"]["image_id"], item.image_id)
                self.assertEqual(full_res["data"]["size_bytes"], len(png))
                self.assertTrue(full_res["data"]["data_url"].startswith("data:image/png;base64,"))
                self.assertRegex(full_res["data"]["filename"], r"^20261012-040506-generate-[0-9a-f]{12}\.png$")

                # 5. Missing image_id returns generated_image_missing; missing query param returns invalid.
                with patch("astrbot_host.image_material_api.request",
                           SimpleNamespace(username="admin", query=_MultiQuery([("image_id", "generated-image:404:0")]))), \
                     patch.object(api, "_authorize_material_access", side_effect=authorize_material_access):
                    missing_res = await api.album_image()
                self.assertEqual(missing_res["status"], "error")
                self.assertEqual(missing_res["data"]["code"], "generated_image_missing")

                with patch("astrbot_host.image_material_api.request",
                           SimpleNamespace(username="admin", query=_MultiQuery([]))):
                    no_param = await api.album_image()
                self.assertEqual(no_param["status"], "error")
                self.assertEqual(no_param["data"]["code"], "image_material_request_invalid")

                # 6. Unauthenticated request is rejected before manager is called.
                with patch("astrbot_host.image_material_api.request",
                           SimpleNamespace(username="", query=_MultiQuery([("image_id", item.image_id)]))):
                    unauth = await api.album_image()
                self.assertEqual(unauth["status"], "error")
                self.assertEqual(unauth["data"]["code"], "dashboard_identity_required")
        finally:
            _restore_modules(saved)


class DailyOutfitAlbumIntegrationTests(unittest.TestCase):
    def setUp(self):
        _clear_album_thumbnail_memory_cache()

    def tearDown(self):
        _clear_album_thumbnail_memory_cache()

    def test_daily_outfit_photos_merge_into_album_pagination_total_full_image_export_hide_and_auth(self):
        import json
        import zipfile
        from companion_v2.references.daily_outfit import (
            DAILY_OUTFIT_ACTION,
            PREFIX as OUTFIT_PREFIX,
            DailyOutfitPhotos,
            daily_outfit_image_id,
        )
        from companion_v2.storage.image_payloads import store_image
        from companion_v2.storage.runtime import StorageRuntime

        with tempfile.TemporaryDirectory(prefix="companion-v2-daily-outfit-album-") as temporary:
            data_root = Path(temporary)
            runtime = StorageRuntime.open(data_root=data_root, installation_id="inst-1")
            try:
                owner = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
                other_owner = "ffeeddccbbaa99887766554433221100"
                outfit_png_new = _make_image("#2e6f56", size=(120, 180))
                outfit_png_mid = _make_image("#553f7a", size=(110, 160))
                outfit_png_other = _make_image("#884422", size=(90, 90))
                marker_new = store_image(data_root, outfit_png_new, mime_type="image/png").decode("ascii")
                marker_mid = store_image(data_root, outfit_png_mid, mime_type="image/png").decode("ascii")
                marker_other = store_image(data_root, outfit_png_other, mime_type="image/png").decode("ascii")
                hash_new = hashlib.sha256(outfit_png_new).hexdigest()
                hash_mid = hashlib.sha256(outfit_png_mid).hexdigest()
                hash_other = hashlib.sha256(outfit_png_other).hexdigest()
                outfit_id_new = daily_outfit_image_id(owner, "2026-11-22", hash_new)
                outfit_id_mid = daily_outfit_image_id(owner, "2026-11-20", hash_mid)
                outfit_id_other = daily_outfit_image_id(other_owner, "2026-11-22", hash_other)

                with runtime.transaction() as conn:
                    conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                        (
                            f"{OUTFIT_PREFIX}{owner}:2026-11-22",
                            json.dumps({
                                "task_id": "outfit-task-new",
                                "photo": {
                                    "marker": marker_new,
                                    "content_hash": hash_new,
                                    "created_at": "2026-11-22T01:30:00+00:00",
                                    "weather": {"role_location": "维也纳", "condition": "晴", "temperature_c": 3},
                                    "activities": ["午间开指热身", "冬季巡演曲目定稿"],
                                },
                            }),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                        (
                            f"{OUTFIT_PREFIX}{owner}:2026-11-20",
                            json.dumps({
                                "task_id": "outfit-task-mid",
                                "photo": {
                                    "marker": marker_mid,
                                    "content_hash": hash_mid,
                                    "created_at": "2026-11-20T06:00:00+00:00",
                                    "weather": {"role_location": "维也纳", "condition": "阴", "temperature_c": 5},
                                    "activities": ["练琴"],
                                },
                            }),
                        ),
                    )
                    # Another role's daily outfit must never leak or be readable.
                    conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                        (
                            f"{OUTFIT_PREFIX}{other_owner}:2026-11-22",
                            json.dumps({
                                "task_id": "outfit-task-other",
                                "photo": {
                                    "marker": marker_other,
                                    "content_hash": hash_other,
                                    "created_at": "2026-11-22T02:00:00+00:00",
                                    "weather": None,
                                    "activities": [],
                                },
                            }),
                        ),
                    )

                gen_items = [
                    _album_item("generated-image:g-newest:0", _make_image("#224466", size=(80, 80)),
                                created_at="2026-11-21T12:00:00+00:00", action="image.generate", width=80, height=80),
                    _album_item("generated-image:g-older:0", _make_image("#664422", size=(80, 80)),
                                created_at="2026-11-19T12:00:00+00:00", action="image.edit", width=80, height=80),
                    _album_item("generated-image:g-oldest:0", _make_image("#446622", size=(80, 80)),
                                created_at="2026-11-18T12:00:00+00:00", action="image.generate", width=80, height=80),
                ]

                class FakeGenerated:
                    def album_page(self, context, at, *, limit=24, cursor=None):
                        start = int(cursor.removeprefix("cursor-idx-")) if cursor else 0
                        batch = tuple(gen_items[start:start + limit])
                        nxt = f"cursor-idx-{start + limit}" if start + limit < len(gen_items) else None
                        return {"items": batch, "next_cursor": nxt}

                    def album_count(self, context, at, *, hidden_ids=()):
                        hidden = set(hidden_ids or ())
                        return sum(1 for item in gen_items if item.image_id not in hidden)

                    def album_image(self, context, at, image_id):
                        for item in gen_items:
                            if item.image_id == image_id:
                                return item
                        raise TaskError("generated_image_missing", "missing")

                app = SimpleNamespace(
                    runtime=runtime,
                    messages=SimpleNamespace(generated_images=FakeGenerated()),
                    references=SimpleNamespace(owner=lambda ctx: owner, assets=object()),
                    role_state=SimpleNamespace(settings=lambda ctx: ({"planning_timezone": "UTC"}, None)),
                    tasks=SimpleNamespace(_record=lambda row: None),
                )
                manager = ReferenceMaterialManagement(app)
                manager._admit = lambda op, ctx, at: owner
                context = SimpleNamespace(key="ctx-outfit-merge")
                at = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)

                # 1. Paginate merged stream (3 generated + 2 daily outfits = 5) with limit=2 across all pages.
                collected = []
                cursor = None
                for _ in range(5):
                    page = manager.album(SimpleNamespace(), context, at=at, cursor=cursor, limit=2)
                    self.assertLessEqual(len(page["items"]), 2)
                    self.assertEqual(page["total"], 5)
                    collected.extend(page["items"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                self.assertIsNone(cursor)
                self.assertEqual(
                    [entry["image_id"] for entry in collected],
                    [
                        outfit_id_new,
                        "generated-image:g-newest:0",
                        outfit_id_mid,
                        "generated-image:g-older:0",
                        "generated-image:g-oldest:0",
                    ],
                )
                first_outfit = collected[0]
                self.assertEqual(first_outfit["action"], DAILY_OUTFIT_ACTION)
                self.assertEqual(first_outfit["local_date"], "2026-11-22")
                self.assertEqual(first_outfit["width"], 120)
                self.assertEqual(first_outfit["height"], 180)
                self.assertEqual(first_outfit["size_bytes"], len(outfit_png_new))
                self.assertEqual(first_outfit["mime_type"], "image/png")
                self.assertRegex(first_outfit["filename"], r"^20261122-013000-wardrobe-outfit-[0-9a-f]{12}\.png$")
                self.assertEqual(first_outfit["weather"]["role_location"], "维也纳")
                self.assertEqual(first_outfit["activities"], ["午间开指热身", "冬季巡演曲目定稿"])

                # 2. GET /album/image returns lossless original for daily outfit photo.
                full = manager.album_image(SimpleNamespace(), context, at=at, image_id=outfit_id_new)
                self.assertEqual(full["image_id"], outfit_id_new)
                self.assertEqual(full["action"], DAILY_OUTFIT_ACTION)
                self.assertEqual(full["local_date"], "2026-11-22")
                self.assertEqual(full["filename"], first_outfit["filename"])
                self.assertEqual(
                    base64.b64decode(full["data_url"].removeprefix("data:image/png;base64,")),
                    outfit_png_new,
                )

                # 3. Cross-role / tampered-hash / nonexistent outfit IDs are rejected cleanly.
                for bad_id in (
                    outfit_id_other,
                    f"wardrobe-outfit:{owner[:8]}:2026-11-22:deadbeef",
                    f"wardrobe-outfit:{owner[:8]}:2026-11-25:{hash_new[:8]}",
                    "wardrobe-outfit:../etc/passwd",
                ):
                    with self.assertRaises(TaskError) as err:
                        manager.album_image(SimpleNamespace(), context, at=at, image_id=bad_id)
                    self.assertEqual(err.exception.code, "generated_image_missing")

                # 4. export_album supports exporting daily outfit photos alongside generated images.
                exported = manager.export_album(
                    SimpleNamespace(), context, at=at, image_ids=[outfit_id_new, "generated-image:g-newest:0"]
                )
                self.assertEqual(exported["count"], 2)
                zip_bytes = base64.b64decode(exported["data_url"].removeprefix("data:application/zip;base64,"))
                with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
                    self.assertEqual(len(zf.namelist()), 2)
                    self.assertEqual(zf.read(zf.namelist()[0]), outfit_png_new)

                # 5. hide_album_images non-destructively hides a daily outfit from /album & /album/image.
                manager.hide_album_images(SimpleNamespace(), context, at=at, image_ids=[outfit_id_mid])
                after_hide = manager.album(SimpleNamespace(), context, at=at, limit=24)
                self.assertEqual(after_hide["total"], 4)
                self.assertNotIn(outfit_id_mid, [item["image_id"] for item in after_hide["items"]])
                with self.assertRaises(TaskError) as hidden_err:
                    manager.album_image(SimpleNamespace(), context, at=at, image_id=outfit_id_mid)
                self.assertEqual(hidden_err.exception.code, "generated_image_missing")

                # 6. DailyOutfitPhotos.status() exposes image_id, filename, dimensions & size_bytes for lightbox.
                outfit_owner = DailyOutfitPhotos(runtime, None)
                outfit_owner.app = app
                status = outfit_owner.status(
                    context,
                    at=at,
                    preview=lambda raw: "data:image/jpeg;base64," + base64.b64encode(raw[:16]).decode("ascii"),
                )
                self.assertEqual(status["photo"]["image_id"], outfit_id_new)
                self.assertEqual(status["photo"]["local_date"], "2026-11-22")
                self.assertEqual(status["photo"]["filename"], first_outfit["filename"])
                self.assertEqual(status["photo"]["mime_type"], "image/png")
                self.assertEqual(status["photo"]["width"], 120)
                self.assertEqual(status["photo"]["height"], 180)
                self.assertEqual(status["photo"]["size_bytes"], len(outfit_png_new))
            finally:
                runtime.close()

    def test_daily_outfit_index_range_scan_and_zero_read_amplification_on_next_pages(self):
        import json
        from companion_v2.references.daily_outfit import (
            PREFIX as OUTFIT_PREFIX,
            daily_outfit_image_id,
        )
        from companion_v2.storage.image_payloads import store_image
        from companion_v2.storage.runtime import StorageRuntime

        with tempfile.TemporaryDirectory(prefix="companion-v2-outfit-perf-") as temporary:
            data_root = Path(temporary)
            runtime = StorageRuntime.open(data_root=data_root, installation_id="inst-1")
            try:
                owner = "1234567890abcdef1234567890abcdef"
                outfit_png = _make_image("#2e6f56", size=(80, 120))
                marker = store_image(data_root, outfit_png, mime_type="image/png").decode("ascii")
                outfit_hash = hashlib.sha256(outfit_png).hexdigest()
                outfit_id = daily_outfit_image_id(owner, "2026-11-22", outfit_hash)
                with runtime.transaction() as conn:
                    conn.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                        (
                            f"{OUTFIT_PREFIX}{owner}:2026-11-22",
                            json.dumps({
                                "task_id": "outfit-perf-1",
                                "photo": {
                                    "marker": marker,
                                    "content_hash": outfit_hash,
                                    "created_at": "2026-11-22T01:30:00+00:00",
                                    "weather": None,
                                    "activities": [],
                                },
                            }),
                        ),
                    )

                # 1. Verify runtime_metadata range query uses the primary-key index (not SCAN).
                prefix = f"{OUTFIT_PREFIX}{owner}:"
                prefix_end = f"{OUTFIT_PREFIX}{owner};"
                plan = runtime.read_all(
                    "EXPLAIN QUERY PLAN SELECT key, value FROM runtime_metadata WHERE key >= ? AND key < ?",
                    (prefix, prefix_end),
                )
                details = " ".join(str(row["detail"]) for row in plan)
                self.assertIn("SEARCH runtime_metadata USING", details)
                self.assertNotIn("SCAN runtime_metadata", details)

                # 2. Verify with GeneratedImages._encode_album_cursor present, pages 2..N do 1x read (no 2x amplification).
                gen_items = [
                    _album_item(
                        f"generated-image:inv-{idx:02d}:0",
                        _make_image("#335577", size=(64, 64)),
                        created_at=f"2026-11-{21 - idx:02d}T12:00:00+00:00",
                        action="image.generate",
                        width=64,
                        height=64,
                    )
                    for idx in range(5)
                ]
                page_calls = []

                class CursorAwareGenerated:
                    _encode_album_cursor = staticmethod(GeneratedImages._encode_album_cursor)

                    def album_page(self, context, at, *, limit=24, cursor=None):
                        page_calls.append((cursor, limit))
                        if cursor is None:
                            start = 0
                        else:
                            decoded = GeneratedImages._album_cursor(cursor)
                            key_before = (decoded["created_at"], decoded["invocation_id"], decoded["position"])
                            start = len(gen_items)
                            for i, item in enumerate(gen_items):
                                item_key = (item.created_at, item.image_id.split(":")[1], item.descriptor.position)
                                if item_key < key_before:
                                    start = i
                                    break
                        batch = tuple(gen_items[start:start + limit])
                        nxt = (
                            GeneratedImages._encode_album_cursor(batch[-1])
                            if batch and start + limit < len(gen_items)
                            else None
                        )
                        return {"items": batch, "next_cursor": nxt}

                    def album_count(self, context, at, *, hidden_ids=()):
                        return len(gen_items)

                app = SimpleNamespace(
                    runtime=runtime,
                    messages=SimpleNamespace(generated_images=CursorAwareGenerated()),
                    references=SimpleNamespace(owner=lambda ctx: owner, assets=object()),
                )
                manager = ReferenceMaterialManagement(app)
                manager._admit = lambda op, ctx, at: owner
                context = SimpleNamespace(key="ctx-outfit-perf")
                at = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)

                collected = []
                cursor = None
                calls_per_page = []
                for _ in range(5):
                    before_calls = len(page_calls)
                    page = manager.album(SimpleNamespace(), context, at=at, cursor=cursor, limit=2)
                    calls_per_page.append(len(page_calls) - before_calls)
                    self.assertEqual(page["total"], 6)
                    collected.extend(page["items"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        break
                self.assertIsNone(cursor)
                # Every single page fetches album_page at most ONCE (zero 2x read amplification!).
                self.assertEqual(calls_per_page, [1, 1, 1])
                self.assertEqual(
                    [entry["image_id"] for entry in collected],
                    [outfit_id, *(item.image_id for item in gen_items)],
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
