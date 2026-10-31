"""Read-only status for the daily self-review task."""

import unittest

from tests.multi_host_support import MultiFixture, multi_config


class SelfReviewStatusTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_status_reports_a_window_that_has_not_run_yet(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        context, batch = await self.accepted()

        status = app.self_review.status(context)
        self.assertEqual(status["state"], "not_run")
        self.assertTrue(status["due"])
        self.assertIsNone(status["last_run_at"])
        self.assertFalse(status["worker"])
        self.assertIsNone(status["code"])


if __name__ == "__main__":
    unittest.main()
