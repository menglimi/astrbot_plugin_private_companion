# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin


TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 14, 10, 0, tzinfo=TZ).timestamp()


class FakeEvent:
    def __init__(self, sender_id: str = "owner", *, private: bool = True, message_str: str = "") -> None:
        self.sender_id = sender_id
        self.private = private
        self.message_str = message_str
        self.extras: dict[str, object] = {}
        self.unified_msg_origin = f"aiocqhttp:{'FriendMessage' if private else 'GroupMessage'}:{sender_id}"

    def get_sender_id(self) -> str:
        return self.sender_id

    def is_private_chat(self) -> bool:
        return self.private

    def get_extra(self, key: str):
        return self.extras.get(key)


class MemoToolHarness(LlmToolActionsMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {"memo_notes": []}
        self.saved = 0
        self.fail_save = False

    @staticmethod
    def _permission_identity_id(value) -> str:
        return str(value or "")

    @staticmethod
    def _is_private_companion_owner_user_id(value) -> bool:
        return str(value or "") == "owner"

    @staticmethod
    def _environment_fromtimestamp(value: float) -> datetime:
        return datetime.fromtimestamp(float(value), TZ)

    def _save_data_sync(self, **_kwargs) -> None:
        if self.fail_save:
            raise OSError("disk unavailable")
        self.saved += 1


class FakeToolSet:
    def __init__(self, *names: str) -> None:
        self.tools = [SimpleNamespace(name=name) for name in names]

    def get_tool(self, name: str):
        return next((tool for tool in self.tools if tool.name == name), None)

    def remove_tool(self, name: str) -> None:
        self.tools = [tool for tool in self.tools if tool.name != name]


class MemoChatToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = MemoToolHarness()
        self.event = FakeEvent()

    async def call(self, *, at: float = NOW, **kwargs):
        with patch("astrbot_plugin_private_companion.llm_tool_actions.time.time", return_value=at):
            result = await self.plugin._pc_manage_memo_impl(self.event, **kwargs)
        return json.loads(result)

    async def test_create_with_natural_due_time_and_list(self):
        created = await self.call(
            action="create",
            title="提交材料",
            content="发给老师",
            due_at="明早9点",
        )
        self.assertTrue(created["saved"])
        self.assertEqual("2026-07-15 09:00", created["note"]["due_text"])
        self.assertTrue(getattr(self.event, "private_companion_memo_reminder_saved", False))
        self.assertEqual(1, self.plugin.saved)

        listed = await self.call(action="list")
        self.assertFalse(listed["saved"])
        self.assertEqual(1, listed["count"])
        self.assertEqual("提交材料", listed["items"][0]["title"])
        self.assertEqual(1, listed["items"][0]["number"])

    async def test_chinese_duration_and_complete_by_title(self):
        created = await self.call(action="create", title="休息", due_at="两小时后")
        self.assertEqual(NOW + 7200, created["note"]["due_at"])
        next_week = await self.call(action="create", title="周会", due_at="下周五下午3点")
        self.assertEqual("2026-07-24 15:00", next_week["note"]["due_text"])
        chinese_clock = await self.call(action="create", title="取快递", due_at="明天下午三点半")
        self.assertEqual("2026-07-15 15:30", chinese_clock["note"]["due_text"])
        relative_date = await self.call(action="create", title="复查", due_at="三天后上午九点")
        self.assertEqual("2026-07-17 09:00", relative_date["note"]["due_text"])
        period_default = await self.call(action="create", title="下午处理", due_at="明天下午")
        self.assertEqual("2026-07-15 15:00", period_default["note"]["due_text"])
        midnight = await self.call(action="create", title="午夜处理", due_at="今晚12点")
        self.assertEqual("2026-07-15 00:00", midnight["note"]["due_text"])
        completed = await self.call(action="complete", selector="休息")
        self.assertTrue(completed["saved"])
        self.assertEqual("completed", completed["note"]["status"])

    async def test_ambiguous_selector_never_mutates(self):
        await self.call(action="create", title="提交材料", content="学校")
        await self.call(action="create", title="整理材料", content="公司")
        before = json.dumps(self.plugin.data["memo_notes"], ensure_ascii=False, sort_keys=True)
        result = await self.call(action="complete", selector="材料")
        self.assertEqual("ambiguous", result["status"])
        self.assertFalse(result["saved"])
        self.assertEqual(before, json.dumps(self.plugin.data["memo_notes"], ensure_ascii=False, sort_keys=True))

    async def test_partial_update_preserves_unspecified_fields(self):
        created = await self.call(
            action="create",
            title="旧标题",
            content="保留正文",
            due_at="明天10点",
            repeat="weekly",
        )
        delattr(self.event, "private_companion_memo_reminder_saved")
        updated = await self.call(action="update", selector="旧标题", title="新标题")
        self.assertTrue(updated["saved"])
        self.assertTrue(getattr(self.event, "private_companion_memo_reminder_saved", False))
        self.assertEqual("新标题", updated["note"]["title"])
        self.assertEqual("保留正文", updated["note"]["content"])
        self.assertEqual(created["note"]["due_at"], updated["note"]["due_at"])
        self.assertEqual("weekly", updated["note"]["repeat"])

    async def test_list_views_query_and_numbered_reopen_are_consistent(self):
        await self.call(action="create", title="学校材料", content="交给老师")
        await self.call(action="create", title="公司材料", content="交给同事")
        await self.call(action="complete", selector="学校材料")
        await self.call(action="complete", selector="公司材料")

        filtered = await self.call(action="list", status="completed", query="老师")
        self.assertEqual("completed", filtered["view"])
        self.assertEqual(1, filtered["count"])
        self.assertEqual("学校材料", filtered["items"][0]["title"])

        completed = await self.call(action="list", status="completed")
        first_title = completed["items"][0]["title"]
        reopened = await self.call(action="reopen", selector="1", status="completed")
        self.assertTrue(reopened["saved"])
        self.assertEqual(first_title, reopened["note"]["title"])
        self.assertEqual("active", reopened["note"]["status"])

    async def test_tool_result_truncates_long_content_without_changing_storage(self):
        long_content = "细节" * 300
        created = await self.call(action="create", title="长便签", content=long_content)
        self.assertTrue(created["note"]["content_truncated"])
        self.assertEqual(240, len(created["note"]["content"]))
        self.assertEqual(long_content, self.plugin.data["memo_notes"][0]["content"])
        detail = await self.call(action="get", selector="长便签")
        self.assertFalse(detail["saved"])
        self.assertFalse(detail["note"]["content_truncated"])
        self.assertEqual(long_content, detail["note"]["content"])

    async def test_delete_requires_short_lived_confirmation_token(self):
        await self.call(action="create", title="临时便签")
        first = await self.call(action="delete", selector="临时便签")
        self.assertEqual("confirmation_required", first["status"])
        self.assertFalse(first["saved"])
        self.assertEqual(1, len(self.plugin.data["memo_notes"]))

        deleted = await self.call(action="delete", confirmation_token=first["confirmation_token"])
        self.assertTrue(deleted["saved"])
        self.assertEqual("delete", deleted["action"])
        self.assertEqual([], self.plugin.data["memo_notes"])

    async def test_delete_can_be_cancelled_and_stale_confirmation_never_deletes(self):
        await self.call(action="create", title="保留便签")
        first = await self.call(action="delete", selector="保留便签")
        cancelled = await self.call(action="cancel_delete", confirmation_token=first["confirmation_token"])
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(1, len(self.plugin.data["memo_notes"]))

        second = await self.call(action="delete", selector="保留便签")
        changed = await self.call(at=NOW + 1, action="update", selector="保留便签", content="确认前改过")
        self.assertTrue(changed["saved"])
        stale = await self.call(at=NOW + 2, action="delete", confirmation_token=second["confirmation_token"])
        self.assertEqual("confirmation_stale", stale["status"])
        self.assertFalse(stale["saved"])
        self.assertEqual(1, len(self.plugin.data["memo_notes"]))

    async def test_only_owner_private_chat_can_manage_notes(self):
        for event in (FakeEvent("other"), FakeEvent("owner", private=False)):
            with self.subTest(event=event.unified_msg_origin):
                with patch("astrbot_plugin_private_companion.llm_tool_actions.time.time", return_value=NOW):
                    result = json.loads(await self.plugin._pc_manage_memo_impl(event, action="create", title="越权"))
                self.assertEqual("forbidden", result["status"])
                self.assertFalse(result["saved"])
        self.assertEqual([], self.plugin.data["memo_notes"])

    async def test_failed_persistence_rolls_back_and_reports_unsaved(self):
        self.plugin.fail_save = True
        with patch("astrbot_plugin_private_companion.llm_tool_actions.logger.error"):
            result = await self.call(action="create", title="不能落库")
        self.assertEqual("error", result["status"])
        self.assertFalse(result["saved"])
        self.assertEqual([], self.plugin.data["memo_notes"])
        self.assertFalse(getattr(self.event, "private_companion_memo_reminder_saved", False))

    async def test_disabled_or_missing_due_reminder_does_not_set_dedup_marker(self):
        await self.call(action="create", title="普通便签")
        self.assertFalse(getattr(self.event, "private_companion_memo_reminder_saved", False))

        self.event = FakeEvent()
        await self.call(
            action="create",
            title="只记录时间",
            due_at="明天9点",
            remind_enabled=False,
        )
        self.assertFalse(getattr(self.event, "private_companion_memo_reminder_saved", False))

    def test_memo_routing_excludes_generic_reminders(self):
        self.assertFalse(self.plugin._memo_management_instruction_matches("明天提醒我交材料"))
        self.assertFalse(self.plugin._memo_management_instruction_matches("半小时后叫醒我"))
        self.assertFalse(self.plugin._memo_management_instruction_matches("别忘了通知我"))
        self.assertTrue(self.plugin._memo_management_instruction_matches("帮我记一下明天交材料"))
        self.assertTrue(self.plugin._memo_management_instruction_matches("新建一张待办，明天交材料"))

    def test_explicit_memo_request_removes_future_task_only_for_that_route(self):
        memo_tools = FakeToolSet("pc_manage_memo", "future_task", "safe_tool")
        memo_req = SimpleNamespace(func_tool=memo_tools)
        self.assertTrue(
            self.plugin._remove_future_task_for_memo_request(
                memo_req,
                "帮我记一下明天交材料",
            )
        )
        self.assertEqual(["pc_manage_memo", "safe_tool"], [tool.name for tool in memo_tools.tools])

        reminder_tools = FakeToolSet("pc_manage_memo", "future_task", "safe_tool")
        reminder_req = SimpleNamespace(func_tool=reminder_tools)
        self.assertFalse(
            self.plugin._remove_future_task_for_memo_request(
                reminder_req,
                "明天提醒我交材料",
            )
        )
        self.assertIn("future_task", [tool.name for tool in reminder_tools.tools])

    def test_agent_begin_finalizer_removes_future_task_added_after_request_hook(self):
        event = FakeEvent(message_str="帮我记一下明天交材料")
        tools = FakeToolSet("pc_manage_memo", "safe_tool")
        req = SimpleNamespace(func_tool=tools)
        self.plugin._mark_memo_request_tool_boundary(event, req)

        # AstrBot 4.26.4 在 on_llm_request 之后才补入内置 future_task。
        tools.tools.append(SimpleNamespace(name="future_task"))
        event.extras["provider_request"] = req

        self.assertTrue(self.plugin._finalize_memo_request_tool_boundary(event))
        self.assertEqual(["pc_manage_memo", "safe_tool"], [tool.name for tool in tools.tools])

    def test_agent_begin_finalizer_never_removes_future_task_without_memo_marker(self):
        event = FakeEvent(message_str="明天提醒我交材料")
        tools = FakeToolSet("future_task", "safe_tool")
        event.extras["provider_request"] = SimpleNamespace(func_tool=tools)

        self.assertFalse(self.plugin._finalize_memo_request_tool_boundary(event))
        self.assertIn("future_task", [tool.name for tool in tools.tools])

    def test_prompt_and_tool_registration_have_truth_contract(self):
        instruction = self.plugin._memo_management_tool_instruction()
        self.assertIn("pc_manage_memo", instruction)
        self.assertIn("saved=true", instruction)
        self.assertIn("confirmation_token", instruction)
        self.assertIn("普通“提醒我/叫醒我/定时", instruction)
        self.assertIn("不得再调用 `future_task`", instruction)
        self.assertTrue(self.plugin._memo_management_instruction_matches("确认删除"))
        self.assertTrue(self.plugin._memo_management_instruction_matches("取消删除"))
        self.assertTrue(self.plugin._memo_management_instruction_matches("完成第2个"))
        self.assertTrue(self.plugin._memo_management_instruction_matches("只看已完成"))
        main_source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('@filter.llm_tool(name="pc_manage_memo")', main_source)
        self.assertIn("private_companion_memo_management_v1", main_source)


if __name__ == "__main__":
    unittest.main()
