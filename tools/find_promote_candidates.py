# -*- coding: utf-8 -*-
"""找出某个 mixin 模块需要、但尚不能从其自身 import 解析的宿主私有件。

用法:
    python tools/find_promote_candidates.py <mixin_module.py> [...]
输出：每行一个需要提升到 main_shared 的名字（无输出表示不需要提升）。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILTIN = set(dir(builtins))
SHARED = {"_multi_persona_event_context", "_plugin_instance_root",
          "_plugin_instance_can_dispatch", "_private_companion_runtime"}


def mod_defs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name.split(".")[0]) for a in node.names}
    return out


def main() -> int:
    shared_defs = mod_defs(ROOT / "main_shared.py") | SHARED
    host_defs = mod_defs(ROOT / "main.py")
    host_tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"), filename="main.py")
    # 宿主里能提供、但不是 import 的顶层定义（即可被"提升"的东西）
    host_own: set[str] = set()
    for node in host_tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            host_own.add(node.name)
        elif isinstance(node, ast.Assign):
            host_own |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            host_own.add(node.target.id)

    out: set[str] = set()
    for name in sys.argv[1:]:
        path = ROOT / name
        own = mod_defs(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for owner in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            used: set[str] = set()
            local: set[str] = set()
            for node in owner.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for piece in [node, *node.decorator_list]:
                    for sub in ast.walk(piece):
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                            for a in (*sub.args.posonlyargs, *sub.args.args, *sub.args.kwonlyargs):
                                local.add(a.arg)
                            if sub.args.vararg:
                                local.add(sub.args.vararg.arg)
                            if sub.args.kwarg:
                                local.add(sub.args.kwarg.arg)
                        elif isinstance(sub, ast.Name):
                            (local if isinstance(sub.ctx, (ast.Store, ast.Del)) else used).add(sub.id)
                        elif isinstance(sub, ast.ExceptHandler) and sub.name:
                            local.add(sub.name)
                        elif isinstance(sub, ast.comprehension):
                            for t in ast.walk(sub.target):
                                if isinstance(t, ast.Name):
                                    local.add(t.id)
            for n in (used - local) - {"self", "cls"}:
                if n in own or n in shared_defs or n in BUILTIN:
                    continue
                # 宿主 import 能直接复用 -> 不算提升候选（由 mixin 自己 import）
                if n in host_defs and n not in host_own:
                    continue
                if n in host_own:
                    out.add(n)
    for n in sorted(out):
        print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
