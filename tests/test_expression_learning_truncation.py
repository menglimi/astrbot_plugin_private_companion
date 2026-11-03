"""A learning round cut by its output ceiling retries once with a smaller ask.

Production evidence (2026-11-01): every learning round routed to a thinking
provider ended with ``finish_reason=length`` while only ~4% of the declared
ceiling became visible text, so the structured answer was never complete. The
gateway is right to refuse an incomplete body; what the owner owed the window was
a way out of it:

* one degraded retry inside the same material batch — a truncation is a known
  round whose call the gateway already discarded, so the batch may spend one
  more call now instead of waiting out its lease and then the 90-minute scan;
* the reduced ask travels through the same parameterized entry the first round
  used, so the second request really carries fewer candidates;
* the newest outcome is recorded on disk, so ``status()`` after a restart still
  says which code failed and when instead of pairing a persisted ``last_run_at``
  with a fresh ``not_run``.

All providers, models and conversations here are synthetic; no test calls a real
model.
"""

from datetime import timedelta
import json
from types import SimpleNamespace
import unittest

from companion_v2.execution.models import ProviderBinding, digest
from companion_v2.execution.runner import RunResult
from companion_v2.expression.learning import mode_instruction, signal_limit
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import (CURSOR_PREFIX, KIND, OUTCOME_PREFIX,
                                                    ExpressionStyleService, reduced_signal_limit)
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.model_gateway.models import (Amount, CapturedModelOutput, LocalProvider,
                                               ProviderResponse)
from companion_v2.storage.models import OperationState
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_learning_skip import SkipNoteFixture
from tests.test_expression_v1_pipeline import ExpressionFixture
from tests.test_host_application import HostFixture


# The production task profile declares the reply-sized ceiling; the retry this
# suite pins is the one derived from it, not from the fixture's tiny default.
LEARNING_TOKENS = 4096
PROVIDER_OUTPUT_TOKENS = 8192
TRUNCATED_BODY = '{"summary":"可复用的短句节奏","signals":['
LOGGER = "companion_v2.expression.style_service"


class ExpressionLearningTruncationTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.expression_profiles(learning_tokens=LEARNING_TOKENS)
        self.model_behavior = self.scripted
        self.truncated_rounds = 0

    @staticmethod
    def material_size(envelope):
        return sum(len(item.payload.encode("utf-8")) for item in envelope.original.context.materials)

    def reserve(self, envelope):
        """The adapter's accounting: material plus the declared output ceiling."""
        return Amount(self.material_size(envelope) + envelope.max_output_tokens, 1000)

    async def scripted(self, envelope):
        """Cut the next learning round at its ceiling, then answer normally.

        ``truncated_rounds`` is how many learning rounds the ceiling cuts: a
        truncation is scripted at the transport layer exactly as production
        reports it (a captured body with ``finish_reason=length``), so the gateway
        produces its own ``model_output_truncated`` code.
        """
        if envelope.original.output_format == "role_expression_learning" and self.truncated_rounds > 0:
            self.truncated_rounds -= 1
            return ProviderResponse("generated",
                captured_output=CapturedModelOutput(TRUNCATED_BODY, "length"),
                usage=Amount(15, 40), receipt_ref="receipt-learning-truncated")
        return await self.expression_model(envelope)

    def learning_requests(self):
        return [item for item in self.called if item.original.output_format == "role_expression_learning"]

    @staticmethod
    def learning_material(envelope):
        return json.loads(next(item for item in envelope.original.context.materials
                               if item.purpose == "role_expression_samples").payload)

    async def prepare(self):
        """Seed an authorized private window whose learning route is real.

        The provider declares a ceiling large enough for the production task
        profile, and the task profile declares the reply-sized output limit the
        retry is derived from.
        """
        self.providers = (LocalProvider(ProviderBinding("model.text", "primary", "1", "model-1"),
                          ("text", "tools"), PROVIDER_OUTPUT_TOKENS, self.reserve, self.generate),)
        host = await self.start(self.host())
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        # The driver's own discovery pass must not start a second round behind
        # these assertions; this suite asks for its rounds.
        app.expression_style.next_checks[context.key] = self.clock.now() + timedelta(days=1)
        return app, context

    async def learn(self, app, context):
        service = app.expression_style
        work_key = service._work_key(context)
        started = ExpressionStyleManagement(app).learn(operator=self.core, context=context).unwrap()
        self.assertIn(started["state"], ("queued", "running"))
        await self.until(lambda: (service.results.get(work_key) or {}).get("state") in ("succeeded", "failed"))
        return service.results[work_key]

    @staticmethod
    def restarted(app):
        """A process that lost only the in-memory reports, not the database.

        Only the collaborators ``status()`` reads are copied over: the real
        restart path rebuilds these from the same runtime, and attaching a second
        service would register the same runner twice in this fixture.
        """
        service = ExpressionStyleService(app.runtime)
        service.tasks = app.tasks
        service.samples = app.expression_style.samples
        return service

    def outcome_key(self, app, context):
        return OUTCOME_PREFIX + digest([app.expression_style.samples.role_owner(context), context.scope])

    def task_states(self, app):
        return [row["state"] for row in app.runtime.read_all(
            "SELECT state FROM execution_tasks WHERE kind = ? ORDER BY rowid", (KIND,))]

    async def test_a_truncated_round_is_retried_with_a_smaller_ask(self):
        app, context = await self.prepare()
        self.truncated_rounds = 1

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            last = await self.learn(app, context)

        requests = self.learning_requests()
        self.assertEqual(len(requests), 2, "a truncated round retries exactly once")
        first, retry = requests
        first_limit = signal_limit("balanced", LEARNING_TOKENS)
        retry_limit = reduced_signal_limit(first_limit)
        self.assertGreater(first_limit, 1, "the fixture profile leaves a smaller ask to retry with")
        self.assertIn("降低产出条数后重试", "\n".join(logs.output),
                      "the log says the batch was retried with a reduced ask, not after a lease")
        self.assertIn(f"limit={first_limit}→{retry_limit}", "\n".join(logs.output))
        self.assertEqual(self.learning_material(first)["output_schema"]["properties"]["signals"]["maxItems"],
                         first_limit)
        retry_material = self.learning_material(retry)
        self.assertEqual(retry_material["output_schema"]["properties"]["signals"]["maxItems"], retry_limit)
        self.assertIn(mode_instruction("balanced", retry_limit), retry.original.instruction)
        self.assertEqual(retry_material["samples"], self.learning_material(first)["samples"],
                         "the retry reuses the same evidence batch instead of re-collecting it")
        self.assertEqual(retry_material["existing_rules"],
                         self.learning_material(first)["existing_rules"],
                         "the retry reuses the same reference rules")

        self.assertEqual(last["state"], "succeeded", last)
        self.assertEqual(last["truncation_retry"], {"limit": first_limit, "retry_limit": retry_limit},
                         "the result says the batch succeeded only after a degraded retry")
        self.assertEqual(self.task_states(app), ["failed", "succeeded"],
                         "the retry runs under a fresh task identity, after the failed one settled")
        pending = ExpressionStyleManagement(app).list(operator=self.core, context=context).unwrap()["items"]
        self.assertEqual(len(pending), 1, "only the successful round persisted a candidate batch")

        live = app.expression_style.status(context)
        self.assertEqual(live["state"], "succeeded")
        self.assertIsNone(live["code"], "a successful round is not displayed with an internal reason code")
        remembered = self.restarted(app).status(context)
        self.assertEqual(remembered["state"], "succeeded")
        self.assertIsNone(remembered["code"])
        self.assertIsNotNone(remembered["last_run_at"])

    async def test_a_round_truncated_twice_fails_after_one_retry(self):
        app, context = await self.prepare()
        self.truncated_rounds = 10

        last = await self.learn(app, context)

        self.assertEqual(last["state"], "failed", last)
        self.assertEqual(last["code"], "model_output_truncated",
                         "the code that ended the batch is the retry's own truncation, not a generic stop")
        self.assertEqual(len(self.learning_requests()), 2, "bounded retry: never a third attempt")
        self.assertEqual(self.task_states(app), ["failed", "failed"])
        self.assertIn("truncation_retry", last, "the failed result still says a degraded retry happened")
        self.assertEqual(ExpressionStyleManagement(app).list(
            operator=self.core, context=context).unwrap()["items"], [],
            "a truncated round commits nothing")

        status = app.expression_style.status(context)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["code"], "model_output_truncated")

    async def test_a_restart_still_explains_the_last_failed_learning(self):
        app, context = await self.prepare()
        self.truncated_rounds = 10
        failed = await self.learn(app, context)
        self.assertEqual(failed["state"], "failed")

        app.expression_style.results.clear()
        remembered = self.restarted(app).status(context)

        self.assertEqual(remembered["state"], "failed",
                         "a restart must not report a window with a persisted run time as never run")
        self.assertEqual(remembered["code"], "model_output_truncated")
        self.assertEqual(remembered["last_result"]["state"], "failed",
                         "the material page reads this same status, so it must not say 'not learned yet'")
        self.assertEqual(remembered["last_result"]["code"], "model_output_truncated")
        self.assertIsNotNone(remembered["last_run_at"])
        self.assertFalse(remembered["worker"])
        self.assertFalse(remembered["due"], "the cursor still dates the last attempt")

        with app.runtime.transaction() as connection:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?",
                               (CURSOR_PREFIX + digest([app.expression_style.samples.role_owner(context),
                                                        context.scope]),))
        dated = self.restarted(app).status(context)
        self.assertEqual(dated["state"], "failed")
        self.assertEqual(dated["code"], "model_output_truncated")
        self.assertEqual(dated["last_result"]["state"], "failed")
        self.assertIsNotNone(dated["last_run_at"],
                             "the outcome record dates the run it describes even without a cursor")

    async def test_a_damaged_outcome_record_degrades_instead_of_breaking_status(self):
        app, context = await self.prepare()
        self.truncated_rounds = 1
        self.assertEqual((await self.learn(app, context))["state"], "succeeded")

        with app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (self.outcome_key(app, context), "{not json"))
        app.expression_style.results.clear()

        with self.assertLogs(LOGGER, level="WARNING"):
            remembered = self.restarted(app).status(context)

        self.assertEqual(remembered["state"], "not_run",
                         "a damaged record falls back to the cursor view instead of failing the page")
        self.assertIsNotNone(remembered["last_run_at"])
        self.assertFalse(remembered["worker"])


