# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path

from astrbot_plugin_private_companion.dreaming import (
    _daily_diary_evidence_ledger,
    _daily_diary_form_instruction,
    _extract_daily_diary_derivatives,
    fallback_diary_payload,
    generate_daily_diary,
    recent_diary_context,
)


ROOT = Path(__file__).resolve().parents[1]


class DiaryHarness:
    daily_diary_form = "auto"
    daily_diary_length = "standard"
    daily_diary_creativity = "balanced"
    daily_diary_custom_direction = ""
    daily_diary_generate_share_seed = True
    diary_provider_id = ""
    mai_style_provider_id = ""
    schedule_persona_prompt = ""
    schedule_worldview_prompt = ""

    def __init__(self, data=None, responses=None):
        self.data = data or {"daily_state": {"mood_bias": "平稳", "energy": 70}}
        self.responses = list(responses or [])

    def _environment_now(self):
        return datetime.now()

    def _get_default_persona_prompt(self):
        return "说话自然、诚实，不虚构生活。"

    def _format_calendar_context_for_prompt(self):
        return "普通日期"

    def _format_important_dates_for_prompt(self):
        return "（暂无）"

    def _recent_diary_context(self):
        return recent_diary_context(self)

    def _task_provider(self, *_args):
        return ""

    async def _llm_call(self, *_args, **_kwargs):
        return self.responses.pop(0) if self.responses else ""

    def _extract_json_payload(self, raw):
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _fallback_diary_payload(self, evidence=None):
        return fallback_diary_payload(self, evidence=evidence)

    def _extract_weighted_dream_fragments(self, payload):
        return list(payload.get("dream_fragments") or [])

    def _normalize_story_plan(self, payload):
        return {
            "today_events": list(payload.get("today_events") or []),
            "proactive_events": list(payload.get("proactive_events") or []),
            "long_term_events": list(payload.get("long_term_events") or []),
        }

    def _self_timeline_from_creative(self, _data):
        return []

    def _self_timeline_from_private_reading(self, _data):
        return []

    def _self_timeline_from_photo_generation(self, _data, **_kwargs):
        return []

    def _self_timeline_from_qzone_publish(self, _data):
        return []


class DailyDiaryCreativePipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_evidence_ledger_keeps_schedule_adjustments_unconfirmed(self):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plugin = DiaryHarness({
            "schedule_adjustments": [{"created_at": stamp, "summary": "晚上读两章书"}],
            "daily_state": {},
        })
        _, evidence = _daily_diary_evidence_ledger(plugin)
        self.assertTrue(any(item["text"] == "晚上读两章书" and item["level"] == "planned" for item in evidence))

    def test_sent_proactive_audit_is_confirmed(self):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plugin = DiaryHarness({
            "proactive_audit_log": [{"status": "sent", "sent_at": stamp, "text_preview": "分享了刚读到的一句话"}],
            "daily_state": {},
        })
        _, evidence = _daily_diary_evidence_ledger(plugin)
        self.assertTrue(any(item["text"] == "分享了刚读到的一句话" and item["level"] == "confirmed" for item in evidence))

    def test_stale_plan_and_detail_snapshot_do_not_enter_today_ledger(self):
        plugin = DiaryHarness({
            "daily_plan": {"date": "2000-01-01", "items": [{"time": "08:00", "activity": "旧计划"}]},
            "detail_enhanced_day": "2000-01-01",
            "detail_enhanced_segments": {"2000-01-01:0:08:00": {"status": "done", "summary": "旧推演"}},
            "daily_state": {},
        })
        _, evidence = _daily_diary_evidence_ledger(plugin)
        texts = {item["text"] for item in evidence}
        self.assertNotIn("旧计划", texts)
        self.assertNotIn("旧推演", texts)

    def test_actual_timeline_source_is_confirmed_but_goal_runtime_is_simulated(self):
        stamp = datetime.now().timestamp()
        plugin = DiaryHarness({
            "daily_state": {},
            "personal_goals": [{"title": "练琴", "recent_logs": [{"ts": stamp, "progress": 20, "evidence": "日程段已过去"}]}],
        })
        plugin._self_timeline_from_creative = lambda _data: [{"ts": stamp, "summary": "续写《小城》", "detail": "写完第二段"}]
        _, evidence = _daily_diary_evidence_ledger(plugin)
        self.assertTrue(any(item["source"] == "实际创作记录" and item["level"] == "confirmed" for item in evidence))
        self.assertTrue(any(item["source"] == "个人目标运行记录" and item["level"] == "simulated" for item in evidence))

    def test_automatic_form_is_stable_for_same_date(self):
        plugin = DiaryHarness()
        evidence = [{"level": "confirmed", "source": "test", "text": "一条记录"}]
        first = _daily_diary_form_instruction(plugin, evidence)
        self.assertEqual(first, _daily_diary_form_instruction(plugin, evidence))

    def test_fallback_is_honest_and_has_no_static_scene(self):
        plugin = DiaryHarness()
        payload = fallback_diary_payload(plugin, [{"level": "planned", "source": "日程", "text": "去书店"}])
        self.assertIn("没有足够记录", payload["body"])
        for motif in ("桌面", "窗光", "窗帘", "茶", "便签", "纸边"):
            self.assertNotIn(motif, payload["body"])
        self.assertEqual([], payload["today_events"])
        self.assertEqual([], payload["proactive_events"])

    async def test_share_seed_can_be_disabled(self):
        plugin = DiaryHarness(responses=[json.dumps({"share_seed": "不应保留", "dream_fragments": []}, ensure_ascii=False)])
        plugin.daily_diary_generate_share_seed = False
        result = await _extract_daily_diary_derivatives(plugin, {"body": "今天读完一页书。"})
        self.assertEqual("不应保留", result["share_seed"])
        # 最终归一化层必须再次执行开关，不能信任模型是否按要求留空。
        plugin.responses = [
            json.dumps({"summary": "读到一句话", "body": "今天看书时读到一句很有意思的话，我停下来重新看了两遍，又顺着前后的段落慢慢读了一会儿。没有急着给它下结论，只把那一页记住了。", "tags": ["阅读"]}, ensure_ascii=False),
            json.dumps({"share_seed": "仍不应保留", "dream_fragments": [], "continuity_thread": {}, "long_term_events": [], "today_events": [{"event": "注入"}], "proactive_events": [{"action": "message"}]}, ensure_ascii=False),
        ]
        diary = await generate_daily_diary(plugin)
        self.assertEqual("", diary["share_seed"])
        self.assertEqual([], diary["story_plan"]["today_events"])
        self.assertEqual([], diary["story_plan"]["proactive_events"])

    async def test_continuity_thread_survives_normalized_result(self):
        plugin = DiaryHarness(responses=[
            json.dumps({"summary": "读到一句话", "body": "今天看书时读到一句很有意思的话，我停下来重新看了两遍，又顺着前后的段落慢慢读了一会儿。没有急着给它下结论，只把那一页记住了。", "tags": ["阅读"]}, ensure_ascii=False),
            json.dumps({"share_seed": "", "dream_fragments": [], "continuity_thread": {"motif": "那一页书", "status": "出现", "next_hint": "有新进展再接"}, "long_term_events": []}, ensure_ascii=False),
        ])
        diary = await generate_daily_diary(plugin)
        self.assertEqual("那一页书", diary["continuity_thread"]["motif"])
        plugin.data["bot_diaries"] = [diary]
        self.assertIn("线索=那一页书（出现）", recent_diary_context(plugin))

    def test_schema_and_frontend_visibility_are_complete(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        items = schema["schedule_detail_config"]["items"]
        keys = (
            "daily_diary_form",
            "daily_diary_length",
            "daily_diary_creativity",
            "daily_diary_custom_direction",
            "daily_diary_generate_share_seed",
        )
        for key in keys:
            self.assertEqual({"enable_daily_diary": True}, items[key]["condition"])
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        for key in keys:
            self.assertIn(key, script)
        self.assertIn('diaryChildren.has(settingKey) && !boolSetting("enable_daily_diary")', script)


if __name__ == "__main__":
    unittest.main()
