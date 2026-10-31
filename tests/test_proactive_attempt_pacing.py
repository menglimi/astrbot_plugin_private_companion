"""Skipped drafts keep attempt pacing without extending unanswered-send silence."""

from datetime import timedelta
import json
import unittest

from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_role_tool_loop import answered


class ProactiveAttemptPacingTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def window_after_attempt(self, decision, *, attempt_second=1400):
        host, _ = await self.scheduled(automatic=False)
        app = host.application
        self.settings(app, {"interval_seconds": 600, "idle_seconds": 60,
            "dedup_enabled": False, "max_contacts_24h": 8,
            "proactive_closing_grace_minutes": 0}).unwrap()
        started = self.clock.now()
        for index, second in enumerate((1, 601)):
            self.clock.advance(started + timedelta(seconds=second) - self.clock.now())
            task = (await self.contact(app, self.opportunity("prior-send-" + str(index)))).unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")

        self.clock.advance(started + timedelta(seconds=attempt_second) - self.clock.now())

        async def generate(_envelope):
            return answered(json.dumps({"decision": decision,
                "text": "带来另一件小事。" if decision == "send" else ""}, ensure_ascii=False))

        self.model_behavior = generate
        attempt = (await self.contact(app, self.opportunity("recent-attempt"))).unwrap().task
        if decision == "send":
            await self.delivery_state(app, attempt.task_id, "delivered")
        else:
            self.assertEqual(attempt.state, "succeeded")

        # Two unanswered sends stretch the 600-second grid to 1020 seconds.
        # Its next slot opens at idle(60) + 2 * 1020 = 2100.
        self.clock.advance(started + timedelta(seconds=2100) - self.clock.now())
        context = self.contact_context()
        self.assertEqual(app.background_history.interaction(context, self.clock.now())[
            "contacts_since_last_input"], 3 if decision == "send" else 2)
        self.settings(app, {"automatic_enabled": True}).unwrap()
        row = app.contact_schedule.latest_conversation(context)
        return app, row

    async def test_skipped_draft_does_not_extend_unanswered_cooldown_into_next_slot(self):
        app, row = await self.window_after_attempt("skip")

        plan = app.contact_schedule._plan(row, self.clock.now())

        self.assertIsNotNone(plan)
        self.assertEqual(plan[1].opened_at, self.clock.now())
        self.assertEqual(len(self.contact_calls()), 3)  # Planning does not invoke another model.

    async def test_skip_still_waits_for_the_configured_base_attempt_interval(self):
        app, row = await self.window_after_attempt("skip", attempt_second=1800)

        self.assertIsNone(app.contact_schedule._plan(row, self.clock.now()))
        diagnostic = app.contact_schedule.diagnostic_for(self.contact_context(), at=self.clock.now())
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "recent_attempt_cooldown")

    async def test_actual_send_still_uses_the_unanswered_slowdown(self):
        app, row = await self.window_after_attempt("send")

        self.assertIsNone(app.contact_schedule._plan(row, self.clock.now()))
        diagnostic = app.contact_schedule.diagnostic_for(self.contact_context(), at=self.clock.now())
        self.assertEqual(diagnostic["evaluation"]["reason_code"], "recent_attempt_cooldown")
