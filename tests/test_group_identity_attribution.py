# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.memory_companion_adapter import MemoryCompanionAdapterMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.worldbook import WorldbookMixin


class _IdentityHarness(WorldbookMixin, GroupObservationMixin, MemoryCompanionAdapterMixin):
    enable_worldbook_member_recognition = True
    worldbook_member_match_aliases = True
    worldbook_member_inject_limit = 4

    def __init__(self) -> None:
        self.bot_name = "小星"
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

    def test_non_target_display_name_cannot_become_owner_address(self) -> None:
        self.harness.data["worldbook_member_profiles"]["100000003"] = {
            "user_id": "100000003",
            "name": "主人",
            "aliases": ["主人"],
            "observed_names": [],
            "enabled": True,
            "observation_only": True,
            "identity_note": "仅观察角色档案。",
        }
        group = {
            "group_id": "200000001",
            "recent_messages": [
                {
                    "sender_id": "100000003",
                    "name": "主人",
                    "identity_name": "主人",
                    "text": "你觉得呢",
                }
            ],
        }

        guard = self.harness._format_group_current_sender_identity_guard(
            group,
            sender_id="100000003",
            text="你觉得呢",
        )
        worldbook = self.harness._format_worldbook_group_members_for_prompt(
            group,
            sender_id="100000003",
            text="你觉得呢",
        )

        self.assertIn("群成员[QQ:100000003]", guard)
        self.assertIn("平台显示名“主人”", guard)
        self.assertIn("不要照抄该显示名称呼对方", guard)
        self.assertNotIn("判断为 主人[QQ:100000003]", guard)
        self.assertIn("当前发言者是 群成员（QQ:100000003）", worldbook)
        self.assertNotIn("当前发言者是 主人", worldbook)
        self.assertNotIn("称呼线索：主人", worldbook)

    def test_target_owner_can_still_use_configured_owner_address(self) -> None:
        self.harness.data["users"]["100000004"] = {
            "relationship_role": "owner",
            "nickname": "主人",
        }
        self.harness._is_target_private_user = (
            lambda user_id, _user=None: user_id == "100000004"
        )

        self.assertEqual(
            "主人[QQ:100000004]",
            self.harness._group_member_identity_label(
                "100000004",
                "主人",
            ),
        )

    def test_group_persona_denoise_marks_relationship_title_as_display_only(self) -> None:
        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enable_group_persona_denoise = True
        plugin.data = {"users": {}}
        plugin._sender_display_name = lambda _event: "主人"
        plugin._private_user_id_for_event = lambda _event, sender_id: sender_id
        plugin._is_target_private_user = lambda _user_id, _user=None: False
        event = SimpleNamespace(
            get_sender_id=lambda: "100000003",
            private_companion_group_scene={"trigger": "at_bot"},
            private_companion_group_high_intensity=None,
        )

        prompt = plugin._format_group_persona_denoise_prompt(event)

        self.assertIn("当前群名片“主人”", prompt)
        self.assertIn("不是关系事实", prompt)
        self.assertIn("不要照着群名片叫", prompt)
        self.assertIn("群聊玩笑边界", prompt)
        self.assertIn("不要写进核心人物画像", prompt)

    def test_own_registered_name_is_not_treated_as_impersonation(self) -> None:
        claim = self.harness._worldbook_claimed_other_identity(
            "100000001",
            "我是林林",
        )

        self.assertEqual({}, claim)

    def test_discourse_phrase_is_not_treated_as_self_registration(self) -> None:
        for text in (
            "我是说……",
            "我是说：刚才不是这个意思",
            "我 是 说，先别记这个",
            "我是说，我叫小明",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.harness._extract_worldbook_self_intro(text))

        self.assertEqual(
            {"name": "小明", "aliases": ["小明"]},
            self.harness._extract_worldbook_self_intro("我是小明"),
        )

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
