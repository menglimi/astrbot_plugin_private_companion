# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HumanizedScheduleUiGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

    def test_schedule_switches_are_embedded_in_humanized_life(self) -> None:
        self.assertIn('enable_humanized_states: ["拟人生活状态"', self.script)
        self.assertIn('enable_daily_plan: "enable_humanized_states"', self.script)
        self.assertIn('enable_detail_enhancement: "enable_humanized_states"', self.script)
        self.assertIn('enable_daily_diary: "enable_humanized_states"', self.script)

    def test_humanized_detail_contains_complete_schedule_sections(self) -> None:
        for title in ("生活日程", "日程细化", "日记与重要日期", "日程生成高级"):
            self.assertIn(f'title: "{title}"', self.script)
        for key in (
            "daily_plan_time",
            "daily_plan_item_count",
            "detail_enhancement_lead_minutes",
            "daily_diary_time",
            "max_diary_entries",
            "important_date_lookahead_days",
            "daily_plan_prompt",
        ):
            self.assertIn(key, self.script)

    def test_schedule_child_controls_follow_their_own_switches(self) -> None:
        self.assertIn('dailyPlanChildren.has(settingKey) && !boolSetting("enable_daily_plan")', self.script)
        self.assertIn('settingKey === "detail_enhancement_lead_minutes" && !boolSetting("enable_detail_enhancement")', self.script)
        self.assertIn('diaryChildren.has(settingKey) && !boolSetting("enable_daily_diary")', self.script)
        for key in ("enable_daily_plan", "enable_detail_enhancement", "enable_daily_diary", "enable_daily_greetings", "enable_enhanced_dreams"):
            self.assertIn(f'"{key}",', self.script)

    def test_advanced_cycle_settings_are_grouped_and_conditionally_visible(self) -> None:
        for title in ("周期策略", "月经期", "卵泡期早", "排卵前期", "排卵期", "黄体期早", "PMS 期"):
            self.assertIn(f'title: "{title}"', self.script)
        for key in (
            "enable_advanced_cycle_strategy",
            "advanced_cycle_link_intensity",
            "advanced_cycle_start_offset",
            "advanced_cycle_menstrual_days",
            "advanced_cycle_follicular_prompt",
            "advanced_cycle_pre_ovulation_mood",
            "advanced_cycle_ovulation_energy",
            "advanced_cycle_luteal_days",
            "advanced_cycle_pms_prompt",
        ):
            self.assertIn(key, self.script)
        self.assertIn('settingKey === "enable_advanced_cycle_strategy") return boolSetting("enable_cycle_state")', self.script)
        self.assertIn('!boolSetting("enable_cycle_state") || !boolSetting("enable_advanced_cycle_strategy")', self.script)
        self.assertIn('manualCycleEnergyKeys.has(settingKey) && boolSetting("advanced_cycle_link_intensity")', self.script)

    def test_advanced_cycle_controls_rerender_without_losing_draft(self) -> None:
        for key in ("enable_cycle_state", "enable_advanced_cycle_strategy", "advanced_cycle_link_intensity"):
            self.assertIn(f'"{key}",', self.script)
        self.assertIn("preserveFeatureParamDraft();", self.script)


if __name__ == "__main__":
    unittest.main()
