import asyncio
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.runner import TaskRun
from companion_v2.foundation.outcome import Outcome
from companion_v2.identity_policy.personas import RuntimeIdentity
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW
from tests.test_role_tool_loop import answered


class HostRecoveryTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_receipt_after_new_input_cannot_resume_outdated_remaining_parts(self):
        async def accepted(envelope):
            return self.receipt(envelope, "accepted")
        self.send_behavior = accepted
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        old = host.receive(self.event()).unwrap()
        await self.done(app, old)
        self.send_behavior = None
        new = host.receive(self.event("new")).unwrap()
        await self.done(app, new)
        app.record_receipt(self.receipt(self.sent[0])).unwrap()
        app.reconcile_delivery(self.context(app, old), self.sent[0].part_id).unwrap()
        await asyncio.sleep(0.02)
        self.assertEqual((app.flow.row(old)["state"], app.flow.row(old)["reason"]), ("closed", "newer_input_received"))
        self.assertEqual(app.messages.batch(old)["state"], "cancelled")
        self.assertEqual(len(self.sent), 3)

    async def test_restart_keeps_original_merge_deadline_and_replay_runtime_identity(self):
        host = await self.start()
        original = self.event()
        batch = host.receive(original).unwrap()
        before = dict(host.application.flow.row(batch))
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.4))
        new = await self.start(self.host(runtime=self.open_runtime(), policy=ConversationPolicy(0.1, 0.1, 90, 0),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host")), 1, 1)
        self.assertEqual(new.receive(original).unwrap(), batch)
        self.assertEqual(dict(new.application.flow.row(batch)), before)
        self.assertEqual(new.receive(self.event("continued")).unwrap(), batch)
        self.assertEqual(new.application.flow.row(batch)["due_at"], (NOW + timedelta(seconds=1)).isoformat(timespec="microseconds"))
        self.advance(new.application, 0.6)
        await self.done(new.application, batch)
        self.assertEqual(len(self.called), 1)
        self.assertEqual(len(self.sent), 2)

    async def test_replayed_changed_payload_does_not_replace_the_original(self):
        host = await self.start()
        event = self.event()
        batch = host.receive(event).unwrap()
        await host.application.close()
        new = await self.start(self.host(runtime=self.open_runtime()), 1, 1)
        self.assertFalse(new.receive(event | {"text": "替换旧消息"}).is_success)
        self.advance(new.application)
        await self.done(new.application, batch)
        self.assertEqual(self.called[0].original.context.materials[0].payload, event["text"])

    async def test_restart_keeps_part_gap_and_does_not_repeat_first_delivery(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 3)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: app.flow.row(batch)["reason"] == "part_spacing")
        due = app.flow.row(batch)["due_at"]
        await app.close()
        new = await self.start(self.host(runtime=self.open_runtime(), policy=ConversationPolicy(0, 0, 180, 0)), 1, 1)
        self.assertEqual(new.application.flow.row(batch)["due_at"], due)
        self.advance(new.application, 1)
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.sent), 1)
        self.advance(new.application, 2)
        await self.done(new.application, batch)
        self.assertEqual(len(self.called), 1)
        self.assertEqual([e.text for e in self.sent], ["第一段\n", "第二段"])

    async def test_accepted_or_unknown_send_is_never_replayed_after_restart(self):
        async def accepted(envelope):
            return self.receipt(envelope, "accepted")
        self.send_behavior = accepted
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        self.assertEqual((await self.done(app, batch))["reason"], "message_delivery_unresolved")
        await app.close()
        new = await self.start(self.host(runtime=self.open_runtime()), 1, 1)
        self.advance(new.application)
        new.application.record_receipt(self.receipt(self.sent[0])).unwrap()
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(new.application.flow.row(batch)["state"], "blocked")
        self.send_behavior = None
        new.application.reconcile_delivery(self.context(new.application, batch), self.sent[0].part_id).unwrap()
        self.assertEqual((await self.done(new.application, batch))["state"], "complete")
        self.assertEqual(len(self.sent), 2)

    async def test_cancelled_flow_cannot_be_revived_by_a_late_receipt(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def sending(envelope):
            entered.set()
            await release.wait()
            return self.receipt(envelope)
        self.send_behavior = sending
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        await asyncio.wait_for(entered.wait(), 2)
        app.cancel(self.context(app, batch), batch).unwrap()
        release.set()
        await self.until(lambda: not app.active_handles)
        await asyncio.sleep(0.02)
        app.record_receipt(self.receipt(self.sent[0])).unwrap()
        app.reconcile_delivery(self.context(app, batch), self.sent[0].part_id).unwrap()
        self.assertEqual(app.flow.row(batch)["state"], "closed")
        self.assertEqual(len(self.sent), 1)

    async def test_startup_reclaims_a_reservation_whose_task_never_reported(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        self.advance(app)
        await self.done(app, batch)
        call = dict(app.runtime.read_one(
            "SELECT * FROM model_calls ORDER BY finished_at DESC LIMIT 1", ()))
        old = (NOW - timedelta(hours=2)).isoformat(timespec="microseconds")
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE execution_tasks SET kind = 'calendar.plan' WHERE task_id = ?",
                               (call["task_id"],))
            connection.execute("""UPDATE model_calls SET state = 'unknown', actual_tokens = NULL,
                actual_cost = NULL, receipt_ref = NULL, started_at = ?, finished_at = ?
                WHERE call_id = ?""", (old, old, call["call_id"]))
            connection.execute("""UPDATE model_budget_accounts SET held_tokens = held_tokens + ?,
                held_cost = held_cost + ? WHERE account_key IN
                (SELECT account_key FROM model_call_accounts WHERE call_id = ?)""",
                (call["reserved_tokens"], call["reserved_cost"], call["call_id"]))
            linked = tuple(row["account_key"] for row in connection.execute(
                "SELECT account_key FROM model_call_accounts WHERE call_id = ?", (call["call_id"],)))
            placeholders = ",".join("?" for _ in linked)
            held = connection.execute(
                "SELECT account_key, held_tokens FROM model_budget_accounts WHERE account_key IN ("
                + placeholders + ")", linked).fetchall()
        self.assertTrue(linked)
        self.assertTrue(all(row["held_tokens"] >= call["reserved_tokens"] for row in held))
        await app.close()

        new = await self.start(self.host(runtime=self.open_runtime()), 1, 1)
        rebuilt = dict(new.application.runtime.read_one(
            "SELECT * FROM model_calls WHERE call_id = ?", (call["call_id"],)))
        self.assertEqual(rebuilt["state"], "unknown")
        self.assertTrue(rebuilt["receipt_ref"].startswith("local:released:expired-reservation"))
        after = new.application.runtime.read_all(
            "SELECT account_key, held_tokens FROM model_budget_accounts WHERE account_key IN ("
            + placeholders + ")", linked)
        self.assertTrue(all(row["held_tokens"] == 0 for row in after))
        self.assertEqual(new.application.gateway.ledger.reclaim_audit()["released_calls"], 1)

    async def test_restart_fences_started_generation_without_requesting_a_new_draft(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        work = app.flow.advance().unwrap()[0]
        claim = app.tasks.claim_task(work.context, work.task_id).unwrap().claim
        app.tasks.begin(claim).unwrap()
        # Simulates a lost process after begin; there is no coroutine we can attest stopped.
        await app.close()
        new = self.host(runtime=self.open_runtime(), policy=ConversationPolicy(0, 0, 180, 0))
        report = (await new.application.start(expected_task_generation=1, expected_model_generation=1)).unwrap()
        self.assertEqual(report.recovered_tasks, 1)
        self.advance(new.application)
        self.assertEqual((await self.done(new.application, batch))["reason"], "conversation_task_uncertain")
        self.assertEqual(self.called, [])
        self.assertEqual(self.sent, [])
        observed = new.application.tasks.observe(self.context(new.application, batch),
            app.messages.batch(batch)["generation_task_id"]).unwrap()
        self.assertFalse(observed["slot_held"])
        self.assertFalse(observed["worker_stopped"])
        self.assertEqual(observed["capacity_release"], "superseded_read_only_generation")

    async def test_restart_fences_dispatched_send_and_keeps_physical_slot(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 5)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: app.flow.row(batch)["reason"] == "part_spacing")
        self.clock.advance(timedelta(seconds=5))
        work = app.flow.advance().unwrap()[0]
        claim = app.tasks.claim_task(work.context, work.task_id).unwrap().claim
        task = app.tasks.begin(claim).unwrap()
        dispatched = app.delivery._dispatch(TaskRun(app.tasks, claim, task))
        await app.close()
        new = self.host(runtime=self.open_runtime(), policy=ConversationPolicy(0, 0, 180, 5))
        report = (await new.application.start(expected_task_generation=1, expected_model_generation=1)).unwrap()
        self.assertEqual((report.recovered_tasks, report.unresolved_deliveries), (1, 1))
        self.advance(new.application)
        await self.done(new.application, batch)
        new.application.record_receipt(self.receipt(dispatched)).unwrap()
        self.assertEqual(new.application.reconcile_delivery(work.context, dispatched.part_id).failure.code, "task_worker_not_stopped")
        new.application.tasks.acknowledge_stopped(claim, proof_ref="simulated_process_exited").unwrap()
        new.application.reconcile_delivery(work.context, dispatched.part_id).unwrap()
        self.assertEqual((await self.done(new.application, batch))["state"], "complete")
        self.assertEqual(len(self.sent), 1)

    async def test_expired_open_input_does_not_generate_on_restart(self):
        host = await self.start(self.host(policy=ConversationPolicy(0.1, 0.2, 1, 0)))
        batch = host.receive(self.event()).unwrap()
        await host.application.close()
        self.clock.advance(timedelta(seconds=2))
        new = await self.start(self.host(runtime=self.open_runtime()), 1, 1)
        self.assertEqual((await self.done(new.application, batch))["reason"], "deadline_elapsed")
        self.assertEqual(self.called, [])

    async def test_deadline_expiring_during_seal_is_local_and_keeps_host_running(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 1, 0)))
        app = host.application
        knowledge = app.messages._knowledge_contributions

        def cross_deadline(*args, **kwargs):
            self.clock.advance(timedelta(seconds=2))
            return knowledge(*args, **kwargs)

        with patch.object(app.messages, "_knowledge_contributions", side_effect=cross_deadline):
            batch = host.receive(self.event()).unwrap()
            await self.until(lambda: app.flow.row(batch)["state"] in ("closed", "blocked") or app.state == "faulted")

        self.assertEqual(app.state, "running", app.failure_code)
        self.assertEqual((app.flow.row(batch)["state"], app.flow.row(batch)["reason"]),
                         ("closed", "deadline_elapsed"))
        self.assertEqual(app.messages.batch(batch)["state"], "cancelled")
        self.assertEqual(self.called, [])

        next_batch = host.receive(self.event("after-deadline")).unwrap()
        self.assertEqual((await self.done(app, next_batch))["state"], "complete")
        self.assertEqual(len(self.called), 1)

    async def test_capacity_failure_keeps_unsealed_input_then_retries_without_duplication(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        with patch.object(app.tasks, "submit", return_value=Outcome.failed("task_queue_full", "fixture full")):
            await self.until(lambda: app.flow.row(batch)["reason"] == "capacity_wait")
            self.assertEqual(app.messages.batch(batch)["state"], "open")
            self.assertEqual(self.called, [])
            later = host.receive(self.event("after-capacity-wait")).unwrap()
            self.assertNotEqual(later, batch)  # A retry timer cannot reopen the expired merge window.
        self.advance(app)
        self.assertEqual((await self.done(app, later))["state"], "complete")
        self.assertEqual(app.flow.row(batch)["state"], "complete")
        self.assertEqual(len(self.called), 2)

    async def test_noncooperative_provider_holds_storage_until_actual_stop(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn(envelope):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return answered("迟到草稿")
        self.model_behavior = stubborn
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0), owns_runtime=True))
        app = host.application
        host.receive(self.event()).unwrap()
        await asyncio.wait_for(entered.wait(), 2)
        try:
            report = await asyncio.wait_for(app.close(grace_seconds=0), 2)
            self.assertEqual(report.state, "draining")
            self.assertTrue(report.pending_handles)
            self.assertFalse(report.storage_closed)
            self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM execution_attempts WHERE slot_held = 1", ())["n"], 1)
            self.assertEqual(host.receive(self.event("after-stop")).failure.code, "host_not_running")
        finally:
            release.set()
        await self.until(lambda: not app.active_handles)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.runtime.read_one("SELECT actual_tokens FROM model_calls", ())["actual_tokens"], 15)
        final = await app.close()
        self.assertEqual((final.state, final.storage_closed), ("stopped", True))


if __name__ == "__main__":
    unittest.main()
