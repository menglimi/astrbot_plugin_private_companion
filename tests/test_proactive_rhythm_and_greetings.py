"""T38 rhythm (burst, hourly curve, tiers, closing silence) and T40 local anchors.

Every value under test is a registered field of the shared ``proactive.contact``
group; the tests drive the real contact scheduler, the durable task ledger and
the synthetic sender, and never open a platform connection.
"""

from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from companion_v2.configuration.models import ConfigurationError
from companion_v2.proactive import rhythm
from companion_v2.proactive.configuration import (CURVE_CEILING, CURVE_FLOOR, DEFAULT_HOUR_CURVE, GROUP,
                                                  configuration_fields, curve_text, hour_activity_curve)
from companion_v2.proactive.greetings import ANCHOR_PURPOSE, INSOMNIA_PURPOSE
from companion_v2.proactive.preferences import ContactPreferences
from tests.contact_schedule_support import ContactScheduleFixture


class StubContext:
    key = "stub-window-key"


def stub_values(**changes):
    values = {"proactive_hour_activity_enabled": False, "proactive_hour_activity_curve": DEFAULT_HOUR_CURVE,
              "quiet_timezone": "UTC", "proactive_intensity_preset": "off", "interval_seconds": 600,
              "idle_seconds": 600, "enable_proactive_burst": False, "proactive_burst_max_messages": 2,
              "proactive_intensity_body_chars": 0, "proactive_burst_gap_min_seconds": 45,
              "proactive_burst_gap_max_seconds": 180, "unanswered_slowdown_start": 1,
              "unanswered_max_interval_multiplier": 2.2}
    return {**values, **changes}


def stub_preferences(**changes):
    """The real preference policy object, backed by explicit values and no snapshot."""
    return ContactPreferences(None, stub_values(**changes), datetime(2026, 9, 21, tzinfo=timezone.utc))


