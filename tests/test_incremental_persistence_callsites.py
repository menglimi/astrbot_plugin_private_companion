# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from astrbot_plugin_private_companion.core_store import (
    CoreStoreMixin,
    _DURABLE_SECTION_NAMES,
    _FULL_SAVE_SCOPES,
)
from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.story_handoff import STORY_MIGRATION_COMMIT_KEY


ROOT = Path(__file__).resolve().parents[1]
SAVE_METHODS = {"_save_data_sync", "_save_data_now_sync", "_schedule_data_save"}


def _production_python_paths() -> list[Path]:
    excluded = {"tests", "__pycache__", ".pytest_cache", ".ruff_cache"}
    return [
        path
        for path in sorted(ROOT.rglob("*.py"))
        if not excluded.intersection(path.relative_to(ROOT).parts)
    ]


def _save_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in SAVE_METHODS
    ]


def _is_official_no_arg_save_compatibility(path: Path, node: ast.Call) -> bool:
    if path.name != "llm_tool_actions.py" or node.func.attr != "_save_data_sync":
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    context = "\n".join(lines[max(0, node.lineno - 5) : node.lineno])
    return "historical no-argument persistence hook" in context


def _literal_durable_data_roots(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        key: str | None = None
        receiver: ast.AST | None = None
        if isinstance(node, ast.Subscript):
            receiver = node.value
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                key = node.slice.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "setdefault", "pop"}
            and node.args
        ):
            receiver = node.func.value
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                key = node.args[0].value
        if (
            key is None
            or not isinstance(receiver, ast.Attribute)
            or receiver.attr != "data"
            or ast.unparse(receiver.value) not in {"self", "plugin", "self.plugin"}
        ):
            continue
        roots.append((key, node.lineno))
    return roots


def _private_handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(
        (ROOT / "message_pipeline.py").read_text(encoding="utf-8"),
        filename="message_pipeline.py",
    )
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "handle_private_message"
    )


def _group_handler() -> ast.AsyncFunctionDef:
    tree = ast.parse(
        (ROOT / "message_pipeline.py").read_text(encoding="utf-8"),
        filename="message_pipeline.py",
    )
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "handle_group_message"
    )


def _if_node(function: ast.AST, condition: str) -> ast.If:
    return next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If) and ast.unparse(node.test) == condition
    )


def _if_nodes(function: ast.AST, condition: str) -> list[ast.If]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If) and ast.unparse(node.test) == condition
    ]


def _string_constants(node: ast.AST) -> set[str]:
    return {
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and type(item.value) is str
    }


class _SmartDebounceHarness(EventDispatchMixin):
    def __init__(self) -> None:
        self.data: dict = {}
        self.enable_message_debounce = True
        self.enable_smart_message_debounce = True
        self.smart_message_debounce_examples_limit = 8
        self.smart_message_debounce_learning_window_seconds = 8.0
        self._schedule_data_save = Mock()


