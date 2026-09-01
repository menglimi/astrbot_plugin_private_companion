# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.agent.tool import FunctionTool, ToolSet

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

from .test_photo_tool_delivery_contract import (
    _CommandEntryPhotoHarness,
    _FakeEvent,
    _PhotoToolHarness,
)


def _tool(name: str) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        active=True,
    )


class UserRequestedPhotoGenerationTests(unittest.IsolatedAsyncioTestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def _plugin(self, *, scope: str = "private_friend") -> PrivateCompanionPlugin:
        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_photo_text_action = True
        plugin.enable_user_requested_photo_generation = False
        plugin.natural_language_photo_generation_mode = "tool_first"
        plugin._image_companion_required = lambda: True
        plugin._image_companion_available = lambda: True
        plugin._photo_generation_scope = lambda _event=None, **_kwargs: scope
        plugin._user_requested_photo_generation_allowed = (
            lambda _event=None, **_kwargs: scope == "proactive"
        )
        return plugin

    def test_passive_request_removes_photo_tool_but_keeps_other_tools(self) -> None:
        plugin = self._plugin()
        original = ToolSet([_tool("pc_generate_photo"), _tool("other_tool")])
        req = SimpleNamespace(func_tool=original)

        self.assertTrue(plugin._scope_photo_generation_tool_for_request(req, _FakeEvent()))
        self.assertIsNone(req.func_tool.get_tool("pc_generate_photo"))
        self.assertIsNotNone(req.func_tool.get_tool("other_tool"))
        self.assertIsNotNone(original.get_tool("pc_generate_photo"))

    def test_proactive_request_keeps_photo_tool_when_user_switch_is_off(self) -> None:
        plugin = self._plugin(scope="proactive")
        event = _FakeEvent()
        event.private_companion_proactive_framework = True
        req = SimpleNamespace(func_tool=ToolSet([_tool("pc_generate_photo")]))

        self.assertFalse(plugin._scope_photo_generation_tool_for_request(req, event))
        self.assertIsNotNone(req.func_tool.get_tool("pc_generate_photo"))
        self.assertTrue(plugin._user_photo_generation_prompt_enabled(event))

    def test_dynamic_prompt_keeps_gallery_rules_without_photo_rules(self) -> None:
        harness = _PhotoToolHarness()
        harness.enable_user_requested_photo_generation = False
        harness._reaction_image_provider_available = lambda: True

        instruction = harness._photo_generation_tool_instruction(
            include_heading=False,
        )

        self.assertIn("pc_find_reaction_image", instruction)
        self.assertNotIn("pc_generate_photo", instruction)
        self.assertNotIn("【图库表情与生图工具】", instruction)
        self.assertNotIn("【实验性表情表达】", instruction)

    def test_dynamic_prompt_is_empty_when_neither_capability_is_available(self) -> None:
        harness = _PhotoToolHarness()
        harness.enable_user_requested_photo_generation = False
        harness._reaction_image_provider_available = lambda: False

        self.assertEqual(
            "",
            harness._photo_generation_tool_instruction(include_heading=False),
        )

    def test_dynamic_prompt_can_expose_photo_without_gallery_rules(self) -> None:
        harness = _PhotoToolHarness()
        harness._reaction_image_provider_available = lambda: False

        instruction = harness._photo_generation_tool_instruction(
            include_heading=False,
        )

        self.assertIn("pc_generate_photo", instruction)
        self.assertNotIn("pc_find_reaction_image", instruction)

    def test_main_chain_spontaneous_prompt_has_no_legacy_heading(self) -> None:
        harness = _PhotoToolHarness()
        harness._reaction_image_provider_available = lambda: True

        instruction = harness._photo_generation_tool_instruction(
            include_spontaneous=True,
            spontaneous_only=True,
            include_heading=False,
        )

        self.assertIn("<pc_reaction_expression>", instruction)
        self.assertNotIn("【实验性表情表达】", instruction)

    async def test_tool_execution_rejects_passive_user_request(self) -> None:
        harness = _PhotoToolHarness()
        harness.enable_user_requested_photo_generation = False

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张海边照片",
            )
        )

        self.assertEqual("disabled", payload["status"])
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertIn("关闭用户请求生图", payload["message"])

    async def test_explicit_command_rejects_when_user_switch_is_off(self) -> None:
        harness = _CommandEntryPhotoHarness()
        harness.enable_user_requested_photo_generation = False
        event = _FakeEvent()

        handled = await harness._handle_companion_photo_command(
            event,
            "10001",
            "生图",
            "海边晚霞",
        )

        self.assertTrue(handled)
        self.assertTrue(event.stopped)
        self.assertEqual(["管理员已关闭用户请求生图/改图。"], harness.replies)

    def test_schema_and_webui_keep_proactive_limit_visible(self) -> None:
        schema = json.loads(
            (self.ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        items = schema["photo_action_config"]["items"]
        switch = items["enable_user_requested_photo_generation"]
        self.assertTrue(switch["default"])
        for key in (
            "photo_generation_private_owner_max_daily",
            "photo_generation_private_friend_max_daily",
            "photo_generation_group_max_daily",
            "command_photo_generation_max_daily",
        ):
            self.assertTrue(
                items[key]["condition"]["enable_user_requested_photo_generation"]
            )
        self.assertNotIn(
            "enable_user_requested_photo_generation",
            items["photo_generation_proactive_max_daily"]["condition"],
        )
        self.assertFalse(
            PrivateCompanionPageApi(None)._normalize_setting_value(
                "enable_user_requested_photo_generation",
                False,
            )
        )

        scripts = [
            (self.ROOT / "pages" / page / "app.js").read_text(encoding="utf-8")
            for page in ("companion-panel", "陪伴面板")
        ]
        self.assertEqual(scripts[0], scripts[1])
        self.assertIn('title: "生图数量限制"', scripts[0])
        self.assertIn('"enable_user_requested_photo_generation"', scripts[0])
        self.assertIn(
            'if (userRequestLimits.has(settingKey) && !enabled("enable_user_requested_photo_generation")) return false;',
            scripts[0],
        )


if __name__ == "__main__":
    unittest.main()
