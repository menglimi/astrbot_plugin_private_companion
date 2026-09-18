#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣柜草稿队列：批量导入后的确认环节（离线，不依赖模型与 AstrBot 运行时）。

典型流程：

    # 1) 导入图片（素材层，幂等）
    python3 scripts/wardrobe_import.py --source-dir <图片目录> --data-dir <数据目录> --origin blogger

    # 2) 识图（真实模型或回放）→ 写草稿
    python3 scripts/wardrobe_understand.py --data-dir <数据目录> --replies <回复.json>

    # 3) 看队列
    python3 scripts/wardrobe_review.py --data-dir <数据目录> --wardrobe <wardrobe.json> --list

    # 4) 确认 / 丢弃
    python3 scripts/wardrobe_review.py --data-dir <数据目录> --wardrobe <wardrobe.json> --apply-all
    python3 scripts/wardrobe_review.py --data-dir <数据目录> --wardrobe <wardrobe.json> --reject <asset_id>

确认时会调用数据层的 apply_wardrobe_draft —— 与聊天里发图入库走的是同一套分流逻辑。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wardrobe import apply_wardrobe_draft  # noqa: E402
from wardrobe_assets import (  # noqa: E402
    ASSET_STATUS_REJECTED,
    ASSET_STATUS_UNDERSTOOD,
    list_pending_drafts,
    load_asset_draft,
    mark_asset_status,
)


def load_wardrobe(path: Path) -> dict:
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("items", [])
            payload.setdefault("outfits", [])
            return payload
    return {"items": [], "outfits": []}


def save_wardrobe(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="衣柜草稿队列：列出 / 确认 / 丢弃")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--wardrobe", required=True, help="落库目标 JSON（items / outfits）")
    parser.add_argument("--list", action="store_true", help="列出待确认项（默认动作）")
    parser.add_argument("--apply", nargs="*", default=[], metavar="ASSET_ID")
    parser.add_argument("--reject", nargs="*", default=[], metavar="ASSET_ID")
    parser.add_argument("--apply-all", action="store_true", help="确认所有已有草稿的待办项")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    wardrobe_path = Path(args.wardrobe).expanduser()
    store = load_wardrobe(wardrobe_path)
    pending = list_pending_drafts(args.data_dir)
    results: list[dict] = []

    def apply_one(asset_id: str) -> dict:
        try:
            draft = load_asset_draft(args.data_dir, asset_id)
            if not draft:
                return {"asset_id": asset_id, "ok": False, "error": "还没有草稿（先跑识图）"}
            items, outfits, outcome = apply_wardrobe_draft(
                store["items"], store["outfits"], draft, asset_id=asset_id
            )
        except Exception as exc:
            # 单条失败不该中断整批（与 import_directory 同一约定）。
            return {"asset_id": asset_id, "ok": False, "error": f"处理失败：{exc}"}
        if not outcome.get("ok"):
            return {"asset_id": asset_id, **outcome}
        # 顺序与运行时一致：**先落库、再推进素材状态**。反过来的话，保存失败时素材
        # 已经变成 understood，队列里再也看不到它，等于静默丢件（批量中途异常时
        # 尤其明显：前面几件状态已推进，衣柜却一个字节都没写）。
        store["items"], store["outfits"] = items, outfits
        save_wardrobe(wardrobe_path, store)
        mark_asset_status(args.data_dir, asset_id, ASSET_STATUS_UNDERSTOOD)
        return {"asset_id": asset_id, **outcome}

    targets = list(args.apply)
    if args.apply_all:
        targets += [row["asset_id"] for row in pending if row.get("has_draft")]
    for asset_id in dict.fromkeys(targets):
        results.append(apply_one(asset_id))
    for asset_id in dict.fromkeys(args.reject):
        record = mark_asset_status(args.data_dir, asset_id, ASSET_STATUS_REJECTED)
        results.append({"asset_id": asset_id, "ok": bool(record), "kind": "rejected",
                        "error": "" if record else "素材不在索引里"})

    # 衣柜已在每条成功确认时就地保存（见 apply_one），这里不再统一落盘。

    if args.json:
        print(json.dumps({"pending": len(pending), "actions": results,
                          "items_total": len(store["items"]),
                          "outfits_total": len(store["outfits"])}, ensure_ascii=False, indent=2))
        return 0

    if results:
        for row in results:
            if row.get("ok"):
                print(f"  ✓ {row['asset_id']}  {row.get('kind',''):<9} {row.get('name','')}"
                      f"{'（更新）' if row.get('replaced') else ''}")
            else:
                print(f"  ✗ {row['asset_id']}  {row.get('error','失败')}")
        print(f"\n衣柜现有 散件 {len(store['items'])} / 整套 {len(store['outfits'])}；"
              f"落库结果：{wardrobe_path}")
        return 0

    print(f"待确认：{len(pending)} 项（status=imported）")
    for row in pending:
        mark = "有草稿" if row.get("has_draft") else "等识图"
        label = row.get("name") or Path(str(row.get("path"))).name
        print(f"  {row['asset_id']}  [{mark}]  {row.get('kind','') or '—':<9}"
              f"{row.get('slot','') or '—':<8}{label}")
    if not pending:
        print("（队列是空的：要么没导入，要么都已确认/丢弃）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
