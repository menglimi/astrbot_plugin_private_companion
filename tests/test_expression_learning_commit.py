"""One learning batch counts only once it commits.

``role.expression.learn`` used to take its daily group batch and advance the
window's new-message checkpoint before the model round. A round that then failed
spent the slot for the day and the checkpoint kept its material from ever being
tried again. The daily slot is a claim while a round is in flight; it becomes a
committed batch only in the transaction that commits the candidates, and a
known failure returns it. The checkpoint is written by that same transaction, so
a failed or crashed round leaves the samples eligible once its lease allows the
next attempt. Private windows keep their scan cursor the same way.

The same batch can only be dispatched for the candidate count its route can
afford: ``parse_output`` enforces the narrowed limit, not the product maximum.
A private-window scan failure belongs to the window it read, not to the binding
that happened to seed the discovery pass.
"""

from datetime import datetime, timedelta, timezone
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from astrbot_host.management_identity import dashboard_operator

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.execution.models import TaskContext, TaskError, digest
from companion_v2.execution.runner import RunResult
from companion_v2.expression import learning, style_service
from companion_v2.expression.configuration import (GROUP, ExpressionConfigurationOwner,
                                                   configuration_fields)
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import (KIND, LEARN_LEASE_SECONDS, ExpressionStyleService,
    parse_output)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime
from tests.group_host_support import GroupFixture
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import completion
from tests.staging_bootstrap_support import wait_for_batch


NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
ROLE = "persona-a"
GROUP_SCOPE = ScopeRef("installation-a", "onebot", ScopeKind.GROUP, "native-g", "bot-a", "login-a",
                       "conversation-g", "session-g")
MATERIAL = "0123456789abcdef0123"
# The documented retry schedule of one material signature: one declared attempt,
# then twice as long for each further retry. Written out here rather than derived,
# so a change to the schedule has to be an explicit decision.
RETRY_WAITS = (LEARN_LEASE_SECONDS, LEARN_LEASE_SECONDS * 2, LEARN_LEASE_SECONDS * 4)
GROUP_CLAIM_PREFIX = getattr(style_service, "GROUP_CLAIM_PREFIX", "role.expression.learn.group-claim.v1:")
SIGNAL = {"dimension": "sentence_rhythm", "observation": "回应计划时常用短句收束",
          "proposal": "回应计划时先给简短回应，再用短句自然收束", "confidence": 0.8,
          "expression": {"kind": "grammar", "situation": "确认计划或约定时",
              "pattern": "先用短句确认，再用短句自然收束", "avoid": "需要交代条件或细节时"},
          "applicability": {"channels": ["group"], "relationship_stages": ["any"],
              "emotion_gates": ["any"], "intent": "any", "keywords": []}}


async def _noop(*_args):
    return None


def learning_payload(count, handles=("input:a", "input:b")):
    return {"summary": "合成的短句节奏", "signals": [
        {**SIGNAL, "evidence_handles": list(handles)} for _ in range(count)]}


class GroupBatchFixture(unittest.TestCase):
    """Real metadata storage; only the model round and the task layer are scripted."""

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="expression-commit-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name),
                                           installation_id="installation-a")
        self.addCleanup(self.runtime.close)
        self.operations = OperationRepository(self.runtime)
        self.now = NOW
        self.material = MATERIAL
        self.calls = 0

    def context(self, key="group:1"):
        return SimpleNamespace(key=key, scope=GROUP_SCOPE,
                               role_binding=SimpleNamespace(role=SimpleNamespace(subject_id=ROLE)))

    def job(self, context):
        return {"context": context, "material_hash": self.material, "latest_sequence": 2,
                "policy": SimpleNamespace(learning_mode="balanced", group_daily_batch_limit=1)}

    def service(self, rounds):
        service = ExpressionStyleService(self.runtime)
        service.gateway = SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()}))
        service.tasks = SimpleNamespace(now=lambda: self.now, submit=lambda spec: Outcome.success(spec))
        service.guard = _noop
        service.samples = SimpleNamespace(role_owner=lambda context: ROLE)
        service.prepare = lambda context, instruction, at: self.job(context)
        service.identifier = lambda context, request_key: "expression-learn:" + request_key
        service.runner = SimpleNamespace(run=self.model_round)
        self.rounds = list(rounds)
        return service

    async def model_round(self, context, task_id):
        self.calls += 1
        outcome = self.rounds.pop(0)
        if outcome == "succeeded":
            self.operations.transition(task_id, expected_state=OperationState.ACCEPTED,
                                       next_state=OperationState.COMPLETED, observed_at=self.now).unwrap()
            return Outcome.success(RunResult(SimpleNamespace(state="succeeded", reason="owner_result")))
        return Outcome.success(RunResult(SimpleNamespace(state="failed", reason="worker_error"),
                                         "model_estimation_failed"))

    def claims(self):
        return [row["key"] for row in self.runtime.read_all(
            "SELECT key FROM runtime_metadata WHERE key LIKE ?", (GROUP_CLAIM_PREFIX + "%",))]