class RhythmConfigurationTests(unittest.TestCase):
    def fields(self):
        return {field.field_id: field for field in configuration_fields()}

    def test_rhythm_and_anchor_fields_are_registered_with_bounded_defaults(self):
        fields = self.fields()
        for name, default in (("enable_proactive_burst", False), ("proactive_intensity_preset", "off"),
                              ("proactive_hour_activity_enabled", False),
                              ("proactive_hour_activity_curve", curve_text(DEFAULT_HOUR_CURVE)),
                              ("enable_daily_greetings", False), ("allow_insomnia_night_message", False),
                              ("insomnia_night_start", "23:00"), ("insomnia_night_end", "06:00"),
                              ("insomnia_require_sleep_state", False), ("greeting_morning_window", "07:45-10:20"),
                              ("greeting_noon_window", "12:05-13:35"), ("greeting_evening_window", "20:10-21:20"),
                              ("proactive_burst_max_messages", 2), ("proactive_burst_gap_min_seconds", 45),
                              ("proactive_burst_gap_max_seconds", 180), ("proactive_closing_grace_minutes", 45),
                              ("proactive_closing_contacts_threshold", 3), ("proactive_intensity_body_chars", 0),
                              ("greeting_idle_minutes", 30), ("insomnia_night_daily_limit", 1),
                              ("proactive_share_probability", 100)):
            self.assertIn(GROUP + "." + name, fields)
            self.assertEqual(fields[GROUP + "." + name].default, default, name)
        burst = fields[GROUP + ".proactive_burst_max_messages"]
        self.assertEqual((burst.minimum, burst.maximum), (2, 3))
        with self.assertRaises(ConfigurationError):
            burst.validate(4)
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".proactive_closing_contacts_threshold"].validate(0)
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".proactive_intensity_preset"].validate("turbo")
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".greeting_morning_window"].validate("10:00-07:00")
        with self.assertRaises(ConfigurationError):
            fields[GROUP + ".insomnia_night_start"].validate("23:00:00")
        self.assertEqual(fields[GROUP + ".proactive_intensity_preset"].validate("standard"), "balanced")

    def test_hour_curve_parsing_clamps_and_degrades_without_failing(self):
        self.assertEqual(len(hour_activity_curve(DEFAULT_HOUR_CURVE)), 24)
        self.assertEqual(hour_activity_curve("1,2,3"), hour_activity_curve(DEFAULT_HOUR_CURVE))
        clamped = hour_activity_curve(",".join(["99"] + ["0"] * 23))
        self.assertEqual((clamped[0], clamped[1]), (CURVE_CEILING, CURVE_FLOOR))
        self.assertEqual(curve_text("oops" + ",1" * 23).split(",")[0], "1.00")

    def test_intensity_tiers_only_scale_configured_values(self):
        plain = stub_preferences()
        self.assertEqual(rhythm.interval_seconds(plain, unanswered=0), 600)
        self.assertEqual(rhythm.idle_seconds(plain), 600)
        self.assertEqual(rhythm.burst_limit(plain), 1)
        self.assertEqual(rhythm.body_chars(plain), 0)
        live = stub_preferences(proactive_intensity_preset="live", enable_proactive_burst=True,
                                proactive_burst_max_messages=2)
        self.assertEqual(rhythm.interval_seconds(live, unanswered=0), 120)
        self.assertEqual(rhythm.idle_seconds(live), 60)
        self.assertEqual(rhythm.burst_limit(live), 2)
        self.assertEqual(rhythm.body_chars(live), 400)
        explicit = stub_preferences(proactive_intensity_preset="live", enable_proactive_burst=True,
                                    proactive_burst_max_messages=3, proactive_intensity_body_chars=120)
        self.assertEqual(rhythm.burst_limit(explicit), 3)
        self.assertEqual(rhythm.body_chars(explicit), 120)
        # An unknown stored tier degrades to the unmodified grid, never to a tighter or freer one.
        unknown = stub_preferences(proactive_intensity_preset="mystery")
        self.assertEqual(rhythm.interval_seconds(unknown, unanswered=0), 600)
        self.assertEqual(rhythm.idle_seconds(unknown), 600)
        self.assertEqual(rhythm.body_chars(unknown), 0)

    def test_enabled_hour_curve_weights_slots_and_never_flaps(self):
        context = StubContext()
        curve = ",".join("0.05" if hour == 3 else "1.00" for hour in range(24))
        preferences = stub_preferences(proactive_hour_activity_enabled=True,
                                      proactive_hour_activity_curve=curve)
        held = rhythm.ContactRhythm(None)
        quiet_base = datetime(2026, 9, 21, 3, tzinfo=timezone.utc)
        busy_base = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
        quiet = [held.hour_allows(context, preferences, quiet_base + timedelta(seconds=14 * index))
                 for index in range(200)]
        busy = [held.hour_allows(context, preferences, busy_base + timedelta(seconds=14 * index))
                for index in range(200)]
        self.assertTrue(all(busy))
        self.assertLess(sum(quiet), len(busy))
        # The same slot answers the same way, so a restart cannot re-roll the draw.
        self.assertEqual(held.hour_allows(context, preferences, quiet_base),
                         held.hour_allows(context, preferences, quiet_base))
        self.assertTrue(held.hour_allows(context, stub_preferences(), quiet_base))

    def test_current_local_hour_uses_the_original_slot_draw_during_long_intervals(self):
        context = StubContext()
        preferences = stub_preferences(proactive_hour_activity_enabled=True, quiet_timezone="Asia/Shanghai",
            proactive_hour_activity_curve=",".join("0.05" if hour == 5 else "1.00" for hour in range(24)))
        held = rhythm.ContactRhythm(None)
        opened = datetime(2026, 9, 21, 4, tzinfo=timezone.utc)  # Local noon.
        overnight = datetime(2026, 9, 21, 21, 12, tzinfo=timezone.utc)  # Local 05:12.
        with patch.object(rhythm, "sample", return_value=0.5) as draw:
            self.assertTrue(held.hour_allows(context, preferences, opened))
            for seconds in (0, 1, 15, 30):
                self.assertFalse(held.hour_allows(context, preferences, opened,
                    observed_at=overnight + timedelta(seconds=seconds)))
            self.assertTrue(held.hour_allows(context, preferences, opened,
                observed_at=overnight + timedelta(hours=5)))
        self.assertEqual(len({tuple(call.args[0]) for call in draw.call_args_list}), 1)


class RhythmSchedulingTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def records(self, app):
        return [json.loads(row["record_json"]) for row in self.contacts(app)]

    async def test_burst_round_adds_one_bounded_follow_up_then_closes(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"enable_proactive_burst": True, "proactive_burst_max_messages": 2,
                            "proactive_burst_gap_min_seconds": 60, "proactive_burst_gap_max_seconds": 60,
                            "proactive_closing_contacts_threshold": 2, "proactive_closing_grace_minutes": 120,
                            "max_contacts_24h": 5, "dedup_enabled": False,
                            # A burst continues chat, not a competing relationship purpose.
                            **{purpose + ".enabled": False for purpose in
                               ("affection", "care", "play", "repair", "invitation")}}).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.assertEqual(self.records(app)[0]["purpose"], "chat")
        self.advance(app, 70)
        await self.automatic_delivery(app, count=2)
        second = self.records(app)[1]
        self.assertEqual(second["purpose"], "chat")
        self.assertEqual(second["automatic_origin"]["burst_event"]["index"], 1)
        self.assertEqual(second["automatic_origin"]["burst_event"]["limit"], 2)
        self.assertEqual(second["automatic_origin"]["burst_event"]["gap_seconds"], 60)
        self.assertEqual(len(self.contact_calls()), 2)
        # The round stops at its configured length and the closing silence holds the slot.
        self.advance(app, 1200)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contacts(app)), 2)

    async def test_burst_is_off_by_default(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"proactive_burst_gap_min_seconds": 60, "proactive_burst_gap_max_seconds": 60,
                            "max_contacts_24h": 5}).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.advance(app, 70)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contacts(app)), 1)

    async def test_closing_silence_holds_the_next_voluntary_slot_then_releases_it(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"proactive_closing_contacts_threshold": 1,
                            "proactive_closing_grace_minutes": 30, "max_contacts_24h": 5,
                            "dedup_enabled": False}).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.advance(app, 120)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contacts(app)), 1)
        self.advance(app, 1800)
        await self.automatic_delivery(app, count=2)

    async def test_body_length_hint_reaches_the_contact_request(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"proactive_intensity_body_chars": 200}).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.assertIn("200 字以内", self.contact_calls()[0].original.instruction)

    async def test_morning_anchor_uses_local_time_and_never_repeats_in_one_day(self):
        host, _ = await self.scheduled()
        app = host.application
        # NOW is 2026-09-21T00:00Z: 08:00 in the default Asia/Shanghai window.
        self.settings(app, {"enable_daily_greetings": True, "greeting_idle_minutes": 0,
                            "max_contacts_24h": 5, "dedup_enabled": False}).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        record = self.records(app)[0]
        self.assertEqual(record["purpose"], ANCHOR_PURPOSE)
        marker = record["automatic_origin"]["greeting_event"]
        self.assertEqual((marker["kind"], marker["anchor"]), ("day_anchor", "morning_greeting"))
        self.assertEqual(marker["local_date"], "2026-09-21")
        self.assertEqual(marker["timezone"], "Asia/Shanghai")
        self.advance(app, 600)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contacts(app)), 1)
        # The next local day opens a new anchor, never a replay of this one.
        self.advance(app, 24 * 3600)
        await self.automatic_delivery(app, count=2)
        self.assertEqual(self.records(app)[1]["automatic_origin"]["greeting_event"]["local_date"], "2026-09-22")

    async def test_anchor_idle_gate_postpones_the_greeting(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"enable_daily_greetings": True, "greeting_idle_minutes": 120,
                            "chat.enabled": False, "max_contacts_24h": 5}).unwrap()
        self.advance(app, 600)
        app.contact_schedule.advance()
        self.assertEqual(self.contacts(app), ())
        self.assertEqual(self.contact_calls(), [])

    async def test_quiet_hours_block_the_anchor_and_the_night_message(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"enable_daily_greetings": True, "greeting_idle_minutes": 0,
                            "allow_insomnia_night_message": True, "chat.enabled": False,
                            "quiet_enabled": True, "quiet_timezone": "Asia/Shanghai",
                            "quiet_start": "00:00", "quiet_end": "23:59", "max_contacts_24h": 5}).unwrap()
        self.advance(app, 600)
        app.contact_schedule.advance()
        self.assertEqual(self.contacts(app), ())
        self.assertEqual(self.contact_calls(), [])

    async def test_insomnia_window_is_explicit_bounded_and_quiet_hours_aware(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"allow_insomnia_night_message": True, "chat.enabled": False,
                            "insomnia_night_daily_limit": 2, "quiet_timezone": "UTC",
                            "quiet_enabled": False, "max_contacts_24h": 5, "dedup_enabled": False}).unwrap()
        # 23:30 local opens the first half of the 23:00-06:00 night.
        self.advance(app, 23 * 3600 + 1800)
        await self.automatic_delivery(app)
        first = self.records(app)[0]
        self.assertEqual(first["purpose"], INSOMNIA_PURPOSE)
        marker = first["automatic_origin"]["greeting_event"]
        self.assertEqual((marker["kind"], marker["slot"], marker["slot_limit"]), ("insomnia_night", 0, 2))
        self.assertEqual(marker["night_key"], "2026-09-21")
        # The second half of the same night may produce exactly one more message.
        self.advance(app, 4 * 3600)
        await self.automatic_delivery(app, count=2)
        second = self.records(app)[1]["automatic_origin"]["greeting_event"]
        self.assertEqual((second["slot"], second["night_key"]), (1, "2026-09-21"))
        self.advance(app, 3600)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contacts(app)), 2)

    async def test_insomnia_limit_zero_disables_the_night_window(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"allow_insomnia_night_message": True, "chat.enabled": False,
                            "insomnia_night_daily_limit": 0, "quiet_timezone": "UTC",
                            "quiet_enabled": False, "max_contacts_24h": 5}).unwrap()
        self.advance(app, 23 * 3600 + 1800)
        app.contact_schedule.advance()
        self.assertEqual(self.contacts(app), ())

    async def test_share_probability_suppresses_only_role_shares(self):
        host, _ = await self.scheduled()
        app = host.application
        context, held = self.contact_context(), app.contact_schedule.rhythm
        self.settings(app, {"proactive_share_probability": 100}).unwrap()
        self.assertTrue(held.share_allowed(context, self.preferences(app, context), "share-slot", purpose="life"))
        self.settings(app, {"proactive_share_probability": 0}).unwrap()
        preferences = self.preferences(app, context)
        self.assertFalse(held.share_allowed(context, preferences, "share-slot", purpose="life"))
        self.assertFalse(held.share_allowed(context, preferences, "share-slot", purpose="creation"))
        self.assertTrue(held.share_allowed(context, preferences, "reminder-slot", purpose="reminder"))

    async def test_sleep_hold_defers_discretionary_contacts_while_exempting_events_and_awake_grace(self):
        from companion_v2.role_work.life import configuration as life_configuration
        from companion_v2.role_work.life import rest_gate
        from companion_v2.role_work.life.configuration import LifeConfigurationOwner

        host, _ = await self.scheduled()
        app = host.application
        context = self.contact_context()
        owner = LifeConfigurationOwner(app.runtime)
        target = life_configuration.target_for(context)
        fields = life_configuration.configuration_fields()
        snapshot = owner.read(target, fields)
        owner.write(target, fields, changes={
            life_configuration.GROUP + ".enable_rest_reply_simulation": True,
            life_configuration.GROUP + ".rest_reply_active_windows": "",
            life_configuration.GROUP + ".rest_reply_awake_grace_minutes": 30,
        }, reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())
        self.grant(context.actor, context.scope, "role.life.run")
        self.grant(context.actor, context.scope, "role.life.read")
        routine = app.life_runtime.bind(context, "原角色人格提示")
        sleep = routine.start_sleep("rest-hold-test", 15)
        now = self.clock.now()
        wake_at = now + timedelta(minutes=15)

        self.assertEqual(app.contact_schedule._sleep_hold(context, {}, now), wake_at)
        self.assertIsNone(app.contact_schedule._sleep_hold(context, {"calendar_event": {"kind": "commitment"}}, now))
        self.assertIsNone(app.contact_schedule._sleep_hold(
            context, {"greeting_event": {"kind": "insomnia_night"}}, now))

        settings = rest_gate.read_settings(app.runtime, context)
        rest_gate.save(app.runtime, context, rest_gate.mark_awake(
            rest_gate.document(app.runtime, context), now=now, settings=settings, sleep=sleep.activity_id))
        self.assertIsNone(app.contact_schedule._sleep_hold(context, {}, now))