class IncrementalPersistenceCallsiteTests(unittest.TestCase):
    def test_production_save_calls_declare_a_contract(self) -> None:
        bare_calls: list[str] = []
        compatibility_calls: list[str] = []
        for path in _production_python_paths():
            for node in _save_calls(path):
                if not any(
                    keyword.arg in {"sections", "deleted_sections", "full_scope"}
                    for keyword in node.keywords
                ):
                    if _is_official_no_arg_save_compatibility(path, node):
                        compatibility_calls.append(path.relative_to(ROOT).as_posix())
                        continue
                    bare_calls.append(
                        f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                    )
        self.assertEqual([], bare_calls)
        self.assertEqual(["llm_tool_actions.py"], compatibility_calls)

    def test_literal_save_sections_are_registered(self) -> None:
        unknown: list[str] = []
        for path in _production_python_paths():
            for node in _save_calls(path):
                for keyword in node.keywords:
                    if keyword.arg not in {"sections", "deleted_sections"}:
                        continue
                    if not isinstance(keyword.value, (ast.Set, ast.List, ast.Tuple)):
                        continue
                    for item in keyword.value.elts:
                        if (
                            isinstance(item, ast.Constant)
                            and isinstance(item.value, str)
                            and item.value not in _DURABLE_SECTION_NAMES
                        ):
                            unknown.append(
                                f"{path.relative_to(ROOT).as_posix()}:{node.lineno}:"
                                f"{item.value}"
                            )
        self.assertEqual([], unknown)

    def test_literal_full_scopes_use_the_fixed_allowlist(self) -> None:
        expected = {
            "startup_migration",
            "startup_maintenance",
            "explicit_reset",
            "shutdown_flush",
            "admin_import_export",
        }
        self.assertEqual(expected, set(_FULL_SAVE_SCOPES))

        invalid: list[str] = []
        for path in _production_python_paths():
            for node in _save_calls(path):
                for keyword in node.keywords:
                    if keyword.arg != "full_scope" or not isinstance(
                        keyword.value, ast.Constant
                    ):
                        continue
                    if keyword.value.value not in _FULL_SAVE_SCOPES:
                        invalid.append(
                            f"{path.relative_to(ROOT).as_posix()}:{node.lineno}:"
                            f"{keyword.value.value}"
                        )
        self.assertEqual([], invalid)

    def test_section_registry_matches_both_default_store_builders(self) -> None:
        new_store = CoreStoreMixin._new_store(object())
        ensured = CoreStoreMixin._ensure_store_defaults({})
        default_sections = set(_DURABLE_SECTION_NAMES) - {
            STORY_MIGRATION_COMMIT_KEY
        }

        self.assertEqual(default_sections, set(new_store))
        self.assertEqual(default_sections, set(ensured))
        self.assertNotIn(STORY_MIGRATION_COMMIT_KEY, new_store)
        self.assertNotIn(STORY_MIGRATION_COMMIT_KEY, ensured)

    def test_literal_durable_data_roots_are_registered(self) -> None:
        unknown: list[str] = []
        for path in _production_python_paths():
            for section, lineno in _literal_durable_data_roots(path):
                if section not in _DURABLE_SECTION_NAMES:
                    unknown.append(
                        f"{path.relative_to(ROOT).as_posix()}:{lineno}:{section}"
                    )

        self.assertEqual([], unknown)

    def test_runtime_section_registry_contains_review_and_hot_path_sections(self) -> None:
        source = (ROOT / "core_store.py").read_text(encoding="utf-8")

        for section in (
            "proactive_review_runtime",
            "proactive_runtime",
            "proactive_audit_log",
            "passive_no_reply_records",
            "smart_message_debounce",
            "external_event_pool",
            "external_event_self_link_cache",
        ):
            self.assertIn(f'"{section}"', source)

    def test_first_fast_smart_debounce_decision_is_recorded(self) -> None:
        harness = _SmartDebounceHarness()
        event = SimpleNamespace()

        wait = asyncio.run(
            harness._smart_message_debounce_wait_seconds_for_event(
                event,
                key=harness._semantic_buffer_key("private:user-1", "user-1"),
                text="在吗？",
                sender_id="user-1",
            )
        )

        self.assertEqual(0.0, wait)
        decisions = harness.data["smart_message_debounce"]["last_decisions"]
        self.assertEqual(1, len(decisions))
        self.assertEqual("complete", next(iter(decisions.values()))["decision"])

    def test_smart_debounce_followup_marks_its_durable_section(self) -> None:
        harness = _SmartDebounceHarness()
        key = harness._semantic_buffer_key("private:user-1", "user-1")
        harness.data["smart_message_debounce"] = {
            "last_decisions": {
                key: {
                    "ts": time.time(),
                    "text": "first",
                    "decision": "complete",
                }
            },
            "examples": [],
            "recent_logs": [],
        }

        changed = harness._maybe_record_smart_message_debounce_followup(
            scope="private:user-1",
            sender_id="user-1",
            text="continued",
            now=time.time(),
        )

        self.assertTrue(changed)
        self.assertEqual(
            "false_complete",
            harness.data["smart_message_debounce"]["examples"][0]["kind"],
        )
        harness._schedule_data_save.assert_not_called()

    def test_private_pipeline_marks_fast_path_meal_and_warmth_sections(self) -> None:
        handler = _private_handler()
        meal_branch = _if_node(handler, "fast_meal_care_result.get('foods')")
        warmth_branches = _if_nodes(handler, "fast_interaction_warmth_applied")

        self.assertIn("food_menu", _string_constants(meal_branch))
        self.assertTrue(
            any(
                {"state_conditions", "daily_state"}.issubset(_string_constants(branch))
                for branch in warmth_branches
            )
        )

    def test_private_pipeline_marks_normal_state_feedback_sections(self) -> None:
        handler = _private_handler()
        care_branches = _if_nodes(handler, "care_feedback_detected")
        food_branches = _if_nodes(handler, "food_feedback_detected")
        warmth_branches = _if_nodes(handler, "interaction_warmth_applied")

        self.assertTrue(
            any(
                "state_conditions" in _string_constants(branch)
                for branch in care_branches
            )
        )
        self.assertTrue(
            any(
                {
                    "last_food_state_feedback_at",
                    "last_food_state_feedback_text",
                }.issubset(_string_constants(branch))
                for branch in food_branches
            )
        )
        self.assertTrue(
            any(
                {"state_conditions", "daily_state"}.issubset(_string_constants(branch))
                for branch in warmth_branches
            )
        )

    def test_private_pipeline_uses_expression_feedback_source_sections(self) -> None:
        handler = _private_handler()
        feedback_branch = _if_node(handler, "expression_feedback")
        source = ast.unparse(feedback_branch)

        self.assertIn("updated_sections", source)
        self.assertIn("updated_rules", source)
        self.assertIn("expression_voice_profile", source)

    def test_private_pipeline_initializes_expression_feedback_before_text_gate(self) -> None:
        handler = _private_handler()
        initializers = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "expression_feedback"
        ]
        self.assertTrue(initializers)
        self.assertIsInstance(initializers[0].value, ast.Dict)
        text_gates = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.If) and ast.unparse(node.test) == "text"
        ]
        self.assertTrue(any(node.lineno > initializers[0].lineno for node in text_gates))

    def test_private_pipeline_saves_smart_state_at_early_and_final_commit_points(
        self,
    ) -> None:
        handler = _private_handler()
        branches = _if_nodes(handler, "smart_debounce_state_changed")

        self.assertGreaterEqual(len(branches), 2)
        self.assertTrue(
            all(
                "smart_message_debounce" in _string_constants(branch)
                for branch in branches
            )
        )

    def test_message_hooks_share_event_batch_inside_persona_context(self) -> None:
        tree = ast.parse(
            (ROOT / "main.py").read_text(encoding="utf-8"),
            filename="main.py",
        )
        expected = {
            "guard_req036_private_capability_early": False,
            "on_private_message": True,
            "guard_blocked_group_member_early": False,
            "capture_group_observation_early": False,
            "review_group_member_safety_early": False,
            "guard_req036_group_portrait_queries": False,
            "on_group_message": True,
        }
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in expected
        }
        self.assertEqual(set(expected), set(functions))
        for name, flush in expected.items():
            decorators = functions[name].decorator_list
            decorator_names = {
                node.id
                for node in decorators
                if isinstance(node, ast.Name)
            }
            decorator_names.update(
                node.func.id
                for node in decorators
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            )
            self.assertIn("_multi_persona_event_context", decorator_names, name)
            self.assertIn("event_data_save_boundary", decorator_names, name)
            if flush:
                self.assertTrue(
                    any(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "event_data_save_boundary"
                        and any(
                            isinstance(keyword, ast.keyword)
                            and keyword.arg == "flush"
                            and isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True
                            for keyword in node.keywords
                        )
                        for node in decorators
                    ),
                    name,
                )

    def test_group_registration_persists_worldbook_profile_sections(self) -> None:
        source = ast.unparse(_group_handler())

        self.assertIn("registration_payload", source)
        self.assertIn("worldbook_member_profiles", source)
        self.assertIn("worldbook_deleted_member_ids", source)
        self.assertIn("self._save_data_sync(sections=save_sections)", source)

    def test_proactive_message_has_no_implicit_full_save_calls(self) -> None:
        tree = ast.parse(
            (ROOT / "proactive_message.py").read_text(encoding="utf-8"),
            filename="proactive_message.py",
        )
        bare_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_save_data_sync"
            and not node.args
            and not node.keywords
        ]

        self.assertEqual([], bare_calls)

    def test_private_image_buffer_persists_smart_learning_state(self) -> None:
        source = (ROOT / "private_image.py").read_text(encoding="utf-8")
        self.assertIn(
            'scheduler(sections={"smart_message_debounce"})',
            source,
        )

    def test_proactive_only_meal_care_marks_food_menu(self) -> None:
        tree = ast.parse(
            (ROOT / "main.py").read_text(encoding="utf-8"),
            filename="main.py",
        )
        handler = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_record_proactive_only_private_feedback"
        )
        source = ast.unparse(handler)

        self.assertIn("meal_care_result = self._handle_meal_care_inbound", source)
        self.assertIn("save_sections.add('food_menu')", source)
        self.assertIn("self._schedule_data_save(sections=save_sections)", source)


if __name__ == "__main__":
    unittest.main()
