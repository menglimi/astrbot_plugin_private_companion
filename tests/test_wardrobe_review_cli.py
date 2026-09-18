# -*- coding: utf-8 -*-
"""scripts/wardrobe_review.py：批量确认的落库顺序与故障隔离。

背景（回归）：原实现「先推进素材状态、最后统一保存衣柜」，批量中途任何一条抛异常都会
让衣柜文件一个字节都没写，而前面几条素材已经被标成 understood —— 它们从待确认队列里
消失，却不在衣柜里，等于静默丢件。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wardrobe_assets import (  # noqa: E402
    load_asset_index,
    save_asset_index,
    write_asset_draft,
)


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "wardrobe_review_cli", ROOT / "scripts" / "wardrobe_review.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["wardrobe_review_cli"] = module
    spec.loader.exec_module(module)
    return module


def _prepare(tmp: pathlib.Path, names: tuple[str, ...]) -> tuple[pathlib.Path, pathlib.Path]:
    data_dir = tmp / "data"
    wardrobe_path = tmp / "wardrobe.json"
    (data_dir / "wardrobe_assets").mkdir(parents=True, exist_ok=True)
    rows = []
    for index, name in enumerate(names):
        rows.append({
            "id": name,
            "path": f"{name}.jpg",
            "sha256": (name * 8)[:64],
            "kind": "image",
        })
        write_asset_draft(
            str(data_dir),
            name,
            {
                "kind": "item",
                "name": f"衣物{index}",
                "description": "描述",
                "tags": [],
                "slot": "upper",
            },
        )
    save_asset_index(str(data_dir), {"assets": rows})
    return data_dir, wardrobe_path


class WardrobeReviewCliTests(unittest.TestCase):
    def test_apply_all_persists_and_marks_every_item(self) -> None:
        cli = _load_cli()
        with tempfile.TemporaryDirectory() as raw:
            data_dir, wardrobe_path = _prepare(pathlib.Path(raw), ("a1", "a2"))
            code = cli.main(["--data-dir", str(data_dir), "--wardrobe", str(wardrobe_path),
                             "--apply-all", "--json"])
            self.assertEqual(0, code)
            store = json.loads(wardrobe_path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(store["items"]))
            index = load_asset_index(str(data_dir))
            self.assertTrue(all(row.get("status") == "understood" for row in index.values()))

    def test_mid_batch_failure_does_not_lose_earlier_items(self) -> None:
        cli = _load_cli()
        with tempfile.TemporaryDirectory() as raw:
            data_dir, wardrobe_path = _prepare(pathlib.Path(raw), ("b1", "b2", "b3"))
            real = cli.apply_wardrobe_draft
            calls = {"n": 0}

            def flaky(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("模拟处理失败")
                return real(*args, **kwargs)

            cli.apply_wardrobe_draft = flaky
            code = cli.main(["--data-dir", str(data_dir), "--wardrobe", str(wardrobe_path),
                             "--apply-all", "--json"])
            self.assertEqual(0, code, "单条失败不该让整批崩溃")

            index = load_asset_index(str(data_dir))
            store = json.loads(wardrobe_path.read_text(encoding="utf-8"))
            landed = {row["name"] for row in store["items"]}
            marked = {key for key, row in index.items() if row.get("status") == "understood"}
            # 核心不变量：被标成 understood 的素材必须真的在衣柜里。
            self.assertEqual(2, len(marked), marked)
            self.assertEqual(2, len(landed), landed)
            # 失败的那条保持 imported，仍留在待确认队列。
            from wardrobe_assets import list_pending_drafts

            pending = {row["asset_id"] for row in list_pending_drafts(str(data_dir))}
            self.assertIn("b2", pending)

    def test_reject_marks_status_without_touching_the_wardrobe(self) -> None:
        cli = _load_cli()
        with tempfile.TemporaryDirectory() as raw:
            data_dir, wardrobe_path = _prepare(pathlib.Path(raw), ("c1",))
            code = cli.main(["--data-dir", str(data_dir), "--wardrobe", str(wardrobe_path),
                             "--reject", "c1", "--json"])
            self.assertEqual(0, code)
            index = load_asset_index(str(data_dir))
            self.assertEqual("rejected", index["c1"].get("status"))
            self.assertFalse(wardrobe_path.is_file(), "丢弃不该创建衣柜文件")


if __name__ == "__main__":
    unittest.main()
