# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.news_exploration import NewsExplorationMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class _CooldownHarness(NewsExplorationMixin):
    pass


class ExternalLinkShareCooldownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _CooldownHarness()
        self.harness.external_link_share_cooldown_hours = 72

    def test_all_legacy_source_timestamps_share_one_cooldown(self) -> None:
        now = 1_000_000.0
        fields = (
            "last_external_link_share_at",
            "last_external_link_candidate_at",
            "last_bilibili_share_at",
            "last_news_share_at",
            "last_web_exploration_share_at",
            "last_external_event_self_link_at",
        )
        for field in fields:
            with self.subTest(field=field):
                remaining = self.harness._external_link_share_cooldown_remaining(
                    {field: now - 10 * 3600},
                    now=now,
                )
                self.assertEqual(remaining, 62 * 3600)

    def test_latest_cross_source_timestamp_wins_and_expires(self) -> None:
        now = 1_000_000.0
        user = {
            "last_bilibili_share_at": now - 90 * 3600,
            "last_news_share_at": now - 24 * 3600,
        }
        self.assertEqual(
            self.harness._external_link_share_cooldown_remaining(user, now=now),
            48 * 3600,
        )
        self.assertEqual(
            self.harness._external_link_share_cooldown_remaining(
                {"last_news_share_at": now - 72 * 3600},
                now=now,
            ),
            0,
        )

    def test_zero_disables_only_the_shared_guard(self) -> None:
        self.harness.external_link_share_cooldown_hours = 0
        self.assertEqual(
            self.harness._external_link_share_cooldown_remaining(
                {"last_news_share_at": 999_999.0},
                now=1_000_000.0,
            ),
            0,
        )

    def test_web_exploration_has_only_one_candidate_path(self) -> None:
        source = inspect.getsource(NewsExplorationMixin._maybe_trigger_web_exploration)
        self.assertNotIn("_queue_web_exploration_impulses", source)
        self.assertFalse(hasattr(NewsExplorationMixin, "_queue_web_exploration_impulses"))
        self.assertEqual(source.count('"reason": "web_exploration_share"'), 1)

    def test_generic_ai_word_does_not_create_fallback_share_intent(self) -> None:
        generic = self.harness._external_event_fallback_wish(
            {"headline": "AI 大模型日报", "impression": "今天是常规更新"},
            source_type="news",
        )
        strong = self.harness._external_event_fallback_wish(
            {"headline": "GPT 多模态 agent 更新", "impression": "能力边界发生变化"},
            source_type="news",
        )
        incidental = self.harness._external_event_fallback_wish(
            {"headline": "daily routine", "impression": "普通生活记录"},
            source_type="news",
        )
        self.assertFalse(generic["should_share"])
        self.assertTrue(strong["should_share"])
        self.assertEqual(incidental["relevance"], 3)

    def test_config_default_and_save_normalization(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["external_link_share_cooldown_hours"]["default"], 72)
        self.assertEqual(
            schema["news_config"]["items"]["external_link_share_cooldown_hours"]["default"],
            72,
        )
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None
        self.assertEqual(api._normalize_setting_value("external_link_share_cooldown_hours", -1), 0)
        self.assertEqual(api._normalize_setting_value("external_link_share_cooldown_hours", 999), 168)
        self.assertEqual(api._normalize_setting_value("external_link_share_cooldown_hours", "bad"), 72)
        self.assertIn("external_link_share_cooldown_hours", api._allowed_setting_keys())


if __name__ == "__main__":
    unittest.main()
