"""Configured recurring dates through the original private contact path."""

from datetime import date, datetime, timedelta, timezone
import json
import unittest
from zoneinfo import ZoneInfo
from unittest.mock import patch

from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.proactive.configuration import ImportantDate, important_dates, local_time
from companion_v2.relationships import anniversaries
from companion_v2.relationships.profile import (
    GROUP as RELATIONSHIP_PROFILE_GROUP,
    RelationshipProfileConfigurationOwner,
    configuration_fields as relationship_profile_fields,
    target_for as relationship_profile_target_for,
)
from companion_v2.role_profile.configuration import (
    GROUP as ROLE_PROFILE_GROUP,
    RoleProfileConfigurationOwner,
    configuration_fields as role_profile_fields,
    target_for as role_profile_target_for,
)
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_role_tool_loop import answered


DATE_JSON = json.dumps([{"month": 9, "day": 21, "label": "共同纪念日"}], ensure_ascii=False)
LUNAR_JSON = json.dumps([{"calendar": "lunar", "month": 8, "day": 11, "label": "农历纪念日"}], ensure_ascii=False)


class DateContactTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def setup_date(self, *, enabled=True, dates=DATE_JSON):
        host, _ = await self.scheduled()
        app = host.application
        values = {"chat.enabled": False, "date_checkin_time": "08:01", "important_dates": dates}
        if enabled is not None:
            values["date_checkin_enabled"] = enabled
        self.settings(app, values).unwrap()
        return app

    def set_role_birthday(self, app, value):
        context = self.contact_context()
        owner = RoleProfileConfigurationOwner(app.runtime)
        target = role_profile_target_for(context)
        fields = role_profile_fields()
        snapshot = owner.read(target, fields)
        owner.write(target, fields, changes={ROLE_PROFILE_GROUP + ".birthday": value},
                    reset=(), expected_revision=snapshot.revision,
                    observed_at=self.clock.now())

    def set_user_birthday(self, app, value):
        context = self.contact_context()
        owner = RelationshipProfileConfigurationOwner(app.runtime)
        target = relationship_profile_target_for(context)
        fields = relationship_profile_fields()
        snapshot = owner.read(target, fields)
        owner.write(target, fields, changes={RELATIONSHIP_PROFILE_GROUP + ".user_birthday": value},
                    reset=(), expected_revision=snapshot.revision,
                    observed_at=self.clock.now())

    async def test_profile_birthdays_are_used_without_a_manual_date_list(self):
        app = await self.setup_date(dates="[]")
        self.set_role_birthday(app, "9月21日")
        self.set_user_birthday(app, "纪念日 9月21日")
        self.advance(app, 60)
        await self.automatic_delivery(app)
        payload = self.contact_calls()[0].original.context.materials[0].payload
        self.assertIn("角色档案中的生日或纪念日「角色生日」", payload)
        self.assertIn("当前私聊资料中的生日或纪念日「对方纪念日」", payload)
        marker = json.loads(self.contacts(app)[0]["record_json"])["automatic_origin"]["date_event"]
        self.assertEqual(marker["entries"], [
            {"basis": "profile_role_birthday", "label": "角色生日",
             "date": "2026-09-21", "calendar": "gregorian"},
            {"basis": "profile_relationship_date", "label": "对方纪念日",
             "date": "2026-09-21", "calendar": "gregorian"},
        ])
        rows = app.contact_schedule.date_events.upcoming(
            self.contact_context(), self.preferences(app), self.clock.now(), lookahead_days=7)
        self.assertEqual({row["label"]: row["days_until"] for row in rows},
                         {"角色生日": 0, "对方纪念日": 0})

    async def test_profile_date_lookahead_and_dashboard_projection_need_no_manual_json(self):
        app = await self.setup_date(dates="[]")
        self.settings(app, {"important_date_lookahead_days": 4}).unwrap()
        self.set_role_birthday(app, "9月24日")
        self.set_user_birthday(app, "纪念日 9月25日")
        preferences = self.preferences(app)
        events = app.contact_schedule.date_events
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        wall = local_time(preferences.values["date_checkin_time"])
        opening = datetime.combine(self.clock.now().astimezone(zone).date(), wall,
                                   tzinfo=zone).astimezone(timezone.utc)
        slot = events._slot(self.contact_context(), preferences, opening)
        self.assertIsNotNone(slot)
        self.assertEqual(slot[1]["entries"], [
            {"basis": "profile_role_birthday", "label": "角色生日",
             "date": "2026-09-24", "calendar": "gregorian", "days_until": 3},
            {"basis": "profile_relationship_date", "label": "对方纪念日",
             "date": "2026-09-25", "calendar": "gregorian", "days_until": 4},
        ])
        rows = events.upcoming(self.contact_context(), preferences, self.clock.now(),
                               lookahead_days=7)
        self.assertEqual([(row["label"], row["days_until"]) for row in rows],
                         [("角色生日", 3), ("对方纪念日", 4)])

    async def test_daily_reflection_can_remember_an_interaction_anniversary_without_configuration(self):
        app = await self.setup_date(dates="[]")
        created = anniversaries.remember(app.runtime, self.contact_context(), {
            "label": "第一次认真说出喜欢",
            "reason": "用户原话明确说出第一次告白。",
            "confidence": 0.92,
            "evidence_handle": "s1",
            "occurred_at": "2026-09-21T00:30:00+00:00",
            "month": 9,
            "day": 21,
            "timezone": "Asia/Shanghai",
        }, source_run_id="daily-reflection", at=self.clock.now())
        self.assertEqual(created["state"], "created")
        self.advance(app, 60)
        await self.automatic_delivery(app)
        payload = self.contact_calls()[0].original.context.materials[0].payload
        self.assertIn("每日反思记下的互动纪念日「第一次认真说出喜欢」", payload)
        marker = json.loads(self.contacts(app)[0]["record_json"])["automatic_origin"]["date_event"]
        self.assertEqual(marker["entries"], [{
            "basis": "auto_relationship_anniversary",
            "label": "第一次认真说出喜欢",
            "date": "2026-09-21",
            "calendar": "gregorian",
        }])
        rows = app.contact_schedule.date_events.upcoming(
            self.contact_context(), self.preferences(app), self.clock.now(), lookahead_days=7)
        self.assertEqual([(row["label"], row["days_until"]) for row in rows],
                         [("第一次认真说出喜欢", 0)])

    async def test_opt_in_and_valid_explicit_dates_are_required(self):
        app = await self.setup_date(enabled=None)
        self.assertFalse(self.preferences(app).values["date_checkin_enabled"])
        for value in ("not-json", '[{"month":2,"day":30,"label":"错"}]',
                      '[{"month":9,"day":21,"label":"重复"},{"month":9,"day":21,"label":"重复"}]'):
            self.assertFalse(self.settings(app, {"important_dates": value}).is_success)
        self.assertFalse(self.settings(app, {"date_checkin_time": "25:00"}).is_success)
        self.assertEqual(important_dates('[{"month":2,"day":29,"label":"闰日"}]'),
                         (ImportantDate("gregorian", 2, 29, "闰日"),))
        for value in ('[{"calendar":"lunar","month":13,"day":1,"label":"错"}]',
                      '[{"calendar":"lunar","month":8,"day":31,"label":"错"}]',
                      '[{"calendar":"lunar","month":8,"day":11,"leap_month":"true","label":"错"}]',
                      '[{"calendar":"gregorian","month":9,"day":21,"leap_month":true,"label":"错"}]'):
            self.assertFalse(self.settings(app, {"important_dates": value}).is_success)
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])

    async def test_configured_day_uses_greeting_once_across_restart(self):
        app = await self.setup_date()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        calls = self.contact_calls()
        self.assertEqual(len(calls), 1)
        request = calls[0].original
        self.assertIn("共同纪念日", request.context.materials[0].payload)
        self.assertIn("不证明对方生日", request.user_instruction)
        self.assertNotIn("不证明对方生日", request.instruction)
        record = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual(record["purpose"], "greeting")
        self.assertEqual(record["automatic_origin"]["date_event"]["entries"], [
            {"basis": "operator_configured_recurring_date", "label": "共同纪念日"}])
        await app.close()
        restarted = (await self.start(self.host(runtime=self.open_runtime()), 1, 1)).application
        restarted.contact_schedule.advance()
        self.assertEqual(len(self.contact_calls()), 1)

    async def test_fixed_holiday_is_labeled_without_implying_time_off(self):
        self.clock.advance(timedelta(days=10))
        app = await self.setup_date(dates="[]")
        self.advance(app, 60)
        await self.automatic_delivery(app)
        request = self.contact_calls()[0].original
        self.assertIn("国庆节", request.context.materials[0].payload)
        self.assertIn("不代表放假", request.context.materials[0].payload)
        record = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual(record["automatic_origin"]["date_event"]["entries"], [
            {"basis": "fixed_gregorian_holiday", "label": "国庆节"}])

    async def test_configured_lunar_date_uses_local_day_and_keeps_its_basis(self):
        app = await self.setup_date(dates=LUNAR_JSON)
        self.advance(app, 60)
        with patch("companion_v2.proactive.date_events.lunar_date_parts",
                   return_value=(2026, 8, 11, False)):
            await self.automatic_delivery(app)
        request = self.contact_calls()[0].original
        self.assertIn("旧版手动保留的农历日期", request.context.materials[0].payload)
        marker = json.loads(self.contacts(app)[0]["record_json"])["automatic_origin"]["date_event"]
        self.assertEqual(marker["entries"], [{"basis": "operator_configured_lunar_date",
            "label": "农历纪念日", "lunar_year": 2026, "leap_month": False}])

    async def test_lunar_leap_month_requires_explicit_match(self):
        dates = json.dumps([
            {"calendar": "lunar", "month": 8, "day": 11, "label": "普通八月"},
            {"calendar": "lunar", "month": 8, "day": 11, "leap_month": True, "label": "闰八月"},
        ], ensure_ascii=False)
        app = await self.setup_date(dates=dates)
        with patch("companion_v2.proactive.date_events.lunar_date_parts", return_value=(2026, 8, 11, True)):
            slot = app.contact_schedule.date_events._slot(
                self.contact_context(), self.preferences(app), self.clock.now() + timedelta(minutes=1))
        self.assertEqual(slot[1]["entries"], [{"basis": "operator_configured_lunar_date",
            "label": "闰八月", "lunar_year": 2026, "leap_month": True}])

    async def test_missing_lunar_calendar_never_guesses_a_match(self):
        app = await self.setup_date(dates=LUNAR_JSON)
        with patch("companion_v2.proactive.date_events.lunar_date_parts", return_value=None):
            slot = app.contact_schedule.date_events._slot(
                self.contact_context(), self.preferences(app), self.clock.now() + timedelta(minutes=1))
        self.assertIsNone(slot)

    async def test_config_change_during_generation_discards_old_greeting(self):
        app = await self.setup_date()

        async def change_date(envelope):
            if isinstance(envelope.original.event, BackgroundPromptTrigger):
                self.settings(app, {"important_dates": "[]"}).unwrap()
                return answered('{"decision":"send","text":"纪念日快乐"}')
            return answered("收到。")

        self.model_behavior = change_date
        self.advance(app, 60)
        await self.until(lambda: len(self.contact_calls()) == 1)
        await self.until(lambda: not app.proactive.active_handles)
        self.assertEqual(self.contacts(app), ())
        self.assertEqual(len(self.sent), 1)

    async def test_expired_date_window_is_not_replayed(self):
        app = await self.setup_date()
        self.clock.advance(timedelta(hours=2))
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])


