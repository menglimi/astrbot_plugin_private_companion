# -*- coding: utf-8 -*-
"""字节级把 main.py 中的宿主私有辅助件改为从 main_shared 导入（保留 CRLF）。

被替换的定义（方法体原样搬进 main_shared.py，不在此重复）：
  * `_new_private_companion_runtime` / `_private_companion_runtime`
  * `_plugin_instance_root` / `_plugin_instance_can_dispatch`
  * `_multi_persona_event_context`
以及随之不再需要的 `_is_primary_plugin_instance`（保留在宿主，它只依赖
_plugin_instance_root，改为从 main_shared 导入该依赖）。

做法：按 AST 行区间整块删除，再在 import 区插入 `from .main_shared import ...`。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "main.py"

DROP = [
    "_new_private_companion_runtime",
    "_multi_persona_event_context",
    "_plugin_instance_root",
    "_plugin_instance_can_dispatch",
]
KEEP_BUT_REWIRE = {"_is_primary_plugin_instance"}
IMPORT_ANCHOR = "from .helpers import ("


def main() -> int:
    raw = HOST.read_bytes()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    text = raw.decode("utf-8")
    tree = ast.parse(text, filename="main.py")
    lines = raw.split(b"\n")

    spans: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in DROP:
            spans.append((node.lineno, node.end_lineno, node.name))
        # runtime 赋值语句
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_private_companion_runtime":
                    spans.append((node.lineno, node.end_lineno, "_private_companion_runtime"))
    spans.sort()
    print("将删除的块:")
    for s, e, name in spans:
        print(f"  {name:<34} L{s}-L{e} ({e - s + 1} 行)")

    drop_idx: set[int] = set()
    for s, e, _ in spans:
        for i in range(s, e + 1):
            drop_idx.add(i)
        # 连带其后紧跟的空行
        if e < len(lines) and lines[e].strip() == b"":
            drop_idx.add(e + 1)

    kept = [lines[i - 1] for i in range(1, len(lines) + 1) if i not in drop_idx]
    new_bytes = b"\n".join(kept)

    import_block = (
        "from .main_shared import (\n"
        "    _multi_persona_event_context,\n"
        "    _plugin_instance_can_dispatch,\n"
        "    _plugin_instance_root,\n"
        "    _private_companion_runtime,\n"
        ")\n"
    ).encode("utf-8")
    idx = new_bytes.find(IMPORT_ANCHOR.encode("utf-8"))
    if idx < 0:
        raise SystemExit("找不到 import 锚点")
    # 插到该 from-import 语句（可能跨多行）结束之后
    text_after = new_bytes.decode("utf-8")
    seg_tree = ast.parse(text_after, filename="main.py")
    anchor_node = next(
        n for n in seg_tree.body
        if isinstance(n, ast.ImportFrom) and n.module == "helpers"
    )
    seg_lines = new_bytes.split(b"\n")
    eol_pos = sum(len(l) + 1 for l in seg_lines[: anchor_node.end_lineno])
    new_bytes = new_bytes[:eol_pos] + import_block.replace(b"\n", nl) + new_bytes[eol_pos:]

    HOST.write_bytes(new_bytes)
    print(f"已改写 {HOST.name} -> {len(new_bytes.splitlines())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
