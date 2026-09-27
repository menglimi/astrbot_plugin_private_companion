# -*- coding: utf-8 -*-
"""按域按需把宿主私有模块级件提升到 main_shared.py（字节级，保留 CRLF）。

用法:
    python tools/promote_to_shared.py <名字> [<名字> ...]

被提升的定义会从 main.py 整块删除，追加到 main_shared.py 末尾，并在
main.py 的 main_shared import 块里补上名字。已提升的名字会被跳过（幂等）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "main.py"
SHARED = ROOT / "main_shared.py"


def host_blocks(tree: ast.Module, names: set[str]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in names:
                out[node.name] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in names:
                    out[tgt.id] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in names:
                out[node.target.id] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                key = (alias.asname or alias.name).split(".")[0]
                if key in names:
                    out[key] = (node.lineno, node.end_lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                key = alias.asname or alias.name
                if key in names:
                    out[key] = (node.lineno, node.end_lineno)
    return out


def main() -> int:
    names = set(sys.argv[1:])
    if not names:
        raise SystemExit("需要至少一个要提升的名字")

    shared_src = SHARED.read_text(encoding="utf-8")
    shared_tree = ast.parse(shared_src, filename="main_shared.py")
    already = {
        n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
        for n in shared_tree.body
    }
    already |= {
        t.id
        for n in shared_tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    todo = {n for n in names if n not in already}
    skipped = sorted(names - todo)
    if skipped:
        print(f"已在 main_shared 中，跳过: {skipped}")
    if not todo:
        print("无需提升")
        return 0

    raw = HOST.read_bytes()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.split(b"\n")
    tree = ast.parse(raw.decode("utf-8"), filename="main.py")
    blocks = host_blocks(tree, todo)
    missing = sorted(todo - set(blocks))
    if missing:
        raise SystemExit(f"宿主中找不到这些定义: {missing}")

    ordered = sorted(blocks.items(), key=lambda kv: kv[1][0])
    print("将提升:")
    chunks: list[bytes] = []
    for name, (s, e) in ordered:
        print(f"  {name:<44} L{s}-L{e} ({e - s + 1} 行)")
        chunks.append(b"\n".join(lines[s - 1: e]))

    drop: set[int] = set()
    for name, (s, e) in ordered:
        for i in range(s, e + 1):
            drop.add(i)
        if e < len(lines) and lines[e].strip() == b"":
            drop.add(e + 1)
    kept = [lines[i - 1] for i in range(1, len(lines) + 1) if i not in drop]
    new_host = b"\n".join(kept)

    # 在 main.py 的 main_shared import 块里补名字
    text = new_host.decode("utf-8")
    m = re.search(r"from \.main_shared import \(\r?\n(.*?)\r?\n\)", text, re.S)
    if not m:
        raise SystemExit("main.py 中找不到 main_shared import 块")
    existing = {ln.strip().rstrip(",") for ln in m.group(1).splitlines() if ln.strip()}
    merged = sorted(existing | todo)
    eol = "\r\n" if "\r\n" in text else "\n"
    body = "".join(f"    {n},{eol}" for n in merged)
    block = f"from .main_shared import ({eol}" + body + ")"
    new_host = (text[: m.start()] + block + text[m.end():]).encode("utf-8")

    HOST.write_bytes(new_host)

    # 追加到 main_shared.py，并补齐其所需 import
    extra_imports = _needed_imports(shared_src, b"\n\n".join(chunks).decode("utf-8"))
    addendum = "\n\n" + "\n\n".join(
        c.decode("utf-8").rstrip() for c in chunks
    ) + "\n"
    shared_new = shared_src.rstrip("\n") + addendum
    if extra_imports:
        shared_new = _inject_imports(shared_new, extra_imports)
    SHARED.write_text(shared_new, encoding="utf-8")

    print(f"已提升 {len(todo)} 个定义 -> main_shared.py；main.py -> {len(new_host.splitlines())} 行")
    if extra_imports:
        print(f"main_shared 补充 import: {extra_imports}")
    return 0


def _needed_imports(existing_src: str, new_src: str) -> list[str]:
    """用 AST 判断新片段真正需要的 import：从宿主 main.py 抄同名 import 行。

    比正则可靠：把新片段里所有自由名收集起来，再对照宿主顶层能提供的
    名字，凡宿主有而 main_shared 没有的，就把宿主那条 import 复用过来。
    """
    host_src = HOST.read_text(encoding="utf-8")
    host_tree = ast.parse(host_src, filename="main.py")
    shared_tree = ast.parse(existing_src, filename="main_shared.py")

    have: set[str] = set()
    for node in shared_tree.body:
        if isinstance(node, ast.Import):
            have |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            have |= {(a.asname or a.name) for a in node.names}

    frag = ast.parse(new_src, filename="fragment.py")
    local: set[str] = set()
    used: set[str] = set()
    for sub in ast.walk(frag):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for a in (*sub.args.posonlyargs, *sub.args.args, *sub.args.kwonlyargs):
                local.add(a.arg)
            if sub.args.vararg:
                local.add(sub.args.vararg.arg)
            if sub.args.kwarg:
                local.add(sub.args.kwarg.arg)
        elif isinstance(sub, ast.Name):
            if isinstance(sub.ctx, (ast.Store, ast.Del)):
                local.add(sub.id)
            else:
                used.add(sub.id)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            local.add(sub.name)
    wanted = (used - local) - have

    # 宿主里能提供这些名字的 import 语句
    out: list[str] = []
    for node in host_tree.body:
        if isinstance(node, ast.Import):
            hits = [a for a in node.names if (a.asname or a.name).split(".")[0] in wanted]
            if hits and str(node.lineno) not in {l.split("\t")[0] for l in out}:
                out.append(ast.unparse(node))
        elif isinstance(node, ast.ImportFrom):
            hits = [a for a in node.names if (a.asname or a.name) in wanted]
            if hits:
                out.append(
                    "from " + ("." * (node.level or 0)) + (node.module or "")
                    + " import " + ", ".join(ast.unparse(a) for a in hits)
                )
    return out


def _inject_imports(src: str, imports: list[str]) -> str:
    lines = src.split("\n")
    last_import = 0
    for i, ln in enumerate(lines[:60]):
        if ln.startswith("import ") or ln.startswith("from "):
            last_import = i
    for imp in imports:
        lines.insert(last_import + 1, imp)
        last_import += 1
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
