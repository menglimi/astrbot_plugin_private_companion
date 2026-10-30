"""Two background owners that declared 180 s and could not spend it.

`content.authoring.draft` asked its provider for the same 180 s its own task declared,
so the two layers expired at the same instant; the supervising task starts first and
always won, and a slow writing model was reported as the supervisor's `timeout`
(and then re-labelled `worker_error` by the owner). `role.state.maintain` declared 180 s
but inherited the untouched 60 s `ModelTaskProfile` default, so its declared budget had
no relation to the time a round could actually spend - production cut one round at
60.007 s while another round on the same provider returned in 56.749 s.

Both now derive their provider budget from one declared ceiling. These tests pin the
derivation, the recorded cause and the unchanged fast path. All providers and
conversations are synthetic; the production numbers come from a read-only snapshot.
"""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import unittest

from companion_v2.conversation.content.authoring_execution import AuthoringParent
from companion_v2.conversation.content.authoring_tasks import (
    AUTHORING_BUDGET_SECONDS,
    AUTHORING_MARGIN_SECONDS,
    AUTHORING_PROVIDER_TIMEOUT_SECONDS,
    DRAFT_KIND,
    authoring_profiles,
)
from companion_v2.conversation.content.authoring_workflow import ContentAuthoringWorkflow
from companion_v2.conversation.foreground import BackgroundPromptTrigger, PromptRequest, SealedForegroundContext
from companion_v2.execution.models import TaskError, encode
from companion_v2.execution.runner import RunResult
from companion_v2.foundation.outcome import Outcome
from companion_v2.role_work.state.maintenance import (KIND as STATE_KIND, STATE_BUDGET_SECONDS,
    STATE_MARGIN_SECONDS, STATE_PROVIDER_TIMEOUT_SECONDS, StateMaintenance, state_profile)
from companion_v2.role_work.state.service import FIELDS
from tests.test_execution import ExecutionFixture
from tests.test_management_access import NOW
from tests.test_role_state import StateFixture
from tests.test_role_tool_loop import answered


def state_data(**changes):
    return {name: [] if name == "conditions" else None for name in FIELDS} | changes


class DeclaredBudgetTests(unittest.TestCase):
    def test_the_writing_round_expires_before_the_task_that_owns_it(self):
        self.assertLess(AUTHORING_PROVIDER_TIMEOUT_SECONDS, AUTHORING_BUDGET_SECONDS)
        self.assertEqual(AUTHORING_PROVIDER_TIMEOUT_SECONDS,
                         AUTHORING_BUDGET_SECONDS - AUTHORING_MARGIN_SECONDS)
        self.assertEqual(authoring_profiles("primary")[0].timeout_seconds,
                         AUTHORING_PROVIDER_TIMEOUT_SECONDS)
        self.assertEqual({registration.definition.timeout_seconds
                          for registration in ContentAuthoringWorkflow(None).registrations},
                         {AUTHORING_BUDGET_SECONDS})

    def test_the_state_round_expires_before_the_task_that_owns_it(self):
        self.assertLess(STATE_PROVIDER_TIMEOUT_SECONDS, STATE_BUDGET_SECONDS)
        self.assertEqual(STATE_PROVIDER_TIMEOUT_SECONDS, STATE_BUDGET_SECONDS - STATE_MARGIN_SECONDS)
        self.assertEqual(state_profile("primary").timeout_seconds, STATE_PROVIDER_TIMEOUT_SECONDS)
        self.assertEqual({registration.definition.timeout_seconds
                          for registration in StateMaintenance(None).registrations},
                         {STATE_BUDGET_SECONDS})


class _StubRunner:
    """Return the supervised attempt an owner would see, without running a model."""

    def __init__(self, record, code=None):
        self.record, self.code = record, code

    async def run(self, _context, _task_id):
        return Outcome.success(RunResult(self.record, self.code))

    async def close(self):
        return ()


