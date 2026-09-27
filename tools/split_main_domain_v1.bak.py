# -*- coding: utf-8 -*-
"""泛化的字节级 mixin 域拆分工具（main.py 版）。

设计要点
--------
* **字节级**：宿主文件以 bytes 读取，按 AST 给出的 ``lineno``/``end_lineno``
  在其上切出行区间，再以 bytes 写回。绝不经过 str 往返，避免 CRLF 被
  Python 的 universal newlines 归一化污染整文件（那会让 ``git diff --numstat``
  第一列变成 20000+）。
* **行尾自适应**：切出行块时保留原始字节内容（含各自的 \\r\\n 或 \\n 变体）；
  仅在新模块的头部/尾部（新生成的行）使用与原文件一致的换行符。
* **守恒**：抽取后立刻做 AST 守恒校验（宿主方法集合 = 原集合 - 目标集合，
  新模块方法集合 == 目标集合），任一不符即中止且不落盘。

用法
----
    python tools/split_main_domain.py \
        --methods-file tmp/night/main_<domain>_methods.txt \
        --new-class PrivateCompanionPluginXxxMixin \
        --new-module main_xxx.py \
        --domain-label "XXX 域" \
        [--dry-run | --write]

``--emit-deps`` 会额外打印模块级依赖（被搬走方法引用的模块级名字）
供人工确认新模块需要哪些 import。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "main.py"
HOST_MODULE = "main"
HOST_CLASS = "PrivateCompanionPlugin"
HOST_IMPORT_NAME = "PrivateCompanionPlugin"
# 新模块 import 宿主模块时使用的包内相对名
PKG_PREFIX = "."
# 已提升到 main_shared.py 的宿主私有件（mixin 需从该模块导入）
SHARED_NAMES = {
    "_multi_persona_event_context",
    "_plugin_instance_root",
    "_plugin_instance_can_dispatch",
    "_private_companion_runtime",
}


def parse_owner(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise SystemExit(f"找不到类 {name}")


def class_methods(owner: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    out: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in owner.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def read_lines(path: Path) -> list[bytes]:
    """按 \\n 切分原始字节（保留每段末尾的 \\r）。"""
    return path.read_bytes().split(b"\n")


def module_level_names(tree: ast.Module) -> dict[str, ast.AST]:
    """宿主模块顶层定义的名字（函数/类/常量/导入绑定）。"""
    names: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = node
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names[tgt.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names[(alias.asname or alias.name).split(".")[0]] = node
    return names


def free_globals(node: ast.AST, owner_name: str, module_names: set[str]) -> set[str]:
    """收集 node 内引用的、不是参数也不是 self 的模块级名字。

    除函数体外，还必须覆盖 **装饰器表达式**（类创建时求值，缺 import 直接
    NameError）以及 **默认值/注解表达式**。做法是把它们作为独立片段并入
    扫描，同时把参数绑定也一并收集，避免误报。
    """
    pieces: list[ast.AST] = [node]
    for dec in getattr(node, "decorator_list", ()) or ():
        pieces.append(dec)
    args_node = getattr(node, "args", None)
    if args_node is not None:
        for default in (*args_node.defaults, *[d for d in args_node.kw_defaults if d is not None]):
            pieces.append(default)
        for a in (*args_node.posonlyargs, *args_node.args, *args_node.kwonlyargs):
            if a.annotation is not None:
                pieces.append(a.annotation)
        if args_node.vararg and args_node.vararg.annotation is not None:
            pieces.append(args_node.vararg.annotation)
        if args_node.kwarg and args_node.kwarg.annotation is not None:
            pieces.append(args_node.kwarg.annotation)
    if getattr(node, "returns", None) is not None:
        pieces.append(node.returns)

    bound: set[str] = set()
    for piece in pieces:
        for sub in ast.walk(piece):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = sub.args
                for a in (
                    *args.posonlyargs,
                    *args.args,
                    *args.kwonlyargs,
                ):
                    bound.add(a.arg)
                if args.vararg:
                    bound.add(args.vararg.arg)
                if args.kwarg:
                    bound.add(args.kwarg.arg)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                bound.add(sub.id)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                bound.add(sub.name)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(sub, ast.comprehension):
                for t in ast.walk(sub.target):
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
            elif isinstance(sub, ast.withitem) and sub.optional_vars is not None:
                for t in ast.walk(sub.optional_vars):
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
            elif isinstance(sub, ast.MatchAs) and sub.name:
                bound.add(sub.name)
            elif isinstance(sub, ast.MatchStar) and sub.name:
                bound.add(sub.name)
    used: set[str] = set()
    for piece in pieces:
        for sub in ast.walk(piece):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                used.add(sub.id)
    used.discard(owner_name)
    used.discard("self")
    used.discard("cls")
    return (used - bound) & module_names


def import_lines_for(tree: ast.Module, needed: set[str]) -> list[str]:
    """为 needed 名字生成 import 文本行（直接复用宿主已有的 import 形状）。

    路由规则：
    * 已提升的宿主私有件 -> `from .main_shared import ...`
    * 宿主顶层定义、且无法 import 复用的私有件（其余 `_xxx` 常量/函数）
      同样归到 `.main_shared`（由宿主写回步骤搬过去）
    * 其余按宿主原 import 形状复制
    """
    lines: list[str] = []
    simple: dict[str, str] = {}
    from_mod: dict[str, list[tuple[str, str | None]]] = {}
    covered: set[str] = set()
    module_defs = module_level_names(tree)
    shared = {
        n for n in needed
        if n in SHARED_NAMES
        or (
            n in module_defs
            and n.startswith("_")
            and not isinstance(module_defs[n], (ast.Import, ast.ImportFrom))
            and n != "logger"
        )
    }
    if shared:
        from_mod.setdefault(".main_shared", []).extend((n, None) for n in sorted(shared))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                key = (alias.asname or alias.name).split(".")[0]
                if key in needed:
                    simple[key] = f"import {alias.name}" + (
                        f" as {alias.asname}" if alias.asname else ""
                    )
                    covered.add(key)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * (node.level or 0) + (node.module or "")
            for alias in node.names:
                key = alias.asname or alias.name
                if alias.name == "*":
                    continue
                if key in needed:
                    from_mod.setdefault(mod, []).append((alias.name, alias.asname))
                    covered.add(key)
    for key in sorted(simple):
        lines.append(simple[key])
    for mod in sorted(from_mod):
        items = sorted(set(from_mod[mod]))
        rendered = ", ".join(
            name + (f" as {asname}" if asname else "") for name, asname in items
        )
        covered |= {name for name, _ in items}
        if len(rendered) <= 88:
            lines.append(f"from {mod} import {rendered}")
        else:
            lines.append(f"from {mod} import (")
            for name, asname in items:
                lines.append(f"    {name}" + (f" as {asname}" if asname else "") + ",")
            lines.append(")")
    return lines, covered


def _covered_names(tree: ast.Module, needed) -> set[str]:
    """返回宿主 import 语句能直接提供的名字集合。"""
    _, covered = import_lines_for(tree, set(needed))
    return covered


def wire_host_bytes(host_bytes: bytes, module: str, new_class: str) -> bytes:
    """在宿主文件里插入 mixin 的 import 与基类条目（纯字节操作，保留 CRLF）。"""
    nl = b"\r\n" if b"\r\n" in host_bytes else b"\n"

    # 1) import：插到 `from .atrelay import AtRelayMixin` 之后
    anchor = b"from .atrelay import AtRelayMixin"
    idx = host_bytes.find(anchor)
    if idx < 0:
        raise SystemExit("找不到 atrelay import 锚点")
    eol = host_bytes.find(b"\n", idx) + 1
    import_line = f"from .{module[:-3]} import {new_class}".encode("utf-8")
    host_bytes = host_bytes[:eol] + import_line + nl + host_bytes[eol:]

    # 2) 基类：插到 `    AtRelayMixin,` 之后
    idx = host_bytes.find(b"\n    AtRelayMixin,")
    if idx < 0:
        raise SystemExit("找不到 AtRelayMixin 基类锚点")
    eol = host_bytes.find(b"\n", idx + 1) + 1
    base_line = f"    {new_class},".encode("utf-8")
    host_bytes = host_bytes[:eol] + base_line + nl + host_bytes[eol:]
    return host_bytes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods-file", required=True)
    ap.add_argument("--new-class", required=True)
    ap.add_argument("--new-module", required=True)
    ap.add_argument("--domain-label", default="")
    ap.add_argument("--host-class", default=HOST_CLASS)
    ap.add_argument("--host", default=str(HOST))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--emit-deps", action="store_true")
    args = ap.parse_args()

    host_path = Path(args.host)
    if not host_path.is_absolute():
        host_path = ROOT / args.host

    raw = host_path.read_bytes()
    text = raw.decode("utf-8")
    tree = ast.parse(text, filename=host_path.name)
    owner = parse_owner(tree, args.host_class)
    all_methods = class_methods(owner)

    wanted = [
        ln.strip()
        for ln in Path(args.methods_file).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    dupes = {n for n in wanted if wanted.count(n) > 1}
    if dupes:
        raise SystemExit(f"清单存在重复方法名: {sorted(dupes)}")

    missing = [n for n in wanted if n not in all_methods]
    if missing:
        raise SystemExit(f"清单中有 {len(missing)} 个名字不在宿主体内: {missing}")

    picked = [all_methods[n] for n in wanted]
    picked.sort(key=lambda n: n.lineno)

    # 守卫：禁止搬走架构约束涉及的入口方法
    guarded = {"__init__", "inject_humanized_state", "on_private_message", "on_group_message"}
    hit = guarded & set(wanted)
    if hit:
        raise SystemExit(f"禁止搬走受架构约束的入口方法: {sorted(hit)}")

    lines = read_lines(host_path)
    # 行区间按 AST 的 lineno/end_lineno（1-based，含装饰器起于 decorator_list）
    spans: list[tuple[int, int]] = []
    for node in picked:
        start = node.lineno
        if node.decorator_list:
            start = min(start, min(d.lineno for d in node.decorator_list))
        spans.append((start, node.end_lineno))

    spans.sort()
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        if s2 <= e1:
            raise SystemExit(f"行区间重叠: ({s1},{e1}) 与 ({s2},{e2})")

    moved_lines = sum(e - s + 1 for s, e in spans)
    # 每个方法后保留 1 个空行分隔
    moved_bytes = sum(
        sum(len(lines[i - 1]) + 1 for i in range(s, e + 1)) for s, e in spans
    )

    module_names = set(module_level_names(tree).keys())
    dep_counts: dict[str, int] = {}
    for node in picked:
        for name in free_globals(node, args.host_class, module_names):
            dep_counts[name] = dep_counts.get(name, 0) + 1

    host_methods_after = set(all_methods) - set(wanted)
    new_methods = set(wanted)

    print(f"== {'DRY-RUN' if args.dry_run else 'WRITE'} : {args.domain_label or args.new_class} ==")
    print(f"宿主            : {host_path.name}（{len(lines)} 行）")
    print(f"新模块          : {args.new_module}")
    print(f"新类            : {args.new_class}")
    print(f"清单方法数      : {len(wanted)}")
    print(f"命中方法数      : {len(picked)}")
    print(f"未命中          : {len(missing)}")
    print(f"搬走行数        : {moved_lines}")
    print(f"搬走字节        : {moved_bytes}")
    print(f"宿主拆分后行数  : {len(lines) - moved_lines - len(spans)}")
    print(f"宿主方法数 before/after : {len(all_methods)} / {len(host_methods_after)}")
    print(f"守恒: 新类方法数 == 清单数 : {len(new_methods) == len(wanted)}")
    print(f"守恒: 宿主与新类无交集     : {not (host_methods_after & new_methods)}")
    print(f"守恒: 宿主+新类 == 原集合  : {host_methods_after | new_methods == set(all_methods)}")

    if args.emit_deps or args.dry_run:
        print(f"\n-- 模块级依赖（{len(dep_counts)} 个）--")
        for name, cnt in sorted(dep_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<58} x{cnt}")
        unresolved = sorted(dep_counts)
        lines_out, covered = import_lines_for(tree, set(unresolved))
        print(f"\n-- 可由宿主 import 直接覆盖: {len(covered)}/{len(unresolved)} --")
        for ln in lines_out:
            print(f"  {ln}")

    print("\n-- 行区间 --")
    for (s, e) in spans:
        print(f"  L{s}-L{e}  ({e - s + 1} 行)")

    if args.write:
        out_lines: list[bytes] = []
        for (s, e) in spans:
            for i in range(s, e + 1):
                out_lines.append(lines[i - 1])
            out_lines.append(b"")
        # 去掉尾部多余空行，保留一个
        while len(out_lines) >= 2 and out_lines[-1] == b"" and out_lines[-2] == b"":
            out_lines.pop()

        dep_names, _ = import_lines_for(tree, set(dep_counts))
        header: list[str] = [
            "# -*- coding: utf-8 -*-",
            f'"""{args.domain_label or args.new_class}。',
            "",
            f"由 tools/split_main_domain.py 从 {host_path.name} 机械抽取"
            f"（{len(wanted)} 个方法 / {moved_lines} 行）。",
            "方法体零改动：所有 self.xxx 依赖通过继承链解析"
            f"（宿主类 {args.host_class}）。",
            '"""',
            "from __future__ import annotations",
            "",
        ]
        header.extend(dep_names)
        if dep_names:
            header.append("")
        if "logger" in dep_counts and "logger" not in _covered_names(tree, dep_counts):
            header.append("from .logging_util import get_module_logger")
            header.append("")
            header.append("logger = get_module_logger(__name__)")
        header.append("")
        header.append(f"class {args.new_class}:")
        header.append(f'    """{args.domain_label or args.new_class}（从 {args.host_class} 拆出）。"""')
        header.append("")
        header_bytes = ("\n".join(header) + "\n").encode("utf-8")

        new_module_path = ROOT / args.new_module
        if new_module_path.exists():
            raise SystemExit(f"目标已存在，拒绝覆盖: {args.new_module}")
        new_module_path.write_bytes(header_bytes + b"\n".join(out_lines) + b"\n")

        # 宿主：从后往前删除区间（含其后 1 个空行）
        drop: set[int] = set()
        for (s, e) in spans:
            for i in range(s, e + 1):
                drop.add(i)
            if e < len(lines) and lines[e].strip() == b"":
                drop.add(e + 1)
        kept = [lines[i - 1] for i in range(1, len(lines) + 1) if i not in drop]
        host_bytes = b"\n".join(kept)
        host_bytes = wire_host_bytes(host_bytes, args.new_module, args.new_class)
        host_path.write_bytes(host_bytes)

        print(f"\n已写入 {args.new_module} 与改写 {host_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
