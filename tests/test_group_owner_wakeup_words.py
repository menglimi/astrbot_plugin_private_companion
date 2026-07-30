# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.group_wakeup import GroupWakeupMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class _OwnerWakeupHarness(GroupWakeupMixin):
    enable_group_wakeup_enhancement = True
    bot_name = ""
    group_wakeup_direct_words: list[str] = []
    group_wakeup_owner_direct_words = ["小暗号"]

    def __init__(self) -> None:
        self.data = {
            "users": {
                "manual-owner": {
                    "user_id": "manual-owner",
                    "relationship_role": "owner",
                }
            }
        }

    @staticmethod
    def _configured_target_ids() -> list[str]:
        return ["target-owner"]

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return "target-owner" if user_id == "owner-alias" else user_id

    @staticmethod
    def _private_user_role(user: dict, _user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")

    @staticmethod
    def _group_wakeup_strength(*_args, **_kwargs) -> str:
        return "strong"


class GroupOwnerWakeupWordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _OwnerWakeupHarness()
        self.scene = {"talking_to": "group", "trigger": "normal"}

    def _evaluate(self, sender_id: str, text: str = "小暗号 https://example.com") -> dict:
        return self.harness._evaluate_group_wakeup(
            {},
            scene=dict(self.scene),
            sender_id=sender_id,
            sender_name="群友",
            text=text,
            group_id="group-1",
        )

    def test_configured_target_user_can_trigger_owner_word(self) -> None:
        wakeup = self._evaluate("target-owner")

        self.assertEqual(wakeup.get("type"), "direct_word")
        self.assertEqual(wakeup.get("reason"), "owner_direct_wakeup_word")
        self.assertEqual(wakeup.get("word"), "小暗号")

    def test_identity_alias_can_trigger_owner_word(self) -> None:
        self.assertEqual(self._evaluate("owner-alias").get("reason"), "owner_direct_wakeup_word")

    def test_manually_marked_owner_can_trigger_owner_word(self) -> None:
        self.assertEqual(self._evaluate("manual-owner").get("reason"), "owner_direct_wakeup_word")

    def test_other_group_member_cannot_trigger_owner_word(self) -> None:
        self.assertEqual(self._evaluate("ordinary-member"), {})

    def test_global_direct_word_still_applies_to_other_members(self) -> None:
        self.harness.group_wakeup_direct_words = ["大家都能叫"]

        wakeup = self._evaluate("ordinary-member", "大家都能叫 https://example.com")

        self.assertEqual(wakeup.get("reason"), "direct_wakeup_word")

    def test_schema_page_and_panel_expose_owner_words(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        item = schema["group_wakeup_config"]["items"]["group_wakeup_owner_direct_words"]
        self.assertEqual(item["default"], [])

        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = self.harness
        api._schema_key_index_cache = None
        normalized = api._normalize_setting_value("group_wakeup_owner_direct_words", "小暗号\n只给主人的称呼")
        self.assertEqual(normalized, ["小暗号", "只给主人的称呼"])
        self.assertIn("group_wakeup_owner_direct_words", api._allowed_setting_keys())

        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn('group_wakeup_owner_direct_words: "主要用户专属强唤醒词"', script)


if __name__ == "__main__":
    unittest.main()
