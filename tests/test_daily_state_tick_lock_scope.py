# -*- coding: utf-8 -*-
"""回归守卫：``_tick`` 不得在 ``self._data_lock`` 内重入 HDSI 生命周期锁。

``_run_hdsi_life_tick_sidecar`` 会经
``run_hdsi_life_tick`` -> ``_record_hdsi_event`` -> ``await _run_trial_update``
-> ``async with lock`` 重新进入同一把 ``self._data_lock``
（``asyncio.Lock`` 非可重入，``plugin_bootstrap.py`` 中创建）。
一旦 ``max_daily_messages <= 0`` 且 HDSI trial 模式开启，该路径即触发
自死锁，``_tick`` 永不返回。

本测试用 AST 静态断言：``_tick`` 里每个 ``async with self._data_lock``
的子树内都不存在 ``self._run_hdsi_life_tick_sidecar()`` 调用。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDECAR_NAME = "_run_hdsi_life_tick_sidecar"
LOCK_NAME = "_data_lock"


def _tree(filename: str) -> ast.Module:
    return ast.parse((ROOT / filename).read_text(encoding="utf-8"), filename=filename)


def _find_method(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    """按方法名定位，不绑定具体类名——``_tick`` 随 mixin 拆分换过宿主。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == name
                ):
                    return child
    raise AssertionError(f"未在任何类中找到方法 {name}()，测试前提失效")


def _is_data_lock(expr: ast.AST) -> bool:
    """匹配 ``self._data_lock``（允许 ``await``/括号等简单包装）。"""
    if isinstance(expr, ast.Attribute) and expr.attr == LOCK_NAME:
        return isinstance(expr.value, ast.Name) and expr.value.id == "self"
    return False


def _calls_sidecar(node: ast.AST) -> list[ast.Call]:
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == SIDECAR_NAME
    ]


class DailyStateTickLockScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _tree("daily_state.py")
        self.tick = _find_method(self.tree, "_tick")

    def _data_lock_blocks(self) -> list[ast.AsyncWith]:
        return [
            node
            for node in ast.walk(self.tick)
            if isinstance(node, ast.AsyncWith)
            and any(_is_data_lock(item.context_expr) for item in node.items)
        ]

    def test_tick_actually_acquires_data_lock(self) -> None:
        """前置条件：``_tick`` 内确实存在 ``async with self._data_lock``。"""
        self.assertTrue(
            self._data_lock_blocks(),
            "_tick 中未找到 `async with self._data_lock`，测试前提失效",
        )

    def test_tick_calls_sidecar_somewhere(self) -> None:
        """前置条件：``_tick`` 仍然会调用 sidecar（修复只是挪位置，不是删除）。"""
        self.assertTrue(
            _calls_sidecar(self.tick),
            f"_tick 已不再调用 {SIDECAR_NAME}()，本守卫失去意义",
        )

    def test_sidecar_not_invoked_inside_data_lock(self) -> None:
        """核心断言：锁块子树内不存在 sidecar 调用（否则会重入导致自死锁）。"""
        violations: list[str] = []
        for block in self._data_lock_blocks():
            for call in _calls_sidecar(block):
                violations.append(
                    f"daily_state.py:{call.lineno}: `self.{SIDECAR_NAME}()` "
                    f"位于 `async with self.{LOCK_NAME}` 块内"
                    f"（锁块起始行 {block.lineno}，结束行 {block.end_lineno}）"
                )
        self.assertEqual(
            [],
            violations,
            "检测到在数据锁内重入 HDSI 生命周期锁的自死锁风险：\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
