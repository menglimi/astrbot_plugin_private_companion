# -*- coding: utf-8 -*-
"""泛化的字节级 mixin 域拆分工具（main.py 版）— v2。

v2 相对 v1 修的五个坑
--------------------
坑 1  模块级全局不搬
      ``--emit-deps`` 新增「需手动提升到 main_shared.py」分节，逐个给出
      名字 / 种类 / 宿主行号 / 是否已在 main_shared / 是否已在宿主的
      main_shared import 列表里。``--auto-promote`` 可自动搬家：
      从宿主字节级删除定义 → 追加进 main_shared.py → 在宿主的
      ``from .main_shared import (`` 列表里补名。全部字节级。

坑 2  依赖分析不深入 try/except
      模块级名字收集现在会**递归下钻** ``Try`` / ``TryStar`` / ``If`` /
      ``With`` / ``For`` / ``While`` 的 body / handlers / orelse /
      finalbody，把多层兜底块定义的名字（Plain / Reply / calendar_cn /
      Converter / Solar ...）全部收进来。生成新模块 import 时，
      **照抄宿主的兜底结构原文**（字节级切片），不再退化成
      ``from x import Plain, Reply``。

坑 3  目标模块已存在就 SystemExit
      加 ``--force``：允许覆盖；dry-run 下只提示。

坑 4  不查宿主类名裸引用
      搬走方法体里的 ``PrivateCompanionPlugin._moved(self, ...)`` 自动改写为
      ``<new_class>._moved(self, ...)``。**保留非绑定调用语义**：只替换
      类名 token，第一个实参 ``self,`` 原样保留，绝不改成 ``self._moved``
      （测试常用只继承部分 mixin 的桩对象做非绑定调用，改 self. 会炸）。
      未搬走的 ``HostClass._other(...)`` 与裸 ``HostClass`` 只告警不改写。

坑 5  写盘必须字节级
      宿主一律 ``read_bytes()`` → ``split(b"\\n")`` → ``b"".join`` 式组装，
      绝不经过 str 往返。v2 追加 ``audit_byte_conservation()`` 硬校验：
      删掉插入行后，after 的行序列必须与「宿主原行 − 删除行」**逐字节相等**，
      否则拒绝落盘。

用法
----
    python tools/split_main_domain_v2.py \\
        --methods-file tmp/refactor/main_<domain>_methods.txt \\
        --new-class PrivateCompanionPluginXxxMixin \\
        --new-module main_xxx.py \\
        --domain-label "XXX 域" \\
        [--dry-run | --write] [--emit-deps] [--force] [--auto-promote]
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "main.py"
HOST_CLASS = "PrivateCompanionPlugin"
# 已提升到 main_shared.py 的宿主私有件（即使宿主里还有同名定义也强制路由到 shared）
SHARED_NAMES = {
    "_multi_persona_event_context",
    "_plugin_instance_root",
    "_plugin_instance_can_dispatch",
    "_private_companion_runtime",
}
# 不该被当成「模块级依赖」的名字
BUILTINS = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__",
    "__debug__", "__class__", "__module__", "__qualname__", "__dict__",
    "self", "cls",
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "dict", "dir",
    "enumerate", "float", "format", "frozenset", "getattr",
    "hasattr", "hash", "int", "isinstance", "issubclass", "iter", "len",
    "list", "map", "max", "min", "next", "object", "open", "ord", "pow",
    "print", "range", "repr", "reversed", "round", "set", "setattr", "slice",
    "sorted", "staticmethod", "classmethod", "property", "str", "sum",
    "super", "tuple", "type", "vars", "zip", "Exception", "BaseException",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "RuntimeError", "OSError", "FileNotFoundError", "ImportError",
    "ModuleNotFoundError", "StopIteration", "NotImplementedError",
    # 注意：`filter` 与 `Any` **不能**列在这里。
    # `filter` 在 AstrBot 里是被 import 覆盖的事件过滤器（@filter.on_llm_request），
    # `Any` 来自 typing；一旦当 builtin 排除，新模块就会漏 import，
    # 类体求值时报 `type object 'filter' has no attribute 'on_llm_request'`。
    # 让它们走正常依赖分析即可：宿主有对应 import 就生成，没有就落到内置语义。
}

CONTAINERS = tuple(
    n for n in (
        ast.Try,
        getattr(ast, "TryStar", None),
        ast.If,
        ast.With,
        getattr(ast, "AsyncWith", None),
        ast.For,
        getattr(ast, "AsyncFor", None),
        ast.While,
    ) if n is not None
)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
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
    """按 \\n 切分原始字节（保留每段末尾的 \\r）—— 字节级前提。"""
    return path.read_bytes().split(b"\n")


def detect_nl(raw: bytes) -> bytes:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return b"\r\n" if crlf >= lf else b"\n"


def norm_line(b: bytes, nl: bytes) -> bytes:
    """把一行的行尾统一成 nl（去掉残留的 \\r）。"""
    return b[:-1] if b.endswith(b"\r") else b


def slice_stmt_bytes(raw_lines: list[bytes], node: ast.AST, nl: bytes) -> bytes:
    """按 AST 行号从原始字节里切出一条语句的原文（保留内容，行尾归一到 nl）。"""
    seg = [norm_line(raw_lines[i - 1], nl) for i in range(node.lineno, node.end_lineno + 1)]
    return nl.join(seg)


# --------------------------------------------------------------------------
# 坑 2：模块级名字收集（下钻 try/except 兜底块）
# --------------------------------------------------------------------------
@dataclass
class ModName:
    name: str
    kind: str                 # import | func | class | assign
    provider: ast.AST         # 宿主里提供该名字的**顶层语句**
    lineno: int
    end_lineno: int
    fallback: bool = False    # provider 是 Try/If/... 兜底块 → 需照抄原文
    mod: str | None = None    # import 的模块名（含相对点）
    orig: str | None = None   # import 原名
    asname: str | None = None


def _subbodies(node: ast.AST) -> list[list[ast.AST]]:
    out: list[list[ast.AST]] = []
    for attr in ("body", "orelse", "finalbody"):
        b = getattr(node, attr, None)
        if isinstance(b, list):
            out.append(b)
    for h in getattr(node, "handlers", []) or []:
        out.append(h.body)
    return out


def collect_module_names(tree: ast.Module) -> dict[str, ModName]:
    """宿主模块顶层可引用名字。v2：递归下钻兜底块（坑 2）。"""
    names: dict[str, ModName] = {}

    def bind(name: str, kind: str, provider: ast.AST, node: ast.AST,
             fallback: bool, mod: str | None = None,
             orig: str | None = None, asname: str | None = None) -> None:
        if not name or name in names:
            return
        names[name] = ModName(
            name=name, kind=kind, provider=provider,
            lineno=getattr(node, "lineno", getattr(provider, "lineno", 0)),
            end_lineno=getattr(node, "end_lineno", getattr(provider, "end_lineno", 0)),
            fallback=fallback, mod=mod, orig=orig, asname=asname,
        )

    def emit(node: ast.AST, provider: ast.AST, fallback: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bind(node.name, "func", provider, node, fallback)
        elif isinstance(node, ast.ClassDef):
            bind(node.name, "class", provider, node, fallback)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    bind(tgt.id, "assign", provider, node, fallback)
                elif isinstance(tgt, ast.Tuple):
                    for e in tgt.elts:
                        if isinstance(e, ast.Name):
                            bind(e.id, "assign", provider, node, fallback)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bind(node.target.id, "assign", provider, node, fallback)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bind((alias.asname or alias.name).split(".")[0], "import",
                     provider, node, fallback, mod=alias.name,
                     orig=alias.name, asname=alias.asname)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * (node.level or 0) + (node.module or "")
            for alias in node.names:
                if alias.name == "*":
                    continue
                bind(alias.asname or alias.name, "import", provider, node,
                     fallback, mod=mod, orig=alias.name, asname=alias.asname)

    def walk(stmts: list[ast.AST], container: ast.AST | None) -> None:
        """container = 直接包裹这些语句的兜底块；None 表示真正的顶层。

        provider 取**最内层**包裹块：照抄时只搬最小必要结构
        （如 Reply 只抄内层 try，而不是外层十几行的 try）。
        """
        for node in stmts:
            if isinstance(node, CONTAINERS):
                for sub in _subbodies(node):
                    walk(sub, node)
            else:
                emit(node, node if container is None else container,
                     container is not None)

    walk(tree.body, None)
    return names


# --------------------------------------------------------------------------
# 引用分析
# --------------------------------------------------------------------------
def _pieces(node: ast.AST) -> list[ast.AST]:
    """方法体 + 装饰器 + 默认值/注解/返回值注解。"""
    out: list[ast.AST] = [node]
    for dec in getattr(node, "decorator_list", ()) or ():
        out.append(dec)
    args_node = getattr(node, "args", None)
    if args_node is not None:
        for default in (*args_node.defaults,
                        *[d for d in args_node.kw_defaults if d is not None]):
            out.append(default)
        for a in (*args_node.posonlyargs, *args_node.args, *args_node.kwonlyargs):
            if a.annotation is not None:
                out.append(a.annotation)
        if args_node.vararg and args_node.vararg.annotation is not None:
            out.append(args_node.vararg.annotation)
        if args_node.kwarg and args_node.kwarg.annotation is not None:
            out.append(args_node.kwarg.annotation)
    if getattr(node, "returns", None) is not None:
        out.append(node.returns)
    return out


def bound_names(node: ast.AST) -> set[str]:
    bound: set[str] = set()
    for piece in _pieces(node):
        for sub in ast.walk(piece):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = sub.args
                for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
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
    return bound


def load_names(node: ast.AST) -> set[str]:
    used: set[str] = set()
    for piece in _pieces(node):
        for sub in ast.walk(piece):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                used.add(sub.id)
    return used


def free_globals(node: ast.AST, module_names: set[str]) -> set[str]:
    """方法体引用的模块级自由名字（**保留** 宿主类名，供坑 4 单独处理）。"""
    return (load_names(node) - bound_names(node) - BUILTINS) & module_names


# --------------------------------------------------------------------------
# 坑 4：宿主类名裸引用改写规划
# --------------------------------------------------------------------------
@dataclass
class ClassRefPlan:
    rewrite: list[tuple[int, int, bytes, bytes]] = field(default_factory=list)
    cross_moved_out: list[tuple[str, int]] = field(default_factory=list)
    bare: list[int] = field(default_factory=list)


def plan_class_refs(node: ast.AST, host_class: str, moved: set[str],
                    new_class: str) -> ClassRefPlan:
    """把 ``HostClass._moved(...)`` 改写为 ``NewClass._moved(...)``。

    只替换类名 token 本身：第一个实参 ``self,`` 原样保留 → 非绑定调用语义不变。
    """
    plan = ClassRefPlan()
    old_token = host_class.encode("utf-8")
    new_bytes = new_class.encode("utf-8")
    # 作为 Attribute.value 出现的 Name 不算「裸引用」（那是 HostClass.xxx，已分类处理）
    as_attr_value = {
        id(n.value)
        for n in ast.walk(node)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                and sub.value.id == host_class:
            if sub.attr in moved:
                plan.rewrite.append(
                    (sub.value.lineno, sub.value.col_offset, old_token, new_bytes)
                )
            else:
                plan.cross_moved_out.append((sub.attr, sub.value.lineno))
        elif isinstance(sub, ast.Name) and sub.id == host_class \
                and isinstance(sub.ctx, ast.Load) and id(sub) not in as_attr_value:
            plan.bare.append(sub.lineno)
    return plan


def apply_byte_edits(buf: list[bytes], start_lineno: int,
                     edits: list[tuple[int, int, bytes, bytes]]) -> int:
    """在已切出的字节行上按 (lineno, col, old_token, new_token) 就地替换。"""
    if not edits:
        return 0
    by_line: dict[int, list[tuple[int, bytes, bytes]]] = {}
    for lineno, col, old_token, new_token in edits:
        by_line.setdefault(lineno, []).append((col, old_token, new_token))
    n = 0
    for lineno, es in by_line.items():
        idx = lineno - start_lineno
        if idx < 0 or idx >= len(buf):
            continue
        line = buf[idx]
        for col, old_token, new_token in sorted(es, key=lambda t: t[0], reverse=True):
            if line[col:col + len(old_token)] != old_token:
                raise SystemExit(
                    f"坑4 改写失配：第 {lineno} 行 col={col} 处不是宿主类名"
                )
            line = line[:col] + new_token + line[col + len(old_token):]
            n += 1
        buf[idx] = line
    return n


# --------------------------------------------------------------------------
# import 生成计划（含兜底块照抄）
# --------------------------------------------------------------------------
@dataclass
class ImportPlan:
    lines: list[str] = field(default_factory=list)
    fallback_blocks: list[tuple[ast.AST, list[str]]] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)


def build_import_plan(names: dict[str, ModName], needed: set[str],
                      shared_import: str) -> ImportPlan:
    plan = ImportPlan()
    simple: dict[str, str] = {}
    from_mod: dict[str, list[tuple[str, str | None]]] = {}
    fb: dict[int, tuple[ast.AST, list[str]]] = {}

    for n in sorted(needed):
        m = names.get(n)
        if m is None:
            plan.unknown.append(n)
            continue
        # logger 走专用绑定
        if n == "logger" and m.kind == "assign":
            plan.covered.add(n)
            continue
        if m.fallback or (m.kind == "import" and isinstance(m.provider, CONTAINERS)):
            key = id(m.provider)
            if key not in fb:
                fb[key] = (m.provider, [])
            fb[key][1].append(n)
            plan.covered.add(n)
            continue
        if m.kind == "import" and n not in SHARED_NAMES:
            if isinstance(m.provider, ast.Import):
                simple[n] = f"import {m.orig}" + (f" as {m.asname}" if m.asname else "")
            else:
                from_mod.setdefault(m.mod or "", []).append((m.orig or n, m.asname))
            plan.covered.add(n)
            continue
        if m.kind == "import" and n in SHARED_NAMES:
            from_mod.setdefault("." + shared_import, []).append((n, None))
            plan.covered.add(n)
            continue
        # func / class / assign：宿主私有件 → main_shared（坑 1）
        plan.shared.append(n)
        from_mod.setdefault("." + shared_import, []).append((n, None))
        plan.covered.add(n)

    for key in sorted(simple):
        plan.lines.append(simple[key])
    for mod in sorted(from_mod):
        items = sorted(set(from_mod[mod]))
        rendered = ", ".join(name + (f" as {asname}" if asname else "")
                             for name, asname in items)
        if len(rendered) <= 88:
            plan.lines.append(f"from {mod} import {rendered}")
        else:
            plan.lines.append(f"from {mod} import (")
            for name, asname in items:
                plan.lines.append(f"    {name}" + (f" as {asname}" if asname else "") + ",")
            plan.lines.append(")")
    plan.fallback_blocks = [v for _, v in sorted(fb.items(), key=lambda kv: kv[1][0].lineno)]
    return plan


def render_fallback_block(raw_lines: list[bytes], node: ast.AST, nl: bytes) -> list[str]:
    return slice_stmt_bytes(raw_lines, node, nl).decode("utf-8").split("\n")


# --------------------------------------------------------------------------
# 坑 5：字节级硬校验
# --------------------------------------------------------------------------
def audit_byte_conservation(before_lines: list[bytes], after_lines: list[bytes],
                            dropped_idx: set[int]) -> tuple[bool, str, list[bytes]]:
    """after 去掉插入行后必须逐字节等于「before − 删除行」。"""
    kept = [l for i, l in enumerate(before_lines, 1) if i not in dropped_idx]
    j = 0
    inserted: list[bytes] = []
    for line in after_lines:
        if j < len(kept) and line == kept[j]:
            j += 1
        else:
            inserted.append(line)
    if j != len(kept):
        return False, f"字节守恒失败：只顺序匹配上 {j}/{len(kept)} 行", inserted
    return True, f"OK（保留 {len(kept)} 行，新增 {len(inserted)} 行）", inserted


# --------------------------------------------------------------------------
# 宿主 / shared 字节级改写
# --------------------------------------------------------------------------
def wire_host_bytes(host_bytes: bytes, module: str, new_class: str,
                    import_anchor: str, base_anchor: str) -> tuple[bytes, list[bytes]]:
    """在宿主里插入 mixin 的 import 与基类条目（纯字节操作，保留 CRLF）。"""
    nl = detect_nl(host_bytes)
    inserted: list[bytes] = []

    import_line = f"from .{module[:-3]} import {new_class}".encode("utf-8")
    anchor = import_anchor.encode("utf-8")
    idx = host_bytes.find(b"\n" + anchor)
    if idx < 0:
        idx = host_bytes.find(anchor)
        if idx < 0:
            raise SystemExit(f"找不到 import 锚点: {import_anchor!r}")
        idx -= 1
    eol = host_bytes.find(b"\n", idx + 1) + 1
    host_bytes = host_bytes[:eol] + import_line + nl + host_bytes[eol:]
    inserted.append(import_line)

    base_line = f"    {new_class},".encode("utf-8")
    banchor = base_anchor.encode("utf-8")
    idx = host_bytes.find(b"\n" + banchor)
    if idx < 0:
        raise SystemExit(f"找不到基类锚点: {base_anchor!r}")
    # 锚点行自己的行尾可能是 CRLF —— 不能假设其后紧跟着 b"\n"
    after = idx + 1 + len(banchor)
    eol = host_bytes.find(b"\n", after) + 1
    if eol <= 0:
        raise SystemExit(f"基类锚点后找不到行尾: {base_anchor!r}")
    host_bytes = host_bytes[:eol] + base_line + nl + host_bytes[eol:]
    inserted.append(base_line)
    return host_bytes, inserted


def shared_import_names(host_bytes: bytes, shared_import: str) -> set[str]:
    """读宿主 ``from .<shared_import> import (...)`` 里已导入的名字。"""
    text = host_bytes.decode("utf-8", "replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and \
                ("." * (node.level or 0) + (node.module or "")) == "." + shared_import:
            for alias in node.names:
                out.add(alias.asname or alias.name)
    return out


def add_shared_imports(host_bytes: bytes, names: list[str],
                       shared_import: str) -> tuple[bytes, list[bytes]]:
    """在宿主的 ``from .<shared> import (`` 列表末尾补名（字节级，末尾追加不重排）。"""
    if not names:
        return host_bytes, []
    nl = detect_nl(host_bytes)
    anchor = f"from .{shared_import} import (".encode("utf-8")
    i = host_bytes.find(anchor)
    if i >= 0:
        end = host_bytes.find(b"\n)", i)
        if end < 0:
            raise SystemExit("main_shared import 块没有找到闭合的 ')' 行")
        # 插入点取「上一行行尾 \n 之后」，避免和上一行自带的 \r 拼成 \r\r\n
        pos = end + 1
        ins = [f"    {n},".encode("utf-8") for n in names]
        block = nl.join(ins) + nl
        return host_bytes[:pos] + block + host_bytes[pos:], ins
    # 单行形式：整块替换成多行
    single = f"from .{shared_import} import ".encode("utf-8")
    j = host_bytes.find(single)
    if j < 0:
        raise SystemExit(f"找不到 {shared_import} import 语句，无法补充导入")
    eol = host_bytes.find(b"\n", j)
    old = host_bytes[j:eol]
    existing = [x.strip() for x in old[len(single):].split(b",") if x.strip()]
    allnames = [x.decode() for x in existing] + names
    block = (f"from .{shared_import} import (".encode("utf-8") + nl
             + nl.join(f"    {n},".encode("utf-8") for n in allnames) + nl + b")")
    return host_bytes[:j] + block + host_bytes[eol:], \
        [f"    {n},".encode("utf-8") for n in names]


def insert_imports_into_shared(shared_bytes: bytes, lines: list[str]) -> bytes:
    """把 import 行插到 main_shared.py 的 ``from __future__`` 之后（字节级）。"""
    if not lines:
        return shared_bytes
    nl = detect_nl(shared_bytes)
    anchor = b"from __future__ import annotations"
    i = shared_bytes.find(anchor)
    if i >= 0:
        eol = shared_bytes.find(b"\n", i) + 1
    else:
        # 退化：插到模块 docstring 之后（第三个 b'"""'）
        k = -1
        for _ in range(3):
            k = shared_bytes.find(b'"""', k + 1)
            if k < 0:
                break
        eol = shared_bytes.find(b"\n", k) + 1 if k >= 0 else 0
    block = nl.join(l.encode("utf-8") for l in lines) + nl
    return shared_bytes[:eol] + block + shared_bytes[eol:]


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods-file", required=True)
    ap.add_argument("--new-class", required=True)
    ap.add_argument("--new-module", required=True)
    ap.add_argument("--domain-label", default="")
    ap.add_argument("--host-class", default=HOST_CLASS)
    ap.add_argument("--host", default=str(HOST))
    ap.add_argument("--out-dir", default=str(ROOT),
                    help="新模块落盘目录（默认仓库根；自测可指向 scratch）")
    ap.add_argument("--shared-module", default=str(ROOT / "main_shared.py"))
    ap.add_argument("--shared-import", default="main_shared")
    ap.add_argument("--new-module-nl", choices=("auto", "crlf", "lf"), default="auto")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--emit-deps", action="store_true")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的目标模块")
    ap.add_argument("--auto-promote", action="store_true",
                    help="把需提升的宿主模块级定义自动搬进 main_shared.py")
    ap.add_argument("--promote-deps", dest="promote_deps", action="store_true", default=True)
    ap.add_argument("--no-promote-deps", dest="promote_deps", action="store_false")
    ap.add_argument("--import-anchor", default="from .atrelay import AtRelayMixin")
    ap.add_argument("--base-anchor", default="    AtRelayMixin,")
    ap.add_argument("--deps-json", default="", help="把依赖分析写成 JSON 到该路径")
    args = ap.parse_args()

    host_path = Path(args.host)
    if not host_path.is_absolute():
        host_path = ROOT / args.host
    out_dir = Path(args.out_dir)
    new_module_path = out_dir / args.new_module
    shared_path = Path(args.shared_module)

    raw = host_path.read_bytes()
    text = raw.decode("utf-8")
    nl = detect_nl(raw)
    if args.new_module_nl == "crlf":
        mod_nl = b"\r\n"
    elif args.new_module_nl == "lf":
        mod_nl = b"\n"
    else:
        mod_nl = nl

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
        raise SystemExit(
            f"清单中有 {len(missing)} 个名字不在宿主体内: {missing[:20]}"
            f"{' ...' if len(missing) > 20 else ''}"
        )

    # 附加守卫：宿主类里同名方法定义多次时，按名字取会静默漏搬第一个
    dup_methods = {
        n for n in set(all_methods)
        if sum(1 for node in owner.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == n) > 1
    }
    if dup_methods:
        print(f"[WARN] 宿主类内存在重名方法定义: {sorted(dup_methods)}"
              f"（本次清单未涉及则无影响）")
    dup_hit = dup_methods & set(wanted)
    if dup_hit:
        raise SystemExit(
            f"清单命中重名方法 {sorted(dup_hit)}：按名字定位会漏搬，"
            f"请先把其中之一改名或手工搬运"
        )

    picked = [all_methods[n] for n in wanted]
    picked.sort(key=lambda n: n.lineno)
    moved_names = set(wanted)

    guarded = {"__init__", "inject_humanized_state", "on_private_message", "on_group_message"}
    hit = guarded & moved_names
    if hit:
        raise SystemExit(f"禁止搬走受架构约束的入口方法: {sorted(hit)}")

    raw_lines = raw.split(b"\n")
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

    moved_line_count = sum(e - s + 1 for s, e in spans)
    moved_bytes = sum(sum(len(raw_lines[i - 1]) + 1 for i in range(s, e + 1))
                      for s, e in spans)

    # ---- 依赖分析 ----
    mod_names = collect_module_names(tree)
    module_name_set = set(mod_names)
    dep_counts: dict[str, int] = {}
    class_rewrites: list[tuple[int, int, bytes, bytes]] = []
    cross_refs: list[tuple[str, str, int]] = []
    bare_refs: list[tuple[str, int]] = []
    for node in picked:
        for name in free_globals(node, module_name_set):
            # 宿主类名本身不是「模块级依赖」：它由坑 4 的逻辑处理，
            # 绝不能进 main_shared（否则会把宿主类整个搬走）。
            if name == args.host_class:
                continue
            dep_counts[name] = dep_counts.get(name, 0) + 1
        plan = plan_class_refs(node, args.host_class, moved_names, args.new_class)
        class_rewrites.extend(plan.rewrite)
        cross_refs.extend((node.name, a, ln) for a, ln in plan.cross_moved_out)
        bare_refs.extend((node.name, ln) for ln in plan.bare)

    needed = set(dep_counts)
    plan = build_import_plan(mod_names, needed, args.shared_import)
    need_promote = [n for n in plan.shared]
    shared_bytes = shared_path.read_bytes() if shared_path.exists() else b""
    already_shared = set()
    if shared_bytes:
        try:
            st = ast.parse(shared_bytes.decode("utf-8", "replace"))
            already_shared = set(collect_module_names(st))
        except SyntaxError:
            already_shared = set()
    host_shared_imported = shared_import_names(raw, args.shared_import)

    host_methods_after = set(all_methods) - moved_names

    # ---- 报告头 ----
    print(f"== {'DRY-RUN' if args.dry_run else 'WRITE'} : {args.domain_label or args.new_class} ==")
    # f-string 表达式内不能出现反斜杠（Python 3.11 限制，PEP 701 才放行），先提取变量。
    crlf_count = raw.count(b"\r\n")
    lf_count = raw.count(b"\n") - crlf_count
    print(f"宿主            : {host_path}（{len(raw_lines) - 1} 行, "
          f"CRLF={crlf_count}, LF={lf_count}）")
    print(f"新模块          : {new_module_path}"
          f"{'（已存在，--force 将覆盖）' if new_module_path.exists() else ''}")
    print(f"新类            : {args.new_class}")
    print(f"清单方法数      : {len(wanted)}")
    print(f"命中方法数      : {len(picked)}")
    print(f"未命中          : {len(missing)}")
    print(f"搬走行数        : {moved_line_count}")
    print(f"搬走字节        : {moved_bytes}")
    print(f"宿主拆分后行数  : {len(raw_lines) - 1 - moved_line_count - len(spans)}")
    print(f"宿主方法数 before/after : {len(all_methods)} / {len(host_methods_after)}")
    print(f"守恒: 新类方法数 == 清单数 : {len(moved_names) == len(wanted)}")
    print(f"守恒: 宿主与新类无交集     : {not (host_methods_after & moved_names)}")
    print(f"守恒: 宿主+新类 == 原集合  : {host_methods_after | moved_names == set(all_methods)}")

    # ---- 坑 4 报告 ----
    print(f"\n-- 宿主类裸引用改写（坑 4）--")
    print(f"  {args.host_class}.<已搬走方法> -> {args.new_class}.<同名> : "
          f"{len(class_rewrites)} 处（保留非绑定调用，self 实参不动）")
    if cross_refs:
        print(f"  [WARN] {args.host_class}.<未搬走方法> : {len(cross_refs)} 处 "
              f"（新模块内会 NameError，需人工改走 self./宿主代理）")
        for m, a, ln in cross_refs[:10]:
            print(f"          L{ln}  {m}: {args.host_class}.{a}")
        if len(cross_refs) > 10:
            print(f"          ... 共 {len(cross_refs)} 处")
    if bare_refs:
        print(f"  [WARN] 裸 {args.host_class} 引用 : {len(bare_refs)} 处")
        for m, ln in bare_refs[:10]:
            print(f"          L{ln}  {m}")
        if len(bare_refs) > 10:
            print(f"          ... 共 {len(bare_refs)} 处")

    if args.emit_deps or args.dry_run:
        print(f"\n-- 模块级依赖（{len(dep_counts)} 个）--")
        for name, cnt in sorted(dep_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            tag = mod_names[name].kind if name in mod_names else "?"
            fb = " [兜底块]" if name in mod_names and mod_names[name].fallback else ""
            print(f"  {name:<56} x{cnt:<4} {tag}{fb}")

        print(f"\n-- A. 可由宿主 import 直接覆盖: {len(plan.covered)}/{len(needed)} --")
        for ln in plan.lines:
            print(f"  {ln}")

        print(f"\n-- B. 兜底块依赖（照抄宿主 try/except 结构，坑 2）: "
              f"{len(plan.fallback_blocks)} 块 --")
        for node, names_in in plan.fallback_blocks:
            print(f"  # 宿主 L{node.lineno}-L{node.end_lineno} "
                  f"({type(node).__name__}) -> {', '.join(sorted(names_in))}")
            for seg in render_fallback_block(raw_lines, node, mod_nl):
                print(f"  | {seg}")

        print(f"\n-- C. 需手动提升到 {args.shared_module} 的宿主私有件（坑 1）: "
              f"{len(need_promote)} 个 --")
        if need_promote:
            for n in need_promote:
                m = mod_names[n]
                state = []
                if n in already_shared:
                    state.append("已在 main_shared")
                if n in host_shared_imported:
                    state.append("宿主已 import")
                if n in SHARED_NAMES:
                    state.append("SHARED_NAMES 命中")
                print(f"  {n:<50} {m.kind:<7} L{m.lineno}-L{m.end_lineno}"
                      + (f"   [{'; '.join(state)}]" if state else "   [需提升]"))
        if plan.unknown:
            print(f"\n-- D. 无法在宿主模块级解析: {len(plan.unknown)} 个 --")
            for n in plan.unknown:
                print(f"  {n}")

    if args.deps_json:
        payload = {
            "host": str(host_path),
            "domain_label": args.domain_label,
            "new_class": args.new_class,
            "new_module": args.new_module,
            "methods": wanted,
            "deps": {k: v for k, v in sorted(dep_counts.items(), key=lambda kv: (-kv[1], kv[0]))},
            "import_lines": plan.lines,
            "fallback_blocks": [
                {"lineno": n.lineno, "end_lineno": n.end_lineno,
                 "names": sorted(names_in),
                 "source": slice_stmt_bytes(raw_lines, n, mod_nl).decode("utf-8")}
                for n, names_in in plan.fallback_blocks
            ],
            "need_promote": [
                {"name": n, "kind": mod_names[n].kind,
                 "lineno": mod_names[n].lineno, "end_lineno": mod_names[n].end_lineno,
                 "already_in_shared": n in already_shared,
                 "host_already_imports": n in host_shared_imported}
                for n in need_promote
            ],
            "class_rewrite_count": len(class_rewrites),
            "cross_refs": [{"method": m, "attr": a, "lineno": ln} for m, a, ln in cross_refs],
            "bare_refs": [{"method": m, "lineno": ln} for m, ln in bare_refs],
            "conservation": {
                "new_equals_wanted": len(moved_names) == len(wanted),
                "disjoint": not (host_methods_after & moved_names),
                "union_equals_original": host_methods_after | moved_names == set(all_methods),
            },
        }
        Path(args.deps_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n依赖 JSON 已写入: {args.deps_json}")

    print("\n-- 行区间 --")
    for (s, e) in spans:
        print(f"  L{s}-L{e}  ({e - s + 1} 行)")

    if args.dry_run:
        print("\n（dry-run：未落盘）")
        return 0

    # ==================== WRITE ====================
    if new_module_path.exists() and not args.force:
        raise SystemExit(
            f"目标已存在，拒绝覆盖: {new_module_path}"
            f"（加 --force 允许覆盖）"
        )
    if new_module_path.exists():
        print(f"[--force] 覆盖已存在的 {new_module_path}")

    # ---- 新模块体（含坑 4 改写）----
    out_lines: list[bytes] = []
    rewrite_applied = 0
    for (s, e) in spans:
        buf = [norm_line(raw_lines[i - 1], mod_nl) for i in range(s, e + 1)]
        edits = [t for t in class_rewrites if s <= t[0] <= e]
        rewrite_applied += apply_byte_edits(buf, s, edits)
        out_lines.extend(buf)
        out_lines.append(b"")
    while len(out_lines) >= 2 and out_lines[-1] == b"" and out_lines[-2] == b"":
        out_lines.pop()
    print(f"[坑4] 已改写 {rewrite_applied} 处宿主类名裸引用")

    # ---- 坑 1：自动提升（只登记删除区间 + 备好 shared 内容，宿主写盘统一在最后）----
    dropped_idx: set[int] = set()
    promote_add_names: list[str] = []
    if args.auto_promote and need_promote:
        promote_targets = [n for n in need_promote if n not in already_shared]
        if promote_targets:
            # 1) 登记要从宿主删除的定义行
            for n in promote_targets:
                m = mod_names[n]
                for i in range(m.lineno, m.end_lineno + 1):
                    dropped_idx.add(i)
                if m.end_lineno < len(raw_lines) - 1 and \
                        raw_lines[m.end_lineno].strip() == b"":
                    dropped_idx.add(m.end_lineno + 1)
            # 2) 记录宿主需要补进 main_shared import 列表的名字
            promote_add_names = [n for n in promote_targets
                                 if n not in host_shared_imported]
            print(f"[坑1] 提升 {len(promote_targets)} 个私有件到 {args.shared_module}；"
                  f"宿主补 import {len(promote_add_names)} 个")
            # 3) shared 落盘
            shared_nl = detect_nl(shared_bytes) if shared_bytes else nl
            chunks: list[bytes] = []
            if shared_bytes and not shared_bytes.endswith(b"\n"):
                chunks.append(shared_nl)
            chunks.append(shared_nl)
            chunks.append(f"# --- 由 tools/split_main_domain_v2.py --auto-promote 提升 "
                          f"({args.domain_label or args.new_class}) ---".encode("utf-8"))
            chunks.append(shared_nl)
            extra_needed: set[str] = set()
            for n in promote_targets:
                m = mod_names[n]
                node = m.provider if m.kind in ("func", "class") and \
                    isinstance(m.provider, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    else None
                if node is not None and args.promote_deps:
                    extra_needed |= free_globals(node, module_name_set) - {n}
                # 定义本体（字节级原文，含其原始缩进与行尾）
                chunks.append(slice_stmt_bytes(raw_lines, m, shared_nl))
                chunks.append(shared_nl)
            # 本轮一起提升的名字、以及 shared 里已有的，都不必再 import
            extra_needed -= set(promote_targets) | already_shared | host_shared_imported
            if extra_needed:
                print(f"[坑1] 被提升件自身依赖: {sorted(extra_needed)}")
            if extra_needed and args.promote_deps:
                sub = build_import_plan(mod_names, extra_needed - already_shared,
                                        args.shared_import)
                add_lines = [
                    l for l in sub.lines
                    if l.encode("utf-8") not in shared_bytes
                    # 避免 shared 自导入
                    and not l.startswith(f"from .{args.shared_import} import")
                ]
                for blk, _names in sub.fallback_blocks:
                    add_lines.extend(
                        render_fallback_block(raw_lines, blk, shared_nl)
                    )
                if add_lines:
                    shared_bytes_final = insert_imports_into_shared(shared_bytes, add_lines)
                    print(f"[坑1] 为 shared 补 {len(add_lines)} 行依赖 import")
                else:
                    shared_bytes_final = shared_bytes
            else:
                shared_bytes_final = shared_bytes
            if not shared_bytes_final:
                shared_bytes_final = b"# -*- coding: utf-8 -*-\n"
            shared_bytes_final = shared_bytes_final + b"".join(chunks)
            shared_path.parent.mkdir(parents=True, exist_ok=True)
            shared_path.write_bytes(shared_bytes_final)
            print(f"[坑1] 已写回 {shared_path}")
        else:
            print("[坑1] 需提升的私有件已全部在 main_shared 中，跳过")
    elif need_promote:
        print(f"[坑1][WARN] 有 {len(need_promote)} 个宿主私有件需手动提升，"
              f"未加 --auto-promote，新模块会 NameError")

    # ---- 新模块头部 ----
    header: list[str] = [
        "# -*- coding: utf-8 -*-",
        f'"""{args.domain_label or args.new_class}。',
        "",
        f"由 tools/split_main_domain_v2.py 从 {host_path.name} 机械抽取"
        f"（{len(wanted)} 个方法 / {moved_line_count} 行）。",
        "方法体零改动：所有 self.xxx 依赖通过继承链解析"
        f"（宿主类 {args.host_class}）。",
        '"""',
        "from __future__ import annotations",
        "",
    ]
    header.extend(plan.lines)
    if plan.lines:
        header.append("")
    for blk, _names in plan.fallback_blocks:
        header.extend(render_fallback_block(raw_lines, blk, mod_nl))
        header.append("")
    if "logger" in dep_counts:
        header.append("from .logging_util import get_module_logger")
        header.append("")
        header.append("logger = get_module_logger(__name__)")
        header.append("")
    header.append(f"class {args.new_class}:")
    header.append(f'    """{args.domain_label or args.new_class}（从 {args.host_class} 拆出）。"""')
    header.append("")
    header_bytes = (mod_nl.decode("utf-8").join(header) + mod_nl.decode("utf-8")).encode("utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    new_module_path.write_bytes(
        header_bytes + mod_nl.join(out_lines) + mod_nl
    )

    # ---- 宿主：一次性删除「提升的定义 + 方法区间」，再统一插入 ----
    for (s, e) in spans:
        for i in range(s, e + 1):
            dropped_idx.add(i)
        if e < len(raw_lines) - 1 and raw_lines[e].strip() == b"":
            dropped_idx.add(e + 1)
    kept = [l for i, l in enumerate(raw_lines, 1) if i not in dropped_idx]
    host_bytes = b"\n".join(kept)
    host_bytes, _ = add_shared_imports(host_bytes, promote_add_names, args.shared_import)
    host_bytes, inserted = wire_host_bytes(
        host_bytes, args.new_module, args.new_class,
        args.import_anchor, args.base_anchor,
    )

    # ---- 坑 5：落盘前硬校验 ----
    ok, msg, ins = audit_byte_conservation(raw_lines, host_bytes.split(b"\n"), dropped_idx)
    print(f"[坑5] 字节守恒审计: {msg}")
    if not ok:
        raise SystemExit("拒绝落盘：宿主改写破坏了字节守恒（行尾被归一化？）")
    crlf_before = raw.count(b"\r\n")
    crlf_after = host_bytes.count(b"\r\n")
    print(f"[坑5] CRLF {crlf_before} -> {crlf_after}（差值应为删除行的 CRLF 数）")

    host_path.write_bytes(host_bytes)
    print(f"\n已写入 {new_module_path} 与改写 {host_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
