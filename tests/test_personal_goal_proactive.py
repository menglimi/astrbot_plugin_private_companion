# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import sys
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


NOW = 1_752_289_200.0


def _patch_now_ts(stack: ExitStack, value: float) -> int:
    """把 `_now_ts` 打到 `daily_state*` 全族的每个模块上。

    mixin 拆分后，方法用的是**自己所在模块**的 `_now_ts`（各模块各自
    `from .helpers import _now_ts`）。只 patch 宿主 `daily_state` 会静默失效 ——
    被搬到 `daily_state_skill_growth` 等模块的方法仍读真实时钟。
    """
    patched = 0
    for name, module in list(sys.modules.items()):
        if not name.startswith("astrbot_plugin_private_companion.daily_state"):
            continue
        if hasattr(module, "_now_ts"):
            stack.enter_context(patch.object(module, "_now_ts", return_value=value))
            patched += 1
    return patched


class PersonalGoalHarness(DailyStateMixin):
    enable_personal_goals = True
    enable_personal_goal_auto_progress = True
    personal_goal_share_cooldown_hours = 12
    personal_goal_stall_days = 3

    def __init__(self, progress: int = 20) -> None:
        self._data_lock = asyncio.Lock()
        self.events: list[tuple[dict, dict]] = []
        self.data = {
            "personal_goal_state": {},
            "personal_goals": [{
                "id": "goal-reading",
                "title": "读完这本书",
                "category": "阅读",
                "status": "active",
                "progress": progress,
                "next_step": "读下一章",
                "keywords": ["阅读", "下一章"],
                "auto_step": 10,
                "created_at": NOW - 86400,
                "last_progress_at": NOW - 86400,
                "recent_logs": [],
            }],
            "daily_plan": {"date": "2025-07-12", "items": [{"time": "09:00", "end": "10:00", "activity": "安静阅读下一章"}]},
        }

    def _effective_plan_now_minutes(self, _day_key: str):
        return 11 * 60

    def _queue_personal_goal_candidate_locked(self, goal, event, *, now):
        self.events.append((deepcopy(goal), deepcopy(event)))
        return 1

    def _save_data_sync(self, **_kwargs):
        return None


class PersonalGoalProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def settle(self, harness: PersonalGoalHarness) -> None:
        with ExitStack() as stack:
            assert _patch_now_ts(stack, NOW) > 0, "没有 patch 到任何 daily_state* 模块的 _now_ts"
            await harness._maybe_settle_personal_goals(force=True)

    async def test_completed_matching_segment_advances_and_crosses_milestone(self) -> None:
        harness = PersonalGoalHarness(progress=20)
        await self.settle(harness)
        goal = harness.data["personal_goals"][0]
        self.assertEqual(goal["progress"], 30)
        self.assertEqual(harness.events[0][1]["kind"], "progress")
        self.assertIn("阅读下一章", harness.events[0][1]["evidence"])

    async def test_segment_not_finished_does_not_advance(self) -> None:
        harness = PersonalGoalHarness(progress=20)
        harness._effective_plan_now_minutes = lambda _day: 9 * 60 + 30
        await self.settle(harness)
        self.assertEqual(harness.data["personal_goals"][0]["progress"], 20)
        self.assertEqual(harness.events, [])

    async def test_cancelled_segment_does_not_advance(self) -> None:
        harness = PersonalGoalHarness(progress=20)
        harness.data["daily_plan"]["items"][0]["lifecycle_status"] = "cancelled"
        await self.settle(harness)
        self.assertEqual(harness.data["personal_goals"][0]["progress"], 20)
        self.assertEqual(harness.events, [])

    async def test_category_alone_is_not_progress_evidence(self) -> None:
        harness = PersonalGoalHarness(progress=20)
        goal = harness.data["personal_goals"][0]
        goal["title"] = "读完技术手册"
        goal["next_step"] = "读第二章"
        goal["keywords"] = ["技术手册"]
        harness.data["daily_plan"]["items"][0]["activity"] = "阅读旅行随笔"
        await self.settle(harness)
        self.assertEqual(goal["progress"], 20)

    async def test_progress_without_new_milestone_does_not_offer(self) -> None:
        harness = PersonalGoalHarness(progress=30)
        await self.settle(harness)
        self.assertEqual(harness.data["personal_goals"][0]["progress"], 40)
        self.assertEqual(harness.events, [])

    async def test_completion_marks_goal_and_offers_completion(self) -> None:
        harness = PersonalGoalHarness(progress=95)
        await self.settle(harness)
        goal = harness.data["personal_goals"][0]
        self.assertEqual(goal["progress"], 100)
        self.assertEqual(goal["status"], "completed")
        self.assertEqual(harness.events[0][1]["kind"], "completed")

    async def test_stagnation_is_offered_only_once(self) -> None:
        harness = PersonalGoalHarness(progress=10)
        harness.data["daily_plan"]["items"] = []
        harness.data["personal_goals"][0]["last_progress_at"] = NOW - 4 * 86400
        await self.settle(harness)
        await self.settle(harness)
        self.assertEqual(len(harness.events), 1)
        self.assertEqual(harness.events[0][1]["kind"], "stalled")

    async def test_milestone_waits_through_cooldown_instead_of_being_lost(self) -> None:
        harness = PersonalGoalHarness(progress=20)
        harness.data["personal_goals"][0]["last_shared_at"] = NOW - 60
        await self.settle(harness)
        goal = harness.data["personal_goals"][0]
        self.assertEqual(harness.events, [])
        self.assertEqual(goal["pending_share_event"]["kind"], "progress")
        goal["last_shared_at"] = NOW - 13 * 3600
        harness.data["personal_goal_state"]["processed_schedule_keys"] = []
        harness.data["daily_plan"]["items"] = []
        await self.settle(harness)
        self.assertEqual(len(harness.events), 1)
        self.assertNotIn("pending_share_event", goal)

    def test_schedule_context_and_prompt_keep_factual_boundaries(self) -> None:
        harness = PersonalGoalHarness()
        context = harness._format_personal_goals_schedule_context()
        self.assertIn("读完这本书", context)
        self.assertIn("读下一章", context)
        user = {"planned_personal_goal_context": {"title": "读完这本书", "progress": 25, "next_step": "读下一章", "event": {"kind": "progress", "evidence": "09:00-10:00 阅读下一章"}}}
        prompt = harness._format_personal_goal_prompt(user, reason="personal_goal_progress")
        self.assertIn("09:00-10:00 阅读下一章", prompt)
        self.assertIn("不虚构", prompt)
        self.assertIn("不把目标变成向用户索取监督", prompt)
        section = harness._format_personal_goal_prompt_section(
            user,
            reason="personal_goal_progress",
        )
        self.assertEqual("personal_goal.progress", section.key)
        self.assertEqual("非创作型个人目标", section.title)
        self.assertEqual("daily_state", section.source)
        self.assertNotIn("【非创作型个人目标】", section.content)


if __name__ == "__main__":
    unittest.main()
