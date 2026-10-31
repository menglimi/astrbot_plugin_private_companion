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
