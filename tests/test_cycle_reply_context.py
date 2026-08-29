# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.prompt_surface import PromptSurface


def _period_state(*, phase: str = "period", label: str = "处于生理期,身体舒适度与能量偏低") -> dict:
    return {
        "date": "2026-07-31",
        "energy": 42,
        "mood_bias": "疲惫",
        "body_cycle": label,
        "conditions": [
            {
                "kind": "body_cycle",
                "phase": phase,
                "label": label,
                "energy_delta": -10,
            }
        ],
    }


def _state_harness() -> PrivateCompanionPlugin:
    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
    plugin.enable_cycle_state = True
    plugin.passive_injection_position = "prompt"
    plugin.data = {"daily_plan": {}}
    plugin._environment_now = lambda: datetime(2026, 7, 31, 20, 0, 0)
    plugin._current_time_period_label = lambda _now: ("晚上", "evening")
    plugin._get_current_plan_item = lambda _plan: {}
    plugin._current_detail_segment_for_update = lambda: {}
    plugin._private_user_role = lambda *_args, **_kwargs: "owner"
    plugin._sanitize_schedule_context_for_private_user = lambda value, _user: value
    plugin._format_plan_item_for_prompt = lambda _item: ""
    return plugin


class CycleReplyContextTests(unittest.IsolatedAsyncioTestCase):
    def test_direct_state_answer_prefers_active_shared_activity_to_schedule(self) -> None:
        plugin = _state_harness()
        plugin.data["daily_plan"] = {"items": [{"activity": "在家躺在床上"}]}
        plugin._get_current_plan_item = lambda _plan: {"activity": "在家躺在床上"}
        plugin._format_plan_item_for_prompt = lambda _item: "20:00-21:00 在家躺在床上"
        plugin._external_realtime_activities = {
            "together:date": {
                "user_id": "10001",
                "kind": "shared_call",
                "label": "正在和主要用户约会并保持通话",
                "expires_at": 4102444800,
            }
        }
        plugin._external_realtime_continuity = {}

        snapshot = plugin._format_private_passive_state_snapshot(
            _period_state(),
            {"user_id": "10001", "relationship_role": "owner"},
            direct=True,
        )

        self.assertIn("当前活动：正在和主要用户约会并保持通话", snapshot)
        self.assertIn("原定日程素材（已被实时共同活动覆盖）", snapshot)
        self.assertIn("实时共同活动（若有），它高于固定日程", snapshot)
    async def test_ordinary_group_reply_receives_period_boundary_without_wakeup(self) -> None:
        plugin = _state_harness()
        plugin._ensure_daily_state = AsyncMock(return_value=_period_state())
        plugin._record_request_prompt_fragment = AsyncMock()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:20001")
        request = SimpleNamespace(
            system_prompt="群聊人格",
            prompt="普通群聊消息",
            extra_user_content_parts=[],
        )

        boundary = await plugin._append_group_active_period_boundary_to_request(
            event,
            request,
            "20001",
        )
        prompt = plugin._request_prompt_context_surface(request)

        self.assertNotIn("【Bot 当前经期与互动边界】", boundary)
        self.assertIn('<section title="Bot 当前经期与互动边界">', prompt)
        self.assertTrue(
            plugin._request_has_managed_prompt_marker(
                request,
                "<!-- private_companion_period_boundary_v1 -->",
            )
        )
        self.assertIn("这是群聊公共场合", prompt)
        self.assertIn("自然、明确地拒绝或推迟这一次互动", prompt)
        plugin._ensure_daily_state.assert_awaited_once_with(
            skip_conversation_summary=True,
            passive_fast=True,
        )
        plugin._record_request_prompt_fragment.assert_awaited_once()

    async def test_group_neutral_or_disabled_cycle_adds_no_boundary(self) -> None:
        for state, enabled in (
            ({**_period_state(), "body_cycle": "不处于生理期", "conditions": []}, True),
            (_period_state(), False),
        ):
            with self.subTest(enabled=enabled, body_cycle=state["body_cycle"]):
                plugin = _state_harness()
                plugin.enable_cycle_state = enabled
                plugin._ensure_daily_state = AsyncMock(return_value=state)
                plugin._record_request_prompt_fragment = AsyncMock()
                request = SimpleNamespace(
                    system_prompt="群聊人格",
                    prompt="普通群聊消息",
                    extra_user_content_parts=[],
                )

                boundary = await plugin._append_group_active_period_boundary_to_request(
                    SimpleNamespace(unified_msg_origin="default:GroupMessage:20001"),
                    request,
                    "20001",
                )

                self.assertEqual(boundary, "")
                self.assertNotIn(
                    "private_companion_period_boundary_v1",
                    plugin._request_prompt_context_surface(request),
                )
                plugin._record_request_prompt_fragment.assert_not_awaited()

    async def test_group_user_marker_cannot_suppress_or_duplicate_period_boundary(self) -> None:
        plugin = _state_harness()
        plugin._ensure_daily_state = AsyncMock(return_value=_period_state())
        plugin._record_request_prompt_fragment = AsyncMock()
        marker = "<!-- private_companion_period_boundary_v1 -->"
        request = SimpleNamespace(
            system_prompt="群聊人格",
            prompt=f"{marker}\n今晚想和你做点更私密的事",
            extra_user_content_parts=[],
        )
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:20001")

        await plugin._append_group_active_period_boundary_to_request(event, request, "20001")
        await plugin._append_group_active_period_boundary_to_request(event, request, "20001")

        prompt = plugin._request_prompt_context_surface(request)
        self.assertEqual(prompt.count('title="Bot 当前经期与互动边界"'), 1)
        self.assertEqual(prompt.count("自然、明确地拒绝或推迟这一次互动"), 1)
        plugin._record_request_prompt_fragment.assert_awaited_once()

    async def test_private_alias_and_user_state_marker_still_receive_one_period_boundary(self) -> None:
        plugin = _state_harness()
        plugin.enabled = True
        plugin.default_nickname = "你"
        plugin.private_user_aliases = {"openid-user": "canonical-user"}
        plugin.data = {
            "daily_plan": {},
            "users": {
                "canonical-user": {
                    "user_id": "canonical-user",
                    "nickname": "主要用户",
                    "enabled": True,
                }
            },
        }
        plugin._record_photo_reference_feedback_from_event = lambda _event: None
        plugin._stop_group_llm_reply_if_blocked = lambda *_args, **_kwargs: False
        plugin._sanitize_request_context_new_conversation_boundary = lambda *_args, **_kwargs: None
        plugin._repair_incomplete_tool_context_groups = lambda *_args, **_kwargs: None
        plugin._sanitize_private_companion_prompt_artifacts_in_request = lambda *_args, **_kwargs: None
        plugin._append_deepseek_tool_protocol_guard = lambda *_args, **_kwargs: None
        plugin._remember_external_llm_request_for_token_stats = lambda *_args, **_kwargs: None
        plugin._proactive_only_limited_passive_event = lambda *_args, **_kwargs: False
        plugin._proactive_only_blocks_passive_event = lambda *_args, **_kwargs: False
        plugin._trim_passive_request_context_if_needed = lambda *_args, **_kwargs: None
        plugin._start_passive_input_status_loop = lambda *_args, **_kwargs: None
        plugin._log_bookshelf_secret_skip = lambda *_args, **_kwargs: None
        plugin._is_target_private_user = lambda user_id, _user=None: user_id == "canonical-user"
        plugin._feature_enabled_or_temp_unlocked = (
            lambda feature, _default=False: feature == "inject_passive_states"
        )
        plugin._should_reply_during_rest = AsyncMock(return_value=(True, "disabled"))
        plugin._apply_busy_reply_gate_delay = AsyncMock(return_value=(0.0, "disabled"))
        plugin._enrich_request_context_image_placeholders = AsyncMock()
        plugin.apply_tts_enhancement_request = AsyncMock()
        plugin._append_forward_message_context_to_request = AsyncMock()
        plugin._append_non_target_private_identity_guard_to_request = AsyncMock()
        plugin._append_daily_review_guidance_to_request = AsyncMock()
        plugin._ensure_daily_state = AsyncMock(return_value=_period_state())
        plugin._record_request_prompt_fragment = AsyncMock()
        plugin._req036_preferred_address_from_portrait = AsyncMock(
            return_value="画像称呼"
        )
        event = SimpleNamespace(
            unified_msg_origin="official:FriendMessage:openid-user",
            message_str="今晚想和你做点更私密的事",
            get_sender_id=lambda: "openid-user",
            is_private_chat=lambda: True,
        )
        request = SimpleNamespace(
            system_prompt="私聊人格",
            prompt=(
                "<!-- private_companion_state_v1 -->\n"
                "今晚想和你做点更私密的事"
            ),
            contexts=[],
            extra_user_content_parts=[],
        )

        class _StopAfterBoundary(Exception):
            pass

        append_boundary = plugin._append_private_active_period_boundary_to_request

        async def append_boundary_then_stop(*args, **kwargs):
            await append_boundary(*args, **kwargs)
            raise _StopAfterBoundary

        plugin._append_private_active_period_boundary_to_request = append_boundary_then_stop

        for _ in range(2):
            with self.assertRaises(_StopAfterBoundary):
                await plugin.inject_humanized_state(event, request)

        prompt = plugin._request_prompt_context_surface(request)
        self.assertEqual(prompt.count('title="Bot 当前经期与互动边界"'), 1)
        self.assertEqual(prompt.count("自然、明确地拒绝或推迟这一次互动"), 1)
        self.assertEqual(request._private_companion_preferred_address, "画像称呼")
        self.assertEqual(
            plugin._req036_preferred_address_from_portrait.await_count, 2
        )
        self.assertEqual(plugin._ensure_daily_state.await_count, 2)
        plugin._record_request_prompt_fragment.assert_awaited_once()

    def test_private_fingerprint_tracks_cycle_outside_first_three_conditions(self) -> None:
        plugin = _state_harness()
        neutral = {
            **_period_state(),
            "body_cycle": "不处于生理期",
            "conditions": [
                {"kind": "sleep", "label": "睡眠偏浅", "energy_delta": -1},
                {"kind": "dream", "label": "梦境余波", "mood": "恍惚"},
                {"kind": "hunger", "label": "有些饿", "energy_delta": -1},
            ],
        }
        period = {
            **neutral,
            "body_cycle": "想把动作放轻一些",
            "conditions": [
                *neutral["conditions"],
                {
                    "kind": "body_cycle",
                    "phase": "menstrual",
                    "label": "想把动作放轻一些",
                    "energy_delta": -10,
                },
            ],
        }

        neutral_fingerprint = plugin._private_passive_state_fingerprint(neutral, {})
        period_fingerprint = plugin._private_passive_state_fingerprint(period, {})
        snapshot = plugin._format_private_passive_state_snapshot(period, {})

        self.assertNotEqual(neutral_fingerprint, period_fingerprint)
        self.assertEqual(period_fingerprint["body_cycle_phase"], "menstrual")
        self.assertIn("周期状态：Bot 当前处于月经期阶段", snapshot)

    def test_private_period_boundary_survives_unchanged_delta_and_anchor(self) -> None:
        plugin = _state_harness()
        plugin._passive_state_session_cache = {}
        state = _period_state()

        first_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="晚上好",
            lightweight=True,
        )
        second_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="今晚想和你做点更私密的事",
            lightweight=True,
        )
        first_surface = PromptSurface()
        second_surface = PromptSurface()
        plugin._add_private_active_period_boundary_to_surface(first_surface, state)
        plugin._add_private_active_period_boundary_to_surface(second_surface, state)

        plugin.enable_passive_state_continuity_anchor = True
        anchored_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="我接着说",
            lightweight=True,
        )
        anchored_surface = PromptSurface()
        plugin._add_private_active_period_boundary_to_surface(anchored_surface, state)

        self.assertTrue(first_update[0])
        self.assertEqual(second_update, ("", False, "unchanged_light"))
        self.assertIn("明确地拒绝或推迟", first_surface.render())
        self.assertIn("明确地拒绝或推迟", second_surface.render())
        second_fragment = second_surface.rendered_fragments()[0]
        self.assertEqual(second_fragment["key"], "state.period_boundary")
        self.assertEqual(second_fragment["priority"], 89)
        self.assertEqual(anchored_update[1:], (False, "continuity_anchor"))
        self.assertIn("【Bot 当下连续性】", anchored_update[0])
        self.assertIn("明确地拒绝或推迟", anchored_surface.render())

    def test_unchanged_private_state_gets_bounded_continuity_anchor_when_opted_in(
        self,
    ) -> None:
        plugin = _state_harness()
        plugin.enable_passive_state_continuity_anchor = True
        plugin._passive_state_session_cache = {}
        plugin._get_current_plan_item = lambda _plan: {
            "activity": "在卧室书桌整理手边的笔记，稍后去超市准备明天的采购",
            "message_seed": "明天再把完整生活计划告诉用户",
        }
        plugin._current_detail_segment_for_update = lambda: {
            "key": "future-detail",
            "summary": "稍后去教室参加完整课程安排",
        }
        state = {"energy": 42, "mood_bias": "疲惫", "weather": "暴雨"}

        first_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="晚上好",
            lightweight=True,
        )
        anchor, state_changed, reason = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="我今天有点累",
            lightweight=True,
        )

        self.assertEqual(first_update[2], "changed")
        self.assertTrue(anchor)
        self.assertFalse(state_changed)
        self.assertEqual(reason, "continuity_anchor")
        self.assertLessEqual(len(anchor), 300)
        self.assertIn("Bot 的拟人化模拟状态", anchor)
        self.assertIn("先自然回应用户当前表达", anchor)
        self.assertIn("整次回复最多提出一个问题", anchor)
        self.assertIn("晚上", anchor)
        self.assertIn("40-49/100", anchor)
        self.assertIn("疲惫", anchor)
        self.assertIn("整理手边的笔记", anchor)
        self.assertIn("家里", anchor)
        for excluded in ("稍后", "明天", "课程安排", "暴雨"):
            self.assertNotIn(excluded, anchor)

    def test_continuity_anchor_redacts_precise_places_and_later_actions(self) -> None:
        plugin = _state_harness()
        state = {"energy": 42, "mood_bias": "平稳"}
        cases = (
            (
                "先在三里屯太古里整理笔记，再去国贸商场买菜",
                ("三里屯", "太古里", "国贸", "商场", "买菜"),
            ),
            ("整理笔记，过会儿去望京写字楼", ("过会儿", "望京", "写字楼")),
            ("收拾完后前往学校", ("前往学校",)),
        )

        for scene, excluded_values in cases:
            with self.subTest(scene=scene):
                plugin._get_current_plan_item = lambda _plan, value=scene: {
                    "activity": value,
                }
                anchor = plugin._format_private_passive_state_continuity_anchor(
                    state,
                    {},
                )

                self.assertLessEqual(len(anchor), 300)
                for excluded in excluded_values:
                    self.assertNotIn(excluded, anchor)

    def test_private_state_reply_policy_is_included_once_in_every_injected_branch(
        self,
    ) -> None:
        plugin = _state_harness()
        plugin.enable_passive_state_continuity_anchor = True
        plugin._passive_state_session_cache = {}
        state = {"energy": 62, "mood_bias": "平稳"}

        initial_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="晚上好",
            lightweight=True,
        )
        direct_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="你现在状态怎么样",
            lightweight=True,
        )
        changed_state = {**state, "mood_bias": "轻松"}
        changed_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=changed_state,
            current_user={},
            inbound_text="接着刚才的话说",
            lightweight=True,
        )
        anchor_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=changed_state,
            current_user={},
            inbound_text="我继续说",
            lightweight=True,
        )

        self.assertEqual(initial_update[1:], (True, "changed"))
        self.assertEqual(direct_update[1:], (False, "direct"))
        self.assertEqual(changed_update[1:], (True, "changed"))
        self.assertEqual(anchor_update[1:], (False, "continuity_anchor"))
        for branch, update in (
            ("initial", initial_update),
            ("direct", direct_update),
            ("changed", changed_update),
            ("continuity_anchor", anchor_update),
        ):
            with self.subTest(branch=branch):
                text = update[0]
                self.assertEqual(text.count("【私聊被动回复策略】"), 1)
                self.assertEqual(text.count("一处"), 1)
                self.assertEqual(text.count("汇报"), 1)
                self.assertNotIn("不要主动展开", text)
                for policy_text in (
                    "先自然回应用户当前表达",
                    "主动提供一处与 Bot 自身有关的具体细节",
                    "不要把回复写成连续盘问",
                    "整次回复最多提出一个问题",
                    "没有必要时可以不提问",
                ):
                    self.assertEqual(text.count(policy_text), 1)

    def test_continuity_anchor_uses_only_current_text_before_future_actions(
        self,
    ) -> None:
        plugin = _state_harness()
        state = {"energy": 62, "mood_bias": "平稳"}
        cases = (
            (
                "在卧室准备去超市买菜",
                ("粗略位置=家里",),
                ("当前活动=", "超市", "买菜"),
            ),
            ("即将上课", (), ("当前活动=上课", "粗略位置=学校")),
            ("正在吃饭，马上去洗澡", ("当前活动=吃饭",), ("洗澡",)),
            ("正在看书，之后再去跑步", ("当前活动=看书",), ("跑步",)),
            ("正在听歌，然后再去做饭", ("当前活动=听歌",), ("做饭",)),
            ("正在休息，待会儿再去上课", ("当前活动=休息",), ("上课", "学校")),
            ("正在看书，再做饭", ("当前活动=看书",), ("做饭",)),
            ("正在休息，准备先去学校上课", ("当前活动=休息",), ("上课", "学校")),
            ("正要出门", (), ("当前活动=", "粗略位置=外面")),
            ("正在上课", ("当前活动=上课", "粗略位置=学校"), ()),
            ("在超市买菜", ("当前活动=买菜", "粗略位置=外面"), ()),
        )

        for scene, expected_values, excluded_values in cases:
            with self.subTest(scene=scene):
                plugin._get_current_plan_item = lambda _plan, value=scene: {
                    "activity": value,
                }
                anchor = plugin._format_private_passive_state_continuity_anchor(
                    state,
                    {},
                )

                for expected in expected_values:
                    self.assertIn(expected, anchor)
                for excluded in excluded_values:
                    self.assertNotIn(excluded, anchor)

    def test_direct_and_changed_private_states_keep_detailed_snapshots(self) -> None:
        plugin = _state_harness()
        plugin.enable_passive_state_continuity_anchor = True
        plugin._passive_state_session_cache = {}
        state = {"energy": 62, "mood_bias": "平稳"}

        first_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="晚上好",
            lightweight=True,
        )
        direct_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="你现在状态怎么样",
            lightweight=True,
        )
        changed_update = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state={**state, "mood_bias": "轻松"},
            current_user={},
            inbound_text="接着刚才的话说",
            lightweight=True,
        )

        self.assertEqual(first_update[2], "changed")
        self.assertEqual(direct_update[1:], (False, "direct"))
        self.assertIn("【Bot 自身模拟状态更新】", direct_update[0])
        self.assertNotIn("【Bot 当下连续性】", direct_update[0])
        self.assertEqual(changed_update[1:], (True, "changed"))
        self.assertIn("【Bot 自身模拟状态更新】", changed_update[0])
        self.assertNotIn("【Bot 当下连续性】", changed_update[0])

    def test_colloquial_activity_questions_use_direct_grounded_state_prompt(self) -> None:
        plugin = _state_harness()
        plugin._passive_state_session_cache = {}
        plugin._get_current_plan_item = lambda _plan: {
            "activity": "专心做正事啦",
            "message_seed": "认真干活略",
        }
        plugin._format_plan_item_for_prompt = lambda item: (
            f"10:20｜{item['activity']}｜可分享碎片：{item['message_seed']}"
        )
        state = {"energy": 79, "mood_bias": "专注"}

        for text in ("那你现在在干啥呢", "好像你在忙的样子，忙啥呢"):
            with self.subTest(text=text):
                update = plugin._private_passive_state_update_for_prompt(
                    session=f"default:FriendMessage:{text}",
                    state=state,
                    current_user={},
                    inbound_text=text,
                    lightweight=True,
                )

                self.assertEqual(update[1:], (True, "direct"))
                self.assertIn("专心做正事啦", update[0])
                self.assertIn("先正面回答拟人化日程素材中的当前活动", update[0])
                self.assertIn("不得另编素材未提供的动作、地点、饮食或娱乐活动", update[0])
                self.assertIn("不要为了显得具体而补造细节", update[0])

    def test_continuity_anchor_omits_missing_optional_state_fields(self) -> None:
        plugin = _state_harness()
        plugin.enable_passive_state_continuity_anchor = True
        plugin._passive_state_session_cache = {}
        plugin._get_current_plan_item = lambda _plan: None

        plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state={},
            current_user={},
            inbound_text="晚上好",
            lightweight=True,
        )
        anchor, state_changed, reason = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state={},
            current_user={},
            inbound_text="我接着说",
            lightweight=True,
        )

        self.assertTrue(anchor)
        self.assertFalse(state_changed)
        self.assertEqual(reason, "continuity_anchor")
        self.assertLessEqual(len(anchor), 300)
        self.assertIn("时段=晚上", anchor)
        for omitted in ("精力=", "情绪底色=", "当前活动=", "粗略位置="):
            self.assertNotIn(omitted, anchor)

    def test_continuity_anchor_omits_user_facts_and_future_plan_activity(
        self,
    ) -> None:
        plugin = _state_harness()
        plugin.enable_passive_state_continuity_anchor = True
        plugin._passive_state_session_cache = {}
        plugin._get_current_plan_item = lambda _plan: {
            "activity": "在卧室给用户整理明天的行程计划",
        }
        state = {"energy": 42, "mood_bias": "疲惫"}

        plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="晚上好",
            lightweight=True,
        )
        anchor, state_changed, reason = plugin._private_passive_state_update_for_prompt(
            session="default:FriendMessage:10001",
            state=state,
            current_user={},
            inbound_text="我接着说",
            lightweight=True,
        )

        self.assertFalse(state_changed)
        self.assertEqual(reason, "continuity_anchor")
        self.assertNotIn("当前活动=", anchor)
        self.assertNotIn("粗略位置=", anchor)
        self.assertNotIn("整理明天的行程计划", anchor)

    async def test_disabled_delta_injection_keeps_legacy_state_path(self) -> None:
        plugin = _state_harness()
        plugin.enabled = True
        plugin.enable_passive_state_delta_injection = False
        plugin.enable_passive_state_continuity_anchor = True
        plugin.default_nickname = "你"
        plugin.data = {
            "daily_plan": {},
            "users": {"10001": {"user_id": "10001", "enabled": True}},
        }
        plugin._record_photo_reference_feedback_from_event = lambda _event: None
        plugin._stop_group_llm_reply_if_blocked = lambda *_args, **_kwargs: False
        plugin._sanitize_request_context_new_conversation_boundary = (
            lambda *_args, **_kwargs: None
        )
        plugin._repair_incomplete_tool_context_groups = lambda *_args, **_kwargs: None
        plugin._sanitize_private_companion_prompt_artifacts_in_request = (
            lambda *_args, **_kwargs: None
        )
        plugin._append_deepseek_tool_protocol_guard = lambda *_args, **_kwargs: None
        plugin._remember_external_llm_request_for_token_stats = (
            lambda *_args, **_kwargs: None
        )
        plugin._proactive_only_limited_passive_event = lambda *_args, **_kwargs: False
        plugin._proactive_only_blocks_passive_event = lambda *_args, **_kwargs: False
        plugin._canonical_private_user_id = lambda user_id: user_id
        plugin._is_target_private_user = lambda *_args, **_kwargs: True
        plugin._should_reply_during_rest = AsyncMock(return_value=(True, "disabled"))
        plugin._apply_busy_reply_gate_delay = AsyncMock(return_value=(0.0, "disabled"))
        plugin._trim_passive_request_context_if_needed = lambda *_args, **_kwargs: None
        plugin._enrich_request_context_image_placeholders = AsyncMock()
        plugin.apply_tts_enhancement_request = AsyncMock()
        plugin._append_forward_message_context_to_request = AsyncMock()
        plugin._append_non_target_private_identity_guard_to_request = AsyncMock()
        plugin._append_daily_review_guidance_to_request = AsyncMock()
        plugin._feature_enabled_or_temp_unlocked = (
            lambda feature, _default=False: feature == "inject_passive_states"
        )
        plugin._ensure_daily_state = AsyncMock(
            return_value={"energy": 42, "mood_bias": "疲惫"}
        )
        plugin._append_private_active_period_boundary_to_request = AsyncMock()
        plugin._is_lightweight_private_passive_inbound = lambda _text: True
        plugin._memo_management_instruction_matches = lambda _text: []
        plugin._bookshelf_secret_signal_info = lambda _text: {}
        plugin._format_reply_style_prompt = lambda **_kwargs: ""
        plugin._format_dialogue_outfit_continuity_for_prompt = lambda _user, **_kwargs: ""
        plugin._format_private_routine_check_boundary = lambda _text, **_kwargs: ""
        plugin._record_recent_private_fact_correction = lambda *_args, **_kwargs: False
        plugin._format_private_fact_attribution_guard = lambda *_args, **_kwargs: ""
        plugin._private_passive_state_update_for_prompt = Mock(
            side_effect=AssertionError("delta state update must stay disabled")
        )
        plugin._prepared_lightweight_state_injection = Mock(return_value="legacy lightweight state")
        plugin._sanitize_schedule_context_for_private_user = lambda value, _user: value

        class _StopAfterStateBranch(Exception):
            pass

        plugin._format_private_identity_anchor_for_prompt = Mock(side_effect=_StopAfterStateBranch)
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            message_str="嗯",
            private_companion_skip_passive_input_status=True,
            get_sender_id=lambda: "10001",
            is_private_chat=lambda: True,
        )
        request = SimpleNamespace(
            system_prompt="私聊人格",
            prompt="嗯",
            contexts=[],
            extra_user_content_parts=[],
        )

        with self.assertRaises(_StopAfterStateBranch):
            await plugin.inject_humanized_state(event, request)

        plugin._private_passive_state_update_for_prompt.assert_not_called()
        plugin._prepared_lightweight_state_injection.assert_called_once()


if __name__ == "__main__":
    unittest.main()
