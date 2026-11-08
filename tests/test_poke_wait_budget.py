"""How long a poke may wait for its model round.

Every assertion here reads configuration or the task ledger.  Nothing measures
elapsed time, so these tests state the *declared* budgets a poke gets, not how
fast this machine happened to be during the run.

Two layers matter, and only their relation to each other makes the wait usable:

* ``POKE_PROVIDER_TIMEOUT_SECONDS`` -- how long one provider round may take;
* ``POKE_BUDGET_SECONDS``           -- the poke task's own deadline, which also
  has to absorb queueing behind another background owner;
* the registered task definition, whose timeout caps the attempt lease
  (``min(deadline, claim + definition.timeout_seconds)``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from companion_v2.conversation.pokes import (
    MODEL_REPLY_KIND, POKE_BUDGET_SECONDS, POKE_MARGIN_SECONDS, POKE_PROVIDER_TIMEOUT_SECONDS,
    PokeReplyModel, poke_reply_profile,
)
from companion_v2.execution.models import Lane, TaskResult, digest
from companion_v2.execution.service import TaskSupervisor
from companion_v2.execution.waiting import BackgroundTaskQueue
from companion_v2.model_gateway.models import ModelTaskProfile
from companion_v2.model_gateway.routing import configuration_fields
from tests.test_execution import ExecutionFixture


class PokeWaitBudgetConfigTests(unittest.TestCase):
    """The declared numbers, read from the same objects the host assembles."""

    def test_the_poke_model_round_waits_forty_five_seconds(self):
        profile = poke_reply_profile("provider-a", max_output_tokens=128)
        self.assertEqual((profile.kind, profile.lane), (MODEL_REPLY_KIND, Lane.BACKGROUND))
        self.assertEqual(profile.timeout_seconds, 45)
        # One short answer; the wait is what changed, not the output budget.
        self.assertEqual(profile.max_output_tokens, 128)

    def test_the_task_budget_is_sixty_seconds_and_the_round_is_derived_from_it(self):
        self.assertEqual(POKE_BUDGET_SECONDS, 60)
        self.assertEqual(POKE_MARGIN_SECONDS, 15)
        self.assertEqual(POKE_PROVIDER_TIMEOUT_SECONDS, POKE_BUDGET_SECONDS - POKE_MARGIN_SECONDS)
        self.assertGreater(POKE_MARGIN_SECONDS, 0)
        # The round must expire first: text the provider already produced still has
        # to be parsed and sent before the poke task is out of time.
        self.assertLess(POKE_PROVIDER_TIMEOUT_SECONDS, POKE_BUDGET_SECONDS)

    def test_the_registered_task_definition_never_caps_the_round_early(self):
        (registration,) = PokeReplyModel(object(), now=lambda: None).registrations
        definition = registration.definition
        self.assertEqual(definition.kind, MODEL_REPLY_KIND)
        self.assertEqual(definition.lane, Lane.BACKGROUND)
        self.assertEqual(definition.timeout_seconds, POKE_BUDGET_SECONDS)
        self.assertEqual(definition.max_attempts, 1)
        # ``claim_task`` stores ``min(deadline_at, claim + definition.timeout_seconds)``
        # as the attempt lease, so a shorter definition would silently hand a poke
        # that queued less than the round it declares.
        self.assertGreater(definition.timeout_seconds, POKE_PROVIDER_TIMEOUT_SECONDS)

    def test_an_operator_can_still_override_the_wait_in_the_configuration_registry(self):
        fields = {field.field_id: field
                  for field in configuration_fields((poke_reply_profile("provider-a"),))}
        field = fields["model.conversation.poke_reply.timeout_seconds"]
        self.assertEqual(field.default, POKE_PROVIDER_TIMEOUT_SECONDS)
        self.assertEqual((field.minimum, field.maximum), (1, 600))

    def test_raising_the_poke_wait_leaves_another_task_wait_alone(self):
        reply = ModelTaskProfile("conversation.reply", Lane.FOREGROUND, "provider-a", "fixture reply")
        fields = {field.field_id: field
                  for field in configuration_fields((reply, poke_reply_profile("provider-a")))}
        self.assertEqual(fields["model.conversation.reply.timeout_seconds"].default, reply.timeout_seconds)
        self.assertNotEqual(fields["model.conversation.reply.timeout_seconds"].default,
                            fields["model.conversation.poke_reply.timeout_seconds"].default)
        # Two task kinds, two independent configuration groups: the poke value can
        # never be adopted on behalf of the conversational reply.
        self.assertNotEqual(fields["model.conversation.reply.timeout_seconds"].group_id,
                            fields["model.conversation.poke_reply.timeout_seconds"].group_id)


class _ScriptedPokeReplyModel(PokeReplyModel):
    """The real submit/claim/lease plumbing, with the provider round replaced."""

    answer = "在呢"

    async def generate(self, run):
        run.validate()
        self.results[run.task.task_id] = self.answer
        return TaskResult("succeeded", result_ref=digest(self.answer))


class _Host:
    """The three host surfaces ``PokeReplyModel.attach`` and ``validate`` read."""

    def __init__(self, fixture):
        self.runtime, self.tasks, self.gateway = fixture.runtime, fixture.tasks, None
        self.background_waiting = BackgroundTaskQueue(fixture.tasks)
        self.role_state = None

    def require_running(self):
        return None


class PokeDeadlineTests(ExecutionFixture, unittest.IsolatedAsyncioTestCase):
    """The deadline the ledger actually stores, for both poke streams."""

    def setUp(self):
        super().setUp()
        self.model = _ScriptedPokeReplyModel(self.runtime, now=self.clock.now)
        self.tasks = TaskSupervisor(self.runtime, self.registrations + self.model.registrations,
                                    limits=self.limits, now=self.clock.now)
        self.assertEqual(self.tasks.start(expected_generation=1).unwrap(), 2)
        self.model.attach(_Host(self))
        self.addAsyncCleanup(self.model.close)

    def submitted(self):
        rows = self.runtime.read_all(
            "SELECT t.task_id, t.next_run_at, t.deadline_at, a.lease_until FROM execution_tasks t "
            "JOIN execution_attempts a ON a.task_id = t.task_id WHERE t.kind = ? ORDER BY t.task_id",
            (MODEL_REPLY_KIND,))
        return tuple((row["task_id"],
                      (datetime.fromisoformat(row["deadline_at"])
                       - datetime.fromisoformat(row["next_run_at"])).total_seconds(),
                      (datetime.fromisoformat(row["lease_until"])
                       - datetime.fromisoformat(row["next_run_at"])).total_seconds()) for row in rows)

    async def test_both_poke_streams_submit_a_sixty_second_deadline(self):
        for mode in ("reply", "context"):
            self.assertEqual(
                await self.model.reply(self.context, "角色扮演指令", "event-" + mode, mode=mode),
                self.model.answer)
        rows = self.submitted()
        self.assertEqual(len(rows), 2)
        for _task_id, deadline_seconds, lease_seconds in rows:
            self.assertEqual(deadline_seconds, POKE_BUDGET_SECONDS)
            # The attempt lease is the other cap: it must not be the shorter one,
            # or a poke that queued behind another owner would lose the very round
            # this budget exists to pay for.
            self.assertEqual(lease_seconds, POKE_BUDGET_SECONDS)
            self.assertLess(POKE_PROVIDER_TIMEOUT_SECONDS, lease_seconds)

    async def test_stream_b_submits_the_same_budget_though_it_runs_later(self):
        # Stream B starts ``context_delay_seconds`` after the poke; its later start
        # must not shorten the budget its own model round needs.
        self.clock.advance(timedelta(seconds=5))
        self.assertEqual(
            await self.model.reply(self.context, "角色扮演指令", "event-later", mode="context"),
            self.model.answer)
        ((task_id, deadline_seconds, lease_seconds),) = self.submitted()
        self.assertTrue(task_id.startswith("poke-context-reply-"))
        self.assertEqual((deadline_seconds, lease_seconds), (POKE_BUDGET_SECONDS, POKE_BUDGET_SECONDS))
