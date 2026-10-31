"""Exercise the review worker through real task, model and delivery owners."""

import asyncio
import json
from types import MappingProxyType
import unittest

from companion_v2.conversation.foreground import ModelDraft
from companion_v2.conversation.media.review import DEFAULTS
from companion_v2.execution.models import ProviderBinding
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, LocalProvider, ProviderResponse
from companion_v2.conversation.reply_settings import (ReplyConfigurationOwner,
    configuration_fields as reply_fields, target_for)
from tests.group_host_support import group_config
from tests.group_image_delivery_support import GroupImageDeliveryFixture
from tests.group_media_support import sdk_image
from tests.image_delivery_support import attach_originals
from tests.native_adapter_support import completion
from tests.staging_bootstrap_support import wait_for_batch


class GroupImageReviewExecutionTests(GroupImageDeliveryFixture, unittest.IsolatedAsyncioTestCase):
    batch_timeout_seconds = 40

    async def asyncSetUp(self):
        self.controller = self.make_controller(group_config(group_delivery_mode="onebot_group"))
        await self.start_host()
        (await self.controller.authorize_group_delivery("group-a")).unwrap()
        self.settings = {**DEFAULTS, "enable_group_nsfw_private_fallback": True}
        self.app.image_review._settings_reader = lambda: self.settings
        self.review_calls = []

        async def respond(payload, state):
            if "固定 JSON" in json.dumps(payload, ensure_ascii=False):
                self.review_calls.append("native-model")
                return completion('{"label":"safe","confidence":0.99}')
            return await attach_originals(payload, state)

        self.native.provider.client.state.behavior = respond

    def add_reviewer(self, name, *, label="safe", unknown=False, delay=0):
        async def generate(envelope):
            self.review_calls.append(name)
            self.assertTrue(envelope.original.images)
            self.assertTrue(envelope.original.context.evidence_sources)
            if delay:
                await asyncio.sleep(delay)
            if unknown:
                return ProviderResponse("unknown", failure=ModelFailure.TIMEOUT)
            return ProviderResponse("generated", ModelDraft(json.dumps({"label": label,
                "confidence": 0.99}), ()), Amount(90, 0), name + ":receipt")

        provider = LocalProvider(ProviderBinding("model.text", name, "1", name),
            ("text", "image"), 1024, lambda envelope: Amount(200, 0), generate)
        router = self.app.gateway.router
        router.providers = MappingProxyType({**router.providers, name: provider})

    async def reviewed(self):
        result = await self.controller.receive_claimed(self.media_event([sdk_image()]))
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch, timeout_seconds=self.batch_timeout_seconds)
        rows = [self.app.image_review.row_for_part(part["part_id"]) for part in self.parts(batch)]
        row = next(row for row in rows if row is not None)
        task = self.app.runtime.read_one("SELECT * FROM execution_tasks WHERE task_id=?", (row["review_key"],))
        self.assertEqual(task["state"], "succeeded", {**dict(task), "review_calls": self.review_calls})
        receipt = json.loads(task["record_json"])
        self.assertEqual((receipt["effect"], receipt["operation_id"], receipt["result_ref"], receipt["receipt_ref"]),
                         ("committed", row["review_key"], row["review_key"], row["review_key"]))
        return json.loads(row["review_json"]), row["review_key"]

    async def test_default_task_route_reaches_the_vision_model_and_group(self):
        verdict, key = await self.reviewed()
        self.assertEqual((verdict["state"], verdict["label"]), ("reviewed", "safe"))
        self.assertEqual(self.review_calls, ["native-model"])
        self.assertEqual(verdict["provider_ids"], ["native-model"])
        self.assertTrue(self.sends())

    async def test_two_selected_models_get_distinct_recorded_rounds(self):
        self.add_reviewer("review-first")
        self.add_reviewer("review-second")
        self.app.image_review.providers = lambda context: ("review-first", "review-second")
        self.settings["group_nsfw_image_review_mode"] = "dual"
        verdict, key = await self.reviewed()
        self.assertEqual(self.review_calls, ["review-first", "review-second"])
        self.assertEqual(verdict["state"], "reviewed")
        self.assertEqual(verdict["provider_ids"], self.review_calls)
        rounds = self.app.runtime.read_all("SELECT round_no FROM model_rounds WHERE task_id=? ORDER BY round_no", (key,))
        self.assertEqual([row[0] for row in rounds], [0, 1])

    async def test_a_missing_second_verdict_cannot_approve_a_dual_review(self):
        self.add_reviewer("review-first")
        self.add_reviewer("review-second", label="uncertain")
        self.app.image_review.providers = lambda context: ("review-first", "review-second")
        self.settings["group_nsfw_image_review_mode"] = "dual"
        verdict, _ = await self.reviewed()
        self.assertEqual(verdict["state"], "unavailable")

    async def test_unknown_first_result_never_dispatches_the_second_model(self):
        self.add_reviewer("review-first", unknown=True)
        self.add_reviewer("review-second")
        self.app.image_review.providers = lambda context: ("review-first", "review-second")
        self.settings["group_nsfw_image_review_mode"] = "dual"
        verdict, _ = await self.reviewed()
        self.assertEqual(verdict["state"], "unavailable")
        self.assertEqual(self.review_calls, ["review-first"])

    async def test_configured_timeout_is_consumed_and_usage_stays_unknown(self):
        self.add_reviewer("review-slow", delay=10)
        self.app.image_review.providers = lambda context: ("review-slow", "")
        self.settings["group_nsfw_image_review_timeout_seconds"] = 3
        verdict, key = await self.reviewed()
        self.assertEqual(verdict["state"], "unavailable")
        self.assertIn("3 秒", verdict["reason"])
        calls = self.app.runtime.read_all("SELECT state, actual_tokens FROM model_calls WHERE task_id=?", (key,))
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["actual_tokens"])

    async def test_image_candidate_probability_does_not_depend_on_image_presence_alone(self):
        anchor = self.media_event([sdk_image()], identifier="image-wakeup-settings")
        context = (await self.controller.observe_group_early(anchor)).unwrap()
        common = self.controller.settings.common
        fields = reply_fields(common.segmentation, common.streaming, common.policy.part_gap_seconds)
        owner = ReplyConfigurationOwner(self.app.runtime)
        target = target_for(context)

        def configure(probability):
            owner.write(target, fields, changes={
                "conversation.presentation.group_wakeup_enabled": True,
                "conversation.presentation.group_wakeup_image_enabled": True,
                "conversation.presentation.group_wakeup_image_weight": probability},
                reset=(), expected_revision=owner.read(target, fields).revision, observed_at=self.clock.now())

        configure(0)
        blocked = (await self.controller.group_reply_candidate(self.media_event(
            [sdk_image()], identifier="image-probability-zero", text="图片"))).unwrap()
        self.assertNotEqual(blocked["reason"], "image_interest")
        configure(1)
        admitted = (await self.controller.group_reply_candidate(self.media_event(
            [sdk_image()], identifier="image-probability-one", text="图片"))).unwrap()
        self.assertEqual(admitted["reason"], "image_interest")
        configure(0.5)
        samples = [(await self.controller.group_reply_candidate(self.media_event(
            [sdk_image()], identifier=f"image-probability-sample-{index}", text="图片"))).unwrap()
            for index in range(8)]
        reasons = [sample["reason"] for sample in samples]
        self.assertIn("image_interest", reasons)
        self.assertTrue(any(reason != "image_interest" for reason in reasons))


if __name__ == "__main__":
    unittest.main()