class DateLookaheadTests(DateContactTests):
    """C2-5: an approaching date may be noticed before the day, never after it."""

    async def slot_days_before(self, app, *, days):
        """Evaluate the slot ``days`` local days *before* the configured date.

        The slot only exists inside a 45-minute window around the configured
        ``date_checkin_time``, so an offset has to land on that moment rather than on
        an arbitrary time of day. Offsets count backwards from the anniversary: a day
        after it is not "upcoming", it is a year away.
        """
        preferences = self.preferences(app)
        events = app.contact_schedule.date_events
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        wall = local_time(preferences.values["date_checkin_time"])
        anniversary = max(item for item in important_dates(preferences.values["important_dates"])
                          if item.calendar == "gregorian")
        year = self.clock.now().astimezone(zone).date().year
        target = date(year, anniversary.month, anniversary.day) - timedelta(days=days)
        opening = datetime.combine(target, wall, tzinfo=zone).astimezone(timezone.utc)
        return events._slot(self.contact_context(), preferences, opening)

    async def test_zero_lookahead_keeps_the_same_day_only_behaviour(self):
        # The default must not change anything: three days early is still nothing.
        app = await self.setup_date()
        self.assertEqual(self.preferences(app).values["important_date_lookahead_days"], 0)
        self.assertIsNone(await self.slot_days_before(app, days=3))
        self.assertIsNotNone(await self.slot_days_before(app, days=0))

    async def test_a_positive_lookahead_surfaces_an_upcoming_date(self):
        app = await self.setup_date()
        self.settings(app, {"important_date_lookahead_days": 5}).unwrap()
        slot = await self.slot_days_before(app, days=3)
        self.assertIsNotNone(slot)
        self.assertEqual(slot[1]["entries"], [
            {"basis": "operator_configured_recurring_date", "label": "共同纪念日",
             "days_until": 3}])

    async def test_the_window_is_bounded_on_both_sides(self):
        app = await self.setup_date()
        self.settings(app, {"important_date_lookahead_days": 3}).unwrap()
        self.assertIsNotNone(await self.slot_days_before(app, days=3))
        self.assertIsNone(await self.slot_days_before(app, days=4))

    async def test_the_lookahead_never_reaches_backwards(self):
        # A broad window must not turn yesterday's anniversary into "upcoming": the
        # next occurrence is a year away, so any sane ceiling keeps it out.
        app = await self.setup_date()
        self.settings(app, {"important_date_lookahead_days": 30}).unwrap()
        preferences = self.preferences(app)
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        wall = local_time(preferences.values["date_checkin_time"])
        anniversary = max(item for item in important_dates(preferences.values["important_dates"])
                          if item.calendar == "gregorian")
        year = self.clock.now().astimezone(zone).date().year
        target = date(year, anniversary.month, anniversary.day) + timedelta(days=1)
        opening = datetime.combine(target, wall, tzinfo=zone).astimezone(timezone.utc)
        self.assertIsNone(app.contact_schedule.date_events._slot(
            self.contact_context(), preferences, opening))

    async def test_a_same_day_entry_keeps_its_original_marker_shape(self):
        # The extra key belongs to upcoming entries only, so the day itself is
        # unchanged from the V1 shape.
        app = await self.setup_date()
        self.settings(app, {"important_date_lookahead_days": 5}).unwrap()
        slot = await self.slot_days_before(app, days=0)
        self.assertEqual(slot[1]["entries"], [
            {"basis": "operator_configured_recurring_date", "label": "共同纪念日"}])

    async def test_the_lookahead_is_a_bounded_registered_field(self):
        app = await self.setup_date()
        self.assertFalse(self.settings(app, {"important_date_lookahead_days": 31}).is_success)
        self.assertFalse(self.settings(app, {"important_date_lookahead_days": -1}).is_success)
        self.assertTrue(self.settings(app, {"important_date_lookahead_days": 30}).is_success)

    def test_the_forward_search_handles_short_months_and_leap_days(self):
        from datetime import date
        from companion_v2.proactive.date_events import _gregorian_days_until
        today = date(2026, 12, 28)
        self.assertEqual(_gregorian_days_until(today, 12, 31), 3)
        self.assertEqual(_gregorian_days_until(today, 1, 2), 5)     # crosses the year
        self.assertEqual(_gregorian_days_until(today, 12, 28), 0)   # today counts
        self.assertIsNone(_gregorian_days_until(today, 2, 30))      # a date that exists in no year
        leap = _gregorian_days_until(today, 2, 29)                  # reaches its real occurrence
        self.assertIsNotNone(leap)
        self.assertGreater(leap, 0)


if __name__ == "__main__":
    unittest.main()
