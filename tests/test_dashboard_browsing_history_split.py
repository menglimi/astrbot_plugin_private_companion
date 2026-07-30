# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class DashboardBrowsingHistorySplitTests(unittest.TestCase):
    def _api(self) -> PrivateCompanionPageApi:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api.plugin = SimpleNamespace(
            enable_news_integration=True,
            enable_news_boredom_read=True,
            enable_news_daily_hot_read=True,
            enable_ai_daily_watch=False,
            enable_web_exploration=True,
            enable_web_exploration_boredom_search=True,
            _news_source_items=lambda: [],
            _custom_web_exploration_search_configured=lambda: False,
            _astrbot_any_web_search_available=lambda: True,
            _format_timestamp_elapsed=lambda value: f"t{int(value)}" if value else "",
        )
        return api

    @staticmethod
    def _data() -> dict:
        return {
            "news_integration": {
                "last_read_at": 20,
                "last_status": "read",
                "last_digest": {
                    "headline": "第二条新闻",
                    "impression": "新闻摘要二",
                    "selected_source": "来源二",
                    "created_ts": 20,
                },
                "digests": [
                    {
                        "headline": "第一条新闻",
                        "impression": "新闻摘要一",
                        "selected_source": "来源一",
                        "created_ts": 10,
                    }
                ],
            },
            "web_exploration": {
                "last_explore_at": 30,
                "last_status": "explored",
                "last_digest": {
                    "query": "测试搜索",
                    "topic": "搜索结果",
                    "note": "搜索笔记",
                    "created_ts": 30,
                },
                "notes": [],
            },
        }

    def test_news_summary_only_contains_news_history(self) -> None:
        summary = self._api()._news_summary(self._data())

        self.assertEqual(summary["history_count"], 2)
        self.assertTrue(summary["history"])
        self.assertTrue(all(item["source"] == "news" for item in summary["history"]))

    def test_web_summary_excludes_news_history(self) -> None:
        summary = self._api()._web_exploration_summary(self._data())

        self.assertEqual(summary["history_count"], 1)
        self.assertEqual([item["source"] for item in summary["history"]], ["web_exploration"])


if __name__ == "__main__":
    unittest.main()
