"""Accepted private commitments through the real contact scheduler and source guard."""

from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.agenda.models import Frequency, Schedule
from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.proactive.calendar_events import CalendarContactEvents, SOURCE_OWNER
from companion_v2.proactive.persona_judge import judge_request
from companion_v2.proactive.prompts import evidence_materials
from companion_v2.role_work.state.management import StateManagement
from companion_v2.sources.models import SourceRecord
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_role_state import calendar_data


class CalendarContactTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def setup_contacts(self, **settings):
        self.host_instance, batch = await self.scheduled()
        self.app = self.host_instance.application
        self.origin = self.context(self.app, batch)
        manager = StateManagement(self.app, self.origin)
        before = manager.status(self.core).unwrap()
        manager.command(self.core, dict(action="configure", operation_key="enable-calendar",
            role_edit_token=before["role_edit_token"], changes={"calendar_enabled": True})).unwrap()
        self.settings(self.app, {"chat.enabled": False, **settings}).unwrap()
        self.assertIsInstance(self.app.contact_schedule.calendar_events, CalendarContactEvents)

    def commitment(self, identifier="appointment", *, due=None, kind="commitment", sources=(), schedule=None):
        schedule = schedule or Schedule(due or self.clock.now() + timedelta(minutes=20), "Asia/Shanghai")
        data = calendar_data(kind=kind, title="一起去看展", detail="下午在入口碰面",
            schedule=json.loads(encode(schedule)))
        self.app.calendar.command(self.origin, identifier=identifier, action="create", expected_revision=0,
            operation_key="create-" + identifier, data=data, sources=sources)
        return data

    def accept(self, identifier="appointment", revision=1):
        return self.app.calendar.command(self.origin, identifier=identifier, action="accept",
            expected_revision=revision, operation_key="accept-" + identifier, operator=self.core)

    def candidate(self):
        return self.app.contact_schedule.calendar_events.select(
            self.origin, self.preferences(self.app, self.origin), self.clock.now())

    async def test_confirmed_commitment_sends_one_bounded_upcoming_reminder(self):
        await self.setup_contacts()
        self.commitment()
        self.accept()
        self.advance(self.app, 60)
        await self.automatic_delivery(self.app)
        request = self.contact_calls()[0].original
        evidence = [json.loads(item.payload) for item in request.context.materials if item.owner == SOURCE_OWNER]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["event"], "accepted_commitment_upcoming")
        self.assertEqual(evidence[0]["title"], "一起去看展")
        self.assertNotIn("_receipt", evidence[0])
        self.assertIn("尚未开始", evidence[0]["meaning"])
        self.assertEqual(json.loads(self.contacts(self.app)[0]["record_json"])["purpose"], "reminder")
        self.app.contact_schedule.advance()
        self.assertEqual(len(self.contact_calls()), 1)

    async def test_accepted_reminder_keeps_its_time_when_activity_curve_holds_shares(self):
        await self.setup_contacts(quiet_enabled=False, proactive_hour_activity_enabled=True,
            quiet_timezone="UTC", proactive_hour_activity_curve=",".join(["0.05"] + ["1.00"] * 23))
        self.commitment()
        self.accept()
        with patch("companion_v2.proactive.rhythm.sample", return_value=0.5):
            self.advance(self.app, 60)
            await self.automatic_delivery(self.app)
        saved = json.loads(self.contacts(self.app)[0]["record_json"])
        self.assertEqual(saved["purpose"], "reminder")
        self.assertIn("calendar_event", saved["automatic_origin"])

    async def test_proposal_role_plan_and_explicit_reminder_do_not_generate_another_contact(self):
        await self.setup_contacts()
        self.commitment("proposal")
        self.commitment("role-plan", kind="role_plan")
        self.commitment("with-reminder")
        self.accept("with-reminder")
        self.app.calendar.remind(self.origin, identifier="with-reminder", expected_revision=2,
            operation_key="explicit-reminder")
        self.assertEqual(self.candidate(), (None, False))
        self.advance(self.app, 60)
        self.app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])

    async def test_reschedule_and_source_revocation_invalidate_selected_candidate(self):
        await self.setup_contacts()
        original = SourceRef("calendar-input", "calendar.fixture", self.origin.scope,
            VersionRef("calendar-input", 1), self.clock.now())
        payload = "一起看展的原始约定"
        self.app.proactive.sources.publish(SourceRecord(original, self.origin.audience,
            digest(payload), payload), observed_at=self.clock.now()).unwrap()
        self.commitment(sources=(original,))
        self.accept()
        candidate, _ = self.candidate()
        self.assertIsNotNone(candidate)
        reference = candidate[1][0].thought.evidence.sources[0]
        marker = candidate[2]
        self.app.calendar.command(self.origin, identifier="appointment", action="exception",
            expected_revision=2, operation_key="move-appointment",
            data={"sequence": 0, "start": (self.clock.now() + timedelta(hours=2)).isoformat()})
        with self.assertRaises(TaskError):
            self.app.contact_schedule.calendar_events.validate(self.origin, marker, self.clock.now())
        self.assertFalse(self.app.proactive.sources.validate_reference(reference,
            observed_at=self.clock.now()).is_success)

        self.commitment("second", sources=(original,))
        self.accept("second")
        second = self.app.contact_schedule.calendar_events.select(
            self.origin, self.preferences(self.app, self.origin), self.clock.now())[0]
        self.assertIsNotNone(second)
        second_ref = second[1][0].thought.evidence.sources[0]
        self.app.proactive.sources.revoke(original, expected_governance_revision=1,
            observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError):
            self.app.contact_schedule.calendar_events.validate(self.origin, second[2], self.clock.now())
        self.assertFalse(self.app.proactive.sources.validate_reference(second_ref,
            observed_at=self.clock.now()).is_success)

    async def test_cancel_during_generation_discards_draft_before_delivery(self):
        await self.setup_contacts()
        self.commitment()
        self.accept()

        async def cancel(envelope):
            self.app.calendar.command(self.origin, identifier="appointment", action="cancel",
                expected_revision=2, operation_key="cancel-appointment")
            from tests.test_role_tool_loop import answered
            return answered('{"decision":"send","text":"已经取消的约定"}')

        self.model_behavior = cancel
        self.advance(self.app, 60)
        await self.until(lambda: len(self.contact_calls()) == 1)
        await self.until(lambda: not self.app.proactive.active_handles)
        self.assertEqual(self.contacts(self.app), ())
        self.assertEqual(len(self.sent), 1)

    async def test_moved_recurring_occurrence_is_scanned_by_original_sequence(self):
        await self.setup_contacts()
        schedule = Schedule(self.clock.now() - timedelta(days=2), "Asia/Shanghai", Frequency.DAILY, count=4)
        self.commitment(schedule=schedule)
        moved = self.clock.now() + timedelta(minutes=15)
        self.app.calendar.command(self.origin, identifier="appointment", action="exception",
            expected_revision=1, operation_key="move-first",
            data={"sequence": 0, "start": moved.isoformat()})
        self.accept(revision=2)
        candidate, _ = self.candidate()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate[2]["sequence"], 0)
        self.assertEqual(candidate[2]["starts_at"], moved.isoformat(timespec="microseconds"))

    async def test_calendar_scan_continues_beyond_one_page(self):
        await self.setup_contacts()
        for index in range(32):
            self.commitment(f"plan-{index:02}", kind="role_plan")
        self.commitment("z-appointment")
        self.accept("z-appointment")
        self.assertEqual(self.candidate(), (None, True))
        candidate, _ = self.candidate()
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate[2]["id"], "z-appointment")

    async def test_two_moved_occurrences_in_one_record_remain_discoverable(self):
        await self.setup_contacts()
        schedule = Schedule(self.clock.now() - timedelta(days=2), "Asia/Shanghai", Frequency.DAILY, count=4)
        self.commitment(schedule=schedule)
        for sequence, minutes in ((0, 10), (1, 20)):
            self.app.calendar.command(self.origin, identifier="appointment", action="exception",
                expected_revision=sequence + 1, operation_key=f"move-{sequence}",
                data={"sequence": sequence, "start": (self.clock.now() + timedelta(minutes=minutes)).isoformat()})
        self.accept(revision=3)
        first, _ = self.candidate()
        self.assertEqual(first[2]["sequence"], 0)
        producer = self.app.contact_schedule.calendar_events
        with patch.object(producer, "_known", side_effect=lambda context, opportunity:
                opportunity.opportunity_id == first[0].opportunity_id):
            second, _ = self.candidate()
        self.assertIsNotNone(second)
        self.assertEqual(second[2]["sequence"], 1)

    async def test_post_commitment_afterglow_and_role_plan_completion_transitions(self):
        await self.setup_contacts()
        due = self.clock.now() + timedelta(minutes=10)
        self.commitment("afterglow-commitment", due=due)
        self.accept("afterglow-commitment")
        # Duration is 60m by default in calendar_data; after due + 60m + 5m delay, post_commitment opens.
        self.advance(self.app, 76 * 60)
        transition, _ = self.app.contact_schedule.calendar_events.select_transitions(
            self.origin, self.preferences(self.app, self.origin), self.clock.now())
        self.assertIsNotNone(transition)
        _, intents, marker = transition
        self.assertEqual(marker["phase"], "post_commitment")
        self.assertEqual(marker["purpose"], "followup")
        self.assertEqual(intents[0].purpose, "followup")
        self.app.contact_schedule.calendar_events.validate(self.origin, marker, self.clock.now())

        plan_due = self.clock.now() + timedelta(minutes=5)
        self.commitment("finished-plan", due=plan_due, kind="role_plan")
        self.accept("finished-plan")
        self.advance(self.app, 66 * 60)
        plan_transition, _ = self.app.contact_schedule.calendar_events.select_transitions(
            self.origin, self.preferences(self.app, self.origin), self.clock.now(), purpose="life")
        self.assertIsNotNone(plan_transition)
        _, plan_intents, plan_marker = plan_transition
        self.assertEqual(plan_marker["phase"], "role_plan_finished")
        self.assertEqual(plan_marker["purpose"], "life")
        self.assertEqual(plan_intents[0].purpose, "life")
        self.app.contact_schedule.calendar_events.validate(self.origin, plan_marker, self.clock.now())
        records = self.app.proactive._records(self.origin, plan_intents[0].thought.evidence.sources, self.clock.now())
        projected = evidence_materials(records)
        request = judge_request({"context": self.origin, "purpose": "life", "body": "先放一放。",
            "at": self.clock.now().isoformat(), "confidence": 0.9, "note": "",
            "role_instruction": "保持原人格。", "cause_id": "review-plan", "observed_at": self.clock.now(),
            "records": records, "thought": plan_intents[0].thought.summary})
        reviewed = tuple(item for item in request.context.materials if item.purpose == "contact_evidence")
        self.assertEqual(reviewed, projected)
        material = json.loads(reviewed[0].payload)
        self.assertEqual(material["event"], "role_plan_window_concluded")
        self.assertNotIn("_receipt", material)
        self.assertIn("不证明实际执行", material["meaning"])
        self.assertIn("_receipt", json.loads(records[0].payload))
