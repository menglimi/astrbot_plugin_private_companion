from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EventDispatchRegistrationTests(unittest.TestCase):
    def test_main_module_registers_every_split_event_dispatch_hook(self) -> None:
        main_tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        main_class = next(
            node
            for node in main_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin"
        )
        methods = {
            node.name: node
            for node in main_class.body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        expected = {
            "route_model_replacement_before_agent_hook": (
                [
                    "_ON_WAITING_LLM_REQUEST(priority=110000)",
                    "_multi_persona_event_context",
                ],
                "EventDispatchMixin.route_model_replacement_before_agent",
            ),
            "enforce_model_replacement_request_hook": (
                [
                    "filter.on_llm_request(priority=110000)",
                    "_multi_persona_event_context",
                ],
                "EventDispatchMixin.enforce_model_replacement_request",
            ),
            "clear_model_replacement_context_hook": (
                [
                    "filter.on_llm_response(priority=-100000)",
                    "_multi_persona_event_context",
                ],
                "EventDispatchMixin.clear_model_replacement_context",
            ),
            "guard_pending_message_debounce_hook": (
                [
                    "_ON_WAITING_LLM_REQUEST(priority=100000)",
                    "_multi_persona_event_context",
                ],
                "EventDispatchMixin.guard_pending_message_debounce",
            ),
            "settle_pending_message_debounce_hook": (
                [
                    "filter.on_llm_response(priority=100000)",
                    "_multi_persona_event_context",
                ],
                "EventDispatchMixin.settle_pending_message_debounce",
            ),
        }
        for method_name, (decorators, delegate) in expected.items():
            with self.subTest(method=method_name):
                method = methods[method_name]
                self.assertEqual(
                    decorators,
                    [ast.unparse(item) for item in method.decorator_list],
                )
                self.assertIn(delegate, ast.unparse(method))

        dispatch_tree = ast.parse(
            (ROOT / "event_dispatch.py").read_text(encoding="utf-8")
        )
        dispatch_class = next(
            node
            for node in dispatch_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "EventDispatchMixin"
        )
        implementation_names = {
            delegate.rpartition(".")[2] for _, delegate in expected.values()
        }
        for method in dispatch_class.body:
            if (
                isinstance(method, ast.AsyncFunctionDef)
                and method.name in implementation_names
            ):
                self.assertEqual([], method.decorator_list)


if __name__ == "__main__":
    unittest.main()
