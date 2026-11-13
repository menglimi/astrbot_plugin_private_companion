import unittest
from types import SimpleNamespace

from astrbot_host.functional_commands import (
    FunctionalCommand,
    command_instruction,
    execute_functional_command,
    inject_functional_command_instruction,
    parse_functional_command,
)


class FunctionalCommandParsingTests(unittest.TestCase):
    def test_private_read_and_image_commands_are_recognized(self):
        cases = (
            ("陪伴 状态", "status"),
            ("/私聊陪伴 长期记忆 咖啡", "memory"),
            ("主动陪伴 日期添加 纪念日 2026-11-02", "dates_add"),
            ("陪伴 生图 一只白猫", "image_generate"),
            ("陪伴 自拍 窗边", "image_generate"),
            ("陪伴 改图 换成海边", "image_edit"),
            ("陪伴合照 把背景换成海边", "image_edit"),
            ("陪伴 查看生图API", "manage_image"),
            ("陪伴 今日穿搭图", "manage_image"),
        )
        for text, intent in cases:
            with self.subTest(text=text):
                command = parse_functional_command(text, channel="private")
                self.assertEqual((command.surface, command.intent), ("private", intent))

    def test_exploration_notes_command_reports_private_research_only(self):
        command = parse_functional_command("陪伴 探索笔记", channel="private")
        self.assertEqual(command.intent, "exploration_notes")
        service = SimpleNamespace(read=lambda _context: {"notes": [{
            "label": "对方生日", "title": "摄影爱好者生日礼物推荐",
            "source": "摄影杂志", "share_policy": "private_note"}], "last_result": "notes"})
        application = SimpleNamespace(contact_schedule=SimpleNamespace(exploration=service))
        receipt = execute_functional_command(command, application=application, context=object())
        self.assertIn("摄影爱好者生日礼物推荐", receipt)
        self.assertIn("仅准备", receipt)
        group = parse_functional_command("陪伴群 探索笔记", channel="group")
        self.assertIsNone(group)

    def test_group_commands_use_group_surface(self):
        current = parse_functional_command("/群陪伴 黑话 贴贴", channel="group")
        self.assertEqual((current.surface, current.intent), ("group", "group_slang"))
        self.assertEqual(current.argument, "贴贴")
        current = parse_functional_command("陪伴群 片段 昨晚的约定", channel="group")
        self.assertEqual((current.surface, current.intent), ("group", "group_memory"))
        self.assertEqual(current.argument, "昨晚的约定")
        self.assertIsNone(parse_functional_command("陪伴群 黑话", channel="private"))
        self.assertIsNone(parse_functional_command("陪伴 状态", channel="group"))

    def test_command_matching_requires_a_known_leading_function(self):
        for text in (
            "你好，陪伴 状态",
            "陪伴 状态谢谢",
            "陪伴 未知功能",
            "引用：陪伴 状态",
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_functional_command(text, channel="private"))

    def test_configuration_and_reset_commands_point_to_the_management_page(self):
        command = parse_functional_command("陪伴 绑定城市 杭州", channel="private")
        self.assertIn("陪伴小屋", command_instruction(command))
        self.assertIn("不要模拟写入", command_instruction(command))

    def test_legacy_function_command_families_are_recognized_without_claiming_missing_actions(self):
        cases = (
            ("HDSI 统计", "manage_hdsi"),
            ("陪伴 答疑确认 123", "diagnostics_confirm"),
            ("陪伴 未完话头删除 午饭", "open_loop_delete"),
            ("陪伴 未完成", "manage_memory"),
            ("陪伴 重要日期添加 生日 5月20日", "dates_add"),
            ("陪伴 删除日程 复诊", "calendar"),
            ("HDSI 观察", "manage_hdsi"),
            ("陪伴 今日穿搭", "manage_image"),
            ("陪伴 交换生图API", "manage_image"),
            ("陪伴 夹层密码", "retired_secret"),
            ("陪伴群 关闭LLM", "group_llm_block"),
            ("陪伴群 插话反馈", "manage_group"),
        )
        for text, intent in cases:
            with self.subTest(text=text):
                command = parse_functional_command(text, channel="private" if not text.startswith("陪伴群") else "group")
                self.assertEqual((command.surface, command.intent),
                                 ("group" if text.startswith("陪伴群") else "private", intent))

        date_command = parse_functional_command("陪伴 日期添加 纪念日 2026-11-02", channel="private")
        self.assertIn("独立回执", command_instruction(date_command))
        self.assertNotIn("companion_v2_calendar_command", command_instruction(date_command))
        self.assertIn("没有更改设置", command_instruction(parse_functional_command("HDSI 统计", channel="private")))
        self.assertIn("不提供", command_instruction(parse_functional_command("陪伴 输出夹层密码", channel="private")))

    def test_group_llm_control_commands_keep_their_own_intents(self):
        cases = (
            ("陪伴群 关闭LLM", "group_llm_block"),
            ("陪伴群 禁用LLM", "group_llm_block"),
            ("陪伴群 关闭主链", "group_llm_block"),
            ("陪伴群 开启LLM", "group_llm_release"),
            ("陪伴群 恢复主链", "group_llm_release"),
            ("陪伴群 LLM状态", "group_llm_status"),
            ("陪伴群 主链状态", "group_llm_status"),
        )
        for text, intent in cases:
            with self.subTest(text=text):
                command = parse_functional_command(text, channel="group")
                self.assertEqual(command.intent, intent)
        self.assertIn("没有更改群开关",
                      command_instruction(parse_functional_command("陪伴群 关闭LLM", channel="group")))

    def test_tool_backed_commands_share_the_receipt_contract(self):
        for text, channel in (("陪伴 日程", "private"), ("陪伴 记忆 咖啡", "private"),
                              ("陪伴 画像", "private"), ("陪伴群 黑话 贴贴", "group"),
                              ("陪伴 生图 猫", "private")):
            with self.subTest(text=text):
                instruction = command_instruction(parse_functional_command(text, channel=channel))
                self.assertIn("没有执行", instruction)
                self.assertIn("未知结果不重试", instruction)
        guide = command_instruction(parse_functional_command("陪伴 重置人格", channel="private"))
        self.assertNotIn("未知结果不重试", guide)

    def test_open_loop_delete_uses_the_memo_tools(self):
        instruction = command_instruction(parse_functional_command("陪伴 删除话头 午饭", channel="private"))
        self.assertIn("companion_v2_memo_list", instruction)
        self.assertIn("companion_v2_memo_delete", instruction)
        self.assertIn("expected_revision", instruction)

    def test_repair_confirmation_is_an_exact_private_command(self):
        confirm = parse_functional_command("陪伴 答疑确认 repair-abc", channel="private")
        cancel = parse_functional_command("陪伴 答疑取消 repair-abc", channel="private")
        self.assertEqual(confirm, FunctionalCommand("private", "diagnostics_confirm", "repair-abc"))
        self.assertEqual(cancel, FunctionalCommand("private", "diagnostics_cancel", "repair-abc"))
        self.assertIn("companion_v2_diagnostics_proposal_confirm", command_instruction(confirm))
        self.assertIn("proposal_id 必须逐字等于该编号", command_instruction(confirm))
        self.assertIn("companion_v2_diagnostics_proposal_cancel", command_instruction(cancel))
        self.assertEqual(parse_functional_command("陪伴 答疑确认 repair-abc 额外文字", channel="private").intent,
                         "manage_diagnostics")

    def test_tool_backed_commands_require_the_current_authorized_tool(self):
        command = parse_functional_command("陪伴 记忆 午饭", channel="private")
        instruction = command_instruction(command)
        self.assertIn("companion_v2_memory_query", instruction)
        self.assertIn("不因未命中断言从未发生", instruction)

    def test_clear_memory_keeps_the_confirmation_argument(self):
        command = parse_functional_command("陪伴 清空记忆", channel="private")
        self.assertEqual(command, FunctionalCommand("private", "memory_clear", ""))
        confirm = parse_functional_command("陪伴 清空记忆 确认 4f2a1c", channel="private")
        self.assertEqual(confirm.argument, "确认 4f2a1c")
        self.assertIn("回执", command_instruction(command))


