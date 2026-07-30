# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot.api.message_components import Record
from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.forward_message import ForwardMessageMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _Reply:
    type = "reply"

    def __init__(self, message_id: str) -> None:
        self.id = message_id
        self.data = {"id": message_id}


class _Event:
    unified_msg_origin = "default:FriendMessage:10001"

    def __init__(self, message_id: str, *, sender_id: str = "10001", self_id: str = "90001") -> None:
        self._messages = [_Reply(message_id)]
        self._sender_id = sender_id
        self._self_id = self_id

    def get_messages(self):
        return list(self._messages)

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id


class _Harness(ForwardMessageMixin, TtsEnhancementMixin):
    def __init__(self, messages: dict[str, dict]) -> None:
        self.messages = messages
        self._recall_message_cache = {}
        self._tts_record_text_index = {}

    def _event_scope_key(self, event: _Event) -> str:
        return f"private:{event.get_sender_id()}"

    def _event_self_id(self, event: _Event) -> str:
        return event.get_self_id()

    def _event_components(self, event: _Event):
        return event.get_messages()

    async def _should_cancel_reply_for_missing_or_recalled_trigger(self, event, message_id: str) -> str:
        return ""

    async def _call_platform_action(self, event, action: str, **kwargs):
        if action != "get_msg":
            return None
        return self.messages.get(str(kwargs.get("message_id") or ""))


class _SentVoiceEvent:
    unified_msg_origin = "default:FriendMessage:10001"

    def __init__(self, component: Record) -> None:
        self._component = component
        self.message_obj = SimpleNamespace(
            message_id="201",
            raw_message={
                "post_type": "message_sent",
                "message_type": "private",
                "message_id": "201",
                "user_id": "10001",
                "self_id": "90001",
                "message": [{"type": "record", "data": {"file": "platform-voice-12345678.amr"}}],
                "raw_message": "[CQ:record,file=platform-voice-12345678.amr]",
            },
        )

    def get_messages(self):
        return [self._component]

    def get_sender_id(self) -> str:
        return "10001"

    def get_self_id(self) -> str:
        return "90001"

    def is_private_chat(self) -> bool:
        return True


class _RecallHarness(EventDispatchMixin, ForwardMessageMixin, TtsEnhancementMixin):
    def __init__(self) -> None:
        self.enable_recall_enhancement = True
        self.enable_recall_message_cache = True
        self.recall_message_cache_text_chars = 500
        self._recall_message_cache = {}
        self._tts_record_text_index = {}
        self.bot_name = "测试角色"

    def _cleanup_recall_message_cache(self) -> None:
        return None


def _voice_message(sender_id: str | None, sender_name: str = "") -> dict:
    payload = {
        "message": [{"type": "record", "data": {"file": "voice.amr"}}],
        "raw_message": "[CQ:record,file=voice.amr]",
    }
    if sender_id:
        payload["sender"] = {"user_id": sender_id, "nickname": sender_name or sender_id}
    return payload


class ReplyVoiceAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_bot_voice_is_attributed_to_bot(self) -> None:
        harness = _Harness({"101": _voice_message("90001", "测试角色")})
        context = await harness._format_reply_chain_context_for_prompt(_Event("101"))

        self.assertIn("原消息发送者：Bot 自己", context)
        self.assertIn("内容类型：语音", context)
        self.assertIn("这条被引用语音是你自己/Bot 此前发送的", context)
        self.assertIn("不要把它说成当前用户自己配的", context)

    async def test_direct_user_voice_is_not_mistaken_for_proven_voice_work(self) -> None:
        harness = _Harness({"102": _voice_message("10001", "测试用户")})
        context = await harness._format_reply_chain_context_for_prompt(_Event("102"))

        self.assertIn("原消息发送者：当前用户", context)
        self.assertIn("不足以证明是用户亲自配音或制作", context)
        self.assertNotIn("这条被引用语音是你自己/Bot 此前发送的", context)

    async def test_other_person_voice_stays_with_other_person(self) -> None:
        harness = _Harness({"103": _voice_message("20002", "群友")})
        context = await harness._format_reply_chain_context_for_prompt(_Event("103"))

        self.assertIn("原消息发送者：其他人", context)
        self.assertIn("不要把发送、配音或制作动作归给当前用户", context)

    async def test_unknown_voice_author_is_not_defaulted_to_user(self) -> None:
        harness = _Harness({"104": _voice_message(None)})
        context = await harness._format_reply_chain_context_for_prompt(_Event("104"))

        self.assertIn("原消息发送者：未知", context)
        self.assertIn("归属未知", context)
        self.assertIn("不要默认说成当前用户发送、配音或制作", context)

    async def test_cached_snapshot_keeps_voice_author_attribution(self) -> None:
        harness = _Harness({})
        harness._recall_message_cache["105"] = {
            "scope": "private:10001",
            "sender_id": "90001",
            "sender_name": "测试角色",
            "raw_message": "[CQ:record,file=cached.amr]",
            "text": "[语音]",
        }
        context = await harness._format_reply_chain_context_for_prompt(_Event("105"))

        self.assertIn("原消息发送者：Bot 自己", context)
        self.assertIn("内容类型：语音", context)

    async def test_bot_generated_voice_recovers_saved_spoken_text(self) -> None:
        harness = _Harness(
            {
                "106": {
                    "sender": {"user_id": "90001", "nickname": "测试角色"},
                    "message": [
                        {
                            "type": "record",
                            "data": {"file": "pc-tts-1234567890.wav"},
                        }
                    ],
                }
            }
        )
        harness._remember_tts_record_text(
            {"type": "record", "data": {"file": "C:/AstrBot/data/tts/pc-tts-1234567890.wav"}},
            "今日はゆっくりしてね。",
            "今天慢一点也没关系。",
        )

        context = await harness._format_reply_chain_context_for_prompt(_Event("106"))

        self.assertIn("插件生成语音实际朗读：今日はゆっくりしてね。", context)
        self.assertIn("生成语音对应原回复：今天慢一点也没关系。", context)
        self.assertIn("不要声称听不到或不知道语音说了什么", context)

    async def test_saved_voice_text_is_not_applied_to_other_people(self) -> None:
        harness = _Harness({"107": _voice_message("20002", "群友")})
        harness._remember_tts_record_text(
            {"type": "record", "data": {"file": "voice.amr"}},
            "不应泄漏给其他人的文本",
            "",
        )

        context = await harness._format_reply_chain_context_for_prompt(_Event("107"))

        self.assertNotIn("不应泄漏给其他人的文本", context)
        self.assertNotIn("插件生成语音实际朗读", context)

    async def test_snapshot_voice_text_survives_changed_platform_reference(self) -> None:
        harness = _Harness({})
        harness._recall_message_cache["108"] = {
            "scope": "private:10001",
            "sender_id": "90001",
            "sender_name": "测试角色",
            "raw_message": "[CQ:record,file=platform-converted.amr]",
            "text": "[语音]",
            "tts_spoken_text": "这是快照里保存的朗读内容。",
            "tts_source_text": "这是快照里保存的原回复。",
        }

        context = await harness._format_reply_chain_context_for_prompt(_Event("108"))

        self.assertIn("插件生成语音实际朗读：这是快照里保存的朗读内容。", context)
        self.assertIn("生成语音对应原回复：这是快照里保存的原回复。", context)

    async def test_message_sent_snapshot_binds_targeted_private_voice_to_bot(self) -> None:
        harness = _RecallHarness()
        record = harness._annotate_tts_record_component(
            Record(file="C:/AstrBot/data/tts/generated-voice-12345678.wav"),
            "这是插件实际生成并朗读的内容。",
            source_text="这是生成语音对应的原回复。",
        )

        await harness._cache_message_for_recall(_SentVoiceEvent(record))

        snapshot = harness._recall_message_cache["201"]
        self.assertEqual("private:10001", snapshot["scope"])
        self.assertEqual("90001", snapshot["sender_id"])
        self.assertEqual("测试角色", snapshot["sender_name"])
        self.assertEqual("这是插件实际生成并朗读的内容。", snapshot["tts_spoken_text"])
        self.assertEqual("这是生成语音对应的原回复。", snapshot["tts_source_text"])


if __name__ == "__main__":
    unittest.main()
