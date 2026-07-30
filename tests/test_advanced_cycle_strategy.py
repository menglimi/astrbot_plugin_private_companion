# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.helpers import _flat_get
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class _AdvancedCycleHarness(DailyStateMixin):
    enable_advanced_cycle_strategy = True
    advanced_cycle_link_intensity = False
    advanced_cycle_start_offset = 0
    humanized_state_intensity = 50
    enable_enhanced_dreams = False

    advanced_cycle_menstrual_days = 5
    advanced_cycle_follicular_days = 5
    advanced_cycle_pre_ovulation_days = 3
    advanced_cycle_ovulation_days = 1
    advanced_cycle_luteal_days = 8
    advanced_cycle_pms_days = 6

    advanced_cycle_menstrual_energy = -12
    advanced_cycle_follicular_energy = 0
    advanced_cycle_pre_ovulation_energy = 8
    advanced_cycle_ovulation_energy = 9
    advanced_cycle_luteal_energy = 5
    advanced_cycle_pms_energy = -8

    def __init__(self) -> None:
        self.data = {"state_conditions": [], "daily_weather": {}}

    def _persona_state_profile(self) -> dict[str, bool]:
        return {"allow_hunger": False, "allow_health": False, "allow_cycle": True}

    def _environment_now(self) -> datetime:
        return datetime(2026, 7, 20, 12, 0, 0)

    def _recent_diary_tags(self) -> set[str]:
        return set()

    def _weather_summary_text(self, _weather) -> str:
        return ""

    def _screen_diary_state_condition_spec(self):
        return None

    def _remember_daily_dream_pick(self, _dream_pick) -> None:
        return None

    def _build_dream_aftertaste_condition(self, _dream_pick):
        return None


