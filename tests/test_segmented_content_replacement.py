# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _SegmentReplacementHarness(ProactiveMessageMixin, EventDispatchMixin):
    def __init__(self) -> None:
        self.enable_segmented_proactive_reply = True
        self.segmented_proactive_threshold = 500
        self.segmented_proactive_min_segment_chars = 1
        self.segmented_proactive_max_segments = 6
        self.segmented_proactive_split_mode = "regex"
        self.segmented_proactive_regex = r".*?[。？！~…\n]+|.+$"
        self.segmented_proactive_split_words = ["。", "？", "！", "~", "…"]
        self.enable_segmented_proactive_content_cleanup = False
        self.segmented_proactive_content_cleanup_scope = "all"
        self.segmented_proactive_content_cleanup_rule = r"[\n]"
        self.segmented_proactive_content_cleanup_words = ["\n"]
        self.enable_segmented_proactive_content_replacement = True
        self.segmented_proactive_content_replacements = []

    @staticmethod
    def _feature_enabled_or_temp_unlocked(key: str) -> bool:
        return key == "enable_segmented_proactive_reply"


class SegmentedContentReplacementTests(unittest.TestCase):
    def test_replaces_content_before_segmenting(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.segmented_proactive_content_replacements = [
            "主人 => 测试用户",
            "晚安 => 睡个好觉",
        ]

        segments = harness._split_proactive_text("主人你好。主人晚安。")

        self.assertEqual(segments, ["测试用户你好。", "测试用户睡个好觉。"])

    def test_replacement_keeps_urls_and_media_tags_unchanged(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.segmented_proactive_content_replacements = ["bilibili => example", "主人 => 大人"]
        source = (
            "主人看这个。https://www.bilibili.com/video/BV123。"
            '<image url="https://img.example/bilibili.png">主人</image>'
        )

        replaced, count = harness._apply_segmented_content_replacements(source)

        self.assertEqual(count, 1)
        self.assertIn("大人看这个", replaced)
        self.assertIn("https://www.bilibili.com/video/BV123", replaced)
        self.assertIn(
            '<image url="https://img.example/bilibili.png">主人</image>',
            replaced,
        )

    def test_replacement_keeps_common_filenames_unchanged(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.segmented_proactive_content_replacements = [
            "jpg => txt",
            "请发送 => 麻烦发送",
        ]

        replaced, count = harness._apply_segmented_content_replacements(
            "请发送对象.jpg，普通jpg，截图.PNG"
        )

        self.assertEqual(replaced, "麻烦发送对象.jpg，普通txt，截图.PNG")
        self.assertEqual(count, 2)

    def test_replacement_rules_support_arrow_dict_and_empty_alias(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.segmented_proactive_content_replacements = [
            "旧称 → 新称",
            {"from": "删掉我", "to": "<empty>"},
        ]

        replaced, count = harness._apply_segmented_content_replacements("旧称，删掉我，旧称")

        self.assertEqual(replaced, "新称，，新称")
        self.assertEqual(count, 3)

    def test_disabled_replacement_keeps_original(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.enable_segmented_proactive_content_replacement = False
        harness.segmented_proactive_content_replacements = ["主人 => 大人"]

        replaced, count = harness._apply_segmented_content_replacements("主人你好")

        self.assertEqual(replaced, "主人你好")
        self.assertEqual(count, 0)

    def test_full_message_deletion_is_not_allowed_to_silence_delivery(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.segmented_proactive_content_replacements = ["整条消息 => <empty>"]

        segments = harness._split_proactive_text("整条消息")

        self.assertEqual(segments, ["整条消息"])

    def test_line_broken_parenthesis_suffix_rejoins_without_comma(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.enable_segmented_proactive_content_cleanup = True
        harness.segmented_proactive_min_segment_chars = 8

        cases = (
            ("嗯，被捏得软绵绵的\n（", "嗯，被捏得软绵绵的（"),
            ("嗯，被捏得软绵绵的\n（笑）", "嗯，被捏得软绵绵的（笑）"),
            ("嗯，被捏得软绵绵的\n(｡･ω･｡)", "嗯，被捏得软绵绵的(｡･ω･｡)"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(harness._split_proactive_text(source), [expected])

    def test_normal_short_segment_merge_still_adds_comma(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.enable_segmented_proactive_content_cleanup = True

        segments = harness._split_proactive_text("嗯\n被捏得软绵绵的")

        self.assertEqual(segments, ["嗯，被捏得软绵绵的"])

    def test_leading_ellipsis_is_preserved_when_short_segments_merge(self) -> None:
        harness = _SegmentReplacementHarness()
        harness.segmented_proactive_split_mode = "words"
        harness.segmented_proactive_split_words = ["。", "？", "！", "~", "…"]
        harness.segmented_proactive_match_width_variants = True
        harness.enable_segmented_proactive_content_cleanup = True
        harness.segmented_proactive_content_cleanup_scope = "trailing"
        harness.segmented_proactive_content_cleanup_words = ["\n"]
        harness.segmented_proactive_min_segment_chars = 8

        for prefix in ("……", "......"):
            with self.subTest(prefix=prefix):
                segments = harness._split_proactive_text(f"{prefix}被你抓到了。那次确实偷懒了。")
                self.assertEqual(segments[0], f"{prefix}被你抓到了，那次确实偷懒了。")

    def test_configuration_and_preview_expose_replacement_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        schema = (root / "_conf_schema.json").read_text(encoding="utf-8")

        self.assertIn('title: "内容替换"', script)
        self.assertIn("applySegmentedPreviewReplacements", script)
        self.assertIn('"enable_segmented_proactive_content_replacement"', schema)
        self.assertIn('"segmented_proactive_content_replacements"', schema)


if __name__ == "__main__":
    unittest.main()
