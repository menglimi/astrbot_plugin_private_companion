"""Visual reaction review must use a provider that can read the attachment."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from astrbot_host.semantic_models import NativeSemanticModels
from companion_v2.execution.models import TaskError


def provider(identifier, modalities):
    return NS(provider_config={"id": identifier, "modalities": modalities},
              text_chat=AsyncMock(return_value=NS(completion_text='{"suitable":true}')))


class NativeSemanticAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def models(self, providers, default=""):
        return NativeSemanticModels(NS(get_provider_by_id=lambda identifier: next(
            (item for item in providers if item.provider_config["id"] == identifier), None),
            get_all_providers=lambda: providers), default_analysis=default)

    async def test_text_only_analysis_route_falls_back_to_default_vision_for_images(self):
        text = provider("text", ["text"])
        first = provider("first-vision", ["text", "image"])
        preferred = provider("default-vision", ["text", "image"])
        models = self.models([text, first, preferred], "default-vision")
        result = await models.analyze("text", "review sticker", ("data:image/png;base64,AA==",))
        self.assertTrue(result["suitable"])
        text.text_chat.assert_not_awaited()
        first.text_chat.assert_not_awaited()
        self.assertEqual(preferred.text_chat.await_args.kwargs["image_urls"], ["data:image/png;base64,AA=="])

    async def test_explicit_vision_and_text_analysis_keep_the_selected_provider(self):
        text = provider("text", ["text"])
        vision = provider("vision", ["text", "image"])
        models = self.models([text, vision])
        await models.analyze("vision", "review sticker", ("data:image/png;base64,AA==",))
        await models.analyze("text", "explain slang")
        vision.text_chat.assert_awaited_once()
        text.text_chat.assert_awaited_once()
        self.assertNotIn("image_urls", text.text_chat.await_args.kwargs)

    async def test_image_review_reports_missing_vision_without_sending_to_text_model(self):
        text = provider("text", ["text"])
        models = self.models([text])
        with self.assertRaises(TaskError) as caught:
            await models.analyze("text", "review sticker", ("data:image/png;base64,AA==",))
        self.assertEqual(caught.exception.code, "semantic_vision_provider_unavailable")
        text.text_chat.assert_not_awaited()

    async def test_analyze_forwards_custom_max_tokens_and_defaults_to_1400(self):
        vision = provider("vision", ["text", "image"])
        models = self.models([vision])
        await models.analyze("vision", "review sticker", ("data:image/png;base64,AA==",))
        self.assertEqual(vision.text_chat.await_args.kwargs["max_tokens"], 1400)
        await models.analyze("vision", "review sticker", ("data:image/png;base64,AA==",), max_tokens=256)
        self.assertEqual(vision.text_chat.await_args.kwargs["max_tokens"], 256)

    async def test_analyze_uses_fallback_provider_when_primary_times_out_or_fails(self):
        primary = provider("primary-vision", ["text", "image"])
        primary.text_chat = AsyncMock(side_effect=TimeoutError("upstream timed out"))
        fallback = provider("fallback-vision", ["text", "image"])
        fallback.text_chat = AsyncMock(return_value=NS(completion_text='{"fit":true,"reason":"贴合"}'))
        models = self.models([primary, fallback])
        result = await models.analyze(
            "primary-vision", "review sticker", ("data:image/png;base64,AA==",),
            max_tokens=256, fallback_identifier="fallback-vision",
        )
        self.assertEqual(result, {"fit": True, "reason": "贴合"})
        primary.text_chat.assert_awaited_once()
        fallback.text_chat.assert_awaited_once()
        self.assertEqual(fallback.text_chat.await_args.kwargs["max_tokens"], 256)

    async def test_analyze_does_not_call_fallback_when_primary_succeeds(self):
        primary = provider("primary-vision", ["text", "image"])
        fallback = provider("fallback-vision", ["text", "image"])
        models = self.models([primary, fallback])
        result = await models.analyze(
            "primary-vision", "review sticker", ("data:image/png;base64,AA==",),
            max_tokens=256, fallback_identifier="fallback-vision",
        )
        self.assertTrue(result["suitable"])
        primary.text_chat.assert_awaited_once()
        fallback.text_chat.assert_not_awaited()

    async def test_analyze_deduplicates_empty_primary_matching_default_fallback(self):
        default_vision = provider("default-vision", ["text", "image"])
        default_vision.text_chat = AsyncMock(side_effect=TimeoutError("upstream timed out"))
        models = self.models([default_vision], default="default-vision")
        with self.assertRaises(TimeoutError):
            await models.analyze(
                "", "review sticker", ("data:image/png;base64,AA==",),
                max_tokens=256, fallback_identifier="default-vision", timeout_seconds=5,
            )
        default_vision.text_chat.assert_awaited_once()