class AdvancedCycleStrategyTests(unittest.IsolatedAsyncioTestCase):
    def test_all_six_phases_are_inferred_without_legacy_collisions(self) -> None:
        harness = _AdvancedCycleHarness()
        cases = {
            "处于月经期，身体更容易疲倦": "menstrual",
            "处于卵泡期早，精力平稳回升": "follicular",
            "处于排卵前期，身体逐渐轻盈": "pre_ovulation",
            "处于排卵期，精力较充足": "ovulation",
            "处于黄体期早，情绪整体平稳": "luteal",
            "处于 PMS 期，情绪波动稍明显": "pms",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(harness._infer_body_cycle_phase(text), expected)

    def test_linked_energy_uses_one_linear_scale(self) -> None:
        harness = _AdvancedCycleHarness()
        expected = {
            0: {"menstrual": 0, "follicular": 0, "pre_ovulation": 0, "ovulation": 0, "luteal": 0, "pms": 0},
            50: {"menstrual": -12, "follicular": 0, "pre_ovulation": 8, "ovulation": 9, "luteal": 4, "pms": -8},
            100: {"menstrual": -24, "follicular": 0, "pre_ovulation": 15, "ovulation": 18, "luteal": 9, "pms": -15},
        }
        for intensity, phase_values in expected.items():
            harness.humanized_state_intensity = intensity
            for phase, energy in phase_values.items():
                with self.subTest(intensity=intensity, phase=phase):
                    self.assertEqual(harness._advanced_cycle_linked_energy(phase), energy)

    def test_full_transition_chain_is_deterministic(self) -> None:
        harness = _AdvancedCycleHarness()
        phases = list(harness._ADVANCED_CYCLE_PHASES)
        condition = harness._advanced_cycle_condition(phases[0])
        for expected in phases[1:] + [phases[0]]:
            target = condition["transition_options"][0]["to"]
            condition = harness._build_transition_condition(target, condition)
            self.assertIsNotNone(condition)
            self.assertEqual(condition["phase"], expected)

    async def test_zero_energy_follicular_phase_is_not_discarded(self) -> None:
        harness = _AdvancedCycleHarness()
        harness._pick_advanced_cycle_spec = lambda _intensity: harness._advanced_cycle_phase_spec("follicular")

        conditions = await harness._generate_state_conditions()

        cycle = next(item for item in conditions if item.get("kind") == "body_cycle")
        self.assertEqual(cycle["phase"], "follicular")
        self.assertEqual(cycle["energy_delta"], 0)
        self.assertEqual(cycle["transition_options"], [{"to": "body_pre_ovulation", "base_weight": 1.0}])

    def test_manual_offset_is_persistent_and_only_reapplied_after_change(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.advanced_cycle_start_offset = 12
        first = harness._synchronize_body_cycle_strategy([], 1_700_000_000.0)
        first_cycle = next(item for item in first if item.get("kind") == "body_cycle")
        self.assertEqual(first_cycle["phase"], "pre_ovulation")
        self.assertEqual(first_cycle["duration_hours"], 48)
        signature = harness.data["body_cycle_state"]["manual_offset_signature"]

        restarted = _AdvancedCycleHarness()
        restarted.advanced_cycle_start_offset = 12
        restarted.data = deepcopy(harness.data)
        same = restarted._synchronize_body_cycle_strategy(deepcopy(first), 1_700_000_100.0)
        self.assertEqual(len([item for item in same if item.get("kind") == "body_cycle"]), 1)
        self.assertEqual(same[0]["id"], first_cycle["id"])
        self.assertEqual(restarted.data["body_cycle_state"]["manual_offset_signature"], signature)

        restarted.advanced_cycle_start_offset = 14
        changed = restarted._synchronize_body_cycle_strategy(same, 1_700_000_200.0)
        changed_cycle = next(item for item in changed if item.get("kind") == "body_cycle")
        self.assertEqual(changed_cycle["phase"], "ovulation")
        self.assertNotEqual(changed_cycle["id"], first_cycle["id"])

    def test_strategy_switch_removes_incompatible_cycle_phases(self) -> None:
        harness = _AdvancedCycleHarness()
        legacy = harness._make_condition(
            kind="body_cycle",
            title="周期",
            label="处于生理期,身体舒适度与能量偏低",
            mood="疲惫",
            energy_delta=-18,
            duration_hours=72,
            intensity=64,
            phase="period",
        )
        marker = harness._make_condition(
            kind="manual_state",
            title="手动状态",
            label="安静",
            mood="平稳",
            energy_delta=0,
            duration_hours=2,
            intensity=40,
        )
        advanced_result = harness._synchronize_body_cycle_strategy([legacy, marker], 1_700_000_000.0)
        self.assertEqual([item["kind"] for item in advanced_result], ["manual_state"])

        advanced = harness._advanced_cycle_condition("pms")
        harness.enable_advanced_cycle_strategy = False
        legacy_result = harness._synchronize_body_cycle_strategy([advanced, marker], 1_700_000_100.0)
        self.assertEqual([item["kind"] for item in legacy_result], ["manual_state"])

    def test_positive_phase_prompt_remains_soft(self) -> None:
        harness = _AdvancedCycleHarness()
        profile = harness._body_cycle_behavior_profile("处于排卵期，精力较充足")
        self.assertIn("不据此强行增加主动消息或亲密程度", profile["influence"])
        self.assertNotIn("必须", profile["influence"])


class AdvancedCycleConfigTests(unittest.TestCase):
    def test_all_advanced_cycle_settings_are_public_and_roundtrip(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        expected = {
            "enable_advanced_cycle_strategy": True,
            "advanced_cycle_link_intensity": False,
            "advanced_cycle_start_offset": 12,
            "advanced_cycle_menstrual_days": 4,
            "advanced_cycle_menstrual_prompt": "想把动作放轻一些",
            "advanced_cycle_menstrual_mood": "安静",
            "advanced_cycle_menstrual_energy": -10,
            "advanced_cycle_pms_days": 5,
            "advanced_cycle_pms_prompt": "语气稍微收一点",
            "advanced_cycle_pms_mood": "敏感",
            "advanced_cycle_pms_energy": -7,
        }

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)
            allowed = api._allowed_setting_keys()

            for key, value in expected.items():
                self.assertIn(key, allowed)
                normalized = api._normalize_setting_value(key, value)
                api._apply_config_value(key, normalized, expected)

            self.assertTrue(asyncio.run(api._save_config_if_possible()))
            reloaded = AstrBotConfig(str(config_path), schema=schema)
            for key, value in expected.items():
                normalized = api._normalize_setting_value(key, value)
                self.assertEqual(_flat_get(reloaded, key), normalized, key)


if __name__ == "__main__":
    unittest.main()
