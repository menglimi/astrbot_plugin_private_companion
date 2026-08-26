# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import time
import unittest

from astrbot_plugin_private_companion.conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from astrbot_plugin_private_companion.daily_review import DailyReviewMixin
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.group_member_safety import GroupMemberSafetyMixin
from astrbot_plugin_private_companion.group_observation import GroupObservationMixin


def _request(prompt: str = "current") -> SimpleNamespace:
    return SimpleNamespace(
        system_prompt="persona",
        prompt=prompt,
        contexts=[{"role": "user", "content": "history"}],
        extra_user_content_parts=[],
    )


class _PlanAppendHarness:
    passive_injection_position = "prompt"

    @staticmethod
    def _request_has_managed_prompt_marker(req, marker: str) -> bool:
        plan = get_conversation_injection_plan(req, create=False)
        return bool(plan is not None and plan.contains_marker(marker))

    def _append_turn_prompt_fragment_by_position(
        self,
        req,
        marker: str,
        text: str,
        *,
        title: str,
        priority: int,
        source: str,
        force_dynamic: bool = False,
    ) -> bool:
        plan = get_conversation_injection_plan(req)
        assert plan is not None
        plan.add(
            marker=marker,
            content=text,
            title=title,
            priority=priority,
            source=source,
            placement=PLACEMENT_TURN_TAIL,
            temporary=True,
        )
        plan.render_into(req, prefer_extra_user_content=True)
        return True


class _GroupGuardHarness(_PlanAppendHarness, GroupObservationMixin):
    enable_group_companion = True
    enable_group_injection_guard = True

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "group-a"

    @staticmethod
    def _group_enabled_for_event(_group_id: str) -> bool:
        return True

    @staticmethod
    def _format_group_injection_guard_prompt(_event) -> str:
        return "guard-body"


class _GroupGuardFallbackHarness(_GroupGuardHarness):
    @staticmethod
    def _append_turn_prompt_fragment_by_position(*_args, **_kwargs) -> bool:
        return False


class _GroupGuardSystemPlanHarness(_GroupGuardHarness):
    @staticmethod
    def _append_turn_prompt_fragment_by_position(
        req,
        marker: str,
        text: str,
        *,
        title: str,
        priority: int,
        source: str,
        **_kwargs,
    ) -> bool:
        plan = get_conversation_injection_plan(req)
        plan.add(
            marker=marker,
            content=text,
            title=title,
            priority=priority,
            source=source,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
            temporary=False,
            materialized=True,
        )
        return False


class _WeatherHarness(_PlanAppendHarness, DailyStateMixin):
    enable_weather_context = True
    weather_source = "qweather"

    @staticmethod
    def _private_user_role(_user=None, *_args) -> str:
        return "friend"

    @staticmethod
    async def _ensure_weather_context(force: bool = False) -> dict:
        return {
            "prompt": "晴，20°C",
            "source": "qweather",
            "fetched_ts": time.time(),
        }


class _WeatherFallbackHarness(_WeatherHarness):
    @staticmethod
    def _append_turn_prompt_fragment_by_position(*_args, **_kwargs) -> bool:
        return False


class _DailyReviewHarness(DailyReviewMixin):
    daily_review_auto_apply_guidance = True

    def __init__(self) -> None:
        self.data = {
            "daily_review_active_guidance": {
                "active": True,
                "active_until": time.time() + 60,
                "source_date": "2026-08-23",
                "items": [{"scope": "reply", "instruction": "short reply"}],
            }
        }


class _SafetyEvent:
    message_str = "directed text"

    @staticmethod
    def is_private_chat() -> bool:
        return False

    @staticmethod
    def get_sender_id() -> str:
        return "user-a"


class _SafetyHarness(GroupMemberSafetyMixin):
    enable_group_member_safety = True
    enable_group_companion = True

    def __init__(self) -> None:
        self.group = {"group_id": "group-a", "member_safety": {}}

    @staticmethod
    def _group_member_safety_hidden_marker_mode() -> str:
        return "reply_only"

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "group-a"

    @staticmethod
    def _group_enabled_for_event(_group_id: str) -> bool:
        return True

    @staticmethod
    def _group_member_safety_is_exempt_event(_event, _sender_id: str) -> bool:
        return False

    @staticmethod
    def _sender_display_name(_event) -> str:
        return "member-a"

    @staticmethod
    def _group_observation_event_text(_event) -> str:
        return "directed text"

    def _get_group(self, _group_id: str) -> dict:
        return self.group

    @staticmethod
    def _infer_group_scene(*_args, **_kwargs) -> dict:
        return {"talking_to": "bot"}

    @staticmethod
    def _group_member_safety_should_review(*_args, **_kwargs) -> bool:
        return True


