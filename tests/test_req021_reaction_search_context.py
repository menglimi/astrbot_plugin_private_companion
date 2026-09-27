from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import json
import logging
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]

from tests.module_source_index import find_method, llm_tool_actions_source_text  # noqa: E402


def _single_line(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _load_method(filename: str, class_name: str, name: str) -> Any:
    # 方法已随 main.py 拆分迁至 main_*.py 域 mixin（类名以 class_name 开头），
    # 跨宿主族聚合扫描 main.py 与全部 main_*.py，与 test_wardrobe_detail_tool
    # 的 _llm_tool_functions 聚合扫描同款（提交 86ebc90 范本）。
    candidates = [ROOT / filename, *sorted(ROOT.glob("main_*.py"))]
    target: Any = None
    found_path: "Path | None" = None
    for path in candidates:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name != class_name and not node.name.startswith(class_name):
                continue
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == name:
                    target = sub
                    found_path = path
                    break
            if target is not None:
                break
        if target is not None:
            break
    if target is None:
        raise ValueError(f"方法 {class_name}.{name} 未在 {filename} 或 main_*.py 中定位")
    method = target
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "json": json,
        "filter": SimpleNamespace(llm_tool=lambda **_kwargs: lambda target: target),
        "_multi_persona_event_context": lambda target: target,
    }
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(found_path), "exec"), namespace)
    return namespace[name]


PUBLIC_TOOL = _load_method("main.py", "PrivateCompanionPlugin", "pc_find_reaction_image")


def _load_reaction_impl() -> Any:
    method = find_method(
        ROOT, "llm_tool_actions", "LlmToolActionsMixin", "_pc_find_reaction_image_impl"
    )
    assert method is not None, "_pc_find_reaction_image_impl 未在 llm_tool_actions 族中定位"
    namespace: dict[str, Any] = {
        "Any": Any,
        "AstrMessageEvent": Any,
        "asyncio": asyncio,
        "json": json,
        "logger": logging.getLogger("test_req021"),
        "os": os,
        "time": time,
        "_path_text": lambda value, _limit: str(value or ""),
        "_single_line": _single_line,
        "runtime_persona_setting": lambda host, key, default=None: getattr(host, key, default),
    }
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(ROOT / "llm_tool_actions.py"), "exec"), namespace)
    return namespace["_pc_find_reaction_image_impl"]


REACTION_IMPL = _load_reaction_impl()


class _ToolHost:
    def _proactive_only_blocks_passive_event(self, _event: object, _scope: str) -> bool:
        return False

    @staticmethod
    def _reaction_expression_opt_out_requested(_text: str) -> bool:
        return False

    @staticmethod
    def _reaction_expression_explicit_request_matches(_text: str) -> bool:
        return False

    @staticmethod
    def _reaction_expression_authorization(_event: object) -> dict[str, bool]:
        return {}

    @staticmethod
    def _reaction_expression_bool_arg(value: object, default: bool) -> bool:
        return bool(value) if isinstance(value, bool) else default

    @staticmethod
    def _sanitize_photo_tool_caption(value: object, *, limit: int) -> str:
        return _single_line(value, limit)

    async def _pc_find_reaction_image_impl(self, event: object, **kwargs: Any) -> str:
        self.called_event = event
        self.called_kwargs = kwargs
        return "ok"


class _ReactionLibrary:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    def has_enabled_assets(self) -> bool:
        return True

    def find(
        self,
        query: str,
        *,
        context: str,
        scope: str,
        selection_preferences: Any = None,
        selection_signature: str = "",
    ) -> dict[str, Any] | None:
        self.calls.append((query, context, scope))
        return self.result


