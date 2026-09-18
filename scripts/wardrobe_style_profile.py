#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从参考穿搭归纳"风格画像"（离线，不依赖模型）。

用法：

    python3 scripts/wardrobe_style_profile.py --wardrobe <wardrobe_preview.json>
    python3 scripts/wardrobe_style_profile.py --wardrobe <...> --json

输入是包含 outfits 的 JSON（wardrobe_understand.py 的产物，
或直接导出的插件配置）。只统计 ownership=reference 的整套。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wardrobe_style import STYLE_DIMENSION_LABELS, build_style_profile, render_reference_profile  # noqa: E402


def load_references(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "wardrobe_outfits" in payload:
        payload = payload.get("wardrobe_outfits")
    elif isinstance(payload, dict) and "outfits" in payload:
        payload = payload.get("outfits")
    rows = payload if isinstance(payload, list) else []
    return [
        row for row in rows
        if isinstance(row, dict) and str(row.get("ownership") or "") == "reference"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从参考穿搭归纳风格画像")
    parser.add_argument("--wardrobe", required=True, help="包含 outfits 的 JSON 文件")
    parser.add_argument("--top", type=int, default=6, help="每个维度最多保留几个词")
    parser.add_argument("--min-count", type=int, default=2, help="出现几次才算风格")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    path = Path(args.wardrobe).expanduser()
    if not path.is_file():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2
    references = load_references(path)
    profile = build_style_profile(references, top=args.top, min_count=args.min_count)
    line = render_reference_profile(references, top=args.top, min_count=args.min_count)

    if args.json:
        print(json.dumps({"references": len(references), "profile": profile, "line": line},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"参考整套：{len(references)} 套")
    if not references:
        print("（没有 ownership=reference 的整套，无法归纳）")
        return 0
    for dim, label in STYLE_DIMENSION_LABELS.items():
        words = profile.get(dim) or []
        print(f"  {label}：{' / '.join(words) if words else '—'}")
    print()
    print("可注入行：" + (line or "（证据不足，暂不注入）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
