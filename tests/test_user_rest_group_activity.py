# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.user_rest_gate import UserRestGateMixin


class RestHarness(UserRestGateMixin, EventDispatchMixin):
    def __init__(self) -> None:
        self.data = {"users": {}}
        self.bot_name = "Bot"

    @staticmethod
    def _event_at_user_ids(_event) -> set[str]:
        return {"10001"}

    @staticmethod
    def _event_self_id(_event) -> str:
        return "99999"

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "group-1"


class UserRestGroupActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = RestHarness()

    def test_group_activity_after_sleep_signal_clears_rest(self) -> None:
        user = {
            "user_id": "10001",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "我先睡了",
            "user_rest_kind": "sleep",
            "last_activity_at": 200,
        }

        self.assertEqual(self.harness._user_rest_silence_until(user, now=250), 0)
        self.assertEqual(user["user_rest_until"], 0)
        self.assertEqual(user["user_rest_kind"], "")

    def test_legacy_sleep_reason_uses_activity_evidence(self) -> None:
        user = {
            "user_id": "10001",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "我去睡了",
            "last_activity_at": 200,
        }

        self.assertEqual(self.harness._user_rest_silence_until(user, now=250), 0)

    def test_quiet_request_is_not_recast_as_sleep(self) -> None:
        user = {
            "user_id": "10001",
            "nickname": "测试用户",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "今天别主动找我",
            "user_rest_kind": "quiet",
            "last_activity_at": 200,
        }
        self.harness.data["users"]["10001"] = user

        target_id, notice = self.harness._group_resting_mention_notice(
            SimpleNamespace(),
            {},
            sender_id="20002",
            now=250,
        )

        self.assertEqual(target_id, "")
        self.assertEqual(notice, "")
        self.assertEqual(user["user_rest_until"], 1000)

    def test_rest_notice_remains_available_without_new_activity(self) -> None:
        user = {
            "user_id": "10001",
            "nickname": "测试用户",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "我先睡了",
            "user_rest_kind": "sleep",
            "last_activity_at": 100,
        }
        self.harness.data["users"]["10001"] = user

        target_id, notice = self.harness._group_resting_mention_notice(
            SimpleNamespace(),
            {},
            sender_id="20002",
            now=250,
        )

        self.assertEqual(target_id, "10001")
        self.assertTrue(notice)

    def test_morning_greeting_from_any_source_bypasses_sleep_rest(self) -> None:
        user = {
            "user_id": "10001",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "我先睡了",
            "user_rest_kind": "sleep",
            "last_activity_at": 100,
        }

        for source in ("daily_greeting", "pending_followup", "story", "state"):
            with self.subTest(source=source):
                self.assertEqual(
                    self.harness._proactive_rest_block_until(
                        user,
                        now=250,
                        reason="morning_greeting",
                        source=source,
                    ),
                    0,
                )

    def test_explicit_do_not_disturb_still_blocks_morning_greeting(self) -> None:
        user = {
            "user_id": "10001",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "晚安，明天早上也别主动找我",
            "user_rest_kind": "sleep",
            "last_activity_at": 100,
        }

        self.assertEqual(
            self.harness._proactive_rest_block_until(
                user,
                now=250,
                reason="morning_greeting",
                source="daily_greeting",
            ),
            1000,
        )

    def test_non_sleep_rest_kinds_still_block_morning_greeting(self) -> None:
        for kind, rest_reason in (
            ("nap", "我先午休一会儿"),
            ("rest", "我去休息一下"),
            ("quiet", "今天别主动找我"),
            ("until_morning", "明早再聊"),
        ):
            with self.subTest(kind=kind):
                user = {
                    "user_id": "10001",
                    "user_rest_until": 1000,
                    "user_rest_set_at": 100,
                    "user_rest_reason": rest_reason,
                    "user_rest_kind": kind,
                    "last_activity_at": 100,
                }
                self.assertEqual(
                    self.harness._proactive_rest_block_until(
                        user,
                        now=250,
                        reason="morning_greeting",
                        source="daily_greeting",
                    ),
                    1000,
                )

    def test_non_morning_candidate_still_blocked_during_sleep(self) -> None:
        user = {
            "user_id": "10001",
            "user_rest_until": 1000,
            "user_rest_set_at": 100,
            "user_rest_reason": "我先睡了",
            "user_rest_kind": "sleep",
            "last_activity_at": 100,
        }

        for reason in ("noon_greeting", "check_in"):
            with self.subTest(reason=reason):
                self.assertEqual(
                    self.harness._proactive_rest_block_until(
                        user,
                        now=250,
                        reason=reason,
                        source="daily_greeting",
                    ),
                    1000,
                )


if __name__ == "__main__":
    unittest.main()
