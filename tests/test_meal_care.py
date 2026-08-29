# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


class _MealCareHarness(ProactiveMixin, ProactiveEngineMixin, DailyStateMixin):
    def __init__(self) -> None:
        self.enable_meal_care_proactive = True
        self.enable_food_menu_recommendation = True
        self.meal_care_max_daily = 2
        self.meal_care_min_interval_hours = 48
        self.meal_care_followup_minutes = 45
        self.data = {
            "food_menu": {
                "items": [
                    {
                        "id": "food-1",
                        "name": "番茄鸡蛋面",
                        "type": "dish",
                        "category": "面食",
                        "tags": ["热乎"],
                        "times": ["lunch"],
                        "aliases": [],
                        "avoid": [],
                        "use_count": 0,
                    }
                ]
            }
        }
        self.saved = 0

    def _private_user_role(self, user, _user_id="") -> str:
        return str(user.get("relationship_role") or "owner")

    def _environment_fromtimestamp(self, value: float) -> datetime:
        return datetime.fromtimestamp(value)

    def _environment_now(self) -> datetime:
        return datetime.now()

    def _normalize_internal_motive_text(self, value) -> str:
        return str(value or "").strip()

    def _window_from_delay_minutes(self, delay_minutes: int, *, width_minutes: int = 18) -> str:
        return f"delay-{delay_minutes}-{width_minutes}"

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1

    def _normalize_legacy_proactive_text(self, value, *, limit: int = 40) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _clear_planned_proactive_trigger(user: dict) -> None:
        user["planned_proactive_trigger_message_id"] = ""

    @staticmethod
    def _active_user(*, stage: str = "awaiting_status") -> dict:
        now = datetime.now().timestamp()
        return {
            "relationship_role": "owner",
            "meal_care_day": datetime.now().strftime("%Y-%m-%d"),
            "meal_care_asked": ["lunch"],
            "meal_care_satisfied": [],
            "meal_check_context": {
                "active": True,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "meal_key": "lunch",
                "meal_label": "午饭",
                "stage": stage,
                "followup_count": 0,
                "followup_due_at": now + 45 * 60,
                "expires_at": now + 4 * 3600,
            },
        }