class AuthoringBudgetRoundTests(ExecutionFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.workflow = ContentAuthoringWorkflow(self.runtime)
        self.registrations = self.workflow.registrations
        self.tasks = self.supervisor()
        self.tasks.attach(expected_generation=1).unwrap()
        gateway = type("Gateway", (), {"runtime": self.runtime, "tasks": self.tasks,
                                       "router": type("Router", (), {"profiles": {DRAFT_KIND: object()}})()})()
        self.workflow.attach(gateway, require_running=lambda _context: None)

    def request(self):
        trigger = BackgroundPromptTrigger("authoring-basis", self.context.actor.principal,
                                          self.context.scope, NOW)
        return PromptRequest(trigger, "原角色人格提示", SealedForegroundContext((), (), ()),
                             "写一段", self.context.role_binding)

    def deadline_seconds(self):
        row = self.runtime.read_one(
            "SELECT created_at, deadline_at FROM execution_tasks WHERE kind = ?", (DRAFT_KIND,))
        self.assertIsNotNone(row)
        return (datetime.fromisoformat(row["deadline_at"])
                - datetime.fromisoformat(row["created_at"])).total_seconds()

    async def draft(self, code=None):
        from companion_v2.conversation.content import authoring_service
        parent = AuthoringParent(self.context, "reply-task", NOW + timedelta(seconds=600), lambda: None)
        job_id = self.workflow._job(parent, type("Call", (), {"call_id": "call-1"})(),
                                    self.request(), authoring_service.effective_settings(self.runtime, self.context),
                                    authoring_service.prepare(action="draft", brief="写一段"), {})
        self.workflow.runner = _StubRunner(type("Record", (), {"state": "failed", "reason": "worker_error"})(), code)
        return await self.workflow._draft(parent, job_id)

    async def test_a_cut_writing_round_is_reported_by_its_own_code(self):
        with self.assertRaises(TaskError) as raised:
            await self.draft("model_timeout")
        self.assertEqual(raised.exception.code, "model_timeout",
                         "the owner must report the model stop, not the supervisor's worker_error")
        self.assertAlmostEqual(self.deadline_seconds(), AUTHORING_BUDGET_SECONDS, delta=1)

    async def test_a_task_level_cut_still_reports_the_supervised_reason(self):
        with self.assertRaises(TaskError) as raised:
            await self.draft()
        self.assertEqual(raised.exception.code, "worker_error")
        self.assertAlmostEqual(self.deadline_seconds(), AUTHORING_BUDGET_SECONDS, delta=1)


class StateBudgetRoundTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def ready(self):
        host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True)
        app.state_maintenance.bind(context, "原角色人格提示")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?",
                               ((NOW + timedelta(days=1)).isoformat(),))
        await self.start(host)
        return app, context

    def deadline_seconds(self):
        row = self.runtime.read_one("SELECT created_at, deadline_at FROM execution_tasks WHERE kind = ? "
                                    "ORDER BY created_at DESC LIMIT 1", (STATE_KIND,))
        self.assertIsNotNone(row)
        return (datetime.fromisoformat(row["deadline_at"])
                - datetime.fromisoformat(row["created_at"])).total_seconds()

    async def test_a_cut_state_round_records_the_model_stop_and_the_declared_budget(self):
        app, context = await self.ready()

        async def cut(_envelope):
            raise TaskError("model_timeout", "provider wait timed out; original result is unknown")

        self.model_behavior = cut
        result = await app.state_maintenance.run(context, "原角色人格提示", "cut-round")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["code"], "model_timeout")
        self.assertAlmostEqual(self.deadline_seconds(), STATE_BUDGET_SECONDS, delta=1)

    async def test_a_fast_round_still_commits_its_state_update(self):
        app, context = await self.ready()
        self.model_behavior = lambda _envelope: asyncio.sleep(
            0, result=answered(encode(state_data(hunger="有点饿", energy=45))))
        result = await app.state_maintenance.run(context, "原角色人格提示", "fast-round")
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(app.role_state.read(context)["values"]["hunger"], "有点饿")
        self.assertAlmostEqual(self.deadline_seconds(), STATE_BUDGET_SECONDS, delta=1)


if __name__ == "__main__":
    unittest.main()
