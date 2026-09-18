# -*- coding: utf-8 -*-
"""风格画像（wardrobe_style）单元测试。"""

from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.wardrobe_style import (
    MIN_REFERENCES_FOR_PROFILE,
    build_style_profile,
    collect_reference_texts,
    extract_style_profile,
    render_reference_profile,
    render_style_profile,
)

REFS = [
    {"name": "白衬衫黑纱裙", "style": "白色长袖衬衫配黑色高腰纱裙，黑色厚底玛丽珍鞋", "tags": ["日常"]},
    {"name": "黑白女仆装", "style": "黑色泡泡袖连衣裙，白色蕾丝荷叶边，白色连裤袜", "tags": ["cosplay"]},
    {"name": "白色荷叶边连衣裙", "style": "白色多层荷叶边连衣裙，白色过膝袜，白色厚底鞋", "tags": ["洛丽塔"]},
    {"name": "红金改良和服", "style": "红色改良和服，金色刺绣，木屐", "tags": ["和风"]},
]


class StyleProfileTests(unittest.TestCase):
    def test_collect_flattens_name_style_and_tags(self) -> None:
        texts = collect_reference_texts(REFS)
        self.assertEqual(len(REFS), len(texts))
        self.assertIn("白衬衫黑纱裙", texts[0])
        self.assertIn("cosplay", texts[1])

    def test_extract_counts_and_filters_rare_words(self) -> None:
        profile = extract_style_profile(collect_reference_texts(REFS), min_count=2)
        self.assertEqual(len(REFS), profile["evidence_count"])
        self.assertIn("白色", profile["color"])
        self.assertIn("黑色", profile["color"])
        # 只出现一次的词不该被当成风格
        self.assertNotIn("木屐", profile["category"])

    def test_extract_keeps_rare_words_when_threshold_is_one(self) -> None:
        profile = extract_style_profile(collect_reference_texts(REFS), min_count=1)
        self.assertIn("木屐", profile["category"])

    def test_render_needs_enough_evidence(self) -> None:
        few = REFS[: MIN_REFERENCES_FOR_PROFILE - 1]
        self.assertEqual("", render_reference_profile(few))
        line = render_reference_profile(REFS)
        self.assertTrue(line.startswith("参考风格（来自 4 套参考）"))
        self.assertIn("白色", line)

    def test_render_respects_char_budget(self) -> None:
        line = render_reference_profile(REFS, max_chars=40)
        self.assertLessEqual(len(line), 40)

    def test_render_empty_profile_is_blank(self) -> None:
        self.assertEqual("", render_style_profile({}))
        self.assertEqual("", render_style_profile(None))

    def test_build_is_pure_and_stable(self) -> None:
        first = build_style_profile(REFS)
        second = build_style_profile(REFS)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
