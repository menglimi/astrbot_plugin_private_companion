# -*- coding: utf-8 -*-
"""校验域 mixin 模块的自有依赖是否都能解析（AST 级 + 可选真实导入）。

用法:
    python tools/check_mixin_imports.py <模块文件名> [<模块文件名> ...]

AST 级检查（默认，纯文本解释器即可跑）：
  收集 mixin 类里每个方法引用的模块级自由名，逐个核对是否由
  本模块 import、从 main_shared 取到、或可从宿主 main.py 顶层名字解析。
  任何无法解析的名字即 FAIL —— 这正是拆分时最容易漏掉的环节。

真实导入检查在设置了 PYTHONPATH 且能用 AstrBot 解释器时由
tools/verify_mro_runtime.py 承担。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILTIN = set(dir(builtins))


def module_level_defs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add(a.asname or a.name.split(".")[0])
    return out


def free_names_in_class(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    local: set[str] = set()
    used: set[str] = set()
    for node in owner.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for piece in [node, *node.decorator_list]:
            for sub in ast.walk(piece):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for a in (
                        *sub.args.posonlyargs,
                        *sub.args.args,
                        *sub.args.kwonlyargs,
                    ):
                        local.add(a.arg)
                    if sub.args.vararg:
                        local.add(sub.args.vararg.arg)
                    if sub.args.kwarg:
                        local.add(sub.args.kwarg.arg)
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                    local.add(sub.id)
                elif isinstance(sub, ast.ExceptHandler) and sub.name:
                    local.add(sub.name)
                elif isinstance(sub, ast.comprehension):
                    for t in ast.walk(sub.target):
                        if isinstance(t, ast.Name):
                            local.add(t.id)
                elif isinstance(sub, ast.withitem) and sub.optional_vars is not None:
                    for t in ast.walk(sub.optional_vars):
                        if isinstance(t, ast.Name):
                            local.add(t.id)
                elif isinstance(sub, ast.MatchAs) and sub.name:
                    local.add(sub.name)
        for piece in [node, *node.decorator_list]:
            for sub in ast.walk(piece):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    used.add(sub.id)
    return used - local - {"self", "cls"}


def main() -> int:
    host_defs = module_level_defs(ROOT / "main.py")
    shared_defs = module_level_defs(ROOT / "main_shared.py")
    ok = True
    for name in sys.argv[1:]:
        path = ROOT / name
        own = module_level_defs(path)
        missing = sorted(
            n
            for n in free_names_in_class(path)
            if n not in own
            and n not in shared_defs
            and n not in host_defs
            and n not in BUILTIN
        )
        if missing:
            ok = False
            print(f"[FAIL] {name} 有 {len(missing)} 个无法解析的名字: {missing[:20]}")
        else:
            print(f"[ok]   {name} 全部自有依赖可解析")
    print("IMPORTS: PASS" if ok else "IMPORTS: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
