# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class ReminderRoutingHarness(DailyStateMixin):
    def __init__(self) -> None:
        self.enable_llm_timer_scheduling = True
        self._schedule_llm_timer = AsyncMock()

    @staticmethod
    def _private_user_role(_user) -> str:
        return "owner"

    @staticmethod
    def _environment_fromtimestamp(value: float) -> datetime:
        return datetime.fromtimestamp(float(value))


class ReminderRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = ReminderRoutingHarness()

    def test_timer_prompt_defines_memo_and_official_tool_boundaries(self):
        instruction = self.plugin._format_timer_scheduling_instruction({})
        self.assertIn("优先调用该工具", instruction)
        self.assertIn("只能选择 `future_task` 或 `<timer>` 其中一种", instruction)
        self.assertIn("应使用 `pc_manage_memo`", instruction)
        self.assertIn("动作查岗", instruction)

    async def test_saved_memo_reminder_strips_timer_but_does_not_schedule(self):
        text = '便签记好了。<timer>{"action":"cancel"}</timer>'
        cleaned, payloads = self.plugin._extract_timer_directives(text)
        self.assertEqual("便签记好了。", cleaned)
        self.assertEqual(1, len(payloads))

        event = SimpleNamespace(private_companion_memo_reminder_saved=True)
        result = await self.plugin._schedule_llm_timer_after_response_dedup(
            event,
            SimpleNamespace(tools_call_name=[]),
            "owner",
            payloads[0],
            source_text="帮我记一下明天交材料",
            visible_text=cleaned,
            trigger_umo="aiocqhttp:FriendMessage:owner",
        )
        self.assertEqual("memo_reminder", result)
        self.plugin._schedule_llm_timer.assert_not_awaited()

    async def test_generic_reminder_still_uses_timer_path(self):
        payload = {"scheduled_ts": 1_800_000_000, "topic": "交材料"}
        result = await self.plugin._schedule_llm_timer_after_response_dedup(
            SimpleNamespace(),
            SimpleNamespace(tools_call_name=[]),
            "owner",
            payload,
            source_text="明天提醒我交材料",
            visible_text="好，明天提醒你。",
        )
        self.assertEqual("scheduled", result)
        self.plugin._schedule_llm_timer.assert_awaited_once()

    async def test_activity_followup_still_uses_timer_path(self):
        payload = {
            "scheduled_ts": 1_800_000_000,
            "reason": "activity_followup",
            "activity": "洗澡",
        }
        result = await self.plugin._schedule_llm_timer_after_response_dedup(
            SimpleNamespace(),
            SimpleNamespace(tools_call_name=[]),
            "owner",
            payload,
            source_text="我去洗澡了",
            visible_text="去吧。",
        )
        self.assertEqual("scheduled", result)
        scheduled_payload = self.plugin._schedule_llm_timer.await_args.args[1]
        self.assertEqual("activity_followup", scheduled_payload["reason"])

    async def test_future_task_tool_call_suppresses_timer_for_string_and_list_names(self):
        payload = {"scheduled_ts": 1_800_000_000, "topic": "交材料"}
        for names in ("future_task", ["future_task"]):
            with self.subTest(names=names):
                self.plugin._schedule_llm_timer.reset_mock()
                result = await self.plugin._schedule_llm_timer_after_response_dedup(
                    SimpleNamespace(),
                    SimpleNamespace(tools_call_name=names),
                    "owner",
                    payload,
                    source_text="明天提醒我交材料",
                    visible_text="已经安排好了。",
                )
                self.assertEqual("official_task", result)
                self.plugin._schedule_llm_timer.assert_not_awaited()

    async def test_successful_future_task_result_sets_reliable_dedup_marker(self):
        event = SimpleNamespace()
        tool = SimpleNamespace(name="future_task")
        tool_result = SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Scheduled future task job-123456 (reminder) one-time at tomorrow.")],
        )
        self.assertTrue(
            self.plugin._record_future_task_result(
                event,
                tool,
                {"action": "create"},
                tool_result,
            )
        )

        result = await self.plugin._schedule_llm_timer_after_response_dedup(
            event,
            SimpleNamespace(tools_call_name=[]),
            "owner",
            {"scheduled_ts": 1_800_000_000, "topic": "交材料"},
            source_text="明天提醒我交材料",
            visible_text="已经安排好了。",
        )
        self.assertEqual("official_task", result)
        self.plugin._schedule_llm_timer.assert_not_awaited()

    async def test_failed_future_task_result_does_not_block_timer_fallback(self):
        event = SimpleNamespace()
        tool = SimpleNamespace(name="future_task")
        tool_result = SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="error: failed to schedule task due to invalid configuration.")],
        )
        self.assertFalse(
            self.plugin._record_future_task_result(
                event,
                tool,
                {"action": "create"},
                tool_result,
            )
        )

        result = await self.plugin._schedule_llm_timer_after_response_dedup(
            event,
            SimpleNamespace(tools_call_name=["future_task"]),
            "owner",
            {"scheduled_ts": 1_800_000_000, "topic": "交材料"},
            source_text="明天提醒我交材料",
            visible_text="官方定时失败，改用临时预约。",
        )
        self.assertEqual("scheduled", result)
        self.plugin._schedule_llm_timer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
