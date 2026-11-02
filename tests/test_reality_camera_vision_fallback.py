"""The camera falls back when the configured vision endpoint is closed."""

import asyncio
import unittest

from astrbot_host.native_extensions.reality.reality_touch_camera import RealityTouchCameraMixin

COMPLETION = (
    '{"scene_description":"一张书桌和亮着的显示器","purpose_answer":"在桌边用电脑",'
    '"visible_evidence":["书桌","显示器"],"uncertainty":"",'
    '"answer_status":"answered","presence":"present","activity":"at_desk",'
    '"activity_detail":"坐在桌前","interruptibility":"medium","brightness":"normal",'
    '"food_visibility":"uncertain","visible_food":"","confidence":0.8}'
)


class Provider:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    async def text_chat(self, prompt, image_urls, max_tokens):
        self.calls += 1
        if self.fail:
            raise RuntimeError("InvalidEndpoint.ClosedEndpoint")
        return type("Result", (), {"completion_text": COMPLETION})()


class Context:
    def __init__(self, providers):
        self.providers = providers

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)


class CameraHost(RealityTouchCameraMixin):
    plugin_vision_provider_id = "closed"
    reality_touch_camera_vision_fallback_ids = ("working",)
    reality_touch_camera_analysis_timeout_seconds = 5
    context = Context({"closed": Provider(fail=True), "working": Provider()})

    def _provider_supports_image(self, provider):
        return True

    def _record_llm_usage(self, **_kwargs):
        return None


class CameraVisionFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_primary_endpoint_falls_back_to_an_image_provider(self):
        host = CameraHost()
        observation = await host._analyze_reality_touch_camera_frame(
            {"jpeg_bytes": b"frame", "brightness": "normal", "width": 1280, "height": 720},
            "用户让我看看他现在在做什么",
        )
        self.assertTrue(observation["analyzed"])
        self.assertTrue(observation["answer_available"])
        self.assertEqual(observation["activity"], "at_desk")
        self.assertEqual(host.context.providers["closed"].calls, 1)
        self.assertEqual(host.context.providers["working"].calls, 1)


if __name__ == "__main__":
    unittest.main()
