import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_host.calendar_command import (
    calendar_error_text,
    calendar_result_text,
    parse_calendar_command,
)


class CalendarCommandTests(unittest.TestCase):
    def test_recognizes_only_complete_supported_private_command_text(self):
        for text, refresh in (
            ("生成日程", False),
            ("陪伴 生成日程", False),
            ("/陪伴 生成日程", False),
            ("刷新日程", True),
            ("重新生成日程", True),
            ("生成周日程", False),
            ("刷新周日程", True),
            ("重新生成周日程", True),
        ):
            with self.subTest(text=text):
                command = parse_calendar_command(text)
                self.assertIsNotNone(command)
                self.assertEqual((command.mode, command.refresh),
                                 ("horizon" if "周" in text else "daily", refresh))

        for text in ("帮我生成日程", "生成日程谢谢", "生成日程 明天", "/生成日程 now", ""):
            with self.subTest(text=text):
                self.assertIsNone(parse_calendar_command(text))

    def test_reply_reports_committed_result_or_replay(self):
        self.assertEqual(calendar_result_text({"state": "succeeded", "saved": 3, "replaced": 2}),
                         "今天的日程已生成，共写入 3 项。已替换 2 项旧的自动日程。")
        self.assertEqual(calendar_result_text({"state": "succeeded", "mode": "horizon", "saved": 4}),
                         "未来日程已生成，共写入 4 项。")
        self.assertIn("没有重复写入", calendar_result_text({"state": "already_attempted"}))

    def test_calendar_and_automatic_planning_switches_have_distinct_guidance(self):
        self.assertIn("角色日程总开关", calendar_error_text("calendar_disabled"))
        self.assertIn("自动规划已关闭", calendar_error_text("planning_disabled"))
        self.assertNotIn("此会话", calendar_error_text("calendar_disabled"))


class CalendarCommandDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_command_runs_planner_and_group_text_is_left_alone(self):
        from astrbot_host.calendar_command import receive_calendar_command

        event = SimpleNamespace(unified_msg_origin="qq:FriendMessage:200", text="刷新日程")
        route = SimpleNamespace(login="100", peer="200")
        session = SimpleNamespace(plan_calendar=AsyncMock(return_value={"state": "succeeded", "saved": 2,
                                                                          "replaced": 1, "rejected": []}))
        with (patch("astrbot_host.calendar_command.authenticate_notice_route", return_value=route),
              patch("astrbot_host.calendar_command.send_private_notice", new_callable=AsyncMock,
                    return_value={"state": "delivered"}) as send):
            result = await receive_calendar_command(SimpleNamespace(), event, session)

        self.assertTrue(result.is_success)
        session.plan_calendar.assert_awaited_once_with(event, mode="daily", refresh=True)
        self.assertEqual(send.await_args.kwargs["text"],
                         "今天的日程已生成，共写入 2 项。已替换 1 项旧的自动日程。")

        group = SimpleNamespace(unified_msg_origin="qq:GroupMessage:300_200", text="生成日程")
        self.assertIsNone(await receive_calendar_command(SimpleNamespace(), group, session))
        session.plan_calendar.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
