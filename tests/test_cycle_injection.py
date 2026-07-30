# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _CycleInjectionHarness(DailyStateMixin):
    def __init__(self, body_cycle: str) -> None:
        self.data = {
            "daily_state": {
                "energy": 42,
                "mood_bias": "疲惫",
                "sleep": "睡眠平稳",
                "dream": "没有记住梦",
                "health": "状态正常",
                "hunger": "无饥饿感",
                "body_cycle": body_cycle,
                "conditions": [],
            }
        }

    def _refresh_sleep_runtime_state(self) -> None:
        return None

    def _current_location_state_text(self, _state) -> str:
        return ""


class _CycleFrequencyHarness(ProactiveMixin):
    def __init__(self, body_cycle: str) -> None:
        self.enable_cycle_state = True
        self.data = {"daily_state": {"body_cycle": body_cycle}}


class CycleInjectionTests(unittest.TestCase):
    def test_active_cycle_has_explicit_behavior_and_disclosure_boundaries(self) -> None:
        harness = _CycleInjectionHarness("处于生理期,能量偏低,想少说重话")
        prompt = harness._format_state_for_prompt(harness.data["daily_state"])
        self.assertIn("边界：这是 Bot 的拟人化/模拟状态，不是用户事实、现实证据或长期记忆", prompt)
        self.assertIn("影响：精力稍低、回复更短更慢、措辞更谨慎，情绪感受稍敏锐", prompt)
        self.assertIn("一定程度上降低私聊与群聊主动频率", prompt)
        self.assertIn("周期状态：Bot 当前的模拟身体状态处于女性生理期", prompt)
        self.assertIn("这是 Bot 自己的状态，不是用户的状态，也不是用户造成的", prompt)
        self.assertNotIn("生理期,能量", prompt)
        self.assertNotIn("影响维度", prompt)
        self.assertNotIn("表达基准", prompt)
        self.assertNotIn("归因边界", prompt)

    def test_neutral_cycle_does_not_add_cycle_rules(self) -> None:
        harness = _CycleInjectionHarness("不处于生理期")
        prompt = harness._format_state_for_prompt(harness.data["daily_state"])
        self.assertNotIn("周期状态：Bot 当前的模拟身体状态", prompt)
        self.assertNotIn("情绪更加敏感", prompt)

    def test_lightweight_style_hint_keeps_same_boundary(self) -> None:
        harness = _CycleInjectionHarness("生理期前,情绪更敏感,耐心更薄")
        hint = harness._format_passive_state_style_hint(harness.data["daily_state"])
        self.assertIn("Bot 接近女性生理期阶段", hint)
        self.assertIn("回复更短更慢", hint)
        self.assertIn("轻微降低私聊与群聊主动频率", hint)
        self.assertIn("这是 Bot 自己的模拟身体状态", hint)

    def test_cycle_phase_applies_moderate_private_and_group_frequency_bias(self) -> None:
        period = _CycleFrequencyHarness("处于生理期,身体舒适度与能量偏低")
        profile = period._cycle_proactive_frequency_profile()
        self.assertEqual(profile["phase"], "period")
        self.assertEqual(profile["private_interval_multiplier"], 1.18)
        self.assertEqual(profile["group_interval_multiplier"], 1.25)
        self.assertEqual(profile["group_probability_multiplier"], 0.76)
        self.assertAlmostEqual(period._cycle_group_interject_probability(0.1), 0.076)

        recovery = _CycleFrequencyHarness("生理期后,慢慢回到稳定状态")
        self.assertEqual(recovery._cycle_proactive_frequency_profile()["phase"], "recovery")
        neutral = _CycleFrequencyHarness("不处于生理期")
        self.assertEqual(neutral._cycle_proactive_frequency_profile()["private_interval_multiplier"], 1.0)


if __name__ == "__main__":
    unittest.main()
