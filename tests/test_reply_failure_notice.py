import unittest

from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.generation import MODEL_FAILURE_NOTICE, model_failure_notice
from companion_v2.execution.models import TaskError
from tests.test_host_application import HostFixture


class ReplyFailureNoticeTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_private_model_failure_sends_one_notice_without_replaying_call(self):
        async def unknown_result(_envelope):
            raise TaskError("model_usage_unknown", "provider result is unknown")

        self.model_behavior = unknown_result
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event("provider-failure")).unwrap()

        self.advance(app, 0)
        self.assertEqual((await self.done(app, batch))["state"], "complete")

        self.assertEqual(len(self.called), 1)
        self.assertEqual([message.text for message in self.sent], [MODEL_FAILURE_NOTICE])
        saved = app.messages.batch(batch)
        self.assertIsNone(saved["call_id"])
        self.assertEqual(saved["part_count"], 1)
        calls = app.runtime.read_all("SELECT state FROM model_calls WHERE task_id = ?",
                                     (saved["generation_task_id"],))
        self.assertEqual([row["state"] for row in calls], ["unknown"])

    async def test_a_failure_with_a_public_reason_says_what_to_do(self):
        async def timed_out(_envelope):
            raise TaskError("model_timeout", "provider did not answer in time")

        self.model_behavior = timed_out
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        batch = host.receive(self.event("provider-timeout")).unwrap()

        self.advance(app, 0)
        self.assertEqual((await self.done(app, batch))["state"], "complete")

        self.assertEqual(len(self.called), 1)
        texts = [message.text for message in self.sent]
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].startswith(MODEL_FAILURE_NOTICE))
        self.assertIn("超时", texts[0])

    def test_only_known_failures_add_a_public_reason(self):
        self.assertEqual(model_failure_notice("model_usage_unknown"), MODEL_FAILURE_NOTICE)
        self.assertIn("额度", model_failure_notice("model_budget_exhausted"))
        self.assertIn("没有可用的文本模型", model_failure_notice("model_provider_unavailable"))
        self.assertIn("截断", model_failure_notice("model_output_truncated"))
        self.assertIn("输入上限", model_failure_notice("host_model_limit_input"))


if __name__ == "__main__":
    unittest.main()
