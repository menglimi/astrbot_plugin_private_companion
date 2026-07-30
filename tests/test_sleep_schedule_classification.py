# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class SleepScheduleClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = DailyStateMixin()

    def test_awake_leisure_is_not_sleep(self) -> None:
        awake_items = [
            {"activity": "摸鱼休息一下下", "mood": "慵懒", "message_seed": "歇会儿啦"},
            {"activity": "躺在沙发上发呆", "mood": "有点困"},
            {"activity": "收工放松", "mood": "疲倦", "message_seed": "终于能休息会了"},
            {"activity": "午睡醒来喝水", "mood": "刚醒"},
            {"activity": "失眠睡不着", "mood": "清醒"},
        ]

        for item in awake_items:
            with self.subTest(item=item):
                self.assertFalse(self.harness._is_sleepy_plan_item(item))

    def test_explicit_sleep_segments_are_sleep(self) -> None:
        sleeping_items = [
            {"activity": "干饭后午休时间", "mood": "安静"},
            {"activity": "准备睡觉觉", "mood": "困"},
            {"activity": "回被窝补觉", "mood": "迷糊"},
            {"activity": "已经进入浅睡", "mood": "平稳"},
            {"activity": "眯一会儿", "mood": "放松"},
        ]

        for item in sleeping_items:
            with self.subTest(item=item):
                self.assertTrue(self.harness._is_sleepy_plan_item(item))

    def test_cleanup_removes_only_false_sleep_interaction(self) -> None:
        false_source = "睡眠中被用户唤醒"
        user_text = "才几点就想着吃晚饭"
        reaction = "她会先带着睡意看一眼消息"
        self.harness.data = {
            "daily_plan": {"items": []},
            "detail_enhanced_segments": {
                "seg": {
                    "summary": f"沙发摸鱼；用户介入后：{reaction}",
                    "interaction_updates": [
                        {"source": false_source, "user_text": user_text, "reaction": reaction, "state_updates": ["清醒程度：刚被唤醒/迷糊"]},
                        {"source": "用户帮助", "user_text": "试试这个", "reaction": "", "state_updates": ["进度：已推进"]},
                    ],
                    "state_variables": [
                        {"name": "清醒程度", "value": "迷糊", "note": "用户介入：刚被唤醒/迷糊"},
                        {"name": "进度", "value": "已推进", "note": "用户介入：已推进"},
                    ],
                }
            },
            "schedule_adjustments": [
                {"source": false_source, "user_text": user_text},
                {"source": "用户帮助", "user_text": "试试这个"},
            ],
            "daily_state": {"sleep_runtime": {"phase": "woken", "last_user_text": user_text}},
        }
        self.harness._collect_detail_segments = lambda _plan, _extra: [
            {"key": "seg", "item": {"activity": "摸鱼休息一下下", "mood": "慵懒"}}
        ]

        self.assertTrue(self.harness._cleanup_false_sleep_interaction_updates())

        snapshot = self.harness.data["detail_enhanced_segments"]["seg"]
        self.assertEqual([item["source"] for item in snapshot["interaction_updates"]], ["用户帮助"])
        self.assertEqual([item["name"] for item in snapshot["state_variables"]], ["进度"])
        self.assertEqual(len(self.harness.data["schedule_adjustments"]), 1)
        self.assertEqual(self.harness.data["daily_state"]["sleep_runtime"]["phase"], "awake")
        self.assertNotIn("用户介入后", snapshot["summary"])


if __name__ == "__main__":
    unittest.main()
