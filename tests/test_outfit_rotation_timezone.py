# -*- coding: utf-8 -*-
"""回归守卫：换装轮换截止日必须使用插件时区，而不是系统时区。

写入侧 ``_today_key()``（``helpers.py``）按插件配置时区取 ``%Y-%m-%d``；
若读取侧的 ``_daily_outfit_rotation_history`` 用系统时区的 ``date.today()``
算 cutoff，两端在跨时区场景下会差一天，导致历史条目被提前剔除或保留。

本测试用 AST 静态断言：
1. ``_daily_outfit_rotation_history`` 不再调用 ``date.today()``；
2. 它确实调用了 ``_today_key()``。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.module_source_index import find_method


ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = "proactive_message.py"
TARGET_METHOD = "_daily_outfit_rotation_history"


def _method(filename: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """跨模块定位方法节点。

    ``{name}`` 已随域拆分迁出 ``{filename}``，改由 ``find_method`` 聚合扫描
    宿主与全部域模块，断言语义不变（仍对同一个方法体做 AST 断言）。
    """
    host = filename[:-3] if filename.endswith(".py") else filename
    node = find_method(ROOT, host, "ProactiveMessageMixin", name)
    if node is None:
        raise AssertionError(f"未找到方法 {name}()，测试前提失效")
    return node


def _call_names(node: ast.AST) -> set[str]:
    """收集形如 ``a.b()`` 与 ``f()`` 的被调函数全名。"""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            names.add(ast.unparse(child.func))
    return names


class OutfitRotationTimezoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.method = _method(TARGET_FILE, TARGET_METHOD)

    def test_rotation_cutoff_does_not_use_system_timezone(self) -> None:
        """核心断言：截止日不再取自系统时区的 ``date.today()``。"""
        offenders = [
            name
            for name in _call_names(self.method)
            if name in {"date.today", "datetime.date.today"}
        ]
        self.assertEqual(
            [],
            offenders,
            f"{TARGET_FILE}:{self.method.lineno} 的 {TARGET_METHOD}() 仍在使用"
            f"系统时区取日 {offenders}；应与写入侧 _today_key() 保持一致。",
        )

    def test_rotation_cutoff_uses_plugin_timezone(self) -> None:
        """确保修复方向正确：确实改为调用 ``_today_key()``。"""
        self.assertIn(
            "_today_key",
            _call_names(self.method),
            f"{TARGET_METHOD}() 未调用 _today_key()，插件时区一致性问题未修复",
        )


if __name__ == "__main__":
    unittest.main()
