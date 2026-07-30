# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

from astrbot_plugin_private_companion.daily_state import DailyStateMixin, _today_key
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _OutfitStateHarness(DailyStateMixin):
    def __init__(self) -> None:
        self.data = {
            "daily_state": {"date": _today_key(), "location": "宿舍卧室"},
            "daily_plan": {},
            "schedule_adjustments": [],
            "detail_enhanced_segments": {},
        }

    @staticmethod
    def _private_user_role(user=None, *_args) -> str:
        return str((user or {}).get("relationship_role") or "owner")

    def _format_schedule_context_for_prompt(self, *_args, **_kwargs) -> str:
        return "当前在宿舍准备出门；今日基础穿搭：白衬衫"

    @staticmethod
    def _format_story_plan_for_prompt() -> str:
        return "默认穿着连衣裙"


class DialogueOutfitContinuityTests(unittest.TestCase):
    def test_explicit_outfit_change_is_recorded_and_survives_a_followup_turn(self) -> None:
        harness = _OutfitStateHarness()
        owner = {"user_id": "10001", "relationship_role": "owner"}

        self.assertTrue(harness._record_dialogue_outfit_override_from_interaction("换一套JK出门", owner))
        prompt = harness._format_dialogue_outfit_continuity_for_prompt(owner)
        self.assertIn("换一套JK出门", prompt)
        self.assertIn("高于人格默认服装", prompt)

        self.assertTrue(harness._record_dialogue_outfit_override_from_interaction("换成睡衣", owner))
        prompt = harness._format_dialogue_outfit_continuity_for_prompt(owner)
        self.assertIn("换成睡衣", prompt)
        self.assertNotIn("换一套JK出门", prompt)

    def test_shared_outing_sentence_keeps_the_outfit_change_signal(self) -> None:
        harness = _OutfitStateHarness()
        owner = {"user_id": "10001", "relationship_role": "owner"}
        self.assertTrue(
            harness._record_dialogue_outfit_override_from_interaction("我们换一套JK出门", owner)
        )
        self.assertIn("JK", harness.data["dialogue_outfit_override"]["instruction"])

    def test_user_clothing_and_feedback_are_not_treated_as_bot_outfit_changes(self) -> None:
        harness = _OutfitStateHarness()
        owner = {"user_id": "10001", "relationship_role": "owner"}
        self.assertFalse(harness._record_dialogue_outfit_override_from_interaction("我换了件外套", owner))
        self.assertFalse(
            harness._record_dialogue_outfit_override_from_interaction("为什么你又换回旧衣服", owner)
        )

    def test_override_is_expired_and_isolated_between_private_users(self) -> None:
        harness = _OutfitStateHarness()
        owner = {"user_id": "10001", "relationship_role": "owner"}
        friend = {"user_id": "20002", "relationship_role": "friend"}
        harness._record_dialogue_outfit_override_from_interaction("换一套JK出门", owner)

        self.assertEqual("", harness._format_dialogue_outfit_continuity_for_prompt(friend))
        harness.data["dialogue_outfit_override"]["expires_at"] = time.time() - 1
        self.assertEqual("", harness._format_dialogue_outfit_continuity_for_prompt(owner))

    def test_limited_private_turn_is_promoted_when_it_changes_outfit(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        self.assertFalse(plugin._is_lightweight_private_passive_inbound("换一套JK"))
        self.assertTrue(plugin._is_lightweight_private_passive_inbound("嗯嗯"))

    def test_life_context_declares_dialogue_state_authority(self) -> None:
        harness = _OutfitStateHarness()
        text = harness._format_life_context_injection()
        self.assertIn("当前会话中已经明确发生且尚未撤销的换装", text)
        self.assertIn("日程和每日穿搭只补足空白", text)


if __name__ == "__main__":
    unittest.main()
