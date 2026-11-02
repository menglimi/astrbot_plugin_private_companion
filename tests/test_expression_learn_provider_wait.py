"""Expression learning waits inside one declared budget.

The learning task is the slowest background round this plugin runs: it sends a
17k-token material pack and reads the answer in a single round. Its waiting budget
used to be three unrelated literals — an inherited 60 s provider default, a 180 s
task definition and a 180 s deadline — so the innermost layer expired with no
relation to the outer one, and a cut round was reported as a generic worker
error. These tests pin the ordering, the recorded cause and the unchanged fast
path. All providers and conversations are synthetic.
"""

from datetime import datetime
import unittest

from companion_v2.execution.models import TaskError
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import (LEARN_BUDGET_SECONDS, LEARN_PROVIDER_TIMEOUT_SECONDS,
    ExpressionStyleService, expression_learning_profile)
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_v1_pipeline import ExpressionFixture
from tests.test_host_application import HostFixture


class ExpressionLearningBudgetTests(unittest.TestCase):
    def test_every_layer_derives_from_one_ceiling(self):
        self.assertLess(LEARN_PROVIDER_TIMEOUT_SECONDS, LEARN_BUDGET_SECONDS,
                        "the provider round must expire before the task that owns it")
        self.assertEqual(expression_learning_profile("primary").timeout_seconds,
                         LEARN_PROVIDER_TIMEOUT_SECONDS)
        definitions = {registration.definition.timeout_seconds
                       for registration in ExpressionStyleService(None).registrations}
        self.assertEqual(definitions, {LEARN_BUDGET_SECONDS})


class ExpressionLearningBudgetPipelineTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.model_behavior = self.expression_model

    async def seed_for_learning(self, host):
        """Reuse the reply pipeline's seeding so this suite reaches a real run."""
        return await pipeline.ExpressionReplyPipelineTests.seed(self, host)

    def learning_deadline(self, app):
        row = app.runtime.read_one("SELECT created_at, deadline_at FROM execution_tasks "
                                   "WHERE kind = ? ORDER BY created_at DESC LIMIT 1", ("role.expression.learn",))
        self.assertIsNotNone(row)
        return (datetime.fromisoformat(row["deadline_at"])
                - datetime.fromisoformat(row["created_at"])).total_seconds(), row

    async def run_learning(self, app, context):
        manager = ExpressionStyleManagement(app)
        self.assertIn(manager.learn(operator=self.core, context=context).unwrap()["state"],
                      ("queued", "running"))
        await self.until(lambda: not app.expression_style.status(context)["running"])
        return app.expression_style.status(context)["last_result"]

    async def test_a_fast_round_keeps_its_owner_result_and_the_declared_budget(self):
        host = await self.start()
        app = host.application
        context = await self.seed_for_learning(host)
        self.configure_expression(app, {"private_learning_enabled": True})
        last = await self.run_learning(app, context)
        self.assertEqual(last["state"], "succeeded")
        self.assertEqual(last["code"], "owner_result")
        seconds, _ = self.learning_deadline(app)
        self.assertAlmostEqual(seconds, LEARN_BUDGET_SECONDS, delta=1)

    async def test_a_cut_model_round_is_reported_by_its_own_code(self):
        host = await self.start()
        app = host.application
        context = await self.seed_for_learning(host)
        self.configure_expression(app, {"private_learning_enabled": True})

        async def cut_round(envelope):
            if envelope.original.output_format == "role_expression_learning":
                raise TaskError("model_timeout", "provider wait timed out; original result is unknown")
            return await self.expression_model(envelope)

        self.model_behavior = cut_round
        last = await self.run_learning(app, context)
        self.assertEqual(last["state"], "failed")
        self.assertEqual(last["code"], "model_timeout",
                         "the owner must report why the round stopped, not the supervisor's stop reason")
        seconds, row = self.learning_deadline(app)
        self.assertAlmostEqual(seconds, LEARN_BUDGET_SECONDS, delta=1)
        calls = app.runtime.read_all("SELECT c.state, f.code AS failure_code FROM model_calls c "
                                     "JOIN execution_tasks t ON t.task_id = c.task_id "
                                     "LEFT JOIN model_call_failures f USING(call_id) "
                                     "WHERE t.kind = ? ORDER BY c.started_at", ("role.expression.learn",))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
