# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _is_llm_tool(function: ast.AsyncFunctionDef) -> bool:
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if isinstance(target, ast.Attribute) and target.attr == "llm_tool":
            return True
    return False


class LlmToolReservedParameterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # LLM 工具已随 main.py 拆分迁至 main_*.py 域 mixin，跨宿主族聚合扫描
        # main.py 与全部 main_*.py（与 test_wardrobe_detail_tool 的聚合扫描同款）。
        cls.tools: dict[str, tuple[ast.AsyncFunctionDef, str]] = {}
        for path in [ROOT / "main.py", *sorted(ROOT.glob("main_*.py"))]:
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef) and _is_llm_tool(node):
                    cls.tools.setdefault(node.name, (node, source))

    def test_llm_tools_do_not_expose_framework_context_parameter(self) -> None:
        conflicts = []
        for name, (function, _source) in self.tools.items():
            argument_names = {
                argument.arg
                for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
            }
            if "context" in argument_names:
                conflicts.append(name)

        self.assertEqual([], conflicts)

    def test_reaction_lookup_uses_search_context_and_maps_it_internally(self) -> None:
        function, source = self.tools["pc_find_reaction_image"]
        argument_names = {argument.arg for argument in function.args.args}
        docstring = ast.get_docstring(function) or ""
        function_source = ast.get_source_segment(source, function) or ""

        self.assertIn("search_context", argument_names)
        self.assertIn("search_context(string)", docstring)
        self.assertNotRegex(docstring, r"(?m)^\s*context\(string\):")
        self.assertIn("context=search_context", function_source)
        self.assertIn("search_context=search_context", function_source)


if __name__ == "__main__":
    unittest.main()
