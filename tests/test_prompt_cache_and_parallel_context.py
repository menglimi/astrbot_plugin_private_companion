# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.conversation_prompt_section import prompt_section
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _PrivateEvent:
    unified_msg_origin = "default:FriendMessage:10001"
    message_str = "你现在在干嘛，穿什么衣服？"

    @staticmethod
    def get_sender_id() -> str:
        return "10001"

    @staticmethod
    def is_private_chat() -> bool:
        return True


async def _no_record(*_args, **_kwargs) -> None:
    return None


def _private_recall_section(content: str):
    return prompt_section(
        key="memory.private_recall",
        title="当前私聊长期记忆补充",
        source="memory_companion",
        content=content,
    )


def _private_collector_harness() -> PrivateCompanionPlugin:
    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
    plugin.memory_companion_context_timeout_seconds = 1.2
    plugin.enable_llm_timer_scheduling = False
    plugin._feature_enabled_or_temp_unlocked = lambda *_args, **_kwargs: False
    plugin._memory_companion_should_defer_prompt_section = lambda *_args, **_kwargs: False
    plugin._expression_private_scope_id = lambda value: f"expression:{value}"
    plugin._expression_voice_selection = lambda **_kwargs: {}

    empty_methods = (
        "_format_bookshelf_secret_for_prompt",
        "_format_bookshelf_reading_context_for_reply",
        "_format_private_reading_preference_influence_for_reply",
        "_format_recent_news_context_for_reply",
        "_format_recent_web_exploration_context_for_reply",
        "_format_mobile_user_location_context",
        "_format_self_timeline_context_for_reply",
        "_format_private_chat_context_injection",
        "_format_companion_planner_injection",
        "_format_livingmemory_guidance",
    )
    for method_name in empty_methods:
        setattr(plugin, method_name, lambda *_args, **_kwargs: "")
    section_methods = (
        ("_format_hidden_creative_context_for_reply_prompt_section", "creative.hidden_context", "私下创作近况"),
        ("_format_recent_photo_share_snapshot_for_reply_prompt_section", "photo.recent_share", "最近一次真实图片分享"),
        ("_format_skill_growth_for_user_text_prompt_section", "skill.growth_match", "本轮相关技能"),
        ("_format_detail_injection_prompt_section", "detail.injection", "Bot 模拟当前片段"),
    )
    for method_name, key, title in section_methods:
        setattr(
            plugin,
            method_name,
            lambda *_args, _key=key, _title=title, **_kwargs: prompt_section(
                key=_key,
                title=_title,
                source="test",
                content="",
            ),
        )
    return plugin


class PromptCacheAndParallelContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_state_and_private_recall_start_in_parallel(self) -> None:
        plugin = _private_collector_harness()
        state_started = asyncio.Event()
        recall_started = asyncio.Event()
        release = asyncio.Event()
        calls: dict[str, dict] = {}

        async def current_state(**kwargs) -> str:
            calls["state"] = dict(kwargs)
            state_started.set()
            await release.wait()
            return "现在在书房，穿浅蓝色居家服。"

        async def private_recall(**kwargs):
            calls["recall"] = dict(kwargs)
            recall_started.set()
            await release.wait()
            return _private_recall_section("用户偏好简短回答。")

        plugin._memory_companion_compose_feature_context = current_state
        plugin._memory_companion_compose_private_recall = private_recall
        task = asyncio.create_task(
            plugin._collect_private_passive_prompt_contexts(
                _PrivateEvent(),
                SimpleNamespace(system_prompt="", prompt=""),
                inbound_text="你现在在干嘛，穿什么衣服？",
                current_user={"user_id": "10001"},
                is_private_chat=True,
            )
        )

        try:
            await asyncio.wait_for(
                asyncio.gather(state_started.wait(), recall_started.wait()),
                timeout=0.5,
            )
        except Exception:
            release.set()
            await task
            raise
        release.set()
        collected = await task

        by_key = {item.key: item for item in collected}
        self.assertEqual(calls["state"]["user_id"], "10001")
        self.assertEqual(calls["recall"]["user_id"], "10001")
        self.assertEqual(calls["state"]["timeout_seconds"], 1.6)
        current_state = by_key["memory.current_state"]
        self.assertEqual(current_state.priority, 54)
        self.assertEqual("我会牢牢记住你 当前状态参考", current_state.sections[0].title)
        self.assertNotIn("【我会牢牢记住你 当前状态参考】", current_state.sections[0].content)
        self.assertIn("现在在书房，穿浅蓝色居家服。", current_state.sections[0].content)
        self.assertIn("优先服从本轮状态注入和当前会话中明确发生的时间线", current_state.sections[0].content)
        self.assertEqual(by_key["memory.private_recall"].status, "hit")

    async def test_unrelated_turn_skips_current_state_memory_lookup(self) -> None:
        plugin = _private_collector_harness()
        state_calls = 0

        async def current_state(**_kwargs) -> str:
            nonlocal state_calls
            state_calls += 1
            return "不应读取"

        async def private_recall(**_kwargs):
            return _private_recall_section("")

        plugin._memory_companion_compose_feature_context = current_state
        plugin._memory_companion_compose_private_recall = private_recall
        collected = await plugin._collect_private_passive_prompt_contexts(
            _PrivateEvent(),
            SimpleNamespace(system_prompt="", prompt=""),
            inbound_text="今天工作有点忙",
            current_user={"user_id": "10001"},
            is_private_chat=True,
        )

        self.assertEqual(state_calls, 0)
        self.assertNotIn("memory.current_state", {item.key for item in collected})

    async def test_third_person_activity_question_skips_current_state_memory_lookup(self) -> None:
        plugin = _private_collector_harness()
        state_calls = 0

        async def current_state(**_kwargs) -> str:
            nonlocal state_calls
            state_calls += 1
            return "不应读取"

        async def private_recall(**_kwargs):
            return _private_recall_section("")

        plugin._memory_companion_compose_feature_context = current_state
        plugin._memory_companion_compose_private_recall = private_recall

        for text in ("你觉得春希现在在干什么？", "你猜他在做什么？"):
            with self.subTest(text=text):
                collected = await plugin._collect_private_passive_prompt_contexts(
                    _PrivateEvent(),
                    SimpleNamespace(system_prompt="", prompt=text),
                    inbound_text=text,
                    current_user={"user_id": "10001"},
                    is_private_chat=True,
                )
                self.assertNotIn("memory.current_state", {item.key for item in collected})

        self.assertEqual(state_calls, 0)

    async def test_private_turn_includes_authorized_mobile_location_context(self) -> None:
        plugin = _private_collector_harness()
        plugin._format_mobile_user_location_context_prompt_section = lambda _user: prompt_section(
            key="reality_touch.mobile_location",
            title="用户手机位置感知",
            source="reality_touch",
            content="用户当前位于已标记地点“公司”（工作地点）范围内",
        )

        async def private_recall(**_kwargs):
            return _private_recall_section("")

        plugin._memory_companion_compose_private_recall = private_recall
        collected = await plugin._collect_private_passive_prompt_contexts(
            _PrivateEvent(),
            SimpleNamespace(system_prompt="", prompt=""),
            inbound_text="今天工作有点忙",
            current_user={"user_id": "10001"},
            is_private_chat=True,
        )

        by_key = {item.key: item for item in collected}
        location = by_key["reality_touch.mobile_location"]
        self.assertEqual(location.priority, 55)
        self.assertIn("已标记地点“公司”", location.sections[0].content)

    async def test_colloquial_current_activity_question_triggers_state_context(self) -> None:
        plugin = _private_collector_harness()
        calls: list[str] = []

        async def current_state(**kwargs) -> str:
            calls.append(kwargs["query"])
            return "当前正在专心处理手头的事。"

        async def private_recall(**_kwargs):
            return _private_recall_section("")

        plugin._memory_companion_compose_feature_context = current_state
        plugin._memory_companion_compose_private_recall = private_recall

        for text in (
            "那你现在在干啥呢",
            "好像你在忙的样子，忙啥呢",
            "你现在在干什么？",
        ):
            with self.subTest(text=text):
                collected = await plugin._collect_private_passive_prompt_contexts(
                    _PrivateEvent(),
                    SimpleNamespace(system_prompt="", prompt=text),
                    inbound_text=text,
                    current_user={"user_id": "10001"},
                    is_private_chat=True,
                )
                by_key = {item.key: item for item in collected}
                self.assertIn("memory.current_state", by_key)
                self.assertIn(text, calls[-1])

    async def test_stable_media_rule_keeps_system_prefix_identical_across_turns(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_photo_text_action = True
        plugin._image_companion_required = lambda: True
        plugin._image_companion_available = lambda: True
        plugin.passive_injection_position = "prompt"
        plugin._record_request_prompt_fragment = _no_record
        event = _PrivateEvent()

        requests = []
        for turn_text in ("第一轮动态状态", "第二轮动态状态"):
            req = SimpleNamespace(
                system_prompt="稳定人格提示",
                prompt="用户消息",
                extra_user_content_parts=[],
            )
            await plugin._append_media_delivery_truth_to_request(event, req)
            plugin._append_turn_prompt_fragment_by_position(
                req,
                "<!-- private_companion_state_v1 -->",
                prompt_section(
                    key="state.test",
                    title="状态",
                    source="passive_state",
                    content=turn_text,
                ),
                priority=40,
            )
            requests.append(req)

        first, second = requests
        self.assertEqual(first.system_prompt, second.system_prompt)
        self.assertNotIn("private_companion_media_delivery_truth_v1", first.system_prompt)
        self.assertIn('<section title="内部历史标记">', first.system_prompt)
        self.assertIn('<section title="明确生图请求">', first.system_prompt)
        self.assertIn('<section title="媒体真实性硬规则">', first.system_prompt)
        self.assertNotIn("【内部历史标记】", first.system_prompt)
        self.assertIn("&lt;pc_history_media ... /&gt;", first.system_prompt)
        self.assertNotIn("private_companion_state_v1", first.system_prompt)
        self.assertIn("第一轮动态状态", plugin._request_prompt_context_surface(first))
        self.assertIn("第二轮动态状态", plugin._request_prompt_context_surface(second))
        self.assertNotEqual(
            plugin._request_prompt_context_surface(first),
            plugin._request_prompt_context_surface(second),
        )


if __name__ == "__main__":
    unittest.main()
