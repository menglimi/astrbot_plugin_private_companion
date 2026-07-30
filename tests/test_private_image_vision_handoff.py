# -*- coding: utf-8 -*-
import asyncio
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import astrbot_plugin_private_companion.private_image as private_image_module
from astrbot_plugin_private_companion.private_image import PrivateImageMixin


PLACEHOLDER = "用户刚刚先单独发送了一张图片,可能马上会补充说明。"


class FakeEvent:
    def __init__(
        self,
        sender_id: str = "10001",
        umo: str = "default:FriendMessage:10001",
    ) -> None:
        self.sender_id = sender_id
        self.unified_msg_origin = umo
        self.message_str = "[图片]"

    def get_sender_id(self) -> str:
        return self.sender_id


class VisionHandoffHarness(PrivateImageMixin):
    def __init__(self) -> None:
        self._semantic_message_buffers = {}
        self._private_image_vision_handoffs = {}
        self.delayed_calls = []
        self.delayed_started = asyncio.Event()
        self.delayed_release = asyncio.Event()

    def _semantic_buffer_key(self, scope: str, user_id: str):
        return (scope, user_id)

    def _message_debounce_seconds(self, kind: str) -> float:
        return 0.0

    def _private_image_vision_text_limit(self, image_count: int) -> int:
        return 1400

    def _private_image_vision_wait_budget_seconds(self) -> float:
        return 1.0

    async def _send_delayed_private_image_only_event(self, event, user_id, buffer) -> None:
        self.delayed_calls.append((event, user_id, buffer))
        self.delayed_started.set()
        await self.delayed_release.wait()


class TtsEventHandoffHarness(PrivateImageMixin):
    @staticmethod
    def _restore_protected_tts_blocks(text, event):
        mapping = getattr(event, "_private_companion_tts_block_tokens", {})
        restored = str(text or "")
        for token, block in mapping.items():
            restored = restored.replace(f"[[PCTTS:{token}]]", block)
        return restored


