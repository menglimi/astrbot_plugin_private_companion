from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.agenda.models import AgendaTarget, Schedule
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_management_access import SCOPE
from tests.test_role_tool_loop import answered


class BackgroundHistoryTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def contact_with_evidence(self, app):
        source = self.source(app)
        opportunity = self.opportunity()
        task = (await self.contact(app, opportunity, (self.intent(opportunity, sources=(source,)),))).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        return source, task

    async def test_confirmed_contact_enters_next_reply_as_history_without_copying_body_into_snapshot(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        await self.contact_with_evidence(app)
        self.advance(app, 1)
        batch = host.receive(self.event("reply-to-contact")).unwrap()
        await self.done(app, batch)
        material = [m for m in self.called[-1].original.context.materials if m.owner == "delivery.history"]
        self.assertEqual(len(material), 1)
        item = json.loads(material[0].payload)
        self.assertEqual(item["text"], "刚想起上次的话题。")
        self.assertEqual(item["recipient_read"], "unknown")
        self.assertIsNone(material[0].source)
        saved = app.messages.batch(batch)["contributions_json"]
        self.assertNotIn(item["text"], saved)
        self.assertEqual(len(app.messages.history(self.context(app, batch)).unwrap()["items"]), 1)

    async def test_unknown_or_accepted_is_absent_until_original_receipt_confirms_once(self):
        app = await self.enabled()
        self.settings(app, {"dedup_enabled": False}).unwrap()
        for status in ("unknown", "accepted"):
            async def unconfirmed(envelope):
                return self.receipt(envelope, status)
            self.send_behavior = unconfirmed
            task = (await self.contact(app, self.opportunity(status))).unwrap().task
            await self.delivery_state(app, task.task_id, status)
        context = self.contact_context()
        self.assertEqual(app.background_history.recent(context).unwrap(), ())
        self.assertEqual(app.background_history.interaction(context, self.clock.now())["unresolved_contacts"], 2)
        receipt = self.receipt(self.sent[0])
        app.record_receipt(receipt).unwrap()
        app.record_receipt(receipt).unwrap()
        items = app.background_history.recent(context).unwrap()
        self.assertEqual(len(items), 1)
        state = app.background_history.interaction(context, self.clock.now())
        self.assertEqual((state["contacts_since_last_input"], state["unresolved_contacts"]), (1, 1))

    async def test_past_contact_remains_readable_after_pause_and_opportunity_expiry(self):
        app = await self.enabled()
        await self.contact_with_evidence(app)
        self.clock.advance(timedelta(hours=1))
        self.settings(app, {"enabled": False, "pause_until": (self.clock.now() + timedelta(days=1)).isoformat()}).unwrap()
        self.assertEqual(len(app.background_history.recent(self.contact_context()).unwrap()), 1)

    async def test_revoked_source_hides_original_and_contacts_that_used_its_history(self):
        app = await self.enabled()
        self.settings(app, {"dedup_enabled": False}).unwrap()
        source, _ = await self.contact_with_evidence(app)
        self.advance(app, 1)
        second = (await self.contact(app, self.opportunity("derived"))).unwrap().task
        await self.delivery_state(app, second.task_id, "delivered")
        record = json.loads(app.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?", (second.task_id,))[0])
        self.assertEqual(record["sources"], [])
        self.assertEqual(len(record["history"]["references"]), 1)
        app.proactive.sources.revoke(source, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        self.assertEqual(app.background_history.recent(self.contact_context()).unwrap(), ())
        third = (await self.contact(app, self.opportunity("after-revocation"))).unwrap().task
        await self.delivery_state(app, third.task_id, "delivered")
        self.assertFalse(any(m.owner == "delivery.history" for m in self.called[-1].original.context.materials))

    async def test_history_withdrawal_during_foreground_generation_discards_derived_reply(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        source, _ = await self.contact_with_evidence(app)
        async def revoke(envelope):
            self.assertTrue(any(m.owner == "delivery.history" for m in envelope.original.context.materials))
            app.proactive.sources.revoke(source, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            return answered("受已撤回来源影响的回复")
        self.model_behavior = revoke
        batch = host.receive(self.event("new")).unwrap()
        self.assertEqual((await self.done(app, batch))["state"], "blocked")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(app.runtime.read_one("SELECT state FROM model_calls ORDER BY rowid DESC LIMIT 1", ())[0], "discarded")

    async def test_shared_history_is_validated_once_per_snapshot_and_rechecked_on_the_next_read(self):
        app = await self.enabled()
        self.settings(app, {"dedup_enabled": False}).unwrap()
        source, _ = await self.contact_with_evidence(app)
        self.advance(app, 1)
        second = (await self.contact(app, self.opportunity("derived"))).unwrap().task
        await self.delivery_state(app, second.task_id, "delivered")
        history, context = app.background_history, self.contact_context()

        with patch.object(history, "_validated_history_record", wraps=history._validated_history_record) as validate:
            snapshot = history.capture(context, self.clock.now())
            identifiers = [call.args[0]["delivery_id"] for call in validate.call_args_list]
            self.assertEqual(len(snapshot["references"]), 2)
            self.assertEqual(len(identifiers), len(set(identifiers)))
            validate.reset_mock()
            self.assertEqual(len(history.read(snapshot, context, self.clock.now())), 2)
            identifiers = [call.args[0]["delivery_id"] for call in validate.call_args_list]
            self.assertEqual(len(identifiers), 2)
            self.assertEqual(len(identifiers), len(set(identifiers)))
            validate.reset_mock()
            app.proactive.sources.revoke(source, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            with self.assertRaises(TaskError):
                history.read(snapshot, context, self.clock.now())
            self.assertGreater(validate.call_count, 0)

    async def test_history_withdrawal_at_foreground_sender_boundary_blocks_effect(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        source, _ = await self.contact_with_evidence(app)
        wire = []
        async def checked(envelope):
            app.proactive.sources.revoke(source, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            try:
                envelope.before_dispatch()
            except TaskError:
                return self.receipt(envelope, "not_sent")
            wire.append(envelope)
            return self.receipt(envelope)
        self.send_behavior = checked
        batch = host.receive(self.event("next")).unwrap()
        self.assertEqual((await self.done(app, batch))["state"], "blocked")
        self.assertEqual(wire, [])

    async def test_different_session_cannot_consume_original_background_history(self):
        app = await self.enabled()
        await self.contact_with_evidence(app)
        context = self.contact_context()
        snapshot = app.background_history.capture(context, self.clock.now())
        scope = replace(SCOPE, native_scope_id="other", conversation_id="other", session_id="other")
        from companion_v2.identity_policy.personas import ConversationRoute
        self.personas.register_route(ConversationRoute(scope, user=self.important.subject), observed_at=self.clock.now())
        self.grant(self.important, scope, "text")
        other = self.contact_context(scope)
        self.assertEqual(app.background_history.recent(other).unwrap(), ())
        with self.assertRaises(TaskError):
            app.background_history.read(snapshot, other, self.clock.now())

    async def test_late_receipt_after_restart_is_projected_without_redelivery(self):
        app = await self.enabled()
        async def unknown(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = unknown
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        envelope = self.sent[0]
        await app.close()
        new = (await self.start(self.host(runtime=self.open_runtime()), 1, 1)).application
        new.record_receipt(self.receipt(envelope)).unwrap()
        self.assertEqual(len(new.background_history.recent(self.contact_context()).unwrap()), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_changed_body_cannot_become_readable_history(self):
        app = await self.enabled()
        _, task = await self.contact_with_evidence(app)
        context = self.contact_context()
        snapshot = app.background_history.capture(context, self.clock.now())
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE proactive_contacts SET body = '替换的正文' WHERE task_id = ?", (task.task_id,))
        self.assertEqual(app.background_history.recent(context).unwrap(), ())
        with self.assertRaises(TaskError):
            app.background_history.read(snapshot, context, self.clock.now())

    async def test_missing_trusted_receipt_is_not_replaced_by_delivery_state_label(self):
        app = await self.enabled()
        await self.contact_with_evidence(app)
        context = self.contact_context()
        snapshot = app.background_history.capture(context, self.clock.now())
        with app.runtime.transaction() as connection:
            connection.execute("DELETE FROM background_delivery_receipts")
        self.assertEqual(app.background_history.recent(context).unwrap(), ())
        with self.assertRaises(TaskError):
            app.background_history.read(snapshot, context, self.clock.now())

    async def test_newly_admitted_backdated_input_also_supersedes_a_pending_contact(self):
        self.clock.advance(timedelta(hours=1))
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        async def backdated(envelope):
            host.receive(self.event("late-input", at=self.clock.now() - timedelta(minutes=30))).unwrap()
            return answered('{"decision":"send","text":"旧联系决定"}')
        self.model_behavior = backdated
        with patch.object(app.flow, "advance", return_value=Outcome.success(())):
            task = (await self.contact(app)).unwrap().task
        self.model_behavior = None
        self.assertNotEqual(task.state, "succeeded")
        self.assertEqual(len(self.sent), 1)

    async def test_confirmed_reminder_is_context_for_contact_and_next_foreground_turn(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        context = self.contact_context()
        self.settings(app, {"enabled": True}).unwrap()
        app.agenda.create_reminder(self.important, target=AgendaTarget(SCOPE, context.role_binding.role, self.important.subject),
            reminder_id="promised", message="约好提醒喝水", schedule=Schedule(self.clock.now(), "UTC"),
            operation=self.operation(SCOPE, owner="agenda"), expected_role_revision=context.role_binding.revision,
            observed_at=self.clock.now()).unwrap()
        app.notify()
        await self.until(lambda: app.runtime.read_one("SELECT 1 FROM background_deliveries WHERE state = 'delivered'", ()))
        await self.until(lambda: app.runtime.read_one("SELECT COUNT(*) FROM execution_attempts WHERE lane = 'background' AND slot_held = 1", ())[0] == 0)
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        history = [json.loads(m.payload) for m in self.contact_calls()[0].original.context.materials if m.owner == "delivery.history"]
        self.assertEqual([item["text"] for item in history], ["约好提醒喝水"])
        batch = host.receive(self.event("after-reminder")).unwrap()
        await self.done(app, batch)
        history = [json.loads(m.payload) for m in self.called[-1].original.context.materials if m.owner == "delivery.history"]
        self.assertEqual({item["owner"] for item in history}, {"agenda.reminder", "proactive.contact"})

    async def test_capture_and_read_share_validation_only_inside_one_snapshot(self):
        app = await self.enabled()
        self.settings(app, {"dedup_enabled": False}).unwrap()
        for index in range(3):
            task = (await self.contact(app, self.opportunity("shared-" + str(index)))).unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")
        context = self.contact_context()
        at = self.clock.now()
        with app.runtime.read_snapshot(), app.background_history.validation_scope():
            with patch.object(app.background_history, "_validated_history_record",
                              wraps=app.background_history._validated_history_record) as guard:
                snapshot = app.background_history.capture(context, at)
                items = app.background_history.read(snapshot, context, at)
                first_pass = guard.call_count
                app.background_history.read(snapshot, context, at + timedelta(seconds=1))
        unique = {ref["delivery_id"] for ref in snapshot["references"]}
        self.assertGreaterEqual(len(unique), 3)
        self.assertEqual(len(items), len(snapshot["references"]))
        self.assertEqual(first_pass, len(unique),
                         "capture and read at one time must validate each reference only once")
        self.assertEqual(guard.call_count, 2 * len(unique),
                         "a later observation time must revalidate the references")

    async def test_capture_authorizes_once_instead_of_once_per_reference(self):
        app = await self.enabled()
        self.settings(app, {"dedup_enabled": False}).unwrap()
        for index in range(3):
            task = (await self.contact(app, self.opportunity("auth-" + str(index)))).unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")
        context = self.contact_context()
        with patch.object(app.tasks, "authorize_context", wraps=app.tasks.authorize_context) as guard:
            snapshot = app.background_history.capture(context, self.clock.now())
        self.assertGreaterEqual(len(snapshot["references"]), 3)
        self.assertLessEqual(guard.call_count, 2,
                             "one capture must not re-authorize every reference")

    async def test_unanswered_is_observed_background_and_resets_after_actual_input(self):
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        self.settings(app, {"dedup_enabled": False}).unwrap()
        self.advance(app, 1)
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.advance(app, 1)
        second = (await self.contact(app, self.opportunity("second"))).unwrap().task
        await self.delivery_state(app, second.task_id, "delivered")
        material = next(m for m in self.contact_calls()[-1].original.context.materials if m.purpose == "communication_state")
        state = json.loads(material.payload)
        self.assertEqual(state["contacts_since_last_input"], 1)
        self.assertEqual(state["recipient_read"], "unknown")
        self.advance(app, 1)
        batch = host.receive(self.event("actual-response")).unwrap()
        await self.done(app, batch)
        self.assertEqual(app.background_history.interaction(self.contact_context(), self.clock.now())["contacts_since_last_input"], 0)
