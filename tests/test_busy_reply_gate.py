"""Busy-hour reply gate: deferral, deadline safety, restart and proactive hold.

V1 delayed a passive reply with ``asyncio.sleep`` and then cancelled it when a
newer private message arrived. V2 expresses the same intent as a durable
``due_at`` deferral on the accepted batch, so these tests assert the durable
column and never a sleep. The proactive chain is filtered before any envelope is
enqueued, so a busy window can only stop a contact from being created; it can
never move one that already exists.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.configuration.models import ConfigLayer
from companion_v2.configuration.registry import FieldRegistry, ValueKind
from companion_v2.configuration.service import ConfigurationService
from companion_v2.conversation import busy_gate
from companion_v2.conversation.busy_gate import (BusyGateConfigurationOwner, Settings, busy_context,
                                                 busy_exempt_source, bypass_reason, configuration_fields,
                                                 defer_seconds, is_busy_text, keywords, proactive_defer_until,
                                                 validate_defer_seconds)
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import TaskError
from companion_v2.identity_policy.personas import RuntimeIdentity
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


QUIET = 0.6
TURN_DEADLINE = 180
DEADLINE_CAP = 900
BUSY_MINUTES = 120


def busy_plan(*, start, minutes=BUSY_MINUTES, title="项目会议", detail="", presence="",
              state="accepted", kind="role_plan", record_id="plan"):
    """One calendar record shaped exactly like ``CalendarService.command(create)``."""
    return {record_id: {"id": record_id, "revision": 1, "kind": kind, "state": state, "title": title,
                        "detail": detail, "presence": presence,
                        "schedule": {"anchor": start.isoformat(), "timezone_name": "UTC",
                                     "frequency": "once", "interval": 1, "count": None, "fold": 0,
                                     "gap_policy": "reject"},
                        "duration_minutes": minutes, "sources": [], "exceptions": {}, "accepted_by": [],
                        "changed_by": {}, "reminder_id": None}}


def gate_settings(**changes):
    return Settings.from_values({**busy_gate.DEFAULTS, **changes})


def plan_data(*, start, minutes=BUSY_MINUTES, title="项目会议", detail="", presence="", kind="role_plan"):
    return {"kind": kind, "title": title, "detail": detail, "presence": presence,
            "duration_minutes": minutes,
            "schedule": {"anchor": start.isoformat(), "timezone_name": "UTC", "frequency": "once",
                         "interval": 1, "count": None, "fold": 0, "gap_policy": "reject"}}


class BusyGateJudgementTests(unittest.TestCase):
    """The pure judgement: no host, no storage, no calendar."""

    def test_configuration_group_contract_and_defaults(self):
        fields = {field.field_id: field for field in configuration_fields()}
        self.assertEqual(len(fields), 7)
        for field in fields.values():
            self.assertEqual((field.group_id, field.consumer_id, field.owner_id),
                             (busy_gate.GROUP, busy_gate.GROUP, busy_gate.OWNER))
            self.assertEqual(field.layers, (ConfigLayer.ROLE, ConfigLayer.WINDOW))
        self.assertIs(fields[busy_gate.GROUP + ".busy_reply_gate_enabled"].kind, ValueKind.BOOLEAN)
        for name in ("busy_reply_min_delay_seconds", "busy_reply_max_delay_seconds",
                     "busy_reply_proactive_resume_buffer_minutes"):
            field = fields[busy_gate.GROUP + "." + name]
            self.assertIs(field.kind, ValueKind.INTEGER)
            self.assertEqual((field.minimum, field.maximum), busy_gate.LIMITS[name])
        for name in busy_gate.TEXT_FIELDS:
            self.assertIs(fields[busy_gate.GROUP + "." + name].kind, ValueKind.TEXT)
        defaults = gate_settings()
        self.assertFalse(defaults.busy_reply_gate_enabled)
        self.assertEqual((defaults.busy_reply_min_delay_seconds, defaults.busy_reply_max_delay_seconds,
                          defaults.busy_reply_proactive_resume_buffer_minutes), (60, 300, 10))
        self.assertIn("上课", defaults.busy_activity_keywords)
        self.assertIn("休息", defaults.busy_exclude_keywords)
        self.assertIn("救命", defaults.busy_urgent_keywords)
        for bad in ({"busy_reply_min_delay_seconds": 901},
                    {"busy_reply_proactive_resume_buffer_minutes": -1},
                    {"busy_reply_gate_enabled": "yes"},
                    {"busy_activity_keywords": ("上课",)}):
            with self.assertRaises(Exception, msg=bad):
                gate_settings(**bad)

    def test_keyword_text_accepts_json_and_separated_forms(self):
        self.assertEqual(keywords(" [\"上课\", \"开会\"] ", field="x"), ("上课", "开会"))
        self.assertEqual(keywords("上课,开会、值班", field="x"), ("上课", "开会", "值班"))
        self.assertEqual(keywords("", field="x"), ())
        self.assertEqual(keywords("上课 上课", field="x"), ("上课",))  # duplicates collapse
        for bad in ("{\"a\": 1}", "[\"上课\", 1]", "[" + "\"很长\"" + "x" * 40 + "]"):
            with self.assertRaises(Exception, msg=bad):
                keywords(bad, field="x")

    def test_busy_context_honours_the_switch_window_and_exclusions(self):
        disabled = busy_context(busy_plan(start=NOW).values(), now=NOW, settings=gate_settings())
        self.assertEqual(disabled["reason"], "disabled")
        settings = gate_settings(busy_reply_gate_enabled=True)
        active = busy_context(busy_plan(start=NOW).values(), now=NOW, settings=settings)
        self.assertTrue(active["busy"])
        self.assertEqual(active["until"], NOW + timedelta(minutes=BUSY_MINUTES))
        self.assertEqual(active["schedule"], "项目会议")
        self.assertFalse(busy_context(busy_plan(start=NOW + timedelta(minutes=5)).values(), now=NOW,
                                      settings=settings)["busy"])
        self.assertFalse(busy_context(busy_plan(start=NOW - timedelta(minutes=BUSY_MINUTES + 1)).values(),
                                      now=NOW, settings=settings)["busy"])
        self.assertEqual(busy_context((), now=NOW, settings=settings)["reason"], "schedule_not_busy")

    def test_leisure_sleep_and_unaccepted_calendar_never_delay(self):
        settings = gate_settings(busy_reply_gate_enabled=True)
        for plan in (busy_plan(start=NOW, title="午休"), busy_plan(start=NOW, title="上课", state="proposed"),
                     busy_plan(start=NOW, title="上课", kind="user_report"),
                     busy_plan(start=NOW, title="陪朋友", detail="在打游戏"),
                     busy_plan(start=NOW, title="上课时偷偷摸鱼"),
                     busy_plan(start=NOW, title="看书", detail="安静地休息一下")):
            self.assertFalse(busy_context(plan.values(), now=NOW, settings=settings)["busy"], plan)
        # An exclusion keyword outranks an activity keyword, exactly as in V1.
        self.assertFalse(is_busy_text("上课时摸鱼", settings))
        self.assertTrue(is_busy_text("在上课", settings))
        self.assertTrue(busy_context(busy_plan(start=NOW, title="看书", detail="在开会").values(), now=NOW,
                                     settings=settings)["busy"])
        self.assertTrue(busy_context(busy_plan(start=NOW, title="看书", presence="忙碌").values(), now=NOW,
                                     settings=settings)["busy"])

    def test_defer_seconds_stays_inside_the_configured_interval(self):
        settings = gate_settings(busy_reply_gate_enabled=True, busy_reply_min_delay_seconds=60,
                                 busy_reply_max_delay_seconds=300)
        active = busy_context(busy_plan(start=NOW).values(), now=NOW, settings=settings)
        samples = [defer_seconds(settings, now=NOW, busy=active) for _ in range(200)]
        self.assertTrue(all(60 <= value <= 300 for value in samples), (min(samples), max(samples)))
        self.assertGreater(len(set(samples)), 1)  # V1 drew uniformly inside the interval.
        self.assertEqual(defer_seconds(gate_settings(), now=NOW, busy=active), 0.0)
        self.assertEqual(defer_seconds(settings, now=NOW, busy=active, text="救命，快回我"), 0.0)
        self.assertEqual(defer_seconds(settings, now=NOW, busy={"busy": False}), 0.0)
        fixed = gate_settings(busy_reply_gate_enabled=True, busy_reply_min_delay_seconds=120,
                              busy_reply_max_delay_seconds=120)
        self.assertEqual(defer_seconds(fixed, now=NOW, busy=active), 120.0)
        reversed_bounds = gate_settings(busy_reply_gate_enabled=True, busy_reply_min_delay_seconds=300,
                                        busy_reply_max_delay_seconds=60)
        self.assertEqual(reversed_bounds.delay_bounds(), (60, 300))  # V1 swapped instead of rejecting
        zero = gate_settings(busy_reply_gate_enabled=True, busy_reply_min_delay_seconds=0,
                             busy_reply_max_delay_seconds=0)
        self.assertEqual(defer_seconds(zero, now=NOW, busy=active), 0.0)

    def test_bypass_reason_migrates_the_v1_intent(self):
        self.assertEqual(bypass_reason("救命，我受伤了"), "urgent_or_safety")
        self.assertEqual(bypass_reason("现在就回家"), "urgent_or_safety")
        self.assertEqual(bypass_reason("/状态"), "command")
        self.assertEqual(bypass_reason("！修复"), "command")
        self.assertEqual(bypass_reason("陪伴设置"), "management_command")
        self.assertEqual(bypass_reason("私聊陪伴状态检查一下"), "management_command")
        self.assertEqual(bypass_reason("陪伴"), "")
        self.assertEqual(bypass_reason("今天开会到几点"), "")
        self.assertEqual(bypass_reason(""), "")
        self.assertEqual(bypass_reason("   "), "")

    def test_proactive_resume_uses_the_busy_end_plus_the_configured_buffer(self):
        settings = gate_settings(busy_reply_gate_enabled=True,
                                 busy_reply_proactive_resume_buffer_minutes=10)
        active = busy_context(busy_plan(start=NOW, minutes=30).values(), now=NOW, settings=settings)
        self.assertEqual(proactive_defer_until(settings, active, now=NOW), NOW + timedelta(minutes=40))
        self.assertIsNone(proactive_defer_until(settings, busy_context((), now=NOW, settings=settings), now=NOW))
        self.assertIsNone(proactive_defer_until(gate_settings(), active, now=NOW))
        # A busy window with no readable end still pauses for the bounded fallback.
        self.assertEqual(proactive_defer_until(settings, {"busy": True, "until": None}, now=NOW),
                         NOW + timedelta(minutes=25))

    def test_event_opportunities_are_exempt_from_the_gate(self):
        self.assertFalse(busy_exempt_source({"batch_id": "b", "event_key": "e"}))
        for marker in ("calendar_event", "journal_event", "activity_event", "date_event", "meal_event",
                       "weather_event", "weather_change_event", "official_alert_event", "balance_event",
                       "news_event"):
            self.assertTrue(busy_exempt_source({"batch_id": "b", marker: "m"}), marker)

    def test_defer_seconds_is_rejected_outside_its_registered_range(self):
        for value in (-1, 901, float("nan"), float("inf"), "60", None):
            with self.assertRaises(TaskError, msg=repr(value)):
                validate_defer_seconds(value)
        self.assertEqual(validate_defer_seconds(0), 0.0)
        self.assertEqual(validate_defer_seconds(900), 900.0)


class BusyGateOwnerTests(HostFixture, unittest.TestCase):
    """The registered owner rejects what the reader would otherwise have to default."""

    def setUp(self):
        super().setUp()
        self.service = ConfigurationService(self.runtime,
            FieldRegistry(version="busy-gate-owner.v1", fields=configuration_fields()),
            owners=(BusyGateConfigurationOwner(self.runtime),))
        self.target = busy_gate.target_for(self.window_context())

    def window_context(self):
        from companion_v2.execution.models import TaskContext
        from companion_v2.foundation.refs import AudienceRef
        return TaskContext(self.important, self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
                           AudienceRef(SCOPE, (self.important.subject,)))

    def patch(self, changes):
        snapshot = self.service.read(self.important, target=self.target, group_id=busy_gate.GROUP,
                                     observed_at=NOW).unwrap()
        return self.service.patch(self.important, target=self.target, group_id=busy_gate.GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={busy_gate.GROUP + "." + key: value for key, value in changes.items()},
            operation=self.operation(SCOPE, owner=busy_gate.OWNER), observed_at=NOW)

    def values(self):
        return self.service.read(self.important, target=self.target, group_id=busy_gate.GROUP,
                                 observed_at=NOW).unwrap().values

    def test_owner_registers_the_group_without_a_grant_and_rejects_a_malformed_keyword_write(self):
        self.assertEqual(self.values()[busy_gate.GROUP + ".busy_activity_keywords"],
                         busy_gate.DEFAULT_ACTIVITY_TEXT)
        self.assertFalse(self.patch({"busy_activity_keywords": "{\"not\": \"a list\"}"}).is_success)
        self.assertEqual(self.values()[busy_gate.GROUP + ".busy_activity_keywords"],
                         busy_gate.DEFAULT_ACTIVITY_TEXT)
        self.assertTrue(self.patch({"busy_reply_gate_enabled": True,
                                    "busy_activity_keywords": "写代码,开会"}).is_success)
        self.assertEqual(self.values()[busy_gate.GROUP + ".busy_reply_gate_enabled"], True)


class BusyGateHostTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.grant(self.important, SCOPE, "calendar.write")
        self.calls = 0

    def gate(self, app, context, changes):
        owner = BusyGateConfigurationOwner(app.runtime)
        target = busy_gate.target_for(context)
        fields = configuration_fields()
        snapshot = owner.read(target, fields)
        return owner.write(target, fields,
                           changes={busy_gate.GROUP + "." + key: value for key, value in changes.items()},
                           reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())

    def calendar(self, app, context, *, start, **changes):
        return app.calendar.command(context, identifier="plan", action="create", expected_revision=0,
                                    operation_key="plan", data=plan_data(start=start, **changes))

    def times(self, app, batch):
        row = app.flow.row(batch)
        return (datetime.fromisoformat(row["due_at"]), datetime.fromisoformat(row["deadline_at"]))

    def accept_deferred(self, app, context, *, defer, identifier=None):
        """Drive one accept with an explicit deferral; no host wiring is needed."""
        from companion_v2.conversation.models import InboundConversationEvent
        from companion_v2.execution.models import digest
        identifier = identifier or "delayed-" + str(self.calls)
        self.calls += 1
        body = "延迟期间的输入"
        reference = self.source(app, context, identifier, body)
        event = InboundConversationEvent(identifier, context.actor.principal, context.scope, reference,
                                        "text", digest(body), self.clock.now())
        return app.flow.accept(context, event, role_instruction="原角色人格提示", defer_seconds=defer)

    def source(self, app, context, identifier, body):
        from companion_v2.foundation.refs import AudienceRef, SourceRef, VersionRef
        from companion_v2.execution.models import digest
        from companion_v2.sources.models import SourceRecord
        reference = SourceRef(identifier, "conversation.host_input", context.scope,
                              VersionRef(identifier, 1), self.clock.now())
        app.messages.sources.publish(SourceRecord(reference, AudienceRef(context.scope, (context.actor.subject,)),
            digest(body), body), observed_at=self.clock.now()).unwrap()
        return reference

    async def test_flow_level_deferral_moves_due_at_and_extends_the_deadline_in_bounds(self):
        """Deadline safety is provable without the host wiring: it lives in ``accept``."""
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        first = host.receive(self.event()).unwrap()
        context = self.context(app, first)
        self.advance(app, 0.7)  # Let the open merge window expire so this is its own flow.
        batch = self.accept_deferred(app, context, defer=300).unwrap()
        self.assertNotEqual(batch, first)
        due, deadline = self.times(app, batch)
        self.assertEqual(due, self.clock.now() + timedelta(seconds=QUIET + 300))
        # The deadline moves with the delay, and only by the delay it must cover,
        # so a deferral can never be stopped by ``deadline_elapsed``.
        self.assertEqual(deadline, self.clock.now() + timedelta(seconds=TURN_DEADLINE + 300))
        self.assertGreater(deadline - due, timedelta(seconds=QUIET))
        self.assertLessEqual(deadline, self.clock.now() + timedelta(seconds=TURN_DEADLINE + DEADLINE_CAP))
        self.assertEqual(app.flow.row(batch)["reason"], "busy_delay")
        self.assertLess(due, deadline)
        self.advance(app, 299)
        self.assertEqual(self.called, [])
        self.advance(app, 2)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(self.called), 1)

    async def test_flow_level_deferral_is_rejected_outside_its_range(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        first = host.receive(self.event()).unwrap()
        context = self.context(app, first)
        self.advance(app, 0.7)
        for value in (-1, 901, float("nan")):
            result = self.accept_deferred(app, context, defer=value)
            self.assertEqual(result.failure.code, "busy_gate_defer_invalid", repr(value))
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) FROM conversation_flows", ())[0], 1)

    async def test_gate_disabled_busy_window_or_urgent_text_never_delays(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        self.calendar(app, self.context(app, batch), start=NOW)
        due, deadline = self.times(app, batch)
        self.assertEqual((due, deadline), (NOW + timedelta(seconds=QUIET),
                                           NOW + timedelta(seconds=TURN_DEADLINE)))
        self.assertEqual(app.flow.row(batch)["reason"], "debounce")
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")

    async def test_busy_window_defers_the_batch_and_keeps_the_deadline_safe(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        first = host.receive(self.event()).unwrap()
        context = self.context(app, first)
        self.advance(app, 1)  # The first exchange finishes before the gate applies.
        await self.done(app, first)
        self.calendar(app, context, start=NOW, title="项目会议", detail="上午的例会")
        self.gate(app, context, {"busy_reply_gate_enabled": True})
        before = len(self.called)
        later = host.receive(self.event("delayed")).unwrap()
        self.assertNotEqual(later, first)
        due, deadline = self.times(app, later)
        self.assertEqual(app.flow.row(later)["reason"], "busy_delay")
        self.assertGreaterEqual(due - self.clock.now(), timedelta(seconds=60))
        self.assertLessEqual(due - self.clock.now(), timedelta(seconds=300))
        self.assertGreater(deadline - due, timedelta(seconds=QUIET))  # never deadline_elapsed
        self.assertLess(due, deadline)
        self.assertEqual(len(self.called), before)
        # Nothing may seal before the durable due time, and no sleep may drive it.
        self.advance(app, 30)
        self.assertEqual(len(self.called), before)
        self.advance(app, (due - self.clock.now()).total_seconds() + 1)
        self.assertEqual((await self.done(app, later))["state"], "complete")
        self.assertEqual(len(self.called), before + 1)
        self.assertIn(busy_gate.BUSY_DELAY_INSTRUCTION, self.called[-1].original.instruction)

    async def test_new_input_arms_and_then_never_shortens_the_deferred_window(self):
        """The first input of the window arms the delay; later ones keep it."""
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        first = host.receive(self.event()).unwrap()
        context = self.context(app, first)
        self.calendar(app, context, start=NOW)
        self.gate(app, context, {"busy_reply_gate_enabled": True})
        merged = host.receive(self.event("first")).unwrap()
        self.assertEqual(merged, first)  # Still inside the first input's quiet window.
        self.assertEqual(app.flow.row(merged)["reason"], "busy_delay")
        due, deadline = self.times(app, merged)
        self.assertGreaterEqual(due - NOW, timedelta(seconds=60))
        self.assertLessEqual(due - NOW, timedelta(seconds=300))
        self.assertGreater(deadline - due, timedelta(seconds=QUIET))
        self.advance(app, 0.4)
        # V2 merges the newer private message into the same batch; it never cancels.
        self.assertEqual(host.receive(self.event("second")).unwrap(), merged)
        after_due, after_deadline = self.times(app, merged)
        self.assertGreaterEqual(after_due, due)  # An armed delay is never shortened.
        self.assertLessEqual(after_due - self.clock.now(), timedelta(seconds=300 + 0.5))
        self.assertGreaterEqual(after_deadline, deadline)
        self.assertEqual(app.flow.row(merged)["reason"], "busy_delay")
        self.advance(app, (after_due - self.clock.now()).total_seconds() + 1)
        self.assertEqual((await self.done(app, merged))["state"], "complete")
        self.assertEqual([m.payload for m in self.called[-1].original.context.materials
                          if m.owner == "conversation.host_input"],
                         ["输入 event-1", "输入 first", "输入 second"])

    async def test_urgent_and_management_text_bypass_the_busy_delay(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.advance(app, 1)
        await self.done(app, batch)
        self.calendar(app, context, start=NOW)
        self.gate(app, context, {"busy_reply_gate_enabled": True})
        from companion_v2.conversation.busy_gate import SettingsReader
        settings = SettingsReader(app.runtime).read(context)
        for index, text in enumerate(("救命，我现在很不舒服", "/状态", "陪伴设置")):
            self.assertTrue(busy_gate.bypass_reason(text, settings=settings), text)
            urgent = host.receive(self.event("bypass-" + str(index), text=text)).unwrap()
            row = app.flow.row(urgent)
            # Bypass runs before the interval is drawn, so the delay is never armed.
            self.assertEqual(row["reason"], "debounce", text)
            self.assertEqual(datetime.fromisoformat(row["due_at"]),
                             self.clock.now() + timedelta(seconds=QUIET), text)
            self.advance(app, 1)
            await self.done(app, urgent, seconds=3)

    async def test_deferred_batch_keeps_its_timer_across_a_restart(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.advance(app, 1)
        await self.done(app, batch)
        self.calendar(app, context, start=NOW)
        self.gate(app, context, {"busy_reply_gate_enabled": True})
        delayed = host.receive(self.event("delayed")).unwrap()
        before = dict(app.flow.row(delayed))
        self.assertEqual(before["reason"], "busy_delay")
        await app.close()
        # Only the durable timer can explain the wait now: the schedule is gone.
        with self.runtime.transaction() as connection:
            connection.execute("DELETE FROM calendar_history")
            connection.execute("DELETE FROM calendar_records")
        calls = len(self.called)
        new = await self.start(self.host(runtime=self.open_runtime(), policy=ConversationPolicy(0.1, 0.1, 90, 0),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host")), 1, 1)
        self.assertEqual(new.receive(self.event("delayed")).unwrap(), delayed)
        self.assertEqual(dict(new.application.flow.row(delayed)), before)
        self.assertEqual(len(self.called), calls)
        self.advance(new.application, (datetime.fromisoformat(before["due_at"]) - self.clock.now()).total_seconds() + 1)
        self.assertEqual((await self.done(new.application, delayed))["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)

    async def test_a_revoked_grant_is_refused_before_the_deferred_batch_seals(self):
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.calendar(app, context, start=NOW)
        self.gate(app, context, {"busy_reply_gate_enabled": True})
        delayed = host.receive(self.event("delayed")).unwrap()
        due, _ = self.times(app, delayed)
        self.assertGreater(due - self.clock.now(), timedelta(seconds=30))
        granted = self.runtime.read_one("SELECT grant_id FROM identity_grants WHERE capability = 'text'", ())[0]
        self.identity.revoke_grant(granted, expected_revision=self.identity.revision, revoked_at=self.clock.now())
        self.advance(app, 30)
        self.assertEqual(app.flow.row(delayed)["state"], "collecting")  # Still waiting, not restarted.
        self.assertEqual(app.flow.row(delayed)["reason"], "busy_delay")
        self.advance(app, (due - self.clock.now()).total_seconds() + 1)
        self.assertEqual((await self.done(app, delayed))["state"], "blocked")
        self.assertEqual(self.called, [])
        self.assertEqual(tuple(self.sent), ())

    async def test_a_busy_calendar_read_failure_keeps_the_ordinary_window(self):
        """A calendar that stopped answering must never hold a reply."""
        host = await self.start(self.host(policy=ConversationPolicy(QUIET, 2, TURN_DEADLINE, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        context = self.context(app, batch)
        self.advance(app, 1)
        await self.done(app, batch)
        with patch.object(busy_gate, "calendar_items", side_effect=RuntimeError("calendar unavailable")):
            self.gate(app, context, {"busy_reply_gate_enabled": True})
            later = host.receive(self.event("after-failure")).unwrap()
        row = app.flow.row(later)
        self.assertEqual(row["reason"], "debounce")
        self.assertEqual(datetime.fromisoformat(row["due_at"]), self.clock.now() + timedelta(seconds=QUIET))
        self.assertEqual(busy_gate.readable_items(app.runtime, context), ())
        self.assertEqual(busy_gate.readable_items(app.runtime, context, items=("kept",)), ("kept",))


class BusyGateProactiveTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def gate(self, app, context, changes):
        owner = BusyGateConfigurationOwner(app.runtime)
        target = busy_gate.target_for(context)
        fields = configuration_fields()
        snapshot = owner.read(target, fields)
        return owner.write(target, fields,
                           changes={busy_gate.GROUP + "." + key: value for key, value in changes.items()},
                           reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())

    def calendar(self, app, context, *, start, **changes):
        return app.calendar.command(context, identifier="plan", action="create", expected_revision=0,
                                    operation_key="plan", data=plan_data(start=start, **changes))

    def open_commitment(self, app, context, *, identifier="appointment", minutes=20):
        """One human-accepted commitment, so the calendar can emit a real occurrence."""
        from companion_v2.agenda.models import Schedule
        from companion_v2.execution.models import encode
        from companion_v2.role_work.state.management import StateManagement
        from tests.test_role_state import calendar_data

        manager = StateManagement(app, context)
        before = manager.status(self.core).unwrap()
        manager.command(self.core, dict(action="configure", operation_key="enable-calendar",
            edit_token=before["edit_token"], role_edit_token=before["role_edit_token"],
            changes={"calendar_enabled": True})).unwrap()
        due = self.clock.now() + timedelta(minutes=minutes)
        app.calendar.command(context, identifier=identifier, action="create", expected_revision=0,
            operation_key="create-" + identifier,
            data=calendar_data(kind="commitment", title="一起去看展", detail="",
                schedule=json.loads(encode(Schedule(due, "UTC")))))
        return app.calendar.command(context, identifier=identifier, action="accept", expected_revision=1,
            operation_key="accept-" + identifier, operator=self.core)

    async def scheduled_with_busy_window(self, *, minutes=2, **gate_changes):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True, "automatic_enabled": True, "interval_seconds": 60,
                            "idle_seconds": 60}).unwrap()
        batch = host.receive(self.event()).unwrap()
        await self.done(app, batch)
        context = self.context(app, batch)
        self.grant(self.important, SCOPE, "calendar.write")
        self.calendar(app, context, start=NOW, minutes=minutes)
        self.gate(app, context, {"busy_reply_gate_enabled": True, **gate_changes})
        return host, app, context, batch

    async def test_busy_gate_reads_the_role_calendar_from_another_private_window(self):
        host = await self.start(self.host())
        app = host.application
        owner_context = self.contact_context()
        self.grant(self.important, SCOPE, "calendar.write")
        self.calendar(app, owner_context, start=NOW, title="项目会议")

        other_context = self.other_window_context("shared-calendar-window")
        self.gate(app, other_context, {"busy_reply_gate_enabled": True})
        settings = busy_gate.SettingsReader(app.runtime).read(other_context)
        items = busy_gate.readable_items(app.runtime, other_context)
        decision = busy_gate.busy_context(items, now=self.clock.now(), settings=settings)

        self.assertEqual([item["id"] for item in items], ["plan"])
        self.assertTrue(decision["busy"])

    async def test_a_busy_calendar_holds_automatic_contact_and_resumes_after_the_window(self):
        host, app, context, batch = await self.scheduled_with_busy_window(
            busy_reply_proactive_resume_buffer_minutes=10)
        self.advance(app, 60)
        app.contact_schedule.advance()
        await asyncio.sleep(0.05)
        self.assertEqual(self.contact_calls(), [])  # No worker, no envelope, no queue entry.
        self.assertEqual(self.contacts(app), ())
        hold = app.contact_schedule._busy_hold(context, {}, self.clock.now())
        # Busy end is NOW + 2 minutes; the configured buffer is added on top of it.
        self.assertEqual(hold, NOW + timedelta(minutes=12))
        self.advance(app, 61)  # Busy window over; the ordinary slot is still open.
        self.assertIsNone(app.contact_schedule._busy_hold(context, {}, self.clock.now()))
        await self.automatic_delivery(app)
        self.assertEqual(len(self.contact_calls()), 1)

    async def test_an_event_opportunity_is_not_held_by_the_busy_window(self):
        """A time-critical event keeps its own verified timing and is exempt from the gate.

        The stub plan has to be built the way the scheduler really builds one —
        from the batch's own origin plus a genuine event marker — because the
        contact service re-derives both on admission and refuses a contact whose
        conversation, persona or occurrence moved.
        """
        host, app, context, batch = await self.scheduled_with_busy_window()
        self.open_commitment(app, context)
        self.advance(app, 60)
        candidate, _ = app.contact_schedule.calendar_events.select(
            context, self.preferences(app, context), self.clock.now())
        self.assertIsNotNone(candidate)  # The commitment owns a real, verifiable occurrence.
        opportunity, intents, marker = candidate
        origin = {**app.contact_schedule._origin(context, batch, self.clock.now())[0],
                  "calendar_event": marker}
        # An event origin is never held; an ordinary one still is.
        self.assertTrue(busy_exempt_source(origin))
        self.assertIsNone(app.contact_schedule._busy_hold(context, origin, self.clock.now()))
        self.assertIsNotNone(app.contact_schedule._busy_hold(context, {}, self.clock.now()))
        instruction = app.runtime.read_one(
            "SELECT role_instruction FROM conversation_batches WHERE batch_id = ?", (batch,))["role_instruction"]
        with patch.object(app.contact_schedule, "_plan", return_value=(
                context, opportunity, intents, instruction, origin)):
            app.contact_schedule.advance()
            await self.until(lambda: bool(app.contact_schedule.active_handles) or app.state == "faulted")
        self.assertEqual(app.state, "running", app.failure_code)
        self.assertTrue(app.contact_schedule.active_handles)
        await app.contact_schedule.close()


if __name__ == "__main__":
    unittest.main()
