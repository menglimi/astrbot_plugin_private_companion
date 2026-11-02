"""One failed model round must not lock its material batch forever.

``role.expression.learn`` spends one model call per material batch: the request
key covers ``material_hash``, so the same samples are not tried twice. The skip
decision used to be "an operation row exists", but ``generate`` leaves that row
at ``accepted`` when the model round fails — nothing was committed, so nothing
closes the receipt — and one failure then skipped the same samples until new ones
arrived. The skip line said which state owned the batch, yet the batch never came
back.

An attempt therefore owns its batch under a lease: the declared budget plus its
finish margin. Inside the lease the batch is skipped and the logged state names
why; past it the earlier attempt is abandoned and the batch is retried under the
next attempt epoch, which derives a new operation and task because both layers
refuse to re-run a used identity. A committed batch is ``completed`` and is never
retried, and no epoch is opened for it.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.execution.runner import RunResult
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import (KIND, LEARN_LEASE_SECONDS, LEASE_PREFIX, OWNER,
    ExpressionStyleService)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_v1_pipeline import ExpressionFixture
from tests.test_host_application import HostFixture

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
SCOPE = ScopeRef("installation-a", "onebot", ScopeKind.PRIVATE, "native-a", "bot-a", "login-a",
                 "conversation-a", "session-a")
MATERIAL = "0123456789abcdef0123"
LOGGER = "companion_v2.expression.style_service"


async def _noop(*_args):
    return None


class SkipNoteFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="expression-skip-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id="installation-a")
        self.addCleanup(self.runtime.close)
        self.operations = OperationRepository(self.runtime)
        self.now = NOW

    def service(self):
        service = ExpressionStyleService(self.runtime)
        service.gateway = SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()}))
        service.tasks = SimpleNamespace(now=lambda: self.now)
        service.guard = _noop
        return service

    def job(self, material=MATERIAL, mode="whole"):
        return {"material_hash": material, "policy": SimpleNamespace(learning_mode=mode), "at": self.now}

    def context(self):
        return SimpleNamespace(key="private:1", scope=SCOPE)

    def receipt(self, operation_id, state=OperationState.ACCEPTED, at=None):
        reference = OperationRef(operation_id, OWNER, SCOPE, VersionRef(operation_id, 1), operation_id)
        self.operations.reserve(reference, parameter_hash=digest(["parameters"]),
                                observed_at=at or self.now).unwrap()
        if state is OperationState.COMPLETED:
            self.operations.transition(operation_id, expected_state=OperationState.ACCEPTED,
                                       next_state=state, observed_at=at or self.now).unwrap()
        return self.operations.get(operation_id).unwrap()


class SkipNoteTests(SkipNoteFixture):
    def test_a_finished_attempt_reports_already_learned(self):
        service, receipt = self.service(), self.receipt("expression-learn:finished", OperationState.COMPLETED)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            service._note_skipped("expression-learn:finished", self.job(), receipt, epoch=0, at=NOW)

        line = "\n".join(logs.output)
        self.assertIn("expression-learn:finished", line)
        self.assertIn("previous=completed", line)
        self.assertIn("material=0123456789ab", line)
        self.assertIn("mode=whole", line)
        self.assertIn("retry=no", line)
        self.assertEqual(logs.records[0].levelno, 20)

    def test_an_unfinished_attempt_is_reported_as_a_lock(self):
        service, receipt = self.service(), self.receipt("expression-learn:locked")
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            service._note_skipped("expression-learn:locked", self.job(), receipt, epoch=0, at=NOW)

        self.assertIn("previous=accepted", "\n".join(logs.output))
        self.assertIn("retry=later", "\n".join(logs.output))
        self.assertEqual(logs.records[0].levelno, 30)

    def test_an_unknown_operation_state_is_still_readable(self):
        service = self.service()
        receipt = SimpleNamespace(state=None, updated_at=None)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            service._note_skipped("expression-learn:odd", self.job(mode=""), receipt, epoch=0, at=NOW)

        self.assertIn("previous=missing", "\n".join(logs.output))


class LeaseFixture(SkipNoteFixture):
    """The lease is real; only the model round and the task layer are scripted."""

    def service(self, rounds):
        service = super().service()
        service.tasks = SimpleNamespace(now=lambda: self.now, submit=lambda spec: Outcome.success(spec))
        service.prepare = lambda context, instruction, at: self.job()
        service.identifier = lambda context, request_key: "expression-learn:" + request_key
        service.runner = SimpleNamespace(run=self.model_round)
        self.rounds = list(rounds)
        return service

    async def model_round(self, context, task_id):
        """One scripted round; ``succeeded`` closes the receipt exactly as generate() does."""
        outcome = self.rounds.pop(0)
        if outcome == "succeeded":
            self.operations.transition(task_id, expected_state=OperationState.ACCEPTED,
                                       next_state=OperationState.COMPLETED, observed_at=self.now).unwrap()
            return Outcome.success(RunResult(SimpleNamespace(state="succeeded", reason="owner_result")))
        return Outcome.success(RunResult(SimpleNamespace(state="failed", reason="worker_error"),
                                         "model_estimation_failed"))

    def live(self, task_id):
        """A ledger row that can still run: its worker owns the batch, not the clock."""
        at = stamp(self.now)
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO execution_tasks(task_id, origin_key, kind, lane, dedupe_key,
                request_hash, revision, state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                created_at, updated_at, record_json)
                VALUES (?, ?, ?, 'background', ?, ?, 1, 'running', 1, 1, 'attempt', ?, ?, ?, ?, '{}')""",
                (task_id, "private:1", KIND, task_id + ":dedupe", task_id + ":request", at, at, at, at))

    def leases(self):
        rows = self.runtime.read_all("SELECT key, value FROM runtime_metadata WHERE key LIKE ?",
                                     (LEASE_PREFIX + "%",))
        return {row["key"]: json.loads(row["value"]) for row in rows}


