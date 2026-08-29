# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


ROOT = Path(__file__).resolve().parents[1]


class ProactiveSourceLocalizationTests(unittest.TestCase):
    def test_backend_labels_current_proactive_sources(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        expected = {
            "environment_change": "环境突变",
            "meal_care": "饭点关心",
            "group_ignore_complaint": "群内冒泡关心",
            "post_goodnight_group_activity": "晚安后群聊活跃",
            "bookshelf_reading": "资料归档",
            "personal_goal": "个人目标",
        }

        self.assertEqual(
            {key: api._proactive_source_label(key) for key in expected},
            expected,
        )
        self.assertEqual(api._proactive_source_label("jm_cosmos"), "jm_cosmos")

    def test_candidate_summary_returns_labels_for_chart_keys(self) -> None:
        plugin = SimpleNamespace(_format_timestamp_elapsed=lambda _value: "刚刚")
        api = PrivateCompanionPageApi(plugin)
        now = time.time()

        summary = api._proactive_candidate_summary(
            {
                "users": {},
                "proactive_candidate_pool": [
                    {
                        "id": "environment-change-1",
                        "user_id": "10001",
                        "source": "environment_change",
                        "reason": "environment_change",
                        "action": "message",
                        "status": "sent",
                        "topic": "外面开始下雨",
                        "created_ts": now,
                        "scheduled_ts": now,
                    }
                ],
            }
        )

        self.assertEqual(summary["source_counts"], {"environment_change": 1})
        self.assertEqual(summary["source_labels"], {"environment_change": "环境突变"})

    def test_chart_prefers_backend_labels_and_keeps_local_fallbacks(self) -> None:
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            "labelFormatter: (label) => sourceLabels[label] || proactiveCandidateSourceLabel(label)",
            script,
        )
        for key, label in {
            "environment_change": "环境突变",
            "meal_care": "饭点关心",
            "group_ignore_complaint": "群内冒泡关心",
            "post_goodnight_group_activity": "晚安后群聊活跃",
            "bookshelf_reading": "资料归档",
            "personal_goal": "个人目标",
        }.items():
            self.assertIn(f'{key}: "{label}"', script)
        self.assertNotIn('jm_cosmos: "私密阅读"', script)


if __name__ == "__main__":
    unittest.main()