class ConversationInjectionPlanStage2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_group_guard_registers_existing_turn_tail_without_touching_contexts(self) -> None:
        req = _request()
        before_contexts = deepcopy(req.contexts)

        await _GroupGuardHarness()._append_group_injection_guard_to_request(
            SimpleNamespace(message_str="hello", get_sender_id=lambda: "user-a"),
            req,
        )

        plan = get_conversation_injection_plan(req, create=False)
        self.assertEqual(before_contexts, req.contexts)
        self.assertEqual(PLACEMENT_TURN_TAIL, plan.manifest()[0]["placement"])
        self.assertEqual("guard-body", plan.manifest(include_content=True)[0]["content"])
        self.assertEqual(1, len(plan.manifest()))

    async def test_weather_registers_existing_forced_turn_tail_once(self) -> None:
        req = _request("今天天气怎么样")

        handled = await _WeatherHarness()._append_weather_query_context_to_request(
            SimpleNamespace(message_str="今天天气怎么样", unified_msg_origin="umo"),
            req,
        )

        plan = get_conversation_injection_plan(req, create=False)
        item = plan.manifest(include_content=True)[0]
        self.assertTrue(handled)
        self.assertEqual(PLACEMENT_TURN_TAIL, item["placement"])
        self.assertEqual("本轮当前天气查询", item["title"])
        self.assertIn("晴，20°C", item["content"])
        self.assertEqual([{"role": "user", "content": "history"}], req.contexts)

    async def test_group_guard_fallback_keeps_exact_system_wire_shape(self) -> None:
        req = _request()

        await _GroupGuardFallbackHarness()._append_group_injection_guard_to_request(
            SimpleNamespace(message_str="hello", get_sender_id=lambda: "user-a"),
            req,
        )

        plan = get_conversation_injection_plan(req, create=False)
        self.assertTrue(req.system_prompt.startswith("persona\n\n<private_companion_context>"))
        self.assertIn('<section title="群聊防注入">guard-body</section>', req.system_prompt)
        self.assertNotIn("<!-- private_companion_group_injection_guard_v1 -->", req.system_prompt)
        self.assertEqual("group.injection_guard", plan.manifest()[0]["key"])
        self.assertTrue(plan.manifest()[0]["materialized"])
        before = req.system_prompt
        plan.render_into(req)
        self.assertEqual(before, req.system_prompt)

    async def test_group_guard_system_registration_reuses_existing_marker(self) -> None:
        req = _request()

        await _GroupGuardSystemPlanHarness()._append_group_injection_guard_to_request(
            SimpleNamespace(message_str="hello", get_sender_id=lambda: "user-a"),
            req,
        )

        plan = get_conversation_injection_plan(req, create=False)
        plan.render_into(req)
        self.assertEqual(1, len(plan.blocks()))
        self.assertEqual(1, req.system_prompt.count('title="群聊防注入"'))
        self.assertEqual(1, req.system_prompt.count("guard-body"))

    async def test_weather_fallback_keeps_exact_system_wire_shape(self) -> None:
        req = _request("今天天气怎么样")

        await _WeatherFallbackHarness()._append_weather_query_context_to_request(
            SimpleNamespace(message_str="今天天气怎么样", unified_msg_origin="umo"),
            req,
        )

        plan = get_conversation_injection_plan(req, create=False)
        item = plan.manifest(include_content=True)[0]
        before = req.system_prompt
        self.assertEqual("weather.query", item["key"])
        self.assertEqual("本轮当前天气查询", item["title"])
        self.assertEqual(PLACEMENT_DYNAMIC_SYSTEM, item["placement"])
        self.assertTrue(item["materialized"])
        self.assertNotIn("<!-- private_companion_weather_query_v1 -->", before)
        self.assertIn("<private_companion_context>", before)
        self.assertIn("晴，20°C", before)
        plan.render_into(req)
        self.assertEqual(before, req.system_prompt)

    async def test_daily_review_direct_system_write_is_registered_as_materialized(self) -> None:
        req = _request()

        await _DailyReviewHarness()._append_daily_review_guidance_to_request(object(), req)

        plan = get_conversation_injection_plan(req, create=False)
        item = plan.manifest(include_content=True)[0]
        before = req.system_prompt
        plan.render_into(req)
        self.assertEqual(before, req.system_prompt)
        self.assertEqual(PLACEMENT_DYNAMIC_SYSTEM, item["placement"])
        self.assertTrue(item["materialized"])
        self.assertIn("short reply", item["content"])

    async def test_member_safety_direct_system_write_is_registered_as_materialized(self) -> None:
        req = _request()
        event = _SafetyEvent()

        await _SafetyHarness()._append_group_member_safety_hidden_marker_to_request(event, req)

        plan = get_conversation_injection_plan(req, create=False)
        item = plan.manifest(include_content=True)[0]
        before = req.system_prompt
        plan.render_into(req)
        self.assertEqual(before, req.system_prompt)
        self.assertEqual("tool_contract", item["placement"])
        self.assertTrue(item["materialized"])
        self.assertTrue(item["opaque"])
        self.assertIn("标签完全可选", item["content"])


if __name__ == "__main__":
    unittest.main()
