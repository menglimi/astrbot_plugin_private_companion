# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.photo_nai_params import (
    cache_user_photo_nai_params,
    extract_user_photo_nai_params,
    merge_user_photo_nai_params,
    recent_cached_photo_nai_params,
)


class PhotoNaiParamsTests(unittest.TestCase):
    def test_new_message_without_tags_clears_previous_params(self) -> None:
        user = {}
        tags = "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes"
        cache_user_photo_nai_params(user, tags, received_at=100.0)
        self.assertEqual(recent_cached_photo_nai_params(user, now=101.0), tags)
        cache_user_photo_nai_params(user, "Draw a cup of coffee", received_at=102.0)
        self.assertEqual(recent_cached_photo_nai_params(user, now=103.0), "")

    def test_extracts_normalized_explicit_tag_block(self) -> None:
        text = "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes"
        self.assertEqual(
            extract_user_photo_nai_params(text),
            "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes",
        )

    def test_cached_params_expire(self) -> None:
        user = {"last_photo_nai_params": "masterpiece, best quality", "last_photo_nai_params_at": 100.0}
        self.assertEqual(recent_cached_photo_nai_params(user, now=100 + 60), "masterpiece, best quality")
        self.assertEqual(recent_cached_photo_nai_params(user, now=100 + 2 * 60 * 60 + 1), "")

    def test_character_markers_do_not_truncate_user_params(self) -> None:
        tags = "masterpiece, best quality, 2girls, room, table, window, soft light, {人物[green hair, smile]人物}"
        self.assertEqual(extract_user_photo_nai_params(tags), tags)

    def test_merge_only_changes_nai_mode_and_preserves_tags(self) -> None:
        inherited = "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes"
        content = "{人物}, [dramatic lighting], {blue eyes}"
        self.assertEqual(
            merge_user_photo_nai_params(content, inherited, prompt_format="nai"),
            "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes, [dramatic lighting], {blue eyes}",
        )
        traditional = "Positive prompt: portrait\nNegative prompt: blurry"
        self.assertEqual(merge_user_photo_nai_params(traditional, inherited, prompt_format="traditional"), traditional)
        self.assertEqual(merge_user_photo_nai_params(content, inherited, prompt_format="natural_language"), content)

    def test_weights_and_character_groups_are_not_split(self) -> None:
        user_tags = "masterpiece, best quality, 1girl, green hair, sitting, bedroom, soft light, detailed eyes"
        model_tags = "1girl, 1.5::green hair, soft light::, {人物[green hair, detailed eyes]人物}"
        merged = merge_user_photo_nai_params(model_tags, user_tags, prompt_format="nai")
        self.assertEqual(merged, user_tags + ", 1.5::green hair, soft light::, {人物[green hair, detailed eyes]人物}")

    def test_invalid_or_legacy_cache_timestamp_is_ignored(self) -> None:
        for stamp in (None, "bad", "nan", "inf", -1, 200):
            with self.subTest(stamp=stamp):
                user = {"last_photo_nai_params": "cached tags", "last_photo_nai_params_at": stamp}
                self.assertEqual(recent_cached_photo_nai_params(user, now=100.0), "")


if __name__ == "__main__":
    unittest.main()
