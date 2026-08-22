# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class MorningGreetingHarness(UserMemoryMixin, ProactiveMixin, ProactiveEngineMixin, DailyStateMixin):
    enable_daily_greetings = True
    enable_meal_care_proactive = False
    default_nickname = "用户"
    quiet_hours = "23:00-06:00"

    def __init__(self) -> None:
        self.data = {
            "daily_plan": {
                "date": "2026-07-12",
                "items": [
                    {
                        "time": "23:30",
                        "end": "07:35",
                        "activity": "夜里进入睡眠",
                        "mood": "安静",
                    },
                    {
                        "time": "07:35",
                        "end": "08:20",
                        "activity": "起床洗漱",
                        "mood": "刚醒",
                    },
                ],
            }
        }

    def _is_plan_date_active(self, _value) -> bool:
        return True

    @staticmethod
    def _environment_fromtimestamp(value: float) -> datetime:
        return datetime.fromtimestamp(value)

    @staticmethod
    def _latest_private_user_activity_ts(user) -> float:
        return float(user.get("last_activity_at") or 0)

    @staticmethod
    def _private_user_role(_user, _user_id="") -> str:
        return "owner"

    @staticmethod
    def _friend_proactive_scheduled_too_early(_user, _scheduled) -> bool:
        return False

    @staticmethod
    def _effective_user_greeting_idle_minutes(_user) -> int:
        return 30

    @staticmethod
    def _effective_user_idle_minutes(_user) -> int:
        return 30

    def _reset_daily_counter_if_needed(self, user) -> None:
        user.setdefault("greetings_sent", [])
        user.setdefault("greetings_suppressed_by_inbound", [])
        user.setdefault("sent_today", 0)

    @staticmethod
    def _choose_proactive_motive(_reason, _user, **_kwargs) -> str:
        return "刚醒来想说声早安"

    @staticmethod
    def _planned_proactive_semantics(_user) -> dict:
        return {"kind": "greeting", "anchor_type": "time_ritual", "score": 0.8, "note": "起床问候"}

    @staticmethod
    def _clear_planned_proactive_trigger(user) -> None:
        user["planned_proactive_trigger_message_id"] = ""


class MorningGreetingScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = MorningGreetingHarness()

    @staticmethod
    def _timestamp(hour: int, minute: int) -> float:
        return datetime(2026, 7, 12, hour, minute).timestamp()

    def test_cross_midnight_sleep_end_drives_morning_window(self) -> None:
        self.assertEqual(self.harness._daily_plan_morning_wake_minutes(), 7 * 60 + 35)
        self.assertEqual(self.harness._morning_greeting_window(), (7 * 60 + 38, 8 * 60 + 25))
        self.assertEqual(self.harness._reason_windows("morning_greeting"), [(7 * 60 + 38, 8 * 60 + 25)])

    def test_inbound_after_morning_greeting_records_reply(self) -> None:
        sent_at = self._timestamp(8, 0)
        user = {
            "morning_greeting_sent_at": sent_at,
            "morning_greeting_reply_at": 0,
        }
        changed = self.harness._note_morning_greeting_reply(user, now=sent_at + 60)
        self.assertTrue(changed)
        self.assertEqual(user["morning_greeting_reply_at"], sent_at + 60)
        self.assertFalse(self.harness._note_morning_greeting_reply(user, now=sent_at + 120))

    def test_morning_text_guard_removes_meal_question_but_keeps_greeting(self) -> None:
        cleaned = ProactiveMessageMixin._strip_morning_meal_questions(
            "嗯，早安。\n早餐吃了吗？"
        )
        self.assertEqual(cleaned, "嗯，早安。")

    def test_morning_text_guard_trims_inline_meal_question(self) -> None:
        cleaned = ProactiveMessageMixin._strip_morning_meal_questions(
            "早安呀，早餐想吃什么？"
        )
        self.assertEqual(cleaned, "早安呀")

    def test_waking_item_start_is_used_when_sleep_segment_is_missing(self) -> None:
        self.harness.data["daily_plan"]["items"] = [
            {"time": "08:20", "end": "09:00", "activity": "起床收拾", "mood": "刚醒"},
            {"time": "09:00", "end": "10:00", "activity": "整理房间", "mood": "平稳"},
        ]

        self.assertEqual(self.harness._daily_plan_morning_wake_minutes(), 8 * 60 + 20)
        self.assertEqual(self.harness._morning_greeting_window(), (8 * 60 + 23, 9 * 60 + 10))

    def test_invalid_schedule_uses_legacy_safe_window(self) -> None:
        self.harness.data["daily_plan"]["items"] = [{"time": "not-a-time", "activity": "普通活动"}]

        self.assertIsNone(self.harness._daily_plan_morning_wake_minutes())
        self.assertEqual(self.harness._morning_greeting_window(), (7 * 60 + 45, 10 * 60 + 20))

    def test_activity_suppresses_matching_morning_and_noon_windows(self) -> None:
        user = {}

        changed_morning = self.harness._mark_greetings_satisfied_by_recent_activity(
            user, activity_ts=self._timestamp(7, 50)
        )
        changed_noon = self.harness._mark_greetings_satisfied_by_recent_activity(
            user, activity_ts=self._timestamp(12, 15)
        )

        self.assertTrue(changed_morning)
        self.assertTrue(changed_noon)
        self.assertIn("morning_greeting", user["greetings_suppressed_by_inbound"])
        self.assertIn("noon_greeting", user["greetings_suppressed_by_inbound"])

    def test_recent_activity_removes_daily_wakeup_candidate(self) -> None:
        now = self._timestamp(7, 55)
        user = {
            "last_activity_at": self._timestamp(7, 30),
            "greetings_sent": ["noon_greeting", "evening_greeting"],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 0,
        }

        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            event = self.harness._pick_daily_greeting_event(user, now=now)

        self.assertIsNone(event)
        self.assertIn("morning_greeting", user["greetings_suppressed_by_inbound"])

    def test_activity_before_wakeup_window_keeps_daily_wakeup_candidate(self) -> None:
        now = self._timestamp(7, 36)
        user = {
            "last_activity_at": self._timestamp(6, 0),
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 0,
        }

        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            event = self.harness._pick_daily_greeting_event(user, now=now)

        self.assertIsNotNone(event)
        self.assertEqual(event["reason"], "morning_greeting")

    def test_same_clock_time_from_previous_day_does_not_suppress_greeting(self) -> None:
        now = self._timestamp(7, 55)
        user = {
            "last_activity_at": now - 24 * 3600,
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 0,
        }

        self.assertFalse(
            self.harness._recent_activity_satisfies_greeting(
                user,
                "morning_greeting",
                now=now,
            )
        )

    def test_other_proactive_messages_do_not_consume_morning_greeting(self) -> None:
        now = self._timestamp(7, 36)
        user = {
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 2,
        }

        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            event = self.harness._pick_daily_greeting_event(user, now=now)

        self.assertIsNotNone(event)
        self.assertEqual(event["reason"], "morning_greeting")

    def test_morning_candidate_prefers_early_part_of_wakeup_window(self) -> None:
        now = self._timestamp(7, 36)
        user = {
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 0,
        }

        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda _low, high: high):
            event = self.harness._pick_daily_greeting_event(user, now=now)

        self.assertIsNotNone(event)
        self.assertLessEqual(event["_scheduled_ts"], self._timestamp(7, 56))

    def test_story_morning_candidate_is_aligned_with_daily_wakeup(self) -> None:
        now = self._timestamp(7, 0)
        candidate = {
            "window": "06:10-07:10",
            "reason": "morning_greeting",
            "action": "message",
            "_scheduled_ts": self._timestamp(6, 30),
        }

        prepared, reason = self.harness._prepare_proactive_candidate_window(
            candidate,
            reason="morning_greeting",
            source="story",
            now=now,
        )

        self.assertEqual(reason, "")
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared["window_start_at"], self._timestamp(7, 38))
        self.assertGreaterEqual(prepared["preferred_ts"], self._timestamp(7, 38))
        self.assertLessEqual(prepared["best_until_at"], self._timestamp(8, 25))

    def test_state_linked_morning_event_uses_dynamic_wakeup_window(self) -> None:
        self.harness.data["daily_state"] = {
            "sleep": "睡眠平稳",
            "energy": 85,
            "conditions": [],
        }

        with patch("astrbot_plugin_private_companion.daily_state.random.random", return_value=0.0):
            events = self.harness._generate_morning_linked_proactive_events()

        self.assertTrue(events)
        for event in events:
            start, end = self.harness._parse_window_minutes(event["window"])
            self.assertGreaterEqual(start, 7 * 60 + 38)
            self.assertLessEqual(end, 8 * 60 + 25)

    def test_morning_impulse_has_priority_over_routine_quiet_care(self) -> None:
        morning = {"source": "daily_greeting", "reason": "morning_greeting"}
        quiet = {"source": "state", "reason": "quiet_care"}

        self.assertGreater(
            self.harness._proactive_impulse_orchestration_priority(morning),
            self.harness._proactive_impulse_orchestration_priority(quiet),
        )

    def test_other_proactive_messages_do_not_trigger_textual_morning_duplicate(self) -> None:
        user = {
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 2,
        }

        reason = self.harness._textual_greeting_duplicate_reason(
            user,
            "早呀，刚洗漱完。",
            now=self._timestamp(8, 0),
        )

        self.assertEqual(reason, "")

    def test_textual_morning_greeting_records_sent_timestamp(self) -> None:
        sent_at = self._timestamp(8, 0)
        user = {
            "greetings_sent": [],
            "morning_greeting_sent_at": 0,
            "morning_greeting_reply_at": 123,
        }

        changed = self.harness._mark_textual_greeting_sent(
            user,
            "测试用户，早上好。",
            sent_at=sent_at,
        )

        self.assertTrue(changed)
        self.assertIn("morning_greeting", user["greetings_sent"])
        self.assertEqual(user["morning_greeting_sent_at"], sent_at)
        self.assertEqual(user["morning_greeting_reply_at"], 0)

    def test_existing_textual_morning_state_repairs_missing_timestamp(self) -> None:
        sent_at = self._timestamp(8, 0)
        user = {
            "greetings_sent": ["morning_greeting"],
            "morning_greeting_sent_at": 0,
        }

        changed = self.harness._mark_textual_greeting_sent(user, "早呀。", sent_at=sent_at)

        self.assertTrue(changed)
        self.assertEqual(user["morning_greeting_sent_at"], sent_at)

    def test_inbound_cancellation_removes_all_conflicting_greetings(self) -> None:
        now = self._timestamp(7, 50)
        wakeup = {
            "planned_proactive_reason": "morning_greeting",
            "planned_proactive_source": "daily_greeting",
            "next_proactive_at": now + 300,
        }
        followup = {
            "planned_proactive_reason": "morning_greeting",
            "planned_proactive_source": "pending_followup",
            "next_proactive_at": now + 300,
        }

        self.assertTrue(self.harness._cancel_inbound_conflicting_greeting(wakeup, now=now))
        self.assertEqual(wakeup["next_proactive_at"], 0)
        self.assertTrue(self.harness._cancel_inbound_conflicting_greeting(followup, now=now))
        self.assertEqual(followup["next_proactive_at"], 0)

    def test_final_recent_chat_guard_blocks_wakeup_and_followup_greetings(self) -> None:
        now = self._timestamp(7, 50)
        wakeup = {
            "planned_proactive_reason": "morning_greeting",
            "planned_proactive_source": "daily_greeting",
            "last_activity_at": now - 27 * 60,
        }
        followup = dict(wakeup, planned_proactive_source="pending_followup")

        self.assertTrue(self.harness._recent_chat_proactive_guard_reason(wakeup, now=now))
        self.assertTrue(self.harness._recent_chat_proactive_guard_reason(followup, now=now))

    def test_promoted_wakeup_candidate_keeps_daily_greeting_source(self) -> None:
        now = self._timestamp(7, 36)
        user = {
            "next_proactive_at": now + 3 * 3600,
            "planned_proactive_source": "random",
            "planned_candidate_id": "old-random-candidate",
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 0,
        }

        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            promoted = self.harness._promote_earlier_daily_greeting_event(user, now=now)

        self.assertTrue(promoted)
        self.assertEqual(user["planned_proactive_reason"], "morning_greeting")
        self.assertEqual(user["planned_proactive_source"], "daily_greeting")
        self.assertEqual(user["planned_candidate_id"], "")

    def test_quiet_hours_covering_wakeup_window_prevent_direct_promotion(self) -> None:
        self.harness.quiet_hours = "23:00-10:30"
        now = self._timestamp(7, 36)
        original_next = now + 3 * 3600
        user = {
            "next_proactive_at": original_next,
            "planned_proactive_source": "random",
            "greetings_sent": [],
            "greetings_suppressed_by_inbound": [],
            "sent_today": 0,
        }

        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            promoted = self.harness._promote_earlier_daily_greeting_event(user, now=now)

        self.assertFalse(promoted)
        self.assertEqual(user["next_proactive_at"], original_next)


if __name__ == "__main__":
    unittest.main()
