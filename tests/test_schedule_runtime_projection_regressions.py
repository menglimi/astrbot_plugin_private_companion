# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.helpers import _today_key
from astrbot_plugin_private_companion.runtime_scene_resolver import RuntimeSceneResolver


class _ScheduleHarness(DailyStateMixin):
    enable_qq_presence_sync = False
    detail_enhancement_lead_minutes = 15

    def __init__(self) -> None:
        today = _today_key()
        self._now = datetime.combine(
            date.fromisoformat(today),
            time(10, 15),
            tzinfo=datetime.now().astimezone().tzinfo,
        )
        self.data = {
            "daily_state": {"date": today, "location": "家里"},
            "daily_plan": {
                "date": today,
                "items": [
                    {"time": "09:00", "end": "10:00", "activity": "在家整理", "lifecycle_status": "planned"},
                    {"time": "10:00", "end": "11:00", "activity": "去学校上课", "lifecycle_status": "planned"},
                ],
            },
            "detail_enhanced_segments": {
                f"{today}:1:10:00": {
                    "status": "done",
                    "location": "学校教室",
                    "location_basis": ["coarse_plan"],
                    "location_confidence": 0.8,
                }
            },
        }
        self.detail_enhanced_day = today
        self.saved = 0

    def _agenda_disclosure_view(self, *_args, **_kwargs):
        return {"entries": []}

    def _environment_now(self) -> datetime:
        return self._now

    def _environment_now_minutes(self) -> int:
        return self._now.hour * 60 + self._now.minute

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class _PresenceScheduleHarness(_ScheduleHarness):
    enable_qq_presence_sync = True
    enable_qq_custom_presence_sync = True

    def __init__(self) -> None:
        super().__init__()
        today = _today_key()
        self.data["detail_enhanced_segments"] = {
            f"{today}:0:09:00": {
                "status": "done",
                "presence_status": {"mode": "custom", "custom_text": "写题中"},
            }
        }
        self.data["qq_presence_state"] = {
            "detail_key": f"{today}:0:09:00",
            "date": today,
            "plan_date": today,
            "mode": "custom",
            "custom_text": "写题中",
            "updated_at": 0,
            "ok": True,
            "managed_by_plugin": True,
        }
        self.online_calls: list[str] = []
        self.custom_calls: list[str] = []

    async def _set_qq_online_presence(self, mode: str) -> tuple[bool, str]:
        self.online_calls.append(mode)
        return True, mode

    async def _set_qq_custom_presence(self, text: str) -> tuple[bool, str]:
        self.custom_calls.append(text)
        return True, text


class ScheduleRuntimeProjectionRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_display_status_follows_clock_without_changing_evidence_status(self) -> None:
        harness = _ScheduleHarness()
        harness._effective_plan_now_minutes = lambda _date: 10 * 60 + 30
        plan = harness.data["daily_plan"]

        self.assertIn(harness._plan_item_runtime_status(plan, plan["items"][0], 0), {"planned", "unknown"})
        self.assertEqual("completed", harness._plan_item_display_status(plan, plan["items"][0], 0))
        self.assertEqual("active", harness._plan_item_display_status(plan, plan["items"][1], 1))

    async def test_current_segment_reapplies_prebuilt_detail_location_at_start(self) -> None:
        harness = _ScheduleHarness()
        harness._effective_plan_now_minutes = lambda _date: 10 * 60 + 15

        await harness._ensure_current_detail_presence_status()

        state = harness.data["daily_state"]
        self.assertEqual("学校教室", state["location"])
        self.assertEqual("detail_model", state["location_source"])
        self.assertEqual("schedule", state["location_projection"])
        self.assertGreaterEqual(harness.saved, 1)

    async def test_presence_status_moves_with_segment_and_clears_old_plugin_status(self) -> None:
        harness = _PresenceScheduleHarness()
        harness._effective_plan_now_minutes = lambda _date: 10 * 60 + 15

        # The second segment has not finished detail generation yet. The old
        # plugin-managed custom status must not remain stuck across the switch.
        await harness._ensure_current_detail_presence_status()
        self.assertEqual(["online"], harness.online_calls)
        self.assertEqual(f"{_today_key()}:1:10:00", harness.data["qq_presence_state"]["detail_key"])
        self.assertEqual("online", harness.data["qq_presence_state"]["mode"])

        harness.data["detail_enhanced_segments"][f"{_today_key()}:1:10:00"] = {
            "status": "done",
            "presence_status": {"mode": "custom", "custom_text": "上课中"},
        }
        await harness._ensure_current_detail_presence_status()
        self.assertEqual(["上课中"], harness.custom_calls)
        self.assertEqual("上课中", harness.data["qq_presence_state"]["custom_text"])

    def test_runtime_scene_without_evidence_is_unknown_not_rest(self) -> None:
        now = datetime.now().astimezone()
        resolver = RuntimeSceneResolver(clock=lambda: now)

        self.assertIsNone(resolver.resolve_now([], now=now))


if __name__ == "__main__":
    unittest.main()
