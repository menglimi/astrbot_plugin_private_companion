# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.constants import MODEL_QUICK_TIMEOUT_KEYS


class _ExpertHarness(CommandHandlersMixin):
    troubleshooting_provider_id = "qa-provider"
    complex_reasoning_provider_id = "complex-provider"
    llm_provider_id = "main-provider"
    response_review_provider_id = "review-provider"
    mai_style_provider_id = "style-provider"
    data: dict = {}

    def __init__(self) -> None:
        self.call: dict = {}

    @staticmethod
    def _task_provider(*provider_ids: str) -> str:
        return next((str(item) for item in provider_ids if str(item or "").strip()), "")

    async def _llm_call(self, prompt: str, **kwargs):
        self.call = {"prompt": prompt, **kwargs}
        return "这是基于当前实现给出的插件专家答复。"

    @staticmethod
    def _model_timeout_seconds_for_call(**_kwargs):
        return 30.0

    @staticmethod
    def _companion_manual_local_hint_text(_event, _selected) -> str:
        return "本地候选证据"

    @staticmethod
    def _companion_manual_recent_context_text(_event) -> str:
        return "上一轮上下文"

    @staticmethod
    def _companion_manual_runtime_snapshot(_event) -> str:
        return "当前运行状态"

    @staticmethod
    def _get_default_persona_prompt() -> str:
        return "自然、直接"


class CompanionManualExpertTests(unittest.IsolatedAsyncioTestCase):
    def test_quick_mode_uses_complex_model_for_plugin_qa(self) -> None:
        self.assertEqual(
            MODEL_QUICK_TIMEOUT_KEYS["TROUBLESHOOTING_PROVIDER_ID"],
            "COMPLEX_REASONING_PROVIDER_ID",
        )

    def test_manual_context_keeps_detailed_matches_and_compact_global_index(self) -> None:
        harness = _ExpertHarness()
        entries = harness._companion_manual_entries()
        context = harness._companion_manual_context_text(entries[:1])
        self.assertIn(f"【{entries[0]['title']}】", context)
        self.assertIn("其他能力索引（用于发现相关链路，不是预设答案）", context)
        self.assertLessEqual(len(context), 18000)

    def test_source_retrieval_can_find_real_implementation(self) -> None:
        harness = _ExpertHarness()
        context = harness._companion_manual_source_context(
            "_apply_photo_generation_prompt_format 是在哪里处理的",
            [],
        )
        self.assertIn("proactive_message.py", context)
        self.assertIn("_apply_photo_generation_prompt_format", context)

    async def test_model_answer_is_expert_led_and_respects_model_timeout(self) -> None:
        harness = _ExpertHarness()
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")
        answer = await harness._companion_manual_model_answer(
            event,
            "为什么生图提示词还会被插件包装？",
            "规则初判",
            [],
        )
        self.assertIn("插件专家答复", answer)
        self.assertEqual(harness.call["provider_id"], "qa-provider")
        self.assertEqual(harness.call["timeout_key"], "TROUBLESHOOTING_PROVIDER_ID")
        self.assertEqual(harness.call["timeout_seconds"], 30.0)
        self.assertEqual(harness.call["max_tokens"], 1400)
        self.assertIn("插件专家答疑助手", harness.call["prompt"])
        self.assertIn("当前源码/文档摘录", harness.call["prompt"])
        self.assertIn("关键词候选（只用于检索，可能不准确）", harness.call["prompt"])
        self.assertNotIn("回复 3-6 行", harness.call["prompt"])


if __name__ == "__main__":
    unittest.main()
