# -*- coding: utf-8 -*-
"""巨型模块拆分的跨模块 AST 源码定位辅助。

背景
----
`main.py` 与 `page_api.py` 都曾是被拆分的巨型宿主模块。拆分后，宿主类
``PrivateCompanionPlugin`` / ``PrivateCompanionPageApi`` 通过多继承把方法体留在
各自的域 mixin 模块里（``main_req041.py``、``main_prompt.py``、
``page_api_persona.py`` …）。

大量既有测试用「读宿主单文件 → 找 ClassDef → 取方法」的方式做源码级断言。
拆分后这类断言会因为方法不在宿主文件里而 KeyError / StopIteration。

本模块提供统一的「宿主 + 各域模块」聚合扫描，让这些测试改一行即可继续工作，
且**不改变断言语义**：仍然逐字比对方法源码，只是扩大查找范围。

用法
----
    from module_source_index import main_sources, find_method, find_class

    # 拿到宿主 + 全部域模块路径（按宿主优先排序）
    for path in main_sources(ROOT):
        ...

    # 直接定位方法定义节点（宿主优先，找不到再找域模块）
    node = find_method(ROOT, "main", "PrivateCompanionPlugin", "_req041_migration_source_files")
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Iterator

# 宿主前缀 -> 该宿主拆分出的域模块 glob 前缀
_HOST_SCOPE = {
    "main": ("main.py", "main_*.py"),
    "page_api": ("page_api.py", "page_api_*.py"),
    "daily_state": ("daily_state.py", "daily_state_*.py"),
    "proactive_message": ("proactive_message.py", "proactive_message_*.py"),
    "proactive_engine": ("proactive_engine.py", "proactive_engine_*.py"),
    "message_pipeline": ("message_pipeline.py", "message_pipeline_*.py"),
    "content_companion": ("content_companion.py", "content_companion_*.py"),
    "user_memory": ("user_memory.py", "user_memory_*.py"),
    "llm_tool_actions": ("llm_tool_actions.py", "llm_tool_actions_*.py"),
    "private_image": ("private_image.py", "private_image_*.py"),
    "token_budget": ("token_budget.py", "token_budget_*.py"),
    "core_store": ("core_store.py", "core_store_*.py"),
    "group_wakeup": ("group_wakeup.py", "group_wakeup_*.py"),
    "group_observation": ("group_observation.py", "group_observation_*.py"),
    "event_dispatch": ("event_dispatch.py", "event_dispatch_*.py"),
    # 特例：proactive 的域模块前缀是 proactive_core_，而非 proactive_。
    # 若写成 proactive_*.py 会误吞 proactive_message_*.py / proactive_engine_*.py，
    # 把另外两个已注册宿主族的模块混进来。
    "proactive": ("proactive.py", "proactive_core_*.py"),
}


def _scope_patterns(root: Path, host: str) -> tuple[str, ...]:
    try:
        return _HOST_SCOPE[host]
    except KeyError as exc:  # pragma: no cover - 防御性
        raise ValueError(
            f"未知宿主 {host!r}，可选：{sorted(_HOST_SCOPE)}"
        ) from exc


def host_sources(root: Path, host: str = "main") -> list[Path]:
    """返回宿主与其全部域模块的路径，宿主排第一，其余按文件名排序。

    只返回真实存在的文件，避免拆分尚未完成时误报。
    """
    root = Path(root)
    patterns = _scope_patterns(root, host)
    paths: list[Path] = []
    for index, pattern in enumerate(patterns):
        found = sorted(root.glob(pattern))
        if index == 0:
            # 宿主优先：确保它在最前面
            paths.extend(found)
        else:
            paths.extend(found)
    # 去重且保序
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


# 兼容更贴近领域命名的调用写法
def main_sources(root: Path) -> list[Path]:
    return host_sources(root, "main")


def page_api_sources(root: Path) -> list[Path]:
    return host_sources(root, "page_api")


def daily_state_sources(root: Path) -> list[Path]:
    return host_sources(root, "daily_state")


def proactive_message_sources(root: Path) -> list[Path]:
    return host_sources(root, "proactive_message")


def proactive_engine_sources(root: Path) -> list[Path]:
    return host_sources(root, "proactive_engine")


def message_pipeline_sources(root: Path) -> list[Path]:
    return host_sources(root, "message_pipeline")


def content_companion_sources(root: Path) -> list[Path]:
    return host_sources(root, "content_companion")


def user_memory_sources(root: Path) -> list[Path]:
    return host_sources(root, "user_memory")


def llm_tool_actions_sources(root: Path) -> list[Path]:
    return host_sources(root, "llm_tool_actions")


def private_image_sources(root: Path) -> list[Path]:
    return host_sources(root, "private_image")


def iter_module_sources(root: Path, host: str = "main") -> Iterator[tuple[Path, ast.Module]]:
    """逐个产出 (路径, 已解析 AST)，跳过语法不可解析或读取失败的文件。"""
    for path in host_sources(root, host):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        yield path, tree


def find_class(
    root: Path,
    host: str,
    class_name: str,
) -> ast.ClassDef | None:
    """在宿主 + 各域模块中定位类定义，返回首个命中。"""
    for _path, tree in iter_module_sources(root, host):
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
    return None


def _accept_class(name: str, concrete: str) -> bool:
    """判断某个类名是否属于目标宿主族。

    命中规则（任一成立即可）：
    - 与具体宿主类名完全相同（如 ``PrivateCompanionPlugin``）；
    - 以具体宿主类名开头（如 ``PrivateCompanionPluginReq041Mixin``）；
    - 宿主类名以 ``Mixin`` 结尾时，接受「宿主名去 Mixin 后缀 + 任意域前缀 + Mixin」
      （如 ``UserMemoryMixin`` -> ``UserMemoryExpressionRuleMixin``）。

    这样既能在宿主类体里找，也能在各域 mixin 类体里找。
    """
    if name == concrete or name.startswith(concrete):
        return True
    if concrete.endswith("Mixin") and len(concrete) > len("Mixin"):
        stem = concrete[: -len("Mixin")]
        return name.startswith(stem) and name.endswith("Mixin")
    return False


# 公开别名：供测试直接复用同一套「宿主族」判定规则
class_matches_host = _accept_class


def class_body_defs(
    root: Path,
    host: str,
    class_name: str,
) -> list[ast.stmt]:
    """聚合「宿主类 + 其全部域 mixin 类」的类体语句。

    返回顺序：宿主类体在前（若存在），随后按文件名排序的各域 mixin 类体。
    用于替换测试里 ``owner.body`` 这种单类体遍历。
    """
    defs: list[ast.stmt] = []
    for _path, tree in iter_module_sources(root, host):
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not _accept_class(node.name, class_name):
                continue
            defs.extend(node.body)
    return defs


def iter_class_methods(
    root: Path,
    host: str,
    class_name: str,
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """逐个产出 (方法节点, 所在模块类名)。"""
    for path, tree in iter_module_sources(root, host):
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not _accept_class(node.name, class_name):
                continue
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, node.name


def find_method(
    root: Path,
    host: str,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """在宿主类与其全部域 mixin 类体中定位类方法定义，返回首个命中。"""
    for child, _owner in iter_class_methods(root, host, class_name):
        if child.name == method_name:
            return child
    # 兜底：跨模块自由函数 / 嵌套定义
    for _path, tree in iter_module_sources(root, host):
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            ):
                return node
    return None


def find_methods(
    root: Path,
    host: str,
    class_name: str,
    names: Iterable[str],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """批量定位，任一缺失即抛 KeyError（保留原测试的失败语义）。"""
    result: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    missing: list[str] = []
    for name in names:
        node = find_method(root, host, class_name, name)
        if node is None:
            missing.append(name)
            continue
        result[name] = node
    if missing:
        raise KeyError(missing)
    return result


def sources_for_file(root: Path, filename: str) -> list[Path]:
    """给定「宿主文件名」，返回其所属宿主族的全部模块（宿主优先）。

    - ``filename`` 命中已注册宿主（如 ``core_store.py``）→ 返回 ``host_sources``。
    - 未注册（如 ``page_api_settings.py``）→ 退化为「该文件 + 同名前缀的 ``X_*.py``」，
      这样即使宿主族没有登记进 ``_HOST_SCOPE``，跨域聚合仍然生效。
    """
    root = Path(root)
    path = root / filename
    stem = Path(filename).stem
    for host, (first, _pattern) in _HOST_SCOPE.items():
        if Path(first).stem == stem:
            return host_sources(root, host)
    family: list[Path] = []
    if path.exists():
        family.append(path)
    for candidate in sorted(root.glob(f"{stem}_*.py")):
        if candidate not in family:
            family.append(candidate)
    return family


def class_body_defs_for_file(root: Path, filename: str, class_name: str) -> list[ast.stmt]:
    """``class_body_defs`` 的按文件名版本，作为测试里 ``owner.body`` 的直接替代。

    调用点原先是「读单个宿主文件 → 找 ClassDef → 遍历 ``owner.body``」。拆分后
    方法搬到域模块里，只读宿主会漏掉。本函数按 ``sources_for_file`` 聚合整族，
    逐字返回同样的语句节点，**断言语义不变**。
    """
    defs: list[ast.stmt] = []
    for path in sources_for_file(root, filename):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and _accept_class(node.name, class_name):
                defs.extend(node.body)
    return defs


def file_family_source_text(root: Path, filename: str) -> str:
    """``sources_for_file`` 对应文件的源码全文拼接（宿主优先，按文件名排序）。

    与 ``host_source_text`` 的区别：后者按**注册宿主**取族，本函数按**具体文件名**
    取族。凡是「在某个拆分过的模块里找字符串 / 计数 / 切片」的断言，都应改用本函数。
    """
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sources_for_file(root, filename)
    )


def module_level_functions(
    root: Path,
    host: str,
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """逐个产出宿主族里的**模块级**函数定义：(节点, 所在模块文件名)。"""
    for path, tree in iter_module_sources(root, host):
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield node, path.name


def find_module_function(
    root: Path,
    host: str,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """在宿主族里定位模块级函数；找不到再兜底扫嵌套定义。"""
    for node, _src in module_level_functions(root, host):
        if node.name == name:
            return node
    for _path, tree in iter_module_sources(root, host):
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ):
                return node
    return None


def class_body_span(node: ast.ClassDef) -> int:
    """类体行数（含装饰器与签名），用于架构边界断言。"""
    return node.end_lineno - node.lineno + 1


def method_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """方法行数（含装饰器与 def 行），用于架构边界断言。"""
    return node.end_lineno - node.lineno + 1


def host_source_text(root: Path, host: str = "main") -> str:
    """宿主类所在模块族的源码全文拼接。

    方法按域拆到 ``main_*.py`` / ``page_api_*.py`` 后，只读宿主单文件会漏掉已
    搬走的实现。凡是「在源码里找某个字符串/片段」的断言，都应改用本函数。
    """
    return "\n".join(
        path.read_text(encoding="utf-8") for path in host_sources(root, host)
    )


def main_source_text(root: Path) -> str:
    """``host_source_text(root, "main")`` 的便捷别名。"""
    return host_source_text(root, "main")


def page_api_source_text(root: Path) -> str:
    """``host_source_text(root, "page_api")`` 的便捷别名。"""
    return host_source_text(root, "page_api")


def daily_state_source_text(root: Path) -> str:
    """``host_source_text(root, "daily_state")`` 的便捷别名。"""
    return host_source_text(root, "daily_state")


def proactive_message_source_text(root: Path) -> str:
    """``host_source_text(root, "proactive_message")`` 的便捷别名。"""
    return host_source_text(root, "proactive_message")


def proactive_engine_source_text(root: Path) -> str:
    """``host_source_text(root, "proactive_engine")`` 的便捷别名。"""
    return host_source_text(root, "proactive_engine")


def user_memory_source_text(root: Path) -> str:
    """``host_source_text(root, "user_memory")`` 的便捷别名。"""
    return host_source_text(root, "user_memory")


def llm_tool_actions_source_text(root: Path) -> str:
    """``host_source_text(root, "llm_tool_actions")`` 的便捷别名。"""
    return host_source_text(root, "llm_tool_actions")


def private_image_source_text(root: Path) -> str:
    """``host_source_text(root, "private_image")`` 的便捷别名。"""
    return host_source_text(root, "private_image")


def user_memory_mixin_tree(root: Path) -> ast.Module:
    """合成一棵只含 ``UserMemoryMixin`` 的 AST，类体聚合了宿主 + 全部域 mixin。

    ``user_memory.py`` 拆分后，既有测试里
    ``ast.parse((ROOT / "user_memory.py").read_text())`` 的写法会漏掉已搬走的方法。
    本函数返回一个形状兼容的 ``ast.Module``，使得后续
    ``next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "UserMemoryMixin")``
    以及 ``owner.body`` 遍历**无需任何改动**即可覆盖整个模块族。
    """
    return ast.Module(
        body=[
            ast.ClassDef(
                name="UserMemoryMixin",
                bases=[],
                keywords=[],
                body=class_body_defs(root, "user_memory", "UserMemoryMixin"),
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )


def llm_tool_actions_mixin_tree(root: Path) -> ast.Module:
    """合成一棵只含 ``LlmToolActionsMixin`` 的 AST，类体聚合宿主 + 全部域 mixin。

    ``llm_tool_actions.py`` 拆分后，既有测试里
    ``ast.parse((ROOT / "llm_tool_actions.py").read_text())`` 的写法会漏掉已搬走的方法。
    本函数返回形状兼容的 ``ast.Module``，使后续
    ``next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LlmToolActionsMixin")``
    以及 ``owner.body`` 遍历无需改动即可覆盖整个模块族。
    """
    return ast.Module(
        body=[
            ast.ClassDef(
                name="LlmToolActionsMixin",
                bases=[],
                keywords=[],
                body=class_body_defs(root, "llm_tool_actions", "LlmToolActionsMixin"),
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )


def private_image_mixin_tree(root: Path) -> ast.Module:
    """合成一棵只含 ``PrivateImageMixin`` 的 AST，类体聚合宿主 + 全部域 mixin。

    ``private_image.py`` 拆分后，既有测试里
    ``ast.parse((ROOT / "private_image.py").read_text())`` 的写法会漏掉已搬走的方法。
    本函数返回形状兼容的 ``ast.Module``，使后续
    ``next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PrivateImageMixin")``
    以及 ``owner.body`` 遍历无需改动即可覆盖整个模块族。
    """
    return ast.Module(
        body=[
            ast.ClassDef(
                name="PrivateImageMixin",
                bases=[],
                keywords=[],
                body=class_body_defs(root, "private_image", "PrivateImageMixin"),
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )
