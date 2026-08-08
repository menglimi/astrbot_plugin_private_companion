# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
import unittest
from datetime import datetime
from unittest.mock import patch

from astrbot_plugin_private_companion.planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    daily_plan_completion_budget,
    detail_target_event_count,
    detail_payload_quality_issues,
    evaluate_daily_plan_quality,
    generate_daily_plan,
    normalize_detail_location,
)
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.helpers import _today_key


class PlanningPromptHarness(DailyStateMixin):
    daily_plan_prompt = ""
    schedule_persona_prompt = "SCHEDULE_PERSONA"
    schedule_worldview_prompt = "SCHEDULE_WORLDVIEW"
    bot_name = "测试角色"
    daily_plan_item_count = 8
    enable_maslow_motivation_experiment = False
    enable_maslow_schedule_influence = False
    allow_screen_peek_action = True
    allow_voice_action = True
    data = {
        "daily_state": {"dream": "DREAM_BODY_SHOULD_NOT_APPEAR"},
        "daily_weather": {},
    }

    def _get_default_persona_prompt(self):
        return "DEFAULT_PERSONA"

    def _format_roleplay_knowledge_context(self, **_kwargs):
        return "ROLEPLAY_KNOWLEDGE"

    def _format_worldview_adaptation_prompt(self):
        return "WORLDVIEW_ADAPTATION"

    def _format_persona_voice_channel_prompt(self, channel):
        return f"VOICE_{channel.upper()}"

    def _format_can_do_for_prompt(self):
        return "CAN_DO"

    def _format_state_for_prompt(self, _state, *, include_dream=True):
        return "STATE_WITH_DREAM" if include_dream else "STATE_WITHOUT_DREAM"

    def _recent_diary_context(self):
        return "RECENT_DIARY"

    def _format_yesterday_conversation_summary_for_prompt(self):
        return "YESTERDAY_CONVERSATION"

    def _format_yesterday_screen_diary_context_for_prompt(self):
        return "YESTERDAY_SCREEN"

    def _weather_summary_text(self, _weather):
        return "WEATHER"

    def _format_calendar_context_for_prompt(self):
        return "CALENDAR"

    def _format_schedule_adjustments_for_prompt(self, segment=None):
        return "TODAY_ADJUSTMENT"

    def _format_recent_daily_plan_history_for_prompt(self):
        return "PLAN_HISTORY"

    def _format_skill_growth_schedule_context(self):
        return "SKILL_BOUNDARY"

    def _format_all_user_behavior_habits_for_schedule(self):
        return "USER_HABITS"

    def _format_important_dates_for_prompt(self):
        return "IMPORTANT_DATES"

    def _minutes_to_hhmm(self, value):
        value = int(value) % (24 * 60)
        return f"{value // 60:02d}:{value % 60:02d}"

    def _photo_text_planning_available(self):
        return False

    def _format_plan_item_for_prompt(self, item):
        return f"{item.get('time', '')} {item.get('activity', '')}"

    def _format_state_continuity_for_prompt(self, _state):
        return "STATE_CONTINUITY"

    def _format_proactive_ability_search_hint(self):
        return "ABILITY_MENU"

    def _format_content_choice_options_for_prompt(self):
        return "CONTENT_MENU"

    def _normalize_story_items(self, items, _text_key):
        return list(items or [])

    def _segment_end_minutes(self, start, _item):
        return start + 180

    def _is_sleepy_plan_item(self, _item):
        return False

    def _parse_window_minutes(self, value):
        match = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", str(value or ""))
        if not match:
            return None, None
        return int(match.group(1)) * 60 + int(match.group(2)), int(match.group(3)) * 60 + int(match.group(4))

    def _parse_hhmm_to_minutes(self, value):
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
        return int(match.group(1)) * 60 + int(match.group(2)) if match else None

    def _plan_has_excess_micro_segments(self, _items):
        return False

    def _plan_has_excess_abstract_segments(self, _items):
        return False

    def _plan_conflicts_with_calendar(self, _items):
        return False

    def _plan_is_too_repetitive(self, _items):
        return False

    def _schedule_text_is_single_meal_action(self, text):
        return "吃面" in str(text or "") and "之后" not in str(text or "")

    def _sanitize_schedule_model_artifacts(self, text, *, limit=180):
        source = str(text or "")
        if "dream_seed" in source:
            source = source.split("dream_seed", 1)[0].rstrip(" *:：")
        return source[:limit]


