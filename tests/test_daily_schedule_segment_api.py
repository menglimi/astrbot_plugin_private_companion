# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
import time
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class SchedulePluginHarness(DailyStateMixin):
    detail_enhancement_lead_minutes = 15
    bot_name = "测试Bot"

    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.saved = 0
        self.data = {
            "daily_plan": {
                "date": "2026-07-11",
                "quality": {"score": 90, "level": "good", "issues": []},
                "items": [
                    {
                        "time": "09:00",
                        "end": "10:00",
                        "activity": "整理房间",
                        "lifecycle_status": "planned",
                        "basis": ["persona"],
                        "confidence": 0.8,
                    },
                    {
                        "time": "10:00",
                        "end": "11:00",
                        "activity": "处理手边事项",
                        "lifecycle_status": "planned",
                        "basis": ["state"],
                        "confidence": 0.76,
                    },
                ],
            },
            "daily_state": {},
            "detail_enhanced_day": "2026-07-11",
            "detail_enhanced_segments": {
                "2026-07-11:0:09:00": {
                    "status": "done",
                    "summary": "把房间慢慢收拾好。",
                    "interaction_updates": [{"note": "用户说先收桌面"}],
                    "today_events": [
                        {"window": "09:00-09:20", "event": "先收拾桌面", "lifecycle_status": "planned"}
                    ],
                    "proactive_events": [
                        {"window": "09:40-09:50", "topic": "分享整理进度", "lifecycle_status": "planned"}
                    ],
                }
            },
            "daily_story_plan": {"date": "2026-07-11", "today_events": [], "proactive_events": []},
        }

    def _is_plan_date_active(self, _value) -> bool:
        return True

    def _environment_now(self) -> datetime:
        return datetime(2026, 7, 11, 9, 15)

    def _effective_plan_now_minutes(self, _date_text: str):
        return 9 * 60 + 15

    def _parse_window_minutes(self, value):
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
        if not match:
            return None, None
        return (
            int(match.group(1)) * 60 + int(match.group(2)),
            int(match.group(3)) * 60 + int(match.group(4)),
        )

    def _save_data_sync(self) -> None:
        self.saved += 1

    async def _ensure_daily_state(self, force: bool = False):
        return self.data.get("daily_state", {})

    def _sanitize_daily_plan_inplace(self, _value) -> bool:
        return False

    def _remember_detail_enhancement_history(self, *_args, **_kwargs) -> None:
        return None

    def _sanitize_detail_enhanced_segments_inplace(self, _value) -> bool:
        return False

    def _refresh_daily_state_location_from_plan(self, **_kwargs) -> None:
        return None


class DailyScheduleSegmentApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = SchedulePluginHarness()
        self.api = PrivateCompanionPageApi(self.plugin)
        self.api._overview_data_snapshot_locked = lambda value: deepcopy(value)

    async def _call(self, payload: dict) -> dict:
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            return await self.api.regenerate_daily_detail_segment()

    async def test_cancel_segment_marks_lifecycle_and_removes_story_candidates(self):
        result = await self._call({"key": "2026-07-11:0:09:00", "action": "cancel"})

        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["cancelled"])
        item = self.plugin.data["daily_plan"]["items"][0]
        snapshot = self.plugin.data["detail_enhanced_segments"]["2026-07-11:0:09:00"]
        self.assertEqual(item["lifecycle_status"], "cancelled")
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertTrue(all(event["lifecycle_status"] == "cancelled" for event in snapshot["today_events"]))
        self.assertEqual(self.plugin.data["daily_story_plan"]["today_events"], [])
        self.assertEqual(self.plugin.data["daily_story_plan"]["proactive_events"], [])
        self.assertIsNone(self.plugin._get_current_plan_item(self.plugin.data["daily_plan"]))
        self.assertNotIn(
            "2026-07-11:0:09:00",
            [
                segment["key"]
                for segment in self.plugin._collect_detail_segments(
                    self.plugin.data["daily_plan"],
                    self.plugin.data["detail_enhanced_segments"],
                )
            ],
        )

    async def test_cancel_failed_snapshot_becomes_terminal(self):
        key = "2026-07-11:0:09:00"
        self.plugin.data["detail_enhanced_segments"][key] = {
            "status": "failed",
            "retry_after_ts": 0,
            "today_events": [{"window": "09:00-09:20", "event": "旧事件"}],
            "proactive_events": [],
        }

        result = await self._call({"key": key, "action": "cancel"})

        self.assertTrue(result["success"])
        self.assertEqual(self.plugin.data["detail_enhanced_segments"][key]["status"], "cancelled")
        candidates = self.plugin._collect_detail_segments(
            self.plugin.data["daily_plan"],
            self.plugin.data["detail_enhanced_segments"],
        )
        self.assertNotIn(key, [segment["key"] for segment in candidates])

    def test_selector_matches_time_activity_and_ordinal(self):
        segment, error = self.plugin._resolve_daily_plan_segment_selector("09:30")
        self.assertEqual("", error)
        self.assertEqual(0, segment["index"])

        segment, error = self.plugin._resolve_daily_plan_segment_selector("处理手边事项")
        self.assertEqual("", error)
        self.assertEqual(1, segment["index"])

        segment, error = self.plugin._resolve_daily_plan_segment_selector("第二段")
        self.assertEqual("", error)
        self.assertEqual(1, segment["index"])

    def test_selector_understands_chinese_period_time(self):
        self.plugin.data["daily_plan"]["items"].append(
            {
                "time": "15:00",
                "end": "16:00",
                "activity": "下午阅读",
                "lifecycle_status": "planned",
            }
        )

        segment, error = self.plugin._resolve_daily_plan_segment_selector("下午三点")

        self.assertEqual("", error)
        self.assertEqual(2, segment["index"])

    def test_time_selector_is_not_mistaken_for_ordinal_on_long_plan(self):
        self.plugin.data["daily_plan"]["items"] = [
            {
                "time": f"{hour:02d}:00",
                "end": f"{hour + 1:02d}:00",
                "activity": f"第 {hour - 7} 项活动",
                "lifecycle_status": "planned",
            }
            for hour in range(8, 18)
        ]

        segment, error = self.plugin._resolve_daily_plan_segment_selector("09:30")

        self.assertEqual("", error)
        self.assertEqual(1, segment["index"])

    def test_natural_language_schedule_tool_prompt_only_matches_explicit_operations(self):
        self.assertTrue(LlmToolActionsMixin._schedule_management_instruction_matches("把下午三点的日程删掉"))
        self.assertTrue(LlmToolActionsMixin._schedule_management_instruction_matches("重新细化第二段安排"))
        self.assertFalse(LlmToolActionsMixin._schedule_management_instruction_matches("我下午三点要出门"))
        self.assertFalse(LlmToolActionsMixin._schedule_management_instruction_matches("你今晚可以早点休息"))

        call = LlmToolActionsMixin._plaintext_tool_call_from_object(
            {"name": "pc_manage_schedule", "arguments": {"action": "cancel", "selector": "下午三点"}}
        )
        self.assertEqual("pc_manage_schedule", call["name"])

    async def test_chat_cancel_selector_uses_terminal_cancel_lifecycle(self):
        ok, message = await self.plugin._cancel_daily_plan_segment_by_selector("整理房间")

        self.assertTrue(ok)
        self.assertIn("09:00-10:00", message)
        self.assertEqual("cancelled", self.plugin.data["daily_plan"]["items"][0]["lifecycle_status"])
        self.assertEqual(
            "cancelled",
            self.plugin.data["detail_enhanced_segments"]["2026-07-11:0:09:00"]["status"],
        )
        self.assertIn("09:00-10:00｜已取消 整理房间", self.plugin._format_daily_plan(self.plugin.data["daily_plan"]))

    async def test_chat_regenerate_selector_only_rebuilds_matched_segment(self):
        async def generator(_plugin, segment, _plan, _state):
            self.assertEqual(1, segment["index"])
            return {
                "summary": "把手边事项分两步处理。",
                "summary_basis": ["coarse_plan"],
                "summary_confidence": 0.85,
                "today_events": [
                    {"window": "10:00-10:30", "event": "处理第一步", "lifecycle_status": "planned"},
                    {"window": "10:30-11:00", "event": "完成收尾", "lifecycle_status": "planned"},
                ],
                "proactive_events": [],
                "state_variables": [],
                "presence_status": {},
                "quality": {"score": 90},
            }

        ok, message, detail = await self.plugin._regenerate_daily_plan_segment_by_selector(
            "10点的处理事项",
            generator,
        )

        self.assertTrue(ok)
        self.assertIn("10:00-11:00", message)
        self.assertEqual("把手边事项分两步处理。", detail["summary"])
        self.assertEqual(
            "done",
            self.plugin.data["detail_enhanced_segments"]["2026-07-11:1:10:00"]["status"],
        )
        self.assertEqual("done", self.plugin.data["detail_enhanced_segments"]["2026-07-11:0:09:00"]["status"])

    async def test_current_detail_reset_replaces_existing_snapshot(self):
        async def generator(_plugin, segment, _plan, _state):
            self.assertEqual(0, segment["index"])
            return {
                "summary": "当前段已经重新生成。",
                "summary_basis": ["coarse_plan"],
                "summary_confidence": 0.9,
                "today_events": [
                    {"window": "09:00-09:30", "event": "重新整理桌面", "lifecycle_status": "active"},
                ],
                "proactive_events": [],
                "state_variables": [],
                "presence_status": {},
                "quality": {"score": 92},
            }

        ok, _, detail = await self.plugin._regenerate_daily_plan_segment_by_selector("当前", generator)

        self.assertTrue(ok)
        self.assertEqual("当前段已经重新生成。", detail["summary"])
        snapshot = self.plugin.data["detail_enhanced_segments"]["2026-07-11:0:09:00"]
        self.assertEqual("当前段已经重新生成。", snapshot["summary"])

    def test_companion_command_normalizes_spaced_management_targets(self):
        normalize = InteractionUtilsMixin._normalize_companion_command_action
        self.assertEqual(("重置插件", ""), normalize("重置", "插件"))
        self.assertEqual(("重置细化", ""), normalize("重置", "细化"))
        self.assertEqual(("重置穿搭图", "红色外套"), normalize("重置", "穿搭图 红色外套"))
        self.assertEqual(("重置夹层密码", ""), normalize("重置", "夹层密码"))
        self.assertEqual(("日期删除", "生日"), normalize("删除", "日期 生日"))
        self.assertEqual(("删除话头", "周末约定"), normalize("删除", "话头 周末约定"))
        self.assertEqual(("绑定城市", "朝阳区,北京"), normalize("绑定", "城市 朝阳区,北京"))
        self.assertEqual(("查看城市", ""), normalize("查看", "城市"))
        self.assertEqual(("解绑城市", ""), normalize("解绑", "城市"))
        self.assertEqual(("重置", ""), normalize("重置", ""))

    def test_companion_help_lists_qweather_city_commands(self):
        help_text = InteractionUtilsMixin()._help_text()
        self.assertIn("陪伴 绑定城市 <城市|区县,城市|LocationID>", help_text)
        self.assertIn("陪伴 查看城市 / 解绑城市", help_text)

    def test_qweather_city_commands_have_owner_private_guard(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        self.assertIn(
            "if action in qweather_location_actions and not self._can_manage_sensitive_location(event):",
            source,
        )
        self.assertIn("self._sensitive_location_denied_text()", source)

    def test_bare_reset_is_not_an_alias_for_full_plugin_reset(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        self.assertNotIn('{"重置插件", "重置", "全部重置"}', source)
        self.assertIn('if action in {"重置插件", "全部重置"}:', source)
        self.assertIn('_regenerate_daily_plan_segment_by_selector(\n                "当前",', source)

    async def test_regenerate_segment_preserves_interaction_updates(self):
        detail = {
            "summary": "重新安排后分段整理。",
            "summary_basis": ["coarse_plan", "adjustment"],
            "summary_confidence": 0.88,
            "today_events": [
                {"window": "09:00-09:25", "event": "先整理桌面", "lifecycle_status": "planned"},
                {"window": "09:25-09:50", "event": "再收纳杂物", "lifecycle_status": "planned"},
            ],
            "proactive_events": [],
            "state_variables": [],
            "presence_status": {},
            "quality": {"score": 92, "level": "good", "issues": []},
        }
        with patch(
            "astrbot_plugin_private_companion.page_api.generate_detail_enhancement",
            AsyncMock(return_value=detail),
        ):
            result = await self._call({"key": "2026-07-11:0:09:00"})

        self.assertTrue(result["success"])
        item = self.plugin.data["daily_plan"]["items"][0]
        snapshot = self.plugin.data["detail_enhanced_segments"]["2026-07-11:0:09:00"]
        self.assertEqual(item["lifecycle_status"], "changed")
        self.assertEqual(snapshot["interaction_updates"], [{"note": "用户说先收桌面"}])
        self.assertEqual(snapshot["quality"]["score"], 92)

    async def test_manual_regeneration_replaces_stale_detail_day(self):
        self.plugin.data["detail_enhanced_day"] = "2026-07-10"
        self.plugin.data["detail_enhanced_segments"] = {
            "2026-07-10:0:09:00": {"status": "done", "summary": "昨天的细化"}
        }
        detail = {
            "summary": "今天重新细化。",
            "today_events": [{"window": "09:00-09:30", "event": "整理今天的房间"}],
            "proactive_events": [],
            "state_variables": [],
            "presence_status": {},
        }
        with patch(
            "astrbot_plugin_private_companion.page_api.generate_detail_enhancement",
            AsyncMock(return_value=detail),
        ):
            result = await self._call({"key": "2026-07-11:0:09:00"})

        self.assertTrue(result["success"])
        self.assertEqual("2026-07-11", self.plugin.data["detail_enhanced_day"])
        self.assertNotIn("2026-07-10:0:09:00", self.plugin.data["detail_enhanced_segments"])
        self.assertIn("2026-07-11:0:09:00", self.plugin.data["detail_enhanced_segments"])

    async def test_unknown_action_is_rejected_without_mutation(self):
        before = deepcopy(self.plugin.data)
        result = await self._call({"key": "2026-07-11:0:09:00", "action": "delete"})
        self.assertFalse(result["success"])
        self.assertEqual(self.plugin.data, before)

    async def test_inflight_regeneration_cannot_overwrite_cancel(self):
        key = "2026-07-11:0:09:00"
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_detail(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {
                "summary": "迟到的重做结果",
                "summary_basis": ["coarse_plan"],
                "summary_confidence": 0.8,
                "today_events": [
                    {"window": "09:00-09:20", "event": "不应重新写入", "lifecycle_status": "planned"}
                ],
                "proactive_events": [],
                "state_variables": [],
                "presence_status": {},
                "quality": {"score": 90},
            }

        with patch(
            "astrbot_plugin_private_companion.page_api.generate_detail_enhancement",
            delayed_detail,
        ):
            regenerating = asyncio.create_task(self._call({"key": key}))
            await started.wait()
            cancelled = await self._call({"key": key, "action": "cancel"})
            release.set()
            late_result = await regenerating

        self.assertTrue(cancelled["success"])
        self.assertFalse(late_result["success"])
        self.assertEqual(self.plugin.data["daily_plan"]["items"][0]["lifecycle_status"], "cancelled")
        self.assertEqual(self.plugin.data["detail_enhanced_segments"][key]["status"], "cancelled")
        self.assertEqual(self.plugin.data["daily_story_plan"]["today_events"], [])

    async def test_second_regeneration_is_rejected_while_first_is_running(self):
        key = "2026-07-11:0:09:00"
        self.plugin.data["detail_enhanced_segments"][key] = {
            "status": "generating",
            "started_at": "09:15",
            "started_ts": time.time(),
            "generation_id": "already-running",
        }

        result = await self._call({"key": key})

        self.assertFalse(result["success"])
        self.assertIn("正在细化", result["error"])
        self.assertEqual(
            self.plugin.data["detail_enhanced_segments"][key]["generation_id"],
            "already-running",
        )

    async def test_failed_regeneration_restores_cancelled_lifecycle(self):
        key = "2026-07-11:0:09:00"
        await self._call({"key": key, "action": "cancel"})
        with patch(
            "astrbot_plugin_private_companion.page_api.generate_detail_enhancement",
            AsyncMock(side_effect=RuntimeError("模型暂时不可用")),
        ):
            result = await self._call({"key": key})

        self.assertFalse(result["success"])
        self.assertEqual(self.plugin.data["daily_plan"]["items"][0]["lifecycle_status"], "cancelled")
        self.assertEqual(self.plugin.data["detail_enhanced_segments"][key]["status"], "cancelled")
        self.assertNotIn("_detail_generation_id", self.plugin.data["daily_plan"]["items"][0])

    def test_timeline_contains_unenhanced_plan_placeholder(self):
        timeline = self.api._daily_timeline_summary(self.plugin.data)

        self.assertEqual(timeline["segment_count"], 2)
        pending = next(item for item in timeline["segments"] if item["key"] == "2026-07-11:1:10:00")
        self.assertEqual(pending["status"], "")
        self.assertEqual(pending["summary"], "处理手边事项")
        self.assertEqual(pending["basis"], ["state"])
        self.assertEqual(timeline["plan_quality"]["score"], 90)

    def test_timeline_hides_snapshots_when_detail_day_is_stale(self):
        self.plugin.data["detail_enhanced_day"] = "2026-07-10"

        timeline = self.api._daily_timeline_summary(self.plugin.data)

        first = next(item for item in timeline["segments"] if item["key"] == "2026-07-11:0:09:00")
        self.assertEqual("", first["status"])
        self.assertEqual("整理房间", first["summary"])
        self.assertEqual(2, timeline["segment_count"])

    async def test_daily_plan_rollover_clears_stale_detail_even_when_enhancement_is_disabled(self):
        self.plugin.enable_daily_plan = True
        self.plugin.enable_detail_enhancement = False
        self.plugin.data["detail_enhanced_day"] = "2026-07-10"
        self.plugin.data["daily_story_plan"] = {
            "date": "2026-07-10",
            "today_events": [{"event": "昨天的事件"}],
        }

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-11"):
            plan = await self.plugin._ensure_daily_plan(force=False)

        self.assertEqual("2026-07-11", plan["date"])
        self.assertEqual("2026-07-11", self.plugin.data["detail_enhanced_day"])
        self.assertEqual({}, self.plugin.data["detail_enhanced_segments"])
        self.assertEqual({}, self.plugin.data["daily_story_plan"])

    def test_cross_midnight_timeline_and_runtime_use_monotonic_axis(self):
        self.plugin.data["daily_plan"]["items"] = [
            {"time": "22:00", "end": "23:00", "activity": "夜间整理", "lifecycle_status": "planned"},
            {"time": "23:00", "end": "00:30", "activity": "准备休息", "lifecycle_status": "planned"},
            {"time": "00:30", "end": "01:30", "activity": "继续收尾", "lifecycle_status": "planned"},
        ]
        self.plugin.data["detail_enhanced_segments"] = {}
        self.plugin._effective_plan_now_minutes = lambda _date: 24 * 60 + 45

        timeline = self.api._daily_timeline_summary(self.plugin.data)

        self.assertEqual([item["window"] for item in timeline["segments"]], ["22:00-23:00", "23:00-00:30", "00:30-01:30"])
        self.assertEqual(timeline["segments"][2]["lifecycle"], "active")
        current = self.plugin._current_detail_segment_for_update()
        self.assertIsNotNone(current)
        self.assertEqual(current["index"], 2)

    def test_adjustment_scope_uses_recorded_anchor_instead_of_moving_current_segment(self):
        self.plugin.data["schedule_adjustments"] = [
            {
                "source_role": "owner",
                "source": "测试介入",
                "note": "只影响最初所在时间段",
                "scope": "当前段",
                "scope_key": "current_only",
                "anchor_segment_index": 0,
                "expires_at": time.time() + 3600,
            }
        ]
        self.plugin._current_detail_segment_for_update = lambda: {"index": 1}

        original_segment = self.plugin._format_schedule_adjustments_for_prompt({"index": 0})
        moved_current_segment = self.plugin._format_schedule_adjustments_for_prompt({"index": 1})

        self.assertIn("只影响最初所在时间段", original_segment)
        self.assertNotIn("只影响最初所在时间段", moved_current_segment)

    def test_proactive_only_adjustment_does_not_enter_coarse_plan_fields(self):
        self.plugin.data["schedule_adjustments"] = [
            {
                "source_role": "owner",
                "source": "用户边界",
                "note": "只降低主动频率",
                "scope": "今日后续主动策略",
                "scope_key": "proactive_only",
                "anchor_segment_index": 0,
                "expires_at": time.time() + 3600,
            }
        ]

        self.assertNotIn("只降低主动频率", self.plugin._format_schedule_adjustments_for_prompt())
        detail_text = self.plugin._format_schedule_adjustments_for_prompt({"index": 0})
        self.assertIn("只降低主动频率", detail_text)
        self.assertIn("只允许影响 proactive_events", detail_text)

    def test_return_home_resolves_prior_until_home_adjustment(self):
        self.plugin.data["schedule_adjustments"] = [
            {
                "source_role": "owner",
                "source": "用户带出/同行",
                "note": "保持在外面",
                "scope": "当前段和今日后续直到回家线索出现",
                "scope_key": "until_condition",
                "condition_key": "return_home",
                "expires_at": time.time() + 3600,
            }
        ]
        self.plugin._private_user_role = lambda _user: "owner"
        self.plugin._detect_schedule_adjustment_from_interaction = lambda _text: {
            "source": "用户带回/回家",
            "note": "已经回到家里",
            "scope": "当前段和下一段",
            "intensity": "中",
        }
        self.plugin._record_detail_interaction_update = lambda _item: None
        self.plugin._invalidate_detail_after_interaction = lambda **_kwargs: None

        changed = self.plugin._record_schedule_adjustment_from_interaction("到家了", {"user_id": "owner"})

        self.assertTrue(changed)
        adjustments = self.plugin.data["schedule_adjustments"]
        self.assertFalse(any(item.get("condition_key") == "return_home" for item in adjustments))
        self.assertTrue(any(item.get("source") == "用户带回/回家" for item in adjustments))


if __name__ == "__main__":
    unittest.main()