class LeaseTests(LeaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_round_is_retried_only_after_its_lease(self):
        service, context = self.service(["failed", "succeeded"]), self.context()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            first = await service.run(context, "学习这一批")
        self.assertIn("本轮未提交", "\n".join(logs.output),
                      "a round that spent its call without committing must say so")
        self.assertEqual(first["state"], "failed")
        self.assertEqual(first["code"], "model_estimation_failed")
        self.assertEqual(self.operations.get(first["task_id"]).unwrap().state, OperationState.ACCEPTED,
                         "a failed model round must not close the batch receipt")
        self.assertEqual(self.leases(), {}, "a first attempt opens no retry epoch")

        self.now = NOW + timedelta(seconds=LEARN_LEASE_SECONDS - 1)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            inside = await service.run(context, "学习这一批")
        self.assertEqual(inside, {"state": "already_attempted", "task_id": first["task_id"]})
        self.assertEqual(len(self.rounds), 1, "the batch may not spend a second call inside its lease")
        self.assertIn("previous=accepted", "\n".join(logs.output))
        self.assertIn("retry=later", "\n".join(logs.output))
        self.assertIn("age=" + str(LEARN_LEASE_SECONDS - 1) + "s", "\n".join(logs.output),
                      "the lease is measured from the receipt the earlier attempt left open")
        self.assertEqual(self.leases(), {}, "a skip inside the lease opens no new epoch")

        self.now = NOW + timedelta(seconds=LEARN_LEASE_SECONDS)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            second = await service.run(context, "学习这一批")
        self.assertEqual(second["state"], "succeeded")
        self.assertNotEqual(second["task_id"], first["task_id"],
                            "a retry needs its own operation and task identity")
        self.assertEqual(self.rounds, [])
        self.assertIn("retry=yes", "\n".join(logs.output))
        self.assertIn("lease=" + str(LEARN_LEASE_SECONDS) + "s", "\n".join(logs.output))
        self.assertEqual([value["epoch"] for value in self.leases().values()], [1],
                         "the retried batch records the epoch that produced its operation")

    async def test_a_finished_batch_is_never_retried_and_its_epoch_stops_growing(self):
        service, context = self.service(["failed", "succeeded"]), self.context()
        first = await service.run(context, "学习这一批")
        self.now = NOW + timedelta(seconds=LEARN_LEASE_SECONDS)
        second = await service.run(context, "学习这一批")
        self.assertEqual(second["state"], "succeeded")
        self.assertNotEqual(second["task_id"], first["task_id"])

        with self.assertLogs(LOGGER, level="INFO") as logs:
            learned = await service.run(context, "学习这一批")
        self.assertEqual(learned, {"state": "already_attempted", "task_id": second["task_id"]})
        self.assertEqual(logs.records[0].levelno, 20, "a learned batch is not a warning")
        self.assertIn("previous=completed", "\n".join(logs.output))
        self.assertIn("retry=no", "\n".join(logs.output))
        self.assertEqual(self.rounds, [], "a completed batch must not spend another model call")
        self.assertEqual([value["epoch"] for value in self.leases().values()], [1],
                         "a finished batch must not keep opening epochs")

    async def test_a_still_running_attempt_keeps_the_batch_past_its_lease(self):
        service, context = self.service(["failed"]), self.context()
        first = await service.run(context, "学习这一批")
        self.live(first["task_id"])
        self.rounds = ["succeeded"]
        self.now = NOW + timedelta(hours=2)

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            again = await service.run(context, "学习这一批")

        self.assertEqual(again, {"state": "already_attempted", "task_id": first["task_id"]})
        self.assertEqual(self.rounds, ["succeeded"],
                         "a worker that can still run owns the material it was admitted for")
        self.assertIn("retry=later", "\n".join(logs.output))
        self.assertEqual(self.leases(), {}, "a live attempt opens no new epoch")

    async def test_an_invalid_lease_record_blocks_the_window_instead_of_raising(self):
        service = self.service(["succeeded"])
        request_key = digest(["private:1", MATERIAL, "whole"])
        with self.runtime.transaction() as connection:
            connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                               (LEASE_PREFIX + request_key, json.dumps({"epoch": 0})))

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            report = await service.run(self.context(), "学习这一批")

        self.assertEqual(report, {"state": "blocked", "code": "expression_lease_invalid"},
                         "an unreadable lease is reported to the window, not raised at the worker")
        self.assertIn("expression_lease_invalid", "\n".join(logs.output))
        self.assertEqual(self.rounds, ["succeeded"], "an unreadable lease must not spend a model call")

    def test_a_receipt_without_its_creation_time_still_owns_the_batch(self):
        service = self.service(["succeeded"])
        receipt = SimpleNamespace(state=OperationState.ACCEPTED, created_at=None, updated_at=None)

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            expired = service._lease_expired(receipt, NOW + timedelta(days=30))

        self.assertFalse(expired, "created_at is NOT NULL in the schema, so this receipt is damaged, not fresh")
        self.assertIn("缺少创建时间", "\n".join(logs.output))


