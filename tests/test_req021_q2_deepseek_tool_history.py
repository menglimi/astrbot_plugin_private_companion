from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest

from tool_history_sanitizer import sanitize_openai_tool_history


ROOT = Path(__file__).resolve().parents[1]

from tests.module_source_index import class_body_defs


class OpenAIToolHistorySanitizerTests(unittest.TestCase):
    def test_valid_single_and_multi_tool_groups_keep_original_objects(self) -> None:
        assistant_single = {"role": "assistant", "tool_calls": [{"id": "call-a"}]}
        result_single = {"role": "tool", "tool_call_id": "call-a", "content": "ok"}
        assistant_multi = {"role": "assistant", "tool_calls": [{"id": "call-b"}, {"id": "call-c"}]}
        result_b = {"role": "tool", "tool_call_id": "call-b", "content": "b"}
        result_c = {"role": "tool", "tool_call_id": "call-c", "content": "c"}
        contexts = [{"role": "user", "content": "hello"}, assistant_single, result_single, assistant_multi, result_b, result_c]

        cleaned, stats = sanitize_openai_tool_history(contexts)

        self.assertIs(contexts, cleaned)
        self.assertEqual(0, stats["changed"])
        self.assertIs(assistant_single, cleaned[1])
        self.assertIs(result_c, cleaned[-1])

    def test_missing_result_removes_the_entire_assistant_group(self) -> None:
        tail = {"role": "user", "content": "next"}
        cleaned, stats = sanitize_openai_tool_history(
            [{"role": "assistant", "tool_calls": [{"id": "call-a"}]}, tail]
        )

        self.assertEqual([tail], cleaned)
        self.assertEqual(1, stats["removed_groups"])
        self.assertEqual(1, stats["removed_assistants"])

    def test_orphan_and_duplicate_results_are_removed(self) -> None:
        orphan = {"role": "tool", "tool_call_id": "orphan", "content": "x"}
        assistant = {"role": "assistant", "tool_calls": [{"id": "call-a"}]}
        first = {"role": "tool", "tool_call_id": "call-a", "content": "one"}
        duplicate = {"role": "tool", "tool_call_id": "call-a", "content": "two"}

        cleaned, stats = sanitize_openai_tool_history([orphan, assistant, first, duplicate])

        self.assertEqual([], cleaned)
        self.assertEqual(1, stats["removed_orphans"])
        self.assertEqual(1, stats["removed_groups"])
        self.assertEqual(3, stats["removed_tool_results"])

    def test_duplicate_call_id_mismatched_id_and_malformed_call_remove_groups(self) -> None:
        malformed = {"role": "assistant", "tool_calls": "call-a"}
        duplicate_call = {"role": "assistant", "tool_calls": [{"id": "call-b"}, {"id": "call-b"}]}
        duplicate_result = {"role": "tool", "tool_call_id": "call-b"}
        mismatch_call = {"role": "assistant", "tool_calls": [{"id": "call-c"}]}
        mismatch_result = {"role": "tool", "tool_call_id": "call-other"}
        normal = {"role": "assistant", "content": "ordinary"}

        cleaned, stats = sanitize_openai_tool_history(
            [malformed, duplicate_call, duplicate_result, mismatch_call, mismatch_result, normal]
        )

        self.assertEqual([normal], cleaned)
        self.assertEqual(3, stats["removed_groups"])
        self.assertEqual(3, stats["removed_assistants"])
        self.assertEqual(2, stats["removed_tool_results"])

    def test_empty_or_absent_tool_calls_and_non_list_history_are_unchanged(self) -> None:
        empty = {"role": "assistant", "tool_calls": []}
        ordinary = {"role": "assistant", "content": "plain"}
        contexts = [empty, ordinary]

        cleaned, stats = sanitize_openai_tool_history(contexts)

        self.assertIs(contexts, cleaned)
        self.assertEqual(0, stats["changed"])
        sentinel = object()
        self.assertIs(sentinel, sanitize_openai_tool_history(sentinel)[0])


def _load_main_methods(names: set[str]) -> dict[str, Any]:
    # 方法体已拆到 main_outbound_guard.py 等域 mixin，
    # 故在「宿主类 + 各域 mixin 类」里聚合查找，保持原有执行语义。
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "ProviderRequest": Any,
        # The production hook is tested separately; this loader only executes
        # the selected method body and therefore needs a no-op decorator stub.
        "_multi_persona_event_context": lambda target: target,
        "filter": SimpleNamespace(on_llm_request=lambda **_kwargs: lambda target: target),
        "sanitize_openai_tool_history": sanitize_openai_tool_history,
        "logger": SimpleNamespace(info=lambda *_args, **_kwargs: None),
    }
    found: set[str] = set()
    for node in class_body_defs(ROOT, "main", "PrivateCompanionPlugin"):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
            found.add(node.name)
    missing = set(names) - found
    if missing:
        raise KeyError(sorted(missing))
    return {name: namespace[name] for name in names}


MAIN_METHODS = _load_main_methods(
    {"_llm_request_uses_deepseek_openai_compatible_provider", "sanitize_deepseek_tool_call_history"}
)


class _HookHost:
    def __init__(self, is_deepseek: bool) -> None:
        self.is_deepseek = is_deepseek

    def _llm_request_uses_deepseek_openai_compatible_provider(self, _event: object, _req: object) -> bool:
        return self.is_deepseek


_HookHost.sanitize_deepseek_tool_call_history = MAIN_METHODS["sanitize_deepseek_tool_call_history"]


class DeepSeekHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_deepseek_request_keeps_contexts_and_p3_p5_attributes_identical(self) -> None:
        contexts = [{"role": "tool", "tool_call_id": "orphan"}]
        request = SimpleNamespace(
            contexts=contexts,
            private_companion_p3_state=object(),
            private_companion_p5_attestation=object(),
        )
        before_p3 = request.private_companion_p3_state
        before_p5 = request.private_companion_p5_attestation

        await _HookHost(False).sanitize_deepseek_tool_call_history(SimpleNamespace(), request)

        self.assertIs(contexts, request.contexts)
        self.assertIs(before_p3, request.private_companion_p3_state)
        self.assertIs(before_p5, request.private_companion_p5_attestation)

    async def test_deepseek_request_changes_only_damaged_context_history(self) -> None:
        request = SimpleNamespace(
            contexts=[{"role": "assistant", "tool_calls": [{"id": "call-a"}]}],
            private_companion_p3_state=object(),
            private_companion_p5_attestation=object(),
        )
        before_p3 = request.private_companion_p3_state
        before_p5 = request.private_companion_p5_attestation

        await _HookHost(True).sanitize_deepseek_tool_call_history(SimpleNamespace(), request)

        self.assertEqual([], request.contexts)
        self.assertIs(before_p3, request.private_companion_p3_state)
        self.assertIs(before_p5, request.private_companion_p5_attestation)

    def test_provider_matcher_is_deepseek_specific(self) -> None:
        host = SimpleNamespace(_llm_request_provider_identity_parts=lambda _event, _req: ["openai", "deepseek-chat"])
        matcher = MAIN_METHODS["_llm_request_uses_deepseek_openai_compatible_provider"]
        self.assertTrue(matcher(host, None, None))
        host._llm_request_provider_identity_parts = lambda _event, _req: ["openai", "gpt-5"]
        self.assertFalse(matcher(host, None, None))


if __name__ == "__main__":
    unittest.main()
