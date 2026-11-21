"""Comprehensive tests for companion_v2.model_gateway.pricing_schedule."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from companion_v2.model_gateway.pricing import validate_pricing_rule
from companion_v2.model_gateway.pricing_schedule import (
    MAX_SCHEDULE_PERIODS,
    PricingScheduleError,
    match_schedule_period,
    normalize_schedule,
    validate_timezone,
)


class PricingScheduleTests(unittest.TestCase):
    def test_timezone_validation_and_period_cap(self):
        self.assertEqual(validate_timezone("Asia/Shanghai"), "Asia/Shanghai")
        self.assertEqual(validate_timezone("UTC"), "UTC")
        self.assertEqual(validate_timezone("America/New_York"), "America/New_York")
        with self.assertRaises(PricingScheduleError):
            validate_timezone("Mars/Phobos")
        with self.assertRaises(PricingScheduleError):
            validate_timezone(" Asia/Shanghai")

        # 64 non-overlapping 15-minute periods on Monday -> valid
        periods_64 = [
            {
                "days": [1],
                "all_day": False,
                "start": f"{(i * 15) // 60:02d}:{(i * 15) % 60:02d}",
                "end": f"{((i + 1) * 15) // 60:02d}:{((i + 1) * 15) % 60:02d}",
                "mode": "multiplier",
                "multiplier": 0.5,
            }
            for i in range(MAX_SCHEDULE_PERIODS)
        ]
        sched = normalize_schedule({"enabled": True, "timezone": "UTC", "periods": periods_64}, rule_validator=validate_pricing_rule)
        self.assertEqual(len(sched["periods"]), 64)

        # 65 periods -> rejected
        periods_65 = periods_64 + [{
            "days": [2], "all_day": True, "mode": "multiplier", "multiplier": 0.5,
        }]
        with self.assertRaises(PricingScheduleError):
            normalize_schedule({"enabled": True, "timezone": "UTC", "periods": periods_65}, rule_validator=validate_pricing_rule)

    def test_cross_midnight_previous_weekday_attribution_and_sunday_wrap(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        sched = normalize_schedule(
            {
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "periods": [
                    {
                        "id": "mon-night",
                        "days": [1],  # Monday ONLY
                        "all_day": False,
                        "start": "23:00",
                        "end": "07:00",
                        "mode": "multiplier",
                        "multiplier": 0.5,
                    },
                    {
                        "id": "sun-night-free",
                        "days": [7],  # Sunday ONLY (wraps to Monday 00:00..07:00!)
                        "all_day": False,
                        "start": "23:00",
                        "end": "07:00",
                        "mode": "multiplier",
                        "multiplier": 0.0,  # Explicit free!
                    },
                ],
            },
            rule_validator=validate_pricing_rule,
        )

        # 2026-11-23 is Monday!
        mon_2259 = datetime(2026, 11, 23, 22, 59, tzinfo=shanghai)
        mon_2300 = datetime(2026, 11, 23, 23, 0, tzinfo=shanghai)
        tue_0659 = datetime(2026, 11, 24, 6, 59, tzinfo=shanghai)
        tue_0700 = datetime(2026, 11, 24, 7, 0, tzinfo=shanghai)
        mon_0659 = datetime(2026, 11, 23, 6, 59, tzinfo=shanghai)  # Belongs to Sunday (7)!
        sun_2330 = datetime(2026, 11, 22, 23, 30, tzinfo=shanghai)

        self.assertIsNone(match_schedule_period(sched, mon_2259))
        self.assertEqual(match_schedule_period(sched, mon_2300)["id"], "mon-night")
        self.assertEqual(match_schedule_period(sched, tue_0659)["id"], "mon-night")
        self.assertIsNone(match_schedule_period(sched, tue_0700))  # Half-open [start, end)

        # Monday 06:59 matches sun-night-free (previous_weekday=7), NOT mon-night!
        matched_mon_early = match_schedule_period(sched, mon_0659)
        self.assertEqual(matched_mon_early["id"], "sun-night-free")
        self.assertEqual(matched_mon_early["multiplier"], 0.0)
        self.assertEqual(match_schedule_period(sched, sun_2330)["id"], "sun-night-free")

        # UTC ISO string converts accurately into Asia/Shanghai (UTC 15:30 = CST 23:30)
        self.assertEqual(match_schedule_period(sched, "2026-11-23T15:30:00Z")["id"], "mon-night")

    def test_weekly_overlap_validation_catches_cross_midnight_and_sunday_wrap_conflicts(self):
        # Adjacent periods [07:00, 23:00) and [23:00, 07:00) do NOT overlap -> valid!
        adjacent = normalize_schedule(
            {
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "periods": [
                    {"days": [1, 2, 3, 4, 5, 6, 7], "all_day": False, "start": "07:00", "end": "23:00", "mode": "multiplier", "multiplier": 1.0},
                    {"days": [1, 2, 3, 4, 5, 6, 7], "all_day": False, "start": "23:00", "end": "07:00", "mode": "multiplier", "multiplier": 0.5},
                ],
            },
            rule_validator=validate_pricing_rule,
        )
        self.assertEqual(len(adjacent["periods"]), 2)

        # Sunday 23:00-07:00 wraps into Monday 00:00-07:00 -> overlaps Monday 06:00-09:00!
        with self.assertRaises(PricingScheduleError) as ctx:
            normalize_schedule(
                {
                    "enabled": True,
                    "timezone": "Asia/Shanghai",
                    "periods": [
                        {"days": [7], "all_day": False, "start": "23:00", "end": "07:00", "mode": "multiplier", "multiplier": 0.5},
                        {"days": [1], "all_day": False, "start": "06:00", "end": "09:00", "mode": "multiplier", "multiplier": 0.8},
                    ],
                },
                rule_validator=validate_pricing_rule,
            )
        self.assertEqual(ctx.exception.code, "pricing_schedule_overlap")

        # Same-day overlap rejected
        with self.assertRaises(PricingScheduleError) as ctx2:
            normalize_schedule(
                {
                    "enabled": True,
                    "timezone": "UTC",
                    "periods": [
                        {"days": [3], "all_day": True, "mode": "multiplier", "multiplier": 0.5},
                        {"days": [3], "all_day": False, "start": "10:00", "end": "12:00", "mode": "multiplier", "multiplier": 0.2},
                    ],
                },
                rule_validator=validate_pricing_rule,
            )
        self.assertEqual(ctx2.exception.code, "pricing_schedule_overlap")

    def test_missing_timestamp_never_uses_now_and_override_validates_nested_rules(self):
        sched = normalize_schedule(
            {
                "enabled": True,
                "timezone": "UTC",
                "periods": [
                    {
                        "id": "weekend-flat",
                        "days": [6, 7],
                        "all_day": True,
                        "mode": "override",
                        "override": {"mode": "per_turn", "currency": "USD", "price": 0.005},
                    }
                ],
            },
            rule_validator=validate_pricing_rule,
        )
        self.assertIsNone(match_schedule_period(sched, None))
        self.assertIsNone(match_schedule_period(sched, ""))
        self.assertIsNone(match_schedule_period(sched, "not-a-timestamp"))
        # Saturday 2026-11-21T12:00:00Z matches!
        matched = match_schedule_period(sched, datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(matched["id"], "weekend-flat")
        self.assertEqual(matched["override"]["mode"], "per_turn")

        # Invalid weekday (bool, 0, 8) rejected
        with self.assertRaises(PricingScheduleError):
            normalize_schedule(
                {"periods": [{"days": [True], "all_day": True, "mode": "multiplier", "multiplier": 1.0}]},
                rule_validator=validate_pricing_rule,
            )
        with self.assertRaises(PricingScheduleError):
            normalize_schedule(
                {"periods": [{"days": [0], "all_day": True, "mode": "multiplier", "multiplier": 1.0}]},
                rule_validator=validate_pricing_rule,
            )


if __name__ == "__main__":
    unittest.main()
