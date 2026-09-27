# -*- coding: utf-8 -*-
"""拆分守恒校验器：宿主 + 全部 main_*.py 域 mixin 的方法名集合守恒/重叠检查。

用法:
    python tools/check_main_conservation.py <before_methods_file> [<mixin> ...]

*before_methods_file* 是拆分前从 main.py dump 的宿主方法名清单（每行一个）。
校验三件事：
1. 宿主剩余方法 ∩ 所有 mixin 方法 == ∅（无新引入的重叠）
2. 宿主剩余方法 ∪ 所有 mixin 方法 == 拆分前的宿主方法集合
3. 任意两个 mixin 之间方法名不重叠
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "main.py"
HOST_CLASS = "PrivateCompanionPlugin"


def _class_named(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def methods_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    if path.name == HOST.name:
        owner = _class_named(tree, HOST_CLASS)
    else:
        owner = next(
            (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith("Mixin")),
            None,
        )
    if owner is None:
        raise SystemExit(f"{path.name} 中找不到目标类")
    return {
        n.name
        for n in owner.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def domain_owner(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node.name
    raise SystemExit(f"{path.name} 中找不到类")


def main() -> int:
    before = {
        ln.strip()
        for ln in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    }
    mixins = sorted(sys.argv[2:]) or sorted(
        p.name for p in ROOT.glob("main_*.py") if p.name != "main.py"
    )

    host_methods = methods_of(HOST)
    ok = True

    print(f"拆分前宿主方法数 : {len(before)}")
    print(f"当前宿主方法数   : {len(host_methods)}")
    print(f"域 mixin 模块数  : {len(mixins)}")

    union: set[str] = set()
    for name in mixins:
        path = ROOT / name
        got = methods_of(path)
        owner = domain_owner(path)
        overlap_host = got & host_methods
        overlap_union = got & union
        if overlap_host:
            ok = False
            print(f"  [FAIL] {name} ({owner}) 与宿主重叠 {len(overlap_host)}: {sorted(overlap_host)[:8]}")
        if overlap_union:
            ok = False
            print(f"  [FAIL] {name} ({owner}) 与其他 mixin 重叠 {len(overlap_union)}: {sorted(overlap_union)[:8]}")
        if not overlap_host and not overlap_union:
            print(f"  [ok]   {name} ({owner}) {len(got)} 方法，无重叠")
        union |= got

    combined = host_methods | union
    missing = before - combined
    introduced = combined - before
    if missing:
        ok = False
        print(f"[FAIL] 丢失方法 {len(missing)}: {sorted(missing)[:10]}")
    if introduced:
        ok = False
        print(f"[FAIL] 新引入方法 {len(introduced)}: {sorted(introduced)[:10]}")
    if not missing and not introduced:
        print(f"[ok]   守恒成立: 宿主({len(host_methods)}) + mixin({len(union)}) == 拆分前({len(before)})")

    print("CONSERVATION: PASS" if ok else "CONSERVATION: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
