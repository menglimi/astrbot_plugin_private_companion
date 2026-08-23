# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _SegmentedProfileHarness(ProactiveMessageMixin, EventDispatchMixin):
    def __init__(self) -> None:
        self.persona_values: dict[str, object] = {}

    def persona_setting(self, key: str, default: object = None) -> object:
        return self.persona_values.get(key, getattr(self, key, default))

    def _feature_enabled_or_temp_unlocked(self, key: str) -> bool:
        return key == "enable_segmented_proactive_reply"


def _harness() -> _SegmentedProfileHarness:
    harness = _SegmentedProfileHarness()
    harness.enable_segmented_proactive_reply = True
    harness.enable_segmented_proactive_chat_profiles = True
    harness.segmented_proactive_chat_scope = "all"
    harness.segmented_proactive_threshold = 500
    harness.segmented_proactive_min_segment_chars = 1
    harness.segmented_proactive_max_segments = 5
    harness.segmented_proactive_split_mode = "words"
    harness.segmented_proactive_split_words = ["。"]
    harness.segmented_proactive_match_width_variants = True
    harness.segmented_proactive_regex = r".*?[。？！~…\n]+|.+$"
    harness.enable_segmented_proactive_content_cleanup = False
    harness.segmented_proactive_content_cleanup_scope = "all"
    harness.segmented_proactive_content_cleanup_rule = ""
    harness.segmented_proactive_content_cleanup_words = []
    harness.enable_segmented_proactive_content_replacement = False
    harness.segmented_proactive_content_replacements = []
    harness.segmented_proactive_interval_method = "random"
    harness.segmented_proactive_interval_min = 1.0
    harness.segmented_proactive_interval_max = 1.0
    harness.segmented_proactive_log_base = 1.8
    for chat_type in ("private", "group"):
        setattr(harness, f"segmented_proactive_{chat_type}_enabled", True)
        setattr(harness, f"segmented_proactive_{chat_type}_scope", "all_llm")
        setattr(harness, f"segmented_proactive_{chat_type}_threshold", 500)
        setattr(harness, f"segmented_proactive_{chat_type}_min_segment_chars", 1)
        setattr(harness, f"segmented_proactive_{chat_type}_max_segments", 5)
        setattr(harness, f"segmented_proactive_{chat_type}_send_as_forward", False)
        setattr(harness, f"segmented_proactive_{chat_type}_interval_method", "random")
        setattr(harness, f"segmented_proactive_{chat_type}_interval_min", 1.0)
        setattr(harness, f"segmented_proactive_{chat_type}_interval_max", 1.0)
        setattr(harness, f"segmented_proactive_{chat_type}_log_base", 1.8)
    return harness


class SegmentedChatProfileTests(unittest.TestCase):
    def test_private_and_group_can_enable_independently(self) -> None:
        harness = _harness()
        harness.segmented_proactive_group_enabled = False

        self.assertTrue(harness._segmented_chat_scope_allows("private"))
        self.assertFalse(harness._segmented_chat_scope_allows("group"))
        self.assertGreater(len(harness._split_proactive_text("一。二。", chat_type="private")), 1)
        self.assertEqual(["一。二。"], harness._split_proactive_text("一。二。", chat_type="group"))

    def test_private_and_group_use_different_segment_limits(self) -> None:
        harness = _harness()
        harness.segmented_proactive_private_max_segments = 2
        harness.segmented_proactive_group_max_segments = 4
        source = "第一段。第二段。第三段。第四段。"

        private_segments = harness._split_proactive_text(source, chat_type="private")
        group_segments = harness._split_proactive_text(source, chat_type="group")

        self.assertEqual(2, len(private_segments))
        self.assertEqual(4, len(group_segments))

    def test_private_and_group_use_different_intervals(self) -> None:
        harness = _harness()
        harness.segmented_proactive_private_interval_min = 0.7
        harness.segmented_proactive_private_interval_max = 0.7
        harness.segmented_proactive_group_interval_min = 2.4
        harness.segmented_proactive_group_interval_max = 2.4

        private_interval = asyncio.run(
            harness._calc_segmented_proactive_interval("测试", chat_type="private")
        )
        group_interval = asyncio.run(
            harness._calc_segmented_proactive_interval("测试", chat_type="group")
        )

        self.assertEqual(0.7, private_interval)
        self.assertEqual(2.4, group_interval)

    def test_legacy_chat_scope_remains_fallback(self) -> None:
        harness = _harness()
        harness.enable_segmented_proactive_chat_profiles = False
        harness.segmented_proactive_chat_scope = "private"
        harness.segmented_proactive_private_max_segments = 1

        self.assertTrue(harness._segmented_chat_scope_allows("private"))
        self.assertFalse(harness._segmented_chat_scope_allows("group"))
        self.assertEqual(5, harness._segmented_setting("max_segments", chat_type="private"))

    def test_active_persona_overrides_shared_and_chat_profile_settings(self) -> None:
        harness = _harness()
        harness.enable_segmented_proactive_chat_profiles = False
        harness.segmented_proactive_chat_scope = "private"
        harness.segmented_proactive_scope = "proactive_only"
        harness.segmented_proactive_max_segments = 5
        harness.persona_values.update(
            {
                "enable_segmented_proactive_chat_profiles": False,
                "segmented_proactive_chat_scope": "group",
                "segmented_proactive_scope": "all_llm",
                "segmented_proactive_max_segments": 2,
            }
        )

        self.assertFalse(harness._segmented_chat_scope_allows("private"))
        self.assertTrue(harness._segmented_chat_scope_allows("group"))
        self.assertEqual(
            "all_llm",
            harness._segmented_setting("scope", chat_type="group"),
        )
        self.assertEqual(2, harness._segmented_setting("max_segments", chat_type="group"))

        harness.persona_values.update(
            {
                "enable_segmented_proactive_chat_profiles": True,
                "segmented_proactive_group_enabled": False,
                "segmented_proactive_group_scope": "all_llm",
                "segmented_proactive_group_threshold": 240,
                "segmented_proactive_group_interval_min": 0.4,
            }
        )
        self.assertFalse(harness._segmented_chat_scope_allows("group"))
        self.assertEqual("all_llm", harness._segmented_setting("scope", chat_type="group"))
        self.assertEqual(240, harness._segmented_setting("threshold", chat_type="group"))
        self.assertEqual(0.4, harness._segmented_setting("interval_min", chat_type="group"))


if __name__ == "__main__":
    unittest.main()
