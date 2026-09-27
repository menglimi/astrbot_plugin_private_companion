# -*- coding: utf-8 -*-
"""泛化的字节级 mixin 域拆分工具（任意宿主模块版）。

与 `split_main_domain.py` 的关系
--------------------------------
`split_main_domain.py` 是为 `main.py` 硬编码的（锚点写死 `from .atrelay import
AtRelayMixin`、shared 模块写死 `.main_shared`）。本脚本是它的参数化通用版，
用于 `daily_state.py` / `proactive_message.py` 等其它巨型宿主。

设计要点（继承自 main 版，已在上游验证）
----------------------------------------
* **字节级**：宿主以 bytes 读取，按 AST 的 `lineno`/`end_lineno` 切行区间，再以
  bytes 写回。绝不经过 str 往返 —— 否则 universal newlines 会把 CRLF 归一化，
  令 `git diff --numstat` 第一列暴涨到整个文件行数。
* **行尾自适应**：切出的行块保留原字节（含各自的 \\r\\n 或 \\n 变体）；仅在
  新生成的头部行使用与宿主一致的换行符。
* **守恒自检**：抽取后立刻断言「宿主方法集合 == 原集合 - 目标集合」且
  「新模块方法集合 == 目标集合」，任一不符即中止且不落盘。
* **不改方法体**：只搬位置。所有 `self.xxx` 依赖由继承链解析。

用法
----
    python tools/split_mixin_domain.py \\
        --host daily_state.py \\
        --host-class DailyStateMixin \\
        --methods-file ../tmp/refactor/weather_methods.txt \\
        --module-funcs-file ../tmp/refactor/weather_funcs.txt \\
        --new-class DailyStateWeatherMixin \\
        --new-module daily_state_weather.py \\
        --domain-label "天气域" \\
        --import-anchor "from .daily_state_tick import DailyStateTickMixin" \\
        --base-line "class DailyStateMixin(DailyStateTickMixin)" \\
        --base-line-new "class DailyStateMixin(DailyStateTickMixin, DailyStateWeatherMixin)" \\
        [--dry-run | --write]

`--dry-run` 会打印模块级依赖与建议 import 行；`--write` 才落盘。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# AST 工具
# --------------------------------------------------------------------------
def parse_owner(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise SystemExit(f"找不到类 {name}")


def class_methods(owner: ast.ClassDef) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in owner.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


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
    """收集 node 内引用的、既非参数也非 self 的模块级名字。

    必须覆盖装饰器表达式（类创建时求值，缺 import 直接 NameError）与
    默认值/注解表达式，同时把各类绑定（参数、赋值、推导式、with、except、
    match）一并收集以免误报。
    """
    pieces: list[ast.AST] = [node]
    for dec in getattr(node, "decorator_list", ()) or ():
        pieces.append(dec)
    args_node = getattr(node, "args", None)
    if args_node is not None:
        for default in (*args_node.defaults, *(d for d in args_node.kw_defaults if d)):
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
                a_args = sub.args
                for a in (*a_args.posonlyargs, *a_args.args, *a_args.kwonlyargs):
                    bound.add(a.arg)
                if a_args.vararg:
                    bound.add(a_args.vararg.arg)
                if a_args.kwarg:
                    bound.add(a_args.kwarg.arg)
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


# --------------------------------------------------------------------------
# import 生成
# --------------------------------------------------------------------------
def import_lines_for(
    tree: ast.Module,
    needed: set[str],
    *,
    module_funcs: set[str],
) -> tuple[list[str], set[str], set[str]]:
    """按宿主原有 import 形状为 needed 生成 import 行。

    返回 (import 行, 已覆盖的名字, 无法解析的名字)。

    路由：
    * `module_funcs` 里已随本次迁移搬到新模块的名字 -> 不需要 import
    * 其余按宿主原 import 形状复制（`import x` / `from m import a, b`）
    * 宿主顶层定义但**未搬走**的私有件（`_xxx`）会被判为「无法解析」，
      由调用方报错交人工决策（避免静默生成一个指向宿主的循环 import）
    """
    simple: dict[str, str] = {}
    from_mod: dict[str, list[tuple[str, str | None]]] = {}
    covered: set[str] = set()
    module_defs = module_level_names(tree)

    needed = set(needed) - set(module_funcs)

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
                if alias.name == "*":
                    continue
                key = alias.asname or alias.name
                if key in needed:
                    from_mod.setdefault(mod, []).append((alias.name, alias.asname))
                    covered.add(key)

    lines: list[str] = [simple[k] for k in sorted(simple)]
    for mod in sorted(from_mod):
        items = sorted(set(from_mod[mod]))
        rendered = ", ".join(
            name + (f" as {asname}" if asname else "") for name, asname in items
        )
        if len(rendered) <= 88:
            lines.append(f"from {mod} import {rendered}")
        else:
            lines.append(f"from {mod} import (")
            for name, asname in items:
                lines.append(f"    {name}" + (f" as {asname}" if asname else "") + ",")
            lines.append(")")

    unresolved = {
        n
        for n in needed
        if n not in covered
        and n in module_defs
        and not isinstance(module_defs[n], (ast.Import, ast.ImportFrom))
        # logger 由调用方在头部生成 `get_module_logger(__name__)`，不算漏搬。
        and n != "logger"
    }
    return lines, covered, unresolved


# --------------------------------------------------------------------------
# 宿主改写
# --------------------------------------------------------------------------
def wire_host_bytes(
    host_bytes: bytes,
    *,
    new_module: str,
    new_class: str,
    import_anchor: str,
    base_line: str,
    base_line_new: str,
) -> bytes:
    """在宿主文件里插入新 mixin 的 import 与基类条目（纯字节操作，保留行尾）。"""
    nl = b"\r\n" if b"\r\n" in host_bytes else b"\n"

    anchor = import_anchor.encode("utf-8")
    idx = host_bytes.find(anchor)
    if idx < 0:
        raise SystemExit(f"找不到 import 锚点: {import_anchor!r}")
    eol = host_bytes.find(b"\n", idx) + 1
    import_line = f"from .{new_module[:-3]} import {new_class}".encode("utf-8")
    host_bytes = host_bytes[:eol] + import_line + nl + host_bytes[eol:]

    old_base = base_line.encode("utf-8")
    idx = host_bytes.find(old_base)
    if idx < 0:
        raise SystemExit(f"找不到基类行: {base_line!r}")
    host_bytes = (
        host_bytes[:idx] + base_line_new.encode("utf-8") + host_bytes[idx + len(old_base):]
    )
    return host_bytes


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def read_lines(path: Path) -> list[bytes]:
    """按 \\n 切分原始字节（保留每段末尾的 \\r）。"""
    return path.read_bytes().split(b"\n")


def load_listing(path_str: str) -> list[str]:
    p = Path(path_str)
    if not p.is_absolute():
        p = ROOT / p
    return [
        ln.strip()
        for ln in p.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="宿主模块文件名，如 daily_state.py")
    ap.add_argument("--host-class", required=True, help="宿主类名，如 DailyStateMixin")
    ap.add_argument("--methods-file", required=True, help="要搬走的方法名清单")
    ap.add_argument("--module-funcs-file", default="", help="要一并搬走的模块级函数名清单")
    ap.add_argument(
        "--class-attrs-file",
        default="",
        help="要一并搬走的类级赋值名清单（如 URL 构造器别名）",
    )
    ap.add_argument("--new-class", required=True)
    ap.add_argument("--new-module", required=True)
    ap.add_argument("--domain-label", default="")
    ap.add_argument("--import-anchor", required=True, help="在该行之后插入新 mixin 的 import")
    ap.add_argument("--base-line", required=True, help="宿主原基类行原文")
    ap.add_argument("--base-line-new", required=True, help="替换后的基类行")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = ap.parse_args()

    host_path = Path(args.host)
    if not host_path.is_absolute():
        host_path = ROOT / args.host
    if not host_path.exists():
        raise SystemExit(f"宿主不存在: {host_path}")

    raw = host_path.read_bytes()
    tree = ast.parse(raw.decode("utf-8"), filename=host_path.name)
    owner = parse_owner(tree, args.host_class)
    all_methods = class_methods(owner)

    wanted = load_listing(args.methods_file)
    dupes = {n for n in wanted if wanted.count(n) > 1}
    if dupes:
        raise SystemExit(f"清单存在重复方法名: {sorted(dupes)}")
    missing = [n for n in wanted if n not in all_methods]
    if missing:
        raise SystemExit(f"清单中有 {len(missing)} 个名字不在 {args.host_class} 内: {missing}")

    funcs_wanted = load_listing(args.module_funcs_file) if args.module_funcs_file else []
    module_defs = module_level_names(tree)
    funcs_missing = [n for n in funcs_wanted if n not in module_defs]
    if funcs_missing:
        raise SystemExit(f"清单中有 {len(funcs_missing)} 个模块级名字不存在: {funcs_missing}")

    # 类级赋值（`_build_x = _build_y` 这类别名）可能引用被搬走的方法名。
    # 类体在创建时求值，父类属性对它不可见 —— 若不随之搬走，直接 NameError。
    attrs_wanted = load_listing(args.class_attrs_file) if args.class_attrs_file else []
    attr_nodes: dict[str, ast.AST] = {}
    for node in owner.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in attrs_wanted:
                    attr_nodes[tgt.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in attrs_wanted:
                attr_nodes[node.target.id] = node
    attrs_missing = [n for n in attrs_wanted if n not in attr_nodes]
    if attrs_missing:
        raise SystemExit(
            f"清单中有 {len(attrs_missing)} 个类级赋值不在 {args.host_class} 内: {attrs_missing}"
        )

    picked = [all_methods[n] for n in wanted]
    picked.sort(key=lambda n: n.lineno)

    # 守卫：禁止搬走构造/入口方法
    guarded = {"__init__", "_tick", "_ensure_daily_state"}
    hit = guarded & set(wanted)
    if hit:
        raise SystemExit(f"禁止搬走受约束的编排/入口方法: {sorted(hit)}")

    lines = read_lines(host_path)

    def docstring_end(node: ast.AST) -> int:
        """方法体内 docstring 的结束行号（若无 docstring 返回 def 行号）。

        前导 `#` 注释可能是**方法自身 docstring 的正文**被错误重排到 `def`
        之前的结果（上游大量方法如此）。只有「docstring 未被前导注释块完整
        覆盖」时，那些注释才真正属于本方法 —— 否则回退会导致注释被复制两遍。
        """
        body = getattr(node, "body", None) or []
        if not body:
            return node.lineno
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            return first.end_lineno
        return node.lineno

    def span_of(node: ast.AST) -> tuple[int, int]:
        start = node.lineno
        decorators = getattr(node, "decorator_list", None) or ()
        if decorators:
            start = min(start, min(d.lineno for d in decorators))
        # 向上吸收紧邻的前导注释块（连续 `#` 行）：注释在语义上属于该成员，
        # 若只按 lineno 切片会把它留在宿主里形成"孤儿注释"（实测会发生）。
        # 只吸收「顶到 docstring 或装饰器为止」的块：再多就会吃掉上一个成员
        # 的尾部空行/注释边界。
        floor = docstring_end(node)
        while start - 2 >= 0 and start - 1 > floor:
            prev = lines[start - 2].strip()
            if prev.startswith(b"#"):
                start -= 1
            else:
                break
        return start, node.end_lineno

    spans: list[tuple[int, int]] = [span_of(n) for n in picked]
    func_spans: list[tuple[int, int]] = [span_of(module_defs[n]) for n in funcs_wanted]
    attr_spans: list[tuple[int, int]] = [span_of(attr_nodes[n]) for n in attrs_wanted]
    all_spans = sorted(spans + func_spans + attr_spans)

    for (s1, e1), (s2, e2) in zip(all_spans, all_spans[1:]):
        if s2 <= e1:
            raise SystemExit(f"行区间重叠: ({s1},{e1}) 与 ({s2},{e2})")

    moved_lines = sum(e - s + 1 for s, e in all_spans)
    host_methods_after = set(all_methods) - set(wanted)
    new_methods = set(wanted)

    # 依赖统计：搬走的类方法 + 搬走的模块级函数
    module_names = set(module_defs)
    dep_counts: dict[str, int] = {}
    for node in picked + [module_defs[n] for n in funcs_wanted] + [
        attr_nodes[n] for n in attrs_wanted
    ]:
        for name in free_globals(node, args.host_class, module_names):
            dep_counts[name] = dep_counts.get(name, 0) + 1

    print(f"== {'DRY-RUN' if args.dry_run else 'WRITE'} : {args.domain_label or args.new_class} ==")
    print(f"宿主            : {host_path.name}（{len(lines)} 行）")
    print(f"新模块          : {args.new_module}")
    print(f"新类            : {args.new_class}")
    print(f"类方法 清单/命中: {len(wanted)} / {len(picked)}")
    print(f"模块级函数搬走  : {len(funcs_wanted)} {funcs_wanted if funcs_wanted else ''}")
    print(f"类级赋值搬走    : {len(attrs_wanted)} {attrs_wanted if attrs_wanted else ''}")
    print(f"搬走行数合计    : {moved_lines}")
    print(f"宿主拆分后行数  : {len(lines) - moved_lines - len(all_spans)}")
    print(f"宿主方法数 before/after : {len(all_methods)} / {len(host_methods_after)}")
    print(f"守恒: 新类方法数 == 清单数 : {len(new_methods) == len(wanted)}")
    print(f"守恒: 宿主与新类无交集     : {not (host_methods_after & new_methods)}")
    print(f"守恒: 宿主+新类 == 原集合  : {host_methods_after | new_methods == set(all_methods)}")

    import_lines, covered, unresolved = import_lines_for(
        tree, set(dep_counts), module_funcs=set(funcs_wanted)
    )
    print(f"\n-- 模块级依赖（{len(dep_counts)} 个）--")
    for name, cnt in sorted(dep_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        marker = "  [随本次搬走]" if name in funcs_wanted else ""
        print(f"  {name:<58} x{cnt}{marker}")

    print(f"\n-- 可由宿主 import 覆盖: {len(covered)}/{len(dep_counts)} --")
    for ln in import_lines:
        print(f"  {ln}")

    if unresolved:
        print(f"\n!! 无法解析的宿主私有件（{len(unresolved)} 个）—— 需人工决策 !!")
        for n in sorted(unresolved):
            print(f"   {n}")
        print("   这些名字在宿主顶层定义但未随本次搬走。若确属本域，")
        print("   请加入 --module-funcs-file；否则需在新模块回导入或提升共享模块。")

    # ---- 残留裸引用检查（防"漏搬"，本轮实测踩到过） ----
    # 扫描宿主里**未被搬走**的行，看是否还存在对已搬走名字的裸 `Load` 引用。
    # 能同时抓到两类漏搬：
    #   1) 类级别名 `_build_now = _build_weather` —— 类体创建时求值，父类不可见
    #   2) 剩余代码裸引用已搬走的模块级函数/常量
    # 注意 `self._xxx()` 是 Attribute 而非 Name，不会被误报（继承链能解析）。
    moved_names = set(wanted) | set(funcs_wanted) | set(attrs_wanted)
    # 注意：这个集合**不能叫 moved_lines** —— 上面 L372 的 `moved_lines` 是
    # 行数（int），docstring 模板会用它。曾因同名覆盖导致生成的模块 docstring
    # 里打印出一个 set 字面量 `{5699, 5293, ...}`。
    moved_line_set: set[int] = set()
    for s, e in all_spans:
        moved_line_set.update(range(s, e + 1))

    residual: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        if node.id not in moved_names:
            continue
        if node.lineno in moved_line_set:
            continue
        residual.setdefault(node.id, []).append(f"L{node.lineno}")

    # ---- 宿主类名硬引用检查（本轮 meal 域实测踩到） ----
    # 形如 `HostClass.some_method(x)` 的**类上调用**。方法搬到新模块后，
    # `HostClass` 这个名字在新模块里不存在 → 运行到即 NameError。
    # 处置：改写成 `NewClass.some_method(x)`。安全的充要条件是 some_method
    # 也在本次搬运集合内（同一新模块的类上能解析到它）；否则新类上也找不到，
    # 必须人工决策（例如改成 self.xxx 或把该方法一并纳入本域）。
    host_refs: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == args.host_class
            and node.lineno in moved_line_set
        ):
            host_refs.setdefault(node.attr, []).append(node.lineno)
    bad_host_refs = {a: locs for a, locs in host_refs.items() if a not in moved_names}
    host_ref_lines = {ln for locs in host_refs.values() for ln in locs}

    print("\n-- 宿主类名硬引用检查 --")
    if not host_refs:
        print(f"   无（搬走的代码里没有 `{args.host_class}.xxx` 形式的类上调用）")
    else:
        for attr, locs in sorted(host_refs.items()):
            mark = "改写" if attr in moved_names else "!! 无法自动处置"
            print(f"   {args.host_class}.{attr:<44} {locs[:4]}  [{mark}]")
        if bad_host_refs:
            print("   以下被引用的方法不在本次搬运集合内，改写成新类名也无法解析：")
            for attr in sorted(bad_host_refs):
                print(f"      {attr}")
            print("   处置：把它加入本域清单，或把调用点改为 self.xxx。")

    print(f"\n-- 宿主残留裸引用检查 --")
    if residual:
        print(f"!! 发现 {len(residual)} 个已搬走名字仍被宿主裸引用 —— 必然 NameError !!")
        for name, locs in sorted(residual.items()):
            print(f"   {name:<48} {locs[:6]}")
        print("   处置：类级赋值加进 --class-attrs-file；模块级名字加进 --module-funcs-file。")
    else:
        print("   无残留（宿主里没有任何未搬走代码裸引用已搬走的名字）")

    print("\n-- 行区间 --")
    for s, e in all_spans:
        if (s, e) in func_spans:
            kind = "func"
        elif (s, e) in attr_spans:
            kind = "attr"
        else:
            kind = "meth"
        print(f"  L{s}-L{e}  ({e - s + 1} 行) [{kind}]")

    if args.dry_run:
        print("\n[DRY-RUN] 未落盘。")
        return 0

    if unresolved:
        raise SystemExit("存在无法解析的依赖，拒绝落盘（先解决上面的 unresolved）。")
    if residual:
        raise SystemExit(
            "宿主仍裸引用已搬走的名字，拒绝落盘（否则运行到即 NameError）："
            f"{sorted(residual)}"
        )
    if bad_host_refs:
        raise SystemExit(
            "搬走的代码里有无法自动处置的宿主类名硬引用，拒绝落盘："
            f"{sorted(bad_host_refs)}（把被引用的方法加入本域清单，或改调用点为 self.xxx）"
        )

    # ---- 组装新模块 ----
    # 关键：模块级成员（缩进 0）必须放在 `class` 声明**之前**，类成员（缩进 4）
    # 必须在其后。若把两者按行号混排在同一序列里，缩进 0 的模块级函数会终结
    # 类体，紧随其后的类方法会被解析成前一个函数的函数体（缩进同为 4）——
    # 实测会让 78 个方法整体"消失"进一个函数里。
    host_ref_re = re.compile(
        rb"\b" + re.escape(args.host_class.encode("utf-8")) + rb"\."
    )
    new_class_bytes = args.new_class.encode("utf-8") + b"."

    module_out: list[bytes] = []
    class_out: list[bytes] = []
    for s, e in all_spans:
        bucket = module_out if (s, e) in func_spans else class_out
        for i in range(s, e + 1):
            raw = lines[i - 1]
            # 只改写**已确认**含类上调用的行，避免波及无关行（含字符串/注释）
            if i in host_ref_lines:
                raw = host_ref_re.sub(new_class_bytes, raw)
            bucket.append(raw)
        bucket.append(b"")
    for bucket in (module_out, class_out):
        while len(bucket) >= 2 and bucket[-1] == b"" and bucket[-2] == b"":
            bucket.pop()

    # header_top 必须整体排在最前（`from __future__` 只允许出现在文件开头），
    # 之后才是模块级成员，再之后是类声明 + 类成员。
    header_top: list[str] = [
        "# -*- coding: utf-8 -*-",
        f'"""{args.domain_label or args.new_class}。',
        "",
        f"由 tools/split_mixin_domain.py 从 {host_path.name} 机械抽取"
        f"（{len(wanted)} 个方法 + {len(funcs_wanted)} 个模块级名字"
        f" + {len(attrs_wanted)} 个类级赋值 / {moved_lines} 行）。",
        "方法体零改动：所有 self.xxx 依赖通过继承链解析"
        f"（宿主类 {args.host_class}）。",
        '"""',
        "from __future__ import annotations",
        "",
    ]
    header_top.extend(import_lines)
    if import_lines:
        header_top.append("")
    if "logger" in dep_counts and "logger" not in covered:
        header_top.append("from .logging_util import get_module_logger")
        header_top.append("")
        header_top.append("logger = get_module_logger(__name__)")
        header_top.append("")
    header_top.append("")

    class_decl: list[str] = [
        f"class {args.new_class}:",
        f'    """{args.domain_label or args.new_class}（从 {args.host_class} 拆出）。"""',
        "",
    ]

    def render(chunk: list[str] | list[bytes]) -> bytes:
        if chunk and isinstance(chunk[0], str):
            return ("\n".join(chunk) + "\n").encode("utf-8")
        return b"\n".join(chunk) + b"\n"

    parts: list[bytes] = [render(header_top)]
    if module_out:
        parts.append(b"\n" + render(module_out))
    parts.append(b"\n" + render(class_decl))
    parts.append(b"\n" + render(class_out))
    payload = b"".join(parts)

    new_module_path = ROOT / args.new_module
    if new_module_path.exists():
        raise SystemExit(f"目标已存在，拒绝覆盖: {args.new_module}")

    # 装配自检：解析内存字节，确认目标类体里真的落了预期数量的方法。
    # 这一步专门防"装配顺序/缩进"事故 —— 缩进 0 的模块级函数若排在类声明
    # 之后，会终结类体并把后续类方法吞进自己的函数体，而 AST 守恒校验当时
    # 只看宿主侧，不会发现。
    try:
        probe = ast.parse(bytes(payload), filename=args.new_module)
    except SyntaxError as exc:
        raise SystemExit(f"装配自检失败（新模块语法错误）: {exc}")
    new_cls = next(
        (n for n in probe.body if isinstance(n, ast.ClassDef) and n.name == args.new_class),
        None,
    )
    if new_cls is None:
        raise SystemExit("装配自检失败：新模块里找不到目标类")
    landed = {
        n.name
        for n in new_cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if landed != set(wanted):
        raise SystemExit(
            f"装配自检失败：{args.new_class} 类体方法数 {len(landed)} != 期望 {len(wanted)}；"
            f"缺 {sorted(set(wanted) - landed)[:5]} 多 {sorted(landed - set(wanted))[:5]}"
        )
    landed_attrs = {
        t.id
        for n in new_cls.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    } | {
        n.target.id
        for n in new_cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    }
    if not set(attrs_wanted) <= landed_attrs:
        raise SystemExit(
            f"装配自检失败：类级赋值未落入类体: {sorted(set(attrs_wanted) - landed_attrs)}"
        )
    print(f"装配自检通过：{args.new_class} 含 {len(landed)} 个方法、"
          f"{len(landed_attrs)} 个类级赋值")

    new_module_path.write_bytes(bytes(payload))

    # ---- 改写宿主 ----
    drop: set[int] = set()
    for s, e in all_spans:
        for i in range(s, e + 1):
            drop.add(i)
        if e < len(lines) and lines[e].strip() == b"":
            drop.add(e + 1)
    kept = [lines[i - 1] for i in range(1, len(lines) + 1) if i not in drop]
    host_bytes = b"\n".join(kept)
    host_bytes = wire_host_bytes(
        host_bytes,
        new_module=args.new_module,
        new_class=args.new_class,
        import_anchor=args.import_anchor,
        base_line=args.base_line,
        base_line_new=args.base_line_new,
    )
    host_path.write_bytes(host_bytes)

    print(f"\n已写入 {args.new_module} 与改写 {host_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
