# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot.core.agent.tool import FunctionTool, ToolSet

from astrbot_plugin_private_companion.main import (
    PrivateCompanionPlugin,
    _PHOTO_TOOL_PROMPT_FORMAT_MARKER,
)


def _tool(name: str, description: str, *, active: bool = True) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
        active=active,
    )


class PhotoToolPromptFormatAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = object.__new__(PrivateCompanionPlugin)
        self.plugin.enabled = True
        self.plugin.photo_generation_prompt_format = "nai"
        self.plugin._image_companion_required = lambda: True
        self.plugin._image_companion_available = lambda: True

        def prompt_format_instruction() -> str:
            if self.plugin.photo_generation_prompt_format == "natural_language":
                return "使用连贯具体的英文句子描述画面"
            return "使用 NAI 4/4.5 标签格式：{tag}"

        self.plugin._photo_tool_prompt_format_instruction = prompt_format_instruction

    def test_request_tool_is_annotated_without_mutating_registered_tool(self) -> None:
        original = _tool("pc_generate_photo", "生成图片")
        other = _tool("other_tool", "其他工具")
        tool_set = ToolSet([other, original])
        req = SimpleNamespace(func_tool=tool_set)

        changed = self.plugin._annotate_photo_tool_prompt_format_for_request(req)

        annotated = req.func_tool.get_tool("pc_generate_photo")
        self.assertTrue(changed)
        self.assertIsNot(req.func_tool, tool_set)
        self.assertIsNot(annotated, original)
        self.assertEqual("生成图片", original.description)
        self.assertEqual([other, original], tool_set.tools)
        self.assertEqual([other, annotated], req.func_tool.tools)
        self.assertIn(_PHOTO_TOOL_PROMPT_FORMAT_MARKER, annotated.description)
        self.assertIn("NAI 4/4.5", annotated.description)
        self.assertIn("{tag}", annotated.description)

    def test_annotation_is_idempotent_and_follows_mode_switch(self) -> None:
        tool_set = ToolSet([_tool("pc_generate_photo", "生成图片")])
        req = SimpleNamespace(func_tool=tool_set)

        self.assertTrue(self.plugin._annotate_photo_tool_prompt_format_for_request(req))
        self.plugin.photo_generation_prompt_format = "natural_language"
        self.assertTrue(self.plugin._annotate_photo_tool_prompt_format_for_request(req))

        description = req.func_tool.get_tool("pc_generate_photo").description
        self.assertEqual(2, description.count(_PHOTO_TOOL_PROMPT_FORMAT_MARKER))
        self.assertNotIn("NAI 4/4.5", description)
        self.assertIn("连贯具体的英文句子", description)

    def test_missing_or_inactive_photo_tool_is_unchanged(self) -> None:
        missing = SimpleNamespace(func_tool=ToolSet([_tool("other_tool", "其他")]))
        inactive_tool = _tool("pc_generate_photo", "生成图片", active=False)
        inactive = SimpleNamespace(func_tool=ToolSet([inactive_tool]))

        self.assertFalse(
            self.plugin._annotate_photo_tool_prompt_format_for_request(missing)
        )
        self.assertFalse(
            self.plugin._annotate_photo_tool_prompt_format_for_request(inactive)
        )
        self.assertEqual("生成图片", inactive_tool.description)

    def test_unavailable_image_extension_is_removed_only_from_current_request(self) -> None:
        original = _tool("pc_generate_photo", "生成图片")
        other = _tool("other_tool", "其他工具")
        registered = ToolSet([other, original])
        req = SimpleNamespace(func_tool=registered)
        self.plugin._image_companion_available = lambda: False

        changed = self.plugin._scope_photo_generation_tool_for_request(req)

        self.assertTrue(changed)
        self.assertIsNot(req.func_tool, registered)
        self.assertIsNone(req.func_tool.get_tool("pc_generate_photo"))
        self.assertIs(req.func_tool.get_tool("other_tool"), other)
        self.assertEqual([other, original], registered.tools)
        self.assertFalse(self.plugin._annotate_photo_tool_prompt_format_for_request(req))

    def test_missing_owner_format_helper_skips_annotation(self) -> None:
        original = _tool("pc_generate_photo", "生成图片")
        req = SimpleNamespace(func_tool=ToolSet([original]))
        self.plugin._photo_tool_prompt_format_instruction = None

        self.assertFalse(self.plugin._annotate_photo_tool_prompt_format_for_request(req))
        self.assertIs(req.func_tool.get_tool("pc_generate_photo"), original)
        self.assertEqual("生成图片", original.description)


if __name__ == "__main__":
    unittest.main()