class _FakeMemoryManagement:
    """Stand-in for the domain service; the real one is covered by memory tests."""

    instances = []

    def __init__(self, memory, operator):
        self.operator = operator
        type(self).instances.append(self)
        self.cleared = 0

    @staticmethod
    def confirmation_code(token):
        return "4F2A1C"

    def preview_clear(self, context):
        from companion_v2.foundation.outcome import Outcome
        return Outcome.success({"counts": {"active": 2, "already_inactive": 1}, "revision_token": "token-1"})

    def clear(self, context, *, expected_revision_token, operation_key):
        from companion_v2.foundation.outcome import Outcome
        self.cleared += 1
        self.operation = (expected_revision_token, operation_key)
        return Outcome.success({"forgotten_count": 2, "physical_erasure": "not_performed"})


class FunctionalCommandExecutionTests(unittest.TestCase):
    def setUp(self):
        from companion_v2.foundation.refs import ScopeKind
        from types import SimpleNamespace
        _FakeMemoryManagement.instances = []
        self.context = SimpleNamespace(key="context-key", scope=SimpleNamespace(kind=ScopeKind.PRIVATE))
        self.application = SimpleNamespace(memory=object())

    def event(self):
        from types import SimpleNamespace
        event = SimpleNamespace()
        event.get_extra = lambda name, default=None: getattr(event, name, default)
        event.set_extra = lambda name, value: setattr(event, name, value)
        return event

    def test_clear_memory_previews_then_executes_once_and_reuses_the_receipt(self):
        from unittest.mock import patch
        from astrbot_host.functional_commands import functional_command_receipt

        with patch("companion_v2.memory.management.MemoryManagement", _FakeMemoryManagement):
            preview = functional_command_receipt(
                self.event(), parse_functional_command("陪伴 清空记忆", channel="private"),
                application=self.application, context=self.context)
            self.assertIn("尚未执行", preview)
            self.assertIn("4F2A1C", preview)
            self.assertIn("3 条", preview)

            event = self.event()
            confirmed = functional_command_receipt(
                event, parse_functional_command("陪伴 清空记忆 确认 4f2a1c", channel="private"),
                application=self.application, context=self.context)
            self.assertIn("已执行", confirmed)
            self.assertIn("2 条", confirmed)
            self.assertIn("未物理删除", confirmed)
            replay = functional_command_receipt(
                event, parse_functional_command("陪伴 清空记忆 确认 4f2a1c", channel="private"),
                application=self.application, context=self.context)
            self.assertEqual(replay, confirmed)
            self.assertEqual(sum(item.cleared for item in _FakeMemoryManagement.instances), 1)

    def test_wrong_code_and_missing_context_never_clear(self):
        from unittest.mock import patch
        from astrbot_host.functional_commands import functional_command_receipt

        with patch("companion_v2.memory.management.MemoryManagement", _FakeMemoryManagement):
            wrong = functional_command_receipt(
                self.event(), parse_functional_command("陪伴 清空记忆 确认 000000", channel="private"),
                application=self.application, context=self.context)
            self.assertIn("未执行", wrong)
            self.assertEqual(_FakeMemoryManagement.instances[-1].cleared, 0)
            empty = functional_command_receipt(
                self.event(), parse_functional_command("陪伴 清空记忆 确认 4F2A1C", channel="private"),
                application=None, context=None)
            self.assertEqual(empty, "")


class FunctionalCommandInjectionTests(unittest.TestCase):
    def test_injection_replaces_owned_instruction_and_competing_commands_win(self):
        from unittest.mock import patch

        request = SimpleNamespace(system_prompt="role", extra_user_content_parts=[])
        event = SimpleNamespace(text="陪伴 状态")
        with patch("astrbot_host.event_routing.competing_command_handler", return_value=None):
            command = inject_functional_command_instruction(event, request, channel="private")
        self.assertEqual(command.intent, "status")
        self.assertIn("functional_command:start", request.system_prompt)

        request.system_prompt = "role"
        with patch("astrbot_host.event_routing.competing_command_handler", return_value=object()):
            command = inject_functional_command_instruction(event, request, channel="private")
        self.assertIsNone(command)
        self.assertNotIn("functional_command:start", request.system_prompt)


if __name__ == "__main__":
    unittest.main()
