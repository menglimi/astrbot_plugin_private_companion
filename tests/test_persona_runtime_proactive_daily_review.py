# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from astrbot_plugin_private_companion.daily_review import DailyReviewMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _PersonaRuntimeHarness(ProactiveMixin, DailyReviewMixin):
    def __init__(self, overrides: dict[str, object] | None = None) -> None:
        self._overrides = dict(overrides or {})
        self.max_daily_messages = 8
        self.enable_proactive_burst = False
        self.proactive_burst_max_messages = 2
        self.proactive_burst_gap_min_seconds = 30
        self.proactive_burst_gap_max_seconds = 30
        self.quiet_hours = "23:00-08:30"
        self.group_wakeup_question_threshold = 65
        self.daily_review_time = "04:00"
        self.daily_review_provider_id = "primary-review"
        self.daily_review_retention_days = 30
        self.enable_daily_review = True
        self.daily_review_auto_apply_guidance = True
        self.environment_perception_timezone = "Asia/Shanghai"
        self.data = {"daily_review_reports": [], "daily_review_active_guidance": {}}

    @staticmethod
    def _environment_fromtimestamp(value):
        return datetime.fromtimestamp(value, ZoneInfo("Asia/Shanghai"))

    def persona_setting(self, key: str, default=None, persona_id: str = ""):
        return self._overrides.get(key, getattr(self, key, default))

    def _proactive_impulse_default_window_seconds(self, *_args, **_kwargs):
        return (1800, 2400)

    def _effective_user_daily_limit(self, _user):
        return 5

    @staticmethod
    def _task_provider(*values):
        return next((value for value in values if value), "")


class PersonaRuntimeSettingsTests(unittest.TestCase):
    def test_proactive_reads_override_without_mutating_primary_attribute(self):
        harness = _PersonaRuntimeHarness({
            "max_daily_messages": 2,
            "quiet_hours": "01:00-02:00",
            "group_wakeup_question_threshold": 22,
        })
        self.assertEqual(2, harness._runtime_max_daily_messages())
        self.assertEqual(8, harness.max_daily_messages)
        self.assertEqual(22, harness._effective_group_wakeup_question_threshold())
        self.assertNotEqual(0.0, harness._quiet_hours_end_timestamp(
            datetime(2026, 8, 21, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        ))

    def test_missing_key_follows_primary_and_explicit_false_is_preserved(self):
        harness = _PersonaRuntimeHarness({"enable_proactive_burst": False})
        harness.enable_proactive_burst = True
        self.assertFalse(bool(harness._proactive_setting("enable_proactive_burst", True)))
        self.assertEqual(8, harness._runtime_max_daily_messages())

    def test_burst_uses_persona_setting_on_unbound_lightweight_harness(self):
        harness = SimpleNamespace(
            persona_setting=lambda key, default=None: {
                "enable_proactive_burst": True,
                "proactive_burst_max_messages": 2,
                "proactive_burst_gap_min_seconds": 30,
                "proactive_burst_gap_max_seconds": 30,
            }.get(key, default),
            _effective_user_daily_limit=lambda _user: 5,
            _proactive_burst_max_messages=lambda: 2,
            _proactive_impulse_default_window_seconds=lambda *_args, **_kwargs: (1800, 2400),
        )
        user = {"sent_today": 1, "planned_proactive_burst": False, "planned_proactive_impulse_id": "abc"}
        self.assertTrue(ProactiveMixin._maybe_schedule_proactive_burst(
            harness, user, now=1000, reason="check_in", source="random",
            action="message", motive="x", topic="y",
        ))


class DailyReviewPersonaTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_review_uses_persona_time_provider_and_retention(self):
        harness = _PersonaRuntimeHarness({
            "daily_review_time": "01:00",
            "daily_review_provider_id": "persona-review",
            "daily_review_retention_days": 3,
        })
        self.assertEqual("persona-review", harness._daily_review_setting("daily_review_provider_id"))
        self.assertEqual("01:00", harness._daily_review_setting("daily_review_time"))
        self.assertEqual(3, harness._daily_review_setting("daily_review_retention_days"))
        now = datetime(2026, 8, 21, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("2026-08-20", harness._daily_review_target_date(now=now))
