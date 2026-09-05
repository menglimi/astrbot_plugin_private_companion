# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.group_command_system import (
    GROUP_COMMAND_HELP,
    GroupLLMAction,
    format_llm_blocked,
    format_llm_status,
    parse_group_command,
)


class GroupCommandSystemTests(unittest.TestCase):
    def test_parser_separates_action_value_and_classifies_alias(self) -> None:
        request = parse_group_command("陪伴群 关闭 LLM回复")
        self.assertEqual("关闭", request.action)
        self.assertEqual("LLM回复", request.value)
        self.assertEqual("关闭llm回复", request.compact)
        self.assertIs(GroupLLMAction.BLOCK, request.llm_action)
        self.assertTrue(request.requires_management)

    def test_parser_preserves_empty_and_unknown_actions(self) -> None:
        empty = parse_group_command("陪伴群")
        unknown = parse_group_command("陪伴群 不存在 参数")
        self.assertEqual(("", "", None), (empty.action, empty.value, empty.llm_action))
        self.assertEqual(("不存在", "参数", None), (unknown.action, unknown.value, unknown.llm_action))
        self.assertFalse(unknown.requires_management)

    def test_management_classification_preserves_recall_permission(self) -> None:
        self.assertTrue(parse_group_command("陪伴群 撤回消息").requires_management)
        self.assertFalse(parse_group_command("陪伴群 状态").requires_management)

    def test_response_formatters_preserve_public_text(self) -> None:
        self.assertEqual(
            "已关闭本群所有 LLM 回复。\n群号：42\n状态：拦截中（刚刚）\n恢复：陪伴群 开启LLM",
            format_llm_blocked("42", "刚刚"),
        )
        self.assertEqual(
            "本群 LLM 回复当前关闭中，开启时间：刚刚。\n恢复：陪伴群 开启LLM",
            format_llm_status(blocked=True, elapsed="刚刚"),
        )
        self.assertEqual("本群 LLM 回复当前未被单独关闭。", format_llm_status(blocked=False))
        self.assertIn("陪伴群 关闭LLM", GROUP_COMMAND_HELP)
        self.assertIn("陪伴群 关闭", GROUP_COMMAND_HELP)


if __name__ == "__main__":
    unittest.main()
