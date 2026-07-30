from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _DiarySchedulerHarness(DailyStateMixin):
    def __init__(self) -> None:
        self.enable_daily_diary = True
        self.daily_diary_time = "23:10"
        self.data: dict[str, object] = {}

    @staticmethod
    def _environment_fromtimestamp(timestamp: float) -> datetime:
        return datetime.fromtimestamp(timestamp, timezone.utc)


class _SchedulerTimeoutHarness:
    check_interval_seconds = 60
    enable_detail_enhancement = False
    data = {"users": {}}

    @staticmethod
    def _next_detail_due_in_seconds(_now: float) -> None:
        return None

    @staticmethod
    def _next_memo_due_in_seconds(_now: float) -> None:
        return None

    @staticmethod
    def _next_daily_diary_due_in_seconds(_now: float) -> float:
        return 15.0


class DailyDiarySchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _DiarySchedulerHarness()
        self.now = datetime(2026, 7, 28, 23, 9, 45, tzinfo=timezone.utc).timestamp()

    def test_reports_exact_wait_until_configured_diary_time(self) -> None:
        self.assertEqual(15.0, self.harness._next_daily_diary_due_in_seconds(self.now))

    def test_due_diary_wakes_immediately_after_configured_time(self) -> None:
        after_due = datetime(2026, 7, 28, 23, 10, 5, tzinfo=timezone.utc).timestamp()
        self.assertEqual(0.0, self.harness._next_daily_diary_due_in_seconds(after_due))

    def test_generated_diary_does_not_request_another_wakeup(self) -> None:
        self.harness.data["diary_generated_day"] = "2026-07-28"
        self.assertIsNone(self.harness._next_daily_diary_due_in_seconds(self.now))

    def test_failed_diary_waits_for_existing_retry_cooldown(self) -> None:
        failed_at = datetime(2026, 7, 28, 23, 10, 0, tzinfo=timezone.utc).timestamp()
        self.harness.data.update(
            {
                "daily_diary_failed_day": "2026-07-28",
                "daily_diary_failed_at": failed_at,
            }
        )
        after_failure = failed_at + 5 * 60
        self.assertEqual(25 * 60, self.harness._next_daily_diary_due_in_seconds(after_failure))

    def test_scheduler_uses_diary_deadline_instead_of_base_interval(self) -> None:
        with patch("astrbot_plugin_private_companion.proactive.random.uniform", return_value=1.0):
            timeout = ProactiveMixin._next_scheduler_timeout(_SchedulerTimeoutHarness())
        self.assertEqual(16.0, timeout)


if __name__ == "__main__":
    unittest.main()
