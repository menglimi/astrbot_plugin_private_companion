# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _HabitHarness(UserMemoryMixin):
    def __init__(self):
        self.enable_user_habit_learning = True
        self.user_habit_min_count = 3
        self.user_habit_max_items = 24


class UserHabitQualityTests(unittest.TestCase):
    def setUp(self):
        self.harness = _HabitHarness()

    def test_system_and_forward_placeholders_are_not_habits(self):
        for text in (
            "我转发了一段聊天记录,你看看里面在说什么。",
            "bili_live_probe 3529488",
            "bili状态",
            "私聊告诉测试用户摸摸",
        ):
            self.assertEqual(self.harness._classify_user_habit_message(text), ("", "", ""))

    def test_one_off_chat_and_questions_are_not_habits(self):
        for text in ("嗯", "喵", "笨", "这是谁？", "夹层密码是？", "才几点就想着吃晚饭"):
            self.assertEqual(self.harness._classify_user_habit_message(text), ("", "", ""))

    def test_self_reported_routine_and_interaction_ritual_are_classified(self):
        self.assertEqual(self.harness._classify_user_habit_message("我还没吃晚饭")[0], "饮食节奏")
        self.assertEqual(self.harness._classify_user_habit_message("刚睡醒呢")[0], "作息节奏")
        self.assertEqual(self.harness._classify_user_habit_message("摸摸摸摸")[0], "互动习惯")
        self.assertEqual(self.harness._classify_user_habit_message("在做什么呢")[0], "互动习惯")

    def test_repeated_messages_need_cross_day_evidence(self):
        now = time.time()
        base = {
            "category": "互动习惯",
            "topic": "亲昵互动",
            "count": 12,
            "last_seen_ts": now,
            "avg_minute": 1200,
        }
        user = {"behavior_habits": {"patterns": [{**base, "evidence_days": ["2026-07-11"]}]}}
        self.assertEqual(self.harness._qualified_user_behavior_habits(user), [])
        user["behavior_habits"]["patterns"][0]["evidence_days"] = ["2026-07-09", "2026-07-10", "2026-07-11"]
        self.assertEqual(len(self.harness._qualified_user_behavior_habits(user)), 1)

    def test_legacy_count_only_records_are_removed_from_pool(self):
        user = {
            "behavior_habits": {
                "patterns": [
                    {"category": "娱乐习惯", "topic": "娱乐/刷内容", "count": 99},
                    {"category": "聊天话题", "topic": "嗯", "count": 99},
                ]
            }
        }
        self.assertTrue(self.harness._sanitize_user_behavior_habit_patterns(user))
        self.assertEqual(user["behavior_habits"]["patterns"], [])


if __name__ == "__main__":
    unittest.main()