class MealCareTests(unittest.TestCase):
    def test_breakfast_waits_for_reply_to_morning_greeting(self) -> None:
        harness = _MealCareHarness()
        harness.enable_daily_greetings = True
        now = datetime.now().replace(hour=8, minute=20, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "greeting_sent_day": datetime.now().strftime("%Y-%m-%d"),
            "morning_greeting_sent_at": now - 10 * 60,
            "morning_greeting_reply_at": 0,
        }
        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            event = harness._pick_meal_care_event(user, now=now)
        self.assertIsNotNone(event)
        self.assertEqual(event["context"]["meal_key"], "lunch")

    def test_breakfast_becomes_candidate_after_morning_reply(self) -> None:
        harness = _MealCareHarness()
        harness.enable_daily_greetings = True
        now = datetime.now().replace(hour=8, minute=20, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "greeting_sent_day": datetime.now().strftime("%Y-%m-%d"),
            "morning_greeting_sent_at": now - 10 * 60,
            "morning_greeting_reply_at": now - 2 * 60,
        }
        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", side_effect=lambda low, _high: low):
            event = harness._pick_meal_care_event(user, now=now)
        self.assertIsNotNone(event)
        self.assertEqual(event["context"]["meal_key"], "breakfast")

    def test_lunch_does_not_require_morning_reply(self) -> None:
        harness = _MealCareHarness()
        harness.enable_daily_greetings = True
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "greeting_sent_day": datetime.now().strftime("%Y-%m-%d"),
            "morning_greeting_sent_at": lunch - 4 * 3600,
            "morning_greeting_reply_at": 0,
        }
        event = harness._pick_meal_care_event(user, now=lunch)
        self.assertIsNotNone(event)
        self.assertEqual(event["context"]["meal_key"], "lunch")

    def test_default_daily_limit_stops_after_one_meal(self) -> None:
        harness = _MealCareHarness()
        del harness.meal_care_max_daily
        dinner = datetime.now().replace(hour=18, minute=10, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "meal_care_day": datetime.now().strftime("%Y-%m-%d"),
            "meal_care_asked": ["lunch"],
            "meal_care_satisfied": [],
        }
        self.assertIsNone(harness._pick_meal_care_event(user, now=dinner))

    def test_explicit_daily_limit_two_keeps_second_meal_available(self) -> None:
        harness = _MealCareHarness()
        dinner = datetime.now().replace(hour=18, minute=10, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "meal_care_day": datetime.now().strftime("%Y-%m-%d"),
            "meal_care_asked": ["lunch"],
            "meal_care_satisfied": [],
        }
        event = harness._pick_meal_care_event(user, now=dinner)
        self.assertIsNotNone(event)
        self.assertEqual(event["context"]["meal_key"], "dinner")

    def test_recent_food_topic_blocks_independent_meal_care(self) -> None:
        harness = _MealCareHarness()
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "last_food_prompt_at": lunch - 2 * 3600,
        }
        self.assertIsNone(harness._pick_meal_care_event(user, now=lunch))

    def test_food_topic_cooldown_expires_after_seven_hours(self) -> None:
        harness = _MealCareHarness()
        harness.meal_care_min_interval_hours = 0
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "last_food_prompt_at": lunch - 7 * 3600 - 1,
        }
        event = harness._pick_meal_care_event(user, now=lunch)
        self.assertIsNotNone(event)
        self.assertEqual(event["context"]["meal_key"], "lunch")

    def test_default_cross_day_interval_blocks_daily_meal_check_in(self) -> None:
        harness = _MealCareHarness()
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "last_food_prompt_at": lunch - 24 * 3600,
        }
        self.assertIsNone(harness._pick_meal_care_event(user, now=lunch))

    def test_cross_day_interval_allows_meal_check_in_after_48_hours(self) -> None:
        harness = _MealCareHarness()
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "last_food_prompt_at": lunch - 48 * 3600 - 1,
        }
        event = harness._pick_meal_care_event(user, now=lunch)
        self.assertIsNotNone(event)
        self.assertEqual(event["context"]["meal_key"], "lunch")

    def test_schedules_owner_meal_care_inside_lunch_window(self) -> None:
        harness = _MealCareHarness()
        user = {"relationship_role": "owner"}
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        event = harness._pick_meal_care_event(user, now=lunch)
        self.assertIsNotNone(event)
        self.assertEqual(event["reason"], "meal_care")
        self.assertEqual(event["context"]["meal_key"], "lunch")
        self.assertTrue(event["_daily_meal_care"])

    def test_friend_never_receives_meal_care(self) -> None:
        harness = _MealCareHarness()
        lunch = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        self.assertIsNone(
            harness._pick_meal_care_event({"relationship_role": "friend"}, now=lunch)
        )

    def test_ate_without_detail_uses_current_reply_as_only_followup(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user()
        result = harness._handle_meal_care_inbound(user, "吃了", now=datetime.now().timestamp())
        self.assertEqual(result["kind"], "ate_without_detail")
        self.assertEqual(user["meal_check_context"]["stage"], "awaiting_detail")
        self.assertEqual(user["meal_check_context"]["followup_count"], 1)
        self.assertNotIn("pending_followup_event", user)
        self.assertEqual(harness._meal_reply_food_items("吃了", active_context=True), [])

    def test_specific_food_resolves_question_and_learns_candidate(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user(stage="awaiting_detail")
        result = harness._handle_meal_care_inbound(
            user,
            "吃了牛肉面和一个鸡蛋",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "specific")
        self.assertFalse(user["meal_check_context"]["active"])
        self.assertIn("lunch", user["meal_care_satisfied"])
        names = [item["name"] for item in harness.data["food_menu"]["items"]]
        self.assertIn("牛肉面和一个鸡蛋", names)
        learned = next(item for item in harness.data["food_menu"]["items"] if item["name"] == "牛肉面和一个鸡蛋")
        self.assertEqual(learned["source"], "meal_care_reply")
        self.assertIn("lunch", learned["times"])

    def test_specific_reply_cancels_materialized_followup(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user(stage="awaiting_detail")
        user.update(
            {
                "next_proactive_at": datetime.now().timestamp() + 1800,
                "planned_proactive_reason": "meal_care_followup",
                "proactive_impulses": [
                    {"reason": "meal_care_followup", "state": "queued"},
                    {"reason": "activity_share", "state": "queued"},
                ],
            }
        )
        harness._handle_meal_care_inbound(
            user,
            "牛肉面",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(user["next_proactive_at"], 0)
        self.assertEqual(user["planned_proactive_reason"], "")
        self.assertEqual(
            [item["reason"] for item in user["proactive_impulses"]],
            ["activity_share"],
        )

    def test_bare_food_name_is_understood_in_active_context(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user(stage="awaiting_detail")
        result = harness._handle_meal_care_inbound(
            user,
            "番茄鸡蛋面",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["foods"], ["番茄鸡蛋面"])
        existing = harness.data["food_menu"]["items"][0]
        self.assertEqual(existing["use_count"], 1)
        self.assertFalse(user["meal_check_context"]["active"])

    def test_explicit_meal_can_fill_menu_without_active_question(self) -> None:
        harness = _MealCareHarness()
        user = {
            "relationship_role": "owner",
            "next_proactive_at": datetime.now().timestamp() + 1800,
            "planned_proactive_reason": "meal_care",
            "planned_meal_care_context": {"meal_key": "lunch"},
            "proactive_impulses": [
                {
                    "reason": "meal_care",
                    "state": "queued",
                    "context": {"meal_key": "lunch"},
                },
                {
                    "reason": "meal_care",
                    "state": "queued",
                    "context": {"meal_key": "dinner"},
                },
            ],
        }
        result = harness._handle_meal_care_inbound(
            user,
            "我午饭吃了咖喱鸡饭",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "specific")
        names = [item["name"] for item in harness.data["food_menu"]["items"]]
        self.assertIn("咖喱鸡饭", names)
        self.assertIn("lunch", user["meal_care_satisfied"])
        self.assertEqual(user["next_proactive_at"], 0)
        self.assertEqual(user["planned_proactive_reason"], "")
        self.assertEqual(len(user["proactive_impulses"]), 1)
        self.assertEqual(user["proactive_impulses"][0]["context"]["meal_key"], "dinner")

    def test_non_food_eating_idioms_are_not_learned(self) -> None:
        harness = _MealCareHarness()
        user = {"relationship_role": "owner"}
        for text in ("我吃了个亏", "我吃了官司", "我吃了感冒药"):
            result = harness._handle_meal_care_inbound(
                user,
                text,
                now=datetime.now().timestamp(),
            )
            self.assertEqual(result["kind"], "none")
            self.assertEqual(result["foods"], [])
        names = [item["name"] for item in harness.data["food_menu"]["items"]]
        self.assertNotIn("个亏", names)
        self.assertNotIn("官司", names)

    def test_non_food_eating_idiom_does_not_trigger_detail_followup(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user()
        result = harness._handle_meal_care_inbound(
            user,
            "我吃了个亏",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "unrelated")
        self.assertEqual(result["foods"], [])
        self.assertNotEqual(user["meal_check_context"].get("stage"), "awaiting_detail")

    def test_specific_food_resolves_when_menu_learning_is_disabled(self) -> None:
        harness = _MealCareHarness()
        harness.enable_food_menu_recommendation = False
        user = harness._active_user(stage="awaiting_detail")
        result = harness._handle_meal_care_inbound(
            user,
            "吃了牛肉面",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "specific")
        self.assertEqual(result["foods"], ["牛肉面"])
        self.assertFalse(user["meal_check_context"]["active"])

    def test_historical_meal_does_not_satisfy_today_slot(self) -> None:
        harness = _MealCareHarness()
        user = {"relationship_role": "owner"}
        result = harness._handle_meal_care_inbound(
            user,
            "昨晚我晚饭吃了咖喱鸡饭",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "specific")
        self.assertNotIn("dinner", user.get("meal_care_satisfied", []))

    def test_promoted_meal_plan_keeps_its_meal_context(self) -> None:
        harness = _MealCareHarness()
        harness.enable_daily_greetings = False
        now = datetime.now().replace(hour=12, minute=5, second=0, microsecond=0).timestamp()
        user = {
            "relationship_role": "owner",
            "next_proactive_at": now + 3 * 3600,
            "planned_proactive_source": "event",
        }
        with patch("astrbot_plugin_private_companion.proactive_engine.random.uniform", return_value=now + 120):
            promoted = ProactiveMixin._promote_earlier_daily_greeting_event(harness, user, now=now)
        self.assertTrue(promoted)
        self.assertEqual(user["planned_proactive_reason"], "meal_care")
        self.assertEqual(user["planned_meal_care_context"]["meal_key"], "lunch")

    def test_not_eaten_uses_candidates_instead_of_past_tense_question(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user()
        result = harness._handle_meal_care_inbound(user, "还没吃呢", now=datetime.now().timestamp())
        self.assertEqual(result["kind"], "not_eaten")
        prompt = harness._format_meal_care_reply_context(user, "还没吃呢")
        self.assertIn("不要追问“吃了什么”", prompt)
        menu = harness._format_food_menu_for_reply("还没吃呢", user=user)
        self.assertIn("番茄鸡蛋面", menu)

    def test_unrelated_short_reply_is_not_learned_as_food(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user()
        result = harness._handle_meal_care_inbound(
            user,
            "我在忙",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "unrelated")
        self.assertEqual(result["foods"], [])
        names = [item["name"] for item in harness.data["food_menu"]["items"]]
        self.assertNotIn("我在忙", names)
        self.assertFalse(user["meal_check_context"]["active"])
        self.assertEqual(user["meal_check_context"]["stage"], "closed_unrelated")
        self.assertNotIn("pending_followup_event", user)

    def test_unrelated_reply_cancels_materialized_meal_followup(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user()
        user.update(
            {
                "pending_followup_event": {
                    "reason": "meal_care_followup",
                    "_meal_care_followup": True,
                },
                "next_proactive_at": datetime.now().timestamp() + 1800,
                "planned_proactive_reason": "meal_care_followup",
                "proactive_impulses": [
                    {"reason": "meal_care_followup", "state": "queued"},
                    {"reason": "activity_share", "state": "queued"},
                ],
            }
        )

        result = harness._handle_meal_care_inbound(
            user,
            "先别问啦，我在赶东西",
            now=datetime.now().timestamp(),
        )

        self.assertEqual(result["kind"], "unrelated")
        self.assertEqual(user["pending_followup_event"], {})
        self.assertEqual(user["next_proactive_at"], 0)
        self.assertEqual(user["planned_proactive_reason"], "")
        self.assertEqual(
            [item["reason"] for item in user["proactive_impulses"]],
            ["activity_share"],
        )

    def test_followup_count_blocks_a_second_followup(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user(stage="awaiting_detail")
        user["meal_check_context"]["followup_count"] = 1
        self.assertIsNone(
            harness._meal_care_followup_event(user, now=datetime.now().timestamp())
        )

    def test_newer_food_prompt_cancels_stale_meal_followup(self) -> None:
        harness = _MealCareHarness()
        now = datetime.now().timestamp()
        user = harness._active_user()
        user["meal_check_context"].update(
            {
                "asked_at": now - 3600,
                "followup_due_at": now - 60,
            }
        )
        user["last_food_prompt_at"] = now - 300
        user["pending_followup_event"] = {
            "reason": "meal_care_followup",
            "_meal_care_followup": True,
            "_scheduled_ts": now - 60,
        }

        self.assertIsNone(harness._pick_pending_followup_event(user, now=now))
        self.assertEqual(user["pending_followup_event"], {})
        self.assertFalse(user["meal_check_context"]["active"])
        self.assertEqual(
            user["meal_check_context"]["stage"],
            "closed_newer_food_prompt",
        )

    def test_vague_reply_after_followup_stops_asking(self) -> None:
        harness = _MealCareHarness()
        user = harness._active_user(stage="awaiting_detail")
        user["meal_check_context"]["followup_count"] = 1
        result = harness._handle_meal_care_inbound(
            user,
            "吃了",
            now=datetime.now().timestamp(),
        )
        self.assertEqual(result["kind"], "ate_without_detail_final")
        self.assertFalse(user["meal_check_context"]["active"])
        self.assertNotIn("pending_followup_event", user)
        prompt = harness._format_meal_care_reply_context(user, "吃了")
        self.assertIn("不要第三次追问", prompt)


if __name__ == "__main__":
    unittest.main()
