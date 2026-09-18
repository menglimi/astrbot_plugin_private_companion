# -*- coding: utf-8 -*-
"""衣柜只读工具 pc_query_wardrobe_detail：注册契约、数据组装与按请求挂载。"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.wardrobe import (
    WARDROBE_PROMPT_MAX_CHARS,
    normalize_wardrobe_items,
)
from astrbot_plugin_private_companion.wardrobe_runtime import (
    WARDROBE_DETAIL_TOOL_HINT,
    WARDROBE_DETAIL_TOOL_NAME,
    WardrobeMixin,
)

ROOT = Path(__file__).resolve().parents[1]


def _llm_tool_functions() -> dict[str, ast.AsyncFunctionDef]:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    tools: dict[str, ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            target = decorator.func
            if isinstance(target, ast.Attribute) and target.attr == "llm_tool":
                tools[node.name] = node
    return tools


class _WardrobeDetailHarness(WardrobeMixin):
    """只读工具走真实代码路径需要的最小替身。"""

    def __init__(self, *, items=None, outfits=None, enabled=True, prompt=True) -> None:
        self.config = {
            "enable_wardrobe": enabled,
            "enable_wardrobe_prompt": prompt,
            "wardrobe_tendency": "偏爱宽松针织",
            "wardrobe_items": list(items or []),
            "wardrobe_outfits": list(outfits or []),
            "wardrobe_outfit_mode": "inventory",
            "wardrobe_injection_detail": "full",
            "wardrobe_outfit_rotation_days": 7,
        }
        # 注：不再需要「取原始工具对象」的替身 —— 同步逻辑只摘不挂。

    def persona_setting(self, key: str, default: object = None) -> object:
        return self.config.get(key, default)

    async def _save_config_if_possible(self) -> bool:
        return True



def _harness(**kwargs) -> _WardrobeDetailHarness:
    items = normalize_wardrobe_items(
        [
            {"name": "白色纯棉T恤", "description": "基础圆领", "slot": "upper"},
            {"name": "深蓝直筒牛仔裤", "description": "经典款", "slot": "lower"},
            {"name": "白色帆布鞋", "description": "低帮", "slot": "feet"},
            {"name": "细框眼镜", "description": "轻量", "slot": "extra"},
        ]
    )
    return _WardrobeDetailHarness(items=items, **kwargs)


class WardrobeDetailToolRegistrationTests(unittest.TestCase):
    """注册契约：宿主靠 docstring 建 schema，靠 allowlist 剥离纯文本调用。"""

    def test_tool_is_registered_with_documented_arguments(self) -> None:
        tools = _llm_tool_functions()
        self.assertIn("pc_query_wardrobe_detail", tools)
        function = tools["pc_query_wardrobe_detail"]
        docstring = ast.get_docstring(function) or ""
        # 宿主用 docstring_parser 解析 Args 段建参数表，缺类型会直接抛错。
        self.assertIn("scope(string):", docstring)
        self.assertIn("slot(string):", docstring)
        argument_names = {argument.arg for argument in function.args.args}
        self.assertIn("event", argument_names)
        # 框架保留参数：作者的既有用例专门盯着这一条。
        self.assertNotIn("context", argument_names)

    def test_tool_name_is_in_the_plaintext_allowlist(self) -> None:
        # 模型把工具调用当纯文本吐出来时靠这张表识别并剥离；漏掉就会在聊天里漏出 JSON。
        source = (ROOT / "llm_tool_actions.py").read_text(encoding="utf-8")
        self.assertIn(f'"{WARDROBE_DETAIL_TOOL_NAME}"', source)


class WardrobeDetailPayloadTests(unittest.TestCase):
    def test_today_scope_lists_the_decided_outfit_with_descriptions(self) -> None:
        payload = _harness()._wardrobe_detail_payload("today")
        self.assertEqual("ok", payload["status"])
        self.assertIn("今天这身", payload["text"])
        self.assertIn("白色纯棉T恤", payload["text"])
        self.assertIn("基础圆领", payload["text"])
        self.assertIn("白色帆布鞋", payload["text"])

    def test_default_scope_is_today(self) -> None:
        payload = _harness()._wardrobe_detail_payload()
        self.assertEqual("today", payload["scope"])

    def test_slot_scope_returns_only_that_slot(self) -> None:
        payload = _harness()._wardrobe_detail_payload("slot", "feet")
        self.assertEqual(1, payload["count"])
        self.assertIn("白色帆布鞋", payload["text"])
        self.assertNotIn("白色纯棉T恤", payload["text"])

    def test_slot_scope_accepts_the_words_the_model_is_likely_to_use(self) -> None:
        for word, expected in (
            ("鞋", "白色帆布鞋"),
            ("上装", "白色纯棉T恤"),
            ("裙子", "深蓝直筒牛仔裤"),
            # 衣柜里没有整身：回一句「还没有」，而不是报错或编一件出来。
            ("连衣裙", "还没有衣物"),
        ):
            payload = _harness()._wardrobe_detail_payload("slot", word)
            self.assertIn(expected, payload["text"], word)

    def test_slot_scope_without_a_slot_asks_for_one(self) -> None:
        payload = _harness()._wardrobe_detail_payload("slot", "")
        self.assertEqual("need_slot", payload["status"])

    def test_all_scope_lists_the_whole_wardrobe(self) -> None:
        payload = _harness()._wardrobe_detail_payload("all")
        self.assertIn("衣柜共 4 件可穿散件", payload["text"])
        self.assertIn("细框眼镜", payload["text"])
        self.assertIn("偏爱宽松针织", payload["text"])

    def test_disabled_wardrobe_reports_unavailable_instead_of_raising(self) -> None:
        payload = _harness(enabled=False)._wardrobe_detail_payload("today")
        self.assertEqual("unavailable", payload["status"])
        self.assertTrue(payload["text"])

    def test_reply_is_json_and_degrades_instead_of_raising(self) -> None:
        plugin = _harness()
        parsed = json.loads(plugin._wardrobe_detail_reply("all"))
        self.assertEqual("ok", parsed["status"])

        def boom():
            raise RuntimeError("boom")

        plugin._wardrobe_items = boom  # type: ignore[method-assign]
        parsed = json.loads(plugin._wardrobe_detail_reply("all"))
        self.assertEqual("error", parsed["status"])


class WardrobeDetailMountTests(unittest.TestCase):
    """按请求同步：衣柜没开就摘掉；开着只回答「在不在」，**不往工具表里塞东西**。

    不代挂是刻意的：宿主本来就会按工具自身的 active 状态、人格 tools 白名单与
    tool_permissions 决定这次请求带哪些工具。自己把原始对象塞回去会复活被管理员
    停用的工具，并绕过权限代理。
    """

    @staticmethod
    def _request(tools):
        if tools is None:
            return SimpleNamespace(func_tool=None)
        return SimpleNamespace(func_tool=SimpleNamespace(tools=list(tools)))

    def test_request_without_a_tool_channel_is_left_alone(self) -> None:
        plugin = _harness()
        request = SimpleNamespace(func_tool=None)
        self.assertFalse(plugin._sync_wardrobe_detail_tool(request))
        self.assertIsNone(request.func_tool)

    def test_present_tool_is_reported_without_duplicating(self) -> None:
        plugin = _harness()
        request = self._request(
            [SimpleNamespace(name="other_tool"), SimpleNamespace(name=WARDROBE_DETAIL_TOOL_NAME)]
        )
        self.assertTrue(plugin._sync_wardrobe_detail_tool(request))
        self.assertEqual(2, len(request.func_tool.tools))

    def test_absent_tool_is_not_force_mounted(self) -> None:
        # 人格白名单排除、或管理员停用了它：尊重宿主的选择，不代挂。
        plugin = _harness()
        request = self._request([SimpleNamespace(name="other_tool")])
        self.assertFalse(plugin._sync_wardrobe_detail_tool(request))
        self.assertEqual(["other_tool"], [tool.name for tool in request.func_tool.tools])

    def test_disabled_wardrobe_strips_the_tool(self) -> None:
        plugin = _harness(enabled=False)
        request = self._request(
            [SimpleNamespace(name=WARDROBE_DETAIL_TOOL_NAME), SimpleNamespace(name="other_tool")]
        )
        self.assertFalse(plugin._sync_wardrobe_detail_tool(request))
        self.assertEqual(["other_tool"], [tool.name for tool in request.func_tool.tools])

    def test_inactive_tool_is_never_mounted(self) -> None:
        # 回归：曾经用 get_func() 取原始对象塞回请求，会把 active=False 的工具复活。
        plugin = _harness()
        request = self._request([SimpleNamespace(name="other_tool")])
        self.assertFalse(plugin._sync_wardrobe_detail_tool(request))
        self.assertEqual(["other_tool"], [tool.name for tool in request.func_tool.tools])
        self.assertFalse(
            hasattr(WardrobeMixin, "_wardrobe_detail_tool"),
            "不该再留取原始对象的入口（那会把停用的工具复活、并绕过权限代理）",
        )


class WardrobeDetailSectionTests(unittest.TestCase):
    def test_hint_appears_only_when_the_tool_is_mounted(self) -> None:
        plugin = _harness()
        without = plugin._wardrobe_prompt_section(None, "")
        with_tool = plugin._wardrobe_prompt_section(None, "", detail_tool=True)
        assert without is not None and with_tool is not None
        self.assertNotIn(WARDROBE_DETAIL_TOOL_NAME, str(without.content))
        self.assertIn(WARDROBE_DETAIL_TOOL_NAME, str(with_tool.content))
        self.assertIn(WARDROBE_DETAIL_TOOL_HINT, str(with_tool.content))
        self.assertLessEqual(len(str(with_tool.content)), WARDROBE_PROMPT_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
