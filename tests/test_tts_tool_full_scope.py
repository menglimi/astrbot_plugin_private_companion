# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api.message_components import Plain
from astrbot.core.provider.entities import LLMResponse

from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin
from astrbot_plugin_private_companion.tts_tool_sanitizer import TtsToolSanitizerMixin
from astrbot_plugin_private_companion.llm_tool_actions import (
    LlmToolActionsMixin,
    PHOTO_TOOL_SILENT_SENTINEL,
)
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _ToolHarness(TtsToolSanitizerMixin, TtsEnhancementMixin):
    enable_tts_enhancement = True
    tts_generation_mode = "fast_tag"
    tts_conversion_scope = "full"
    tts_voice_language = "ja"
    tts_delivery_mode = "voice_and_text"
    tts_foreign_text_mode = "translation"


class _ResponseHarness(TtsToolSanitizerMixin):
    enabled = True
    normalize_tts_enhancement_response = PrivateCompanionPlugin.normalize_tts_enhancement_response
    _photo_tool_followup_is_redundant = staticmethod(LlmToolActionsMixin._photo_tool_followup_is_redundant)
    _photo_tool_followup_chain_has_visible_content = staticmethod(
        PrivateCompanionPlugin._photo_tool_followup_chain_has_visible_content
    )
    suppress_empty_photo_tool_followup_before_send = (
        PrivateCompanionPlugin.suppress_empty_photo_tool_followup_before_send
    )
    attach_reaction_expression_image_before_send = (
        PrivateCompanionPlugin.attach_reaction_expression_image_before_send
    )

    @staticmethod
    def _build_result_from_chain(chain):
        return SimpleNamespace(chain=list(chain), stop_event=lambda: None)

    @staticmethod
    async def _recover_plaintext_photo_tool_call(_event, _resp, text):
        return text, {}

    @staticmethod
    def _guard_unread_creative_work_response(_event, text):
        return text

    @staticmethod
    def _proactive_only_blocks_passive_event(_event, _feature):
        return False

    @staticmethod
    async def protect_tts_enhancement_response_blocks(_event, _resp):
        return None


class TtsToolFullScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_tool_empty_placeholder_chain_is_stopped_before_send(self):
        harness = _ResponseHarness()

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            _private_companion_photo_tool_sent = True

            def __init__(self):
                self.result = SimpleNamespace(chain=[Plain("\u200b\ue000")])
                self.stopped = False

            def get_result(self):
                return self.result

            def set_result(self, result):
                self.result = result

            def stop_event(self):
                self.stopped = True

        event = Event()
        await harness.suppress_empty_photo_tool_followup_before_send(event)

        self.assertTrue(event.stopped)
        self.assertEqual(event.result.chain, [])

    async def test_photo_tool_real_followup_text_is_suppressed_after_delivery(self):
        harness = _ResponseHarness()

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            _private_companion_photo_tool_sent = True

            def __init__(self):
                self.result = SimpleNamespace(chain=[Plain("补充说明：参考图没有成功载入。")])
                self.stopped = False

            def get_result(self):
                return self.result

            def set_result(self, result):
                self.result = result

            def stop_event(self):
                self.stopped = True

        event = Event()
        await harness.suppress_empty_photo_tool_followup_before_send(event)

        self.assertTrue(event.stopped)
        self.assertEqual(event.result.chain, [])

    async def test_photo_delivery_discards_nonempty_llm_followup_and_reaction_state(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            _private_companion_photo_tool_sent=True,
            _private_companion_reaction_expression_intent={"emotion": "开心"},
            _private_companion_deferred_reaction_tts={"text": "看这里"},
        )
        resp = LLMResponse(
            role="assistant",
            completion_text="比折大人你看～我刚刚画的小星星。",
            tools_call_name=[],
            tools_call_args=[],
        )

        await harness.normalize_tts_enhancement_response(event, resp)

        self.assertEqual(resp.completion_text, "")
        self.assertIsNone(resp.result_chain)
        self.assertFalse(hasattr(event, "_private_companion_reaction_expression_intent"))

    async def test_real_photo_marker_skips_reaction_attachment(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            _private_companion_skip_reaction_expression=True,
            _private_companion_reaction_expression_intent={"emotion": "开心"},
        )

        await harness.attach_reaction_expression_image_before_send(event)

        self.assertFalse(hasattr(event, "_private_companion_reaction_expression_intent"))

    async def test_photo_silent_sentinel_is_removed_after_confirmed_delivery(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            _private_companion_photo_tool_sent=True,
            _private_companion_photo_tool_sent_caption="图片说明",
        )
        resp = LLMResponse(
            role="assistant",
            completion_text=PHOTO_TOOL_SILENT_SENTINEL,
            tools_call_name=[],
            tools_call_args=[],
        )

        await harness.normalize_tts_enhancement_response(event, resp)

        self.assertEqual(resp.completion_text, "")
        self.assertIsNone(resp.result_chain)

    async def test_photo_silent_sentinel_is_not_removed_without_delivery_marker(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        resp = LLMResponse(
            role="assistant",
            completion_text=PHOTO_TOOL_SILENT_SENTINEL,
            tools_call_name=[],
            tools_call_args=[],
        )

        await harness.normalize_tts_enhancement_response(event, resp)

        self.assertEqual(resp.completion_text, PHOTO_TOOL_SILENT_SENTINEL)

    async def test_tool_fast_tag_uses_complete_visible_reply(self):
        harness = _ToolHarness()
        harness._process_tts_tags = AsyncMock(return_value=[Plain("processed")])
        text = "<tts>最初の一文。</tts>第一句。后面的正文也必须朗读。"
        fallback = "第一句。后面的正文也必须朗读。"

        result = await harness._process_tool_plain_tts_components(
            text,
            SimpleNamespace(unified_msg_origin="test-session"),
            fallback_plain=fallback,
        )

        call = harness._process_tts_tags.await_args
        self.assertEqual(f"<tts>{fallback}</tts>", call.args[0])
        self.assertEqual(fallback, call.kwargs["fallback_plain"])
        self.assertEqual("processed", result[0].text)

    async def test_same_session_plain_tool_send_is_deferred_without_platform_send(self):
        harness = _ToolHarness()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        platform_send = AsyncMock()
        context = SimpleNamespace(
            context=SimpleNamespace(
                event=event,
                context=SimpleNamespace(send_message=platform_send),
            )
        )
        kwargs = {"messages": [{"type": "plain", "text": "只发一次。"}]}

        result = await harness._send_message_to_user_tool_with_tts_processing(
            SimpleNamespace(), context, kwargs
        )

        self.assertIn("deferred", result)
        platform_send.assert_not_awaited()
        self.assertEqual(event._private_companion_same_session_tool_text, "只发一次。")

    async def test_tool_call_intermediate_text_is_hidden_and_restored_once_as_final(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        tool_resp = LLMResponse(
            role="tool",
            completion_text="这句不能作为中间回复发送。",
            tools_call_name=["send_message_to_user"],
            tools_call_args=[
                {"messages": [{"type": "plain", "text": "真正只发这一句。"}]}
            ],
        )

        await harness.normalize_tts_enhancement_response(event, tool_resp)

        self.assertEqual(tool_resp.completion_text, "")
        self.assertEqual(event._private_companion_same_session_tool_text, "真正只发这一句。")

        final_resp = LLMResponse(
            role="assistant",
            completion_text="模型又重复了一次。",
            tools_call_name=[],
            tools_call_args=[],
        )
        await harness.normalize_tts_enhancement_response(event, final_resp)

        self.assertEqual(final_resp.completion_text, "真正只发这一句。")
        self.assertTrue(event._private_companion_same_session_tool_finalized)

    async def test_reaction_tool_intermediate_text_is_hidden_until_tool_result(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        tool_resp = LLMResponse(
            role="assistant",
            completion_text="这句不能在表情工具执行前先发送。（发送了一张表情包）",
            tools_call_name=["pc_find_reaction_image"],
            tools_call_args=[
                {
                    "query": "温柔回应",
                    "caption": "这句只能作为图片正文或最终纯文字回复。",
                    "send": True,
                }
            ],
        )

        await harness.normalize_tts_enhancement_response(event, tool_resp)

        self.assertEqual(tool_resp.completion_text, "")
        self.assertIsNone(tool_resp.result_chain)

    async def test_unrelated_tool_intermediate_text_keeps_existing_behavior(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        tool_resp = LLMResponse(
            role="assistant",
            completion_text="普通工具的已有中间说明。",
            tools_call_name=["pc_manage_memo"],
            tools_call_args=[{"action": "list"}],
        )

        await harness.normalize_tts_enhancement_response(event, tool_resp)

        self.assertEqual(tool_resp.completion_text, "普通工具的已有中间说明。")

    async def test_cross_session_tool_send_is_not_deferred(self):
        harness = _ToolHarness()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:10001")
        context = SimpleNamespace(context=SimpleNamespace(event=event))
        kwargs = {
            "session": "default:FriendMessage:20002",
            "messages": [{"type": "plain", "text": "发给另一个会话。"}],
        }

        result = await harness._send_message_to_user_tool_with_tts_processing(
            SimpleNamespace(), context, kwargs
        )

        self.assertIsNone(result)
        self.assertFalse(hasattr(event, "_private_companion_same_session_tool_pending"))

    async def test_scheduler_same_session_plain_tool_send_is_not_deferred(self):
        harness = _ToolHarness()
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            get_platform_name=lambda: "cron",
        )
        context = SimpleNamespace(context=SimpleNamespace(event=event))
        kwargs = {"messages": [{"type": "plain", "text": "定时提醒。"}]}

        result = await harness._send_message_to_user_tool_with_tts_processing(
            SimpleNamespace(), context, kwargs
        )

        self.assertIsNone(result)
        self.assertFalse(hasattr(event, "_private_companion_same_session_tool_pending"))

    async def test_scheduler_tool_response_is_not_cleared_for_deferred_delivery(self):
        harness = _ResponseHarness()
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            get_platform_name=lambda: "cron",
        )
        tool_resp = LLMResponse(
            role="tool",
            completion_text="定时任务工具调用仍保留。",
            tools_call_name=["send_message_to_user"],
            tools_call_args=[
                {"messages": [{"type": "plain", "text": "该吃药啦。"}]}
            ],
        )

        await harness.normalize_tts_enhancement_response(event, tool_resp)

        self.assertEqual(tool_resp.completion_text, "定时任务工具调用仍保留。")
        self.assertFalse(hasattr(event, "_private_companion_same_session_tool_pending"))

    async def test_scheduler_tts_tool_send_is_processed_and_sent_directly_once(self):
        harness = _ToolHarness()
        harness._process_tts_tags = AsyncMock(return_value=[Plain("已生成语音")])
        event = SimpleNamespace(
            unified_msg_origin="default:FriendMessage:10001",
            get_platform_name=lambda: "cron",
        )
        platform_send = AsyncMock()
        context = SimpleNamespace(
            context=SimpleNamespace(
                event=event,
                context=SimpleNamespace(send_message=platform_send),
            )
        )
        kwargs = {
            "messages": [
                {"type": "plain", "text": "<pc_tts>该起床啦。</pc_tts>"}
            ]
        }

        result = await harness._send_message_to_user_tool_with_tts_processing(
            SimpleNamespace(), context, kwargs
        )

        self.assertIn("Message sent to session", result)
        platform_send.assert_awaited_once()
        harness._process_tts_tags.assert_awaited_once()
        self.assertFalse(hasattr(event, "_private_companion_same_session_tool_pending"))


if __name__ == "__main__":
    unittest.main()