class _ReactionHost:
    data: dict[str, Any] = {}

    def __init__(self, library: _ReactionLibrary | None) -> None:
        self.library = library

    def _reaction_expression_scope(self, _event: object) -> str:
        return "test-scope"

    @staticmethod
    def _sanitize_photo_tool_caption(value: object, *, limit: int) -> str:
        return _single_line(value, limit)

    def _reaction_asset_library(self) -> _ReactionLibrary | None:
        return self.library

    @staticmethod
    def _reaction_expression_lookup_cache_key(*_args: object) -> str:
        return "cache-key"

    @staticmethod
    def _reaction_expression_lookup_cache_revision(_library: object) -> int:
        return 1

    @staticmethod
    def _reaction_expression_selection_revision(
        _selection_preferences: object,
        _selection_signature: object = "",
    ) -> str:
        return ""

    @staticmethod
    def _reaction_expression_lookup_cache_get(_key: str) -> None:
        return None

    @staticmethod
    def _reaction_expression_lookup_cache_put(_key: str, _value: object) -> None:
        return None

    @staticmethod
    def _log_reaction_expression_event(*_args: object, **_kwargs: object) -> None:
        return None


class ReactionSearchContextCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_schema_uses_search_context_and_captures_host_context(self) -> None:
        signature = inspect.signature(PUBLIC_TOOL)
        self.assertIn("search_context", signature.parameters)
        self.assertNotIn("context", signature.parameters)
        self.assertEqual(inspect.Parameter.VAR_KEYWORD, signature.parameters["kwargs"].kind)

    async def test_search_context_wins_over_legacy_context(self) -> None:
        host = _ToolHost()
        event = object()

        self.assertEqual(
            "ok",
            await PUBLIC_TOOL(
                host,
                event,
                query="惊讶",
                search_context="新字段",
                context="旧字段",
                send=False,
            ),
        )
        self.assertIs(event, host.called_event)
        self.assertEqual("新字段", host.called_kwargs["search_context"])
        self.assertNotIn("context", host.called_kwargs)

    async def test_legacy_string_context_is_accepted_but_host_object_is_not_search_text(self) -> None:
        host = _ToolHost()
        event = object()

        await PUBLIC_TOOL(host, event, context="旧调用", send=False)
        self.assertEqual("旧调用", host.called_kwargs["search_context"])

        await PUBLIC_TOOL(host, event, context=SimpleNamespace(host_owned=True), send=False)
        self.assertEqual("", host.called_kwargs["search_context"])

    def test_internal_name_and_smart_imagechat_keyword_are_separated(self) -> None:
        # 反应表情域方法随拆分迁至 main_reaction_expression.py，跨宿主族聚合源码断言。
        main_source = "\n".join(
            p.read_text(encoding="utf-8")
            for p in [ROOT / "main.py", *sorted(ROOT.glob("main_*.py"))]
        )
        impl_source = llm_tool_actions_source_text(ROOT)

        self.assertIn("search_context: str = \"\"", main_source)
        self.assertIn("search_context=search_context", main_source)
        self.assertIn("query/search_context", impl_source)
        self.assertIn("search_context: str = \"\"", impl_source)
        self.assertIn("lookup_context = _single_line(search_context, 1000)", impl_source)
        self.assertIn("context=lookup_context", impl_source)

    async def test_absent_owned_library_is_a_controlled_unavailable_result(self) -> None:
        payload = json.loads(await REACTION_IMPL(_ReactionHost(None), object(), query="惊讶", send=False))

        self.assertEqual("unavailable", payload["status"])
        self.assertFalse(payload["success"])
        self.assertTrue(payload["must_not_claim_sent"])

    async def test_owned_library_receives_search_context_under_its_own_keyword(self) -> None:
        library = _ReactionLibrary({"success": False, "status": "not_found", "message": "no match"})

        payload = json.loads(
            await REACTION_IMPL(_ReactionHost(library), object(), query="惊讶", search_context="当前对话", send=False)
        )

        self.assertEqual("not_found", payload["status"])
        self.assertEqual([("惊讶", "当前对话", "test-scope")], library.calls)


if __name__ == "__main__":
    unittest.main()
