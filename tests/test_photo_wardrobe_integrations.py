from __future__ import annotations

import ast
import re
import sys
import types
import unittest
from importlib.util import find_spec
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"
package = sys.modules.setdefault(PACKAGE_NAME, types.ModuleType(PACKAGE_NAME))
package.__path__ = [str(PLUGIN_ROOT)]
package.__package__ = PACKAGE_NAME

IMAGE_SPEC = find_spec("astrbot_plugin_image_companion")
IMAGE_ROOT = Path(IMAGE_SPEC.origin).resolve().parent if IMAGE_SPEC and IMAGE_SPEC.origin else None

if IMAGE_ROOT is not None:
    from astrbot_plugin_image_companion.photo_wardrobe_decision import PhotoWardrobeIntent


def _module_tree(name: str) -> ast.Module:
    return ast.parse((PLUGIN_ROOT / name).read_text(encoding="utf-8"), filename=name)


def _image_owner_tree() -> ast.Module:
    if IMAGE_ROOT is None:
        raise unittest.SkipTest("optional Image Companion runtime is not installed")
    name = "image_runtime.py"
    return ast.parse((IMAGE_ROOT / name).read_text(encoding="utf-8"), filename=name)


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function not found: {name}")


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


class PhotoWardrobeIntegrationTests(unittest.TestCase):
    def test_generation_chain_reuses_one_intent_for_plan_selection_and_resolution(self) -> None:
        generate = _function(
            _image_owner_tree(),
            "_generate_photo_image_legacy",
        )
        calls = [node for node in ast.walk(generate) if isinstance(node, ast.Call)]

        analyze_calls = [call for call in calls if _call_name(call) == "analyze_photo_wardrobe"]
        select_call = next(
            call for call in calls if _call_name(call) == "_select_photo_reference_plan_async"
        )
        resolve_call = next(
            call for call in calls if _call_name(call) == "resolve_photo_wardrobe_decision"
        )

        self.assertEqual(len(analyze_calls), 1)
        select_intent = next(
            keyword.value for keyword in select_call.keywords if keyword.arg == "wardrobe_intent"
        )
        resolve_intent = next(keyword.value for keyword in resolve_call.keywords if keyword.arg == "intent")
        self.assertIsInstance(select_intent, ast.Name)
        self.assertIsInstance(resolve_intent, ast.Name)
        self.assertEqual(select_intent.id, "wardrobe_intent")
        self.assertEqual(resolve_intent.id, "wardrobe_intent")

    def test_selection_score_only_treats_outfit_roles_as_clothing_constraints(self) -> None:
        score_function = _function(
            _image_owner_tree(),
            "_photo_reference_candidate_score",
        )
        harness = ast.ClassDef(
            name="ScoreHarness",
            bases=[],
            keywords=[],
            body=[score_function],
            decorator_list=[],
        )
        module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
        namespace = {
            "Any": Any,
            "PhotoWardrobeIntent": PhotoWardrobeIntent,
            "re": re,
            "_single_line": lambda value, limit: str(value or "").strip()[:limit],
        }
        exec(compile(module, "proactive_message.py", "exec"), namespace)
        score = namespace["ScoreHarness"]._photo_reference_candidate_score
        intent = PhotoWardrobeIntent(
            target_category="school_uniform",
            excluded_categories=("sleepwear",),
        )

        outfit_score = score(
            {
                "kind": "library",
                "note": "卧室睡衣参考",
                "outfit_category": "sleepwear",
                "reference_roles": ["identity", "outfit"],
                "outfit_lock_default": True,
            },
            "穿校服拍照，不要睡衣",
            "",
            wardrobe_intent=intent,
            requested_outfit_category="school_uniform",
        )
        identity_score = score(
            {
                "kind": "persona",
                "note": "基础身份图，文件标签沿用睡衣分类",
                "outfit_category": "sleepwear",
                "reference_roles": ["identity"],
                "outfit_lock_default": False,
            },
            "穿校服拍照，不要睡衣",
            "",
            wardrobe_intent=intent,
            requested_outfit_category="school_uniform",
        )

        self.assertGreater(identity_score, outfit_score)

    def test_only_wardrobe_module_constructs_production_decisions(self) -> None:
        offenders: list[str] = []
        if IMAGE_ROOT is None:
            self.skipTest("optional Image Companion runtime is not installed")
        owner_root = IMAGE_ROOT
        for path in owner_root.glob("*.py"):
            if path.name == "photo_wardrobe_decision.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
            if any(
                isinstance(node, ast.Call) and _call_name(node) == "PhotoWardrobeDecision"
                for node in ast.walk(tree)
            ):
                offenders.append(path.name)

        self.assertEqual(offenders, [])

    def test_debug_schema_and_command_prompt_use_the_unified_decision_contract(self) -> None:
        if IMAGE_ROOT is None:
            self.skipTest("optional Image Companion runtime is not installed")
        owner_root = IMAGE_ROOT
        owner_runtime = "image_runtime.py"
        proactive = (owner_root / owner_runtime).read_text(encoding="utf-8")
        commands = (PLUGIN_ROOT / "command_handlers.py").read_text(encoding="utf-8")
        page_api = (PLUGIN_ROOT / "page_api.py").read_text(encoding="utf-8")

        self.assertIn('"schema_version": 3', proactive)
        self.assertIn('"wardrobe_rule_id"', proactive)
        self.assertIn('"wardrobe_adjustments"', proactive)
        self.assertIn("resolve_photo_prompt_context(", proactive)
        self.assertIn('"residual_conflicts"', proactive)
        self.assertIn('"sanitizer_version"', proactive)
        self.assertIn('"detected_conflicts"', proactive)
        self.assertIn('"removed_conflict_details"', proactive)
        self.assertIn('"prompt_sections_after"', proactive)
        self.assertNotIn("def _append_photo_prompt_conflict_resolution", proactive)
        self.assertNotIn("_natural_photo_prompt_has_explicit_wardrobe_request", commands)
        self.assertIn("structured=True", commands)
        self.assertIn("preserve character identity and stable appearance", commands)
        self.assertIn('"reference_removed"', page_api)
        self.assertIn('"residual_conflicts"', page_api)


if __name__ == "__main__":
    unittest.main()