class DailyPlanGenerationHarness:
    daily_plan_provider_id = "daily-provider"
    mai_style_provider_id = ""
    llm_provider_id = "default-provider"
    daily_plan_item_count = 8

    def __init__(self, responses, parsed, *, calendar_conflict=False, previous_plan=None):
        self.responses = list(responses)
        self.parsed = dict(parsed)
        self.calendar_conflict = calendar_conflict
        self.calls = []
        self.data = {"daily_plan": dict(previous_plan or {})}

    async def _ensure_weather_context(self):
        return None

    def _build_daily_plan_prompt(self, _now, *, memory_companion_context=""):
        return "DAILY_PLAN_PROMPT"

    def _task_provider(self, *provider_ids):
        return next((item for item in provider_ids if item), "")

    async def _llm_call(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.responses.pop(0)

    def _parse_plan_items(self, raw):
        return [dict(item) for item in self.parsed.get(raw, [])]

    def _plan_has_excess_micro_segments(self, _items):
        return False

    def _plan_has_excess_abstract_segments(self, _items):
        return False

    def _plan_conflicts_with_calendar(self, _items):
        return self.calendar_conflict

    def _plan_is_too_repetitive(self, _items):
        return False

    def _normalize_plan_item_intervals(self, _items):
        return None

    def _remember_daily_plan_history(self, plan):
        self.remembered_plan = plan


class DailyPlanRetryHarness(DailyStateMixin):
    enable_daily_plan = True
    daily_plan_time = "00:00"

    def __init__(self, retry_after):
        self.generated_count = 0
        self._data_lock = asyncio.Lock()
        self.data = {
            "users": {"10001": {"umo": "default:FriendMessage:10001"}},
            "daily_plan": {
                "date": _today_key(),
                "source": "fallback_previous_plan",
                "retry_after": retry_after,
                "items": [{"time": "09:00", "end": "10:00", "activity": "旧的个性化日程"}],
            },
        }

    async def _ensure_daily_state(self, **_kwargs):
        return {}

    def _environment_now(self):
        return datetime.now()

    def _sync_detail_enhancement_day_locked(self, _date, reset=False):
        return False

    def _sanitize_daily_plan_inplace(self, _plan):
        return False

    def _refresh_daily_state_location_from_plan(self, **_kwargs):
        return None

    def _save_data_sync(self):
        return None

    async def _generate_daily_plan(self):
        self.generated_count += 1
        return {
            "date": _today_key(),
            "source": "llm",
            "items": [{"time": "10:00", "end": "11:00", "activity": "重新生成的日程"}],
        }

    async def _ensure_daily_news_reading(self, **_kwargs):
        return None


class PlanningReferenceSourceTests(unittest.TestCase):
    def setUp(self):
        self.plugin = PlanningPromptHarness()

    def test_daily_plan_completion_budget_scales_with_item_count(self):
        self.plugin.daily_plan_item_count = 10
        self.assertEqual(daily_plan_completion_budget(self.plugin), 1500)

        self.plugin.daily_plan_item_count = 24
        self.assertEqual(daily_plan_completion_budget(self.plugin), 3180)
        self.assertGreater(daily_plan_completion_budget(self.plugin), 1500)

    def test_daily_plan_prompt_mentions_compact_high_count_output(self):
        self.plugin.daily_plan_item_count = 24

        prompt = build_daily_plan_prompt(self.plugin, "2026-07-11 09:00")

        self.assertIn("超过 12 段", prompt)
        self.assertIn("完整、可解析的 JSON", prompt)

    def test_schedule_prompts_do_not_override_configured_gender_or_pronoun(self):
        prompt = build_daily_plan_prompt(self.plugin, "2026-07-11 09:00")
        detail_prompt = build_detail_enhancement_prompt(
            self.plugin,
            {
                "start": 15 * 60,
                "end": 16 * 60,
                "item": {"time": "15:00", "activity": "整理桌面"},
            },
            {"items": [{"time": "15:00", "activity": "整理桌面"}]},
            self.plugin.data["daily_state"],
        )

        for generated_prompt in (prompt, detail_prompt):
            self.assertIn("严格服从", generated_prompt)
            self.assertIn("中性、无性别", generated_prompt)
            self.assertIn("它/TA", generated_prompt)
            self.assertNotIn("她没有点开", generated_prompt)


    def test_daily_prompt_orders_sources_by_authority(self):
        prompt = build_daily_plan_prompt(self.plugin, "2026-07-11 09:00", memory_companion_context="MEMORY_CONTEXT")

        positions = [prompt.index(marker) for marker in ("【A｜硬约束】", "【B｜当前事实】", "【C｜连续性参考】", "【D｜软灵感与避重】")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("SCHEDULE_PERSONA", prompt)
        self.assertIn("TODAY_ADJUSTMENT", prompt)
        self.assertIn("至少保留约三分之一节点给 17:00 后", prompt)
        self.assertIn("MEMORY_CONTEXT", prompt)
        self.assertIn("RECENT_DIARY", prompt)
        self.assertIn("STATE_WITHOUT_DREAM", prompt)
        self.assertNotIn("DREAM_BODY_SHOULD_NOT_APPEAR", prompt)
        self.assertLess(prompt.index("TODAY_ADJUSTMENT"), prompt.index("MEMORY_CONTEXT"))
        self.assertLess(prompt.index("MEMORY_CONTEXT"), prompt.index("RECENT_DIARY"))

    def test_daily_prompt_filters_unverified_relationships_before_generation(self):
        self.plugin._recent_diary_context = lambda: "昨晚妈妈炖了汤，后来安静看书。"
        self.plugin._format_recent_daily_plan_history_for_prompt = (
            lambda: "昨天：桌上摆着妈妈洗的青提，之后继续写题。"
        )

        prompt = build_daily_plan_prompt(
            self.plugin,
            "2026-07-14 09:00",
            memory_companion_context="记忆：妈妈做了晚饭，饭后翻开练习册。",
        )

        self.assertNotIn("妈妈", prompt)
        self.assertIn("后来安静看书", prompt)
        self.assertIn("饭后翻开练习册", prompt)
        self.assertIn("关系事实权限", prompt)

    def test_daily_prompt_keeps_identity_declared_relationship_alias(self):
        self.plugin.schedule_persona_prompt = "角色与母亲共同生活。"

        prompt = build_daily_plan_prompt(
            self.plugin,
            "2026-07-14 09:00",
            memory_companion_context="妈妈做了晚饭。",
        )

        self.assertIn("妈妈做了晚饭", prompt)

    def test_detail_prompt_uses_coarse_plan_without_reloading_diary_or_dates(self):
        plan = {
            "items": [
                {"time": "14:00", "activity": "整理桌面"},
                {"time": "15:00", "activity": "继续处理下午的小事"},
                {"time": "16:00", "activity": "准备出门"},
            ]
        }
        segment = {
            "start": 15 * 60,
            "end": 16 * 60,
            "item": plan["items"][1],
            "previous_item": plan["items"][0],
            "next_item": plan["items"][2],
        }

        prompt = build_detail_enhancement_prompt(
            self.plugin,
            segment,
            plan,
            self.plugin.data["daily_state"],
            memory_companion_context="MEMORY_CONTEXT",
        )

        positions = [prompt.index(marker) for marker in ("【A｜当前段硬框架】", "【B｜当前事实】", "【C｜连续性参考】", "【D｜表达与主动规划】")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("- 15:00 继续处理下午的小事", prompt)
        self.assertIn("SCHEDULE_PERSONA", prompt)
        self.assertIn("STATE_WITHOUT_DREAM", prompt)
        self.assertIn("MEMORY_CONTEXT", prompt)
        self.assertNotIn("RECENT_DIARY\n", prompt)
        self.assertNotIn("IMPORTANT_DATES", prompt)
        self.assertEqual(prompt.count("DEFAULT_PERSONA"), 1)
        self.assertIn("location_basis", prompt)
        self.assertIn("地点必须与 summary、today_events、presence_status 和当前事项一致", prompt)
        self.assertIn("新的换装会替换旧换装", prompt)

    def test_detail_prompt_preserves_custom_status_when_sync_is_disabled(self):
        self.plugin.enable_qq_custom_presence_sync = False
        plan = {"items": [{"time": "15:00", "activity": "写题"}]}
        segment = {
            "start": 15 * 60,
            "end": 16 * 60,
            "item": plan["items"][0],
        }

        prompt = build_detail_enhancement_prompt(
            self.plugin,
            segment,
            plan,
            self.plugin.data["daily_state"],
        )

        self.assertIn("当前未开启 QQ 自定义短状态同步", prompt)
        self.assertIn('"presence_status": {"mode": "unchanged"', prompt)

    def test_detail_prompt_allows_custom_status_when_sync_is_enabled(self):
        self.plugin.enable_qq_custom_presence_sync = True
        plan = {"items": [{"time": "15:00", "activity": "写题"}]}
        segment = {
            "start": 15 * 60,
            "end": 16 * 60,
            "item": plan["items"][0],
        }

        prompt = build_detail_enhancement_prompt(
            self.plugin,
            segment,
            plan,
            self.plugin.data["daily_state"],
        )

        self.assertIn("可以用 custom", prompt)
        self.assertIn('"presence_status": {"mode": "custom"', prompt)

    def test_detail_model_location_is_normalized_as_structured_output(self):
        self.assertEqual(normalize_detail_location("当前位置：宿舍卧室"), "宿舍卧室")
        self.assertEqual(normalize_detail_location({"place": "办公室工位"}), "办公室工位")

    def test_detail_quality_retry_detects_sparse_and_contaminated_output(self):
        segment = {"start": 14 * 60 + 30, "end": 17 * 60 + 30, "item": {}}
        payload = {
            "summary": "一直吃面 **dream_seed**: 继续续写",
            "today_events": [
                {"window": "14:30-14:45", "event": "吃面"},
            ],
        }

        issues = detail_payload_quality_issues(self.plugin, payload, segment)

        self.assertTrue(any("只有 1 条" in issue for issue in issues))
        self.assertTrue(any("中后段" in issue for issue in issues))
        self.assertTrue(any("短时进食" in issue for issue in issues))
        self.assertTrue(any("草稿字段" in issue for issue in issues))

    def test_detail_quality_accepts_three_events_across_the_window(self):
        segment = {"start": 10 * 60, "end": 11 * 60, "item": {}}
        payload = {
            "summary": "上午把手边的事情分段推进，并在结束前自然收尾。",
            "today_events": [
                {"window": "10:00-10:12", "event": "慢慢进入状态"},
                {"window": "10:22-10:40", "event": "推进手边事项"},
                {"window": "10:47-10:58", "event": "整理并准备下一段"},
            ],
        }

        self.assertEqual(detail_payload_quality_issues(self.plugin, payload, segment), [])

    def test_detail_density_scales_with_segment_duration(self):
        self.assertEqual(detail_target_event_count(self.plugin, {"start": 600, "end": 620, "item": {}}), 2)
        self.assertEqual(detail_target_event_count(self.plugin, {"start": 600, "end": 660, "item": {}}), 3)
        self.assertEqual(detail_target_event_count(self.plugin, {"start": 600, "end": 690, "item": {}}), 4)
        self.assertEqual(detail_target_event_count(self.plugin, {"start": 600, "end": 780, "item": {}}), 5)

    def test_sleep_detail_density_stays_lightweight(self):
        self.plugin._is_sleepy_plan_item = lambda _item: True
        self.assertEqual(detail_target_event_count(self.plugin, {"start": 0, "end": 30, "item": {}}), 2)
        self.assertEqual(detail_target_event_count(self.plugin, {"start": 0, "end": 180, "item": {}}), 3)

    def test_schedule_basis_and_adjustment_scope_are_normalized(self):
        self.assertEqual(
            DailyStateMixin._normalize_schedule_basis("calendar, state,unknown,calendar", default=["coarse_plan"]),
            ["calendar", "state"],
        )
        self.assertEqual(
            DailyStateMixin._normalize_schedule_basis("unknown", default=["coarse_plan"]),
            ["coarse_plan"],
        )
        self.assertEqual(DailyStateMixin._normalize_schedule_adjustment_scope("只影响当前段"), "current_only")
        self.assertEqual(DailyStateMixin._normalize_schedule_adjustment_scope("影响当前和下一段"), "current_and_next")
        self.assertEqual(DailyStateMixin._normalize_schedule_adjustment_scope("今天剩余时间"), "rest_of_day")
        self.assertEqual(DailyStateMixin._normalize_schedule_adjustment_scope("直到到家"), "until_condition")
        self.assertEqual(DailyStateMixin._normalize_schedule_adjustment_scope("只调整主动消息频率"), "proactive_only")

    def test_daily_quality_flags_overlong_single_meal(self):
        items = [
            {"time": "09:00", "end": "10:00", "activity": "整理房间"},
            {"time": "10:00", "end": "11:00", "activity": "处理事情"},
            {"time": "11:00", "end": "12:00", "activity": "稍作休息"},
            {"time": "14:30", "end": "17:30", "activity": "一直吃面"},
            {"time": "17:30", "end": "18:30", "activity": "收拾桌面"},
        ]

        quality = evaluate_daily_plan_quality(self.plugin, items)

        self.assertLess(quality["score"], 85)
        self.assertTrue(any("进食动作" in issue for issue in quality["issues"]))

    def test_daily_quality_keeps_cross_midnight_item_order(self):
        items = [
            {"time": "22:00", "end": "23:00", "activity": "夜间整理"},
            {"time": "23:00", "end": "00:30", "activity": "准备休息"},
            {"time": "00:30", "end": "01:30", "activity": "继续收尾"},
            {"time": "01:30", "end": "02:15", "activity": "安静下来"},
            {"time": "02:15", "end": "03:00", "activity": "进入睡眠"},
        ]

        quality = evaluate_daily_plan_quality(self.plugin, items)

        self.assertFalse(any("时间重叠" in issue for issue in quality["issues"]))
        self.assertFalse(any("超过三小时" in issue for issue in quality["issues"]))

    def test_daily_quality_rejects_plan_that_uses_all_nodes_before_evening(self):
        items = [
            {"time": "08:00", "end": "09:00", "activity": "起床整理"},
            {"time": "09:00", "end": "10:00", "activity": "处理上午事项"},
            {"time": "10:00", "end": "11:00", "activity": "继续推进"},
            {"time": "11:00", "end": "12:00", "activity": "午前收尾"},
            {"time": "12:00", "end": "13:00", "activity": "午间休息"},
            {"time": "13:00", "end": "15:00", "activity": "下午整理"},
        ]

        quality = evaluate_daily_plan_quality(self.plugin, items)

        self.assertTrue(any("没有覆盖晚间" in issue for issue in quality["issues"]))
        self.assertLess(quality["score"], 85)

    def test_daily_quality_reserves_about_one_third_for_evening(self):
        items = [
            {"time": f"{8 + index:02d}:00", "end": f"{9 + index:02d}:00", "activity": "白天事项"}
            for index in range(9)
        ]
        items.append({"time": "20:00", "end": "22:00", "activity": "晚间收尾"})

        quality = evaluate_daily_plan_quality(self.plugin, items)

        self.assertTrue(any("晚间节点不足" in issue for issue in quality["issues"]))


class DailyPlanGenerationFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unparseable_first_response_retries_before_fallback(self):
        items = [{"time": "09:00", "end": "10:00", "activity": "按世界观整理藏书"}]
        plugin = DailyPlanGenerationHarness(
            ["invalid", "valid"],
            {"valid": items},
        )

        with patch(
            "astrbot_plugin_private_companion.planning.evaluate_daily_plan_quality",
            return_value={"score": 100, "level": "good", "issues": []},
        ):
            plan = await generate_daily_plan(plugin)

        self.assertEqual(len(plugin.calls), 2)
        self.assertIn("输出格式纠偏", plugin.calls[1][0])
        self.assertEqual(plan["source"], "llm")
        self.assertEqual(plan["items"], items)

    async def test_calendar_warning_keeps_parsed_worldview_plan(self):
        items = [{"time": "09:00", "end": "10:00", "activity": "按世界观巡视领地"}]
        plugin = DailyPlanGenerationHarness(
            ["first", "calendar_retry"],
            {"first": items, "calendar_retry": items},
            calendar_conflict=True,
        )

        with patch(
            "astrbot_plugin_private_companion.planning.evaluate_daily_plan_quality",
            return_value={"score": 70, "level": "fair", "issues": ["日程与日期性质冲突"]},
        ):
            plan = await generate_daily_plan(plugin)

        self.assertEqual(plan["source"], "llm_calendar_warning")
        self.assertEqual(plan["items"], items)
        self.assertNotEqual(plan["raw"], "fallback")

    async def test_double_parse_failure_reuses_recent_personalized_plan(self):
        previous_items = [
            {"time": "10:00", "end": "11:00", "activity": "在树屋里整理收藏的地图"}
        ]
        for previous_source in ("llm", "fallback_previous_plan"):
            with self.subTest(previous_source=previous_source):
                plugin = DailyPlanGenerationHarness(
                    ["invalid", "still-invalid"],
                    {},
                    previous_plan={"source": previous_source, "items": previous_items},
                )

                with patch(
                    "astrbot_plugin_private_companion.planning.evaluate_daily_plan_quality",
                    return_value={"score": 80, "level": "fair", "issues": []},
                ):
                    plan = await generate_daily_plan(plugin)

                self.assertEqual(plan["source"], "fallback_previous_plan")
                self.assertEqual(plan["items"], previous_items)
                self.assertGreater(plan["retry_after"], 0)

    async def test_saved_fallback_retries_only_after_retry_time(self):
        future = DailyPlanRetryHarness(retry_after=10**12)
        cached = await future._ensure_daily_plan(force=False)
        self.assertEqual(future.generated_count, 0)
        self.assertEqual(cached["source"], "fallback_previous_plan")

        due = DailyPlanRetryHarness(retry_after=1)
        regenerated = await due._ensure_daily_plan(force=False)
        self.assertEqual(due.generated_count, 1)
        self.assertEqual(regenerated["source"], "llm")


if __name__ == "__main__":
    unittest.main()
