# -*- coding: utf-8 -*-
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.constants import _REASON_TEXT
from astrbot_plugin_private_companion.daily_state_tick import DailyStateTickMixin
from astrbot_plugin_private_companion.helpers import _today_key
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_routes import PROACTIVE_ROUTE_REGISTRY


class PersonalizedRhythmTests(unittest.TestCase):
    def test_existing_external_share_reasons_have_labels_and_routes(self):
        expected_labels = {
            "news_share": "新闻",
            "web_exploration_share": "探索",
            "game_invite": "一局",
        }
        for reason, keyword in expected_labels.items():
            with self.subTest(reason=reason):
                self.assertIn(keyword, _REASON_TEXT[reason])
                self.assertEqual(
                    "content_share",
                    PROACTIVE_ROUTE_REGISTRY.route_for(reason=reason).key,
                )
        for reason in ("memory_echo", "mood_checkin", "absence_miss"):
            with self.subTest(reason=reason):
                self.assertIn(reason, _REASON_TEXT)
                route = PROACTIVE_ROUTE_REGISTRY.route_for(reason=reason)
                self.assertEqual("relational", route.key)
                prepared = route.prepare_candidate(
                    {"reason": reason, "topic": "测试", "motive": "测试"},
                    source="event",
                    now=1.0,
                    date_key="2026-08-16",
                )
                self.assertEqual("none", prepared["response_expectation"])
                self.assertFalse(route.settlement(prepared)["allow_automatic_followup"])
        for reason in ("open_loop_followup", "dream_share", "qzone_life_publish"):
            with self.subTest(reason=reason):
                self.assertIn(reason, _REASON_TEXT)

    @staticmethod
    def _candidate_harness(summary=None):
        return SimpleNamespace(
            data={"yesterday_conversation_summary": summary or {}},
            _private_user_role=lambda _user: "owner",
            _move_timestamp_into_reason_window=lambda timestamp, _reason, user=None: timestamp,
            _window_from_delay_minutes=lambda minutes, width_minutes=55: f"{minutes}:{width_minutes}",
            _current_relationship_gate_mode=lambda _user, now=None: "",
            _current_emotion_gate_mode=lambda _user, now=None: "",
        )

    def test_memory_echo_uses_summary_once(self):
        now = 2_000_000.0
        summary = {
            "date": _today_key(),
            "source_date": "2026-08-15",
            "scope": "owner_private_only",
            "raw_excerpt_chars": 120,
            "summary": "昨天聊到了新项目。",
            "residues": [{"type": "计划", "content": "用户准备继续完善新项目", "strength": "中"}],
        }
        harness = self._candidate_harness(summary)
        user = {"last_user_message_at": now - 3600, "last_sent": 0, "ignored_streak": 0}
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            event = ProactiveEngineMixin._pick_memory_echo_event(harness, user, now=now)
        self.assertEqual("memory_echo", event["reason"])
        self.assertEqual("用户准备继续完善新项目", event["context"]["residue"])
        self.assertIsNone(ProactiveEngineMixin._pick_memory_echo_event(harness, user, now=now))

    def test_memory_echo_can_reuse_an_explicit_correction_once(self):
        now = 2_000_000.0
        harness = self._candidate_harness()
        harness._recent_memory_correction_for_echo = lambda _user, now=None: {
            "correction_key": "fixed-1",
            "text": "不是用户先提的，是 Bot 先提的",
            "at": now - 86400,
        }
        user = {"last_user_message_at": now - 3600, "last_sent": 0, "ignored_streak": 0}
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            event = ProactiveEngineMixin._pick_memory_echo_event(harness, user, now=now)
        self.assertEqual("memory_echo", event["reason"])
        self.assertEqual("不是用户先提的，是 Bot 先提的", event["context"]["correction"])
        self.assertIsNone(ProactiveEngineMixin._pick_memory_echo_event(harness, user, now=now))

    def test_mood_checkin_accepts_typed_emotional_residue_and_rejects_neutral(self):
        now = 2_000_000.0
        base = {
            "date": _today_key(),
            "source_date": "2026-08-15",
            "scope": "owner_private_only",
            "raw_excerpt_chars": 120,
            "summary": "昨天聊了近况。",
        }
        user = {"last_user_message_at": now - 3600, "last_sent": 0, "ignored_streak": 0}
        neutral = dict(base, residues=[{"type": "计划", "content": "准备整理资料", "strength": "中"}])
        self.assertIsNone(
            ProactiveEngineMixin._pick_mood_checkin_event(self._candidate_harness(neutral), dict(user), now=now)
        )
        positive = dict(base, residues=[{"type": "情绪", "content": "用户因为项目进展顺利很开心", "strength": "中"}])
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            typed_event = ProactiveEngineMixin._pick_mood_checkin_event(
                self._candidate_harness(positive), dict(user), now=now
            )
        self.assertEqual("mood_checkin", typed_event["reason"])
        negative = dict(base, residues=[{"type": "情绪", "content": "用户昨晚因为面试有些焦虑", "strength": "中"}])
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            event = ProactiveEngineMixin._pick_mood_checkin_event(
                self._candidate_harness(negative), dict(user), now=now
            )
        self.assertEqual("mood_checkin", event["reason"])

    def test_absence_miss_ignores_passive_reply_but_blocks_unanswered_proactive(self):
        now = 2_000_000.0
        harness = self._candidate_harness()
        base = {"last_user_message_at": now - 4 * 86400, "ignored_streak": 0, "last_sent": 0}
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            event = ProactiveEngineMixin._pick_absence_miss_event(harness, dict(base), now=now)
        self.assertEqual("absence_miss", event["reason"])
        passive_reply = dict(base, last_sent=now - 2 * 86400, ignored_streak=0, last_proactive_sent_at=0)
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            passive_event = ProactiveEngineMixin._pick_absence_miss_event(harness, passive_reply, now=now)
        self.assertEqual("absence_miss", passive_event["reason"])
        unanswered = dict(base, last_sent=now - 2 * 86400, last_proactive_sent_at=now - 2 * 86400, ignored_streak=0)
        self.assertIsNone(ProactiveEngineMixin._pick_absence_miss_event(harness, unanswered, now=now))

    def test_game_invite_requires_recent_high_interest_afterglow(self):
        now = 2_000_000.0
        harness = self._candidate_harness()
        harness._game_afterglow_for_user = lambda _user: {}
        harness._game_afterglow_public_view = lambda _state, now=None: {
            "active": True,
            "invite_interest": 82,
            "last_event_at": now - 2 * 3600,
            "game": "gomoku",
            "game_label": "五子棋",
            "tone": "还有点不服气",
            "reflection": "想找机会再来一局",
        }
        user = {"ignored_streak": 0, "last_sent": 0}
        with patch("astrbot_plugin_private_companion.proactive_engine.random.random", return_value=0.0):
            event = ProactiveEngineMixin._pick_game_invite_event(harness, user, now=now)
        self.assertEqual("game_invite", event["reason"])
        self.assertEqual("五子棋", event["context"]["game_label"])
        expired = dict(user)
        expired_view = dict(harness._game_afterglow_public_view({}, now=now), last_event_at=now - 6 * 86400)
        harness._game_afterglow_public_view = lambda _state, now=None: expired_view
        self.assertIsNone(ProactiveEngineMixin._pick_game_invite_event(harness, expired, now=now))

    def test_source_feedback_bias_is_bounded_and_directional(self):
        harness = SimpleNamespace()
        score = ProactiveEngineMixin._proactive_source_feedback_modifier
        cold = {"proactive_source_feedback": {"news_share": {"sent": 6, "replied": 0}}}
        warm = {
            "proactive_source_feedback": {
                "meal_care": {"sent": 6, "replied": 5, "positive": 4, "negative": 0}
            }
        }
        self.assertLess(score(harness, cold, "news_share"), 0)
        self.assertGreater(score(harness, warm, "meal_care"), 0)
        self.assertLessEqual(abs(score(harness, warm, "meal_care")), 0.18)

    def test_open_loop_is_converted_to_one_expiring_candidate(self):
        now = 2_000_000.0
        harness = SimpleNamespace(
            enable_open_loop_tracking=True,
            _private_user_role=lambda _user: "owner",
            _window_from_delay_minutes=lambda minutes, width_minutes=75: "window",
            _normalize_internal_motive_text=lambda text: text,
        )
        user = {
            "awaiting_reply_since": 0,
            "open_loops": [
                {
                    "text": "上周说的面试后来怎么样了",
                    "status": "待自然延续",
                    "created_ts": now - 8 * 3600,
                    "source": "dialogue_episode",
                }
            ],
        }
        candidate = ProactiveEngineMixin._pick_open_loop_followup_event(harness, user, now)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["reason"], "open_loop_followup")
        self.assertEqual(candidate["followup_kind"], "open_loop")
        self.assertGreater(candidate["_scheduled_ts"], now)
        self.assertGreater(user["open_loops"][0]["proactive_candidate_at"], 0)

    def test_burst_is_opt_in_and_respects_daily_reserve(self):
        method = ProactiveMixin._maybe_schedule_proactive_burst
        base = dict(sent_today=1, planned_proactive_burst=False, planned_proactive_impulse_id="abc")
        disabled = SimpleNamespace(enable_proactive_burst=False)
        self.assertFalse(method(disabled, base, now=1000, reason="check_in", source="random", action="message", motive="x", topic="y"))
        enabled = SimpleNamespace(
            enable_proactive_burst=True,
            proactive_burst_max_messages=2,
            _proactive_burst_max_messages=lambda: 2,
            proactive_burst_gap_min_seconds=30,
            proactive_burst_gap_max_seconds=30,
            _effective_user_daily_limit=lambda _user: 4,
            _proactive_impulse_default_window_seconds=lambda *_args, **_kwargs: (1800, 2400),
        )
        user = dict(base)
        self.assertTrue(method(enabled, user, now=1000, reason="check_in", source="random", action="message", motive="x", topic="y"))
        self.assertTrue(user["planned_proactive_burst"])
        self.assertEqual(user["next_proactive_at"], 1030)
        exhausted = dict(base, sent_today=3)
        self.assertFalse(method(enabled, exhausted, now=1000, reason="check_in", source="random", action="message", motive="x", topic="y"))

    def test_burst_scheduler_can_plan_a_third_message(self):
        enabled = SimpleNamespace(
            enable_proactive_burst=True,
            proactive_burst_max_messages=3,
            _proactive_burst_max_messages=lambda: 3,
            proactive_burst_gap_min_seconds=30,
            proactive_burst_gap_max_seconds=30,
            _effective_user_daily_limit=lambda _user: 5,
            _proactive_impulse_default_window_seconds=lambda *_args, **_kwargs: (1800, 2400),
        )
        user = {
            "sent_today": 1,
            "planned_proactive_burst": False,
            "planned_proactive_impulse_id": "abc",
        }
        self.assertTrue(ProactiveMixin._maybe_schedule_proactive_burst(
            enabled,
            user,
            now=1000,
            reason="check_in",
            source="random",
            action="message",
            motive="x",
            topic="y",
        ))
        self.assertEqual(user["proactive_burst_index"], 1)
        user["planned_proactive_burst"] = False
        self.assertTrue(ProactiveMixin._maybe_schedule_proactive_burst(
            enabled,
            user,
            now=1100,
            reason="check_in",
            source="random",
            action="message",
            motive="x",
            topic="y",
        ))
        self.assertEqual(user["proactive_burst_index"], 2)

    def test_burst_plan_bypasses_similarity_guard_but_normal_plan_does_not(self):
        guard = DailyStateTickMixin._proactive_similarity_guard_enabled
        args = dict(is_troubleshooting=False, action="message", timeliness="routine", duplicate_policy="semantic")
        self.assertTrue(guard({}, **args))
        self.assertFalse(guard({"planned_proactive_burst": True}, **args))

    def test_hour_curve_sampler_stays_inside_requested_window(self):
        harness = SimpleNamespace(
            proactive_hour_activity_curve=", ".join(["1"] * 24),
            _environment_fromtimestamp=datetime.fromtimestamp,
        )
        harness._proactive_hour_activity_weights = lambda: ProactiveMixin._proactive_hour_activity_weights(harness)
        sampled = ProactiveMixin._sample_proactive_timestamp(harness, {}, now=1000, delay_hours=(1, 2), reason="check_in")
        self.assertGreaterEqual(sampled, 4600)
        self.assertLessEqual(sampled, 8200)


if __name__ == "__main__":
    unittest.main()
