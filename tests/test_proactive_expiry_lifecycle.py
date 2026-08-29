# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


TZ = ZoneInfo("Asia/Shanghai")


class _LifecycleHarness(ProactiveMixin, ProactiveEngineMixin):
    def __init__(self) -> None:
        self.quiet_hours = ""
        self.environment_perception_timezone = "Asia/Shanghai"
        self.data = {"users": {}, "proactive_candidate_pool": []}

    @staticmethod
    def _environment_fromtimestamp(value: float) -> datetime:
        return datetime.fromtimestamp(value, TZ)

    @staticmethod
    def _private_user_role(_user, _user_id="") -> str:
        return "owner"

    @staticmethod
    def _proactive_generation_disabled(_user=None) -> bool:
        return False

    @staticmethod
    def _proactive_topic_signature(*parts) -> str:
        return "|".join(str(part or "").strip().lower() for part in parts)

    @staticmethod
    def _topic_signature_similar(left: str, right: str) -> bool:
        return left == right

    @staticmethod
    def _is_plan_date_active(_value) -> bool:
        return True

    @staticmethod
    def _unverified_social_relay_plan_reason(*_args, **_kwargs) -> str:
        return ""


class ProactiveExpiryLifecycleTests(unittest.TestCase):
    def test_only_short_lived_events_receive_timeliness_relaxation(self) -> None:
        harness = _LifecycleHarness()

        self.assertEqual(
            harness._proactive_timeliness_level(reason="weather_alert", source="weather_alert"),
            "urgent",
        )
        self.assertEqual(
            harness._proactive_timeliness_level(reason="health_alert", source="body_monitor"),
            "urgent",
        )
        self.assertEqual(
            harness._proactive_timeliness_level(reason="environment_change", source="environment_change"),
            "timely",
        )
        self.assertEqual(
            harness._proactive_timeliness_level(reason="memo_note_reminder", source="memo_note"),
            "timely",
        )
        self.assertEqual(
            harness._proactive_timeliness_level(reason="activity_share", source="story"),
            "routine",
        )

    def test_body_monitor_and_memo_reminders_are_short_lived_delivery_items(self) -> None:
        harness = _LifecycleHarness()

        self.assertEqual(
            harness._proactive_item_freshness_class(
                action="message",
                reason="health_alert",
                source="body_monitor",
            ),
            "immediate",
        )
        self.assertEqual(
            harness._proactive_item_freshness_class(
                action="message",
                reason="memo_note_reminder",
                source="memo_note",
            ),
            "immediate",
        )

    def test_expired_queued_impulse_is_blocked_immediately(self) -> None:
        harness = _LifecycleHarness()
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        user = {
            "proactive_impulses": [
                {
                    "id": "expired-1",
                    "state": "queued",
                    "created_ts": now - 3600,
                    "updated_ts": now - 3600,
                    "window_start_at": now - 1800,
                    "expire_at": now - 1,
                }
            ]
        }

        impulses = harness._cleanup_proactive_impulses(user, now=now)

        self.assertEqual(impulses[0]["state"], "blocked")
        self.assertEqual(impulses[0]["last_note"], "潜在念头窗口已过期")

    def test_expired_impulse_cannot_enter_future_materialization(self) -> None:
        harness = _LifecycleHarness()
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        user = {
            "user_id": "10001",
            "proactive_impulses": [
                {
                    "id": "expired-future-1",
                    "state": "deferred",
                    "created_ts": now - 3600,
                    "updated_ts": now - 60,
                    "window_start_at": now + 300,
                    "preferred_ts": now + 300,
                    "best_until_at": now - 30,
                    "expire_at": now - 1,
                }
            ],
        }

        self.assertFalse(harness._materialize_best_proactive_impulse(user, now=now))
        self.assertEqual(user["proactive_impulses"][0]["state"], "blocked")
        self.assertEqual(harness.data["proactive_candidate_pool"], [])

    def test_invalid_legacy_impulse_window_is_blocked_instead_of_stalling_pool(self) -> None:
        harness = _LifecycleHarness()
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        user = {
            "proactive_impulses": [
                {
                    "id": "legacy-invalid-1",
                    "state": "queued",
                    "created_ts": now - 60,
                    "window_start_at": 0,
                    "expire_at": 0,
                }
            ]
        }

        impulses = harness._cleanup_proactive_impulses(user, now=now)

        self.assertEqual(impulses[0]["state"], "blocked")
        self.assertEqual(impulses[0]["last_note"], "潜在念头时间窗口无效")

    def test_deferred_status_keeps_candidate_and_impulse_nonterminal(self) -> None:
        harness = _LifecycleHarness()
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        impulse = {
            "id": "defer-1",
            "state": "queued",
            "created_ts": now - 60,
            "updated_ts": now - 60,
            "source": "story",
            "reason": "state_share",
            "action": "message",
            "topic": "桌边刚收好的几支笔",
            "window_start_at": now,
            "preferred_ts": now,
            "best_until_at": now + 3600,
            "expire_at": now + 7200,
        }
        user = {
            "user_id": "10001",
            "planned_candidate_id": "candidate-defer-1",
            "planned_proactive_impulse_id": "defer-1",
            "planned_proactive_reason": "state_share",
            "planned_proactive_action": "message",
            "planned_proactive_source": "story",
            "planned_proactive_topic": impulse["topic"],
            "planned_proactive_window_start_at": now,
            "planned_proactive_best_until_at": now + 3600,
            "planned_proactive_expire_at": now + 7200,
            "next_proactive_at": now,
            "proactive_impulses": [impulse],
        }
        harness.data["proactive_candidate_pool"] = [
            {
                "id": "candidate-defer-1",
                "user_id": "10001",
                "status": "accepted",
                "source": "story",
                "reason": "state_share",
                "created_ts": now - 60,
                "updated_ts": now - 60,
                "scheduled_ts": now,
            }
        ]

        with patch("astrbot_plugin_private_companion.proactive_engine._now_ts", return_value=now):
            harness._mark_planned_candidate_status(user, "deferred", "用户刚活跃过")

        self.assertEqual("deferred", user["proactive_impulses"][0]["state"])
        self.assertEqual("defer-1", user["planned_proactive_impulse_id"])
        self.assertEqual("deferred", harness.data["proactive_candidate_pool"][0]["status"])

    def test_impossible_future_window_is_terminated_before_queueing(self) -> None:
        harness = _LifecycleHarness()
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        event = {
            "reason": "check_in",
            "source": "story",
            "window_start_at": now + 600,
            "preferred_ts": now + 900,
            "best_until_at": now + 1800,
            "expire_at": now + 1200,
        }

        prepared, note = harness._prepare_proactive_candidate_window(
            event,
            reason="check_in",
            source="story",
            now=now,
        )

        self.assertIsNone(prepared)
        self.assertEqual(note, "来源事件时间窗口无效")
        self.assertEqual(event["lifecycle_status"], "skipped")

    def test_candidate_from_previous_timezone_is_terminally_skipped(self) -> None:
        harness = _LifecycleHarness()
        harness.environment_perception_timezone = "Asia/Tokyo"
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        event = {
            "reason": "check_in",
            "source": "story",
            "window_timezone": "Asia/Shanghai",
            "window_start_at": now,
            "preferred_ts": now + 60,
            "best_until_at": now + 600,
            "expire_at": now + 1200,
        }

        prepared, note = harness._prepare_proactive_candidate_window(
            event,
            reason="check_in",
            source="story",
            now=now,
        )

        self.assertIsNone(prepared)
        self.assertEqual(note, "来源事件生成时区已变化")
        self.assertEqual(event["lifecycle_status"], "skipped")

    def test_explicit_timer_is_exempt_from_timezone_invalidation(self) -> None:
        harness = _LifecycleHarness()
        harness.environment_perception_timezone = "Asia/Tokyo"
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        event = {
            "reason": "timer",
            "source": "timer",
            "window_timezone": "Asia/Shanghai",
            "window_start_at": now,
            "preferred_ts": now + 60,
            "best_until_at": now + 600,
            "expire_at": now + 1200,
        }

        prepared, note = harness._prepare_proactive_candidate_window(
            event,
            reason="timer",
            source="timer",
            now=now,
        )

        self.assertEqual(note, "")
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared["window_timezone"], "Asia/Shanghai")

    def test_stable_origin_id_prevents_terminal_source_from_requeueing(self) -> None:
        harness = _LifecycleHarness()
        now = datetime.now(TZ).timestamp()
        user = {"proactive_impulses": []}
        base = {
            "origin_event_id": "story-event-20260718-0810",
            "reason": "morning_greeting",
            "source": "story",
            "topic": "早间问候",
            "window_start_at": now + 600,
            "preferred_ts": now + 900,
            "best_until_at": now + 1800,
            "expire_at": now + 2400,
        }

        first = harness._queue_proactive_impulse(user, dict(base))
        self.assertTrue(first)
        first["state"] = "blocked"
        first["updated_ts"] = now

        second = harness._queue_proactive_impulse(user, dict(base))

        self.assertEqual(second, {})
        self.assertEqual(len(user["proactive_impulses"]), 1)

    def test_time_window_origin_id_ignores_random_minute_within_same_slot(self) -> None:
        harness = _LifecycleHarness()
        first = {
            "date": "2026-07-18",
            "window": "12:05-13:35",
            "reason": "meal_care",
            "topic": "午饭吃了吗",
            "_scheduled_ts": datetime(2026, 7, 18, 12, 10, tzinfo=TZ).timestamp(),
        }
        second = dict(
            first,
            _scheduled_ts=datetime(2026, 7, 18, 12, 48, tzinfo=TZ).timestamp(),
        )

        self.assertEqual(
            harness._proactive_origin_event_id(first, source="meal_care"),
            harness._proactive_origin_event_id(second, source="meal_care"),
        )

    def test_same_origin_block_records_merge_instead_of_growing_pool(self) -> None:
        harness = _LifecycleHarness()
        candidate = {
            "origin_event_id": "expired-story-event",
            "source": "story",
            "reason": "morning_greeting",
            "action": "message",
            "topic": "已经过期的问候",
        }

        harness._record_proactive_candidate("10001", dict(candidate), status="blocked", note="潜在念头窗口已过期")
        harness._record_proactive_candidate("10001", dict(candidate), status="blocked", note="潜在念头窗口已过期")

        pool = harness.data["proactive_candidate_pool"]
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["repeat_count"], 2)
        self.assertEqual(pool[0]["merged_trigger_count"], 1)

    def test_weather_candidate_does_not_survive_into_the_next_day(self) -> None:
        harness = _LifecycleHarness()
        now = datetime.now(TZ).timestamp()
        harness.data["proactive_candidate_pool"] = [{
            "id": "old-weather",
            "user_id": "10001",
            "source": "weather_alert",
            "reason": "weather_alert",
            "status": "accepted",
            "created_ts": now - 24 * 3600,
            "scheduled_ts": now - 24 * 3600,
            "last_seen_ts": now - 60,
            "expire_at": now - 23 * 3600,
        }]

        pool = harness._cleanup_proactive_candidate_pool(now=now)

        self.assertEqual([], pool)

    def test_fresh_weather_candidate_merges_but_old_one_starts_a_new_record(self) -> None:
        harness = _LifecycleHarness()
        now = datetime.now(TZ).timestamp()
        base = {
            "origin_event_id": "weather:event-1",
            "source": "weather_alert",
            "reason": "weather_alert",
            "action": "message",
            "topic": "外面开始下雨",
            "expire_at": now + 3600,
        }
        first = harness._record_proactive_candidate("10001", dict(base), status="blocked")
        first["created_ts"] = now - 150 * 60
        first["last_seen_ts"] = now - 60 * 60
        first["scheduled_ts"] = now - 60 * 60

        merged = harness._record_proactive_candidate("10001", dict(base), status="blocked")
        self.assertEqual(first["id"], merged["id"])
        self.assertEqual(merged["repeat_count"], 2)

        first["last_seen_ts"] = now - 3 * 3600
        first["created_ts"] = now - 3 * 3600
        first["scheduled_ts"] = now - 3 * 3600
        fresh_event = dict(base, origin_event_id="weather:event-2")
        new_record = harness._record_proactive_candidate("10001", fresh_event, status="blocked")
        self.assertNotEqual(first["id"], new_record["id"])

    def test_cross_midnight_quiet_hours_return_same_day_end(self) -> None:
        harness = _LifecycleHarness()
        harness.quiet_hours = "23:00-10:30"
        now = datetime(2026, 7, 18, 1, 0, tzinfo=TZ).timestamp()

        quiet_end = harness._quiet_hours_end_timestamp(now)

        self.assertEqual(datetime.fromtimestamp(quiet_end, TZ), datetime(2026, 7, 18, 10, 30, tzinfo=TZ))

    def test_contextual_window_fully_covered_by_quiet_hours_is_skipped(self) -> None:
        harness = _LifecycleHarness()
        harness.quiet_hours = "23:00-10:30"
        now = datetime(2026, 7, 18, 1, 0, tzinfo=TZ).timestamp()
        event = {
            "reason": "morning_greeting",
            "source": "story",
            "window_start_at": datetime(2026, 7, 18, 1, 10, tzinfo=TZ).timestamp(),
            "preferred_ts": datetime(2026, 7, 18, 1, 20, tzinfo=TZ).timestamp(),
            "best_until_at": datetime(2026, 7, 18, 2, 0, tzinfo=TZ).timestamp(),
            "expire_at": datetime(2026, 7, 18, 3, 0, tzinfo=TZ).timestamp(),
        }

        prepared, note = harness._prepare_proactive_candidate_window(
            event,
            reason="morning_greeting",
            source="story",
            now=now,
        )

        self.assertIsNone(prepared)
        self.assertEqual(note, "免打扰覆盖整个有效窗口")
        self.assertEqual(event["lifecycle_status"], "skipped")

    def test_durable_window_can_move_after_quiet_hours(self) -> None:
        harness = _LifecycleHarness()
        harness.quiet_hours = "23:00-10:30"
        now = datetime(2026, 7, 18, 1, 0, tzinfo=TZ).timestamp()
        event = {
            "reason": "creative_share",
            "source": "creative",
            "window_start_at": datetime(2026, 7, 18, 1, 10, tzinfo=TZ).timestamp(),
            "preferred_ts": datetime(2026, 7, 18, 1, 20, tzinfo=TZ).timestamp(),
            "best_until_at": datetime(2026, 7, 18, 2, 0, tzinfo=TZ).timestamp(),
            "expire_at": datetime(2026, 7, 18, 3, 0, tzinfo=TZ).timestamp(),
        }

        prepared, note = harness._prepare_proactive_candidate_window(
            event,
            reason="creative_share",
            source="creative",
            now=now,
        )

        self.assertEqual(note, "")
        self.assertIsNotNone(prepared)
        self.assertGreaterEqual(
            prepared["window_start_at"],
            datetime(2026, 7, 18, 10, 32, tzinfo=TZ).timestamp(),
        )
        self.assertGreater(prepared["expire_at"], prepared["best_until_at"])

    def test_expired_story_event_is_terminated_on_original_plan_item(self) -> None:
        harness = _LifecycleHarness()
        harness.max_proactive_plan_lag_minutes = 480
        now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ).timestamp()
        event = {
            "date": "2026-07-18",
            "window": "08:00-08:10",
            "reason": "morning_greeting",
            "topic": "早间问候",
            "lifecycle_status": "planned",
        }
        harness.data["daily_story_plan"] = {
            "date": "2026-07-18",
            "proactive_events": [event],
        }

        selected = harness._pick_story_plan_event(now, user={})

        self.assertIsNone(selected)
        self.assertEqual(event["lifecycle_status"], "expired")
        self.assertEqual(event["lifecycle_note"], "来源事件有效窗口已过期")


