# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin


class _AddressingHarness(GroupObservationMixin):
    bot_name = "春梦蝶"


class GroupAddressingRulesCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _AddressingHarness()

    def test_structured_addressing_precedence_in_forward_and_reverse_order(self) -> None:
        cases = [
            ({"talking_to": "bot", "text": "普通文本"}, True),
            ({"scene_trigger": "at_bot", "text": "普通文本"}, True),
            ({"scene_trigger": "group_wakeup_word", "text": "普通文本"}, True),
            ({"at_targets": [{"user_id": "bot", "is_bot": True}], "text": "普通文本"}, True),
            ({"talking_to": "member-2", "text": "春梦蝶在吗"}, False),
            ({"scene_trigger": "reply_other", "text": "bot 在吗"}, False),
            ({"at_targets": [{"user_id": "member-2", "is_bot": False}], "text": "机器人在吗"}, False),
        ]

        forward = [self.harness._group_observed_message_addresses_bot(item) for item, _ in cases]
        reverse = [self.harness._group_observed_message_addresses_bot(item) for item, _ in reversed(cases)]

        self.assertEqual([expected for _, expected in cases], forward)
        self.assertEqual([expected for _, expected in reversed(cases)], reverse)

    def test_unstructured_text_fallback_in_forward_and_reverse_order(self) -> None:
        cases = [
            ({"text": "春梦蝶出来晒太阳"}, True),
            ({"text": "BOT are you there"}, True),
            ({"text": "机器人说句话"}, True),
            ({"text": "小星在吗"}, True),
            ({"text": "大家继续聊"}, False),
            ({"text": ""}, False),
            ({}, False),
            (None, False),
        ]

        for ordered_cases in (cases, list(reversed(cases))):
            with self.subTest(order="forward" if ordered_cases is cases else "reverse"):
                self.assertEqual(
                    [expected for _, expected in ordered_cases],
                    [
                        self.harness._group_observed_message_addresses_bot(item)
                        for item, _ in ordered_cases
                    ],
                )

    def test_explicit_other_target_overrides_bot_name_text_fallback(self) -> None:
        message = {
            "talking_to": "member-2",
            "scene_trigger": "at_other",
            "at_targets": [{"user_id": "member-2", "is_bot": False}],
            "text": "春梦蝶，你觉得群友乙说得对吗",
        }

        self.assertFalse(self.harness._group_observed_message_addresses_bot(message))


if __name__ == "__main__":
    unittest.main()
