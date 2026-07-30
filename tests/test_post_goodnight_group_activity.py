# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


class _GoodnightGroupHarness(GroupObservationMixin):
    enable_group_companion = True

    def __init__(self) -> None:
        self.offered: list[dict] = []
        self.profile = {"playful": True, "clingy": False, "observant": False}
        self.data = {
            "users": {
                "10001": {
                    "user_id": "10001",
                    "enabled": True,
                    "umo": "default:FriendMessage:10001",
                    "user_rest_kind": "sleep",
                    "user_rest_set_at": 100.0,
                    "user_rest_reason": "晚安，我先睡了",
                    "last_companion_message_at": 105.0,
                    "last_companion_message": "晚安，早点休息，明天见。",
                }
            }
        }

    @staticmethod
    def _private_user_role(_user, user_id: str = "") -> str:
        return "owner" if user_id == "10001" else "friend"

    def _persona_action_profile(self) -> dict[str, bool]:
        return dict(self.profile)

    @staticmethod
    def _group_member_identity_name(_user_id: str, fallback: str, *, limit: int = 24) -> str:
        return str(fallback)[:limit]

    def _offer_proactive_candidate(self, user_id: str, _user: dict, candidate: dict) -> bool:
        self.offered.append({"user_id": user_id, **candidate})
        return True


class PostGoodnightGroupActivityTests(unittest.TestCase):
    def test_mutual_goodnight_can_offer_one_soft_candidate(self) -> None:
        harness = _GoodnightGroupHarness()
        with patch("astrbot_plugin_private_companion.group_observation.random.random", return_value=0.0), patch(
            "astrbot_plugin_private_companion.group_observation.random.randint", return_value=6
        ):
            accepted = harness._maybe_schedule_post_goodnight_group_activity(
                "group-1",
                {"name": "测试群"},
                sender_id="10001",
                sender_name="主要用户",
                text="还没睡，继续聊会儿",
                now=200.0,
            )

        self.assertTrue(accepted)
        self.assertEqual(len(harness.offered), 1)
        candidate = harness.offered[0]
        self.assertEqual(candidate["source"], "post_goodnight_group_activity")
        self.assertEqual(candidate["reason"], "post_goodnight_group_activity")
        self.assertEqual(candidate["scheduled_ts"], 560.0)
        self.assertIn("不要质问、查岗", candidate["motive"])
        self.assertEqual(candidate["context"]["group_activity_at"], 200.0)

    def test_probability_is_drawn_only_once_per_goodnight_episode(self) -> None:
        harness = _GoodnightGroupHarness()
        with patch("astrbot_plugin_private_companion.group_observation.random.random", return_value=0.99):
            first = harness._maybe_schedule_post_goodnight_group_activity(
                "group-1", {}, sender_id="10001", now=200.0
            )
        with patch("astrbot_plugin_private_companion.group_observation.random.random", return_value=0.0):
            second = harness._maybe_schedule_post_goodnight_group_activity(
                "group-1", {}, sender_id="10001", now=220.0
            )

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(harness.offered, [])

    def test_requires_bot_goodnight_after_user_rest_signal(self) -> None:
        harness = _GoodnightGroupHarness()
        harness.data["users"]["10001"]["last_companion_message_at"] = 90.0
        with patch("astrbot_plugin_private_companion.group_observation.random.random", return_value=0.0):
            accepted = harness._maybe_schedule_post_goodnight_group_activity(
                "group-1", {}, sender_id="10001", now=200.0
            )
        self.assertFalse(accepted)
        self.assertEqual(harness.offered, [])

    def test_explicit_do_not_disturb_remains_silent(self) -> None:
        harness = _GoodnightGroupHarness()
        harness.data["users"]["10001"]["user_rest_reason"] = "晚安，今晚别主动找我"
        with patch("astrbot_plugin_private_companion.group_observation.random.random", return_value=0.0):
            accepted = harness._maybe_schedule_post_goodnight_group_activity(
                "group-1", {}, sender_id="10001", now=200.0
            )
        self.assertFalse(accepted)
        self.assertEqual(harness.offered, [])

    def test_secondary_user_never_uses_this_candidate(self) -> None:
        harness = _GoodnightGroupHarness()
        user = dict(harness.data["users"]["10001"])
        user["user_id"] = "20002"
        harness.data["users"] = {"20002": user}
        with patch("astrbot_plugin_private_companion.group_observation.random.random", return_value=0.0):
            accepted = harness._maybe_schedule_post_goodnight_group_activity(
                "group-1", {}, sender_id="20002", now=200.0
            )
        self.assertFalse(accepted)


class PostGoodnightEngineTests(unittest.TestCase):
    def test_night_reason_window_and_fresh_context_are_supported(self) -> None:
        harness = ProactiveEngineMixin()
        user = {
            "planned_proactive_source": "post_goodnight_group_activity",
            "post_goodnight_group_activity_context": {
                "rest_set_at": 100.0,
                "group_activity_at": 200.0,
            },
        }

        self.assertEqual(
            harness._reason_windows("post_goodnight_group_activity"),
            [(20 * 60, 24 * 60), (0, 2 * 60)],
        )
        self.assertTrue(harness._post_goodnight_group_activity_is_fresh(user, now=300.0))
        self.assertFalse(harness._post_goodnight_group_activity_is_fresh(user, now=200.0 + 51 * 60))


if __name__ == "__main__":
    unittest.main()
