from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from companion_v2.agenda.background import AgendaDispatchPolicy
from companion_v2.agenda.models import AgendaError, AgendaTarget, Frequency, Schedule
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import ClaimResult, SupervisorLimits
from companion_v2.execution.runner import TaskRun
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute
from tests.test_host_application import HostFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE, SCOPE


class AgendaBackgroundFixture(HostFixture):
    def reminder(self, app, identifier="reminder", *, schedule=None, scope=SCOPE, actor=None, memo_id=None):
        actor = actor or self.important
        binding = self.identity.resolve_role(scope, observed_at=self.clock.now()).unwrap()
        target = AgendaTarget(scope, binding.role, actor.subject)
        app.agenda.create_reminder(actor, target=target, reminder_id=identifier, memo_id=memo_id,
            expected_memo_revision=1 if memo_id else None, message="喝点水，休息一下。" if not memo_id else "",
            schedule=schedule if memo_id else schedule or Schedule(self.clock.now(), "Asia/Shanghai"),
            operation=self.operation(scope, owner="agenda"), expected_role_revision=binding.revision,
            observed_at=self.clock.now()).unwrap()
        row = self.runtime.read_one("SELECT occurrence_id FROM agenda_occurrences WHERE reminder_id = ?", (identifier,))
        return row["occurrence_id"], target

    def dispatch(self, occurrence):
        return self.runtime.read_one("SELECT * FROM agenda_dispatches WHERE occurrence_id = ?", (occurrence,))

    def delivery(self, occurrence):
        row = self.dispatch(occurrence)
        return self.runtime.read_one("SELECT * FROM background_deliveries WHERE delivery_id = ?", (row["delivery_id"],)) if row and row["delivery_id"] else None

    async def delivered(self, app, occurrence):
        await self.until(lambda: (self.delivery(occurrence) is not None and self.delivery(occurrence)["state"] == "delivered") or app.state == "faulted")
        self.assertEqual(app.state, "running", app.failure_code)
        await self.until(lambda: self.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?",
            (self.delivery(occurrence)["task_id"],))["state"] == "succeeded")
        return self.delivery(occurrence)

    def memo(self, app, *, schedule):
        binding = self.identity.resolve_role(SCOPE, observed_at=self.clock.now()).unwrap()
        target = AgendaTarget(SCOPE, ROLE, self.important.subject)
        app.agenda.create_memo(self.important, target=target, memo_id="memo", title="读书计划", body="读完第二章",
            operation=self.operation(SCOPE, owner="agenda"), expected_role_revision=binding.revision,
            schedule=schedule, observed_at=self.clock.now()).unwrap()
        return target, binding