class SkipWiringTests(SkipNoteFixture, unittest.IsolatedAsyncioTestCase):
    async def test_run_notes_the_previous_attempt_instead_of_skipping_silently(self):
        service = self.service()
        self.receipt("expression-learn:task-b")
        service.identifier = lambda context, request_key: "expression-learn:task-b"
        service.prepare = lambda context, instruction, at: self.job()
        context = SimpleNamespace(key="private:1", scope=SimpleNamespace(kind=ScopeKind.PRIVATE))

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            report = await service.run(context, "学习这一批")

        self.assertEqual(report, {"state": "already_attempted", "task_id": "expression-learn:task-b"})
        self.assertIn("previous=accepted", "\n".join(logs.output))


class LearningRetryPipelineTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    """The same lease through the real host, scheduler and task ledger.

    One failed learning round must leave the samples retryable, and the retry must
    really be attempted — not only reported as allowed. The clock only moves by
    one lease, well inside the 90-minute scan interval, so the driver's own
    discovery pass cannot start a second round behind this test's back.
    """

    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.learning_rounds = 0
        self.model_behavior = self.expression_model

    async def failing_round(self, envelope):
        """The round this batch is spent on fails before any candidate exists."""
        self.learning_rounds += 1
        raise TaskError("model_estimation_failed", "local reservation estimation failed; details omitted")

    async def answer_round(self, envelope):
        self.learning_rounds += 1
        return await self.expression_model(envelope)

    async def seed_for_learning(self, host):
        """Reuse the reply pipeline's seeding so this suite reaches a real run."""
        return await pipeline.ExpressionReplyPipelineTests.seed(self, host)

    async def learn(self, app, context):
        """Ask for one round and read the receipt of exactly that worker."""
        service = app.expression_style
        started = ExpressionStyleManagement(app).learn(operator=self.core, context=context).unwrap()
        self.assertIn(started["state"], ("queued", "running"))
        worker = service.workers[service._work_key(context)]
        await self.until(lambda: worker.done())
        return service.results[service._work_key(context)]

    async def test_a_failed_model_round_is_retried_once_its_lease_expires(self):
        host = await self.start()
        app = host.application
        context = await self.seed_for_learning(host)
        self.configure_expression(app, {"private_learning_enabled": True})
        # Hold the driver's own discovery pass off before the loop can run again:
        # this test asks for its rounds, and a scan-started round would spend the
        # scripted model behaviour under a different assertion.
        app.expression_style.next_checks[context.key] = self.clock.now() + timedelta(days=1)

        self.model_behavior = self.failing_round
        first = await self.learn(app, context)
        self.assertEqual(first["state"], "failed", first)
        self.assertEqual(first["code"], "model_estimation_failed")
        self.assertEqual(self.learning_rounds, 1)
        self.assertEqual(OperationRepository(app.runtime).get(first["task_id"]).unwrap().state,
                         OperationState.ACCEPTED,
                         "the failed round committed nothing, so its receipt stays open")

        self.model_behavior = self.answer_round
        inside = await self.learn(app, context)
        self.assertEqual(inside, {"state": "already_attempted", "task_id": first["task_id"]},
                         "inside its lease the batch still belongs to the failed attempt")
        self.assertEqual(self.learning_rounds, 1, "the same samples may not be paid for twice")

        self.clock.advance(timedelta(seconds=LEARN_LEASE_SECONDS))
        app.notify()
        retried = await self.learn(app, context)
        self.assertEqual(retried["state"], "succeeded", retried)
        self.assertNotEqual(retried["task_id"], first["task_id"],
                            "a retry needs an identity the execution layer has not used")
        self.assertEqual(self.learning_rounds, 2)
        self.assertEqual(OperationRepository(app.runtime).get(retried["task_id"]).unwrap().state,
                         OperationState.COMPLETED)

        learned = await self.learn(app, context)
        self.assertEqual(learned, {"state": "already_attempted", "task_id": retried["task_id"]},
                         "the learned batch is finished, not merely retryable")
        self.assertEqual(self.learning_rounds, 2, "a completed batch must not spend another call")


if __name__ == "__main__":
    unittest.main()
