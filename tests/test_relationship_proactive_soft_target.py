# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path

from astrbot_plugin_private_companion.companion_interaction_expression import (
    build_expression_decision,
    expression_decision_prompt,
)
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.relationship_policy import default_relationship_stage_policy


class _ProactiveHarness(ProactiveMixin):
    max_daily_messages = 8
    idle_minutes = 5
    min_interval_minutes = 20
    greeting_idle_minutes = 30
    proactive_unanswered_slowdown_start = 1
    proactive_unanswered_max_interval_multiplier = 2.2
    friend_unanswered_max_cooldown_hours = 60.0
    enable_custom_relationship_stage_policy = True
    normal_interaction_band_cap = "warm"
    owner_exclusive_proactive_limit = 6

    def __init__(self) -> None:
        self.config = {}
        self.data = {"daily_state": {"energy": 70, "conditions": []}}

    @staticmethod
    def _private_user_role(_user, _user_id: str = "") -> str:
        return "friend"

    @staticmethod
    def _get_relevant_important_dates() -> list[dict]:
        return []

    @staticmethod
    def _proactive_intensity_effect(_key: str, default):
        return default


class RelationshipProactiveSoftTargetTests(unittest.TestCase):
    def test_acquaintance_starts_without_proactive_care_target(self) -> None:
        stages = default_relationship_stage_policy()
        acquaintance = next(item for item in stages if item["key"] == "acquaintance")
        self.assertEqual(0, acquaintance["proactive_care_limit"])

    def test_non_distant_stage_target_does_not_cut_the_hard_daily_allowance(self) -> None:
        harness = _ProactiveHarness()
        user = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 0,
            "ignored_streak": 0,
        }

        self.assertEqual(8, harness._effective_user_daily_limit(user))
        self.assertEqual(1, harness._relationship_proactive_soft_target(user))
        self.assertLess(harness._soft_daily_target(user), 1.0)

        decision = build_expression_decision(
            {
                "relationship_score": 0,
                "relationship_baseline": {
                    "stage_key": "acquaintance",
                    "proactive_care_limit": 1,
                },
                "proactive_candidate": {"eligible": True, "budget": 8},
            }
        )
        self.assertEqual(8, decision.proactive_budget)
        self.assertEqual(1, decision.proactive_target)
        self.assertIn("relationship_proactive_soft_target", decision.reason_codes)
        prompt = expression_decision_prompt(decision)
        self.assertIn("柔性节奏目标", prompt)
        self.assertIn("不是必须凑满或一到即停的硬配额", prompt)

    def test_owner_soft_target_tracks_configured_daily_limit(self) -> None:
        harness = _ProactiveHarness()
        harness._private_user_role = lambda _user, _user_id="": "owner"
        user = {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "relationship_score": 1200,
            "ignored_streak": 0,
        }

        self.assertEqual(8, harness._effective_user_daily_limit(user))
        self.assertAlmostEqual(7.36, harness._soft_daily_target(user), places=2)

    def test_unanswered_slowdown_keeps_daily_allowance_and_scales_interval(self) -> None:
        harness = _ProactiveHarness()
        for ignored, expected_multiplier in ((0, 1.0), (1, 1.147), (2, 1.294), (3, 1.441), (4, 1.504)):
            user = {
                "relationship_role": "friend",
                "relationship_mode": "normal",
                "relationship_score": 120,
                "ignored_streak": ignored,
            }
            self.assertEqual(8, harness._effective_user_daily_limit(user))
            self.assertAlmostEqual(expected_multiplier, harness._unanswered_interval_multiplier(user), places=2)

    def test_friend_pacing_respects_configured_values(self) -> None:
        harness = _ProactiveHarness()
        user = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 120,
            "ignored_streak": 0,
        }

        self.assertEqual(5, harness._effective_user_idle_minutes(user))
        self.assertEqual(20, harness._effective_user_min_interval_minutes(user))
        self.assertEqual(30, harness._effective_user_greeting_idle_minutes(user))

    def test_friend_unanswered_state_slows_down_without_stopping(self) -> None:
        harness = _ProactiveHarness()
        user = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 120,
            "ignored_streak": 2,
            "awaiting_reply_since": 1_000.0,
        }

        reason = harness._friend_unanswered_silence_reason(user, now=1_000.0 + 49 * 3600)

        self.assertEqual("", reason)
        self.assertIn("不自动停发", user["friend_unanswered_silence_note"])
        self.assertEqual(0, user["friend_unanswered_silenced_since"])

    def test_friend_followup_delay_scales_from_configured_interval(self) -> None:
        harness = _ProactiveHarness()
        user = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 120,
            "ignored_streak": 0,
            "sent_today": 1,
        }

        low, high = harness._friend_proactive_spread_delay_hours(user, now=10_000.0)

        self.assertGreaterEqual(low, 0.25)
        self.assertLess(high, 1.0)

    def test_random_impulse_is_available_without_negative_state(self) -> None:
        harness = _ProactiveHarness()
        harness.data["daily_state"] = {"energy": 70, "mood": "平稳", "conditions": []}
        context = harness._random_proactive_impulse_context(
            {"ignored_streak": 0, "awaiting_reply_since": 0, "last_sent": 0},
            now=10_000.0,
        )

        self.assertTrue(context["allowed"])
        self.assertFalse(context["suggest_soft_reason"])
        self.assertIn("自然", context["reasons"][0])

    def test_user_detail_explains_interval_slowdown(self) -> None:
        root = Path(__file__).resolve().parents[1]
        page_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(root.glob("page_api*.py"))
        )
        panel_source = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"unanswered_interval_multiplier"', page_source)
        self.assertIn('"unanswered_slowdown_text"', page_source)
        self.assertIn('"soft_daily_target"', page_source)
        self.assertIn('"未回应节奏"', panel_source)
        self.assertIn('"有效空闲门槛"', panel_source)
        self.assertIn('"有效最小间隔"', panel_source)

    def test_setup_guide_preserves_review_switches_and_schema_defaults(self) -> None:
        root = Path(__file__).resolve().parents[1]
        page_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(root.glob("page_api*.py"))
        )
        panel_source = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertIn('number_value("privateMaxDailyMessages", 8)', page_source)
        self.assertIn('number_value("privateIdleMinutes", 60)', page_source)
        self.assertIn('number_value("privateMinIntervalMinutes", 120)', page_source)
        self.assertIn('bool_value(\n                "enable_proactive_message_review",', page_source)
        self.assertIn("privateMaxDailyMessages: 8", panel_source)
        self.assertIn("privateIdleMinutes: 60", panel_source)
        self.assertIn("privateMinIntervalMinutes: 120", panel_source)

    def test_relationship_distance_and_explicit_zeroes_remain_hard_gates(self) -> None:
        harness = _ProactiveHarness()
        distant = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": -1,
        }
        disabled = {**distant, "relationship_score": 0, "proactive_daily_limit": 0}

        self.assertEqual(0, harness._effective_user_daily_limit(distant))
        self.assertEqual(0, harness._effective_user_daily_limit(disabled))

        harness.max_daily_messages = 0
        self.assertEqual(0, harness._effective_user_daily_limit({**distant, "relationship_score": 0}))

    def test_disabled_affinity_master_switch_uses_only_the_normal_daily_limit(self) -> None:
        harness = _ProactiveHarness()
        harness.enable_custom_relationship_stage_policy = False
        distant = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": -1200,
            "current_interaction": {"expression_band": "hurt"},
        }

        self.assertEqual(8, harness._effective_user_daily_limit(distant))
        self.assertEqual(8, harness._relationship_proactive_soft_target(distant))

    def test_cooldown_and_hurt_state_remain_hard_decision_gates(self) -> None:
        baseline = {
            "stage_key": "familiar",
            "proactive_care_limit": 2,
        }
        cooldown = build_expression_decision(
            {
                "relationship_score": 200,
                "relationship_baseline": baseline,
                "proactive_candidate": {
                    "eligible": True,
                    "budget": 8,
                    "current_ts": 100,
                    "cooldown_until": 200,
                },
            }
        )
        hurt = build_expression_decision(
            {
                "relationship_score": 200,
                "relationship_baseline": baseline,
                "current_interaction": {"expression_band": "hurt"},
                "proactive_candidate": {"eligible": True, "budget": 8},
            }
        )

        self.assertEqual(0, cooldown.proactive_budget)
        self.assertIn("proactive_cooldown_active", cooldown.reason_codes)
        self.assertEqual(0, hurt.proactive_budget)
        self.assertIn("interaction_proactive_suppressed", hurt.reason_codes)


if __name__ == "__main__":
    unittest.main()
