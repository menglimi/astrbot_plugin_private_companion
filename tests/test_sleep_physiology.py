"""Pure checks for role-level sleep pressure, recovery and rest decisions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from companion_v2.role_work.life.physiology import (nap_ready, night_sleep_ready,
    oversleep_extension, sleep_profile, sleep_quality_range)
from companion_v2.role_work.models import RoleActivityState as State, RoleSleepReceipt
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def episode(identifier, *, at, kind="night", state=State.COMPLETED, base=480, target=480,
            slept=480, quality=(84, 94), workload=0, interruptions=0, early=0):
    receipt = RoleSleepReceipt(kind, base, target, slept_seconds=slept * 60,
        workload_score=workload, quality_min=quality[0], quality_max=quality[1],
        interruptions=interruptions, early_interruptions=early)
    return SimpleNamespace(activity_id=identifier, started_at=at, updated_at=at + timedelta(hours=8),
        state=state, sleep=receipt, work=SimpleNamespace(due_at=at + timedelta(minutes=target)))


class SleepPhysiologyTests(unittest.TestCase):
    def test_sleep_debt_accumulates_and_recovers_gradually(self):
        short = episode("short", at=NOW - timedelta(days=2), slept=360)
        full = episode("full", at=NOW - timedelta(days=1), slept=600, quality=(90, 90))
        nap = episode("nap", at=NOW - timedelta(hours=10), kind="nap", base=480, target=25,
                      slept=25, quality=(90, 90))
        first = sleep_profile((short,), workload_score=0, now=NOW)
        second = sleep_profile((short, full), workload_score=0, now=NOW)
        recovered = sleep_profile((short, full, nap), workload_score=0, now=NOW)
        self.assertEqual(first["sleep_debt_minutes"], 120)
        self.assertGreater(second["sleep_debt_minutes"], 0)
        self.assertLess(second["sleep_debt_minutes"], first["sleep_debt_minutes"])
        self.assertLess(recovered["sleep_debt_minutes"], second["sleep_debt_minutes"])

    def test_pressure_tracks_awake_time_debt_and_observed_workload(self):
        last = episode("last", at=NOW - timedelta(hours=8))
        rested = sleep_profile((last,), workload_score=0, now=NOW)
        loaded = sleep_profile((last,), workload_score=80, now=NOW)
        later = sleep_profile((last,), workload_score=0, now=NOW + timedelta(hours=8))
        self.assertGreater(loaded["sleep_pressure"], rested["sleep_pressure"])
        self.assertGreater(later["sleep_pressure"], rested["sleep_pressure"])
        self.assertTrue(night_sleep_ready({"sleep_pressure": 90, "workload_score": 0, "sleep_debt_minutes": 0}))
        self.assertTrue(night_sleep_ready({"sleep_pressure": None}))

    def test_interrupted_sleep_has_lower_quality_and_bounded_lie_in(self):
        rested = sleep_quality_range(10, 0)
        interrupted = sleep_quality_range(10, 100, interruptions=2, early_interruptions=1)
        self.assertLess(interrupted[0], rested[0])
        self.assertLess(interrupted[1], rested[1])
        tired = episode("tired", at=NOW, workload=100, interruptions=2)
        self.assertIn(oversleep_extension(tired, sleep_debt_minutes=200, roll=0), (15, 30))
        self.assertEqual(oversleep_extension(tired, sleep_debt_minutes=200, roll=99), 0)

    def test_nap_requires_daytime_and_sustained_fatigue(self):
        profile = {"sleep_pressure": 82, "sleep_debt_minutes": 120, "workload_score": 50,
                   "awake_minutes": 360, "has_sleep_history": True}
        self.assertTrue(nap_ready(profile, local_minutes=13 * 60))
        self.assertFalse(nap_ready(profile, local_minutes=18 * 60))
        self.assertFalse(nap_ready({**profile, "awake_minutes": 60}, local_minutes=13 * 60))

    def test_afternoon_nap_can_follow_heavy_work_or_significant_sleep_debt(self):
        rested_night = episode("rested", at=NOW - timedelta(hours=16))
        heavy_day = sleep_profile((rested_night,), workload_score=100, now=NOW)
        self.assertTrue(nap_ready(heavy_day, local_minutes=14 * 60))
        self.assertFalse(nap_ready(heavy_day, local_minutes=10 * 60))
        self.assertFalse(nap_ready(heavy_day, local_minutes=16 * 60))

        early_day = sleep_profile((rested_night,), workload_score=100,
                                  now=NOW - timedelta(hours=3))
        self.assertFalse(nap_ready(early_day, local_minutes=13 * 60))

        short_night = episode("short", at=NOW - timedelta(hours=16), slept=390)
        debt_day = sleep_profile((short_night,), workload_score=0, now=NOW)
        self.assertGreaterEqual(debt_day["sleep_debt_minutes"], 90)
        self.assertTrue(nap_ready(debt_day, local_minutes=14 * 60))

    def test_disabled_auto_sleep_does_not_pin_pressure_from_stale_history(self):
        old = episode("old", at=NOW - timedelta(days=3))
        disabled = sleep_profile((old,), workload_score=10, now=NOW, sleep_enabled=False)
        self.assertIsNone(disabled["sleep_pressure"])
        self.assertIsNone(disabled["awake_minutes"])
        self.assertFalse(disabled["has_sleep_history"])
        self.assertEqual(disabled["sleep_debt_minutes"], 0)
        # Active sleep still reports 0 pressure even when auto-sleep is disabled.
        manual_active = episode("active", at=NOW - timedelta(hours=1), state=State.ACTIVE)
        active_profile = sleep_profile((old, manual_active), workload_score=10, now=NOW, sleep_enabled=False)
        self.assertEqual(active_profile["sleep_pressure"], 0)
        self.assertTrue(active_profile["has_sleep_history"])

    def test_zero_workload_sixteen_hours_awake_is_ready_without_daily_drift(self):
        rested_night = episode("rested", at=NOW - timedelta(hours=24), slept=480)
        at_bedtime = sleep_profile((rested_night,), workload_score=0, now=NOW)
        self.assertEqual(at_bedtime["sleep_pressure"], 64)
        self.assertTrue(night_sleep_ready(at_bedtime))

    def test_sleep_due_window_and_schedule_summary_handle_midnight_and_late_bedtime(self):
        from companion_v2.role_work.life.scheduling import sleep_due_window_expired, sleep_schedule_summary
        # Bedtime at 23:55 crossing into 00:10 next day must NOT expire.
        bedtime = datetime(2026, 10, 14, 15, 55, tzinfo=timezone.utc)
        self.assertFalse(sleep_due_window_expired(
            bedtime + timedelta(minutes=15), bedtime, window_minutes=120, sleep_minutes=480))
        # A bedtime missed by 12 hours (e.g. machine offline overnight) expires cleanly.
        self.assertTrue(sleep_due_window_expired(
            bedtime + timedelta(hours=12), bedtime, window_minutes=120, sleep_minutes=480))
        summary = sleep_schedule_summary({
            "sleep_enabled": True, "sleep_time": "04:00",
            "sleep_timezone": "Asia/Shanghai", "sleep_minutes": 480,
        })
        self.assertEqual(summary, {
            "enabled": True, "sleep_time": "04:00",
            "timezone": "Asia/Shanghai", "duration_minutes": 480,
            "expected_wake_time": "12:00",
        })


if __name__ == "__main__":
    unittest.main()
