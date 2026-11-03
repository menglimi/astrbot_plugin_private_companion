"""#317: an exact task that never ran must never be reported as success.

Two independent mechanisms silently turned "not executed" into "done" and threw
the queued reservation away with a generic ``owner_unavailable`` label:

* ``ExactTaskRunner.run`` re-read the row and returned ``Outcome.success`` even
  when it never obtained a claim (lane capacity, owner validation refusal);
* ``BackgroundTaskQueue.claim`` retired a still-queued task as
  ``owner_unavailable`` whenever the wait ended without a hand-off, including
  for unexpected claim errors that carry their own cause.

Every assertion here is about the durable task ledger and the caller-visible
result, never about internal call counts alone.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import ClaimResult, Lane, TaskClaim, TaskContext, TaskResult, digest
from companion_v2.execution.runner import ExactTaskRunner
from companion_v2.execution.waiting import BackgroundTaskQueue
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, ReferenceValidationError, RoleOwnerRef
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.role_work.journal import JournalContext, JournalKey, JournalKind
from companion_v2.role_work.journal.writing import writing_profiles
from tests.test_execution import ExecutionFixture
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, ROLE, SCOPE


async def until(predicate, *, seconds=2):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.002)
    raise AssertionError("condition was not observed")


class UnclaimedExactTaskTests(ExecutionFixture, unittest.IsolatedAsyncioTestCase):
    def attached(self):
        self.tasks = self.supervisor()
        self.tasks.attach(expected_generation=1).unwrap()
        return self.tasks

    def runner(self, kind, handler, *, waiting=None, retain_refusals=False):
        runner = ExactTaskRunner(self.tasks, frozenset({kind}), handler, check_interval=0.005,
                                 stop_grace=0.05, waiting=waiting, retain_refusals=retain_refusals)
        self.addAsyncCleanup(runner.close)
        return runner

    def ledger(self, task_id):
        """Read the durable row directly; authorization may already be gone."""
        row = self.runtime.read_one("SELECT state, record_json FROM execution_tasks WHERE task_id = ?", (task_id,))
        return row["state"], json.loads(row["record_json"])["reason"]

    async def test_full_lane_never_reports_success_for_an_unexecuted_exact_task(self):
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        # background_slots == 1: the started holder owns the whole lane.
        self.submit("holder", kind="fixture.background")
        self.tasks.begin(self.claim(Lane.BACKGROUND)).unwrap()
        self.submit("blocked", kind="fixture.background")
        runner = self.runner("fixture.background", handler, retain_refusals=True)
        result = (await runner.run(self.context, "blocked")).unwrap()
        self.assertEqual(calls, [])
        # Nothing ran, so this is never an owner result. A retaining owner opted
        # in to converging the row by ID, so a momentarily full lane keeps the
        # reservation instead of turning a shortfall into a permanent loss.
        self.assertEqual((result.task.state, result.task.reason), ("created", "queued"))
        self.assertEqual((result.failure_code, result.refused), ("task_lane_full", "task_lane_full"))
        self.assertEqual(self.ledger("blocked"), ("created", "queued"))

    async def test_default_runner_still_retires_a_full_lane_reservation(self):
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        self.submit("holder", kind="fixture.background")
        self.tasks.begin(self.claim(Lane.BACKGROUND)).unwrap()
        self.submit("blocked", kind="fixture.background")
        # Retaining is an explicit opt-in: an owner without an ID-convergence
        # policy must not inherit a queued row it would never look at again.
        result = (await self.runner("fixture.background", handler).run(self.context, "blocked")).unwrap()
        self.assertEqual(calls, [])
        self.assertEqual((result.task.state, result.failure_code), ("cancelled", "task_lane_full"))
        self.assertEqual(self.ledger("blocked"), ("cancelled", "task_lane_full"))

    async def test_owner_validation_refusal_is_reported_instead_of_success(self):
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        self.submit("rejected", kind="fixture.background")
        # The owner's original inputs expire between admission and the claim.
        self.inputs_current = False
        result = (await self.runner("fixture.background", handler).run(self.context, "rejected")).unwrap()
        self.assertEqual(calls, [])
        self.assertEqual((result.task.state, result.failure_code), ("failed", "input_expired"))
        self.assertEqual(self.ledger("rejected"), ("failed", "input_expired"))

    async def test_unexpected_claim_error_keeps_its_own_cause_in_the_ledger(self):
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        queue = BackgroundTaskQueue(self.tasks, interval=0.005)
        runner = self.runner("fixture.background", handler, waiting=queue)
        self.submit("pending", kind="fixture.background")
        with patch.object(self.tasks, "claim_task",
                          side_effect=ReferenceValidationError("scope kind must be explicit")):
            with self.assertRaises(ReferenceValidationError):
                await runner.run(self.context, "pending")
        self.assertEqual(calls, [])
        self.assertEqual(self.ledger("pending"), ("cancelled", "task_claim_interrupted"))

    async def test_authorization_failure_while_waiting_is_not_relabelled(self):
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        self.submit("holder", kind="fixture.background")
        self.tasks.begin(self.claim(Lane.BACKGROUND)).unwrap()
        queue = BackgroundTaskQueue(self.tasks, interval=0.005)
        runner = self.runner("fixture.background", handler, waiting=queue)
        self.submit("waiting", kind="fixture.background")
        pending = asyncio.create_task(runner.run(self.context, "waiting"))
        await until(lambda: "waiting" in queue._pending)
        self.identity.revoke_binding(self.important.binding_id, expected_revision=self.identity.revision,
                                     revoked_at=NOW)
        result = await asyncio.wait_for(pending, 2)
        self.assertEqual(calls, [])
        self.assertEqual(result.failure.code, "binding_revoked")
        self.assertEqual(self.ledger("waiting"), ("cancelled", "binding_revoked"))

    async def test_transient_refusal_names_its_condition_and_never_cancels_the_row(self):
        self.attached()
        self.submit("holder", kind="fixture.background")
        self.tasks.begin(self.claim(Lane.BACKGROUND)).unwrap()
        self.submit("blocked", kind="fixture.background")
        refused = self.tasks.claim_task(self.context, "blocked").unwrap()
        self.assertEqual((refused.claim, refused.refused), (None, "task_lane_full"))
        self.assertEqual(self.ledger("blocked"), ("created", "queued"))
        with self.assertRaises(ValueError):
            replace(refused, claim=TaskClaim("blocked", "attempt", 1))

    async def test_refusal_code_matrix_decides_whether_the_row_survives(self):
        """Only capacity refusals may be kept, and only for an opted-in owner."""
        calls = []
        capacity = ("task_lane_full", "task_concurrency_limited")
        row_refusals = ("task_not_due", "task_not_queued", None)

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        for index, (retained, code) in enumerate(
                itertools.product((True, False), capacity + row_refusals)):
            with self.subTest(retain_refusals=retained, code=code):
                task_id = "refused-" + str(index)
                self.attached()
                self.submit(task_id, kind=self.background.kind)
                runner = self.runner(self.background.kind, handler, retain_refusals=retained)
                with patch.object(self.tasks, "claim_task",
                                  return_value=Outcome.success(ClaimResult(None, code))):
                    result = (await runner.run(self.context, task_id)).unwrap()
                keep = retained and code in capacity
                self.assertEqual(calls, [])
                self.assertEqual(result.task.state, "created" if keep else "cancelled")
                self.assertEqual(result.failure_code, code or "task_not_admitted")
                if keep:
                    # A kept row reports the reservation and the refusal together.
                    self.assertEqual(result.refused, code)
                else:
                    # A retired row names its cause in `failure_code` only.
                    self.assertIsNone(result.refused)
                self.assertEqual(self.ledger(task_id), ("created", "queued") if keep else
                                 ("cancelled", code or "task_not_admitted"))

    async def test_retained_refusal_never_revives_a_row_that_is_already_settled(self):
        """A cancelled row wins over the refusal: the runner neither keeps nor re-retires it."""
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        saved = self.submit("cancelled", kind=self.background.kind)
        self.tasks.cancel(self.context, "cancelled", expected_revision=saved.revision).unwrap()
        self.assertEqual(self.ledger("cancelled"), ("cancelled", "cancelled_before_start"))
        runner = self.runner(self.background.kind, handler, retain_refusals=True)
        with patch.object(self.tasks, "claim_task",
                          return_value=Outcome.success(ClaimResult(None, "task_lane_full"))):
            result = (await runner.run(self.context, "cancelled")).unwrap()
        self.assertEqual(calls, [])
        # The row's own state is authoritative: the settled row wins over the
        # refusal, so nothing is kept and nothing is re-retired. The pre-existing
        # early exit reports the row's own reason, never the refused code.
        self.assertEqual(result.task.state, "cancelled")
        self.assertEqual(result.failure_code, "cancelled_before_start")
        self.assertIsNone(result.refused)
        self.assertEqual(self.ledger("cancelled"), ("cancelled", "cancelled_before_start"))

    async def test_unnamed_queued_refusal_is_still_retired_not_kept(self):
        """A retaining owner still gives up on a refusal that names no capacity."""
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        self.submit("pending", kind=self.background.kind)
        runner = self.runner(self.background.kind, handler, retain_refusals=True)
        # An unnamed non-claim keeps the legacy cleanup: the reservation is gone.
        with patch.object(self.tasks, "claim_task",
                          return_value=Outcome.success(ClaimResult(None))):
            result = (await runner.run(self.context, "pending")).unwrap()
        self.assertEqual(calls, [])
        self.assertEqual((result.task.state, result.failure_code), ("cancelled", "task_not_admitted"))
        self.assertEqual(self.ledger("pending"), ("cancelled", "task_not_admitted"))

    async def test_retained_reservation_still_runs_once_the_lane_frees_up(self):
        calls = []

        async def handler(run):
            calls.append(run.task.task_id)
            return TaskResult("succeeded")

        self.attached()
        self.submit("holder", kind="fixture.background")
        held = self.claim(Lane.BACKGROUND)
        self.tasks.begin(held).unwrap()
        self.submit("blocked", kind="fixture.background")
        runner = self.runner("fixture.background", handler, retain_refusals=True)
        self.assertEqual((await runner.run(self.context, "blocked")).unwrap().failure_code, "task_lane_full")
        self.assertEqual(calls, [])
        self.assertEqual(self.ledger("blocked"), ("created", "queued"))
        self.tasks.finish(held, TaskResult("succeeded")).unwrap()
        self.assertEqual((await runner.run(self.context, "blocked")).unwrap().task.state, "succeeded")
        self.assertEqual(calls, ["blocked"])
        self.assertEqual(self.ledger("blocked"), ("succeeded", "owner_result"))

    async def test_discard_records_the_owner_supplied_cause(self):
        self.attached()
        saved = self.submit("discardable", kind="fixture.background")
        self.assertEqual(saved.state, "created")
        retired = self.tasks.discard_queued("discardable", reason="owner_unavailable",
                                            code="task_concurrency_limited").unwrap()
        self.assertEqual((retired.state, retired.reason), ("cancelled", "task_concurrency_limited"))
        self.assertEqual(self.ledger("discardable"), ("cancelled", "task_concurrency_limited"))


class JournalUnclaimedTaskTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    """The reported symptom: the diary task is dropped and relabelled as a shutdown."""

    def setUp(self):
        super().setUp()
        self.profiles += writing_profiles("primary", max_output_tokens=100)
        self.registry = FieldRegistry(version="host.journal.v1", fields=configuration_fields(self.profiles))
        self.owner = RoleOwnerRef(SCOPE.installation_id, SCOPE.bot_id, ROLE.subject_id)

    async def start_writer(self):
        self.host_instance = await self.start(self.host())
        self.app = self.host_instance.application
        self.writer = self.app.journal_writer
        self.trigger = self.host_instance.inbound.ingest(self.event("journal-trigger"), capability="text").unwrap()
        self.context = TaskContext(self.important, self.trigger.runtime_scope.persona,
                                   AudienceRef(SCOPE, (self.important.subject,)))

    def target(self, day="2026-09-21"):
        return JournalContext(JournalKey(self.owner, JournalKind.DIARY, day), SCOPE, "Asia/Shanghai")

    def ledger(self, task_id):
        row = self.runtime.read_one("SELECT state, record_json FROM execution_tasks WHERE task_id = ?", (task_id,))
        return row["state"], json.loads(row["record_json"])["reason"]

    async def test_dropped_diary_task_keeps_its_own_cause_instead_of_a_shutdown_label(self):
        await self.start_writer()
        task_id = "journal-task-" + digest([self.context.key, "write"])
        with patch.object(self.app.tasks, "claim_task",
                          side_effect=ReferenceValidationError("scope kind must be explicit")):
            with self.assertRaises(ValueError):
                await self.writer.write(self.context, self.trigger, self.target(),
                                        role_instruction="保留的角色人格", request_id="write", recent=())
        self.assertEqual(self.ledger(task_id), ("cancelled", "task_claim_interrupted"))


if __name__ == "__main__":
    unittest.main()
