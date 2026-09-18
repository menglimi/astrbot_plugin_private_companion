#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""衣柜素材批量导入器（离线，不依赖 AstrBot 运行时）。

用法：

    python3 scripts/wardrobe_import.py --source-dir <图片目录> --data-dir <插件数据目录> \
        --origin blogger --limit 10

它会：扫描目录 → 按内容指纹去重 → 复制进 <data-dir>/wardrobe_assets/<ab>/<digest>.<ext>
→ 写 index.json → 打印摘要。

设计要点：

1. **与插件运行时解耦**：只用到 wardrobe_assets，不需要 AstrBot、不需要模型；
2. **幂等**：同一批图片重复导入只会增加 skipped 计数，不会产生重复资产；
3. **不动原文件**：默认复制（--move 才移动），源目录可继续作为你自己的备份。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wardrobe_assets import (  # noqa: E402
    ASSET_ORIGIN_BLOGGER,
    ASSET_ORIGINS,
    import_directory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把一批图片导入衣柜素材层")
    parser.add_argument("--source-dir", required=True, help="源目录（图片所在位置）")
    parser.add_argument("--data-dir", required=True, help="插件数据目录（素材会存到它的 wardrobe_assets/ 下）")
    parser.add_argument("--origin", default=ASSET_ORIGIN_BLOGGER, choices=sorted(ASSET_ORIGINS),
                        help="来源标记，默认 blogger")
    parser.add_argument("--limit", type=int, default=0, help="最多导入多少个文件（0 = 全部）")
    parser.add_argument("--stride", type=int, default=0,
                        help="先按固定间隔抽样（例如 21 表示每隔 21 个取一个），用于挑少量样本试跑")
    parser.add_argument("--no-recursive", action="store_true", help="只扫描一层目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于脚本消费）")
    parser.add_argument("--quiet", action="store_true", help="不打印逐条进度")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.source_dir).expanduser()
    if not source.is_dir():
        print(f"源目录不存在：{source}", file=sys.stderr)
        return 2

    # --stride 抽样：先列出候选，再按间隔取，避免"只导入最前面几张"
    if args.stride and args.stride > 1:
        from wardrobe_assets import scan_source_dir

        files = scan_source_dir(source, recursive=not args.no_recursive)
        picked = files[:: args.stride]
        if args.limit:
            picked = picked[: args.limit]
        stats = {"scanned": len(picked), "imported": 0, "skipped": 0, "failed": 0, "errors": []}
        from wardrobe_assets import import_asset, load_asset_index, save_asset_index

        index = load_asset_index(args.data_dir)
        for path in picked:
            try:
                record, created = import_asset(args.data_dir, path, index=index, origin=args.origin)
            except Exception as exc:
                stats["failed"] += 1
                if len(stats["errors"]) < 20:
                    stats["errors"].append(f"{path.name}: {exc}")
                continue
            index[record["id"]] = record
            stats["imported" if created else "skipped"] += 1
            if not args.quiet:
                print(f"  {'+' if created else '='} {record['id']}  {path.name}")
        save_asset_index(args.data_dir, index)
        stats["index"] = index
    else:
        def progress(position: int, total: int, path: Path) -> None:
            if not args.quiet:
                print(f"  [{position}/{total}] {path.name}")

        stats = import_directory(
            args.data_dir,
            source,
            origin=args.origin,
            limit=args.limit,
            recursive=not args.no_recursive,
            on_progress=progress,
        )

    index = stats.pop("index", {})
    payload = {
        "source_dir": str(source),
        "data_dir": str(Path(args.data_dir).expanduser()),
        "origin": args.origin,
        "summary": {k: v for k, v in stats.items() if k != "errors"},
        "errors": stats.get("errors") or [],
        "index_size": len(index),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print()
    print(f"扫描 {payload['summary']['scanned']} 个文件；"
          f"新导入 {payload['summary']['imported']}，"
          f"已存在跳过 {payload['summary']['skipped']}，"
          f"失败 {payload['summary']['failed']}")
    print(f"素材索引：{Path(args.data_dir).expanduser() / 'wardrobe_assets' / 'index.json'}（共 {len(index)} 条）")
    for line in payload["errors"]:
        print(f"  ! {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