class PrivateImageVisionHandoffTests(unittest.IsolatedAsyncioTestCase):
    def test_framework_tts_tokens_are_restored_from_the_creating_event(self):
        harness = TtsEventHandoffHarness()
        framework_event = SimpleNamespace(
            _private_companion_tts_block_tokens={"token-1": "<tts>语音正文</tts>"}
        )
        original_event = SimpleNamespace()
        source = "[[PCTTS:token-1]]可见正文"

        self.assertEqual(
            "<tts>语音正文</tts>可见正文",
            harness._restore_private_image_framework_tts_reply(source, framework_event),
        )
        self.assertEqual(
            source,
            harness._restore_private_image_framework_tts_reply(source, original_event),
        )

    def test_framework_tts_restore_runs_before_reply_quality_checks(self):
        source = Path(private_image_module.__file__).read_text(encoding="utf-8")
        restore_call = source.index("reply = self._restore_private_image_framework_tts_reply(")
        internal_error_check = source.index(
            "if reply and self._private_image_reply_is_internal_error(reply):",
            restore_call,
        )
        self.assertLess(restore_call, internal_error_check)

    async def test_live_context_claim_suppresses_delayed_image_dispatch(self):
        harness = VisionHandoffHarness()
        event = FakeEvent()
        key = harness._semantic_buffer_key("private:10001", "10001")
        vision_task = asyncio.create_task(asyncio.sleep(0, result="图片里是一段报错日志"))
        harness._semantic_message_buffers[key] = {
            "first_ts": time.time() - 1,
            "updated_ts": time.time() - 1,
            "messages": [{"ts": time.time() - 1, "text": PLACEHOLDER}],
            "images": ["image.png"],
            "image_mode": "caption",
            "vision_task": vision_task,
            "original_event": event,
        }

        context = harness._take_buffered_private_image_context_for_event(FakeEvent())

        self.assertEqual(context["images"], ["image.png"])
        self.assertIs(context["vision_task"], vision_task)
        self.assertIn("vision_context_claimed_ts", harness._semantic_message_buffers[key])
        harness.delayed_release.set()
        with patch.object(private_image_module, "AstrMessageEvent", FakeEvent):
            await harness._finalize_private_image_buffer_after_wait(key, "10001", time.time() - 1)

        self.assertEqual(harness.delayed_calls, [])
        self.assertNotIn(key, harness._semantic_message_buffers)
        self.assertEqual(harness._private_image_vision_handoffs, {})

    async def test_finalizer_hands_same_pending_vision_task_to_later_text(self):
        harness = VisionHandoffHarness()
        event = FakeEvent()
        vision_release = asyncio.Event()

        async def caption_task():
            await vision_release.wait()
            return "截图里显示一段报错日志"

        vision_task = asyncio.create_task(caption_task())
        key = harness._semantic_buffer_key("private:10001", "10001")
        original_buffer = {
            "first_ts": time.time() - 1,
            "updated_ts": time.time() - 1,
            "messages": [{"ts": time.time() - 1, "text": PLACEHOLDER}],
            "images": ["image.png"],
            "image_mode": "caption",
            "vision_task": vision_task,
            "original_event": event,
        }
        harness._semantic_message_buffers[key] = original_buffer

        with patch.object(private_image_module, "AstrMessageEvent", FakeEvent):
            finalize_task = asyncio.create_task(
                harness._finalize_private_image_buffer_after_wait(
                    key, "10001", time.time() - 1
                )
            )
            await asyncio.wait_for(harness.delayed_started.wait(), timeout=1)

            self.assertNotIn(key, harness._semantic_message_buffers)
            context = harness._take_buffered_private_image_context_for_event(
                FakeEvent()
            )

            self.assertEqual(context["images"], ["image.png"])
            self.assertEqual(context["image_mode"], "caption")
            self.assertIs(context["vision_task"], vision_task)
            self.assertTrue(context["from_handoff"])
            self.assertEqual(len(harness.delayed_calls), 1)
            delayed_buffer = harness.delayed_calls[0][2]
            self.assertIsNot(delayed_buffer, original_buffer)
            self.assertIsNot(delayed_buffer["images"], original_buffer["images"])

            vision_release.set()
            self.assertEqual(await context["vision_task"], "截图里显示一段报错日志")
            harness.delayed_release.set()
            await asyncio.wait_for(finalize_task, timeout=1)

    async def test_completed_handoff_is_consumed_once(self):
        harness = VisionHandoffHarness()
        event = FakeEvent()

        async def caption_task():
            return "图片里是一张日程截图"

        vision_task = asyncio.create_task(caption_task())
        await vision_task
        key = harness._semantic_buffer_key("private:10001", "10001")
        harness._remember_private_image_vision_handoff(
            key,
            event,
            {
                "images": ["schedule.png"],
                "image_mode": "caption",
                "vision_task": vision_task,
            },
        )

        first = harness._take_buffered_private_image_context_for_event(event)
        second = harness._take_buffered_private_image_context_for_event(event)

        self.assertEqual(first["vision_text"], "图片里是一张日程截图")
        self.assertEqual(first["images"], ["schedule.png"])
        self.assertEqual(second, {})

    async def test_expired_handoff_is_ignored_and_cleaned(self):
        harness = VisionHandoffHarness()
        event = FakeEvent()
        key = harness._semantic_buffer_key("private:10001", "10001")
        harness._private_image_vision_handoffs[key] = {
            "created_ts": time.time() - 60,
            "expires_ts": time.time() - 1,
            "session": event.unified_msg_origin,
            "images": ["old.png"],
            "image_mode": "caption",
        }

        context = harness._take_buffered_private_image_context_for_event(event)

        self.assertEqual(context, {})
        self.assertNotIn(key, harness._private_image_vision_handoffs)

    async def test_handoff_is_isolated_by_sender_and_session(self):
        harness = VisionHandoffHarness()
        original_event = FakeEvent(umo="bot-a:FriendMessage:10001")
        key = harness._semantic_buffer_key("private:10001", "10001")
        harness._remember_private_image_vision_handoff(
            key,
            original_event,
            {
                "images": ["private.png"],
                "image_mode": "caption",
                "vision_text": "只属于 bot-a 会话的图片",
            },
        )

        wrong_session = harness._take_buffered_private_image_context_for_event(
            FakeEvent(umo="bot-b:FriendMessage:10001")
        )
        wrong_user = harness._take_buffered_private_image_context_for_event(
            FakeEvent(sender_id="20002", umo="bot-a:FriendMessage:20002")
        )
        right_session = harness._take_buffered_private_image_context_for_event(
            original_event
        )

        self.assertEqual(wrong_session, {})
        self.assertEqual(wrong_user, {})
        self.assertEqual(right_session["vision_text"], "只属于 bot-a 会话的图片")
        self.assertEqual(right_session["images"], ["private.png"])


if __name__ == "__main__":
    unittest.main()
