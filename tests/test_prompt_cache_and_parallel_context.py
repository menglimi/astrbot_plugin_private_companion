# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

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


def _private_collector_harness() -> PrivateCompanionPlugin:
    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
    plugin.memory_companion_context_timeout_seconds = 1.2
    plugin.enable_llm_timer_scheduling = False
    plugin._feature_enabled_or_temp_unlocked = lambda *_args, **_kwargs: False
    plugin._memory_companion_should_defer_prompt_section = lambda *_args, **_kwargs: False
    plugin._expression_private_scope_id = lambda value: f"expression:{value}"
    plugin._expression_voice_selection = lambda **_kwargs: {}

    empty_methods = (
        "_format_hidden_creative_context_for_reply",
        "_format_recent_photo_share_snapshot_for_reply",
        "_format_bookshelf_secret_for_prompt",
        "_format_bookshelf_reading_context_for_reply",
        "_format_private_reading_preference_influence_for_reply",
        "_format_recent_news_context_for_reply",
        "_format_recent_web_exploration_context_for_reply",
        "_format_skill_growth_for_user_text",
        "_format_self_timeline_context_for_reply",
        "_format_private_chat_context_injection",
        "_format_companion_planner_injection",
        "_format_livingmemory_guidance",
        "_format_detail_injection",
    )
    for method_name in empty_methods:
        setattr(plugin, method_name, lambda *_args, **_kwargs: "")
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

        async def private_recall(**kwargs) -> str:
            calls["recall"] = dict(kwargs)
            recall_started.set()
            await release.wait()
            return "【当前私聊长期记忆补充】\n用户偏好简短回答。"

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

        by_key = {item["key"]: item for item in collected}
        self.assertEqual(calls["state"]["user_id"], "10001")
        self.assertEqual(calls["recall"]["user_id"], "10001")
        self.assertEqual(calls["state"]["timeout_seconds"], 1.6)
        self.assertEqual(by_key["memory.current_state"]["priority"], 54)
        self.assertIn("【我会牢牢记住你 当前状态参考】", by_key["memory.current_state"]["content"])
        self.assertIn("现在在书房，穿浅蓝色居家服。", by_key["memory.current_state"]["content"])
        self.assertIn("优先服从本轮状态注入和当前会话中明确发生的时间线", by_key["memory.current_state"]["content"])
        self.assertEqual(by_key["memory.private_recall"]["status"], "hit")

    async def test_unrelated_turn_skips_current_state_memory_lookup(self) -> None:
        plugin = _private_collector_harness()
        state_calls = 0

        async def current_state(**_kwargs) -> str:
            nonlocal state_calls
            state_calls += 1
            return "不应读取"

        async def private_recall(**_kwargs) -> str:
            return ""

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
        self.assertNotIn("memory.current_state", {item["key"] for item in collected})

    async def test_stable_media_rule_keeps_system_prefix_identical_across_turns(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_photo_text_action = True
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
                turn_text,
                priority=40,
                source="passive_state",
            )
            requests.append(req)

        first, second = requests
        self.assertEqual(first.system_prompt, second.system_prompt)
        self.assertIn("private_companion_media_delivery_truth_v1", first.system_prompt)
        self.assertNotIn("private_companion_state_v1", first.system_prompt)
        self.assertIn("第一轮动态状态", plugin._request_prompt_context_surface(first))
        self.assertIn("第二轮动态状态", plugin._request_prompt_context_surface(second))
        self.assertNotEqual(
            plugin._request_prompt_context_surface(first),
            plugin._request_prompt_context_surface(second),
        )


if __name__ == "__main__":
    unittest.main()
