"""Tests for role-scoped generated-album folder materialization and open-folder API."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from companion_v2.conversation.media.generated import GeneratedAlbumImage
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.storage.generated_album import (
    album_filename,
    album_short_id,
    generated_album_root,
    materialize_album_image,
    role_album_dir,
    safe_role_slug,
    sync_generated_album_folder,
)
from companion_v2.storage.image_payloads import image_root, store_image


def _image_bytes(color: str, *, fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format=fmt)
    return buffer.getvalue()


def _album_entry(
    image_id: str,
    content: bytes,
    *,
    created_at: str = "2026-10-12T04:05:06+00:00",
    action: str = "image.generate",
    mime_type: str = "image/png",
    position: int = 0,
) -> GeneratedAlbumImage:
    descriptor = ImageDescriptor(
        position=position,
        mime_type=mime_type,
        width=16,
        height=16,
        byte_count=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    return GeneratedAlbumImage(
        image_id=image_id,
        created_at=created_at,
        action=action,
        descriptor=descriptor,
        content=content,
    )


class GeneratedAlbumStorageUnitTests(unittest.TestCase):
    def test_safe_role_slug_and_path_traversal_guard(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-album-slug-") as temporary:
            data_root = Path(temporary)
            album_base = generated_album_root(data_root)

            # Simple lowercase role IDs remain clean and readable.
            self.assertEqual(safe_role_slug("kazusa"), "kazusa")
            self.assertEqual(safe_role_slug("role-a"), "role-a")
            self.assertEqual(safe_role_slug("role_1.main"), "role_1.main")

            # Omitted or blank role_id keeps backward-compatible root resolution.
            self.assertEqual(role_album_dir(data_root, None), album_base)
            self.assertEqual(role_album_dir(data_root, ""), album_base)
            self.assertEqual(role_album_dir(data_root, "   "), album_base)

            # Hostile traversal, Windows device names, null bytes, and Unicode stay confined.
            hostile_ids = (
                "../../etc/passwd",
                "..\\..\\Windows\\System32",
                "/etc/passwd",
                "C:\\Windows\\System32",
                ".",
                "..",
                "...",
                "a..b",
                "CON",
                "con.txt",
                "PRN",
                "AUX",
                "NUL",
                "COM1",
                "LPT9",
                "role\x00evil",
                "role-a.",
                "千早爱音",
                "a" * 200,
            )
            slugs = set()
            for role_id in hostile_ids:
                slug = safe_role_slug(role_id)
                self.assertNotIn("/", slug)
                self.assertNotIn("\\", slug)
                self.assertNotIn("..", slug)
                self.assertNotIn("\x00", slug)
                self.assertFalse(slug.endswith("."))
                self.assertLessEqual(len(slug), 64)
                resolved = role_album_dir(data_root, role_id)
                self.assertEqual(resolved.parent, album_base)
                self.assertTrue(resolved.is_relative_to(album_base))
                self.assertNotEqual(resolved, album_base)
                slugs.add(slug)
            self.assertEqual(len(slugs), len(hostile_ids))

            # Case variants and punctuation variants never collide on case-insensitive volumes.
            self.assertNotEqual(safe_role_slug("RoleA"), safe_role_slug("rolea"))
            self.assertNotEqual(safe_role_slug("role-a"), safe_role_slug("role/a"))

    def test_readable_filenames_and_distinct_short_ids(self):
        png = _image_bytes("#336699", fmt="PNG")
        jpg = _image_bytes("#993366", fmt="JPEG")
        webp = _image_bytes("#339966", fmt="WEBP")

        name_gen = album_filename(
            "generated-image:inv-1:0", "2026-10-12T04:05:06+00:00",
            "image.generate", png, mime_type="image/png",
        )
        name_pos1 = album_filename(
            "generated-image:inv-1:1", "2026-10-12T04:05:06+00:00",
            "image.generate", png, mime_type="image/png",
        )
        name_edit = album_filename(
            "generated-image:inv-2:0", "2026-10-12T12:30:45+08:00",
            "image.edit", jpg, mime_type="image/jpeg",
        )
        name_mask = album_filename(
            "generated-image:inv-3:0", "2026-10-12T04:05:06Z",
            "image.mask_edit", webp, mime_type="image/webp",
        )

        self.assertRegex(name_gen, r"^20261012-040506-generate-[0-9a-f]{12}\.png$")
        self.assertRegex(name_pos1, r"^20261012-040506-generate-[0-9a-f]{12}\.png$")
        self.assertNotEqual(name_gen, name_pos1)
        self.assertEqual(name_edit, f"20261012-043045-edit-{album_short_id('generated-image:inv-2:0')}.jpg")
        self.assertRegex(name_mask, r"^20261012-040506-mask_edit-[0-9a-f]{12}\.webp$")

    def test_editing_or_deleting_an_album_copy_preserves_the_original(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-album-copy-") as temporary:
            data_root = Path(temporary)
            png = _image_bytes("#2b6cb0", fmt="PNG")
            store_image(data_root, png, mime_type="image/png")
            source_file = image_root(data_root) / (hashlib.sha256(png).hexdigest() + ".png")
            self.assertTrue(source_file.is_file())
            before_bytes = source_file.read_bytes()

            entry = _album_entry("generated-image:inv-link:0", png)
            result = sync_generated_album_folder(data_root, [entry], role_id="kazusa")

            album_dir = Path(result["path"])
            self.assertEqual(album_dir, generated_album_root(data_root) / "kazusa")
            self.assertEqual(result["synced_count"], 1)
            self.assertEqual(result["total_count"], 1)

            files = list(album_dir.iterdir())
            self.assertEqual(len(files), 1)
            materialized = files[0]
            self.assertRegex(materialized.name, r"^20261012-040506-generate-[0-9a-f]{12}\.png$")
            self.assertEqual(materialized.read_bytes(), png)
            self.assertFalse(os.path.samefile(source_file, materialized))

            edited = _image_bytes("#bb1122")
            materialized.write_bytes(edited)
            self.assertEqual(source_file.read_bytes(), before_bytes)
            same = sync_generated_album_folder(data_root, [entry], role_id="kazusa")
            self.assertEqual(same["synced_count"], 0)
            self.assertEqual(materialized.read_bytes(), edited)
            materialized.unlink()
            self.assertTrue(source_file.is_file())
            self.assertEqual(source_file.read_bytes(), before_bytes)

            # Re-running sync restores only the missing file cleanly.
            resynced = sync_generated_album_folder(data_root, [entry], role_id="kazusa")
            self.assertEqual(resynced["synced_count"], 1)
            self.assertEqual(resynced["total_count"], 1)
            self.assertEqual(next(album_dir.iterdir()).read_bytes(), png)

    def test_blob_and_file_backed_payloads_use_atomic_write(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-album-write-") as temporary:
            data_root = Path(temporary)
            blob_jpg = _image_bytes("#c53030", fmt="JPEG")
            stored_png = _image_bytes("#2f855a", fmt="PNG")
            store_image(data_root, stored_png, mime_type="image/png")
            source_png = image_root(data_root) / (hashlib.sha256(stored_png).hexdigest() + ".png")

            blob_entry = _album_entry(
                "generated-image:inv-blob:0", blob_jpg,
                action="image.edit", mime_type="image/jpeg",
            )
            stored_entry = _album_entry(
                "generated-image:inv-stored:0", stored_png,
                action="image.generate", mime_type="image/png",
            )

            result = sync_generated_album_folder(data_root, [blob_entry, stored_entry], role_id="role-a")

            album_dir = Path(result["path"])
            self.assertEqual(result["synced_count"], 2)
            self.assertEqual(result["total_count"], 2)

            files = sorted(album_dir.iterdir(), key=lambda p: p.name)
            self.assertEqual(len(files), 2)
            self.assertFalse(any(p.name.startswith(".album-") for p in files))
            by_ext = {p.suffix: p.read_bytes() for p in files}
            self.assertEqual(by_ext[".jpg"], blob_jpg)
            self.assertEqual(by_ext[".png"], stored_png)
            # Immutable store was neither mutated nor polluted with the BLOB image.
            self.assertEqual(list(image_root(data_root).iterdir()), [source_png])
            self.assertEqual(source_png.read_bytes(), stored_png)

    def test_incremental_idempotency_skips_already_materialized_short_ids(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-album-idem-") as temporary:
            data_root = Path(temporary)
            first_png = _image_bytes("#112233", fmt="PNG")
            second_jpg = _image_bytes("#445566", fmt="JPEG")
            third_png = _image_bytes("#778899", fmt="PNG")
            store_image(data_root, first_png, mime_type="image/png")

            e1 = _album_entry("generated-image:inv-1:0", first_png)
            e2 = _album_entry("generated-image:inv-2:0", second_jpg, action="image.edit", mime_type="image/jpeg")
            e3 = _album_entry("generated-image:inv-3:0", third_png, created_at="2026-10-13T08:00:00+00:00")

            first = sync_generated_album_folder(data_root, [e1, e2], role_id="role-a")
            self.assertEqual((first["synced_count"], first["total_count"]), (2, 2))

            # Second sync skips existing copies.
            with patch(
                "companion_v2.storage.generated_album.tempfile.mkstemp",
                side_effect=AssertionError("mkstemp should not be called on already-synced items"),
            ):
                second = sync_generated_album_folder(data_root, [e1, e2], role_id="role-a")
            self.assertEqual((second["synced_count"], second["total_count"]), (0, 2))

            # Adding one new entry syncs only the new file.
            third = sync_generated_album_folder(data_root, [e1, e2, e3], role_id="role-a")
            self.assertEqual((third["synced_count"], third["total_count"]), (1, 3))
            self.assertEqual(len(list(Path(third["path"]).iterdir())), 3)

    def test_symlink_role_folder_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-album-symlink-") as temporary:
            data_root = Path(temporary)
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(ValueError):
                    role_album_dir(data_root, "role-a")
                with self.assertRaises(ValueError):
                    materialize_album_image(
                        data_root, filename="test.png", content=_image_bytes("#000000"),
                    )


class ReferenceManagementOpenAlbumFolderTests(unittest.TestCase):
    def _build_manager(self, data_root: Path, pages: dict, *, hidden_ids: set[str] | None = None, role_id: str | None = "role-a"):
        from companion_v2.references.management import ReferenceMaterialManagement

        class FakeGeneratedImages:
            def __init__(self, page_map):
                self.page_map = page_map
                self.calls = []

            def album_page(self, context, at, *, limit=24, cursor=None):
                self.calls.append((cursor, limit))
                return self.page_map[cursor]

        generated_images = FakeGeneratedImages(pages)
        runtime = SimpleNamespace(data_root=data_root, installation_id="inst-1")
        application = SimpleNamespace(
            runtime=runtime,
            messages=SimpleNamespace(generated_images=generated_images),
        )
        manager = ReferenceMaterialManagement(application)
        manager._admit = lambda operator, context, at: "owner-1"
        manager._hidden_album_ids = lambda context: set(hidden_ids or ())
        role_binding = (
            SimpleNamespace(role=SubjectRef("inst-1", SubjectKind.BOT, role_id))
            if role_id is not None else None
        )
        context = SimpleNamespace(
            key="ctx-1",
            scope=ScopeRef("inst-1", "onebot", ScopeKind.PRIVATE, "private-a"),
            role_binding=role_binding,
        )
        return manager, context, generated_images

    def test_open_album_folder_materializes_paginated_entries_skips_hidden_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-mgmt-album-") as temporary:
            data_root = Path(temporary)
            marker_png = _image_bytes("#224488", fmt="PNG")
            blob_jpg = _image_bytes("#884422", fmt="JPEG")
            hidden_png = _image_bytes("#888888", fmt="PNG")
            store_image(data_root, marker_png, mime_type="image/png")
            source_marker = image_root(data_root) / (hashlib.sha256(marker_png).hexdigest() + ".png")

            item_marker = _album_entry("generated-image:inv-m:0", marker_png, created_at="2026-10-12T04:05:06+00:00")
            item_hidden = _album_entry("generated-image:inv-h:0", hidden_png, created_at="2026-10-12T04:04:00+00:00")
            item_blob = _album_entry(
                "generated-image:inv-b:0", blob_jpg,
                created_at="2026-10-12T04:03:00+00:00", action="image.edit", mime_type="image/jpeg",
            )
            pages = {
                None: {"items": (item_marker, item_hidden), "next_cursor": "cursor-2"},
                "cursor-2": {"items": (item_blob,), "next_cursor": None},
            }
            manager, context, generated_images = self._build_manager(
                data_root, pages, hidden_ids={"generated-image:inv-h:0"}, role_id="kazusa",
            )

            opened_targets: list[str] = []
            launcher_attr = "os.startfile" if os.name == "nt" else "subprocess.Popen"

            def fake_launch(arg, *_args, **_kwargs):
                opened_targets.append(arg if isinstance(arg, str) else arg[-1])

            with patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
                first = manager.open_album_folder(SimpleNamespace(), context, at="2026-10-12T05:00:00+00:00")
                second = manager.open_album_folder(SimpleNamespace(), context, at="2026-10-12T05:01:00+00:00")

            expected_dir = role_album_dir(data_root, "kazusa", owner="owner-1")
            self.assertEqual(first["path"], str(expected_dir))
            self.assertTrue(first["opened"])
            self.assertEqual(first["synced_count"], 2)
            self.assertEqual(first["total_count"], 2)
            self.assertEqual(second, {
                "path": str(expected_dir),
                "opened": True,
                "synced_count": 0,
                "total_count": 2,
            })
            self.assertEqual(opened_targets, [str(expected_dir), str(expected_dir)])
            self.assertEqual(len(generated_images.calls), 4)

            materialized = sorted(expected_dir.iterdir(), key=lambda p: p.name)
            self.assertEqual(len(materialized), 2)
            self.assertTrue(any(path.suffix == ".png" and path.read_bytes() == marker_png for path in materialized))
            self.assertFalse(any(os.path.samefile(source_marker, path) for path in materialized))
            self.assertTrue(any(path.suffix == ".jpg" and path.read_bytes() == blob_jpg for path in materialized))
            self.assertFalse(any(album_short_id("generated-image:inv-h:0") in path.name for path in materialized))

            manager._admit = lambda operator, context, at: "owner-2"
            generated_images.page_map = {None: {"items": (), "next_cursor": None}}
            with patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
                separate = manager.open_album_folder(SimpleNamespace(), context, at="2026-10-12T05:00:00+00:00")
            self.assertNotEqual(separate["path"], first["path"])
            self.assertEqual(list(Path(separate["path"]).iterdir()), [])

    def test_open_album_folder_guards_hostile_role_mismatch_and_root_compat(self):
        with tempfile.TemporaryDirectory(prefix="companion-v2-mgmt-guard-") as temporary:
            data_root = Path(temporary)
            png = _image_bytes("#123456", fmt="PNG")
            item = _album_entry("generated-image:inv-hostile:0", png)
            pages = {None: {"items": (item,), "next_cursor": None}}

            # Hostile bound role ID stays strictly confined inside media/generated-album/.
            manager, context, _ = self._build_manager(data_root, pages, role_id="../../etc/passwd")
            opened: list[str] = []
            launcher_attr = "os.startfile" if os.name == "nt" else "subprocess.Popen"

            def fake_launch(arg, *_args, **_kwargs):
                opened.append(arg if isinstance(arg, str) else arg[-1])

            with patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
                result = manager.open_album_folder(SimpleNamespace(), context, at="2026-10-12T05:00:00+00:00")
                root_fallback = manager.open_album_folder(
                    SimpleNamespace(), context, at="2026-10-12T05:00:00+00:00", role_id="",
                )

            album_base = generated_album_root(data_root)
            target_dir = Path(result["path"])
            self.assertEqual(target_dir.parent, album_base)
            self.assertTrue(target_dir.is_relative_to(album_base))
            self.assertEqual(result["synced_count"], 1)
            self.assertEqual(result["total_count"], 1)

            # Empty role_id opens generated-album root cleanly without polluting it.
            self.assertEqual(root_fallback["path"], str(album_base))
            self.assertEqual(root_fallback["synced_count"], 0)
            self.assertEqual(root_fallback["total_count"], 0)

            # Cross-role mismatch is rejected.
            with self.assertRaises(TaskError) as error:
                manager.open_album_folder(
                    SimpleNamespace(), context, at="2026-10-12T05:00:00+00:00", role_id="other-role",
                )
            self.assertEqual(error.exception.code, "reference_target_changed")

            # Missing role_binding (backward-compat omitted role) opens generated-album root.
            no_role_manager, no_role_ctx, _ = self._build_manager(data_root, pages, role_id=None)
            with patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
                compat = no_role_manager.open_album_folder(SimpleNamespace(), no_role_ctx, at="2026-10-12T05:00:00+00:00")
            self.assertEqual(compat["path"], str(album_base))
            self.assertEqual(compat["total_count"], 0)

            # OS launch failure preserves generated_album_open_failed code.
            with patch(
                f"companion_v2.references.management.{launcher_attr}",
                side_effect=OSError("no display"),
            ):
                with self.assertRaises(TaskError) as open_error:
                    manager.open_album_folder(SimpleNamespace(), context, at="2026-10-12T05:00:00+00:00")
            self.assertEqual(open_error.exception.code, "generated_album_open_failed")


class HostApiOpenAlbumFolderRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="companion-v2-album-route-")
        cls.repository_root = Path(__file__).resolve().parents[1]
        cls.environment = os.environ.copy()
        cls.environment["ASTRBOT_ROOT"] = cls.temporary.name
        backend_app = Path(sys.executable).resolve().parents[1] / "app"
        paths = [str(cls.repository_root), str(backend_app)]
        paths.extend(value for value in cls.environment.get("PYTHONPATH", "").split(os.pathsep) if value)
        cls.environment["PYTHONPATH"] = os.pathsep.join(paths)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_post_open_folder_route_returns_materialized_role_album_dir_not_runtime_root(self):
        source = r'''
import hashlib, io, json, os, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from PIL import Image
from astrbot.api.web import PluginMultiDict
from companion_v2.conversation.media.generated import GeneratedAlbumImage
from companion_v2.conversation.media.models import ImageDescriptor
from companion_v2.storage.generated_album import role_album_dir
from companion_v2.storage.image_payloads import image_root, store_image
from tests.multi_host_support import MultiFixture


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpg(color):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="JPEG")
    return buf.getvalue()


class OpenFolderRouteTest(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_open_folder_route_materializes_and_opens_role_album_folder(self):
        app = await self.start_host()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        paths = [route for route in handlers if route.endswith("/image-materials")]
        first = next(route for route in paths if "/targets/first/roles/" in route)
        other = next(route for route in paths if "/targets/first/roles/" in route and route != first)
        active_role_id = self.controller.authority.context("first", "role-a").role_binding.role.subject_id
        other_role_id = self.controller.authority.context("first", "role-b").role_binding.role.subject_id

        async def call(path, body=None, username="materials-admin"):
            async def read():
                return json.dumps({} if body is None else body, ensure_ascii=False).encode("utf-8")
            with patch(
                "astrbot_host.image_material_api.request",
                NS(username=username, query=PluginMultiDict([]), body=read),
            ):
                return await handlers[path]()

        data_root = Path(app.runtime.data_root).resolve()
        marker_png = _png("#2b6cb0")
        blob_jpg = _jpg("#b83280")
        store_image(data_root, marker_png, mime_type="image/png")
        source_file = image_root(data_root) / (hashlib.sha256(marker_png).hexdigest() + ".png")

        role_a_items = (
            GeneratedAlbumImage(
                "generated-image:route-inv-1:0",
                "2026-10-12T04:05:06+00:00",
                "image.generate",
                ImageDescriptor(0, "image/png", 16, 16, len(marker_png), hashlib.sha256(marker_png).hexdigest()),
                marker_png,
            ),
            GeneratedAlbumImage(
                "generated-image:route-inv-2:0",
                "2026-10-12T04:06:00+00:00",
                "image.edit",
                ImageDescriptor(0, "image/jpeg", 16, 16, len(blob_jpg), hashlib.sha256(blob_jpg).hexdigest()),
                blob_jpg,
            ),
        )

        def fake_album_page(context, at, *, limit=24, cursor=None):
            if context.role_binding.role.subject_id == active_role_id:
                return {"items": role_a_items, "next_cursor": None}
            return {"items": (), "next_cursor": None}

        opened = []
        launcher_attr = "os.startfile" if os.name == "nt" else "subprocess.Popen"

        def fake_launch(arg, *_args, **_kwargs):
            opened.append(arg if isinstance(arg, str) else arg[-1])

        try:
            with patch.object(app.messages.generated_images, "album_page", side_effect=fake_album_page), \
                 patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
                first_res = await call(first + "/album/open-folder", {})
                self.assertEqual(first_res["status"], "ok", first_res)
                payload = first_res["data"]
                active_context = self.controller.authority.context("first", "role-a")
                expected_dir = role_album_dir(data_root, active_role_id, owner=app.references.owner(active_context))
                self.assertEqual(Path(payload["path"]), expected_dir)
                self.assertNotEqual(Path(payload["path"]), data_root)
                self.assertTrue(payload["opened"])
                self.assertEqual(payload["synced_count"], 2)
                self.assertEqual(payload["total_count"], 2)
                self.assertEqual(opened[-1], str(expected_dir))

                files = sorted(expected_dir.iterdir(), key=lambda p: p.name)
                self.assertEqual(len(files), 2)
                self.assertTrue(any(p.suffix == ".png" and p.read_bytes() == marker_png for p in files))
                self.assertFalse(any(os.path.samefile(source_file, p) for p in files))
                self.assertTrue(any(p.suffix == ".jpg" and p.read_bytes() == blob_jpg for p in files))
                self.assertEqual(source_file.read_bytes(), marker_png)

                # Repeat click is incremental and idempotent.
                second_res = await call(first + "/album/open-folder", {})
                self.assertEqual(second_res["status"], "ok", second_res)
                self.assertEqual(second_res["data"]["synced_count"], 0)
                self.assertEqual(second_res["data"]["total_count"], 2)

                # Mismatched role_id in body is rejected.
                mismatch = await call(first + "/album/open-folder", {"role_id": other_role_id})
                self.assertEqual(mismatch["status"], "error")
                self.assertEqual(mismatch["data"]["code"], "reference_target_changed")

                # Unexpected body fields are still rejected.
                invalid = await call(first + "/album/open-folder", {"unexpected": True})
                self.assertEqual(invalid["status"], "error")
                self.assertEqual(invalid["data"]["code"], "image_material_request_invalid")

                # Other role opens its own isolated album directory.
                other_res = await call(other + "/album/open-folder", {})
                self.assertEqual(other_res["status"], "ok", other_res)
                other_dir = Path(other_res["data"]["path"])
                self.assertEqual(other_dir.parent, data_root / "media" / "generated-album")
                self.assertNotEqual(other_dir, expected_dir)
                self.assertEqual(other_res["data"]["total_count"], 0)
                self.assertEqual(list(other_dir.iterdir()), [])
        finally:
            self.controller.unregister_routes()
            await self.controller.terminate()


unittest.main()
'''
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", source],
            cwd=self.repository_root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_real_native_generated_album_sqlite_marker_and_legacy_blob_materialization(self):
        source = r'''
import hashlib, json, os, sqlite3, unittest
from pathlib import Path
from unittest.mock import patch
from astrbot_host.management_identity import dashboard_operator
from companion_v2.references.management import ReferenceMaterialManagement
from companion_v2.storage.generated_album import role_album_dir
from companion_v2.storage.image_payloads import MARKER_PREFIX, image_root, is_marker, load_image
from tests.test_conversation_images import image_bytes
from tests.test_core_reply_images import CoreReplyImagesTests


class RealNativeAlbumFolderTest(CoreReplyImagesTests):
    async def test_marker_and_legacy_blob_materialize_from_real_album_projection(self):
        event, request = self.prepare("album-folder-e2e")
        gen = self.result(await self.call(event, request, "image.generate", {"prompt": "红色"}, "gen-1"), "red")
        artifact = json.loads(gen["payload"])["native"]["artifacts"][0]["artifact_id"]
        self.result(await self.call(event, request, "image.edit", {"prompt": "蓝色", "artifact_id": artifact}, "edit-1"), "blue")

        data_root = Path(self.runtime.data_root).resolve()
        red_bytes = image_bytes("red")
        blue_bytes = image_bytes("blue")
        red_sha = hashlib.sha256(red_bytes).hexdigest()
        blue_sha = hashlib.sha256(blue_bytes).hexdigest()
        red_source = image_root(data_root) / (red_sha + ".png")
        blue_source = image_root(data_root) / (blue_sha + ".png")
        self.assertTrue(red_source.is_file())
        self.assertTrue(blue_source.is_file())

        # Convert only the blue image payload row back into a legacy SQLite BLOB and remove its hash file,
        # so the real album projection serves 1 marker-backed file + 1 raw SQLite BLOB in one call.
        rows = self.runtime.read_all(
            "SELECT parent_id, parent_lineage, parent_revision, position, content FROM conversation_image_payloads",
            (),
        )
        converted = 0
        with self.runtime.transaction() as connection:
            for row in rows:
                if is_marker(row["content"]) and load_image(data_root, row["content"]) == blue_bytes:
                    connection.execute(
                        "UPDATE conversation_image_payloads SET content=? "
                        "WHERE parent_id=? AND parent_lineage=? AND parent_revision=? AND position=?",
                        (blue_bytes, row["parent_id"], row["parent_lineage"], row["parent_revision"], row["position"]),
                    )
                    converted += 1
        self.assertEqual(converted, 1)
        blue_source.unlink()
        self.assertFalse(blue_source.exists())

        native_db = self.image_directory / "native_image.sqlite3"
        native_db_before = native_db.read_bytes() if native_db.is_file() else None
        red_before = red_source.read_bytes()

        manager = ReferenceMaterialManagement(self.app)
        operator = dashboard_operator(self.runtime, "album-e2e-admin", at=self.clock.now())

        opened = []
        launcher_attr = "os.startfile" if os.name == "nt" else "subprocess.Popen"

        def fake_launch(arg, *_args, **_kwargs):
            opened.append(arg if isinstance(arg, str) else arg[-1])

        with patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
            first = manager.open_album_folder(operator, self.context, at=self.clock.now())
            second = manager.open_album_folder(operator, self.context, at=self.clock.now())

        expected_dir = role_album_dir(data_root, self.context.role_binding.role.subject_id,
                                      owner=self.app.references.owner(self.context))
        self.assertEqual(Path(first["path"]), expected_dir)
        self.assertEqual(first["synced_count"], 2)
        self.assertEqual(first["total_count"], 2)
        self.assertEqual(second["synced_count"], 0)
        self.assertEqual(second["total_count"], 2)
        self.assertEqual(opened, [str(expected_dir), str(expected_dir)])

        materialized = sorted(expected_dir.iterdir(), key=lambda p: p.name)
        self.assertEqual(len(materialized), 2)
        by_bytes = {p.read_bytes(): p for p in materialized}
        self.assertIn(red_bytes, by_bytes)
        self.assertIn(blue_bytes, by_bytes)
        self.assertFalse(os.path.samefile(red_source, by_bytes[red_bytes]))
        self.assertIn("-generate-", by_bytes[red_bytes].name)
        self.assertIn("-edit-", by_bytes[blue_bytes].name)
        # Immutable hash store and native_image.sqlite3 were not mutated.
        self.assertEqual(red_source.read_bytes(), red_before)
        self.assertFalse(blue_source.exists())
        if native_db_before is not None:
            self.assertEqual(native_db.read_bytes(), native_db_before)


unittest.main()
'''
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", source],
            cwd=self.repository_root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