GROUP_SCOPE = ScopeRef("installation-a", "onebot", ScopeKind.GROUP, "native-g", "bot-a", "login-a",
                       "conversation-g", "session-g")
GROUP_MATERIAL = "0123456789abcdef0123"
GROUP_INPUT_SEQUENCE = 3


async def _noop(*_args):
    return None


class GroupLearningTruncationTests(SkipNoteFixture, unittest.IsolatedAsyncioTestCase):
    """A group batch is reserved once even when its round is truncated twice.

    The retry reuses the batch the first attempt already reserved: the group
    checkpoint must not advance again and the daily batch must not be charged
    twice. Otherwise the retry's own "new messages" gate fails with
    ``expression_group_budget_exhausted`` and the smaller ask never runs.
    """

    def context(self):
        return SimpleNamespace(key="group:1", scope=GROUP_SCOPE)

    def job(self, material=GROUP_MATERIAL, mode="whole"):
        return {"material_hash": material, "policy": SimpleNamespace(learning_mode=mode,
                group_daily_batch_limit=6), "at": self.now, "latest_sequence": GROUP_INPUT_SEQUENCE,
                "context": self.context()}

    def service(self, rounds):
        """The lease/epoch machinery is real; only the model round is scripted."""
        service = ExpressionStyleService(self.runtime)
        service.gateway = SimpleNamespace(router=SimpleNamespace(profiles={KIND: object()}))
        service.tasks = SimpleNamespace(now=lambda: self.now, submit=lambda spec: Outcome.success(spec))
        service.samples = SimpleNamespace(role_owner=lambda context: "role-owner")
        service.guard = _noop
        service.prepare = lambda context, instruction, at: self.job()
        service.identifier = lambda context, request_key: "expression-learn:" + request_key
        service.runner = SimpleNamespace(run=self.model_round)
        self.service_ref, self.rounds, self.model_calls = service, list(rounds), []
        return service

    async def model_round(self, context, task_id):
        """One scripted round; a truncated one spends its call and settles failed."""
        self.model_calls.append(task_id)
        # ``generate`` records the ask it used on this attempt's pending job; the
        # scripted round supplies it so ``run`` derives the retry's smaller ask.
        self.service_ref.pending[task_id]["learn_limit"] = 6
        if self.rounds.pop(0) == "truncated":
            return Outcome.success(RunResult(SimpleNamespace(state="failed", reason="worker_error"),
                                             "model_output_truncated"))
        self.operations.transition(task_id, expected_state=OperationState.ACCEPTED,
                                   next_state=OperationState.COMPLETED, observed_at=self.now).unwrap()
        return Outcome.success(RunResult(SimpleNamespace(state="succeeded", reason="owner_result")))

    def assert_batch_reserved_once(self, service, context):
        self.assertEqual(service._global_group_batches(service._day(self.now)), 1,
                         "one material batch must spend one daily group batch")
        progress = service._progress(context)
        self.assertEqual(progress["batches"], 1)
        self.assertEqual(progress["input_sequence"], GROUP_INPUT_SEQUENCE,
                         "the retry reuses the checkpoint the first attempt advanced")

    async def test_a_group_truncation_retry_does_not_reserve_the_batch_twice(self):
        service, context = self.service(["truncated", "succeeded"]), self.context()

        report = await service.run(context, "学习这一批")

        self.assertEqual(report["state"], "succeeded", report)
        self.assertNotEqual(report.get("code"), "expression_group_budget_exhausted",
                            "the retry must reach the model instead of its own budget gate")
        self.assertEqual(len(self.model_calls), 2, "a group batch retries once with a smaller ask")
        self.assertEqual(report["truncation_retry"], {"limit": 6, "retry_limit": 1})
        self.assert_batch_reserved_once(service, context)

    async def test_a_group_round_truncated_twice_fails_without_a_third_call(self):
        service, context = self.service(["truncated", "truncated"]), self.context()

        report = await service.run(context, "学习这一批")

        self.assertEqual(report["state"], "failed", report)
        self.assertEqual(report["code"], "model_output_truncated")
        self.assertEqual(len(self.model_calls), 2, "bounded retry: never a third group call")
        self.assert_batch_reserved_once(service, context)


if __name__ == "__main__":
    unittest.main()
