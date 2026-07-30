# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api.message_components import Plain, Record

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


SESSION_ID = "default:FriendMessage:10001"


class _BridgeHarness(ProactiveMessageMixin):
    def __init__(self) -> None:
        self.enable_proactive_chat_integration = True
        self.proactive_chat_bridge_review_mode = "local"
        self.enable_proactive_message_review = True
        self.proactive_review_mode = "full"
        self.data = {
            "users": {
                "10001": {
                    "user_id": "10001",
                    "nickname": "小明",
                    "enabled": True,
                    "inbound_count": 3,
                    "sent_day": "",
                    "sent_today": 7,
                    "pending_followup_event": {"kind": "old"},
                }
            }
        }
        self._data_lock = asyncio.Lock()
        self.saved = 0
        self.local_calls = 0

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return str(user_id or "")

    @staticmethod
    def _user_enabled_for_proactive(_user_id: str, user: dict) -> bool:
        return bool(user.get("enabled", True))

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][user_id]

    def _save_data_sync(self) -> None:
        self.saved += 1

    @staticmethod
    def _sanitize_proactive_text(text: str) -> str:
        return str(text or "").strip()

    @staticmethod
    def _visible_text_without_tts_reading(text: str, *, limit: int = 500) -> str:
        return str(text or "").strip()[:limit]

    @staticmethod
    def _format_proactive_review_runtime_context(_user, *, now=None) -> str:
        return "当前关系稳定"

    @staticmethod
    def _format_proactive_recipient_identity_guard(_user, _nickname) -> str:
        return "当前收件人是小明"

    @staticmethod
    def _format_proactive_voice_prompt() -> str:
        return "自然短句"

    @staticmethod
    def _format_expression_voice_for_prompt(**_kwargs) -> str:
        return "【已审核的表达学习规则】\n避免客服腔"

    @staticmethod
    def _format_proactive_relationship_fact(_user) -> str:
        return "关系熟悉度：亲近；互动偏好：自然"

    @staticmethod
    def _format_intent_relationship_injection(_user) -> str:
        return "气氛轻松"

    @staticmethod
    def _format_time_period_injection() -> str:
        return "当前是傍晚"

    @staticmethod
    def _format_state_for_framework_prompt(_state, **_kwargs) -> str:
        return "语气可以轻快一点"

    @staticmethod
    def _format_schedule_context_for_prompt() -> str:
        return "刚收好手边的东西"

    @staticmethod
    def _sanitize_schedule_context_for_private_user(text, _user) -> str:
        return text

    def _local_proactive_send_decision(self, _user, text, **_kwargs):
        self.local_calls += 1
        return {"decision": "send", "reason": "ok", "text": text}

    @staticmethod
    def _note_action_sent(*_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _remember_proactive_topic(*_args, **_kwargs) -> None:
        return None


class _Event:
    unified_msg_origin = SESSION_ID

    def __init__(self, chain: list[object]) -> None:
        self.result = SimpleNamespace(chain=list(chain))
        self.stopped = False

    @staticmethod
    def is_private_chat() -> bool:
        return True

    def get_result(self):
        return self.result

    def set_result(self, result) -> None:
        self.result = result

    def stop_event(self) -> None:
        self.stopped = True


def _plugin_harness() -> PrivateCompanionPlugin:
    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
    plugin.enabled = True
    plugin.enable_proactive_chat_integration = True
    plugin.proactive_chat_bridge_review_mode = "local"
    plugin.enable_proactive_message_review = True
    plugin.proactive_review_mode = "full"
    plugin.data = {
        "users": {
            "10001": {
                "user_id": "10001",
                "nickname": "小明",
                "enabled": True,
                "inbound_count": 2,
                "sent_day": "",
                "sent_today": 0,
            }
        }
    }
    plugin._data_lock = asyncio.Lock()
    plugin._canonical_private_user_id = lambda value: str(value or "")
    plugin._user_enabled_for_proactive = lambda _user_id, user: bool(user.get("enabled", True))
    plugin._get_user = lambda user_id: plugin.data["users"][user_id]
    plugin._save_data_sync = lambda: None
    plugin._sanitize_proactive_text = lambda text: str(text or "").strip()
    plugin._visible_text_without_tts_reading = lambda text, limit=500: str(text or "").strip()[:limit]
    plugin._reset_daily_counter_if_needed = lambda user: user.update({"sent_day": "test-day", "sent_today": 0})
    plugin._note_action_sent = lambda *_args, **_kwargs: None
    plugin._remember_proactive_topic = lambda *_args, **_kwargs: None
    plugin._build_result_from_chain = lambda chain: SimpleNamespace(chain=list(chain))
    plugin._local_proactive_send_decision = lambda _user, text, **_kwargs: {
        "decision": "send",
        "reason": "ok",
        "text": text,
    }
    return plugin


class ProactiveChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stack_detection_reads_proactive_chat_send_context(self):
        namespace = {"__name__": "astrbot_plugin_proactive_chat.core.message_sender"}
        exec(
            "def _send_proactive_message(target):\n"
            "    text = '完整主动正文。'\n"
            "    is_tts_sent = True\n"
            "    segments = ['第一段。', '第二段。']\n"
            "    idx = 1\n"
            "    return target._proactive_chat_decorating_context()\n",
            namespace,
        )

        context = namespace["_send_proactive_message"](_BridgeHarness())

        self.assertTrue(context["detected"])
        self.assertEqual("完整主动正文。", context["full_text"])
        self.assertTrue(context["tts_sent"])
        self.assertEqual(1, context["segment_index"])
        self.assertEqual(2, context["segment_count"])
        self.assertTrue(context["attempt_id"].startswith("proactive-chat-"))

    async def test_unmanaged_and_disabled_users_are_left_untouched(self):
        harness = _BridgeHarness()

        unmanaged = await harness._review_proactive_chat_bridge_message(
            "default:FriendMessage:20002",
            "原样发送",
        )
        harness.data["users"]["10001"]["enabled"] = False
        disabled = await harness._review_proactive_chat_bridge_message(SESSION_ID, "仍然原样")

        self.assertEqual("bridge_not_managed", unmanaged["reason"])
        self.assertEqual("原样发送", unmanaged["text"])
        self.assertEqual("bridge_not_managed", disabled["reason"])
        self.assertEqual("仍然原样", disabled["text"])
        self.assertEqual(0, harness.local_calls)

    async def test_review_result_is_reused_for_one_tts_and_segmented_attempt(self):
        harness = _BridgeHarness()
        rewrite_calls = 0

        def rewrite(_user, _text, **_kwargs):
            nonlocal rewrite_calls
            rewrite_calls += 1
            return {
                "decision": "rewrite",
                "reason": "去掉回复式开头",
                "text": "自然一点。",
            }

        harness._local_proactive_send_decision = rewrite

        first = await harness._review_proactive_chat_bridge_message(
            SESSION_ID,
            "好呀，自然一点。",
            attempt_id="attempt-1",
        )
        second = await harness._review_proactive_chat_bridge_message(
            SESSION_ID,
            "好呀，自然一点。",
            attempt_id="attempt-1",
        )

        self.assertEqual("自然一点。", first["text"])
        self.assertEqual(first, second)
        self.assertEqual(1, rewrite_calls)

    async def test_follow_review_mode_can_use_existing_proactive_final_review(self):
        harness = _BridgeHarness()
        harness.proactive_chat_bridge_review_mode = "follow_proactive_review"
        harness._review_proactive_message_send_decision = AsyncMock(
            return_value={"decision": "rewrite", "text": "终审后的正文。", "reason": "人格修正"}
        )

        result = await harness._review_proactive_chat_bridge_message(SESSION_ID, "候选正文。")

        self.assertTrue(result["ok"])
        self.assertEqual("终审后的正文。", result["text"])
        harness._review_proactive_message_send_decision.assert_awaited_once()

    async def test_record_sync_is_idempotent_and_preserves_internal_sending_flag(self):
        harness = _BridgeHarness()
        user = harness.data["users"]["10001"]
        user["proactive_sending"] = True
        user["proactive_sending_started_at"] = 123

        first = await harness._record_proactive_chat_bridge_sent(
            SESSION_ID,
            "联动主动正文。",
            attempt_id="attempt-2",
        )
        second = await harness._record_proactive_chat_bridge_sent(
            SESSION_ID,
            "联动主动正文。",
            attempt_id="attempt-2",
        )

        self.assertTrue(first["recorded"])
        self.assertEqual("duplicate_attempt", second["reason"])
        self.assertTrue(user["proactive_sending"])
        self.assertEqual(1, user["sent_today"])
        self.assertEqual(1, user["proactive_sent_count"])
        self.assertEqual(1, user["ignored_streak"])
        self.assertEqual({}, user["pending_followup_event"])
        self.assertEqual("联动主动正文。", user["last_proactive_message"])

    async def test_prepare_and_cancel_require_matching_bridge_token(self):
        harness = _BridgeHarness()

        prepared = await harness._prepare_proactive_chat_bridge(SESSION_ID, unanswered_count=2)
        wrong = await harness._cancel_proactive_chat_bridge(SESSION_ID, token="wrong")
        correct = await harness._cancel_proactive_chat_bridge(SESSION_ID, token=prepared["token"])

        self.assertTrue(prepared["allowed"])
        self.assertIn("当前连续未回应次数：2", prepared["prompt_fragment"])
        self.assertIn("关系熟悉度：亲近", prepared["prompt_fragment"])
        self.assertIn("当前是傍晚", prepared["prompt_fragment"])
        self.assertIn("刚收好手边的东西", prepared["prompt_fragment"])
        self.assertIn("已审核的表达学习规则", prepared["prompt_fragment"])
        self.assertFalse(wrong)
        self.assertTrue(correct)
        self.assertFalse(harness.data["users"]["10001"]["proactive_sending"])

    async def test_prepare_blocks_recent_cross_scheduler_collision_before_generation(self):
        harness = _BridgeHarness()
        harness.proactive_chat_bridge_collision_window_seconds = 90
        harness.data["users"]["10001"]["last_sent"] = time.time() - 5

        prepared = await harness._prepare_proactive_chat_bridge(SESSION_ID)

        self.assertFalse(prepared["allowed"])
        self.assertEqual("recent_proactive_collision_window", prepared["reason"])
        self.assertFalse(harness.data["users"]["10001"].get("proactive_sending", False))

    async def test_outbound_hook_marks_upstream_tts_and_finalizes_once(self):
        plugin = _plugin_harness()
        plugin._proactive_chat_decorating_context = lambda: {
            "detected": True,
            "attempt_id": "attempt-3",
            "full_text": "已经有上游语音。",
            "tts_sent": True,
            "segment_index": 0,
            "segment_count": 1,
        }
        event = _Event([Plain("已经有上游语音。")])

        await plugin.bridge_proactive_chat_outbound(event)
        await plugin.finalize_proactive_chat_outbound_bridge(event)
        await plugin.finalize_proactive_chat_outbound_bridge(event)

        user = plugin.data["users"]["10001"]
        self.assertEqual("proactive_chat_prebuilt_tts", event._private_companion_skip_tts_enhancement)
        self.assertEqual(1, user["sent_today"])
        self.assertEqual("已经有上游语音。", user["last_proactive_message"])

    async def test_deep_bridge_does_not_record_during_pre_send_decoration(self):
        plugin = _plugin_harness()
        plugin._proactive_chat_decorating_context = lambda: {
            "detected": True,
            "deep_bridge": True,
            "attempt_id": "deep-attempt",
            "token": "deep-token",
            "full_text": "等待平台确认。",
            "tts_sent": False,
            "segment_index": 0,
            "segment_count": 1,
        }
        plugin._proactive_chat_runtime_bridge = SimpleNamespace(
            owns_outbound=lambda session_id, attempt_id: session_id == SESSION_ID and attempt_id == "deep-attempt"
        )
        event = _Event([Plain("等待平台确认。")])

        await plugin.bridge_proactive_chat_outbound(event)
        await plugin.finalize_proactive_chat_outbound_bridge(event)

        self.assertEqual(0, plugin.data["users"]["10001"]["sent_today"])

    async def test_record_rewrite_replaces_audio_and_suppresses_later_duplicate_text(self):
        plugin = _plugin_harness()
        context = {
            "detected": True,
            "attempt_id": "attempt-4",
            "full_text": "好呀，换个自然开头。",
            "tts_sent": False,
            "segment_index": 0,
            "segment_count": 1,
        }
        plugin._proactive_chat_decorating_context = lambda: dict(context)
        plugin._local_proactive_send_decision = lambda _user, _text, **_kwargs: {
            "decision": "rewrite",
            "reason": "去掉回复式开头",
            "text": "换个自然开头。",
        }
        audio_event = _Event([Record(file="old.wav")])

        await plugin.bridge_proactive_chat_outbound(audio_event)

        self.assertEqual(1, len(audio_event.result.chain))
        self.assertIsInstance(audio_event.result.chain[0], Plain)
        self.assertEqual("换个自然开头。", audio_event.result.chain[0].text)

        context["tts_sent"] = True
        text_event = _Event([Plain("好呀，换个自然开头。")])
        await plugin.bridge_proactive_chat_outbound(text_event)

        self.assertEqual([], text_event.result.chain)
        self.assertTrue(text_event.stopped)

    async def test_local_review_drop_produces_an_empty_send_chain(self):
        plugin = _plugin_harness()
        plugin._proactive_chat_decorating_context = lambda: {
            "detected": True,
            "attempt_id": "attempt-5",
            "full_text": "内部状态字段。",
            "tts_sent": False,
            "segment_index": 0,
            "segment_count": 1,
        }
        plugin._local_proactive_send_decision = lambda *_args, **_kwargs: {
            "decision": "drop",
            "reason": "内部信息泄漏",
            "hard": True,
        }
        event = _Event([Plain("内部状态字段。")])

        await plugin.bridge_proactive_chat_outbound(event)
        await plugin.finalize_proactive_chat_outbound_bridge(event)

        self.assertEqual([], event.result.chain)
        self.assertTrue(event.stopped)
        self.assertEqual(0, plugin.data["users"]["10001"]["sent_today"])


if __name__ == "__main__":
    unittest.main()
