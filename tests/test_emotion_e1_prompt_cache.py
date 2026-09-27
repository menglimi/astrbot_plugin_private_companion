from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.module_source_index import find_method


class EmotionE1PromptCacheTests(unittest.TestCase):
    def test_expression_decision_is_a_forced_dynamic_fragment(self) -> None:
        # 方法已随域拆分分散在 main.py 与 main_*.py，跨宿主族聚合定位。
        hook = find_method(ROOT, "main", "PrivateCompanionPlugin", "inject_unified_relationship_expression")
        assert hook is not None
        calls = [
            node
            for node in ast.walk(hook)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_append_turn_prompt_fragment_by_position"
        ]
        self.assertEqual(1, len(calls))
        keywords = {item.arg: item.value for item in calls[0].keywords if item.arg}
        self.assertIsInstance(keywords.get("force_dynamic"), ast.Constant)
        self.assertTrue(keywords["force_dynamic"].value)

        assignments = [
            node
            for node in ast.walk(hook)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            and "system_prompt" in ast.unparse(node)
            and "Companion expression" in ast.unparse(node)
        ]
        self.assertEqual([], assignments)

    def test_turn_fragment_helper_delegates_typed_section_and_force_dynamic(self) -> None:
        # 该 helper 已随提示词编排域拆到 main_prompt.py，故跨宿主 + 各域 mixin 定位。
        helper = find_method(
            ROOT, "main", "PrivateCompanionPlugin", "_append_turn_prompt_fragment_by_position"
        )
        self.assertIsNotNone(helper)
        parameters = [arg.arg for arg in helper.args.args + helper.args.kwonlyargs]
        self.assertEqual(
            ["self", "req", "marker", "section", "priority", "force_dynamic"],
            parameters,
        )
        for removed in ("key", "content", "title", "source", "structured", "opaque"):
            self.assertNotIn(removed, parameters)

        placement_calls = [
            node
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_place_conversation_prompt_section"
        ]
        self.assertEqual(1, len(placement_calls))
        call = placement_calls[0]
        self.assertGreaterEqual(len(call.args), 3)
        self.assertIsInstance(call.args[2], ast.Name)
        self.assertEqual("section", call.args[2].id)
        keywords = {item.arg: item.value for item in call.keywords if item.arg}
        self.assertIsInstance(keywords.get("force_dynamic"), ast.Name)
        self.assertEqual("force_dynamic", keywords["force_dynamic"].id)


if __name__ == "__main__":
    unittest.main()
