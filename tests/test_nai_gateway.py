"""Image calls use the real gateway ledger without inventing token or money usage."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from companion_v2.execution.models import ProviderBinding, TaskError
from companion_v2.model_gateway.images import IMAGE_PROVIDERS
from tests.test_model_gateway import ModelFixture


class NAIGatewayTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def image_session(self):
        run = self.model_run()
        invocation = NS(context=run.task.spec.context, invocation_id=run.task.spec.request_ref,
            parent_task_id="original-parent", arguments_hash="synthetic-arguments",
            descriptor=NS(tool=NS(action="image.generate"),
                binding=ProviderBinding("image.generate", "image.native", "1", "native-generation")))
        self.ledger.policy = replace(self.ledger.policy, enabled=False)
        return self.gateway.image_session(run, invocation), run

    async def test_registered_image_backend_is_separate_from_text_providers(self):
        self.assertEqual(IMAGE_PROVIDERS["nai"].provider_id, "image.nai")
        self.assertEqual(IMAGE_PROVIDERS["nai"].operations, ("generate", "edit"))
        self.assertNotIn("image.nai", self.router.providers)

    async def test_success_is_recorded_once_with_unknown_actual_cost_and_no_prompt_body(self):
        session, run = self.image_session()
        fence = AsyncMock()

        async def generate(dispatch):
            await dispatch()
            return "synthetic-image"

        self.assertEqual(await session.execute("nai", "generate", generate, fence), "synthetic-image")
        fence.assert_awaited_once()
        row = self.call_rows()[0]
        self.assertEqual(row["state"], "generated")
        self.assertEqual(json.loads(row["provider_json"])["provider"], "image.nai")
        self.assertIsNone(row["actual_tokens"])
        self.assertIsNone(row["actual_cost"])
        observed = self.gateway.observe(run.task.spec.context, run.task.task_id).unwrap()
        self.assertEqual(observed["transport"], "native_image")
        self.assertEqual(observed["plan"]["cost_availability"], "unavailable")
        self.assertEqual(observed["calls"][0]["response_diagnostics"]["parse_state"], "validated")
        self.assertEqual(len(self.runtime.read_all("SELECT * FROM model_call_accounts", ())), 4)
        with self.assertRaisesRegex(TaskError, "inspect the original image call"):
            await session.execute("nai", "generate", generate, fence)
        fence.assert_awaited_once()

    async def test_hard_monetary_budget_refuses_an_unpriced_request_before_dispatch(self):
        session, _ = self.image_session()
        self.ledger.policy = replace(self.ledger.policy, enabled=True)
        generate, fence = AsyncMock(), AsyncMock()
        with self.assertRaises(TaskError) as caught:
            await session.execute("nai", "generate", generate, fence)
        self.assertEqual(caught.exception.code, "image_usage_unavailable")
        generate.assert_not_awaited()
        fence.assert_not_awaited()
        row = self.call_rows()[0]
        self.assertEqual((row["state"], row["actual_tokens"], row["actual_cost"]), ("not_started", 0, 0))
        diagnostic = self.runtime.read_one("SELECT summary_json FROM model_call_diagnostics", ())
        self.assertEqual(json.loads(diagnostic[0])["error_code"], "image_usage_unavailable")

    async def test_pre_dispatch_refusal_is_proven_not_started(self):
        session, _ = self.image_session()
        fence = AsyncMock()

        async def generate(dispatch):
            raise TaskError("nai_plugin_unavailable", "private message must not be persisted")

        with self.assertRaises(TaskError):
            await session.execute("nai", "generate", generate, fence)
        fence.assert_not_awaited()
        self.assertEqual(self.call_rows()[0]["state"], "not_started")
        code = self.runtime.read_one("SELECT code FROM model_call_failures", ())[0]
        self.assertEqual(code, "provider_recheck_failed")
        self.assertNotIn("private message", self.runtime.read_one(
            "SELECT summary_json FROM model_call_diagnostics", ())[0])

    async def test_post_dispatch_timeout_is_unknown_and_cannot_be_replayed(self):
        session, _ = self.image_session()
        fence = AsyncMock()

        async def generate(dispatch):
            await dispatch()
            raise TaskError("nai_generation_timed_out", "private upstream body")

        with self.assertRaises(TaskError):
            await session.execute("nai", "generate", generate, fence)
        row = self.call_rows()[0]
        self.assertEqual(row["state"], "unknown")
        self.assertIsNone(row["actual_cost"])
        self.assertEqual(self.runtime.read_one("SELECT code FROM model_call_failures", ())[0],
                         "provider_wait_timed_out")
        with self.assertRaises(TaskError):
            await session.execute("nai", "generate", generate, fence)
        fence.assert_awaited_once()

    async def test_cancellation_keeps_one_unknown_attempt_and_its_ledger_receipt(self):
        session, _ = self.image_session()
        entered = asyncio.Event()

        async def generate(dispatch):
            await dispatch()
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(session.execute("nai", "generate", generate, AsyncMock()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.call_rows()[0]["state"], "unknown")
        self.assertEqual(self.runtime.read_one("SELECT code FROM model_call_failures", ())[0],
                         "provider_wait_interrupted")

    async def test_closed_or_unregistered_sessions_cannot_dispatch(self):
        session, _ = self.image_session()
        generate, fence = AsyncMock(), AsyncMock()
        with self.assertRaises(TaskError):
            await session.execute("unregistered", "generate", generate, fence)
        with self.assertRaises(TaskError):
            await session.execute("nai", "mask_edit", generate, fence)
        session.close()
        with self.assertRaises(TaskError):
            await session.execute("nai", "generate", generate, fence)
        self.assertFalse(self.call_rows())
        generate.assert_not_awaited()
