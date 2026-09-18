#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣柜理解层运行器：把「模型对一张图的回复」变成落库的散件 / 整套。

用法（离线回放，用于测试与回归）：

    python3 scripts/wardrobe_understand.py --data-dir <数据目录> --replies <回复.json>

replies.json 形状：{"asset_xxxx": "类型：整套\n名称：…\n描述：…\n标签：…", ...}

联网实跑时，只需把 --replies 换成真实模型输出（回复内容与形状完全一致）：
生产路径下这一步由 wardrobe_runtime 的识图调用产出，本脚本是同一套解析与落库逻辑的
离线入口，因此回放与实跑走的是同一条代码路径。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wardrobe import (  # noqa: E402
    OWNERSHIP_OWNED,
    OWNERSHIP_REFERENCE,
    WARDROBE_IMAGE_KIND_ITEM,
    WARDROBE_IMAGE_KIND_OUTFIT,
    WARDROBE_IMAGE_KIND_REFERENCE,
    add_wardrobe_item,
    add_wardrobe_outfit,
    parse_wardrobe_image_reply,
)
from wardrobe_assets import (  # noqa: E402
    ASSET_STATUS_REJECTED,
    ASSET_STATUS_UNDERSTOOD,
    load_asset_index,
    save_asset_index,
    write_asset_draft,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把识图回复落成散件 / 整套")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--replies", required=True, help="asset_id → 模型回复文本 的 JSON")
    parser.add_argument("--wardrobe-file", default="", help="落库结果写到哪里（默认 <data-dir>/wardrobe_preview.json）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="只写草稿并保持 status=imported，交给 wardrobe_review.py 确认（推荐批量导入时使用）",
    )
    args = parser.parse_args(argv)

    replies = json.loads(Path(args.replies).read_text(encoding="utf-8"))
    index = load_asset_index(args.data_dir)
    wardrobe_path = Path(args.wardrobe_file) if args.wardrobe_file else Path(args.data_dir) / "wardrobe_preview.json"
    if wardrobe_path.is_file():
        store = json.loads(wardrobe_path.read_text(encoding="utf-8"))
    else:
        store = {"items": [], "outfits": []}

    rows = []
    for asset_id, raw in replies.items():
        record = index.get(asset_id)
        if record is None:
            rows.append({"asset_id": asset_id, "error": "素材不在索引里"})
            continue
        draft = parse_wardrobe_image_reply(raw)
        if draft is None:
            record["status"] = ASSET_STATUS_REJECTED
            index[asset_id] = record
            write_asset_draft(args.data_dir, asset_id, {"kind": "none", "reason": "无可辨认衣物"})
            rows.append({"asset_id": asset_id, "kind": "none", "name": "", "slot": "", "landed": "rejected"})
            continue
        if args.draft_only:
            # 草稿队列模式：只落草稿，等用户确认；素材状态保持 imported
            write_asset_draft(args.data_dir, asset_id, draft)
            rows.append({
                "asset_id": asset_id,
                "kind": draft["kind"],
                "name": draft["name"],
                "slot": draft["slot"],
                "tags": draft["tags"],
                "landed": "draft",
            })
            continue
        kind = draft["kind"]
        landed = ""
        if kind in (WARDROBE_IMAGE_KIND_OUTFIT, WARDROBE_IMAGE_KIND_REFERENCE):
            ownership = OWNERSHIP_REFERENCE if kind == WARDROBE_IMAGE_KIND_REFERENCE else OWNERSHIP_OWNED
            store["outfits"], stored = add_wardrobe_outfit(
                store["outfits"],
                name=draft["name"],
                kind="style",
                style=draft["description"],
                asset_ids=[asset_id],
                ownership=ownership,
            )
            landed = f"outfits/{ownership}"
        elif kind == WARDROBE_IMAGE_KIND_ITEM:
            store["items"], stored = add_wardrobe_item(
                store["items"],
                name=draft["name"],
                description=draft["description"],
                tags=draft["tags"],
                slot=draft["slot"],
                asset_ids=[asset_id],
                ownership=OWNERSHIP_OWNED,
            )
            landed = "items"
        else:
            rows.append({"asset_id": asset_id, "kind": kind, "name": draft["name"], "slot": "", "landed": "?"})
            continue
        record["status"] = ASSET_STATUS_UNDERSTOOD
        index[asset_id] = record
        write_asset_draft(args.data_dir, asset_id, draft)
        rows.append({
            "asset_id": asset_id,
            "kind": kind,
            "name": draft["name"],
            "slot": draft["slot"],
            "tags": draft["tags"],
            "landed": landed,
            "stored_id": stored["id"],
        })

    save_asset_index(args.data_dir, index)
    wardrobe_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "assets": len(replies),
        "drafts": sum(1 for r in rows if r.get("landed") == "draft"),
        "items": sum(1 for r in rows if r.get("landed") == "items"),
        "outfits": sum(1 for r in rows if str(r.get("landed", "")).startswith("outfits/")),
        "rejected": sum(1 for r in rows if r.get("landed") == "rejected"),
        "wardrobe_file": str(wardrobe_path),
        "items_total": len(store["items"]),
        "outfits_total": len(store["outfits"]),
    }
    if args.json:
        print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))
        return 0
    print(f"{'asset':<22}{'类型':<8}{'名称':<16}{'部位':<7}去向")
    for row in rows:
        if row.get("error"):
            print(f"{row['asset_id']:<22}{'—':<8}{row['error']}")
            continue
        print(f"{row['asset_id']:<22}{row.get('kind',''):<8}{row.get('name',''):<16}"
              f"{row.get('slot','') or '-':<7}{row.get('landed','')}")
    print()
    print(f"共 {summary['assets']} 张：散件 {summary['items']}，整套 {summary['outfits']}，"
          f"丢弃 {summary['rejected']}；衣柜现有 散件 {summary['items_total']} / 整套 {summary['outfits_total']}")
    print(f"落库结果：{wardrobe_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
