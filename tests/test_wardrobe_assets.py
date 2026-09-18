# -*- coding: utf-8 -*-
"""素材层（wardrobe_assets）单元测试：导入、去重、索引往返、抽帧草稿。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.wardrobe_assets import (
    ASSET_KIND_IMAGE,
    ASSET_KIND_VIDEO_FRAME,
    ASSET_ORIGIN_BLOGGER,
    ASSET_ORIGIN_TAOBAO,
    ASSET_STATUS_IMPORTED,
    ASSET_STATUS_REJECTED,
    asset_id_for,
    image_dimensions,
    import_asset,
    import_directory,
    list_pending_drafts,
    load_asset_draft,
    load_asset_index,
    normalize_asset,
    normalize_asset_index,
    normalize_asset_kind,
    normalize_asset_origin,
    mark_asset_status,
    save_asset_index,
    scan_source_dir,
    sha256_file,
    write_asset_draft,
)

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


def _write_png(path: Path, payload: bytes = PNG_1x1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class AssetNormalizeTests(unittest.TestCase):
    def test_kind_falls_back_to_suffix(self) -> None:
        self.assertEqual(ASSET_KIND_IMAGE, normalize_asset_kind("", suffix=".JPG"))
        self.assertEqual(ASSET_KIND_VIDEO_FRAME, normalize_asset_kind("", suffix=".mp4"))
        self.assertEqual(ASSET_KIND_IMAGE, normalize_asset_kind("图片"))
        self.assertEqual(ASSET_KIND_IMAGE, normalize_asset_kind(None))

    def test_origin_accepts_chinese_aliases(self) -> None:
        self.assertEqual(ASSET_ORIGIN_BLOGGER, normalize_asset_origin("博主"))
        self.assertEqual(ASSET_ORIGIN_TAOBAO, normalize_asset_origin("淘宝"))
        self.assertEqual(ASSET_ORIGIN_BLOGGER, normalize_asset_origin(ASSET_ORIGIN_BLOGGER))

    def test_row_without_digest_is_dropped(self) -> None:
        self.assertIsNone(normalize_asset({"path": "a/b.jpg"}))
        self.assertIsNotNone(normalize_asset({"sha256": "AB" * 32}))

    def test_index_accepts_wrapper_and_bare_forms(self) -> None:
        # 回归：索引文件是 {"version":…, "assets":[…]}，直接喂回去也要能解析，
        # 否则"读回来是空的"会让所有资产状态静默丢失。
        record = normalize_asset({"sha256": "cd" * 32, "kind": "image", "path": "cd/x.jpg"})
        assert record is not None
        wrapped = {"version": 1, "updated_at": 1.0, "assets": [record]}
        self.assertEqual(1, len(normalize_asset_index(wrapped)))
        self.assertEqual(1, len(normalize_asset_index([record])))
        self.assertEqual(1, len(normalize_asset_index({record["id"]: record})))

    def test_index_dedupes_by_digest(self) -> None:
        row = {"sha256": "ef" * 32, "path": "ef/x.jpg", "id": "asset_a"}
        other = {"sha256": "ef" * 32, "path": "ef/y.jpg", "id": "asset_b"}
        self.assertEqual(1, len(normalize_asset_index([row, other])))


class AssetImportTests(unittest.TestCase):
    def test_import_copies_dedupes_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = _write_png(Path(root) / "src" / "a.png")
            data_dir = Path(root) / "data"
            record, created = import_asset(data_dir, source)
            self.assertTrue(created)
            self.assertEqual(ASSET_STATUS_IMPORTED, record["status"])
            self.assertEqual(ASSET_KIND_IMAGE, record["kind"])
            self.assertEqual(1, record["width"])
            self.assertEqual(1, record["height"])
            stored = data_dir / "wardrobe_assets" / record["path"]
            self.assertTrue(stored.is_file())
            self.assertEqual(sha256_file(source), record["sha256"])
            # 幂等：同一份内容再导一次不会新增
            same, created_again = import_asset(data_dir, source)
            self.assertFalse(created_again)
            self.assertEqual(record["id"], same["id"])

    def test_same_bytes_under_two_names_share_one_asset(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = _write_png(Path(root) / "src" / "one.png")
            second = _write_png(Path(root) / "src" / "copy.png")
            data_dir = Path(root) / "data"
            a, _ = import_asset(data_dir, first)
            b, created = import_asset(data_dir, second)
            self.assertFalse(created)
            self.assertEqual(a["id"], b["id"])

    def test_index_round_trip_keeps_status(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = _write_png(Path(root) / "src" / "a.png")
            data_dir = Path(root) / "data"
            record, _ = import_asset(data_dir, source)
            record["status"] = ASSET_STATUS_REJECTED
            save_asset_index(data_dir, {record["id"]: record})
            loaded = load_asset_index(data_dir)
            self.assertEqual(1, len(loaded))
            self.assertEqual(ASSET_STATUS_REJECTED, loaded[record["id"]]["status"])

    def test_import_directory_counts_and_scans(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_png(Path(root) / "src" / "a.png")
            _write_png(Path(root) / "src" / "b.png", PNG_1x1 + b"\x00")
            (Path(root) / "src" / "note.txt").write_text("忽略", encoding="utf-8")
            stats = import_directory(Path(root) / "data", Path(root) / "src", origin=ASSET_ORIGIN_BLOGGER)
            self.assertEqual(2, stats["scanned"])
            self.assertEqual(2, stats["imported"])
            self.assertEqual(0, stats["failed"])

    def test_missing_source_raises(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                import_asset(Path(root) / "data", Path(root) / "nope.png")


class AssetScanAndDraftTests(unittest.TestCase):
    def test_scan_filters_suffix_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write_png(Path(root) / "b.png")
            _write_png(Path(root) / "a.png")
            (Path(root) / "c.txt").write_text("x", encoding="utf-8")
            found = scan_source_dir(root)
            self.assertEqual(["a.png", "b.png"], [p.name for p in found])
            self.assertEqual(1, len(scan_source_dir(root, limit=1)))

    def test_draft_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            write_asset_draft(data_dir, "asset_x", {"kind": "outfit", "name": "通勤正装"})
            draft = load_asset_draft(data_dir, "asset_x")
            self.assertEqual("outfit", draft["kind"])
            self.assertIsNone(load_asset_draft(data_dir, "asset_missing"))

    def test_image_dimensions_reads_png_header(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = _write_png(Path(root) / "a.png")
            self.assertEqual((1, 1), image_dimensions(path))
            (Path(root) / "broken.png").write_bytes(b"not an image")
            self.assertIsNone(image_dimensions(Path(root) / "broken.png"))

    def test_asset_id_is_digest_prefix(self) -> None:
        digest = "ab" * 32
        self.assertEqual("asset_" + "ab" * 8, asset_id_for(digest))

    def test_index_json_is_json_serialisable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = _write_png(Path(root) / "src" / "a.png")
            data_dir = Path(root) / "data"
            record, _ = import_asset(data_dir, source)
            save_asset_index(data_dir, {record["id"]: record})
            payload = json.loads((data_dir / "wardrobe_assets" / "index.json").read_text(encoding="utf-8"))
            self.assertIn("assets", payload)
            self.assertEqual(1, len(payload["assets"]))


if __name__ == "__main__":
    unittest.main()


class AssetDraftQueueTests(unittest.TestCase):
    """草稿队列：待确认列表与状态标记。"""

    def test_pending_lists_imported_assets_with_draft(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = _write_png(Path(root) / "src" / "a.png")
            data_dir = Path(root) / "data"
            record, _ = import_asset(data_dir, source, origin=ASSET_ORIGIN_BLOGGER)
            write_asset_draft(data_dir, record["id"], {"kind": "outfit", "name": "白衬衫黑纱裙"})
            rows = list_pending_drafts(data_dir)
            self.assertEqual(1, len(rows))
            self.assertTrue(rows[0]["has_draft"])
            self.assertEqual("outfit", rows[0]["kind"])
            self.assertEqual("白衬衫黑纱裙", rows[0]["name"])
            self.assertTrue(Path(rows[0]["path"]).is_file())

    def test_pending_without_draft_is_still_listed(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = _write_png(Path(root) / "src" / "a.png")
            data_dir = Path(root) / "data"
            import_asset(data_dir, source)
            rows = list_pending_drafts(data_dir)
            self.assertEqual(1, len(rows))
            self.assertFalse(rows[0]["has_draft"])

    def test_mark_status_removes_from_queue(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = _write_png(Path(root) / "src" / "a.png")
            data_dir = Path(root) / "data"
            record, _ = import_asset(data_dir, source)
            updated = mark_asset_status(data_dir, record["id"], ASSET_STATUS_REJECTED)
            assert updated is not None
            self.assertEqual(ASSET_STATUS_REJECTED, updated["status"])
            self.assertEqual([], list_pending_drafts(data_dir))
            self.assertIsNone(mark_asset_status(data_dir, "asset_missing", ASSET_STATUS_REJECTED))


if __name__ == "__main__":
    unittest.main()
