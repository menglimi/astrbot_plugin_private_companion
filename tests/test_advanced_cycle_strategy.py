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
from astrbot_plugin_private_companion.helpers import _flat_get, _now_ts
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
        self._persona_overrides: dict[str, object] = {}

    def persona_setting(self, key: str, default=None):
        return self._persona_overrides.get(key, getattr(self, key, default))

    def _persona_state_profile(self) -> dict[str, bool]:
        return {"allow_hunger": False, "allow_health": False, "allow_cycle": True}

    def _environment_now(self) -> datetime:
        return datetime(2026, 7, 20, 12, 0, 0)

    def _environment_fromtimestamp(self, timestamp: float) -> datetime:
        return datetime.fromtimestamp(timestamp)

    def _recent_diary_tags(self) -> set[str]:
        return set()

    def _weather_summary_text(self, _weather) -> str:
        return ""

    def _current_location_state_text(self, _state=None) -> str:
        return ""

    def _screen_diary_state_condition_spec(self):
        return None

    def _remember_daily_dream_pick(self, _dream_pick) -> None:
        return None

    def _build_dream_aftertaste_condition(self, _dream_pick):
        return None


class AdvancedCycleStrategyTests(unittest.IsolatedAsyncioTestCase):
    def test_advanced_cycle_fields_and_dedup_windows_use_persona_overrides(self) -> None:
        harness = _AdvancedCycleHarness()
        harness._persona_overrides.update(
            {
                "advanced_cycle_menstrual_days": 9,
                "advanced_cycle_menstrual_prompt": "次人格经期提示",
                "advanced_cycle_menstrual_mood": "次人格疲惫",
                "advanced_cycle_menstrual_energy": -23,
                "proactive_dedup_sent_window_minutes": 17,
            }
        )

        self.assertEqual(9, harness._advanced_cycle_phase_days("menstrual"))
        self.assertEqual(
            ("次人格经期提示", "次人格疲惫", -23, 216),
            harness._advanced_cycle_phase_spec("menstrual"),
        )
        self.assertEqual(17, harness._proactive_dedup_window_minutes("sent", 240))
        self.assertEqual(5, harness.advanced_cycle_menstrual_days)

    def test_all_six_phases_are_inferred_without_legacy_collisions(self) -> None:
        harness = _AdvancedCycleHarness()
        cases = {
            "处于月经期，身体更容易疲倦": "menstrual",
            "处于卵泡期，精力平稳回升": "follicular",
            "处于排卵前期，身体逐渐轻盈": "pre_ovulation",
            "处于排卵期，精力较充足": "ovulation",
            "处于黄体期，情绪整体平稳": "luteal",
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
        self.assertEqual([item["kind"] for item in advanced_result], ["manual_state", "body_cycle"])
        self.assertEqual(advanced_result[-1]["phase"], "menstrual")

        advanced = harness._advanced_cycle_condition("pms")
        harness.enable_advanced_cycle_strategy = False
        legacy_result = harness._synchronize_body_cycle_strategy([advanced, marker], 1_700_000_100.0)
        self.assertEqual([item["kind"] for item in legacy_result], ["manual_state"])

    def test_positive_phase_prompt_remains_soft(self) -> None:
        harness = _AdvancedCycleHarness()
        profile = harness._body_cycle_behavior_profile("处于排卵期，精力较充足")
        self.assertIn("不据此强行增加主动消息或亲密程度", profile["influence"])
        self.assertNotIn("必须", profile["influence"])

    def test_first_enable_auto_seeds_cycle_from_menstrual_day_one(self) -> None:
        harness = _AdvancedCycleHarness()
        result = harness._synchronize_body_cycle_strategy([], _now_ts())
        cycle = next(item for item in result if item.get("kind") == "body_cycle")
        self.assertEqual(cycle["phase"], "menstrual")
        self.assertEqual(cycle["duration_hours"], 5 * 24)
        self.assertGreater(harness.data["body_cycle_state"]["cycle_anchor_ts"], 0)

    def test_anchor_drives_continuous_phase_position_without_conditions(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts() - 7 * 86400}
        runtime = harness._advanced_cycle_runtime()
        self.assertEqual(runtime["phase"], "follicular")
        self.assertEqual(runtime["day_in_phase"], 3)
        self.assertEqual(runtime["phase_name"], "卵泡期")
        self.assertEqual(runtime["cycle_day"], 8)
        self.assertEqual(runtime["next_phase"], "pre_ovulation")
        self.assertEqual(runtime["next_phase_name"], "排卵前期")

    def test_runtime_is_empty_before_first_cycle(self) -> None:
        harness = _AdvancedCycleHarness()
        self.assertEqual(harness._advanced_cycle_runtime(), {})

    def test_manual_offset_sets_cycle_anchor(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.advanced_cycle_start_offset = 12
        harness._synchronize_body_cycle_strategy([], _now_ts())
        runtime = harness._advanced_cycle_runtime()
        self.assertEqual(runtime["phase"], "pre_ovulation")
        self.assertEqual(runtime["day_in_phase"], 2)
        self.assertEqual(runtime["cycle_day"], 12)

    def test_reconcile_realigns_stale_condition_to_anchor(self) -> None:
        harness = _AdvancedCycleHarness()
        anchor = _now_ts() - 9 * 86400  # cycle day 10 -> follicular day 5
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": anchor}
        stale = harness._advanced_cycle_condition("menstrual", duration_hours=24)
        conditions = harness._reconcile_advanced_cycle_condition([stale], _now_ts())
        cycle = next(item for item in conditions if item.get("kind") == "body_cycle")
        self.assertEqual(cycle["phase"], "follicular")
        self.assertEqual(cycle["start_ts"], anchor + 5 * 86400)
        self.assertEqual(cycle["end_ts"], anchor + 10 * 86400)

    def test_cycle_episode_record_preserves_discomfort_roll_marker(self) -> None:
        harness = _AdvancedCycleHarness()
        marker = "2026-08-14"
        harness.data["body_cycle_state"] = {
            "cycle_anchor_ts": _now_ts() - 86400,
            "last_discomfort_roll_date": marker,
        }
        condition = harness._advanced_cycle_condition("follicular")
        harness._record_body_cycle_episode(condition)
        self.assertEqual(
            harness.data["body_cycle_state"].get("last_discomfort_roll_date"),
            marker,
        )

    def test_incompatible_legacy_condition_does_not_reset_existing_anchor(self) -> None:
        harness = _AdvancedCycleHarness()
        anchor = _now_ts() - 86400
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": anchor}
        legacy = harness._make_condition(
            kind="body_cycle",
            title="周期",
            label="处于生理期",
            mood="疲惫",
            energy_delta=-10,
            duration_hours=24,
            intensity=50,
            phase="period",
        )
        result = harness._synchronize_body_cycle_strategy([legacy], _now_ts())
        self.assertEqual(harness.data["body_cycle_state"].get("cycle_anchor_ts"), anchor)
        self.assertFalse(any(item.get("phase") == "period" for item in result))

    def test_compose_surfaces_phase_name_day_and_runtime(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts() - 86400}
        state = harness._compose_state_from_conditions()
        self.assertEqual(state["body_cycle"], "月经期 第2天")
        runtime = state["cycle_runtime"]
        self.assertEqual(runtime["phase"], "menstrual")
        self.assertEqual(runtime["day_in_phase"], 2)
        self.assertEqual(runtime["phase_name"], "月经期")

    def test_discomfort_picker_respects_toggles_and_phases(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts()}
        # Disabled by default.
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())
        harness.advanced_cycle_discomfort_simulation = True
        harness.advanced_cycle_discomfort_chance = 0
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())
        # The zero-chance roll above consumed today's attempt; simulate the
        # next day so the guaranteed roll below is not blocked by the dedup.
        harness.data["body_cycle_state"]["last_discomfort_roll_date"] = "2000-01-01"
        harness.advanced_cycle_discomfort_chance = 100
        harness.advanced_cycle_discomfort_types = "痛经"
        condition = harness._maybe_pick_cycle_discomfort()
        self.assertIsNotNone(condition)
        self.assertEqual(condition["kind"], "cycle_discomfort")
        self.assertEqual(condition["phase"], "痛经")
        self.assertLess(condition["energy_delta"], 0)
        # A second roll the same day is skipped.
        harness.data["state_conditions"] = [condition]
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())

    def test_discomfort_rolls_at_most_once_per_day(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.advanced_cycle_discomfort_simulation = True
        harness.advanced_cycle_discomfort_chance = 100
        harness.advanced_cycle_discomfort_types = "痛经"
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts()}
        first = harness._maybe_pick_cycle_discomfort()
        self.assertIsNotNone(first)
        # Even with no active discomfort left, the same day gets no second roll.
        harness.data["state_conditions"] = []
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())

    def test_discomfort_skipped_without_cycle_allowance_or_intensity(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.advanced_cycle_discomfort_simulation = True
        harness.advanced_cycle_discomfort_chance = 100
        harness.advanced_cycle_discomfort_types = "痛经"
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts()}
        harness._persona_state_profile = lambda: {
            "allow_hunger": False,
            "allow_health": False,
            "allow_cycle": False,
        }
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())
        harness._persona_state_profile = lambda: {
            "allow_hunger": False,
            "allow_health": False,
            "allow_cycle": True,
        }
        harness.humanized_state_intensity = 0
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())

    def test_cleanup_drops_cycle_discomfort_when_cycle_or_simulation_off(self) -> None:
        def make_discomfort(harness: _AdvancedCycleHarness) -> dict:
            return harness._make_condition(
                kind="cycle_discomfort",
                title="经期不适",
                label="今天有点痛经",
                mood="疲惫",
                energy_delta=-12,
                duration_hours=6,
                intensity=60,
                phase="痛经",
            )

        # allow_cycle off: discomfort pruned together with body_cycle.
        harness = _AdvancedCycleHarness()
        harness._persona_state_profile = lambda: {
            "allow_hunger": False,
            "allow_health": False,
            "allow_cycle": False,
        }
        harness.data["state_conditions"] = [make_discomfort(harness)]
        harness._cleanup_expired_conditions()
        self.assertEqual(harness.data["state_conditions"], [])

        # Advanced strategy off: lingering discomfort pruned.
        harness = _AdvancedCycleHarness()
        harness.enable_advanced_cycle_strategy = False
        harness.advanced_cycle_discomfort_simulation = True
        harness.data["state_conditions"] = [make_discomfort(harness)]
        harness._cleanup_expired_conditions()
        self.assertNotIn(
            "cycle_discomfort",
            [item.get("kind") for item in harness.data["state_conditions"] if isinstance(item, dict)],
        )

        # Simulation toggle off: lingering discomfort pruned.
        harness = _AdvancedCycleHarness()
        harness.advanced_cycle_discomfort_simulation = False
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts()}
        harness.data["state_conditions"] = [make_discomfort(harness)]
        harness._cleanup_expired_conditions()
        self.assertNotIn(
            "cycle_discomfort",
            [item.get("kind") for item in harness.data["state_conditions"] if isinstance(item, dict)],
        )

    def test_discomfort_picker_only_fires_in_matching_phases(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.advanced_cycle_discomfort_simulation = True
        harness.advanced_cycle_discomfort_chance = 100
        harness.advanced_cycle_discomfort_types = "痛经"
        # Cycle day 8 -> follicular, where 痛经 is not allowed.
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts() - 7 * 86400}
        self.assertIsNone(harness._maybe_pick_cycle_discomfort())

    def test_cycle_discomfort_ignored_in_compose_without_cycle_allowance(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts()}
        harness._persona_state_profile = lambda: {
            "allow_hunger": False,
            "allow_health": False,
            "allow_cycle": False,
        }
        condition = harness._make_condition(
            kind="cycle_discomfort",
            title="经期不适",
            label="今天有点痛经",
            mood="疲惫",
            energy_delta=-12,
            duration_hours=6,
            intensity=60,
            phase="痛经",
        )
        harness.data["state_conditions"] = [condition]
        state = harness._compose_state_from_conditions()
        self.assertFalse(state.get("cycle_runtime"))
        self.assertEqual(state["body_cycle"], "生理期模拟未开启")

    def test_cycle_discomfort_shows_up_in_composed_runtime(self) -> None:
        harness = _AdvancedCycleHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": _now_ts()}
        condition = harness._make_condition(
            kind="cycle_discomfort",
            title="经期不适",
            label="今天有点痛经，小腹闷闷地不舒服",
            mood="疲惫",
            energy_delta=-14,
            duration_hours=6,
            intensity=60,
            phase="痛经",
        )
        harness.data["state_conditions"] = [condition]
        state = harness._compose_state_from_conditions()
        discomfort = state["cycle_runtime"]["discomfort"]
        self.assertEqual(discomfort[0]["type"], "痛经")
        self.assertIn("痛经", discomfort[0]["label"])