class AgendaBackgroundTests(AgendaBackgroundFixture, unittest.IsolatedAsyncioTestCase):
    async def test_due_reminder_automatically_uses_shared_task_sender_and_receipt(self):
        host = self.host()
        app = host.application
        occurrence, target = self.reminder(app)
        await self.start(host)
        row = await self.delivered(app, occurrence)
        self.assertEqual(self.called, [])
        self.assertEqual([e.text for e in self.sent], ["喝点水，休息一下。"])
        self.assertEqual(self.sent[0].context.audience.recipients, (target.owner,))
        self.assertEqual(self.sent[0].context.scope, target.scope)
        task = app.tasks.read(self.sent[0].context, row["task_id"]).unwrap()
        self.assertEqual((task.state, task.attempt_no), ("succeeded", 1))
        lane = self.runtime.read_one("SELECT lane FROM execution_tasks WHERE task_id = ?", (task.task_id,))["lane"]
        self.assertEqual(lane, "background")
        report = app.agenda_background.observe(self.important, target=target, occurrence_id=occurrence, observed_at=NOW).unwrap()
        self.assertEqual((report["scheduling"], report["delivery"]), ("queued", "delivered"))
        self.assertIsNotNone(report["receipt_ref"])
        self.assertNotIn("喝点水", row["request_json"])
        app.notify()
        await asyncio.sleep(0.03)
        self.assertEqual(len(self.sent), 1)

    async def test_outbound_waits_for_saturated_background_lane_without_losing_message(self):
        host = await self.start()
        app = host.application
        original_claim = app.tasks.claim_task
        busy = True
        refused = []

        def saturated_lane(context, task_id):
            if busy and task_id.startswith("send-outbound-"):
                refused.append(task_id)
                return Outcome.success(ClaimResult(None, "task_lane_full"))
            return original_claim(context, task_id)

        # Simulate a concurrent background task holding the final lane slot.
        with patch.object(app.tasks, "claim_task", saturated_lane):
            occurrence, _ = self.reminder(app)
            app.notify()
            await self.until(lambda: len(refused) > 0)
            self.assertEqual(self.sent, [])
            row = self.delivery(occurrence)
            self.assertIsNotNone(row)
            self.assertEqual(app.tasks.read(DeliveryRequest.restore(row["request_json"]).context,
                                            row["task_id"]).unwrap().attempt_no, 0)
            busy = False
            delivered = await self.delivered(app, occurrence)

        self.assertEqual(delivered["state"], "delivered")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].text, "喝点水，休息一下。")

    async def test_future_due_timer_needs_no_manual_scan_or_notification(self):
        current = lambda: datetime.now(timezone.utc)
        self.receipt_now = current
        host = self.host(now=current)
        occurrence, _ = self.reminder(host.application, schedule=Schedule(current() + timedelta(seconds=0.06), "Asia/Shanghai"))
        await self.start(host)
        await self.delivered(host.application, occurrence)
        self.assertEqual(len(self.sent), 1)

    async def test_queue_and_preparation_roll_back_together_under_capacity_pressure(self):
        host = self.host(limits=replace(self.limits, queued_per_lane=1))
        app = host.application
        await self.start(host)
        first, _ = self.reminder(app, "first", schedule=Schedule(NOW - timedelta(seconds=1), "UTC"))
        second, _ = self.reminder(app, "second")
        app.agenda_background.advance().unwrap()
        self.assertEqual(self.dispatch(first)["state"], "queued")
        self.assertIsNone(self.dispatch(second))
        self.assertEqual(app.agenda.repository.occurrence(second)["state"], "scheduled")
        await self.delivered(app, first)
        await self.delivered(app, second)
        self.assertEqual(len(self.sent), 2)

    async def test_persisted_scan_advances_past_denied_pages(self):
        host = self.host(agenda_policy=AgendaDispatchPolicy(scan_size=2))
        app = host.application
        scope = replace(SCOPE, native_scope_id="standard", conversation_id="standard", session_id="standard")
        self.personas.register_route(ConversationRoute(scope, user=self.standard.subject), observed_at=NOW)
        self.grant(self.standard, scope, "reminder.create")
        grant = self.grant(self.standard, scope, "reminder.deliver")
        old = [self.reminder(app, f"denied-{i}", scope=scope, actor=self.standard,
            schedule=Schedule(NOW - timedelta(seconds=10), "UTC"))[0] for i in range(5)]
        good, _ = self.reminder(app, "good")
        self.identity.revoke_grant(grant.grant_id, revoked_at=NOW, expected_revision=self.identity.revision)
        await self.start(host)
        self.assertTrue(app.agenda_background.advance().unwrap())
        cursor = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = 'agenda.background.cursor.v1'", ())["value"]
        self.assertNotEqual(json.loads(cursor), ["", ""])
        await self.delivered(app, good)
        self.assertTrue(all(self.dispatch(occurrence)["state"] == "blocked" for occurrence in old))
        self.assertEqual(len(self.sent), 1)

    async def test_long_outage_jumps_old_recurrence_and_sends_only_recent_period(self):
        host = self.host()
        app = host.application
        occurrence, _ = self.reminder(app, schedule=Schedule(NOW - timedelta(days=3000), "Asia/Shanghai", Frequency.DAILY))
        await self.start(host)
        await self.until(lambda: len(self.sent) == 1 or app.state == "faulted")
        self.assertEqual(app.state, "running", app.failure_code)
        old = self.dispatch(occurrence)
        self.assertEqual((old["state"], old["next_sequence"]), ("skipped", 3000))
        self.assertLessEqual(len(self.runtime.read_all("SELECT * FROM agenda_occurrences", ())), 3)
        self.assertEqual(self.called, [])
        await self.until(lambda: not app.background.active_handles)

    async def test_missed_memo_reminder_does_not_complete_or_advance_the_memo(self):
        host = self.host()
        app = host.application
        schedule = Schedule(NOW - timedelta(days=2), "UTC", Frequency.DAILY)
        target, _ = self.memo(app, schedule=schedule)
        occurrence, _ = self.reminder(app, memo_id="memo")
        await self.start(host)
        await self.until(lambda: self.dispatch(occurrence) is not None)
        memo = app.agenda.read_memo(self.important, target=target, memo_id="memo", observed_at=NOW).unwrap()
        self.assertEqual((memo.sequence, memo.state, memo.completion_count), (0, "active", 0))
        self.assertEqual(self.dispatch(occurrence)["reason"], "missed_awaiting_memo_completion")
        self.assertEqual(self.sent, [])

    async def test_memo_edit_after_queue_prevents_old_text_from_being_sent(self):
        host = await self.start()
        app = host.application
        target, binding = self.memo(app, schedule=Schedule(NOW, "UTC"))
        occurrence, _ = self.reminder(app, memo_id="memo")
        app.agenda_background.advance().unwrap()
        app.agenda.change_memo(self.important, target=target, memo_id="memo", action="edit",
            expected_revision=1, expected_role_revision=binding.revision, changes={"body": "改为第三章"},
            operation=self.operation(SCOPE, owner="agenda"), observed_at=NOW).unwrap()
        await self.until(lambda: self.delivery(occurrence)["state"] == "cancelled" or app.state == "faulted")
        self.assertEqual(app.state, "running", app.failure_code)
        self.assertEqual(self.sent, [])

    async def test_revoked_identity_after_queue_cannot_fault_host_or_redirect_delivery(self):
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        app.agenda_background.advance().unwrap()
        self.identity.revoke_binding(self.important.binding_id, revoked_at=NOW, expected_revision=self.identity.revision)
        await self.until(lambda: self.delivery(occurrence)["state"] == "cancelled" or app.state == "faulted")
        self.assertEqual(app.state, "running", app.failure_code)
        self.assertEqual(self.sent, [])

    async def test_reschedule_queues_new_occurrence_but_cancels_original_draft(self):
        host = await self.start()
        app = host.application
        occurrence, target = self.reminder(app)
        app.agenda_background.advance().unwrap()
        reminder = app.agenda.repository.reminder("reminder")
        app.agenda.change_reminder(self.important, target=target, reminder_id="reminder", action="reschedule",
            expected_revision=1, expected_role_revision=reminder.role_binding_revision,
            schedule=Schedule(NOW + timedelta(seconds=3), "UTC"), operation=self.operation(SCOPE, owner="agenda"), observed_at=NOW).unwrap()
        await self.until(lambda: self.delivery(occurrence)["state"] == "cancelled")
        self.assertEqual(self.sent, [])
        self.advance(app, 3)
        new = self.runtime.read_one("SELECT occurrence_id FROM agenda_occurrences WHERE reminder_revision = 2", ())["occurrence_id"]
        await self.delivered(app, new)
        self.assertEqual(len(self.sent), 1)

    async def test_unknown_receipt_is_not_replayed_after_restart_and_late_ack_is_accounted(self):
        async def unknown(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = unknown
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        await self.until(lambda: self.delivery(occurrence) is not None and self.delivery(occurrence)["state"] == "unknown")
        await self.until(lambda: not app.background.active_handles)
        envelope = self.sent[0]
        await app.close()
        new = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)).application
        new.agenda_background.advance().unwrap()
        await asyncio.sleep(0.03)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(new.record_receipt(replace(self.receipt(envelope), payload_hash="different")).failure.code, "delivery_receipt_mismatch")
        ack = self.receipt(envelope)
        self.assertEqual(new.record_receipt(ack).unwrap(), "delivered")
        self.assertEqual(new.record_receipt(ack).unwrap(), "delivered")
        self.assertEqual(new.reconcile_delivery(envelope.context, envelope.part_id).unwrap().state, "succeeded")
        self.assertEqual(len(self.sent), 1)

    async def test_restart_recovers_dispatched_task_without_new_platform_attempt(self):
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        app.agenda_background.advance().unwrap()
        row = self.delivery(occurrence)
        request = DeliveryRequest.restore(row["request_json"])
        claim = app.tasks.claim_task(request.context, row["task_id"]).unwrap().claim
        task = app.tasks.begin(claim).unwrap()
        app.outbound._dispatch(TaskRun(app.tasks, claim, task))
        await app.close()
        new_host = self.host(runtime=self.open_runtime())
        report = (await new_host.application.start(expected_task_generation=1, expected_model_generation=1)).unwrap()
        self.assertEqual(report.unresolved_deliveries, 1)
        self.assertEqual(self.delivery(occurrence)["state"], "unknown")
        self.assertEqual(self.sent, [])

    async def test_queued_unsent_work_survives_restart_and_retains_original_context(self):
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        app.agenda_background.advance().unwrap()
        original = self.delivery(occurrence)["request_json"]
        await app.close()
        new = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)).application
        await self.delivered(new, occurrence)
        self.assertEqual(self.delivery(occurrence)["request_json"], original)
        self.assertEqual(len(self.sent), 1)

    async def test_queued_deadline_is_not_extended_by_restart(self):
        host = await self.start()
        occurrence, _ = self.reminder(host.application)
        host.application.agenda_background.advance().unwrap()
        await host.application.close()
        self.clock.advance(timedelta(seconds=301))
        new = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)).application
        await self.until(lambda: self.delivery(occurrence)["state"] == "cancelled")
        self.assertEqual(new.state, "running")
        self.assertEqual(self.sent, [])

    async def test_queue_idempotency_cannot_change_payload_and_failed_registration_is_atomic(self):
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        app.agenda_background.advance().unwrap()
        row = self.delivery(occurrence)
        request = DeliveryRequest.restore(row["request_json"])
        self.assertEqual(app.outbound.queue(request).unwrap().task_id, row["task_id"])
        self.assertEqual(app.outbound.queue(replace(request, payload_hash="different")).failure.code, "delivery_idempotency_conflict")
        self.assertFalse(app.outbound.queue(replace(request, logical_key="other-occurrence")).is_success)
        self.assertEqual(len(self.runtime.read_all("SELECT * FROM background_deliveries", ())), 1)
        await self.delivered(app, occurrence)

    async def test_shutdown_keeps_storage_until_a_stubborn_sender_stops(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn(envelope):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return self.receipt(envelope)
        self.send_behavior = stubborn
        self.addCleanup(release.set)
        host = await self.start(self.host(owns_runtime=True))
        app = host.application
        self.reminder(app)
        await asyncio.wait_for(entered.wait(), 2)
        first = await app.close(grace_seconds=0)
        self.assertEqual((first.state, first.storage_closed), ("draining", False))
        self.assertTrue(first.pending_handles)
        self.runtime.read_one("SELECT 1", ())
        release.set()
        await self.until(lambda: not app.active_handles)
        second = await app.close(grace_seconds=0)
        self.assertEqual((second.state, second.storage_closed), ("stopped", True))

    async def test_background_wait_does_not_take_foreground_slots(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(envelope):
            if envelope.part_id.startswith("outbound-"):
                entered.set()
                await release.wait()
            return self.receipt(envelope)
        self.send_behavior = wait
        self.addCleanup(release.set)
        host = await self.start()
        occurrence, _ = self.reminder(host.application)
        await asyncio.wait_for(entered.wait(), 2)
        batch = host.receive(self.event()).unwrap()
        self.advance(host.application, 1)
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        release.set()
        await self.delivered(host.application, occurrence)

    async def test_sender_rechecks_cancellation_at_its_actual_effect_boundary(self):
        entered, release = asyncio.Event(), asyncio.Event()
        wire_calls = []
        async def checked_sender(envelope):
            entered.set()
            await release.wait()
            try:
                envelope.before_dispatch()
            except ValueError:
                return self.receipt(envelope, "not_sent")
            wire_calls.append(envelope)
            return self.receipt(envelope)
        self.send_behavior = checked_sender
        self.addCleanup(release.set)
        host = await self.start()
        app = host.application
        occurrence, target = self.reminder(app)
        await asyncio.wait_for(entered.wait(), 2)
        reminder = app.agenda.repository.reminder("reminder")
        app.agenda.change_reminder(self.important, target=target, reminder_id="reminder", action="cancel",
            expected_revision=1, expected_role_revision=reminder.role_binding_revision,
            operation=self.operation(SCOPE, owner="agenda"), observed_at=NOW).unwrap()
        release.set()
        await self.until(lambda: self.delivery(occurrence)["state"] == "failed")
        self.assertEqual(wire_calls, [])
        self.assertEqual(app.state, "running")

    async def test_known_not_sent_and_accepted_are_distinct_and_neither_replays(self):
        async def not_sent(envelope):
            return self.receipt(envelope, "not_sent")
        self.send_behavior = not_sent
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        await self.until(lambda: self.delivery(occurrence) is not None and self.delivery(occurrence)["state"] == "failed")
        failed_envelope = self.sent[0]
        self.assertEqual(app.record_receipt(self.receipt(failed_envelope)).failure.code, "delivery_receipt_conflict")
        async def accepted(envelope):
            return self.receipt(envelope, "accepted")
        self.send_behavior = accepted
        another, _ = self.reminder(app, "accepted")
        app.notify()
        await self.until(lambda: self.delivery(another) is not None and self.delivery(another)["state"] == "accepted")
        envelope = self.sent[-1]
        self.assertEqual(app.record_receipt(self.receipt(envelope, "unknown")).unwrap(), "accepted")
        self.assertEqual(app.record_receipt(self.receipt(envelope)).unwrap(), "delivered")
        self.assertEqual(len(self.sent), 2)

    async def test_receipt_accounting_remains_valid_after_revocation_without_source_access(self):
        async def unknown(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = unknown
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        await self.until(lambda: self.delivery(occurrence) is not None and self.delivery(occurrence)["state"] == "unknown")
        self.identity.revoke_binding(self.important.binding_id, revoked_at=NOW, expected_revision=self.identity.revision)
        self.assertEqual(app.record_receipt(self.receipt(self.sent[0])).unwrap(), "delivered")
        self.assertFalse(app.reconcile_delivery(self.sent[0].context, self.sent[0].part_id).is_success)
        self.assertEqual(len(self.sent), 1)

    async def test_role_changed_away_and_back_still_invalidates_original_task(self):
        host = await self.start()
        app = host.application
        occurrence, _ = self.reminder(app)
        app.agenda_background.advance().unwrap()
        original = self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap()
        other = self.identity.bind_role(scope=SCOPE, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "other-role"),
            expected_revision=original.revision, observed_at=NOW)
        self.identity.bind_role(scope=SCOPE, role=ROLE, expected_revision=other.revision, observed_at=NOW)
        await self.until(lambda: self.delivery(occurrence)["state"] == "cancelled")
        self.assertEqual(self.sent, [])

    async def test_in_flight_scope_does_not_starve_another_background_route(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(envelope):
            if envelope.context.scope == SCOPE:
                entered.set()
                await release.wait()
            return self.receipt(envelope)
        self.send_behavior = held
        self.addCleanup(release.set)
        host = await self.start(self.host(limits=replace(self.limits, background_slots=2)))
        app = host.application
        self.reminder(app, "held", schedule=Schedule(NOW - timedelta(seconds=20), "UTC"))
        await asyncio.wait_for(entered.wait(), 2)
        self.reminder(app, "same-route", schedule=Schedule(NOW - timedelta(seconds=10), "UTC"))
        scope = replace(SCOPE, native_scope_id="other", conversation_id="other", session_id="other")
        self.personas.register_route(ConversationRoute(scope, user=self.important.subject), observed_at=NOW)
        other, _ = self.reminder(app, "other-route", scope=scope)
        app.notify()
        await self.delivered(app, other)
        release.set()


class ScheduleJumpTests(unittest.TestCase):
    def test_calendar_jump_keeps_month_end_anchor_count_and_minimum(self):
        anchor = datetime(2024, 1, 31, 1, tzinfo=timezone.utc)
        schedule = Schedule(anchor, "UTC", Frequency.MONTHLY, count=4)
        self.assertEqual(schedule.first_at_or_after(datetime(2024, 3, 1, tzinfo=timezone.utc)), 2)
        self.assertEqual(schedule.at(2).day, 31)
        self.assertIsNone(schedule.first_at_or_after(datetime(2024, 6, 1, tzinfo=timezone.utc)))
        self.assertEqual(schedule.first_at_or_after(anchor, minimum=3), 3)
        self.assertIsNone(Schedule(anchor, "UTC").first_at_or_after(anchor + timedelta(seconds=1)))

    def test_millennia_of_missed_days_use_at_most_two_calendar_lookups(self):
        anchor = datetime(1000, 1, 1, tzinfo=timezone.utc)
        target = datetime(9000, 1, 1, tzinfo=timezone.utc)
        schedule = Schedule(anchor, "UTC", Frequency.DAILY)
        original = Schedule.at
        calls = []
        def at(instance, sequence):
            calls.append(sequence)
            return original(instance, sequence)
        with patch.object(Schedule, "at", at):
            index = schedule.first_at_or_after(target)
        self.assertEqual(schedule.at(index), target)
        self.assertLessEqual(len(calls), 2)

    def test_dst_gap_requires_explicit_resolution(self):
        anchor = datetime(2026, 3, 7, 7, 30, tzinfo=timezone.utc)
        schedule = Schedule(anchor, "America/New_York", Frequency.DAILY)
        with self.assertRaises(AgendaError) as error:
            schedule.first_at_or_after(datetime(2026, 3, 8, 5, tzinfo=timezone.utc))
        self.assertEqual(error.exception.code, "schedule_needs_resolution")


if __name__ == "__main__":
    unittest.main()
