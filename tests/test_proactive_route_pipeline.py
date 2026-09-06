# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.daily_state_tick import DailyStateTickMixin
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_routes import PROACTIVE_ROUTE_REGISTRY


class _RouteHarness(DailyStateTickMixin, ProactiveMixin, ProactiveEngineMixin):
    max_daily_messages = 8
    idle_minutes = 60
    min_interval_minutes = 120
    greeting_idle_minutes = 30
    proactive_unanswered_slowdown_start = 1
    proactive_unanswered_max_interval_multiplier = 2.2
    friend_unanswered_max_cooldown_hours = 60.0
    enable_custom_relationship_stage_policy = False

    def __init__(self) -> None:
        self.config = {}
        self.data = {"daily_state": {"energy": 70, "conditions": []}}

    @staticmethod
    def _private_user_role(_user, _user_id: str = "") -> str:
        return "lover"

    @staticmethod
    def _get_relevant_important_dates() -> list[dict]:
        return []

    @staticmethod
    def _proactive_intensity_effect(_key: str, default):
        return default

    @staticmethod
    def _recent_chat_proactive_guard_reason(*_args, **_kwargs) -> str:
        return "刚聊完，普通主动延后（还需安静约 30 分钟）"


class _RetryHarness(DailyStateMixin):
    @staticmethod
    def _planned_proactive_delivery_key(user: dict) -> str:
        return str(user.get("delivery_key") or "")

    @staticmethod
    def _latest_private_user_activity_ts(user: dict) -> float:
        return float(user.get("activity_at") or 0)

    @staticmethod
    def _validate_proactive_outbound_candidate(text: str, **_kwargs) -> dict:
        return {"decision": "send", "text": text}


class ProactiveRoutePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _RouteHarness()

    def test_timer_check_in_uses_transactional_route_and_window(self) -> None:
        user = {
            "planned_proactive_source": "timer",
            "planned_proactive_reason": "check_in",
            "planned_proactive_action": "message",
            "planned_proactive_topic": "拿药",
            "planned_proactive_motive": "提醒按时拿药",
        }

        self.harness._store_planned_proactive_route_fields(
            user,
            {"source": "timer", "reason": "check_in", "origin_event_id": "timer-1"},
        )

        self.assertEqual("transactional", user["planned_proactive_kind"])
        self.assertTrue(user["planned_proactive_route_dedupe_key"].startswith("transactional:"))
        self.assertEqual(
            (90 * 60.0, 6 * 3600.0),
            self.harness._proactive_impulse_default_window_seconds("check_in", source="timer"),
        )

    def test_timer_activity_followup_uses_continuation_route(self) -> None:
        route = PROACTIVE_ROUTE_REGISTRY.route_for(
            source="timer",
            reason="activity_followup",
        )

        self.assertEqual("continuation", route.key)
        self.assertTrue(route.delivery_options({})["cancel_if_new_inbound"])
        activity_plan = {"reason": "activity_followup"}
        self.assertFalse(route.delivery_options(activity_plan)["allow_automatic_followup"])
        self.assertFalse(route.settlement(activity_plan)["await_reply"])

        ordinary_timer = PROACTIVE_ROUTE_REGISTRY.route_for(source="timer", reason="reminder")
        self.assertEqual("transactional", ordinary_timer.key)

    def test_routes_have_independent_dedupe_keys(self) -> None:
        cases = (
            ("timer", "reminder", "transactional"),
            ("weather_alert", "weather_alert", "safety_event"),
            ("pending_followup", "check_in", "continuation"),
            ("daily_greeting", "morning_greeting", "ritual"),
            ("news_share", "news_share", "content_share"),
            ("state", "state_share", "self_life"),
            ("habit", "quiet_care", "relational"),
        )
        keys = set()
        for source, reason, expected in cases:
            route = PROACTIVE_ROUTE_REGISTRY.route_for(source=source, reason=reason)
            prepared = route.prepare_candidate(
                {
                    "reason": reason,
                    "topic": "同一个表面话题",
                    "motive": "同一个表面动机",
                    "origin_event_id": f"event-{expected}",
                    "scheduled_ts": 1000,
                },
                source=source,
                now=900,
                date_key="2026-08-08",
            )
            self.assertEqual(expected, prepared["kind"])
            keys.add(prepared["route_dedupe_key"])
        self.assertEqual(7, len(keys))

    def test_new_inbound_policy_depends_on_route(self) -> None:
        transactional = PROACTIVE_ROUTE_REGISTRY.route_for(source="timer", reason="check_in")
        safety = PROACTIVE_ROUTE_REGISTRY.route_for(source="weather_alert", reason="weather_alert")
        continuation = PROACTIVE_ROUTE_REGISTRY.route_for(source="pending_followup", reason="check_in")
        relational = PROACTIVE_ROUTE_REGISTRY.route_for(source="habit", reason="quiet_care")

        self.assertFalse(transactional.delivery_options({})["cancel_if_new_inbound"])
        self.assertFalse(safety.delivery_options({})["cancel_if_new_inbound"])
        self.assertTrue(continuation.delivery_options({})["cancel_if_new_inbound"])
        self.assertTrue(relational.delivery_options({})["cancel_if_new_inbound"])

    def test_continuation_cancels_when_anchor_has_been_superseded(self) -> None:
        user = {
            "planned_proactive_kind": "continuation",
            "planned_proactive_source": "pending_followup",
            "planned_proactive_reason": "check_in",
            "planned_proactive_trigger_message_id": "message-1",
            "planned_proactive_trigger_inbound_count": 3,
            "private_inbound_count": 4,
        }

        decision = self.harness._planned_proactive_route_preflight(user, now=1000)

        self.assertFalse(decision.allowed)
        self.assertEqual("cancel", decision.action)
        self.assertIn("锚点", decision.reason)

    def test_recent_chat_policy_bypasses_events_and_short_defers_shares(self) -> None:
        timer_user = {
            "planned_proactive_source": "timer",
            "planned_proactive_reason": "check_in",
        }
        self_user = {
            "planned_proactive_source": "state",
            "planned_proactive_reason": "state_share",
        }

        self.assertEqual(
            "",
            self.harness._route_recent_chat_guard_reason(
                timer_user,
                now=1000,
                planned_reason="check_in",
                due_timer_active=False,
                is_troubleshooting=False,
            ),
        )
        note = self.harness._route_recent_chat_guard_reason(
            self_user,
            now=1000,
            planned_reason="state_share",
            due_timer_active=False,
            is_troubleshooting=False,
        )
        self.assertIn("分享路线短暂避让", note)

    def test_settlement_only_builds_reply_debt_for_reply_routes(self) -> None:
        no_reply_cases = (
            ("transactional", "reminder"),
            ("safety_event", "weather_alert"),
            ("self_life", "state_share"),
            ("content_share", "news_share"),
            ("continuation", "activity_followup"),
        )
        for kind, reason in no_reply_cases:
            with self.subTest(kind=kind):
                user = {
                    "planned_proactive_kind": kind,
                    "planned_proactive_reason": reason,
                    "ignored_streak": 2,
                    "awaiting_reply_since": 50,
                }
                settlement = self.harness._planned_proactive_route_settlement(user)
                self.harness._settle_proactive_route_state(
                    user,
                    route_key=kind,
                    settlement=settlement,
                    sent_at=1000,
                )
                self.assertEqual(2, user["ignored_streak"])
                self.assertEqual(2, user["unanswered_proactive_count"])
                self.assertEqual(50, user["awaiting_reply_since"])

        for kind, reason in (("relational", "quiet_care"), ("continuation", "check_in"), ("ritual", "meal_care")):
            with self.subTest(kind=kind):
                user = {
                    "planned_proactive_kind": kind,
                    "planned_proactive_reason": reason,
                    "ignored_streak": 2,
                }
                settlement = self.harness._planned_proactive_route_settlement(user)
                self.harness._settle_proactive_route_state(
                    user,
                    route_key=kind,
                    settlement=settlement,
                    sent_at=1000,
                )
                self.assertEqual(3, user["ignored_streak"])
                self.assertEqual(3, user["unanswered_proactive_count"])
                self.assertEqual(1000, user["awaiting_reply_since"])

    def test_content_settlement_clears_only_its_context_and_disables_followup(self) -> None:
        user = {
            "planned_proactive_kind": "content_share",
            "planned_proactive_reason": "news_share",
            "news_context": {"title": "真实标题"},
            "group_share_context": {"message": "保留"},
        }
        settlement = self.harness._planned_proactive_route_settlement(user)

        self.harness._settle_proactive_route_state(
            user,
            route_key="content_share",
            settlement=settlement,
            sent_at=1000,
        )

        self.assertFalse(settlement["allow_automatic_followup"])
        self.assertEqual({}, user["news_context"])
        self.assertEqual({"message": "保留"}, user["group_share_context"])
        self.assertEqual(1, user["proactive_route_sent_counts"]["content_share"])

    def test_content_route_rejects_missing_core_evidence(self) -> None:
        user = {
            "planned_proactive_kind": "content_share",
            "planned_proactive_reason": "news_share",
            "news_context": {},
        }

        decision = self.harness._planned_proactive_route_preflight(user, now=1000)

        self.assertFalse(decision.allowed)
        self.assertIn("核心上下文", decision.reason)

    def test_retry_respects_route_inbound_policy(self) -> None:
        harness = _RetryHarness()
        base_payload = {
            "active": True,
            "expires_at": 2000,
            "fresh_until_at": 1800,
            "delivery_key": "event-1",
            "freshness": "contextual",
            "private_activity_at": 900,
            "private_inbound_count": 2,
            "text": "记得拿药。",
            "route_retry_profile": "until_expiry",
        }
        transactional_user = {
            "delivery_key": "event-1",
            "activity_at": 950,
            "private_inbound_count": 3,
            "pending_proactive_send_retry": {
                **base_payload,
                "route_cancel_if_new_inbound": False,
            },
        }
        continuation_user = {
            "delivery_key": "event-1",
            "activity_at": 950,
            "private_inbound_count": 3,
            "pending_proactive_send_retry": {
                **base_payload,
                "route_retry_profile": "while_anchor_live",
                "route_cancel_if_new_inbound": True,
            },
        }

        self.assertIsNotNone(harness._pending_proactive_send_retry(transactional_user, now=1000))
        self.assertIsNone(harness._pending_proactive_send_retry(continuation_user, now=1000))

    def test_delivery_shape_is_route_specific(self) -> None:
        expected = {
            "transactional": True,
            "safety_event": False,
            "continuation": True,
            "ritual": False,
            "content_share": False,
            "self_life": False,
            "relational": False,
        }
        for kind, disable_segmenting in expected.items():
            with self.subTest(kind=kind):
                route = PROACTIVE_ROUTE_REGISTRY.route_for(kind=kind)
                self.assertEqual(disable_segmenting, route.delivery_options({})["disable_segmenting"])


if __name__ == "__main__":
    unittest.main()
