# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.memory_companion_adapter import MemoryCompanionAdapterMixin
from astrbot_plugin_private_companion.worldbook import WorldbookMixin


class _IdentityHarness(WorldbookMixin, GroupObservationMixin, MemoryCompanionAdapterMixin):
    enable_worldbook_member_recognition = True
    worldbook_member_match_aliases = True
    worldbook_member_inject_limit = 4

    def __init__(self) -> None:
        self.data = {
            "users": {},
            "worldbook_member_profiles": {
                "100000001": {
                    "user_id": "100000001",
                    "name": "小林",
                    "aliases": ["林林"],
                    "observed_names": [],
                    "enabled": True,
                    "priority": 120,
                },
                "100000002": {
                    "user_id": "100000002",
                    "name": "小周",
                    "aliases": ["周周", "阿周"],
                    "observed_names": [],
                    "enabled": True,
                    "priority": 120,
                },
            },
            "worldbook_group_profiles": {},
        }

    @staticmethod
    def _is_target_private_user(_user_id, _user=None) -> bool:
        return False

    @staticmethod
    def _private_user_role(_user, _user_id="") -> str:
        return "secondary"

    @staticmethod
    def _protected_owner_nickname_tokens() -> set[str]:
        return {"主人"}


class GroupIdentityAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _IdentityHarness()

    @staticmethod
    def _group(text: str) -> dict:
        return {
            "group_id": "200000001",
            "recent_messages": [
                {
                    "sender_id": "100000001",
                    "name": "林林",
                    "identity_name": "小林",
                    "text": text,
                }
            ],
        }

    def test_other_member_self_claim_cannot_override_current_qq_identity(self) -> None:
        text = "我是周周"
        group = self._group(text)

        guard = self.harness._format_group_current_sender_identity_guard(
            group,
            sender_id="100000001",
            text=text,
        )
        worldbook = self.harness._format_worldbook_group_members_for_prompt(
            group,
            sender_id="100000001",
            text=text,
        )

        self.assertIn("小林[QQ:100000001]", guard)
        self.assertIn("最高优先级身份事实", guard)
        self.assertIn("小周[QQ:100000002]", guard)
        self.assertIn("不要把关于那位成员的历史记忆套给当前发言者", guard)
        self.assertIn("当前发言者是 小林（QQ:100000001）", worldbook)
        self.assertIn("不能把当前发言者改认成 小周", worldbook)

    def test_followup_who_am_i_keeps_current_sender_anchor(self) -> None:
        text = "我是谁呀"
        group = self._group(text)

        guard = self.harness._format_group_current_sender_identity_guard(
            group,
            sender_id="100000001",
            text=text,
        )

        self.assertIn("小林[QQ:100000001]", guard)
        self.assertIn("MemoryCompanion/长期记忆召回都不能覆盖它", guard)
        self.assertNotIn("小周[QQ:100000002]", guard)

    def test_own_registered_name_is_not_treated_as_impersonation(self) -> None:
        claim = self.harness._worldbook_claimed_other_identity(
            "100000001",
            "我是林林",
        )

        self.assertEqual({}, claim)

    def test_memory_bridge_receives_stable_sender_identity(self) -> None:
        text = "我是周周"
        payload = self.harness._memory_companion_build_group_context(
            group_id="200000001",
            group=self._group(text),
            sender_id="100000001",
            sender_name="林林",
            text=text,
        )

        self.assertEqual("小林", payload["sender_name"])
        self.assertEqual("小林(QQ:100000001)", payload["identity_anchor"])
        self.assertIn("小林", payload["entities"])
        self.assertTrue(any("旧记忆不能覆盖" in fact for fact in payload["facts"]))
        self.assertTrue(any("另一成员小周" in fact for fact in payload["facts"]))


if __name__ == "__main__":
    unittest.main()
