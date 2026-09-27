# -*- coding: utf-8 -*-
"""扫描（并可清理）宿主类体里的「孤儿注释」—— 已经失去归属对象的注释块。

## 为什么需要这个工具

`split_mixin_domain.py` 搬运类方法时，会把**紧贴方法定义上方**的连续注释一并带走
（`span_of` 向上吞并注释行）。但当注释块与方法之间**隔了空行**时，注释不会被带走，
于是留在了宿主的原位置 —— 成为一个「孤儿」：

```python
class Host:
    def moved_away(self): ...          # 被搬走

    # ---- 区块注释（原本是下面方法的说明）----
    # ... (孤儿：留在这里)

    def also_moved_away(self): ...     # 也被搬走
```

## 为什么 numstat 检查抓不到它

**孤儿注释不产生 diff** —— 它本来就存在于宿主，只是失去了归属。所以
`git diff --numstat` 看起来完全正常（如 `2 2637`），直到**后续再一次拆分**
把孤儿注释上下两侧的代码都搬走，它才作为「新增 12 行」暴露出来，
并被抽取器的 numstat 守卫误判为行尾污染。

## 判据

一个注释块被判为孤儿，当且仅当同时满足：
1. 缩进等于宿主类的成员缩进（即它是类体里的注释，不是方法体内的）
2. 注释块**下方**（跳过任意个空行后）存在的下一条语句是 `def` / `async def` /
   `class` / 装饰器行 / 类体结束
3. 注释块与那条语句之间 **至少有一个空行**

第 3 条是关键：正常的方法前导注释必然紧贴 `def`（中间无空行）。
若作者有意用「注释 + 空行 + def」做章节标题，会被本工具误判 ——
因此默认**只报告不删除**，需显式 `--write` 才清理。

用法:
    python tools/clean_orphan_comments.py <宿主文件> [--host-class 类名] [--write]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMENT = re.compile(r"^(\s*)#")
DEF_LIKE = re.compile(r"^\s*(def |async def |class |@)")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _collect_blocks(
    lines: list[str], start: int, end: int, member_indent: int
) -> list[tuple[int, int]]:
    """收集 [start, end) 内所有「缩进等于类成员缩进」的连续注释块（0-based 闭区间）。"""
    blocks: list[tuple[int, int]] = []
    i = start
    while i < end:
        line = lines[i]
        if COMMENT.match(line) and indent_of(line) == member_indent:
            s = i
            while (
                i < end
                and COMMENT.match(lines[i])
                and indent_of(lines[i]) == member_indent
            ):
                i += 1
            blocks.append((s, i - 1))
            continue
        i += 1
    return blocks


def find_orphans(lines: list[str], host_class: str) -> list[tuple[int, int]]:
    """返回孤儿注释块的行区间列表（1-based，闭区间）。"""
    # 先定位宿主类的类体范围与成员缩进
    class_start = None
    for i, line in enumerate(lines):
        if re.match(rf"^class {re.escape(host_class)}\b", line):
            class_start = i
            break
    if class_start is None:
        raise SystemExit(f"找不到 `class {host_class}`")

    member_indent = None
    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if indent_of(line) == 0:  # 下一个顶层定义，类体结束
            break
        member_indent = indent_of(line)
        break
    if member_indent is None:
        raise SystemExit(f"{host_class} 类体为空")

    body_end = len(lines)
    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        if line.strip() and indent_of(line) == 0:
            body_end = i
            break

    # 关键：向下扫描时要把**后续的所有注释块**一并跳过。
    # 因为一个语义段落可能由「注释块 + 空行 + 注释块 + 空行 + def」组成
    # （本仓库常见：`# ----` 分隔条 + 说明 + `# ----` 分隔条 + 说明 + def），
    # 只看紧邻的下一块会漏报前半部分。
    orphans: list[tuple[int, int]] = []
    for s, e in _collect_blocks(lines, class_start + 1, body_end, member_indent):
        j = e + 1
        had_blank = False
        while j < body_end:
            line = lines[j]
            if not line.strip():
                had_blank = True
                j += 1
                continue
            if COMMENT.match(line) and indent_of(line) == member_indent:
                while (
                    j < body_end
                    and COMMENT.match(lines[j])
                    and indent_of(lines[j]) == member_indent
                ):
                    j += 1
                continue
            break
        if not had_blank:
            continue  # 紧贴下方代码 → 正常前导注释
        if j >= body_end or DEF_LIKE.match(lines[j]):
            orphans.append((s + 1, e + 1))  # 1-based
    return orphans


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host", help="宿主文件（相对仓库根或绝对路径）")
    ap.add_argument("--host-class", default="", help="宿主类名（不传则扫描所有顶部类）")
    ap.add_argument("--write", action="store_true", help="真的删除（默认只报告）")
    args = ap.parse_args()

    path = Path(args.host)
    if not path.is_absolute():
        path = ROOT / path
    raw = path.read_bytes()
    # 本仓库的宿主文件普遍是**混合行尾**（如 daily_state.py 有 12606 个 CRLF
    # 与 514 个裸 LF）。按 b"\n" 切分可保留每行原始行尾（CRLF 行的段尾带 \r），
    # 写回时 b"\n".join 即逐字节还原 —— 绝不能用 b"\r\n" 切分，
    # 否则裸 LF 行会被并进上一行，行号整体错位，且写回会篡改行尾。
    segments = raw.split(b"\n")
    lines = [seg.decode("utf-8") for seg in segments]

    classes = [args.host_class] if args.host_class else [
        m.group(1)
        for line in lines
        if (m := re.match(r"^class (\w+)", line))
    ]
    if not classes:
        raise SystemExit("没有找到任何类")

    all_orphans: list[tuple[int, int]] = []
    for cls in classes:
        found = find_orphans(lines, cls)
        if found:
            print(f"=== {path.name} :: class {cls} ===")
        for s, e in found:
            print(f"  孤儿注释块 L{s}-L{e}（{e - s + 1} 行）:")
            for n in range(s, e + 1):
                print(f"      {n:5d}| {lines[n - 1][:110]}")
            all_orphans.append((s, e))

    print()
    if not all_orphans:
        print("未发现孤儿注释 ✅")
        return 0

    total = sum(e - s + 1 for s, e in all_orphans)
    print(f"共 {len(all_orphans)} 个孤儿注释块 / {total} 行")

    if not args.write:
        print("（只报告。加 --write 才会删除）")
        return 0

    kill = {n for s, e in all_orphans for n in range(s, e + 1)}
    kept = [seg for idx, seg in enumerate(segments, 1) if idx not in kill]
    path.write_bytes(b"\n".join(kept))
    # 行尾必须逐字节不变 —— 用 git diff --numstat 复核第一列不应出现整文件行数
    print(f"已删除 {total} 行孤儿注释，写回 {path.name}")
    print("  （复核：git diff --numstat 第一列应与删除行数相称，且行尾统计不变）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