class GroupBatchCommitTests(GroupBatchFixture, unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_round_returns_the_daily_batch_and_the_new_message_checkpoint(self):
        service, context = self.service(["failed"]), self.context()
        report = await service.run(context, "学习这一批")
        self.assertEqual(report["state"], "failed", report)
        day = service._day(NOW)
        self.assertEqual(service._global_group_batches(day, at=NOW), 0,
                         "a batch that committed nothing must not spend today's slot")
        self.assertEqual(service._progress(context)["input_sequence"], 0,
                         "a failed round must leave the same samples eligible")
        self.assertEqual(self.claims(), [], "the in-flight claim is returned with the failure")

    async def test_a_claim_that_never_settles_stops_counting_at_its_lease(self):
        service, context = self.service([]), self.context()
        service._reserve_group_batch(context, self.job(context)["policy"], NOW, 2,
                                     "expression-learn:crashed")
        day = service._day(NOW)
        self.assertEqual(service._global_group_batches(day, at=NOW), 1,
                         "an admitted round holds its slot while it can still run")
        self.assertEqual(service._global_group_batches(day, at=NOW + timedelta(seconds=LEARN_LEASE_SECONDS)), 0,
                         "a crashed claim must not outlive the lease of its attempt")
        self.assertEqual(service._progress(context)["input_sequence"], 0,
                         "a crashed round never committed a checkpoint")

    async def test_two_windows_cannot_take_the_last_daily_batch(self):
        service = self.service([])
        policy = self.job(self.context())["policy"]
        service._reserve_group_batch(self.context("group:1"), policy, NOW, 2, "operation-a")
        with self.assertRaises(TaskError) as blocked:
            service._reserve_group_batch(self.context("group:2"), policy, NOW, 2, "operation-b")
        self.assertEqual(blocked.exception.code, "expression_group_budget_exhausted")
        self.assertEqual(service._global_group_batches(service._day(NOW), at=NOW), 1,
                         "two admitted attempts never both count as one slot twice")

    async def test_a_committed_batch_is_counted_once_and_releases_its_claim(self):
        service, context = self.service([]), self.context()
        service._reserve_group_batch(context, self.job(context)["policy"], NOW, 2, "operation-a")
        with self.runtime.transaction() as connection:
            service._commit_group_batch(connection, context, NOW, 2)
        day = service._day(NOW)
        self.assertEqual(self.claims(), [])
        self.assertEqual(service._global_group_batches(day, at=NOW), 1)
        progress = service._progress(context)
        self.assertEqual((progress["day"], progress["batches"], progress["input_sequence"]),
                         (day, 1, 2))

    async def test_yesterdays_claim_releases_at_its_lease_not_at_the_day_change(self):
        # A claim left behind by a crash is recognised by its lease, not only by
        # the calendar: yesterday's still-live claim must not keep holding a slot
        # of today's budget, while a claim inside its lease always counts.
        service, context = self.service([]), self.context()
        policy = self.job(context)["policy"]
        service._reserve_group_batch(context, policy, NOW, 2, "operation-a")
        tomorrow = NOW + timedelta(days=1)
        self.assertNotEqual(service._day(NOW), service._day(tomorrow), "the case crosses a day boundary")
        self.assertEqual(service._group_claims(service._day(tomorrow), tomorrow), [],
                         "a claim of yesterday never takes a slot of today")
        self.assertEqual(service._global_group_batches(service._day(tomorrow), at=tomorrow), 0,
                         "yesterday's abandoned claim must not block today's budget")


class MaterialRetryCapTests(GroupBatchFixture, unittest.IsolatedAsyncioTestCase):
    """One material signature is retried three times and then left alone.

    A failed round keeps its samples eligible, but "eligible" cannot mean forever:
    an initial attempt plus three retries that all failed says the samples are not
    learnable with the current route, and every further scan would spend another
    model call on them. The cap belongs to the signature — a different sample set
    starts with a full allowance again.
    """

    async def test_the_wait_doubles_per_retry_and_the_attempt_after_the_cap_never_runs(self):
        service, context = self.service(["failed"] * 4), self.context()
        first = await service.run(context, "学习这一批")
        self.assertEqual(first["state"], "failed", first)
        self.assertEqual(self.calls, 1)

        for epoch, wait in enumerate(RETRY_WAITS):
            self.now += timedelta(seconds=wait - 1)
            early = await service.run(context, "学习这一批")
            self.assertEqual(early["state"], "already_attempted",
                             "retry %d must not run inside its %ds backoff" % (epoch + 1, wait))
            self.assertEqual(self.calls, epoch + 1, "a wait spends no model call")
            self.now += timedelta(seconds=1)
            retried = await service.run(context, "学习这一批")
            self.assertEqual(retried["state"], "failed", retried)
            self.assertEqual(self.calls, epoch + 2)
            self.assertNotEqual(retried["task_id"], first["task_id"])
            following = RETRY_WAITS[epoch + 1] if epoch + 1 < len(RETRY_WAITS) else 0
            self.assertEqual(service.next_checks[context.key], self.now + timedelta(seconds=following),
                             "the window is held exactly as long as the next attempt declares")

        stopped = await service.run(context, "学习这一批")
        self.assertEqual(stopped["state"], "retry_exhausted", stopped)
        self.assertEqual(stopped["code"], "expression_retry_exhausted")
        self.assertEqual(self.calls, 1 + len(RETRY_WAITS),
                         "the cap stops the automatic retry, not the retries it allows")
        self.assertEqual(service._lease_epoch(digest([service._work_key(context), MATERIAL, "balanced"])),
                         len(RETRY_WAITS), "the exhausted signature keeps the epoch it reached")

    async def test_retries_are_counted_per_material_signature_not_per_window(self):
        service, context = self.service(["failed"] * 4 + ["succeeded"]), self.context()
        for wait in (0, *RETRY_WAITS):
            self.now += timedelta(seconds=wait)
            self.assertEqual((await service.run(context, "学习这一批"))["state"], "failed")
        self.assertEqual((await service.run(context, "学习这一批"))["state"], "retry_exhausted")
        self.assertEqual(self.calls, 4)

        # New samples change the material hash, and with it the request key and
        # the lease epoch: the window may learn again with a full allowance.
        self.material = "fedcba9876543210fedc"
        learned = await service.run(context, "学习这一批")
        self.assertEqual(learned["state"], "succeeded", learned)
        self.assertEqual(self.calls, 5, "different samples are a different signature")
        work_key = service._work_key(context)
        self.assertEqual(service._lease_epoch(digest([work_key, MATERIAL, "balanced"])), 3,
                         "the spent allowance stays with the material it was spent on")
        self.assertEqual(service._lease_epoch(digest([work_key, self.material, "balanced"])), 0,
                         "a new sample set starts at epoch zero")


class DerivedLimitTests(unittest.TestCase):
    def test_parse_output_treats_the_dispatched_limit_as_a_hard_cap(self):
        self.assertEqual(len(learning.parse_output(
            json.dumps(learning_payload(2), ensure_ascii=False),
            allowed_evidence={"input:a", "input:b"}, limit=2).signals), 2)
        with self.assertRaises(ValueError) as caught:
            learning.parse_output(json.dumps(learning_payload(3), ensure_ascii=False),
                                  allowed_evidence={"input:a", "input:b"}, limit=2)
        self.assertEqual(str(caught.exception), "invalid_shape")

    def test_the_service_reports_an_over_limit_output_as_its_own_error(self):
        with self.assertRaises(TaskError) as caught:
            parse_output(json.dumps(learning_payload(2), ensure_ascii=False),
                         allowed_evidence={"input:a", "input:b"}, limit=1)
        self.assertEqual(caught.exception.code, "expression_output_invalid")

    def test_a_wider_limit_never_raises_the_product_cap(self):
        # ``limit`` narrows the ask; it must never widen the per-run maximum, or
        # a caller passing a larger number would accept an answer the product
        # contract bounds and the prompt schema never promised.
        cap = learning.MAX_SIGNALS_PER_RUN
        self.assertEqual(len(learning.parse_output(
            json.dumps(learning_payload(cap), ensure_ascii=False),
            allowed_evidence={"input:a", "input:b"}, limit=cap + 87).signals), cap)
        with self.assertRaises(ValueError) as caught:
            learning.parse_output(json.dumps(learning_payload(cap + 1), ensure_ascii=False),
                                  allowed_evidence={"input:a", "input:b"}, limit=99)
        self.assertEqual(str(caught.exception), "invalid_shape")


class PrivateScanAttributionTests(unittest.TestCase):
    def test_a_candidate_failure_is_recorded_on_the_candidate_window(self):
        service = ExpressionStyleService.__new__(ExpressionStyleService)
        first = SimpleNamespace(key="private-a", scope=SimpleNamespace(kind=ScopeKind.PRIVATE))
        second = SimpleNamespace(key="private-b", scope=SimpleNamespace(kind=ScopeKind.PRIVATE))
        service.closed = False
        service.workers, service.results, service.next_checks = {}, {}, {}
        service.cursor, service._next_discovery_at = 0, None
        service.gateway = SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()}))
        service.runtime = SimpleNamespace()
        service.tasks = SimpleNamespace(now=lambda: NOW, identity=None)
        service.app = SimpleNamespace(state_maintenance=SimpleNamespace(
            bindings={first.key: (first, "instr-a", None), second.key: (second, "instr-b", None)}))
        service.samples = SimpleNamespace(messages=object(), role_owner=lambda context: ROLE)
        service._work_key = lambda context: context.key
        service._group_candidates = lambda at: ()

        def scan(context, instruction, at):
            if context.key == "private-b":
                raise TaskError("expression_cursor_invalid", "style-learning cursor is invalid")
            return False

        service._scan = scan
        with patch("companion_v2.expression.private_scope.role_private_contexts",
                   return_value=(first, second)):
            service.advance()

        self.assertEqual(service.results,
                         {"private-b": {"state": "blocked", "code": "expression_cursor_invalid"}},
                         "the blocked state belongs to the window whose read failed")


class GroupLearningCommitPipelineTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    """The claim accounting through the real host, scheduler and task ledger."""

    def setUp(self):
        super().setUp()
        self.calls = 0
        self.behavior = None
        self.native.provider.client.state.behavior = self.model

    async def model(self, payload, tools):
        if "role_expression_samples" not in json.dumps(payload, ensure_ascii=False):
            return completion()
        self.calls += 1
        source = self.source(payload)
        return self.behavior(source)

    @staticmethod
    def source(payload):
        for message in payload.get("messages", []):
            if message.get("role") != "user" or not isinstance(message.get("content"), str):
                continue
            try:
                content = json.loads(message["content"])
            except ValueError:
                continue
            for item in content.get("materials", []):
                if item.get("purpose") == "role_expression_samples":
                    return json.loads(item["text"])
        return None

    def invalid(self, _source):
        return completion("这不是 JSON")

    def over_limit(self, source):
        # One candidate above what this run actually dispatched. The count lives
        # in the material's own ``output_schema`` and follows the route's output
        # ceiling, so the answer stays over-limit whatever default that ceiling
        # is raised to; a fixed two only exceeded the smallest ceilings.
        asked = source["output_schema"]["properties"]["signals"]["maxItems"]
        self.assertIsInstance(asked, int)
        return completion(json.dumps(learning_payload(asked + 1, tuple(
            item["handle"] for item in source["samples"][:2])), ensure_ascii=False))

    def answer(self, source):
        return completion(json.dumps({"summary": "合成的短句节奏", "signals": [
            {**SIGNAL, "evidence_handles": [item["handle"] for item in source["samples"][:2]]}]},
            ensure_ascii=False))

    def configure(self, changes):
        app = self.controller.session.application
        owner, fields = ExpressionConfigurationOwner(app.runtime), configuration_fields()
        target = ConfigTarget(app.runtime.installation_id, ConfigLayer.INSTALLATION)
        snapshot = owner.read(target, fields)
        owner.write(target, fields,
                    changes={GROUP + "." + name: value for name, value in changes.items()},
                    reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())

    async def group_context(self, index, sender="200"):
        result = await self.controller.receive_claimed(self.group_event(
            identifier="learning-commit-" + str(index), sender=sender,
            text="嗯哼，那就这么定啦。第 " + str(index) + " 句。"))
        self.assertTrue(result.is_success, result.failure)
        await wait_for_batch(self.controller.session, result.unwrap())
        app = self.controller.session.application
        return TaskContext.restore(json.loads(app.messages.batch(result.unwrap())["context_json"]))

    async def learn(self, app, context):
        service = app.expression_style
        work_key = service._work_key(context)
        started = ExpressionStyleManagement(app).learn(
            operator=dashboard_operator(app.runtime, "tester", at=self.clock.now()),
            context=context).unwrap()
        self.assertIn(started["state"], ("queued", "running"), started)
        worker = service.workers[work_key]
        deadline = asyncio.get_running_loop().time() + 10
        while not worker.done():
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(0.01)
        return service.results[work_key]

    def settings(self, changes):
        self.configure({"private_learning_enabled": False, "group_learning_enabled": True,
                        "group_min_samples": 5, "group_new_messages": 5, **changes})

    async def seed(self, count):
        context = None
        for index in range(count):
            context = await self.group_context(index)
        return context

    async def test_a_failed_round_keeps_the_batch_and_retries_after_its_lease(self):
        app = await self.start_host()
        self.settings({"group_daily_batch_limit": 1})
        context = await self.seed(5)

        self.behavior = self.invalid
        first = await self.learn(app, context)
        self.assertEqual(first["state"], "failed", first)
        service = app.expression_style
        self.assertEqual(service.status(context)["group_batches_today"], 0,
                         "a failed round must not burn the only batch of the day")
        self.assertEqual(service._progress(context)["input_sequence"], 0,
                         "the failed round must not advance the new-message checkpoint")

        self.behavior = self.answer
        self.clock.advance(timedelta(seconds=LEARN_LEASE_SECONDS))
        retried = await self.learn(app, context)
        self.assertEqual(retried["state"], "succeeded", retried)
        self.assertNotEqual(retried["task_id"], first["task_id"])
        self.assertEqual(service.status(context)["group_batches_today"], 1)
        self.assertGreater(service._progress(context)["input_sequence"], 0)

    async def test_the_retry_cap_is_visible_in_the_status_and_spends_nothing_after_it(self):
        app = await self.start_host()
        self.settings({"group_daily_batch_limit": 6})
        context = await self.seed(5)
        service = app.expression_style
        self.behavior = self.invalid

        for wait in (0, *RETRY_WAITS):
            self.clock.advance(timedelta(seconds=wait))
            attempt = await self.learn(app, context)
            self.assertEqual(attempt["state"], "failed", attempt)
        self.assertEqual(self.calls, 1 + len(RETRY_WAITS))

        stopped = await self.learn(app, context)
        self.assertEqual(stopped["state"], "retry_exhausted", stopped)
        self.assertEqual(stopped["code"], "expression_retry_exhausted")
        status = service.status(context)
        self.assertEqual((status["state"], status["code"]),
                         ("retry_exhausted", "expression_retry_exhausted"),
                         "the page has to show that this signature is no longer retried")
        self.assertEqual((status["retry_epoch"], status["retry_limit"]), (len(RETRY_WAITS), len(RETRY_WAITS)))
        self.assertEqual(self.calls, 1 + len(RETRY_WAITS), "a stopped signature spends no model call")

    async def test_an_output_over_the_dispatched_limit_is_refused(self):
        app = await self.start_host()
        self.settings({"group_daily_batch_limit": 6})
        context = await self.seed(5)

        self.behavior = self.over_limit
        report = await self.learn(app, context)

        self.assertEqual(report["state"], "failed", report)
        self.assertEqual(report["code"], "expression_output_invalid")
        rows = app.runtime.read_all("SELECT signal_id FROM expression_style_signals", ())
        self.assertFalse(rows, "an over-limit answer must not commit any candidate")


class PrivateLearningCursorPipelineTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    """A private round that committed nothing leaves its scan checkpoint untouched."""

    def setUp(self):
        super().setUp()
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))

    async def test_a_failed_private_round_leaves_the_window_due(self):
        app = await self.start_host()
        self.native.provider.client.state.behavior = self.invalid
        context, _ = await self.accepted(text="我不喝咖啡")
        await self.accepted(identifier="event-2", text="我更喜欢喝茶")

        service = app.expression_style
        work_key = service._work_key(context)
        started = ExpressionStyleManagement(app).learn(
            operator=dashboard_operator(app.runtime, "tester", at=self.clock.now()),
            context=context).unwrap()
        self.assertIn(started["state"], ("queued", "running"), started)
        worker = service.workers[work_key]
        deadline = asyncio.get_running_loop().time() + 10
        while not worker.done():
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(0.01)
        self.assertEqual(service.results[work_key]["state"], "failed")

        status = service.status(context)
        self.assertTrue(status["due"], "a failed private round must stay eligible for its retry")
        self.assertIsNone(status["last_run_at"], "the scan checkpoint is committed only with a result")

    @staticmethod
    async def invalid(payload, _tools):
        if "role_expression_samples" in json.dumps(payload, ensure_ascii=False):
            return completion("这不是 JSON")
        return completion()


if __name__ == "__main__":
    unittest.main()