class ProactiveCandidatePageStatsTests(unittest.TestCase):
    def test_snapshot_and_statistics_use_full_candidate_pool(self) -> None:
        now = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)
        yesterday = now - timedelta(days=1)
        plugin = SimpleNamespace(
            _format_timestamp_elapsed=lambda _value: "刚刚",
            _environment_fromtimestamp=lambda value: datetime.fromtimestamp(value, TZ),
        )
        api = PrivateCompanionPageApi(plugin)
        candidates = []
        for index in range(300):
            created = now if index < 250 else yesterday
            status = "blocked" if index < 20 else "sent"
            candidates.append(
                {
                    "id": f"candidate-{index}",
                    "user_id": "10001",
                    "source": "story",
                    "reason": "check_in",
                    "action": "message",
                    "status": status,
                    "topic": f"topic-{index}",
                    "signature": f"signature-{index}",
                    "created_ts": created.timestamp(),
                    "updated_ts": created.timestamp(),
                    "scheduled_ts": created.timestamp(),
                    "repeat_count": 1,
                }
            )
        raw_data = {
            "users": {"10001": {"user_id": "10001", "enabled": True}},
            "groups": {},
            "proactive_candidate_pool": candidates,
        }

        snapshot = api._overview_data_snapshot_locked(raw_data)
        summary = api._proactive_candidate_summary(snapshot)

        self.assertEqual(len(snapshot["proactive_candidate_pool"]), 300)
        self.assertEqual(summary["pool_record_total"], 300)
        self.assertEqual(summary["today_record_total"], 250)
        self.assertEqual(summary["today_blocked_record_total"], 20)
        self.assertEqual(summary["list_total"], 300)
        self.assertEqual(summary["list_displayed_total"], 60)
        self.assertTrue(summary["list_truncated"])


if __name__ == "__main__":
    unittest.main()
