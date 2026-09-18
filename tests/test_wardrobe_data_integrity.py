# -*- coding: utf-8 -*-
"""数据与素材层的评审回归（每条都对应一次真实缺陷）。"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from astrbot_plugin_private_companion import wardrobe_assets as A
from astrbot_plugin_private_companion.wardrobe import (
    apply_wardrobe_draft,
    find_wardrobe_item_by_exact_name,
    normalize_wardrobe_items,
    normalize_wardrobe_tags,
    parse_wardrobe_image_reply,
)


class OutfitMergeTests(unittest.TestCase):
    def test_same_name_outfit_keeps_asset_union_and_new_ownership(self) -> None:
        # 回归：同名整套再次落库时，新增的 asset_ids 参数没有进合并字典，
        # 第二张图的引用静默丢失、归属永不更新（参考整套被当自有穿）。
        _, outfits, _ = apply_wardrobe_draft(
            [], [], {"kind": "outfit", "name": "通勤套装", "description": "浅灰西装"},
            asset_id="asset_1",
        )
        _, outfits, outcome = apply_wardrobe_draft(
            [], outfits, {"kind": "reference", "name": "通勤套装", "description": "浅灰西装"},
            asset_id="asset_2",
        )
        self.assertTrue(outcome["replaced"])
        self.assertEqual(["asset_1", "asset_2"], outfits[0]["asset_ids"])
        self.assertEqual("reference", outfits[0]["ownership"])


class DraftExistenceTests(unittest.TestCase):
    def test_numeric_name_draft_does_not_hit_another_item(self) -> None:
        # 回归：落库判断撞名时误用了「用户点名」那套宽松查找（id → 序号 → 名字 → 子串），
        # 一件叫「3」的新衣物会被判成已存在：replaced 报 true、面板取回别人那一行。
        items = normalize_wardrobe_items([{"name": "白衬衫"}, {"name": "牛仔裤"}, {"name": "球鞋"}])
        items, _, outcome = apply_wardrobe_draft(
            items, [], {"kind": "item", "name": "3", "description": "全新外套"}, asset_id="a"
        )
        self.assertFalse(outcome["replaced"])
        self.assertIn("3", [row["name"] for row in items])
        self.assertEqual("3", find_wardrobe_item_by_exact_name(items, "3")["name"])

    def test_same_name_twice_is_a_real_replace(self) -> None:
        items = normalize_wardrobe_items([{"name": "白衬衫", "description": "旧"}])
        items, _, outcome = apply_wardrobe_draft(
            items, [], {"kind": "item", "name": "白衬衫", "description": "新"}, asset_id="a"
        )
        self.assertTrue(outcome["replaced"])
        self.assertEqual(1, len(items))


class AssetIndexRobustnessTests(unittest.TestCase):
    def test_bom_prefixed_index_is_still_readable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data_dir = pathlib.Path(raw)
            A.save_asset_index(str(data_dir), {"a": {"id": "a", "sha256": "a" * 64, "path": "aa/x.png", "status": "understood"}})
            path = A.asset_index_path(str(data_dir))
            path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
            self.assertEqual(["a"], sorted(A.load_asset_index(str(data_dir))))

    def test_unreadable_index_is_quarantined_not_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data_dir = pathlib.Path(raw)
            A.save_asset_index(str(data_dir), {"a": {"id": "a", "sha256": "a" * 64, "path": "aa/x.png"}})
            path = A.asset_index_path(str(data_dir))
            path.write_bytes(path.read_bytes()[:20])  # 写了一半
            self.assertEqual({}, A.load_asset_index(str(data_dir)))
            backups = list(path.parent.glob("index.json.corrupt-*"))
            self.assertEqual(1, len(backups), "读不出来时必须留档，而不是等下一次 save 抹掉")

    def test_bad_numeric_fields_do_not_break_the_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data_dir = pathlib.Path(raw)
            A.save_asset_index(str(data_dir), {})
            path = A.asset_index_path(str(data_dir))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["assets"] = [{"id": "c", "sha256": "c" * 64, "path": "cc/z.png",
                                 "bytes": "1.2MB", "width": "x", "height": None}]
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            record = A.load_asset_index(str(data_dir))["c"]
            self.assertEqual(0, record["bytes"])
            self.assertEqual(0, record["width"])

    def test_save_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data_dir = pathlib.Path(raw)
            A.save_asset_index(str(data_dir), {"a": {"id": "a", "sha256": "a" * 64, "path": "aa/x.png"}})
            leftovers = list(A.asset_index_path(str(data_dir)).parent.glob("*.tmp-*"))
            self.assertEqual([], leftovers, "原子写不应留下临时文件")


class VisionReplyFormatTests(unittest.TestCase):
    def test_full_width_pipe_separates_tags(self) -> None:
        # 回归：默认识图提示词让模型用竖线分隔标签，而分隔正则只认半角。
        self.assertEqual(["居家", "秋冬", "宽松"], normalize_wardrobe_tags("居家｜秋冬｜宽松"))
        self.assertEqual(["居家", "秋冬"], normalize_wardrobe_tags("居家|秋冬"))

    def test_bulleted_irrelevant_reply_is_still_rejected(self) -> None:
        # 回归：模型把字段写成项目符号时，整段落到「未标注文本」，类型判定失效 ——
        # 一张明确写着「无关」的图会被落库成一件叫「- 类型：无关」的衣服。
        self.assertIsNone(
            parse_wardrobe_image_reply("- 类型：无关\n- 名称：无\n- 描述：画面里没有衣物")
        )

    def test_bulleted_normal_reply_still_parses(self) -> None:
        parsed = parse_wardrobe_image_reply(
            "- 类型：散件\n- 名称：黑色风衣\n- 描述：长款\n- 部位：上身\n- 标签：秋冬|通勤"
        )
        assert parsed is not None
        self.assertEqual("黑色风衣", parsed["name"])
        self.assertEqual("upper", parsed["slot"])
        self.assertEqual(["秋冬", "通勤"], parsed["tags"])


if __name__ == "__main__":
    unittest.main()
