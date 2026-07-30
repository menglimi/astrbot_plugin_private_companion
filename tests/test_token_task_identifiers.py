# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_SCRIPT = ROOT / "pages" / "陪伴面板" / "app.js"
CONSTANTS = ROOT / "constants.py"
MODEL_CALL_NAMES = {"_llm_call", "_tts_provider_text_chat"}
RECORDED_CALL_NAMES = MODEL_CALL_NAMES | {"_record_llm_usage", "_record_llm_budget_skip"}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


class TokenTaskIdentifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.python_files = sorted(ROOT.glob("*.py"))
        cls.trees = {
            path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for path in cls.python_files
        }
        cls.script = PAGE_SCRIPT.read_text(encoding="utf-8")

    def test_every_internal_model_call_has_explicit_task(self) -> None:
        missing: list[str] = []
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) not in MODEL_CALL_NAMES:
                    continue
                if not any(keyword.arg == "task" for keyword in node.keywords):
                    missing.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], missing, "缺少 task 标识的模型调用：" + "、".join(missing))

    def test_literal_task_identifiers_have_chinese_labels(self) -> None:
        label_block = re.search(r"const tokenTaskLabels = \{(.*?)\n\};", self.script, re.S)
        self.assertIsNotNone(label_block)
        labels = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*):", label_block.group(1), re.M))
        task_ids: set[str] = set()
        for tree in self.trees.values():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) not in RECORDED_CALL_NAMES:
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "task" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        task_ids.add(keyword.value.value)
        self.assertEqual([], sorted(task_ids - labels), "缺少中文名称的任务标识")

    def test_unclassified_bucket_is_only_a_clear_fallback(self) -> None:
        self.assertIn('other: "未分类模型调用"', self.script)
        self.assertIn("没有有效任务标识", self.script)

    def test_new_task_identifiers_keep_their_provider_routing(self) -> None:
        tree = ast.parse(CONSTANTS.read_text(encoding="utf-8"), filename=str(CONSTANTS))
        mapping: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "MODEL_TASK_PROVIDER_KEYS"
                for target in node.targets
            ):
                continue
            if isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                        mapping[str(key.value)] = str(value.value)
        expected = {
            "diary_rewrite": "DREAM_DIARY_PROVIDER_ID",
            "diary_derivatives": "DREAM_DIARY_PROVIDER_ID",
            "bookshelf_password": "DREAM_DIARY_PROVIDER_ID",
            "bookshelf_password_reason": "DREAM_DIARY_PROVIDER_ID",
            "tts_conversion": "tts_conversion_provider_id",
            "tts_spoken_conversion": "tts_conversion_provider_id",
        }
        self.assertEqual(expected, {key: mapping.get(key) for key in expected})


if __name__ == "__main__":
    unittest.main()
