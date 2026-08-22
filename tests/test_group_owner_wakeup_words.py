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
    enable_group_image_wakeup = True
    enable_group_bot_name_wakeup = True
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

    @staticmethod
    def _group_wakeup_interest_words(_group=None) -> list[str]:
        return []

    @staticmethod
    def _event_scene_signals(_event) -> dict:
        return {"self_id": "bot", "at_targets": [], "at_all": False, "reply_to_id": ""}


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

    def test_bot_name_is_automatic_direct_word_by_default(self) -> None:
        self.harness.bot_name = "星缘"

        wakeup = self._evaluate("ordinary-member", "星缘 在吗")

        self.assertEqual("direct_wakeup_word", wakeup.get("reason"))
        self.assertEqual("星缘", wakeup.get("word"))

    def test_bot_name_switch_off_removes_only_automatic_word(self) -> None:
        self.harness.bot_name = "星缘"
        self.harness.enable_group_bot_name_wakeup = False

        self.assertEqual({}, self._evaluate("ordinary-member", "星缘 在吗"))
        self.assertFalse(self.harness._group_message_addresses_bot(object(), "星缘 在吗"))
        scene = self.harness._infer_group_scene(
            None,
            {},
            sender_id="ordinary-member",
            sender_name="群友",
            text="星缘 在吗",
        )
        self.assertEqual("group", scene["talking_to"])
        self.assertEqual(
            {},
            self.harness._group_wakeup_from_image_vision_summary(
                "图片文字：星缘 在吗",
                sender_id="ordinary-member",
            ),
        )

    def test_manual_direct_word_still_wakes_when_bot_name_switch_is_off(self) -> None:
        self.harness.bot_name = "星缘"
        self.harness.enable_group_bot_name_wakeup = False
        self.harness.group_wakeup_direct_words = ["星缘"]

        self.assertEqual(
            "direct_wakeup_word",
            self._evaluate("ordinary-member", "星缘 在吗").get("reason"),
        )

    def test_image_summary_global_direct_word_applies_to_other_members(self) -> None:
        self.harness.group_wakeup_direct_words = ["图里叫我"]

        wakeup = self.harness._group_wakeup_from_image_vision_summary(
            "图片文字：图里叫我",
            sender_id="ordinary-member",
        )

        self.assertEqual(wakeup.get("type"), "direct_word")
        self.assertEqual(wakeup.get("word"), "图里叫我")
        self.assertEqual(wakeup.get("reason"), "image_direct_wakeup_word")
        self.assertEqual(wakeup.get("source"), "image_vision")

    def test_image_summary_owner_word_requires_primary_user(self) -> None:
        self.harness.group_wakeup_owner_direct_words = ["只给主人的称呼"]
        summary = "图片中的文字：只给主人的称呼"

        owner_wakeup = self.harness._group_wakeup_from_image_vision_summary(
            summary,
            sender_id="owner-alias",
        )
        ordinary_wakeup = self.harness._group_wakeup_from_image_vision_summary(
            summary,
            sender_id="ordinary-member",
        )

        self.assertEqual(owner_wakeup.get("word"), "只给主人的称呼")
        self.assertEqual(owner_wakeup.get("reason"), "image_direct_wakeup_word")
        self.assertEqual(ordinary_wakeup, {})

    def test_image_summary_wakeup_switch_off_blocks_direct_word(self) -> None:
        self.harness.group_wakeup_direct_words = ["图里叫我"]
        self.harness.enable_group_image_wakeup = False

        self.assertEqual(
            {},
            self.harness._group_wakeup_from_image_vision_summary(
                "图片文字：图里叫我",
                sender_id="ordinary-member",
            ),
        )

    def test_schema_page_and_panel_expose_owner_words_and_bot_name_switch(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        wakeup_items = schema["group_wakeup_config"]["items"]
        self.assertTrue(wakeup_items["enable_group_bot_name_wakeup"]["default"])
        item_keys = list(wakeup_items)
        self.assertLess(item_keys.index("group_wakeup_direct_words"), item_keys.index("enable_group_bot_name_wakeup"))
        self.assertLess(item_keys.index("enable_group_bot_name_wakeup"), item_keys.index("group_wakeup_owner_direct_words"))
        item = schema["group_wakeup_config"]["items"]["group_wakeup_owner_direct_words"]
        self.assertEqual(item["default"], [])

        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = self.harness
        api._schema_key_index_cache = None
        normalized = api._normalize_setting_value("group_wakeup_owner_direct_words", "小暗号\n只给主人的称呼")
        self.assertEqual(normalized, ["小暗号", "只给主人的称呼"])
        self.assertFalse(api._normalize_setting_value("enable_group_bot_name_wakeup", False))
        self.assertIn("group_wakeup_owner_direct_words", api._allowed_setting_keys())
        self.assertIn("enable_group_bot_name_wakeup", api._allowed_setting_keys())

        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn('group_wakeup_owner_direct_words: "主要用户专属强唤醒词"', script)
        self.assertIn('enable_group_bot_name_wakeup: "Bot 名字作为强唤醒词"', script)
        section = script.split('title: "唤醒词与节流"', 1)[1].split("},", 1)[0]
        self.assertLess(section.index('"group_wakeup_direct_words"'), section.index('"enable_group_bot_name_wakeup"'))
        self.assertLess(section.index('"enable_group_bot_name_wakeup"'), section.index('"group_wakeup_owner_direct_words"'))


if __name__ == "__main__":
    unittest.main()