class AdvancedCycleConfigTests(unittest.TestCase):
    def test_panel_contains_fallback_values_for_every_cycle_setting(self) -> None:
        panel = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        fallback_block = panel.split("const fallbackValue = (name) => {", 1)[1].split(
            "return Object.prototype.hasOwnProperty.call(defaults, name)", 1
        )[0]
        expected = {
            "enable_cycle_state",
            "enable_advanced_cycle_strategy",
            "advanced_cycle_link_intensity",
            "advanced_cycle_start_offset",
            "advanced_cycle_menstrual_days",
            "advanced_cycle_menstrual_prompt",
            "advanced_cycle_menstrual_mood",
            "advanced_cycle_menstrual_energy",
            "advanced_cycle_follicular_days",
            "advanced_cycle_follicular_prompt",
            "advanced_cycle_follicular_mood",
            "advanced_cycle_follicular_energy",
            "advanced_cycle_pre_ovulation_days",
            "advanced_cycle_pre_ovulation_prompt",
            "advanced_cycle_pre_ovulation_mood",
            "advanced_cycle_pre_ovulation_energy",
            "advanced_cycle_ovulation_days",
            "advanced_cycle_ovulation_prompt",
            "advanced_cycle_ovulation_mood",
            "advanced_cycle_ovulation_energy",
            "advanced_cycle_luteal_days",
            "advanced_cycle_luteal_prompt",
            "advanced_cycle_luteal_mood",
            "advanced_cycle_luteal_energy",
            "advanced_cycle_pms_days",
            "advanced_cycle_pms_prompt",
            "advanced_cycle_pms_mood",
            "advanced_cycle_pms_energy",
            "advanced_cycle_discomfort_simulation",
            "advanced_cycle_discomfort_chance",
            "advanced_cycle_discomfort_types",
        }
        for key in expected:
            with self.subTest(key=key):
                self.assertIn(f"{key}:", fallback_block)

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
            "advanced_cycle_discomfort_simulation": True,
            "advanced_cycle_discomfort_chance": 70,
            "advanced_cycle_discomfort_types": "痛经,头痛",
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
