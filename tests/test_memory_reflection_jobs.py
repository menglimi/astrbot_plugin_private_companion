"""Explicit background repair survives HTTP completion and local restarts."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.execution.models import ClaimResult, Lane, TaskClaim, TaskResult, TaskSpec, digest, encode, stamp
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.memory.reflection import reflection_profile
from companion_v2.memory.reflection_management import ReflectionManagement
from companion_v2.model_gateway.routing import configuration_fields
from tests.test_host_application import HostFixture
from tests import test_memory_selection_host as selection
from tests.test_role_tool_loop import answered


class MemoryReflectionJobsTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    setup_reflection = selection.MemorySelectionHostTests.setup_reflection
    setup_manual = selection.MemorySelectionHostTests.setup_manual
    configure = selection.MemorySelectionHostTests.configure
    cold = selection.MemorySelectionHostTests.cold
    prepare = selection.MemorySelectionHostTests.prepare
    restart = selection.MemorySelectionHostTests.restart
    recovered = selection.MemorySelectionHostTests.recovered
    row = selection.MemorySelectionHostTests.row
    manifest = selection.MemorySelectionHostTests.manifest

    def setUp(self):
        super().setUp()
        self.profiles += (reflection_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="memory-jobs-test.v1", fields=configuration_fields(self.profiles))

    def job(self, app, job_id):
        return dict(app.runtime.read_one("SELECT * FROM memory_reflection_jobs WHERE job_id = ?", (job_id,)))

    def enqueue(self, app, context, batch, operation="continue", operator=None):
        return ReflectionManagement(app, operator or self.core).enqueue(context, batch_id=batch, operation_key=operation)

    async def settle(self, app, job_id):
        for _ in range(30):
            self.advance(app)
            await self.until(lambda: not app.memory_schedule.continuations.active_handles)
            # Let the host launch/reap this step before checking its durable result.
            await asyncio.sleep(0.02)
            await self.until(lambda: not app.memory_schedule.continuations.active_handles)
            value = self.job(app, job_id)
            if value["state"] != "queued":
                return value
        self.fail("explicit continuation did not settle")

    async def test_cold_selection_finishes_after_one_enqueue_and_keeps_original_operator(self):
        app, context = await self.cold()
        batch = self.prepare(app, context)["batch_id"]
        calls, sends = len(self.called), len(self.sent)
        result = self.enqueue(app, context, batch).unwrap()
        self.assertEqual(result["state"], "queued")
        self.assertEqual(len(self.called), calls)
        self.assertEqual(self.enqueue(app, context, batch).unwrap(), result)
        final = await self.settle(app, result["job_id"])
        self.assertEqual(final["state"], "complete", final)
        self.assertEqual((len(self.called), len(self.sent)), (calls + 1, sends))
        self.assertEqual(json.loads(final["operator_json"])["subject"]["subject_id"], self.core.subject.subject_id)
        self.assertEqual(json.loads(final["context_json"])["actor"]["subject"]["subject_id"], context.actor.subject.subject_id)
        receipt = self.enqueue(app, context, batch).unwrap()
        self.assertEqual(receipt["job_id"], result["job_id"])
        self.assertEqual(receipt["state"], "complete")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM memory_reflection_jobs", ())["n"], 1)

    async def test_restart_resumes_queued_request_but_never_adopts_unrequested_batch(self):
        app, context = await self.cold()
        batch = self.prepare(app, context)["batch_id"]
        other = self.prepare(app, context, "unrequested")["batch_id"]
        queued = self.enqueue(app, context, batch).unwrap()
        calls = len(self.called)
        await app.close()
        restored = await self.restart(app)
        final = await self.settle(restored, queued["job_id"])
        self.assertEqual(final["state"], "complete", final)
        self.assertEqual(len(self.called), calls + 1)
        self.assertEqual(self.row(restored, other)["state"], "selecting")
        self.assertIsNone(self.row(restored, other)["task_id"])

    async def test_lost_admin_authority_blocks_dispatch_and_original_receipt_does_not_rearm(self):
        app, context = await self.cold()
        batch = self.prepare(app, context)["batch_id"]
        queued = self.enqueue(app, context, batch).unwrap()
        calls = len(self.called)
        self.identity.revoke_binding(self.core.binding_id, expected_revision=self.identity.revision, revoked_at=self.clock.now())
        final = await self.settle(app, queued["job_id"])
        self.assertEqual((final["state"], final["result_code"]), ("blocked", "binding_revoked"))
        self.assertEqual(len(self.called), calls)
        self.assertIsNone(self.row(app, batch)["task_id"])

    async def test_memory_disabled_after_enqueue_blocks_without_model_call(self):
        app, context = await self.cold()
        batch = self.prepare(app, context)["batch_id"]
        queued = self.enqueue(app, context, batch).unwrap()
        calls = len(self.called)
        self.configure(app, context, enabled=False)
        final = await self.settle(app, queued["job_id"])
        self.assertEqual((final["state"], final["result_code"]), ("blocked", "memory_disabled"))
        self.assertEqual(len(self.called), calls)

    async def test_unknown_original_call_after_restart_is_not_replayed(self):
        host, context, prepared = await self.setup_reflection()
        app, batch = host.application, prepared["batch_id"]
        queued = self.enqueue(app, context, batch).unwrap()
        with app.runtime.transaction() as db:
            db.execute("UPDATE memory_reflections SET state = 'running' WHERE batch_id = ?", (batch,))
        calls = len(self.called)
        await app.close()
        restored = await self.restart(app)
        final = await self.settle(restored, queued["job_id"])
        self.assertEqual((final["state"], final["result_code"]), ("blocked", "memory_reflection_uncertain"))
        self.assertEqual(len(self.called), calls)

    async def test_distinct_requests_for_one_batch_still_invoke_model_once(self):
        host, context, prepared = await self.setup_reflection()
        app, batch = host.application, prepared["batch_id"]
        calls = len(self.called)
        first = self.enqueue(app, context, batch, "one").unwrap()
        second = self.enqueue(app, context, batch, "two").unwrap()
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual((await self.settle(app, first["job_id"]))["state"], "complete")
        self.assertEqual((await self.settle(app, second["job_id"]))["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)

    async def test_explicit_repair_continues_the_original_chain(self):
        host, context, prepared = await self.setup_manual()
        app, original = host.application, self.model_behavior
        async def broken(_):
            return answered('{"summary":"private broken output",')
        self.model_behavior = broken
        failed = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(failed["state"], "failed")
        calls = len(self.called)
        self.model_behavior = original
        job = self.enqueue(app, context, prepared["batch_id"]).unwrap()
        self.assertNotEqual(job["batch_id"], prepared["batch_id"])
        self.assertEqual((await self.settle(app, job["job_id"]))["state"], "complete")
        self.assertEqual(self.row(app, prepared["batch_id"])["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)
        listed = ReflectionManagement(app, self.core).list(context, batch_id=job["batch_id"]).unwrap()
        self.assertEqual(listed["continuation"]["job_id"], job["job_id"])
        self.assertNotIn("raw_output", encode(listed))
        self.assertEqual(self.enqueue(app, context, prepared["batch_id"]).unwrap()["job_id"], job["job_id"])

    async def test_replaying_a_failed_job_returns_receipt_and_only_new_command_repairs(self):
        host, context, prepared = await self.setup_manual()
        app, original = host.application, self.model_behavior
        async def broken(_):
            return answered('{"summary":"broken",')
        self.model_behavior = broken
        job = self.enqueue(app, context, prepared["batch_id"]).unwrap()
        final = await self.settle(app, job["job_id"])
        self.assertEqual(final["state"], "blocked")
        calls = len(self.called)
        count = app.runtime.read_one("SELECT COUNT(*) n FROM memory_reflections", ())["n"]
        self.assertEqual(self.enqueue(app, context, prepared["batch_id"]).unwrap()["state"], "blocked")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM memory_reflections", ())["n"], count)
        self.model_behavior = original
        newer = self.enqueue(app, context, prepared["batch_id"], "explicit-new-repair").unwrap()
        self.assertNotEqual(newer["batch_id"], job["batch_id"])
        self.assertEqual((await self.settle(app, newer["job_id"]))["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)
        self.assertEqual(self.enqueue(app, context, prepared["batch_id"]).unwrap()["job_id"], job["job_id"])

    async def test_stopped_host_and_foreign_context_cannot_register_work(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        foreign_scope = replace(context.scope, session_id="another-session")
        foreign = replace(context, role_binding=replace(context.role_binding, scope=foreign_scope),
                          audience=replace(context.audience, scope=foreign_scope))
        self.assertFalse(self.enqueue(app, foreign, prepared["batch_id"]).is_success)
        await app.close()
        self.assertFalse(self.enqueue(app, context, prepared["batch_id"]).is_success)

    async def test_enqueue_rollback_leaves_no_job_or_repair_child(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        with app.runtime.transaction() as db:
            db.execute("UPDATE memory_reflections SET state = 'failed' WHERE batch_id = ?", (prepared["batch_id"],))
        before = app.runtime.read_one("SELECT COUNT(*) n FROM memory_reflections", ())["n"]
        with self.assertRaisesRegex(RuntimeError, "cancel command"):
            with app.runtime.transaction():
                self.enqueue(app, context, prepared["batch_id"]).unwrap()
                raise RuntimeError("cancel command")
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM memory_reflections", ())["n"], before)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM memory_reflection_jobs", ())["n"], 0)

    async def test_durable_output_finishes_without_a_second_provider_call(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        from companion_v2.memory import reflection_recovery
        with patch.object(reflection_recovery, "consume", side_effect=RuntimeError("interrupted parser")):
            await app.memory_reflection.run(context, prepared["batch_id"])
        self.assertEqual(self.row(app, prepared["batch_id"])["state"], "received")
        calls = len(self.called)
        job = self.enqueue(app, context, prepared["batch_id"]).unwrap()
        self.assertEqual((await self.settle(app, job["job_id"]))["state"], "complete")
        self.assertEqual(len(self.called), calls)

    async def test_kind_concurrency_refusal_also_re_queues_the_repair(self):
        host, context, prepared = await self.setup_reflection()
        app, batch = host.application, prepared["batch_id"]
        job = self.enqueue(app, context, batch).unwrap()
        calls, original = len(self.called), app.tasks.claim_task

        def limited(ctx, task_id):
            # The kind's own concurrency seat is taken, not the lane itself.
            if task_id.startswith("memory-extract-task-"):
                return Outcome.success(ClaimResult(None, "task_concurrency_limited"))
            return original(ctx, task_id)

        with patch.object(app.tasks, "claim_task", side_effect=limited):
            app.memory_schedule.continuations.advance(1)
            worker = next(iter(app.memory_schedule.continuations.workers.values()))[1]
            await worker
            value = self.job(app, job["job_id"])
            self.assertEqual((value["state"], value["result_code"]), ("queued", "task_concurrency_limited"))
            self.assertEqual(value["next_at"], stamp(self.clock.now() + timedelta(seconds=5)))
            self.assertEqual(len(self.called), calls)
            self.assertFalse(app.memory_schedule.continuations.active_handles)
            task_id = app.runtime.read_one("SELECT task_id FROM memory_reflections WHERE batch_id = ?", (batch,))["task_id"]
            self.assertEqual(self.execution_row(app, task_id), ("created", 0))
        self.assertEqual((await self.settle(app, job["job_id"]))["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)

    def execution_row(self, app, task_id):
        row = app.runtime.read_one("SELECT state, attempt_no FROM execution_tasks WHERE task_id = ?", (task_id,))
        return row["state"], row["attempt_no"]

    async def test_cache_busy_waits_without_holding_a_slot_and_then_finishes(self):
        app, context = await self.cold()
        batch = self.prepare(app, context)["batch_id"]
        job = self.enqueue(app, context, batch).unwrap()
        calls = len(self.called)
        with patch.object(app.memory.query.recall_index, "advance", side_effect=TaskError("memory_index_busy", "fixture lock")):
            app.memory_schedule.continuations.advance(1)
            worker = next(iter(app.memory_schedule.continuations.workers.values()))[1]
            await worker
            value = self.job(app, job["job_id"])
            self.assertEqual((value["state"], value["result_code"]), ("queued", "memory_index_busy"))
            self.assertEqual(value["next_at"], stamp(self.clock.now() + timedelta(seconds=5)))
            self.assertEqual(len(self.called), calls)
            self.assertFalse(app.memory_schedule.continuations.active_handles)
        self.assertEqual((await self.settle(app, job["job_id"]))["state"], "complete")

    async def test_saturated_background_lane_re_queues_instead_of_blocking_the_repair(self):
        """A really full background lane must not retire the reflection reservation."""
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        # Two batches over disjoint windows: the holder captures the older event,
        # the repaired one the newer, so neither consumes the other's evidence.
        older = host.receive(self.event("older")).unwrap()
        self.advance(app)
        await self.done(app, older)
        self.clock.advance(timedelta(hours=3))
        newer = host.receive(self.event("newer", text="我最近还是只喝白水")).unwrap()
        self.advance(app)
        await self.done(app, newer)
        holder_batch = app.memory_reflection.prepare(context, request_id="lane-holder",
            start=self.clock.now() - timedelta(hours=4), end=self.clock.now() - timedelta(hours=2, minutes=30),
            role_instruction="原角色人格提示").unwrap()["batch_id"]
        batch = app.memory_reflection.prepare(context, request_id="daily",
            start=self.clock.now() - timedelta(minutes=30), end=self.clock.now(),
            role_instruction="原角色人格提示").unwrap()["batch_id"]
        job = self.enqueue(app, context, batch).unwrap()
        # Really occupy the only background slot: no claim patching anywhere.
        holder = await self.occupy_lane(app, context, holder_batch)
        calls = len(self.called)
        # Both batches are already `prepared`; the job's single attempt meets the
        # full lane, so nothing is dispatched and the reservation must survive.
        app.memory_schedule.continuations.advance(1)
        worker = next(iter(app.memory_schedule.continuations.workers.values()))[1]
        await worker
        self.assertFalse(app.memory_schedule.continuations.active_handles)
        self.assertEqual(self.row(app, batch)["state"], "prepared")
        task_id = self.row(app, batch)["task_id"]
        self.assertIsNotNone(task_id)
        task = app.runtime.read_one("SELECT state, attempt_no FROM execution_tasks WHERE task_id = ?", (task_id,))
        self.assertEqual((task["state"], task["attempt_no"]), ("created", 0))
        value = self.job(app, job["job_id"])
        self.assertEqual((value["state"], value["result_code"]), ("queued", "task_lane_full"))
        self.assertFalse(app.memory_schedule.continuations.active_handles)
        self.assertEqual(len(self.called), calls)  # the refused attempt dispatched nothing
        self.assertIsNone(json.loads(self.row(app, batch)["receipts_json"]).get("repair_batch"))
        # Releasing the real slot lets the next due wake re-enter the same job.
        app.tasks.stop(holder, reason="cancelled").unwrap()
        final = await self.settle(app, job["job_id"])
        self.assertEqual(final["state"], "complete", final)
        self.assertEqual(self.row(app, batch)["state"], "complete")

    async def occupy_lane(self, app, context, holder_batch):
        """Hold the single background slot with a real begun attempt that never finishes."""
        reflection = app.memory_reflection
        for _ in range(30):
            row = self.row(app, holder_batch)
            if row["state"] != "selecting":
                break
            reflection.selection.advance(context, row)
        self.assertEqual(self.row(app, holder_batch)["state"], "prepared")
        # Submit the batch's own task the way `run` does, then begin it and leave
        # the attempt running: the slot is really held by the supervisor.
        now = reflection.tasks.now()
        task_id = "memory-extract-task-" + digest(holder_batch)
        with reflection.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET task_id = ? WHERE batch_id = ?", (task_id, holder_batch))
        reflection.tasks.submit(TaskSpec(task_id, context, "memory.reflect", holder_batch, holder_batch,
                                         now, now + timedelta(minutes=5))).unwrap()
        claim = reflection.tasks.claim_task(context, task_id).unwrap().claim
        self.assertIsNotNone(claim)
        reflection.tasks.begin(claim).unwrap()
        return claim

    async def test_auto_repair_child_survives_multiple_selection_steps(self):
        app, context = await self.cold()
        batch = self.prepare(app, context)["batch_id"]
        original, calls = self.model_behavior, len(self.called)
        attempts = 0
        async def recoverable(envelope):
            nonlocal attempts
            if envelope.original.output_format == "memory_reflection":
                attempts += 1
                if attempts == 1:
                    return answered('{"summary":"broken",')
            return await original(envelope)
        self.model_behavior = recoverable
        job = self.enqueue(app, context, batch).unwrap()
        final = await self.settle(app, job["job_id"])
        self.assertEqual(final["state"], "complete", final)
        self.assertEqual(len(self.called), calls + 2)
        self.assertIsNotNone(json.loads(self.row(app, batch)["receipts_json"])["repair_batch"])

    async def test_active_job_holds_no_immediate_wake_and_shutdown_preserves_unknown(self):
        host, context, prepared = await self.setup_reflection()
        app, started = host.application, asyncio.Event()
        async def waiting(_):
            started.set()
            await asyncio.Event().wait()
        self.model_behavior = waiting
        job = self.enqueue(app, context, prepared["batch_id"]).unwrap()
        await asyncio.wait_for(started.wait(), 3)
        self.assertIsNone(app.memory_schedule.next_wake())
        self.assertTrue(app.memory_schedule.continuations.active_handles)
        calls = len(self.called)
        await app.close()
        self.assertFalse(app.memory_schedule.continuations.active_handles)
        restored = await self.restart(app)
        final = await self.settle(restored, job["job_id"])
        self.assertEqual((final["state"], final["result_code"]), ("blocked", "memory_reflection_uncertain"))
        self.assertEqual(len(self.called), calls)
