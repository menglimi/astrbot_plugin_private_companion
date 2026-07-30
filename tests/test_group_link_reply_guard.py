# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.group_wakeup import GroupWakeupMixin
from astrbot_plugin_private_companion.forward_message import ForwardMessageMixin
from astrbot_plugin_private_companion.helpers import _group_link_message_context


class _InterjectionHarness(GroupObservationMixin):
    enable_group_interjection = True


class _WakeupHarness(GroupWakeupMixin):
    enable_group_wakeup_enhancement = True


class _QuotedLinkHarness(ForwardMessageMixin):
    def __init__(self, rows):
        self.rows = rows

    @staticmethod
    def _event_has_reply_component(event) -> bool:
        return True

    async def _reply_message_chain_for_event(self, event, *, max_depth=3):
        return self.rows[:max_depth]


class _WakeEvent:
    is_wake = True

    def get_sender_id(self) -> str:
        raise AssertionError("wakeup messages must not enter the proactive interjection path")


class GroupLinkReplyGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.interjection = _InterjectionHarness()
        self.wakeup = _WakeupHarness()
        self.wakeup.bot_name = ""
        self.wakeup.group_wakeup_direct_words = []

    def test_url_query_question_mark_is_not_user_question(self) -> None:
        url = "https://www.smzdm.com/p/test-item/?from=other&invite_code=test"
        non_link_text, has_link = _group_link_message_context(url)

        self.assertTrue(has_link)
        self.assertEqual(non_link_text, "")
        self.assertEqual(self.wakeup._group_wakeup_question_signal(url), {})

    def test_bare_link_and_share_card_cannot_trigger_interjection(self) -> None:
        for text in (
            "https://example.com/item?id=1",
            "[分享] https://example.com/item?id=1",
            "【QQ小程序】商品分享",
        ):
            allowed, reason = self.interjection._group_interjection_allowed({}, text)
            self.assertFalse(allowed)
            self.assertIn("链接或分享", reason)

    def test_bare_link_cannot_trigger_other_group_wakeup_sources(self) -> None:
        wakeup = self.wakeup._evaluate_group_wakeup(
            {},
            scene={"talking_to": "group", "trigger": "normal"},
            sender_id="10001",
            sender_name="群友",
            text="https://example.com/bot/interesting-topic?id=1",
            group_id="20001",
        )

        self.assertEqual(wakeup, {})

    def test_qq_compatibility_boilerplate_is_treated_as_share_payload(self) -> None:
        for text in (
            "当前QQ版本不支持此应用，请升级",
            "当前 QQ 版本不支持该应用, 请升级",
            "您的QQ版本过低，请升级后查看",
            "请使用最新版本手机QQ查看",
        ):
            non_link_text, has_link = _group_link_message_context(text)

            self.assertTrue(has_link)
            self.assertEqual(non_link_text, "")

    def test_normal_qq_version_discussion_is_not_treated_as_share_payload(self) -> None:
        text = "我的QQ版本过低，怎么升级？"
        non_link_text, has_link = _group_link_message_context(text)

        self.assertFalse(has_link)
        self.assertEqual(non_link_text, text)

    def test_qq_compatibility_boilerplate_cannot_trigger_interest_wakeup(self) -> None:
        self.wakeup.group_wakeup_interest_keywords = ["升级"]

        wakeup = self.wakeup._evaluate_group_wakeup(
            {},
            scene={"talking_to": "group", "trigger": "normal"},
            sender_id="10001",
            sender_name="群友",
            text="当前QQ版本不支持此应用，请升级",
            group_id="20001",
        )

        self.assertEqual(wakeup, {})

    def test_question_beside_qq_boilerplate_keeps_only_user_text(self) -> None:
        text = "这个视频讲什么？ 当前QQ版本不支持此应用，请升级"
        non_link_text, has_link = _group_link_message_context(text)

        self.assertTrue(has_link)
        self.assertEqual(non_link_text, "这个视频讲什么？")
        signal = self.wakeup._group_wakeup_question_signal(text)
        self.assertTrue(signal)
        self.assertEqual(signal.get("raw_text"), "这个视频讲什么？")

    def test_qq_compatibility_boilerplate_cannot_trigger_interjection(self) -> None:
        allowed, reason = self.interjection._group_interjection_allowed(
            {}, "当前QQ版本不支持此应用，请升级"
        )

        self.assertFalse(allowed)
        self.assertIn("链接或分享", reason)

    def test_question_beside_link_does_not_wake_without_addressing_bot(self) -> None:
        wakeup = self.wakeup._evaluate_group_wakeup(
            {},
            scene={"talking_to": "group", "trigger": "normal"},
            sender_id="10001",
            sender_name="群友",
            text="这个测试商品值得买吗？ https://www.smzdm.com/p/test-item/?from=other",
            group_id="20001",
        )

        self.assertEqual(wakeup, {})

    def test_direct_bot_name_can_still_override_current_link_guard(self) -> None:
        self.wakeup.bot_name = "测试角色"
        self.wakeup.group_wakeup_direct_words = []
        wakeup = self.wakeup._evaluate_group_wakeup(
            {},
            scene={"talking_to": "group", "trigger": "normal"},
            sender_id="10001",
            sender_name="群友",
            text="测试角色看看这个 https://example.com/item",
            group_id="20001",
        )

        self.assertEqual(wakeup.get("type"), "direct_word")

    async def test_quoted_plain_link_is_detected(self) -> None:
        harness = _QuotedLinkHarness(
            [{"raw_message": "https://example.com/item?id=1", "text": "https://example.com/item?id=1"}]
        )
        self.assertTrue(await harness._event_reply_contains_link_payload(object()))

    async def test_quoted_share_card_link_is_detected_but_image_only_is_not(self) -> None:
        card = _QuotedLinkHarness(
            [{"raw_message": {"type": "json", "data": {"title": "商品", "jumpUrl": "https://example.com/item"}}, "text": "商品"}]
        )
        image = _QuotedLinkHarness(
            [{"raw_message": {"type": "image", "data": {"url": "https://cdn.example.com/a.jpg"}}, "text": "[图片]"}]
        )

        self.assertTrue(await card._event_reply_contains_link_payload(object()))
        self.assertFalse(await image._event_reply_contains_link_payload(object()))

    def test_quoted_link_scene_cannot_trigger_question_wakeup(self) -> None:
        wakeup = self.wakeup._evaluate_group_wakeup(
            {},
            scene={"talking_to": "group", "trigger": "normal", "quoted_link_payload": True},
            sender_id="10001",
            sender_name="群友",
            text="这个值得买吗？",
            group_id="20001",
        )

        self.assertEqual(wakeup, {})

    async def test_wakeup_message_does_not_compete_with_interjection(self) -> None:
        await self.interjection._maybe_group_interject(_WakeEvent(), {}, "有没有人知道这个怎么弄？")


if __name__ == "__main__":
    unittest.main()
