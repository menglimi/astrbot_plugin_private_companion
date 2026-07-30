import asyncio
import time
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.private_image import PrivateImageMixin


class FakeEvent:
    def __init__(self, message: str = "看看这张图") -> None:
        self.message_str = message
        self.unified_msg_origin = "aiocqhttp:FriendMessage:10001"
        self._extras = {}

    def is_private_chat(self) -> bool:
        return True

    def get_sender_id(self) -> str:
        return "10001"

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


class FakeRouter:
    def __init__(self) -> None:
        self.calls = []

    def route_companion_image_caption(self, event, caption_text: str) -> bool:
        self.calls.append((event, caption_text))
        event.set_extra("selected_provider", "vision-route")
        return True


class FakeContext:
    def __init__(self, router: FakeRouter) -> None:
        self.metadata = SimpleNamespace(star_cls=router)

    def get_registered_star(self, name: str):
        if name == "astrbot_plugin_keyword_model_router":
            return self.metadata
        return None


class ImageBridgeHarness(PrivateImageMixin):
    def __init__(self) -> None:
        self.enabled = True
        self.data = {"users": {"10001": {"enabled": True}}}
        self._semantic_message_buffers = {}

    def _canonical_private_user_id(self, user_id: str) -> str:
        return user_id

    def _is_target_private_user(self, user_id: str, user: dict) -> bool:
        return user_id == "10001"

    def _feature_enabled_or_temp_unlocked(self, name: str) -> bool:
        return True

    def _semantic_buffer_key(self, scope: str, user_id: str):
        return (scope, user_id)

    def _message_debounce_seconds(self, kind: str) -> float:
        return 2.0

    def _private_image_vision_text_limit(self, image_count: int) -> int:
        return 1400

    def _private_image_vision_wait_budget_seconds(self) -> float:
        return 1.0

    async def _find_reply_image_sources_for_event(self, event):
        return []


class KeywordModelRouterBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepares_caption_from_buffered_vision_task(self):
        harness = ImageBridgeHarness()
        event = FakeEvent()

        async def caption_task():
            await asyncio.sleep(0)
            return "截图里出现 Python 报错"

        key = harness._semantic_buffer_key("private:10001", "10001")
        harness._semantic_message_buffers[key] = {
            "updated_ts": time.time(),
            "images": ["image.png"],
            "vision_task": asyncio.create_task(caption_task()),
        }

        caption = await harness.prepare_keyword_model_router_image_caption(event)

        self.assertEqual(caption, "截图里出现 Python 报错")
        self.assertEqual(
            event.private_companion_image_caption_route_text,
            "截图里出现 Python 报错",
        )
        self.assertEqual(
            harness._semantic_message_buffers[key]["vision_text"],
            "截图里出现 Python 报错",
        )

    async def test_manual_agent_bridge_calls_keyword_router(self):
        harness = ImageBridgeHarness()
        router = FakeRouter()
        harness.context = FakeContext(router)
        event = FakeEvent("[图片]")

        routed = harness._route_private_image_caption_with_keyword_router(
            event, "画面是一段代码"
        )

        self.assertTrue(routed)
        self.assertEqual(event.get_extra("selected_provider"), "vision-route")
        self.assertEqual(router.calls[0][1], "画面是一段代码")


if __name__ == "__main__":
    unittest.main()
