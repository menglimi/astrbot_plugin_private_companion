from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"
if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PLUGIN_ROOT)]
    package.__package__ = PACKAGE_NAME
    sys.modules[PACKAGE_NAME] = package

from astrbot_plugin_private_companion.scene_context import SceneContextMixin


class _SceneHarness(SceneContextMixin):
    def __init__(self) -> None:
        self.data = {}

    @staticmethod
    def _parse_minutes(value: object) -> int | None:
        parts = str(value or "").split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        hour, minute = (int(part) for part in parts)
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _normalized_plan_item_starts(self, items):
        starts = []
        previous = None
        offset = 0
        for item in items:
            raw = self._parse_minutes(item.get("time")) if isinstance(item, dict) else None
            if raw is not None and previous is not None and raw < previous:
                offset += 24 * 60
            starts.append(None if raw is None else raw + offset)
            if raw is not None:
                previous = raw
        return starts

    def _plan_item_end_minutes(self, start, item, *, next_start=None):
        explicit = self._parse_minutes(item.get("end"))
        if explicit is not None:
            if explicit <= start % (24 * 60):
                explicit += 24 * 60
            return explicit
        return next_start if next_start is not None else start + 60

    @staticmethod
    def _normalize_schedule_lifecycle_status(value):
        return {
            "planned": "planned",
            "active": "active",
            "completed": "completed",
            "changed": "changed",
            "cancelled": "cancelled",
        }.get(str(value or "").lower(), "")

    def _plan_item_runtime_status(self, plan, item, index=-1):
        starts = self._normalized_plan_item_starts(plan.get("items"))
        start = starts[index]
        next_start = next((value for value in starts[index + 1 :] if value is not None), None)
        end = self._plan_item_end_minutes(start, item, next_start=next_start)
        now = 12 * 60
        if now < start:
            return "planned"
        return "completed" if now >= end else "active"

    @staticmethod
    def _minutes_to_hhmm(minutes):
        minutes %= 24 * 60
        return f"{minutes // 60:02d}:{minutes % 60:02d}"


class SceneContextScheduleHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _SceneHarness()
        self.captured = datetime(2026, 7, 29, 12, 0)

    def test_history_contains_started_facts_and_excludes_cancelled_and_future_items(self) -> None:
        plan = {
            "date": "2026-07-29",
            "items": [
                {"time": "08:00", "end": "09:00", "activity": "在学校上课", "mood": "专注", "lifecycle_status": "planned", "message_seed": "secret"},
                {"time": "09:00", "end": "10:00", "activity": "取消的会议", "lifecycle_status": "cancelled"},
                {"time": "10:00", "end": "13:00", "activity": "在咖啡店写作", "mood": "平静", "lifecycle_status": "planned"},
                {"time": "11:00", "end": "12:30", "activity": "临时改为散步", "lifecycle_status": "changed"},
                {"time": "15:00", "end": "16:00", "activity": "未来安排", "lifecycle_status": "changed"},
            ],
        }

        history = self.harness._scene_context_schedule_history(plan, captured=self.captured)

        self.assertEqual([item["activity"] for item in history], ["在学校上课", "在咖啡店写作", "临时改为散步"])
        self.assertEqual([item["status"] for item in history], ["completed", "active", "changed"])
        self.assertNotIn("message_seed", history[0])
        self.assertEqual(set(history[0]), {"time", "end", "status", "activity", "mood"})

        self.harness.data["daily_plan"] = plan
        snapshot = self.harness._build_companion_scene_snapshot(now=self.captured)
        self.assertEqual(snapshot["schedule"]["history"], history)

    def test_history_requires_today_and_limits_valid_items_to_24(self) -> None:
        yesterday = {"date": "2026-07-28", "items": [{"time": "08:00", "activity": "昨天"}]}
        self.assertEqual(
            self.harness._scene_context_schedule_history(yesterday, captured=self.captured),
            [],
        )

        items = [
            {"time": "00:00", "activity": f"取消 {index}", "lifecycle_status": "cancelled"}
            for index in range(5)
        ]
        items.extend(
            {
                "time": f"{index // 60:02d}:{index % 60:02d}",
                "end": f"{(index + 1) // 60:02d}:{(index + 1) % 60:02d}",
                "activity": f"有效 {index}",
                "lifecycle_status": "completed",
            }
            for index in range(30)
        )
        history = self.harness._scene_context_schedule_history(
            {"date": "2026-07-29", "items": items},
            captured=self.captured,
        )

        self.assertEqual(len(history), 24)
        self.assertEqual(history[0]["activity"], "有效 0")
        self.assertEqual(history[-1]["activity"], "有效 23")
        self.assertEqual([item["time"] for item in history], sorted(item["time"] for item in history))

    def test_midnight_rollover_entries_do_not_crash_or_become_today_facts(self) -> None:
        plan = {
            "date": "2026-07-29",
            "items": [
                {"time": "23:30", "end": "01:00", "activity": "跨午夜工作", "lifecycle_status": "planned"},
                {"time": "01:00", "end": "02:00", "activity": "次日休息", "lifecycle_status": "planned"},
            ],
        }

        history = self.harness._scene_context_schedule_history(
            plan,
            captured=datetime(2026, 7, 29, 1, 30),
        )

        self.assertEqual(history, [])

    def test_history_fields_are_bounded(self) -> None:
        history = self.harness._scene_context_schedule_history(
            {
                "date": "2026-07-29",
                "items": [
                    {
                        "time": "08:00",
                        "end": "09:00",
                        "activity": "活" * 300,
                        "mood": "静" * 100,
                        "lifecycle_status": "completed",
                    }
                ],
            },
            captured=self.captured,
        )

        self.assertLessEqual(len(history[0]["activity"]), 160)
        self.assertLessEqual(len(history[0]["mood"]), 32)


if __name__ == "__main__":
    unittest.main()
