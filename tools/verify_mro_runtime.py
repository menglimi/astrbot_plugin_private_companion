# -*- coding: utf-8 -*-
"""运行时 MRO 验证：确认 mixin 真正进入 PrivateCompanionPlugin 的 MRO，
且被搬走的方法全部可在实例上解析。

用 AstrBot 解释器跑：
    "H:/AstrBot/backend/python/python.exe" tools/verify_mro_runtime.py <repo_dir> [<mixin_class> ...]
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path


def main() -> int:
    repo = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo.parent))

    pkg = repo.name
    mod = importlib.import_module(f"{pkg}.main")
    plugin_cls = mod.PrivateCompanionPlugin
    mro_names = [c.__name__ for c in plugin_cls.__mro__]
    print(f"插件类 : {plugin_cls.__name__}")
    print(f"MRO 长度: {len(mro_names)}")

    wanted = list(sys.argv[2:])
    if not wanted:
        wanted = [
            n
            for n in mro_names
            if n != plugin_cls.__name__ and n.startswith("PrivateCompanionPlugin")
            or n.endswith("Mixin")
        ]

    ok = True
    for name in wanted:
        if name not in mro_names:
            ok = False
            print(f"  [FAIL] {name} 不在 MRO 中")
            continue
        owner = next(c for c in plugin_cls.__mro__ if c.__name__ == name)
        methods = sorted(
            k
            for k in vars(owner)
            if not k.startswith("__") or k in {"__init__"}
        )
        print(f"  [ok]   {name} 位于 MRO#{mro_names.index(name)}，自有方法 {len(methods)} 个")

    # 抽样：每个 `main_*.py` 模块里定义的方法，必须能在插件类上解析到
    missing: list[str] = []
    for path in sorted(repo.glob("main_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    resolved = getattr(plugin_cls, item.name, None)
                    if resolved is None:
                        missing.append(f"{path.name}::{node.name}.{item.name}")
                    elif getattr(resolved, "__qualname__", "").split(".")[0] != node.name:
                        # 被宿主同名覆盖时此处会暴露
                        missing.append(
                            f"{path.name}::{node.name}.{item.name} 被 "
                            f"{getattr(resolved, '__qualname__', '?')} 覆盖"
                        )
    if missing:
        ok = False
        print(f"\n[FAIL] {len(missing)} 个方法无法从插件类解析:")
        for m in missing[:30]:
            print(f"   {m}")
    else:
        print(f"\n[ok] 全部域 mixin 方法均可从 {plugin_cls.__name__} 解析")

    print("MRO: PASS" if ok else "MRO: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
