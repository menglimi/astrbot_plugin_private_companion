import builtins
from importlib.util import module_from_spec, spec_from_file_location
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from companion_v2.conversation.date_context import (period_label, project_interaction_gap, project_local_date,
                                                    project_personal_dates)
from companion_v2.conversation import date_context
from companion_v2.agenda.annual_calendar import SCHEMA, parse_annual_calendar


class LocalDateContextTests(unittest.TestCase):
    def test_projects_date_and_weekday_in_explicit_timezone(self):
        result = project_local_date(datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc), "Asia/Shanghai")
        self.assertEqual(result["local_date"], "2026-09-25")
        self.assertEqual(result["weekday"], "星期五")
        self.assertEqual((result["local_time"], result["period"]), ("00:30", "凌晨"))
        self.assertEqual(result["timezone"], "Asia/Shanghai")

    def test_period_label_is_one_vocabulary_for_every_prompt_path(self):
        self.assertEqual([period_label(hour) for hour in (0, 5, 8, 11, 13, 18, 22, 23)],
                         ["凌晨", "早上", "上午", "中午", "下午", "晚上", "夜里", "夜里"])

    def test_calendar_instruction_does_not_compress_real_world_durations(self):
        self.assertIn("elapsed_minutes", date_context.CALENDAR_INSTRUCTION)
        self.assertIn("煮饭", date_context.CALENDAR_INSTRUCTION)
        self.assertIn("不能只隔一两分钟", date_context.CALENDAR_INSTRUCTION)

    def test_fixed_holiday_name_is_a_calendar_label(self):
        result = project_local_date(datetime(2026, 5, 1, 4, tzinfo=timezone.utc), "Asia/Shanghai")
        self.assertEqual(result["fixed_gregorian_holiday"], "劳动节")

    def test_holiday_provider_and_weekend_fallback_are_explicit(self):
        provider = SimpleNamespace(is_holiday=lambda day: day.isoformat() == "2026-10-01",
            get_holiday_detail=lambda _day: (True, "国庆节"), is_workday=lambda _day: True)
        with patch("companion_v2.conversation.date_context._chinese_calendar", provider):
            holiday = project_local_date(datetime(2026, 10, 1, 4, tzinfo=timezone.utc), "UTC")
            workday = project_local_date(datetime(2026, 10, 2, 4, tzinfo=timezone.utc), "UTC")
        self.assertEqual(holiday["holiday"], {"status": "available", "kind": "holiday",
                         "label": "国庆节", "source": "chinese_calendar"})
        self.assertEqual(workday["holiday"]["kind"], "workday")
        fallback = project_local_date(datetime(2026, 10, 3, 4, tzinfo=timezone.utc), "UTC",
                                      holiday_country="WEEKEND_ONLY")
        self.assertEqual(fallback["holiday"]["label"], "周末")

    def test_holiday_provider_out_of_range_degrades_to_weekday_fallback(self):
        def out_of_range(_day):
            raise NotImplementedError("holiday data is unavailable for this year")

        provider = SimpleNamespace(is_holiday=out_of_range, is_workday=lambda _day: False)
        with patch("companion_v2.conversation.date_context._chinese_calendar", provider):
            result = project_local_date(datetime(2027, 1, 1, 4, tzinfo=timezone.utc), "UTC")
        self.assertEqual(result["holiday"], {"status": "available", "kind": "workday",
                         "label": "工作日", "source": "weekday_fallback"})

    def test_configured_makeup_day_overrides_provider_and_almanac_has_content(self):
        provider = SimpleNamespace(is_holiday=lambda _day: False,
            get_holiday_detail=lambda _day: (False, ""), is_workday=lambda _day: False)
        calendar = parse_annual_calendar({"schema": SCHEMA, "year": 2026,
            "makeup_workdays": [{"date": "2026-09-25", "name": "调休上班"}]})
        with patch("companion_v2.conversation.date_context._chinese_calendar", provider):
            result = project_local_date(datetime(2026, 9, 25, 4, tzinfo=timezone.utc), "UTC", (calendar,),
                                        almanac_enabled=True)
        self.assertEqual(result["holiday"], {"status": "available", "kind": "makeup_workday",
                         "label": "调休上班", "source": "operator_configured_annual_calendar"})
        self.assertEqual(result["almanac"]["status"], "available")
        self.assertTrue(result["almanac"]["yi"])
        self.assertTrue(result["almanac"]["ji"])

    def test_missing_lunar_providers_degrade_without_affecting_gregorian_context(self):
        with patch.dict(sys.modules, {"lunardate": None, "lunarcalendar": None}):
            result = project_local_date(datetime(2026, 9, 24, 12, tzinfo=timezone.utc), "UTC")
        self.assertEqual(result["lunar_status"], "unavailable")
        self.assertIsNone(result["lunar_date"])
        self.assertEqual(result["local_date"], "2026-09-24")

    def test_unreadable_lunar_packages_preserve_the_gregorian_context(self):
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name in {"lunardate", "lunarcalendar"}:
                raise PermissionError("optional calendar package is unreadable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=unavailable):
            result = project_local_date(datetime(2026, 9, 24, 12, tzinfo=timezone.utc), "UTC")
        self.assertEqual(result["lunar_status"], "unavailable")
        self.assertIsNone(result["lunar_date"])
        self.assertEqual((result["local_date"], result["local_time"], result["period"]),
                         ("2026-09-24", "12:00", "中午"))

    def test_unreadable_primary_lunar_package_uses_the_available_fallback(self):
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name == "lunardate":
                raise PermissionError("primary calendar package is unreadable")
            return original_import(name, *args, **kwargs)

        fallback = ModuleType("lunarcalendar")
        fallback.Solar = lambda year, month, day: (year, month, day)
        fallback.Converter = SimpleNamespace(Solar2Lunar=lambda _day: SimpleNamespace(
            year=2026, month=8, day=14, isleap=False))
        with patch.dict(sys.modules, {"lunarcalendar": fallback}), \
                patch("builtins.__import__", side_effect=unavailable):
            result = project_local_date(datetime(2026, 9, 24, 12, tzinfo=timezone.utc), "UTC")
        self.assertEqual(result["lunar_status"], "available")
        self.assertEqual(result["lunar_date"], "农历2026年八月十四")

    def test_unreadable_holiday_package_does_not_prevent_module_import(self):
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name == "chinese_calendar":
                raise PermissionError("holiday package is unreadable")
            return original_import(name, *args, **kwargs)

        spec = spec_from_file_location("companion_v2.conversation.date_context_import_fixture", date_context.__file__)
        isolated = module_from_spec(spec)
        with patch("builtins.__import__", side_effect=unavailable):
            spec.loader.exec_module(isolated)
        self.assertIsNone(isolated._chinese_calendar)
        result = isolated.project_local_date(datetime(2026, 10, 3, 4, tzinfo=timezone.utc), "UTC",
                                              lunar_enabled=False)
        self.assertEqual(result["holiday"]["source"], "weekday_fallback")
        self.assertEqual(result["holiday"]["label"], "周末")

    def test_unreadable_holiday_data_retains_the_weekday_fallback(self):
        def unreadable(_day):
            raise OSError("optional holiday data could not be read")

        provider = SimpleNamespace(is_holiday=unreadable)
        with patch("companion_v2.conversation.date_context._chinese_calendar", provider):
            result = project_local_date(datetime(2026, 10, 3, 4, tzinfo=timezone.utc), "UTC")
        self.assertEqual(result["holiday"]["source"], "weekday_fallback")
        self.assertEqual(result["holiday"]["label"], "周末")

    def test_lunar_calendar_fallback_is_used_when_primary_provider_is_missing(self):
        lunarcalendar = ModuleType("lunarcalendar")

        class Solar:
            def __init__(self, year, month, day):
                self.year, self.month, self.day = year, month, day

        def solar_to_lunar(solar):
            return SimpleNamespace(year=solar.year, month=1, day=1, isleap=True)

        lunarcalendar.Solar = Solar
        lunarcalendar.Converter = SimpleNamespace(Solar2Lunar=solar_to_lunar)
        with patch.dict(sys.modules, {"lunardate": None, "lunarcalendar": lunarcalendar}):
            result = project_local_date(datetime(2026, 9, 24, 12, tzinfo=timezone.utc), "UTC")
        self.assertEqual(result["lunar_status"], "available")
        self.assertEqual(result["lunar_date"], "农历2026年闰正月初一")

    def test_lunar_provider_year_boundaries_and_out_of_range_degradation(self):
        lunardate = ModuleType("lunardate")

        def from_solar_date(year, _month, _day):
            if not 1900 <= year <= 2100:
                raise ValueError("year outside provider data")
            return SimpleNamespace(year=year, month=1, day=1, isLeapMonth=False)

        lunardate.LunarDate = SimpleNamespace(fromSolarDate=from_solar_date)
        with patch.dict(sys.modules, {"lunardate": lunardate, "lunarcalendar": None}):
            first_supported = project_local_date(datetime(1900, 1, 1, tzinfo=timezone.utc), "UTC")
            last_supported = project_local_date(datetime(2100, 12, 31, tzinfo=timezone.utc), "UTC")
            before_supported = project_local_date(datetime(1899, 12, 31, tzinfo=timezone.utc), "UTC")
            after_supported = project_local_date(datetime(2101, 1, 1, tzinfo=timezone.utc), "UTC")
        self.assertEqual(first_supported["lunar_status"], "available")
        self.assertEqual(last_supported["lunar_status"], "available")
        self.assertEqual(before_supported["lunar_status"], "unavailable")
        self.assertEqual(after_supported["lunar_status"], "unavailable")
        self.assertEqual(before_supported["local_date"], "1899-12-31")
        self.assertEqual(after_supported["local_date"], "2101-01-01")

    def test_naive_datetime_and_invalid_timezone_are_rejected(self):
        with self.assertRaises(ValueError):
            project_local_date(datetime(2026, 9, 25, 10))
        with self.assertRaises(ValueError):
            project_local_date(datetime(2026, 9, 25, 10, tzinfo=timezone.utc), "Invalid/Zone")

    def test_configured_annual_calendar_lists_only_explicit_local_dates(self):
        calendar = parse_annual_calendar({"schema": SCHEMA, "year": 2026,
            "holidays": [{"name": "合成假期", "start": "2026-09-25", "end": "2026-09-25"}],
            "solar_terms": [{"date": "2026-09-25", "name": "合成节气"}]})
        result = project_local_date(datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc),
                                    "Asia/Shanghai", (calendar,))
        self.assertEqual(result["configured_annual_calendar"], {
            "basis": "operator_configured_annual_calendar", "year_configured": True,
            "listed_today": {"holiday": "合成假期", "solar_terms": ["合成节气"]},
            "upcoming_solar_terms": []})
        unlisted = project_local_date(datetime(2026, 9, 25, 16, 30, tzinfo=timezone.utc),
                                      "Asia/Shanghai", (calendar,))
        self.assertEqual(unlisted["configured_annual_calendar"]["listed_today"], {})
        missing_year = project_local_date(datetime(2027, 9, 24, 16, 30, tzinfo=timezone.utc),
                                          "Asia/Shanghai", (calendar,))
        self.assertFalse(missing_year["configured_annual_calendar"]["year_configured"])
        self.assertEqual(missing_year["configured_annual_calendar"]["listed_today"], {})

    def test_solar_term_lookahead_uses_only_listed_dates_across_years(self):
        current = parse_annual_calendar({"schema": SCHEMA, "year": 2026})
        next_year = parse_annual_calendar({"schema": SCHEMA, "year": 2027,
            "solar_terms": [{"date": "2027-01-01", "name": "合成节气"}]})
        at = datetime(2026, 12, 30, 12, tzinfo=timezone.utc)
        projected = project_local_date(at, "UTC", (current, next_year))["configured_annual_calendar"]
        self.assertEqual(projected["upcoming_solar_terms"], [
            {"date": "2027-01-01", "days_ahead": 2, "names": ["合成节气"]}])
        missing_year = project_local_date(at, "UTC", (current,))["configured_annual_calendar"]
        self.assertEqual(missing_year["upcoming_solar_terms"], [])

    def test_project_interaction_gap_classifies_elapsed_intervals_by_local_calendar(self):
        now = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)  # 10:00 in Asia/Shanghai
        self.assertEqual(
            project_interaction_gap(now, datetime(2026, 9, 25, 1, 45, tzinfo=timezone.utc), "Asia/Shanghai"),
            {"band": "in_flow", "elapsed_minutes": 15, "days_apart": 0},
        )
        self.assertEqual(
            project_interaction_gap(now, datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc), "Asia/Shanghai"),
            {"band": "same_day_pause", "elapsed_minutes": 240, "days_apart": 0},
        )
        self.assertEqual(
            project_interaction_gap(now, datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc), "Asia/Shanghai"),
            {"band": "overnight", "elapsed_minutes": 660, "days_apart": 1},
        )
        self.assertEqual(
            project_interaction_gap(now, datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc), "Asia/Shanghai"),
            {"band": "days_apart", "elapsed_minutes": 3540, "days_apart": 3},
        )

    def test_explicit_personal_birthdays_are_projected_with_their_owner(self):
        at = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        result = project_personal_dates(at, "Asia/Shanghai", role_birthday="設定為 10月24日",
                                        user_birthday="2001-10-24")
        self.assertEqual(result["entries"], [
            {"subject": "role", "label": "角色生日", "date": "2026-10-24",
             "days_until": 1, "calendar": "gregorian"},
            {"subject": "user", "label": "对方生日", "date": "2026-10-24",
             "days_until": 1, "calendar": "gregorian"},
        ])

    def test_anniversary_wording_stays_owned_and_lookahead_covers_configured_window(self):
        at = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        projected = project_personal_dates(
            at, "Asia/Shanghai", user_birthday="相识纪念日 10月27日", lookahead_days=30)
        self.assertEqual(projected["entries"], [{"subject": "user", "label": "对方纪念日",
            "date": "2026-10-27", "days_until": 4, "calendar": "gregorian"}])
        with self.assertRaises(ValueError):
            project_personal_dates(at, "Asia/Shanghai", user_birthday="10月27日", lookahead_days=31)

    def test_lunar_personal_birthday_and_ambiguous_text(self):
        at = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
        with patch("companion_v2.conversation.date_context.lunar_date_parts",
                   side_effect=lambda day: (2026, 9, 15, False) if day.isoformat() == "2026-10-24" else None):
            result = project_personal_dates(at, "Asia/Shanghai", user_birthday="农历九月十五")
        self.assertEqual(result["entries"], [{"subject": "user", "label": "对方生日",
            "date": "2026-10-24", "days_until": 1, "calendar": "lunar"}])
        self.assertEqual(project_personal_dates(at, "Asia/Shanghai",
            user_birthday="生日大概在秋天，具体日期不清楚")["entries"], [])


if __name__ == "__main__":
    unittest.main()
