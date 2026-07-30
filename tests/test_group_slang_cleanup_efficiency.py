# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin


class _GroupSlangCleanupHarness(GroupObservationMixin):
    def __init__(self) -> None:
        self.name_token_calls = 0
        self.data = {
            "groups": {
                "10001": {"slang_terms": [{"term": f"词{index}"} for index in range(159)] + [{"term": "成员甲"}]},
                "10002": {"slang_terms": [{"term": f"梗{index}"} for index in range(160)]},
            }
        }

    def _group_member_name_tokens(self, _group) -> set[str]:
        self.name_token_calls += 1
        return {"成员甲"}


class _GroupSlangLearningHarness(GroupObservationMixin):
    def __init__(self) -> None:
        self.max_group_slang_terms = 40

    @staticmethod
    def _group_text_blocked_by_injection_guard(_text: str) -> bool:
        return False

    @staticmethod
    def _group_member_name_tokens(_group) -> set[str]:
        return set()


class GroupSlangCleanupEfficiencyTests(unittest.TestCase):
    def test_member_name_index_is_built_once_per_group(self) -> None:
        harness = _GroupSlangCleanupHarness()

        changed = harness._cleanup_all_group_slang_terms()

        self.assertTrue(changed)
        self.assertEqual(harness.name_token_calls, 2)
        remaining = harness.data["groups"]["10001"]["slang_terms"]
        self.assertNotIn("成员甲", {item["term"] for item in remaining})

    def test_transport_metadata_is_removed_even_with_saved_meaning(self) -> None:
        harness = _GroupSlangCleanupHarness()
        group = {
            "slang_terms": [
                {"term": "引用消息", "count": 573},
                {"term": "MSG_ID", "count": 502},
                {"term": "Reply", "count": 169},
                {"term": "share_source", "count": 16},
                {"term": "share_medium", "count": 15},
                {"term": "NeonBot", "count": 199},
            ],
            "slang_meanings": {
                "Reply": {"source": "manual", "meaning": "旧协议字段"},
                "NeonBot": {"source": "manual", "meaning": "群机器人"},
            },
        }

        self.assertTrue(harness._cleanup_group_slang_terms(group))
        self.assertEqual({item["term"] for item in group["slang_terms"]}, {"NeonBot"})
        self.assertNotIn("Reply", group["slang_meanings"])
        self.assertIn("NeonBot", group["slang_meanings"])

    def test_transport_metadata_is_not_learned_as_group_slang(self) -> None:
        harness = _GroupSlangLearningHarness()
        group = {"slang_terms": []}

        harness._learn_group_slang(
            group,
            "引用消息 MSG_ID Reply share_source share_medium message_id quoted_message_id NeonBot",
        )

        self.assertEqual({item["term"] for item in group["slang_terms"]}, {"NeonBot"})

    def test_long_chinese_sentence_is_not_split_into_fake_slang(self) -> None:
        harness = _GroupSlangLearningHarness()
        group = {"slang_terms": []}

        harness._learn_group_slang(
            group,
            "我这实在是太热了，能不能申请高温补贴领个小风扇",
        )

        self.assertEqual(group["slang_terms"], [])

    def test_url_and_common_technical_terms_are_not_learned(self) -> None:
        harness = _GroupSlangLearningHarness()
        group = {"slang_terms": []}

        harness._learn_group_slang(
            group,
            "https://www.bilibili.com/video/b23 gpt qwen Gemini api token NeonBot",
        )

        self.assertEqual({item["term"] for item in group["slang_terms"]}, {"NeonBot"})

    def test_repeated_short_expression_is_promoted_after_two_observations(self) -> None:
        harness = _GroupSlangLearningHarness()
        group = {"slang_terms": [], "slang_meanings": {}}

        harness._learn_group_slang(group, "求放过")
        item = group["slang_terms"][0]
        self.assertFalse(harness._group_slang_term_is_promoted(group, item))

        harness._learn_group_slang(group, "求放过")
        item = group["slang_terms"][0]
        self.assertEqual(item["count"], 2)
        self.assertTrue(harness._group_slang_term_is_promoted(group, item))


if __name__ == "__main__":
    unittest.main()
