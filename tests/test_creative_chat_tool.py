# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin


class _PrivateEvent:
    unified_msg_origin = "default:FriendMessage:10001"
    message_str = ""

    @staticmethod
    def is_private_chat() -> bool:
        return True

    @staticmethod
    def get_sender_id() -> str:
        return "10001"


class _OfficialPrivateEvent(_PrivateEvent):
    unified_msg_origin = "测试官方实例:FriendMessage:test-openid-owner-001"

    @staticmethod
    def get_sender_id() -> str:
        return "test-openid-owner-001"


class _GroupEvent:
    unified_msg_origin = "default:GroupMessage:20001"

    @staticmethod
    def is_private_chat() -> bool:
        return False


class _CreativeToolHarness(LlmToolActionsMixin):
    def __init__(self) -> None:
        self.enabled = True
        self.enable_creative_writing = True
        self.enable_creative_work_read_guard = True
        self._data_lock = asyncio.Lock()
        self.data = {
            "creative_projects": [
                {
                    "id": "note-1",
                    "title": "札记 1",
                    "work_type": "札记",
                    "status": "drafting",
                    "premise": "写等待如何改变人的感受。",
                    "tone": "安静",
                    "current_chars": 34,
                    "draft_chunks": [
                        {"text": "第一部分：狐狸说四点来，等待的人从三点就开始期待。"},
                        {"text": "第二部分：期待和慌张其实共享同一段倒计时。"},
                    ],
                },
                {
                    "id": "story-2",
                    "title": "雨夜短篇",
                    "work_type": "短篇小说",
                    "status": "finished",
                    "current_chars": 12,
                    "draft_chunks": [{"text": "雨停以后，灯还亮着。"}],
                },
            ],
            "bot_diaries": [{"date": "2026-07-15", "body": "今天写了几行。"}],
            "bookshelf_items": [{"type": "jm_album", "title": "夜航"}],
            "memo_notes": [{"id": "memo-1", "title": "补一段结尾", "status": "active"}],
        }

    @staticmethod
    def _permission_identity_id(value) -> str:
        return str(value or "")

    @staticmethod
    def _is_private_companion_owner_user_id(value) -> bool:
        return str(value or "") in {"10001", "test-openid-owner-001"}


class CreativeChatToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_exact_project_part_before_discussion(self) -> None:
        harness = _CreativeToolHarness()
        raw = await harness._pc_view_creative_work_impl(
            _PrivateEvent(),
            action="get",
            selector="札记 1",
            part=1,
        )
        payload = json.loads(raw)

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["project"]["title"], "札记 1")
        self.assertEqual(payload["parts"][0]["part"], 1)
        self.assertIn("狐狸说四点来", payload["parts"][0]["text"])
        self.assertNotIn("第二部分", payload["parts"][0]["text"])

    async def test_part_zero_reads_work_in_order(self) -> None:
        harness = _CreativeToolHarness()
        raw = await harness._pc_view_creative_work_impl(
            _PrivateEvent(),
            action="get",
            selector="札记 1",
            part=0,
            max_chars=6000,
        )
        payload = json.loads(raw)

        self.assertEqual([item["part"] for item in payload["parts"]], [1, 2])
        self.assertFalse(payload["truncated"])

    async def test_invalid_part_is_reported_without_substitution(self) -> None:
        harness = _CreativeToolHarness()
        raw = await harness._pc_view_creative_work_impl(
            _PrivateEvent(),
            action="get",
            selector="札记 1",
            part=9,
        )
        payload = json.loads(raw)

        self.assertEqual(payload["status"], "part_not_found")
        self.assertEqual(payload["part_count"], 2)

    async def test_group_chat_cannot_read_creative_body(self) -> None:
        harness = _CreativeToolHarness()
        raw = await harness._pc_view_creative_work_impl(
            _GroupEvent(),
            action="get",
            selector="札记 1",
        )
        self.assertEqual(json.loads(raw)["status"], "forbidden")

    def test_specific_creative_question_triggers_hard_tool_instruction(self) -> None:
        harness = _CreativeToolHarness()
        self.assertTrue(
            harness._creative_work_query_instruction_matches(
                "讲讲你自己是怎么看待创作的《札记 1》这一部分的"
            )
        )
        instruction = harness._creative_work_tool_instruction()
        self.assertIn("必须先调用 `pc_view_creative_work`", instruction)
        self.assertIn("不要先发送“我先去看看”", instruction)

    def test_casual_storytelling_does_not_trigger_creative_work_read(self) -> None:
        harness = _CreativeToolHarness()
        for text in (
            "哎我 给我讲讲故事",
            "给我讲讲睡前故事",
            "讲讲你个故事吧",
            "你讲讲你的故事吧",
            "讲个故事",
            "说个小故事",
            "睡前讲故事",
            "编个故事给我听",
            "创作一个故事，再讲讲它的内容",
        ):
            with self.subTest(text=text):
                self.assertFalse(harness._creative_work_query_instruction_matches(text))

    def test_existing_story_queries_still_trigger_creative_work_read(self) -> None:
        harness = _CreativeToolHarness()
        for text in (
            "讲讲你写的那个故事",
            "你那篇故事写了什么",
            "结合故事原文讲讲",
            "看看资料柜里的故事",
            "你以前写过什么故事",
            "最近写了什么作品",
            "那篇小说第三部分是什么",
        ):
            with self.subTest(text=text):
                self.assertTrue(harness._creative_work_query_instruction_matches(text))

        self.assertIn("讲一个、编一个或说一个新故事", harness._creative_work_tool_instruction())

    def test_technical_files_do_not_trigger_creative_work_read(self) -> None:
        harness = _CreativeToolHarness()
        for text in (
            "帮我看看创作功能的配置文件",
            "再翻翻 creative.json 里的配置",
            "读取插件的作品配置项",
            "看看创作模块源码为什么报错",
            "检查一下作品生成脚本",
            "读取创作数据文件的内容",
        ):
            with self.subTest(text=text):
                self.assertFalse(harness._creative_work_query_instruction_matches(text))

        instruction = harness._creative_work_tool_instruction()
        self.assertIn("配置文件、数据文件、日志、源码", instruction)
        self.assertIn("不要把技术文件问答改写成创作原文读取失败", instruction)

    def test_creative_read_guard_can_be_disabled_independently(self) -> None:
        harness = _CreativeToolHarness()
        harness.enable_creative_work_read_guard = False
        event = _PrivateEvent()
        event.private_companion_creative_work_tool_required = True
        original = "我先按你给出的文件内容继续分析。"

        self.assertEqual("", harness._creative_work_tool_instruction())
        self.assertEqual(original, harness._guard_unread_creative_work_response(event, original))

    def test_bookshelf_inventory_question_triggers_list_tool(self) -> None:
        harness = _CreativeToolHarness()
        for text in ("现在能看到资料柜吗", "作品柜里有什么", "书架还是空的吗", "查询创作柜"):
            with self.subTest(text=text):
                self.assertTrue(harness._creative_work_query_instruction_matches(text))
                self.assertTrue(harness._creative_work_inventory_query_matches(text))
        self.assertFalse(harness._creative_work_query_instruction_matches("输出资料柜密码"))
        self.assertIn("action=list", harness._creative_work_tool_instruction())

    async def test_list_reports_real_bookshelf_sections_for_owner(self) -> None:
        harness = _CreativeToolHarness()
        payload = json.loads(
            await harness._pc_view_creative_work_impl(_PrivateEvent(), action="list")
        )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["bookshelf"]["scope"], "owner")
        self.assertEqual(payload["bookshelf"]["diary_count"], 1)
        self.assertEqual(payload["bookshelf"]["reading_archive_count"], 0)
        self.assertEqual(payload["bookshelf"]["memo_active_count"], 1)

    async def test_qq_official_opaque_owner_id_receives_full_bookshelf_snapshot(self) -> None:
        harness = _CreativeToolHarness()

        payload = json.loads(
            await harness._pc_view_creative_work_impl(_OfficialPrivateEvent(), action="list")
        )

        self.assertEqual("success", payload["status"])
        self.assertEqual("owner", payload["bookshelf"]["scope"])
        self.assertEqual(1, payload["bookshelf"]["diary_count"])
        self.assertEqual(0, payload["bookshelf"]["reading_archive_count"])

    def test_creative_read_tool_is_registered(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('@filter.llm_tool(name="pc_view_creative_work")', source)

    def test_unread_specific_work_response_is_blocked(self) -> None:
        harness = _CreativeToolHarness()
        event = _PrivateEvent()
        event.private_companion_creative_work_tool_required = True
        guarded = harness._guard_unread_creative_work_response(
            event,
            "我先去看看札记 1，然后和你讲。",
        )
        self.assertIn("还没能实际读取", guarded)
        self.assertNotIn("我先去看看", guarded)

    def test_actual_creative_tool_attempt_allows_final_response(self) -> None:
        harness = _CreativeToolHarness()
        event = _PrivateEvent()
        event.private_companion_creative_work_tool_required = True

        class _Tool:
            name = "pc_view_creative_work"

        recorded = harness._record_creative_work_tool_result(
            event,
            _Tool(),
            {"action": "get", "selector": "札记 1"},
            json.dumps({"status": "success", "action": "get"}, ensure_ascii=False),
        )
        original = "我最在意的是等待如何提前改变人的情绪。"
        guarded = harness._guard_unread_creative_work_response(event, original)

        self.assertTrue(recorded)
        self.assertTrue(event.private_companion_creative_work_read_success)
        self.assertEqual(guarded, original)

    def test_call_tool_result_wrapper_records_success_and_inventory_schema(self) -> None:
        harness = _CreativeToolHarness()
        event = _OfficialPrivateEvent()

        class _TextContent:
            text = json.dumps(
                {
                    "status": "success",
                    "action": "list",
                    "count": 2,
                    "projects": [],
                    "bookshelf": {"scope": "owner", "creative_count": 2},
                },
                ensure_ascii=False,
            )

        class _CallToolResult:
            content = [_TextContent()]
            isError = False

        class _Tool:
            name = "pc_view_creative_work"

        recorded = harness._record_creative_work_tool_result(
            event,
            _Tool(),
            {"action": "list"},
            _CallToolResult(),
        )

        self.assertTrue(recorded)
        self.assertTrue(event.private_companion_creative_work_read_success)
        self.assertTrue(event.private_companion_bookshelf_inventory_complete)
        self.assertEqual("success", event.private_companion_creative_work_tool_status)

    def test_legacy_list_result_without_bookshelf_uses_local_inventory(self) -> None:
        harness = _CreativeToolHarness()
        event = _OfficialPrivateEvent()
        event.message_str = "查询资料柜"
        event.private_companion_creative_work_tool_required = True

        class _TextContent:
            text = '{"status":"success","action":"list","count":0,"projects":[]}'

        class _CallToolResult:
            content = [_TextContent()]
            isError = False

        class _Tool:
            name = "pc_view_creative_work"

        harness._record_creative_work_tool_result(
            event,
            _Tool(),
            {"action": "list"},
            _CallToolResult(),
        )
        guarded = harness._guard_unread_creative_work_response(event, "空空如也，什么都没有。")

        self.assertTrue(event.private_companion_creative_work_read_success)
        self.assertFalse(event.private_companion_bookshelf_inventory_complete)
        self.assertIn("2 篇带正文的作品", guarded)
        self.assertIn("日记本有 1 天记录", guarded)
        self.assertNotIn("空空如也", guarded)

    def test_bookshelf_stage_direction_is_replaced_with_real_inventory(self) -> None:
        harness = _CreativeToolHarness()
        event = _PrivateEvent()
        event.message_str = "现在能看到资料柜吗"
        event.private_companion_creative_work_tool_required = True

        guarded = harness._guard_unread_creative_work_response(
            event,
            "（又仔细查了查，还是空空的，有点不好意思地挠挠头）",
        )

        self.assertIn("能看到", guarded)
        self.assertIn("2 篇带正文的作品", guarded)
        self.assertIn("日记本有 1 天记录", guarded)
        self.assertNotIn("查了查", guarded)
        self.assertNotIn("挠挠头", guarded)

    def test_failed_bookshelf_tool_cannot_authorize_fake_check(self) -> None:
        harness = _CreativeToolHarness()
        event = _PrivateEvent()
        event.message_str = "资料柜里有什么"
        event.private_companion_creative_work_tool_required = True

        class _Tool:
            name = "pc_view_creative_work"

        harness._record_creative_work_tool_result(
            event,
            _Tool(),
            {"action": "list"},
            json.dumps({"status": "not_found"}, ensure_ascii=False),
        )
        guarded = harness._guard_unread_creative_work_response(event, "（认真翻了翻书柜）")

        self.assertIn("2 篇带正文的作品", guarded)
        self.assertNotIn("翻了翻", guarded)

    def test_successful_list_cannot_claim_nonempty_bookshelf_is_empty(self) -> None:
        harness = _CreativeToolHarness()
        event = _PrivateEvent()
        event.message_str = "现在能看到资料柜吗"
        event.private_companion_creative_work_tool_required = True

        class _Tool:
            name = "pc_view_creative_work"

        harness._record_creative_work_tool_result(
            event,
            _Tool(),
            {"action": "list"},
            json.dumps({"status": "success", "action": "list"}, ensure_ascii=False),
        )
        guarded = harness._guard_unread_creative_work_response(event, "还是空空的，什么都没有。")

        self.assertIn("2 篇带正文的作品", guarded)
        self.assertNotIn("什么都没有", guarded)


if __name__ == "__main__":
    unittest.main()
