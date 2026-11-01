"""Host-level scheduling of automatic expression learning.

The service had no host regression: a private window with admitted input could
stay in `source_not_selected` forever because no explicit main companion was
configured, and no `role.expression.learn` task was ever enqueued.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import unittest

from astrbot_host.management_identity import dashboard_operator
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.execution.models import TaskContext
from companion_v2.expression.configuration import (GROUP, ExpressionConfigurationOwner,
                                                   configuration_fields)
from tests.group_host_support import GroupFixture
from tests.multi_host_support import MultiFixture, multi_config
from tests.staging_bootstrap_support import wait_for_batch


class ExpressionLearningScheduleTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_due_private_window_enqueues_a_learning_task(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        await self.accepted(text="我不喝咖啡")
        context, batch = await self.accepted(identifier="event-2", text="我更喜欢喝茶")

        service = app.expression_style
        service.next_checks.clear()
        service.cursor = 0
        service._next_discovery_at = None
        service.advance()
        for _ in range(200):
            if service.workers or service.results:
                break
            await asyncio.sleep(0.01)
        state = service.results.get(service._work_key(context)) or {}
        self.assertTrue(service.workers, f"expression learning did not start: {state}")
        status = service.status(context)
        self.assertTrue(status["learning_enabled"])
        self.assertTrue(status["due"] is False)
        self.assertIsInstance(status["worker"], bool)
        self.assertEqual(status["limit"], 60)
        self.assertIsInstance(status["state"], str)

    async def test_a_window_rebound_after_a_policy_revision_change_still_learns(self):
        """A stale stored snapshot must not look like a changed role.

        Opening the companion page authenticates the dashboard operator, which
        advances the installation-wide ``policy_revision`` without touching
        this conversation. Reloading the host then re-binds the window from the
        current identity directory, so the live binding is newer than the batch
        the scheduler restores. The source check has to re-derive the window
        from it and start the due learning worker.
        """
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        first, _ = await self.accepted(text="我不喝咖啡")
        service = app.expression_style
        # Keep the running host from scanning this window while the drift and
        # the re-binding are arranged.
        service.next_checks[first.key] = app.tasks.now() + timedelta(hours=1)
        context, _ = await self.accepted(identifier="event-2", text="我更喜欢喝茶")
        self.assertEqual(context.key, first.key)

        operator = dashboard_operator(app.runtime, "tester", at=app.tasks.now())
        self.assertIsNotNone(operator)
        target = self.controller.settings.target("first")
        refreshed, instruction = await self.controller.session.synchronize(target)
        self.assertEqual(refreshed.key, context.key)
        self.assertGreater(refreshed.actor.policy_revision, context.actor.policy_revision)
        app.state_maintenance.bind(refreshed, instruction)
        stored_row = app.runtime.read_one("""SELECT context_json FROM conversation_batches
            WHERE origin_key = ? ORDER BY rowid DESC LIMIT 1""", (context.key,))
        stored = TaskContext.restore(json.loads(stored_row["context_json"]))
        self.assertEqual(stored.key, refreshed.key)
        self.assertLess(stored.actor.policy_revision, refreshed.actor.policy_revision)

        service.next_checks.clear()
        service.cursor = 0
        service._next_discovery_at = None
        service.advance()
        for _ in range(200):
            if service.workers or service.results:
                break
            await asyncio.sleep(0.01)
        work_key = service._work_key(context)
        state = service.results.get(work_key) or {}
        self.assertNotEqual(state.get("code"), "expression_binding_changed")
        self.assertIn(work_key, service.workers, f"expression learning did not start: {state}")


class ExpressionGroupWindowDriftTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    """A restored group batch must not look stale just because its revision is.

    The scheduler rebuilds group candidates from stored batches, which keep the
    ``policy_revision`` they were admitted with, while the host holds that same
    window under the revision the identity directory has reached since. Scanning
    the live binding must still start the due group worker.
    """

    def configure(self, changes):
        app = self.controller.session.application
        owner, fields = ExpressionConfigurationOwner(app.runtime), configuration_fields()
        target = ConfigTarget(app.runtime.installation_id, ConfigLayer.INSTALLATION)
        snapshot = owner.read(target, fields)
        owner.write(target, fields,
                    changes={GROUP + "." + name: value for name, value in changes.items()},
                    reset=(), expected_revision=snapshot.revision, observed_at=self.clock.now())

    async def group_accepted(self, identifier, *, text, sender="200"):
        result = await self.controller.receive_claimed(
            self.group_event(identifier=identifier, sender=sender, text=text))
        self.assertTrue(result.is_success, result.failure)
        await wait_for_batch(self.controller.session, result.unwrap())
        app = self.controller.session.application
        return TaskContext.restore(json.loads(app.messages.batch(result.unwrap())["context_json"]))

    async def test_a_group_batch_with_an_older_policy_revision_still_learns(self):
        app = await self.start_host()
        # Private learning stays off so the private queue cannot claim the scan
        # first, and the group window only needs a handful of samples.
        self.configure({"private_learning_enabled": False, "group_learning_enabled": True,
                        "group_min_samples": 5, "group_new_messages": 5})
        for index in range(5):
            await self.group_accepted(f"group-expression-{index}",
                                      text=f"嗯哼，那就这么定啦。第 {index} 句。")

        service = app.expression_style
        at = app.tasks.now()
        candidates = service._group_candidates(at)
        self.assertTrue(candidates, "the group window produced no scheduler candidate")
        candidate = candidates[0]
        instruction = service._group_instruction(candidate, at)
        self.assertIsNotNone(instruction)
        # The live window has moved on; the stored batch kept its revision.
        refreshed = replace(candidate, actor=replace(
            candidate.actor, policy_revision=candidate.actor.policy_revision + 1))
        self.assertTrue(refreshed.same_window(candidate))
        self.assertNotEqual(refreshed, candidate)
        app.state_maintenance.bind(refreshed, instruction)

        service.next_checks.clear()
        service.cursor = 0
        service._next_discovery_at = None
        service.advance()
        for _ in range(200):
            if service.workers or service.results:
                break
            await asyncio.sleep(0.01)
        work_key = service._work_key(candidate)
        state = service.results.get(work_key) or {}
        self.assertNotEqual(state.get("code"), "expression_binding_changed")
        self.assertIn(work_key, service.workers, f"group expression learning did not start: {state}")


if __name__ == "__main__":
    unittest.main()
