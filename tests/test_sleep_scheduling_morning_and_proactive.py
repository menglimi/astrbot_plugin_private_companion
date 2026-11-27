"""Regression tests for sleep scheduling boundaries, morning rhythm wake receipts, and proactive prompt polish."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.host.tick_offload import advance_life_runtime_async
from companion_v2.proactive.morning_rhythm import (
    manual_wake_window,
    morning_plan,
    role_wake_at,
)
from companion_v2.proactive.prompts import INSTRUCTION
from companion_v2.proactive.wake_events import WakeContactEvents
from companion_v2.role_work.life.scheduling import (
    bedtime_at,
    next_bedtime_occurrence,
    project_sleep_occurrence,
)
from tests.multi_host_support import multi_config
from tests.multi_life_support import MultiLifeFixture
from tests.test_proactive_morning_chain import FakePreferences, FakeRuntime, values as morning_values


class SleepSchedulingAuditTests(MultiLifeFixture, unittest.IsolatedAsyncioTestCase):
    def shared_role_config(self):
        config = multi_config()
        config["multi_host"]["targets"][1]["bot_id"] = "bot-a"
        config["multi_host"]["personas"] = [
            mapping for mapping in config["multi_host"]["personas"]
            if mapping["bot_id"] == "bot-a"
        ]
        return config

    def test_midnight_crossing_variation_keeps_distinct_schedule_day_keys(self):
        owner = RoleOwnerRef("install-a", "bot-a", "role-a")
        day1 = date(2026, 9, 21)
        day2 = date(2026, 9, 22)

        def fake_variation(_owner, local_day, _max_minutes):
            return 15 if local_day == day1 else -15

        with patch("companion_v2.role_work.life.scheduling.variation_for", side_effect=fake_variation):
            b1 = bedtime_at(owner, day1, "Asia/Shanghai", time(23, 55), 20)
            b2 = bedtime_at(owner, day2, "Asia/Shanghai", time(23, 55), 20)
            # Both actual local datetimes land on 2026-09-22 (00:10 and 23:40).
            self.assertEqual(b1.astimezone(ZoneInfo("Asia/Shanghai")).date(), day2)
            self.assertEqual(b2.astimezone(ZoneInfo("Asia/Shanghai")).date(), day2)

            s1_day, s1_at = next_bedtime_occurrence(
                b1 - timedelta(minutes=5), owner, "Asia/Shanghai", time(23, 55), 20
            )
            s2_day, s2_at = next_bedtime_occurrence(
                b1 + timedelta(microseconds=1), owner, "Asia/Shanghai", time(23, 55), 20, after_day=s1_day
            )
            self.assertEqual((s1_day, s1_at), (day1, b1))
            self.assertEqual((s2_day, s2_at), (day2, b2))
            self.assertNotEqual(s1_day, s2_day)

    async def test_midnight_crossing_consecutive_nights_both_execute_without_dedupe_collision(self):
        await self.initialize(self.shared_role_config())
        await self.enable(
            sleep_enabled=True,
            sleep_time="23:55",
            sleep_timezone="UTC",
            sleep_variation_minutes=20,
            sleep_window_minutes=60,
            sleep_minutes=30,
        )
        day1 = date(2026, 9, 21)
        day2 = date(2026, 9, 22)

        def fake_variation(_owner, local_day, _max_minutes):
            return 15 if local_day == day1 else -15

        with patch("companion_v2.role_work.life.scheduling.variation_for", side_effect=fake_variation):
            app = await self.start()
            # Night 1 (schedule_day=2026-09-21) fires at 2026-09-22 00:10 UTC.
            self.clock.set(datetime(2026, 9, 22, 0, 10, tzinfo=timezone.utc))
            await advance_life_runtime_async(app.life_runtime)
            first_episodes = app.role_state.body_clock.episodes(self.context())
            self.assertEqual(len(first_episodes), 1)
            self.assertEqual(first_episodes[0].work.request_id, "daily-sleep-2026-09-21")

            # Wake after night 1 and drain any post-sleep dream worker before night 2.
            self.clock.set(datetime(2026, 9, 22, 0, 45, tzinfo=timezone.utc))
            app.role_state.body_clock.finish_due(self.context())
            routine = self.routine()
            routine._dreams.add(first_episodes[0].activity_id)

            # Night 2 (schedule_day=2026-09-22) fires at 2026-09-22 23:40 UTC (same UTC date!).
            self.clock.set(datetime(2026, 9, 22, 23, 40, tzinfo=timezone.utc))
            await advance_life_runtime_async(app.life_runtime)
            second_episodes = app.role_state.body_clock.episodes(self.context())
            self.assertEqual(len(second_episodes), 2)
            self.assertEqual(second_episodes[1].work.request_id, "daily-sleep-2026-09-22")

    async def test_manual_night_sleep_covers_tonight_while_short_nap_does_not(self):
        await self.initialize(self.shared_role_config())
        await self.enable(
            sleep_enabled=True,
            sleep_time="02:00",
            sleep_timezone="UTC",
            sleep_variation_minutes=0,
            sleep_window_minutes=90,
            sleep_minutes=420,
        )
        app = await self.start()
        routine = self.routine()
        routine.refresh()
        context = self.context()

        # 1) A short nap (<= 60 min) near bedtime must NOT suppress tonight's scheduled night sleep.
        self.clock.set(datetime(2026, 9, 21, 0, 30, tzinfo=timezone.utc))
        nap = routine.start_sleep("manual-short-nap", 30, sleep_kind="manual")
        self.clock.set(datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc))
        routine.wake(nap.activity_id, expected_revision=nap.revision, operation_id="wake-nap")

        settings = routine.settings
        day_after_nap, due_after_nap = project_sleep_occurrence(
            app.role_state.body_clock, context, routine.owner, settings, self.clock.now()
        )
        self.assertEqual(day_after_nap, date(2026, 9, 21))
        self.assertEqual(due_after_nap, datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc))

        # 2) A manual night sleep (> 60 min) started before bedtime DOES cover tonight's window
        # and must advance strictly to schedule_day + 1 (even while now < due!).
        self.clock.set(datetime(2026, 9, 21, 1, 10, tzinfo=timezone.utc))
        manual_night = routine.start_sleep("manual-night-sleep", 420, sleep_kind="manual")
        self.clock.set(datetime(2026, 9, 21, 1, 50, tzinfo=timezone.utc))
        routine.wake(manual_night.activity_id, expected_revision=manual_night.revision, operation_id="wake-manual-night")
        routine._dreams.update({nap.activity_id, manual_night.activity_id})

        day_after_night, due_after_night = project_sleep_occurrence(
            app.role_state.body_clock, context, routine.owner, settings, self.clock.now()
        )
        self.assertEqual(day_after_night, date(2026, 9, 22))
        self.assertEqual(due_after_night, datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc))

        # Reaching 02:00 must NOT start a second night sleep for 2026-09-21.
        self.clock.set(datetime(2026, 9, 21, 2, 5, tzinfo=timezone.utc))
        await advance_life_runtime_async(app.life_runtime)
        self.assertEqual(app.role_state.body_clock.active(context, "sleep"), ())
        self.assertEqual(routine._next_sleep, datetime(2026, 9, 22, 2, 0, tzinfo=timezone.utc))

    async def test_failed_start_sleep_preserves_next_sleep_until_retry_succeeds(self):
        await self.initialize(self.shared_role_config())
        await self.enable(
            sleep_enabled=True,
            sleep_time="00:05",
            sleep_timezone="UTC",
            sleep_variation_minutes=0,
            sleep_window_minutes=90,
            sleep_minutes=30,
        )
        app = await self.start()
        routine = self.routine()
        routine.refresh()
        expected_due = datetime(2026, 9, 21, 0, 5, tzinfo=timezone.utc)
        self.assertEqual(routine._next_sleep, expected_due)

        self.clock.set(datetime(2026, 9, 21, 0, 10, tzinfo=timezone.utc))
        with patch.object(routine, "start_sleep", side_effect=TaskError("life_reading_active", "busy reading")):
            with self.assertRaises(TaskError):
                routine._start_due_night_sleep(self.clock.now())
        # _next_sleep must NOT have advanced when start_sleep failed.
        self.assertEqual(routine._next_sleep, expected_due)

        # Retry succeeds once the transient blocker clears.
        started = routine._start_due_night_sleep(self.clock.now())
        self.assertTrue(started)
        self.assertEqual(routine._next_sleep, datetime(2026, 9, 22, 0, 5, tzinfo=timezone.utc))

    async def test_sleep_schedule_status_matches_open_window_when_no_executor_bound(self):
        await self.initialize(self.shared_role_config())
        await self.enable(
            sleep_enabled=True,
            sleep_time="00:01",
            sleep_timezone="UTC",
            sleep_variation_minutes=0,
            sleep_window_minutes=120,
            sleep_minutes=60,
        )
        app = await self.start()
        routine = self.routine()
        self.clock.set(datetime(2026, 9, 21, 0, 30, tzinfo=timezone.utc))
        with patch.object(app.life_runtime, "_select_sleep_executor", return_value=(None, None)):
            status = (await self.status())["sleep_schedule"]
        # Even without an active executor, status must report the open window's due time, not tomorrow.
        self.assertEqual(status["next_sleep_at"], datetime(2026, 9, 21, 0, 1, tzinfo=timezone.utc).isoformat())


class MorningRhythmAndWakeTimeTests(unittest.TestCase):
    def test_role_wake_at_prefers_sleep_execution_ended_at_and_falls_back_to_legacy_body_sleep(self):
        ended = "2026-10-12T08:15:00+08:00"
        legacy = "2026-10-12T06:40:00+08:00"
        state = {
            "sleep_execution": {"state": "awake", "ended_at": ended},
            "body_sleep": {"state": "awake", "last_completed": {"completed_at": legacy}},
        }
        self.assertEqual(role_wake_at(state), datetime(2026, 10, 12, 0, 15, tzinfo=timezone.utc))

        # Direct sleep_execution dict also works.
        self.assertEqual(
            role_wake_at({"state": "awake", "ended_at": "2026-10-12T00:15:00Z"}),
            datetime(2026, 10, 12, 0, 15, tzinfo=timezone.utc),
        )
        # Legacy body_sleep fallback remains intact.
        self.assertEqual(
            role_wake_at({"body_sleep": {"state": "awake", "last_completed": {"completed_at": legacy}}}),
            datetime(2026, 10, 11, 22, 40, tzinfo=timezone.utc),
        )
        # Active sleep in sleep_execution must not fall back to stale legacy body_sleep.
        self.assertIsNone(
            role_wake_at({
                "sleep_execution": {"state": "sleeping", "ended_at": None},
                "body_sleep": {"state": "awake", "last_completed": {"completed_at": legacy}},
            })
        )

    def test_disabled_routine_learning_uses_manual_morning_window_and_sleep_execution_receipt(self):
        zone = ZoneInfo("Asia/Shanghai")
        at = datetime(2026, 10, 12, 9, 45, tzinfo=zone)
        role_woke = datetime(2026, 10, 12, 9, 10, tzinfo=zone)

        class SleepExecutionState:
            def read(self, _context, at=None):
                return {"sleep_execution": {"state": "awake", "ended_at": role_woke.isoformat()}}

        prefs = FakePreferences(morning_values(
            routine_learning_enabled=False,
            wake_morning_start="09:00",
            wake_morning_end="11:30",
        ))
        contacts = NS(
            runtime=FakeRuntime(),
            task_id=lambda _context, opportunity: "task-" + opportunity.opportunity_id,
            preferences=NS(read=lambda _context, observed_at: NS(is_success=True, failure=None, unwrap=lambda: prefs)),
            history=NS(interaction=lambda _context, _at, action=None: None),
        )
        events = WakeContactEvents(contacts, SleepExecutionState(), lambda _context: {})
        context = NS(key="ctx", scope=NS(key="scope"), actor=NS(principal=NS(native_account_id="200")))

        # At 09:45 (well past the old hardcoded 07:00 default window!), manual window 09:00-11:30 triggers.
        candidate = events.select(context, prefs, at)
        self.assertIsNotNone(candidate, events.last_diagnostic)
        _, _, marker = candidate
        self.assertEqual(marker["action"], "role_morning")
        self.assertEqual(marker["source"], "manual")
        self.assertEqual(
            datetime.fromisoformat(marker["window_start"]).astimezone(zone),
            datetime(2026, 10, 12, 9, 0, tzinfo=zone),
        )
        self.assertEqual(
            datetime.fromisoformat(marker["window_end"]).astimezone(zone),
            datetime(2026, 10, 12, 11, 30, tzinfo=zone),
        )
        # Validate must accept the exact same manual window and sleep_execution wake receipt.
        self.assertTrue(events.validate(context, marker, at))

        # Before manual start (08:30) must be outside_wake_window.
        early = datetime(2026, 10, 12, 8, 30, tzinfo=zone)
        self.assertIsNone(events.select(context, prefs, early))
        self.assertEqual(events.last_diagnostic["reason"], "outside_wake_window")

        # Manual window respects real role wake time grace (>180 min after waking -> skip).
        late_at = datetime(2026, 10, 12, 11, 20, tzinfo=zone)
        old_woke = datetime(2026, 10, 12, 7, 30, tzinfo=zone)
        win = manual_wake_window(at=late_at, zone="Asia/Shanghai", morning_start_minutes=9 * 60, morning_end_minutes=11 * 60 + 30)
        step = morning_plan(
            None,
            at=late_at,
            zone="Asia/Shanghai",
            role_woke_at=old_woke,
            morning_start_minutes=9 * 60,
            morning_end_minutes=11 * 60 + 30,
            window=win,
        )
        self.assertEqual((step.action, step.reason), ("skip", "role_wake_window_elapsed"))


class ProactiveBoilerplatePolishTests(unittest.TestCase):
    def test_prompts_and_scheduling_avoid_mechanical_no_rush_reply_disclaimers(self):
        self.assertIn("不用急着回", INSTRUCTION)
        self.assertIn("免责套话", INSTRUCTION)
        self.assertNotIn("不把回复当成义务，不给回应制造压力", INSTRUCTION)

        from pathlib import Path
        scheduling_src = (
            Path(__file__).resolve().parent.parent / "companion_v2" / "proactive" / "scheduling.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("不要求对方立即回复", scheduling_src)
        self.assertNotIn("也不要求对方回应", scheduling_src)
        self.assertNotIn("这是一次低压力的主动联系机会", scheduling_src)


if __name__ == "__main__":
    unittest.main()
