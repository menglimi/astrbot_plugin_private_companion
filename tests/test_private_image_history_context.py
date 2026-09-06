# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.private_image import PrivateImageMixin


class _ConversationManager:
    def __init__(self, history):
        self.history = list(history)

    async def get_curr_conversation_id(self, _umo: str) -> str:
        return "conversation-1"

    async def get_conversation(self, _umo: str, _conversation_id: str):
        return SimpleNamespace(history=json.dumps(self.history, ensure_ascii=False))

    async def update_conversation(self, _umo: str, _conversation_id: str, *, history=None, **_kwargs):
        self.history = list(history or [])


class _ImageHistoryHarness(PrivateImageMixin):
    def __init__(self, history):
        self.context = SimpleNamespace(conversation_manager=_ConversationManager(history))

    @staticmethod
    def _private_image_vision_text_limit(_image_count: int = 1) -> int:
        return 1400


class PrivateImageHistoryContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_conversation_context_falls_back_to_raw_history(self) -> None:
        harness = _ImageHistoryHarness(
            [
                {"role": "user", "content": "前一句"},
                {"role": "assistant", "content": [{"type": "text", "text": "上一轮回复"}]},
            ]
        )

        context = await harness._private_image_recent_conversation_context(
            "default:FriendMessage:10001",
            limit=2,
        )

        self.assertIn("前一句", context)
        self.assertIn("上一轮回复", context)

    async def test_vision_summary_is_persisted_as_user_text_and_is_idempotent(self) -> None:
        harness = _ImageHistoryHarness(
            [{"role": "user", "content": "看这张图 [图片]"}]
        )
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            message_str="看这张图 [图片]",
            private_companion_delayed_image_vision_text="图片里是一张展览海报",
        )

        self.assertTrue(await harness._persist_private_image_vision_summary_to_history(event))
        self.assertFalse(await harness._persist_private_image_vision_summary_to_history(event))
        history = harness.context.conversation_manager.history
        self.assertEqual(1, len(history))
        self.assertIn("[图片内容：图片里是一张展览海报]", history[0]["content"])

    async def test_vision_summary_marks_live_run_context_before_core_save(self) -> None:
        harness = _ImageHistoryHarness([])
        user_message = SimpleNamespace(
            role="user",
            content=[SimpleNamespace(type="text", text="看这张图")],
        )
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            message_str="看这张图",
            private_companion_delayed_image_vision_text="图片里是一张展览海报",
            _private_companion_run_context=SimpleNamespace(messages=[user_message]),
        )

        self.assertTrue(await harness._persist_private_image_vision_summary_to_history(event))
        self.assertIn("[图片内容：图片里是一张展览海报]", user_message.content[0].text)
        self.assertEqual([], harness.context.conversation_manager.history)


if __name__ == "__main__":
    unittest.main()
