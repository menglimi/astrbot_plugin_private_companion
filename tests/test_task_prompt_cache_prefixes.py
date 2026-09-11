# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.group_observation import build_group_episode_cache_prompts
from astrbot_plugin_private_companion.planning import split_detail_prompt_cache_sections
from astrbot_plugin_private_companion.tts_enhancement import (
    TtsEnhancementMixin,
    build_tts_spoken_conversion_prompts,
)


class _SystemPromptProvider:
    provider_id = "system-provider"

    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def text_chat(self, **kwargs):
        self.kwargs = dict(kwargs)
        return SimpleNamespace(completion_text="自然口语")


class _LegacyPromptProvider:
    provider_id = "legacy-provider"

    def __init__(self) -> None:
        self.prompt = ""

    async def text_chat(self, *, prompt: str):
        self.prompt = prompt
        return SimpleNamespace(completion_text="自然口语")


class _TtsCacheHarness(TtsEnhancementMixin):
    context = SimpleNamespace(get_provider_by_id=lambda _provider_id: None)

    def __init__(self, task_prompt_extra: str = "") -> None:
        self.task_prompt_extra = task_prompt_extra

    def _apply_task_prompt_override_for_call(self, task, prompt, system_prompt=None):
        if not self.task_prompt_extra:
            return prompt, system_prompt
        self.applied_task = task
        return prompt, f"{system_prompt or ''}\n\n{self.task_prompt_extra}".strip()

    @staticmethod
    def _provider_id_from_instance(provider) -> str:
        return provider.provider_id

    @staticmethod
    def _model_fallback_provider_id(_key: str, _provider_id: str = "") -> str:
        return ""


class TaskPromptCachePrefixTests(unittest.IsolatedAsyncioTestCase):
    def test_detail_dynamic_segment_does_not_change_system_prefix(self) -> None:
        fixed = "固定日程细化规则\n" * 1200
        first_system, first_user = split_detail_prompt_cache_sections(
            f"{fixed}\n【A｜当前段硬框架】\n当前段：08:00-09:00"
        )
        second_system, second_user = split_detail_prompt_cache_sections(
            f"{fixed}\n【A｜当前段硬框架】\n当前段：18:00-19:00"
        )

        self.assertEqual(first_system, second_system)
        self.assertNotEqual(first_user, second_user)
        self.assertNotIn("08:00", first_system)
        self.assertTrue(first_user.startswith("【A｜当前段硬框架】"))

    def test_group_episode_keeps_one_long_prefix_across_dynamic_modes(self) -> None:
        first_system, first_user = build_group_episode_cache_prompts(
            ["甲: 第一段动态消息"],
            learn_expression_rules=False,
        )
        second_system, second_user = build_group_episode_cache_prompts(
            ["乙: 第二段动态消息"],
            learn_expression_rules=True,
            candidate_count=1,
            existing_rule_reference="rule-1: 已有模板",
        )

        self.assertEqual(first_system, second_system)
        self.assertGreater(len(first_system), 2000)
        self.assertNotIn("第一段动态消息", first_system)
        self.assertNotIn("已有模板", first_system)
        self.assertIn("第一段动态消息", first_user)
        self.assertIn("已有模板", second_user)
        self.assertIn('"style_expressions"', first_system)

    def test_tts_source_text_stays_out_of_stable_prefix(self) -> None:
        first_system, first_user = build_tts_spoken_conversion_prompts(
            "第一句原文",
            language_name="日语",
            persona_context="固定人格",
            provider_rule="固定 Provider 规则",
        )
        second_system, second_user = build_tts_spoken_conversion_prompts(
            "第二句原文",
            language_name="日语",
            persona_context="固定人格",
            provider_rule="固定 Provider 规则",
        )

        self.assertEqual(first_system, second_system)
        self.assertNotEqual(first_user, second_user)
        self.assertNotIn("第一句原文", first_system)
        self.assertIn("第一句原文", first_user)

    async def test_tts_provider_receives_separate_system_prompt(self) -> None:
        provider = _SystemPromptProvider()
        harness = _TtsCacheHarness()

        await harness._tts_provider_text_chat(
            provider,
            "【待转换原文】\n动态原文",
            system_prompt="稳定转换规则",
            task="tts_spoken_conversion",
        )

        self.assertEqual(provider.kwargs["system_prompt"], "稳定转换规则")
        self.assertEqual(provider.kwargs["prompt"], "【待转换原文】\n动态原文")

    async def test_tts_legacy_provider_receives_merged_prompt(self) -> None:
        provider = _LegacyPromptProvider()
        harness = _TtsCacheHarness()

        await harness._tts_provider_text_chat(
            provider,
            "【待转换原文】\n动态原文",
            system_prompt="稳定转换规则",
            task="tts_spoken_conversion",
        )

        self.assertIn("稳定转换规则", provider.prompt)
        self.assertIn("动态原文", provider.prompt)

    async def test_tts_task_prompt_override_is_kept_in_system_channel(self) -> None:
        provider = _SystemPromptProvider()
        harness = _TtsCacheHarness("TTS 附加约束：只保留朗读正文")

        await harness._tts_provider_text_chat(
            provider,
            "动态原文",
            system_prompt="稳定转换规则",
            task="tts_spoken_conversion",
        )

        self.assertEqual("tts_spoken_conversion", harness.applied_task)
        self.assertIn(harness.task_prompt_extra, provider.kwargs["system_prompt"])
        self.assertEqual("动态原文", provider.kwargs["prompt"])


if __name__ == "__main__":
    unittest.main()