class RhythmUndeliveredCandidateTests(unittest.TestCase):
    """A sealed candidate without a delivered receipt must never open a burst."""

    class Runtime:
        def read_one(self, statement, parameters):
            return {"contact_id": "contact-1",
                    "created_at": "2026-09-21T00:00:00+00:00",
                    "record_json": json.dumps({"purpose": "chat"}),
                    "delivered_at": None}

    def policy(self):
        return stub_preferences(enable_proactive_burst=True, proactive_burst_max_messages=2,
                                proactive_burst_gap_min_seconds=60, proactive_burst_gap_max_seconds=60,
                                enabled=True, quiet_enabled=False, pause_until=None,
                                quiet_start="00:00", quiet_end="00:00",
                                **{"chat.enabled": True, "chat.pause_until": None})

    def test_undelivered_candidate_keeps_no_delivery_time(self):
        instance = rhythm.ContactRhythm(self.Runtime())
        last = instance.last_contact(StubContext())
        self.assertIsNotNone(last)
        self.assertIsNone(last["delivered_at"])

    def test_undelivered_candidate_does_not_open_a_burst_slot(self):
        instance = rhythm.ContactRhythm(self.Runtime())
        last = instance.last_contact(StubContext())
        at = datetime(2026, 9, 21, 0, 0, 30, tzinfo=timezone.utc)
        self.assertIsNone(instance.burst_slot(
            StubContext(), self.policy(), at,
            interaction={"contacts_since_last_input": 1, "unresolved_contacts": 0},
            last_contact=last))

    def test_undelivered_candidate_cannot_validate_a_burst_marker(self):
        instance = rhythm.ContactRhythm(self.Runtime())
        marker = {"index": 1, "limit": 2, "gap_seconds": 60,
                  "opened_at": "2026-09-21T00:01:00+00:00",
                  "expires_at": "2026-09-21T00:02:00+00:00",
                  "origin_contact_id": "contact-1"}
        at = datetime(2026, 9, 21, 0, 1, 30, tzinfo=timezone.utc)
        self.assertFalse(instance.validate_burst(
            StubContext(), self.policy(), marker, at,
            interaction={"contacts_since_last_input": 1, "unresolved_contacts": 0}))
