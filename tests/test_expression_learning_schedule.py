"""Host-level scheduling of automatic expression learning.

The service had no host regression: a private window with admitted input could
stay in `source_not_selected` forever because no explicit main companion was
configured, and no `role.expression.learn` task was ever enqueued.
"""

import asyncio
import unittest

from tests.multi_host_support import MultiFixture, multi_config


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


if __name__ == "__main__":
    unittest.main()
