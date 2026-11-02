import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_management_access import INSTALLATION, SCOPE
from tests.test_role_tool_loop import answered


class ContactSchedulingTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def test_synthetic_diagnostic_uses_ephemeral_event_and_keeps_real_persona_anchor(self):
        host, batch_id = await self.scheduled(automatic=False)
        app = host.application
        context = self.contact_context()
        input_count = app.runtime.read_one(
            "SELECT COUNT(*) FROM conversation_inputs WHERE batch_id = ?", (batch_id,))[0]
        at = self.clock.now()

        candidate = await app.contact_schedule.synthetic_diagnostic_candidate(
            context, "news", at=at, test_id="attempt-synthetic-news")

        self.assertEqual(candidate["intents"][0].purpose, "news")
        self.assertTrue(candidate["intents"][0].thought.summary.startswith("本轮联系情境："))
        self.assertEqual(candidate["route"]["label"], "合成测试事件")
        self.assertEqual(candidate["intents"][0].thought.evidence.sources, ())
        self.assertEqual(candidate["route"]["id"], "synthetic_event")
        self.assertEqual(app.runtime.read_one(
            "SELECT COUNT(*) FROM conversation_inputs WHERE batch_id = ?", (batch_id,))[0], input_count)
        app.contact_schedule.validate_diagnostic_origin(
            context, candidate["diagnostic_origin"], at, purpose="news")

    async def test_synthetic_diagnostic_without_conversation_uses_current_persona(self):
        host, _batch_id = await self.scheduled(automatic=False)
        app = host.application
        context = self.contact_context()
        at = self.clock.now()
        role_instruction = "当前角色人格提示"

        with patch.object(app.contact_schedule, "latest_conversation", return_value=None):
            candidate = await app.contact_schedule.synthetic_diagnostic_candidate(
                context, "chat", at=at, test_id="attempt-without-history",
                role_instruction=role_instruction)
            receipt = candidate["diagnostic_origin"]["_diagnostic"]
            self.assertEqual(candidate["role_instruction"], role_instruction)
            self.assertIsNone(receipt["batch_id"])
            self.assertEqual(receipt["source_mode"], "no_conversation")
            app.contact_schedule.validate_diagnostic_origin(
                context, candidate["diagnostic_origin"], at, purpose="chat",
                role_instruction=role_instruction)
            with self.assertRaises(TaskError):
                app.contact_schedule.validate_diagnostic_origin(
                    context, candidate["diagnostic_origin"], at, purpose="chat",
                    role_instruction="changed persona")

    async def test_legacy_group_flows_are_not_considered_for_private_contact(self):
        host, batch_id = await self.scheduled()
        app = host.application
        context = self.contact_context()
        original = app.runtime.read_one("SELECT context_json FROM conversation_batches WHERE batch_id = ?",
                                        (batch_id,))["context_json"]
        document = json.loads(original)
        document["audience"]["scope"]["kind"] = "group"
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET context_json = ? WHERE batch_id = ?",
                               (json.dumps(document, ensure_ascii=False), batch_id))

        self.assertEqual(app.contact_schedule._legacy_rows(0), ())

    async def test_full_task_history_blocks_repeated_contact_planning_with_reason(self):
        host, _ = await self.scheduled()
        app = host.application
        retained = app.runtime.read_one("SELECT COUNT(*) FROM execution_tasks", ())[0]
        with (patch.object(app.tasks, "limits", replace(app.tasks.limits, retained_tasks=retained)),
              patch.object(app.tasks, "check_generation", return_value=Outcome.success(True))):
            app.contact_schedule.advance()

        diagnostic = app.contact_schedule.diagnostic_for(
            self.contact_context(), at=self.clock.now(), host_state="running")
        self.assertEqual(diagnostic["scheduler"]["state"], "blocked")
        self.assertEqual(diagnostic["scheduler"]["reason_code"], "task_storage_full")

    async def test_contact_admission_failure_is_recorded_after_candidate_queue(self):
        host, _ = await self.scheduled()
        app = host.application
        context = self.contact_context()
        row = app.contact_schedule.latest_conversation(context)
        self.clock.advance(timedelta(seconds=60))
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan)

        async def full_capacity(*args, **kwargs):
            return Outcome.failed("task_queue_full", "capacity reached")

        with patch.object(app.proactive, "contact", new=full_capacity):
            result = await app.contact_schedule._run(plan)

        self.assertEqual(result.failure.code, "task_queue_full")
        diagnostic = app.contact_schedule.diagnostic_for(context, at=self.clock.now(), host_state="running")
        self.assertEqual(diagnostic["evaluation"]["stage"], "task_admission")
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "task_queue_full")

    async def test_private_window_exposes_the_last_automatic_schedule_gate(self):
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        app.contact_schedule.advance()

        diagnostic = app.contact_schedule.diagnostic_for(
            self.contact_context(), at=self.clock.now(), host_state=app.state)
        self.assertEqual(diagnostic["scheduler"]["state"], "running")
        self.assertEqual(diagnostic["evaluation"]["outcome"], "skipped")
        self.assertEqual(diagnostic["evaluation"]["stage"], "settings")
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "automatic_contact_disabled")
        self.assertNotIn("conversation_ref", diagnostic["evaluation"])

    async def test_scheduler_waits_for_a_completed_conversation_before_planning(self):
        host, batch = await self.scheduled(automatic=True)
        app = host.application
        schedule = app.contact_schedule
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET state = 'open' WHERE batch_id = ?", (batch,))
        self.assertEqual(tuple(schedule._legacy_rows(0)), ())
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET state = 'ready' WHERE batch_id = ?", (batch,))
        self.assertTrue(tuple(schedule._legacy_rows(0)))

    async def test_persisted_superseded_contact_is_read_as_a_wait(self):
        host, _ = await self.scheduled(automatic=True)
        app = host.application
        context = self.contact_context()
        schedule = app.contact_schedule
        latest = schedule.latest_conversation(context)
        schedule._note_window(context, latest, self.clock.now(), "validation", "failed",
                              "contact_conversation_changed")

        diagnostic = schedule.diagnostic_for(context, at=self.clock.now(), host_state="running")
        self.assertEqual(diagnostic["evaluation"]["outcome"], "stale")
        self.assertEqual(diagnostic["evaluation"]["stage"], "conversation")
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "newer_conversation_not_evaluated")

    async def test_scheduler_does_not_persist_a_superseded_conversation_as_failure(self):
        host, _ = await self.scheduled(automatic=True)
        app = host.application
        context = self.contact_context()
        schedule = app.contact_schedule
        with app.runtime.transaction() as connection:
            connection.execute("DELETE FROM proactive_schedule_diagnostics")
        with patch.object(schedule, "_peek", return_value=False), patch.object(
                schedule, "_plan",
                side_effect=TaskError("contact_conversation_changed", "a newer turn superseded this row")):
            schedule.advance()

        failed = app.runtime.read_all("""SELECT stage, outcome, reason_code FROM proactive_schedule_diagnostics
            WHERE diagnostic_key = ? AND outcome = 'failed'""",
            (schedule._diagnostic_key("window", context.key),))
        self.assertEqual(tuple(failed), ())

    async def test_scheduler_heartbeat_coalesces_unchanged_diagnostic_writes(self):
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        schedule = app.contact_schedule
        with app.runtime.transaction() as connection:
            connection.execute("DELETE FROM proactive_schedule_diagnostics")
        schedule._diagnostics_cleaned_at = self.clock.now()
        start = self.clock.now()
        schedule._note_coordinator(start, "running", "contact_schedule_running")
        first = app.runtime.read_one("SELECT observed_at FROM proactive_schedule_diagnostics WHERE diagnostic_key = ?",
            (schedule._diagnostic_key("coordinator"),))["observed_at"]

        schedule._note_coordinator(start + timedelta(seconds=15), "running", "contact_schedule_running")
        unchanged = app.runtime.read_one("SELECT observed_at FROM proactive_schedule_diagnostics WHERE diagnostic_key = ?",
            (schedule._diagnostic_key("coordinator"),))["observed_at"]
        self.assertEqual(unchanged, first)

        schedule._note_coordinator(start + timedelta(seconds=30), "running", "contact_schedule_running")
        refreshed = app.runtime.read_one("SELECT observed_at FROM proactive_schedule_diagnostics WHERE diagnostic_key = ?",
            (schedule._diagnostic_key("coordinator"),))["observed_at"]
        self.assertNotEqual(refreshed, first)

    async def test_unanswered_slowdown_applies_to_ordinary_check_in_and_new_input_resets_pause(self):
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        self.advance(app, 1)
        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        self.settings(app, {"automatic_enabled": True, "unanswered_pause_after": 1,
                            "dedup_enabled": False}).unwrap()
        self.advance(app, 140)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contact_calls()), 1)
        batch = host.receive(self.event("returned")).unwrap()
        await self.done(app, batch)
        self.assertEqual(app.background_history.interaction(self.contact_context(), self.clock.now())[
            "contacts_since_last_input"], 0)
        self.advance(app, 60)
        await self.automatic_delivery(app, count=2)

    async def test_unanswered_slowdown_does_not_delay_a_date_event(self):
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        self.advance(app, 1)
        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        date = json.dumps([{"month": 9, "day": 21, "label": "纪念日"}], ensure_ascii=False)
        self.settings(app, {"automatic_enabled": True, "date_checkin_enabled": True,
                            "date_checkin_time": "08:01", "important_dates": date,
                            "unanswered_pause_after": 1, "chat.enabled": False}).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app, count=2)
        self.assertEqual(json.loads(self.contacts(app)[-1]["record_json"])["purpose"], "greeting")

    async def test_wall_clock_opportunity_runs_without_a_manual_scan_or_wake(self):
        current = lambda: datetime.now(timezone.utc)
        self.receipt_now = current
        host = await self.start(self.host(now=current, policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 1, "interval_seconds": 60}).unwrap()
        batch = host.receive(self.event(at=current())).unwrap()
        await self.done(app, batch)
        await self.automatic_delivery(app)
        self.assertEqual(len(self.contact_calls()), 1)

    async def test_automatic_requires_its_own_opt_in_and_completed_idle_conversation(self):
        host, batch = await self.scheduled(automatic=False)
        app = host.application
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])
        self.settings(app, {"automatic_enabled": True}).unwrap()
        app.notify()
        await self.automatic_delivery(app)
        request = self.contact_calls()[0].original
        self.assertEqual(request.role_instruction, "原角色人格提示")
        # A completed window supplies trusted identity and role context; an
        # autonomous expression need not quote the user's last message.
        saved = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual(saved["automatic_origin"]["batch_id"], batch)
        self.assertEqual(self.contacts(app)[0]["origin_key"], self.contact_context().key)

    async def test_repeated_ticks_and_restart_do_not_repeat_current_opportunity(self):
        host, _ = await self.scheduled()
        app = host.application
        self.advance(app, 60)
        row = await self.automatic_delivery(app)
        for _ in range(3):
            app.contact_schedule.advance()
            await asyncio.sleep(0)
        await app.close()
        new = (await self.start(self.host(runtime=self.open_runtime()), 1, 1)).application
        new.contact_schedule.advance()
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.contact_calls()), 1)
        self.assertEqual(new.background_history.recent(self.contact_context()).unwrap()[0]["text"], "刚想起上次的话题。")
        self.assertEqual(self.contacts(new)[0]["contact_id"], json.loads(row["request_json"])["source_ref"])

    async def test_restart_produces_only_current_slot_without_missed_backlog(self):
        host, _ = await self.scheduled()
        await host.application.close()
        self.clock.advance(timedelta(days=3, seconds=60))
        app = (await self.start(self.host(runtime=self.open_runtime()), 1, 1)).application
        row = await self.automatic_delivery(app)
        self.assertEqual(len(self.contact_calls()), 1)
        self.assertEqual(json.loads(row["request_json"])["not_before"], self.clock.now().isoformat(timespec="microseconds"))

    async def test_skip_is_durable_and_next_slot_can_make_a_new_decision(self):
        host, _ = await self.scheduled()
        app = host.application
        async def skip(envelope):
            return answered('{"decision":"skip","text":""}')
        self.model_behavior = skip
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 1)
        await self.until(lambda: not app.proactive.active_handles)
        app.contact_schedule.advance()
        self.assertEqual(len(self.contact_calls()), 1)
        self.assertEqual(len(self.sent), 1)  # Foreground reply only.
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 2)
        self.assertTrue(all(json.loads(row["record_json"])["decision"] == "skip" for row in self.contacts(app)))

    async def test_pause_quiet_and_purpose_preference_prevent_automatic_model_calls(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"pause_until": (self.clock.now() + timedelta(minutes=2)).isoformat()}).unwrap()
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])
        self.settings(app, {"quiet_enabled": True, "quiet_timezone": "UTC", "quiet_start": "00:00", "quiet_end": "00:03"}).unwrap()
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])
        self.settings(app, {"chat.enabled": False}).unwrap()
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])
        self.settings(app, {"chat.enabled": True}).unwrap()
        app.notify()
        await self.automatic_delivery(app)

    async def test_revoked_input_or_role_rebind_cannot_generate_for_old_conversation(self):
        host, batch = await self.scheduled()
        app = host.application
        context = self.context(app, batch)
        binding = self.identity.resolve_role(SCOPE, observed_at=self.clock.now()).unwrap()
        self.identity.bind_role(scope=SCOPE, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "new-role"),
            expected_revision=binding.revision, observed_at=self.clock.now())
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])
        self.assertFalse(app.background_history.recent(context).is_success)

    async def test_withdrawn_origin_is_not_replaced_with_older_input(self):
        host, batch = await self.scheduled()
        app = host.application
        key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))[0]
        app.withdraw(self.context(app, batch), key).unwrap()
        self.advance(app, 60)
        app.contact_schedule.advance()
        self.assertEqual(self.contact_calls(), [])

    async def test_new_user_input_during_generation_discards_contact_and_keeps_usage(self):
        host, _ = await self.scheduled()
        app = host.application
        async def interrupt(envelope):
            if isinstance(envelope.original.event, BackgroundPromptTrigger):
                host.receive(self.event("new-input")).unwrap()
                return answered('{"decision":"send","text":"已经过时的主动消息"}')
            return answered("接到新消息。")
        self.model_behavior = interrupt
        self.advance(app, 60)
        await self.until(lambda: len(self.contact_calls()) == 1)
        await self.until(lambda: not app.proactive.active_handles)
        self.assertEqual(self.contacts(app), ())
        self.assertFalse(any(e.part_id.startswith("outbound-") for e in self.sent))
        self.assertEqual(app.runtime.read_one("""SELECT c.state FROM model_calls c JOIN execution_tasks t ON t.task_id = c.task_id
            WHERE t.kind = 'proactive.compose'""", ())[0], "discarded")

    async def test_new_input_at_sender_boundary_prevents_the_old_automatic_wire_effect(self):
        host, _ = await self.scheduled()
        app = host.application
        wire = []
        async def checked(envelope):
            if envelope.part_id.startswith("outbound-"):
                host.receive(self.event("arrived-before-send")).unwrap()
                try:
                    envelope.before_dispatch()
                except TaskError:
                    return self.receipt(envelope, "not_sent")
                wire.append(envelope)
            return self.receipt(envelope)
        self.send_behavior = checked
        self.advance(app, 60)
        await self.until(lambda: len(self.contacts(app)) == 1)
        await self.delivery_state(app, self.contacts(app)[0]["task_id"], "failed")
        self.assertEqual(wire, [])

    async def test_shutdown_keeps_storage_until_stubborn_automatic_model_stops(self):
        host, _ = await self.scheduled(owns_runtime=True)
        app = host.application
        entered, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        async def stubborn(envelope):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return answered('{"decision":"send","text":"停机后的结果"}')
        self.model_behavior = stubborn
        self.advance(app, 60)
        await asyncio.wait_for(entered.wait(), 3)
        report = await app.close(grace_seconds=0)
        self.assertEqual((report.state, report.storage_closed), ("draining", False))
        release.set()
        await self.until(lambda: not app.proactive.active_handles)
        self.assertTrue((await app.close(grace_seconds=0)).storage_closed)
        self.assertEqual(len(self.sent), 1)

    async def test_contact_capacity_is_reserved_until_pending_or_unknown_delivery_resolves(self):
        app = await self.enabled()
        self.settings(app, {"max_contacts_24h": 1}).unwrap()
        with patch.object(app.background, "advance", return_value=False):
            first = (await self.contact(app)).unwrap().task
            blocked = await self.contact(app, self.opportunity("second"))
            self.assertEqual(blocked.failure.code, "contact_delivery_budget")
            app.proactive.cancel(self.contact_context(), first.task_id, expected_revision=first.revision).unwrap()
        async def unknown(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = unknown
        second = (await self.contact(app, self.opportunity("second"))).unwrap().task
        await self.delivery_state(app, second.task_id, "unknown")
        self.clock.advance(timedelta(hours=25))
        self.assertEqual((await self.contact(app, self.opportunity("third"))).failure.code, "contact_delivery_budget")
        app.record_receipt(self.receipt(self.sent[0], "not_sent")).unwrap()
        self.send_behavior = None
        third = (await self.contact(app, self.opportunity("third"))).unwrap().task
        await self.delivery_state(app, third.task_id, "delivered")

    async def test_delivery_allowance_is_per_window_even_for_the_same_relationship(self):
        app = await self.enabled()
        first_context = self.contact_context()
        second_context = self.other_window_context()
        from companion_v2.proactive.configuration import target_for
        self.settings(app, {"enabled": True, "max_contacts_24h": 1}, target=target_for(first_context)).unwrap()
        self.settings(app, {"enabled": True, "max_contacts_24h": 1}, target=target_for(second_context)).unwrap()

        async def unknown(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = unknown
        first = (await self.contact(app, context=first_context)).unwrap().task
        await self.delivery_state(app, first.task_id, "unknown")
        delivery_usage = app.proactive.budget.delivery_usage(first_context, self.preferences(app), self.clock.now())
        self.assertEqual(delivery_usage["session"]["used"], 1)
        self.assertEqual(delivery_usage["session"]["actual"], 0)
        self.assertEqual(delivery_usage["session"]["delivered"], 0)
        self.assertEqual(delivery_usage["session"]["reserved"], 1)
        second = (await self.contact(app, self.opportunity("other-window-contact", scope=second_context.scope),
                                     context=second_context)).unwrap().task
        await self.delivery_state(app, second.task_id, "unknown")
        second_usage = app.proactive.budget.delivery_usage(second_context, self.preferences(app, second_context),
                                                           self.clock.now())
        self.assertEqual(second_usage["session"]["used"], 1)
        self.assertEqual(second_usage["session"]["reserved"], 1)

    async def test_decision_usage_is_per_window_but_does_not_limit_contact_admission(self):
        app = await self.enabled()
        first_context = self.contact_context()
        second_context = self.other_window_context()
        from companion_v2.proactive.configuration import target_for
        self.settings(app, {"enabled": True, "max_contacts_24h": 1}, target=target_for(first_context)).unwrap()
        self.settings(app, {"enabled": True, "max_contacts_24h": 1}, target=target_for(second_context)).unwrap()

        async def skip(envelope):
            return answered('{"decision":"skip","text":""}')
        self.model_behavior = skip
        first = (await self.contact(app, context=first_context)).unwrap().task
        self.assertEqual(first.state, "succeeded")
        decision_usage = app.proactive.budget.attempt_usage(first_context, self.preferences(app), self.clock.now())
        self.assertEqual(decision_usage["session"]["used"], 1)
        self.assertNotIn("limit", decision_usage["session"])
        self.assertNotIn("relationship", decision_usage)
        repeated = (await self.contact(app, self.opportunity("another-first-window-decision"),
                                       context=first_context)).unwrap().task
        self.assertEqual(repeated.state, "succeeded")
        self.assertEqual(app.proactive.budget.attempt_usage(first_context, self.preferences(app),
            self.clock.now())["session"]["used"], 2)
        second = (await self.contact(app, self.opportunity("other-window-attempt", scope=second_context.scope),
                                     context=second_context)).unwrap().task
        self.assertEqual(second.state, "succeeded")
        second_usage = app.proactive.budget.attempt_usage(second_context, self.preferences(app, second_context),
                                                          self.clock.now())
        self.assertEqual(second_usage["session"]["used"], 1)
        self.assertEqual(self.sent, [])

    async def test_delivered_contact_allowance_resets_at_window_local_midnight(self):
        app = await self.enabled()
        from companion_v2.proactive.configuration import target_for
        context = self.contact_context()
        self.settings(app, {"max_contacts_24h": 1}, target=target_for(context)).unwrap()

        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        usage = app.proactive.budget.delivery_usage(context, self.preferences(app), self.clock.now())
        self.assertEqual(usage["session"]["delivered"], 1)
        self.assertEqual(usage["session"]["date"], "2026-09-21")

        self.clock.advance(timedelta(hours=16))
        usage = app.proactive.budget.delivery_usage(context, self.preferences(app), self.clock.now())
        self.assertEqual(usage["session"]["used"], 0)
        self.assertEqual(usage["session"]["date"], "2026-09-22")
        second = (await self.contact(app, self.opportunity("next-local-day"))).unwrap().task
        await self.delivery_state(app, second.task_id, "delivered")

    async def test_rhythm_snapshot_counts_only_confirmed_contacts(self):
        app = await self.enabled()
        context = self.contact_context()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.clock.advance(timedelta(minutes=40))

        rhythm = app.proactive.budget.rhythm_snapshot(context, self.preferences(app), self.clock.now())

        self.assertEqual(rhythm["delivered_today"], 1)
        self.assertEqual(rhythm["delivered_last_4_hours"], 1)
        self.assertEqual(rhythm["minutes_since_last_delivery"], 40)
        self.assertEqual(rhythm["last_delivered_purpose"], "chat")
        self.assertEqual(rhythm["recent_purposes_today"], ["chat"])

    async def test_skips_do_not_consume_daily_contact_allowance(self):
        app = await self.enabled()
        self.settings(app, {"max_contacts_24h": 1}).unwrap()
        async def skip(envelope):
            return answered('{"decision":"skip","text":""}')
        self.model_behavior = skip
        self.assertEqual((await self.contact(app)).unwrap().task.state, "succeeded")
        usage = app.proactive.budget.attempt_usage(self.contact_context(), self.preferences(app), self.clock.now())
        self.assertEqual((usage["session"]["used"], usage["session"]["date"],
                          usage["session"]["timezone"]), (1, "2026-09-21", "Asia/Shanghai"))
        self.model_behavior = None
        sent = (await self.contact(app, self.opportunity("send-after-skip"))).unwrap().task
        await self.delivery_state(app, sent.task_id, "delivered")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((await self.contact(app, self.opportunity("after-contact-cap"))).failure.code,
                         "contact_delivery_budget")

    async def test_preparation_failure_does_not_count_but_unknown_model_call_does(self):
        from companion_v2.model_gateway.failures import ModelFailure
        from companion_v2.model_gateway.models import Amount, ProviderResponse
        from companion_v2.proactive.configuration import target_for

        app = await self.enabled()
        first_context = self.contact_context()
        other_context = self.other_window_context()
        async def not_started(_envelope):
            return ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref="test-not-started",
                                    failure=ModelFailure.PREPARATION)

        self.model_behavior = not_started
        failed = (await self.contact(app, context=first_context)).unwrap().task
        self.assertEqual(failed.state, "failed")
        usage = app.proactive.budget.attempt_usage(first_context, self.preferences(app), self.clock.now())
        self.assertEqual(usage["session"]["used"], 0)

        async def unknown(_envelope):
            return ProviderResponse("unknown", failure=ModelFailure.TRANSPORT)

        self.model_behavior = unknown
        failed = (await self.contact(app, self.opportunity("unknown-provider-result"), context=first_context)).unwrap().task
        self.assertEqual(failed.state, "failed")
        calls = app.runtime.read_all("SELECT state FROM model_calls WHERE task_id = ?", (failed.task_id,))
        self.assertEqual([call["state"] for call in calls], ["unknown"])
        usage = app.proactive.budget.attempt_usage(first_context, self.preferences(app), self.clock.now())
        self.assertEqual(usage["session"]["used"], 1)

        async def skip(_envelope):
            return answered('{"decision":"skip","text":""}')

        self.model_behavior = skip
        decided = (await self.contact(app, self.opportunity("successful-skip"), context=first_context)).unwrap().task
        self.assertEqual(decided.state, "succeeded")
        usage = app.proactive.budget.attempt_usage(first_context, self.preferences(app), self.clock.now())
        self.assertEqual(usage["session"]["used"], 2)
        self.settings(app, {"enabled": True}, target=target_for(other_context)).unwrap()
        independent = (await self.contact(app, self.opportunity("other-window", scope=other_context.scope),
                                          context=other_context)).unwrap().task
        self.assertEqual(independent.state, "succeeded")
        other_usage = app.proactive.budget.attempt_usage(other_context, self.preferences(app, other_context),
                                                         self.clock.now())
        self.assertEqual(other_usage["session"]["used"], 1)

    async def test_numeric_controls_inherit_and_zero_budget_blocks_without_model_usage(self):
        app = await self.enabled()
        self.settings(app, {"interval_seconds": 120, "max_contacts_24h": 0}).unwrap()
        self.assertEqual(self.preferences(app).values["interval_seconds"], 120)
        self.assertEqual((await self.contact(app)).failure.code, "contact_delivery_budget")
        for key, value in (("interval_seconds", 59), ("idle_seconds", -1), ("max_contacts_24h", True),
                           ("max_attempts_24h", 1001), ("proactive_persona_judge_daily_limit", 12)):
            self.assertFalse(self.settings(app, {key: value}).is_success)
        self.assertEqual(self.called, [])

    async def test_disabled_scan_page_does_not_starve_later_window_or_mix_its_persona(self):
        from companion_v2.proactive.configuration import target_for
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        for index in range(17):
            scope = replace(SCOPE, native_scope_id="window-" + str(index), conversation_id="conversation-" + str(index), session_id=str(index))
            self.grant(self.important, scope, "text")
            batch = host.receive(self.event(str(index), scope=scope, prompt="角色 " + str(index))).unwrap()
            await self.done(app, batch)
        context = self.context(app, batch)
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60, "interval_seconds": 60},
                      target=target_for(context)).unwrap()
        self.advance(app, 60)
        await self.automatic_delivery(app)
        self.assertEqual(len(self.contact_calls()), 1)
        self.assertEqual(self.contact_calls()[0].original.role_instruction, "角色 16")
        self.assertEqual(self.sent[-1].context.scope, scope)

    async def test_busy_lane_discards_unstarted_contact_without_crashing_or_replaying(self):
        app = await self.enabled()
        entered, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        async def held(envelope):
            entered.set()
            await release.wait()
            return answered('{"decision":"send","text":"第一个机会"}')
        self.model_behavior = held
        first = asyncio.create_task(self.contact(app))
        await asyncio.wait_for(entered.wait(), 3)
        second = (await self.contact(app, self.opportunity("busy"))).unwrap().task
        self.assertEqual(second.state, "cancelled")
        self.assertEqual(len(self.called), 1)
        release.set()
        task = (await first).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual((await self.contact(app, self.opportunity("busy"))).unwrap().task.task_id, second.task_id)
        self.assertEqual(len(self.called), 1)

    async def test_known_delivery_releases_its_allowance_at_the_rolling_boundary(self):
        app = await self.enabled()
        self.settings(app, {"max_contacts_24h": 1}).unwrap()
        first = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        self.assertEqual((await self.contact(app, self.opportunity("later"))).failure.code, "contact_delivery_budget")
        self.clock.advance(timedelta(hours=24))
        later = (await self.contact(app, self.opportunity("later"))).unwrap().task
        await self.delivery_state(app, later.task_id, "delivered")

    async def test_superseded_anchor_reanchors_to_latest_completed_conversation(self):
        host, first = await self.scheduled(automatic=False)
        app = host.application
        context = self.contact_context()
        schedule = app.contact_schedule
        original, _, _ = schedule._origin(context, first, self.clock.now())

        self.clock.advance(timedelta(seconds=30))
        second = host.receive(self.event("superseding")).unwrap()
        await self.done(app, second)
        self.assertEqual(schedule.latest_conversation(context)["batch_id"], second)

        self.settings(app, {"automatic_enabled": True}).unwrap()
        self.clock.advance(timedelta(seconds=120))
        current = schedule.validate_origin(context, original, self.clock.now())

        self.assertEqual(current["batch_id"], second)
        self.assertTrue(current.get("reanchored"))
        self.assertEqual(current["previous_batch_id"], first)
        self.assertEqual(current["role_hash"], original["role_hash"])

    async def test_incomplete_latest_conversation_is_not_reanchored(self):
        host, first = await self.scheduled(automatic=False)
        app = host.application
        context = self.contact_context()
        schedule = app.contact_schedule

        self.clock.advance(timedelta(seconds=30))
        second = host.receive(self.event("incomplete")).unwrap()
        await self.done(app, second)
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET state = 'open' WHERE batch_id = ?", (second,))
        self.assertFalse(schedule._conversation_ready(schedule.latest_conversation(context)))

        with self.assertRaises(TaskError) as caught:
            schedule._origin(context, first, self.clock.now())
        self.assertEqual(caught.exception.code, "contact_conversation_changed")
