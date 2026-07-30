# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class ScheduleContentSanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = DailyStateMixin()
        self.harness._environment_now = lambda: datetime(2026, 7, 11, 14, 30)
        self.harness.data = {}

    def test_removes_model_scratch_speaker_and_unverified_named_companion(self) -> None:
        source = (
            "吃晚饭时，狐言与 Joris 喝了一杯热茶。她发现自己对这碗牛肉面有些许想念。 "
            "**dream_seed**: 轻微颤抖，眼中有泪光闪现。 Fox: 今天感觉不错"
        )

        cleaned = self.harness._sanitize_daily_plan_social_fact_text(source, field="detail.summary")

        self.assertEqual(cleaned, "她发现自己对这碗牛肉面有些许想念")
        self.assertNotIn("dream_seed", cleaned)
        self.assertNotIn("Fox:", cleaned)
        self.assertNotIn("Joris", cleaned)

    def test_overlong_meal_activity_describes_the_rest_of_the_window(self) -> None:
        plan = {
            "items": [
                {
                    "time": "14:30",
                    "activity": "狐言坐在桌边，心情平静地品尝着热面。",
                    "mood": "放松，平静",
                    "message_seed": "",
                },
                {
                    "time": "17:30",
                    "activity": "起身收拾桌面，准备傍晚的小事。",
                    "mood": "平稳",
                    "message_seed": "",
                },
            ]
        }

        self.assertTrue(self.harness._sanitize_daily_plan_inplace(plan))
        activity = plan["items"][0]["activity"]
        self.assertIn("这段开始时", activity)
        self.assertIn("吃完后", activity)
        self.assertIn("处理手边的事", activity)

    def test_named_person_declared_by_persona_is_preserved(self) -> None:
        self.harness.schedule_persona_prompt = "Joris 是角色设定中明确存在的同行伙伴。"
        source = "狐言与 Joris 喝了一杯热茶，随后各自收拾桌面。"

        cleaned = self.harness._sanitize_daily_plan_social_fact_text(source, field="detail.summary")

        self.assertEqual(cleaned, source)

    def test_undeclared_mother_is_removed_from_generation_context(self) -> None:
        source = "翻开物理练习册。桌上摆着妈妈刚洗的青提，后来继续把错题做完。"

        cleaned = self.harness._sanitize_generation_relationship_context(
            source,
            source="test.memory",
        )

        self.assertNotIn("妈妈", cleaned)
        self.assertIn("翻开物理练习册", cleaned)
        self.assertIn("后来继续把错题做完", cleaned)

    def test_mother_alias_is_preserved_when_identity_declares_it(self) -> None:
        self.harness.schedule_persona_prompt = "角色与母亲共同生活，母亲是明确家庭成员。"
        source = "妈妈洗了青提，她拿了几颗放在桌边。"

        cleaned = self.harness._sanitize_generation_relationship_context(source, source="test.identity")

        self.assertEqual(source, cleaned)

    def test_declared_sibling_aliases_are_preserved(self) -> None:
        self.harness.schedule_worldview_prompt = "小林是姐姐，小周是哥哥，小夏是妹妹。"
        source = "姐姐把书放回架上，哥哥在门边停了一会儿，妹妹低头看题。"

        cleaned = self.harness._sanitize_generation_relationship_context(source, source="test.siblings")

        self.assertEqual(source, cleaned)

    def test_mothers_day_is_not_treated_as_a_relationship(self) -> None:
        source = "翻到母亲节主题海报，顺手记下配色。"

        cleaned = self.harness._sanitize_generation_relationship_context(source, source="test.calendar")

        self.assertEqual(source, cleaned)

    def test_touching_a_pocket_is_not_treated_as_meeting_a_person(self) -> None:
        source = "翻到错题页时，指尖碰到口袋里的月亮发夹。"

        cleaned = self.harness._sanitize_daily_plan_social_fact_text(source, field="detail.summary")

        self.assertEqual(source, cleaned)

    def test_touching_an_object_and_later_mentioning_teacher_is_not_a_social_fact(self) -> None:
        source = (
            "刚把物理练习册塞进桌洞，跟着全班一起翻到七十四页，"
            "指尖碰到昨天咬出坑的笔帽，赶紧攥着手心藏到桌沿下，怕被老师看见说上课走神"
        )

        with patch("astrbot_plugin_private_companion.daily_state.logger.info") as info:
            cleaned = self.harness._sanitize_daily_plan_social_fact_text(
                source,
                field="detail.today_events.event",
            )

        self.assertEqual(source, cleaned)
        info.assert_not_called()

    def test_contextual_friend_role_is_not_hard_removed(self) -> None:
        source = "作为朋友把主动放轻一点，只顺手留一句。"

        cleaned = self.harness._sanitize_generation_relationship_context(
            source,
            source="test.recipient_relationship",
        )

        self.assertEqual(source, cleaned)

    def test_student_identity_does_not_imply_workplace_relationships(self) -> None:
        self.harness.schedule_persona_prompt = "职业/身份：高一学生。"
        self.harness.schedule_worldview_prompt = "现代校园。"

        declared = self.harness._daily_plan_declared_relation_tokens()

        self.assertIn("老师", declared)
        self.assertIn("同学", declared)
        self.assertNotIn("同事", declared)
        self.assertNotIn("上司", declared)

    def test_service_role_aunt_is_not_mistaken_for_family(self) -> None:
        source = "食堂阿姨把餐盘放到窗口，她端起来找了个空位。"

        cleaned = self.harness._sanitize_generation_relationship_context(source, source="test.service_role")

        self.assertEqual(source, cleaned)

    def test_explicit_user_owned_mother_context_is_preserved_without_transferring_ownership(self) -> None:
        source = "用户：我妈妈最近有点忙，提醒时不要把这层关系写成 Bot 的家庭。"

        context = self.harness._sanitize_generation_relationship_context(
            source,
            source="test.user_owned_relation",
        )
        generated_life = self.harness._sanitize_daily_plan_social_fact_text(
            "Bot 回家后和用户的妈妈一起吃饭。",
            field="activity",
        )

        self.assertEqual(source, context)
        self.assertNotIn("妈妈", generated_life)

    def test_startup_cleanup_keeps_records_but_removes_unverified_relation_clauses(self) -> None:
        self.harness.data = {
            "bot_diaries": [
                {"summary": "妈妈炖了汤，晚饭后继续看书。", "body": "坐回桌边继续写题。"}
            ],
            "creative_projects": [
                {"title": "晚饭后的书页", "source_text": "妈妈做了晚饭，后来翻开小说。"}
            ],
            "qzone_integration": {
                "recent_life_publish_texts": [
                    {"text": "桌上是妈妈洗的青提，后来把练习册翻开了。"}
                ]
            },
        }

        changed = self.harness._cleanup_generated_relationship_history_inplace()

        self.assertTrue(changed)
        self.assertEqual(1, len(self.harness.data["bot_diaries"]))
        self.assertEqual(1, len(self.harness.data["creative_projects"]))
        rendered = str(self.harness.data)
        self.assertNotIn("妈妈", rendered)
        self.assertIn("晚饭后继续看书", rendered)
        self.assertIn("后来翻开小说", rendered)

    def test_normal_meal_window_is_not_rewritten(self) -> None:
        original = "狐言静静地吃着冷面，感觉周围很安静。"
        plan = {
            "items": [
                {"time": "12:00", "activity": original, "mood": "平和", "message_seed": ""},
                {"time": "13:00", "activity": "午饭后稍作休息。", "mood": "放松", "message_seed": ""},
            ]
        }

        self.assertTrue(self.harness._sanitize_daily_plan_inplace(plan))
        self.assertEqual(plan["items"][0]["activity"], original)
        self.assertEqual(plan["items"][0]["end"], "13:00")

    def test_meal_presence_is_capped_to_detailed_event_duration(self) -> None:
        detail = {
            "summary": "狐言坐在桌边，心情平静地品尝着热面。",
            "today_events": [
                {"window": "14:30-14:45", "event": "端起碗面对镜头，微笑着享用热面", "mood": "放松"}
            ],
            "presence_status": {"mode": "custom", "custom_text": "吃面中", "duration_minutes": "180"},
        }

        changed = self.harness._sanitize_detail_snapshot_for_segment_inplace(
            detail,
            {"start": 14 * 60 + 30, "end": 17 * 60 + 30, "item": {}},
        )

        self.assertTrue(changed)
        self.assertIn("吃完后", detail["summary"])
        self.assertEqual(detail["presence_status"]["duration_minutes"], "15")

    def test_legacy_plan_gets_explicit_end_times(self) -> None:
        items = [
            {"time": "09:00", "activity": "开始上午的事情"},
            {"time": "10:15", "activity": "换到下一件事"},
        ]

        self.assertTrue(self.harness._normalize_plan_item_intervals(items))
        self.assertEqual(items[0]["end"], "10:15")
        self.assertEqual(items[1]["end"], "13:15")

    def test_explicit_end_is_preserved_and_overlap_is_capped(self) -> None:
        items = [
            {"time": "09:00", "end": "09:40", "activity": "短时活动"},
            {"time": "10:00", "end": "12:00", "activity": "下一段"},
            {"time": "11:30", "end": "12:30", "activity": "重叠段"},
        ]

        self.assertTrue(self.harness._normalize_plan_item_intervals(items))
        self.assertEqual(items[0]["end"], "09:40")
        self.assertEqual(items[1]["end"], "11:30")

    def test_schedule_lifecycle_follows_time_and_explicit_changes(self) -> None:
        self.harness._effective_plan_now_minutes = lambda _date: 10 * 60 + 30

        self.assertEqual(
            self.harness._schedule_window_runtime_status(11 * 60, 12 * 60, plan_date="2026-07-11"),
            "planned",
        )
        self.assertEqual(
            self.harness._schedule_window_runtime_status(10 * 60, 11 * 60, plan_date="2026-07-11"),
            "active",
        )
        self.assertEqual(
            self.harness._schedule_window_runtime_status(9 * 60, 10 * 60, plan_date="2026-07-11"),
            "completed",
        )
        self.assertEqual(
            self.harness._schedule_window_runtime_status(
                10 * 60,
                11 * 60,
                plan_date="2026-07-11",
                explicit_status="changed",
            ),
            "changed",
        )
        self.assertEqual(
            self.harness._schedule_window_runtime_status(
                10 * 60,
                11 * 60,
                plan_date="2026-07-11",
                explicit_status="cancelled",
            ),
            "cancelled",
        )
        self.assertEqual(
            self.harness._schedule_window_runtime_status(
                9 * 60,
                10 * 60,
                plan_date="2026-07-11",
                explicit_status="changed",
            ),
            "completed",
        )


if __name__ == "__main__":
    unittest.main()
