"""A full shared lane is capacity, not an admission denial.

The administrator diagnostic composes on the same background lane as automatic
proactive work. When every slot is held for a moment, the submitted row is
still exactly what the owner handed over, so the diagnostic waits and re-claims
it instead of telling the operator that admission failed.
"""

import json
import unittest
from unittest.mock import patch

from companion_v2.execution.models import ClaimResult
from companion_v2.foundation.outcome import Outcome
from tests.proactive_support import ContactFixture


class ProactiveCapacityWaitTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    def full_lane(self, app, *, remaining=None):
        """Report a full lane before delegating to the real claim.

        ``remaining`` bounds how many claims are refused; without it every claim
        stays refused, i.e. the lane never frees inside the test.
        """
        original = app.tasks.claim_task
        seen = {"claims": 0}

        def claim_task(context, task_id):
            seen["claims"] += 1
            if remaining is None or seen["claims"] <= remaining:
                return Outcome.success(ClaimResult(None, "task_lane_full"))
            return original(context, task_id)

        app.tasks.claim_task = claim_task
        self.addCleanup(setattr, app.tasks, "claim_task", original)
        return seen

    async def test_diagnostic_waits_out_a_busy_lane_and_still_composes(self):
        app = await self.enabled()
        seen = self.full_lane(app, remaining=3)

        task = (await self.contact(app, self.opportunity("lane-freed"),
                                   administrator_test=True)).unwrap().task

        self.assertEqual(task.state, "succeeded")
        self.assertEqual(len(self.called), 1)
        self.assertGreaterEqual(seen["claims"], 4)
        self.assertEqual(self.sent, [], "a wait for capacity must not send anything early")

    async def test_diagnostic_names_a_lane_that_never_frees(self):
        app = await self.enabled()
        self.full_lane(app)

        with patch("companion_v2.proactive.service.CONTACT_SLOT_WAIT_SECONDS", 0.3):
            result = await self.contact(app, self.opportunity("lane-stays-busy"),
                                        administrator_test=True)

        self.assertEqual(result.failure.code, "proactive_lane_busy")
        self.assertEqual(self.called, [], "a task that never got a slot must not call the model")
        row = app.runtime.read_one("""SELECT state, record_json FROM execution_tasks
            WHERE kind = 'proactive.compose' ORDER BY updated_at DESC LIMIT 1""", ())
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual(json.loads(row["record_json"])["reason"], "proactive_lane_busy")
        queued = app.runtime.read_one("""SELECT COUNT(*) n FROM execution_tasks
            WHERE kind = 'proactive.compose' AND state IN ('created', 'retry_scheduled')""", ())
        self.assertEqual(queued["n"], 0, "an abandoned reservation must not stay queued")

    async def test_ordinary_contact_reports_the_lane_shortfall_without_waiting(self):
        app = await self.enabled()
        self.full_lane(app)

        result = await self.contact(app, self.opportunity("ordinary-lane-full"))

        self.assertEqual(result.failure.code, "task_lane_full")
        self.assertEqual(self.called, [])


if __name__ == "__main__":
    unittest.main()
