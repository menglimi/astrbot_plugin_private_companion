# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.message_components import Plain

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


LEAKED_REPLY = (
    "这是工具调用前保留给用户的可见回复。\n"
    '{"name":"pc_generate_photo","parameters":{"prompt":"室内测试自拍",'
    '"kind":"selfie","scene_preset":"日常穿搭"}}'
)


class FakeEvent:
    message_str = "拍张照片看看"
    unified_msg_origin = "default:FriendMessage:10001"


class RecoveryHarness(LlmToolActionsMixin):
    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result or {"status": "success", "success": True, "sent": True}

    @staticmethod
    def _proactive_only_blocks_passive_event(_event, _feature) -> bool:
        return False

    async def _pc_generate_photo_impl(self, _event, **kwargs) -> str:
        self.calls.append(kwargs)
        return json.dumps(self.result, ensure_ascii=False)


class TtsRecoveryHarness(TtsEnhancementMixin, LlmToolActionsMixin):
    enable_segmented_proactive_reply = True
    segmented_proactive_scope = "all_llm"
    tts_voice_language = "zh"

    @staticmethod
    def _split_proactive_text(text: str) -> list[str]:
        return [part for part in text.splitlines() if part.strip()]


class PlaintextToolCallRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_known_tool_envelope_and_keeps_visible_reply(self):
        harness = RecoveryHarness()
        cleaned, calls = harness._strip_plaintext_tool_call_envelopes(LEAKED_REPLY)
        self.assertEqual("这是工具调用前保留给用户的可见回复。", cleaned)
        self.assertEqual("pc_generate_photo", calls[0]["name"])
        self.assertEqual("selfie", calls[0]["parameters"]["kind"])

    def test_does_not_strip_unrecognized_json(self):
        harness = RecoveryHarness()
        source = '示例：{"name":"ordinary_payload","parameters":{"value":1}}'
        cleaned, calls = harness._strip_plaintext_tool_call_envelopes(source)
        self.assertEqual(source, cleaned)
        self.assertEqual([], calls)

    async def test_recovers_photo_call_only_for_matching_user_intent(self):
        harness = RecoveryHarness()
        event = FakeEvent()
        cleaned, recovery = await harness._recover_plaintext_photo_tool_call(
            event,
            SimpleNamespace(tools_call_name=[]),
            LEAKED_REPLY,
        )
        self.assertNotIn("pc_generate_photo", cleaned)
        self.assertTrue(recovery["sent"])
        self.assertTrue(event._private_companion_plaintext_photo_sent)
        self.assertEqual(1, len(harness.calls))
        self.assertTrue(harness.calls[0]["send"])
        self.assertEqual("日常穿搭", harness.calls[0]["scene_preset"])

    async def test_intent_mismatch_sanitizes_without_execution(self):
        harness = RecoveryHarness()
        event = FakeEvent()
        event.message_str = "请解释为什么照片工具输出了这段 JSON"
        cleaned, recovery = await harness._recover_plaintext_photo_tool_call(
            event,
            SimpleNamespace(tools_call_name=[]),
            LEAKED_REPLY,
        )
        self.assertNotIn("pc_generate_photo", cleaned)
        self.assertEqual("intent_mismatch", recovery["status"])
        self.assertEqual([], harness.calls)

    async def test_failed_recovery_reports_failure_without_claiming_sent(self):
        harness = RecoveryHarness({"status": "error", "sent": False, "message": "图片后端暂时不可用"})
        cleaned, recovery = await harness._recover_plaintext_photo_tool_call(
            FakeEvent(),
            SimpleNamespace(tools_call_name=[]),
            LEAKED_REPLY,
        )
        self.assertFalse(recovery["sent"])
        self.assertIn("图片没能发出来", cleaned)
        self.assertIn("图片后端暂时不可用", cleaned)

    def test_tts_chunking_never_sends_tool_json_as_followup_text(self):
        harness = TtsRecoveryHarness()
        chunks = harness._tts_segment_plain_chunk_for_ordered_send(FakeEvent(), [Plain(LEAKED_REPLY)])
        sent_text = "\n".join(
            str(getattr(component, "text", "") or "")
            for chunk in chunks
            for component in chunk
        )
        self.assertIn("可见回复", sent_text)
        self.assertNotIn("pc_generate_photo", sent_text)
        self.assertNotIn("parameters", sent_text)

    def test_main_hooks_recovery_before_tts_and_has_final_guard(self):
        main_source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn("_recover_plaintext_photo_tool_call(event, resp, original_text)", main_source)
        self.assertIn("strip_plaintext_tool_calls_before_send", main_source)


if __name__ == "__main__":
    unittest.main()
