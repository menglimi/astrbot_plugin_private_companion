# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

try:
    from astrbot.api import logger as _astrbot_logger  # noqa: F401
except ModuleNotFoundError:
    astrbot_module = types.ModuleType("astrbot")
    astrbot_api_module = types.ModuleType("astrbot.api")
    astrbot_api_module.logger = logging.getLogger("daily-review-test")
    astrbot_module.api = astrbot_api_module
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules.setdefault("astrbot.api", astrbot_api_module)

from astrbot_plugin_private_companion.daily_review import DailyReviewMixin


ROOT = Path(__file__).resolve().parents[1]


class _DailyReviewHarness(DailyReviewMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self._daily_review_generation_lock = asyncio.Lock()
        self.environment_perception_timezone = "Asia/Shanghai"
        self.enable_daily_review = True
        self.daily_review_time = "04:00"
        self.daily_review_retention_days = 30
        self.daily_review_auto_apply_guidance = True
        self.enable_daily_case_review_experiment = False
        self.daily_review_provider_id = "review-provider"
        self.troubleshooting_provider_id = "fallback-provider"
        self.complex_reasoning_provider_id = ""
        self.mai_style_provider_id = ""
        self.llm_provider_id = ""
        self.llm_calls = 0
        self.saved = 0
        self.llm_result = json.dumps(
            {
                "headline": "整体稳定，TTS 需要继续观察",
                "summary": "当天主要链路可用，发现一次 TTS 模型失败和一次主动发送异常。",
                "health_score": 82,
                "findings": [
                    {
                        "severity": "warn",
                        "category": "tts",
                        "title": "TTS 任务出现失败",
                        "evidence": "tts_spoken_conversion errors=1",
                        "impact": "部分语音可能回退为文本",
                    }
                ],
                "corrections": [
                    {
                        "type": "prompt_guidance",
                        "scope": "tts",
                        "instruction": "生成外语语音时先保证完整表达，再给出对应中文正文。",
                        "reason": "避免语音转换截断",
                        "risk": "low",
                        "auto_apply": True,
                    },
                    {
                        "type": "prompt_guidance",
                        "scope": "group",
                        "instruction": "把群成员风控阈值改为 1 并加入黑名单。",
                        "reason": "高风险配置动作不应自动执行",
                        "risk": "low",
                        "auto_apply": True,
                    },
                ],
                "suggested_config_changes": [
                    {
                        "key": "tts_conversion_provider_id",
                        "suggestion": "人工检查备用模型",
                        "reason": "出现一次调用失败",
                        "risk": "medium",
                    }
                ],
                "tomorrow_focus": ["观察 TTS 完整性"],
            },
            ensure_ascii=False,
        )
        day_ts = datetime(2026, 7, 28, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.data = {
            "token_usage": {
                "by_day_task": {
                    "2026-07-28": {
                        "tts_spoken_conversion": {
                            "calls": 2,
                            "success": 1,
                            "errors": 1,
                            "total_tokens": 300,
                            "elapsed_ms": 14000,
                        }
                    }
                },
                "recent": [
                    {
                        "ts": day_ts,
                        "time": "2026-07-28 18:00:00",
                        "task": "tts_spoken_conversion",
                        "provider": "tts-model",
                        "success": False,
                        "error": "timeout",
                    }
                ],
            },
            "proactive_audit_log": [
                {
                    "created_ts": day_ts,
                    "user_id": "sensitive-user-123",
                    "status": "failed",
                    "action": "voice",
                    "reason": "activity_share",
                    "note": "发送失败",
                    "text_preview": "不应进入巡视摘要的主动消息原文",
                }
            ],
            "passive_no_reply_records": {
                "items": [
                    {
                        "last_ts": day_ts,
                        "reason": "模型未返回文本",
                        "source": "reply",
                        "count": 1,
                        "last_inbound": "用户隐私原话不应进入摘要",
                        "last_detail": "empty response",
                    }
                ]
            },
            "groups": {
                "sensitive-group-456": {
                    "member_safety": {
                        "sensitive-member-789": {
                            "events": [
                                {
                                    "ts": day_ts,
                                    "message": "群聊原话不应进入摘要",
                                    "category": "harassment",
                                    "counted": False,
                                    "severity": 1,
                                    "confidence": 0.55,
                                    "validation_reason": "缺少多轮证据",
                                }
                            ]
                        }
                    }
                }
            },
            "daily_plan": {"date": "2026-07-28"},
            "daily_plan_history": [],
            "bot_diaries": [{"date": "2026-07-28"}],
            "daily_review_reports": [],
            "daily_review_active_guidance": {},
        }

    @staticmethod
    def _task_provider(*provider_ids: str) -> str:
        return next((item for item in provider_ids if item), "")

    async def _llm_call(self, *_args, **_kwargs) -> str:
        self.llm_calls += 1
        return self.llm_result

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1

    async def _record_request_prompt_fragment(self, *_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _is_private_companion_owner_user_id(user_id: str) -> bool:
        return str(user_id) == "owner-1"


class DailyReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.harness = _DailyReviewHarness()

    def _recent_complete_case_day(self) -> tuple[str, float]:
        review_day = self.harness._daily_review_now() - timedelta(days=1)
        case_time = review_day.replace(hour=20, minute=0, second=0, microsecond=0)
        return review_day.date().isoformat(), case_time.timestamp()

    async def test_snapshot_excludes_raw_messages_and_stable_identifiers(self) -> None:
        snapshot = self.harness._daily_review_snapshot("2026-07-28")
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("sensitive-user-123", encoded)
        self.assertNotIn("sensitive-group-456", encoded)
        self.assertNotIn("用户隐私原话", encoded)
        self.assertNotIn("群聊原话", encoded)
        self.assertNotIn("主动消息原文", encoded)
        self.assertEqual(1, snapshot["proactive"]["total"])
        self.assertEqual(1, len(snapshot["model_failures"]))
        self.assertFalse(snapshot["case_review"]["enabled"])

    async def test_experimental_case_audit_is_opt_in_and_anonymous(self) -> None:
        date_key, day_ts = self._recent_complete_case_day()
        self.assertEqual("", self.harness._append_daily_review_case(
            kind="reply", scene="private", inbound="用户 123456789", output="回复", ts=day_ts
        ))
        self.assertEqual([], self.harness.data.get("daily_review_case_audit", []))

        self.harness.enable_daily_case_review_experiment = True
        case_id = self.harness._append_daily_review_case(
            kind="reply",
            scene="private",
            inbound="用户 123456789 问今天怎么样",
            output="今天还不错",
            outcome="prepared",
            components=["plain"],
            signals={"session_id": "default:FriendMessage:123456789", "note": "token=secret-value"},
            ts=day_ts,
        )
        self.assertTrue(case_id)
        sampled = self.harness._daily_review_case_samples(date_key)
        encoded = json.dumps(sampled, ensure_ascii=False)
        self.assertTrue(sampled["enabled"])
        self.assertGreaterEqual(sampled["sampled"], 1)
        self.assertNotIn("123456789", encoded)
        self.assertNotIn("session_id", encoded)
        self.assertNotIn("secret-value", encoded)
        reply_cases = [item for item in sampled["cases"] if item.get("kind") == "reply"]
        self.assertEqual(1, len(reply_cases))
        self.assertRegex(reply_cases[0]["case_id"], r"^C-[0-9A-F]{8,}$")
        self.assertIn("[数字标识已隐藏]", reply_cases[0]["inbound"])

    async def test_case_review_only_accepts_real_sample_ids(self) -> None:
        date_key, day_ts = self._recent_complete_case_day()
        self.harness.enable_daily_case_review_experiment = True
        self.harness._append_daily_review_case(
            kind="tts",
            scene="group",
            inbound="请用日语回答",
            output="完整中文正文",
            outcome="incomplete",
            components=["record", "plain"],
            signals={"segments_expected": 3, "segments_sent": 1},
            ts=day_ts,
        )
        payload = json.loads(self.harness.llm_result)
        real_case_id = self.harness._daily_review_case_samples(date_key)["cases"][0]["case_id"]
        payload["case_reviews"] = [
            {
                "case_id": real_case_id,
                "verdict": "needs_attention",
                "dimensions": ["completeness", "tts"],
                "reason": "只发送了首段",
                "recommended_behavior": "补齐对应正文",
            },
            {"case_id": "C-NOTFOUND", "verdict": "needs_attention", "reason": "不存在的案例"},
        ]
        self.harness.llm_result = json.dumps(payload, ensure_ascii=False)
        expected_samples = self.harness._daily_review_case_samples(date_key)["sampled"]
        report = await self.harness._ensure_daily_review(target_date=date_key)
        self.assertEqual([real_case_id], [item["case_id"] for item in report["case_reviews"]])
        self.assertEqual(expected_samples, report["evidence_summary"]["case_samples"])

    async def test_disabling_experiment_clears_short_term_case_audit(self) -> None:
        self.harness.enable_daily_case_review_experiment = True
        self.harness._append_daily_review_case(
            kind="reply", scene="private", inbound="测试", output="测试回复"
        )
        self.assertTrue(self.harness.data["daily_review_case_audit"])
        self.harness.enable_daily_case_review_experiment = False
        self.assertEqual([], self.harness._daily_review_case_audit())

    async def test_tts_case_separates_actual_output_from_expected_transcript(self) -> None:
        self.harness.enable_daily_case_review_experiment = True
        record_type = type("Record", (), {})
        record = record_type()
        record._private_companion_tts_spoken_text = "プラグインはQQに対応しています"
        record._private_companion_tts_source_text = "插件主要适配 QQ，个人微信暂无支持。"
        event = SimpleNamespace(
            message_str="个人微信能用吗",
            private_companion_group_text="",
            private_companion_proactive_framework=False,
            is_private_chat=lambda: True,
        )
        case_id = self.harness._record_daily_review_outbound_case(event, [record])
        self.assertTrue(case_id)
        case = self.harness.data["daily_review_case_audit"][0]
        self.assertIn("[语音]", case["output"])
        self.assertNotIn("个人微信暂无支持", case["output"])
        self.assertIn("个人微信暂无支持", case["signals"]["expected_text_preview"])
        self.assertFalse(case["signals"]["visible_text_complete"])

    async def test_sampling_mixes_controls_clusters_repeats_and_prioritizes_owner(self) -> None:
        self.harness.enable_daily_case_review_experiment = True
        date_key, day_ts = self._recent_complete_case_day()
        for _ in range(3):
            self.harness._append_daily_review_case(
                kind="tts",
                scene="private",
                role="owner",
                inbound="请完整回答",
                output="只发送第一段",
                outcome="incomplete",
                components=["record", "plain"],
                signals={"segments_expected": 3, "segments_sent": 1, "visible_text_complete": False},
                ts=day_ts,
            )
        self.harness._append_daily_review_case(
            kind="reply",
            scene="private",
            role="owner",
            inbound="今天怎么样",
            output="今天还不错",
            outcome="prepared",
            components=["plain"],
            ts=day_ts,
        )
        sampled = self.harness._daily_review_case_samples(date_key)
        self.assertGreaterEqual(sampled["sample_mix"].get("anomaly", 0), 1)
        self.assertGreaterEqual(sampled["sample_mix"].get("control", 0), 1)
        clustered = next(item for item in sampled["cases"] if item["kind"] == "tts")
        self.assertEqual(3, clustered["occurrence_count"])
        self.assertEqual("owner", clustered["role"])
        self.assertTrue(any(stage["stage"] == "segments" for stage in clustered["timeline"]))

    async def test_experimental_guidance_requires_confident_case_evidence(self) -> None:
        self.harness.enable_daily_case_review_experiment = True
        date_key, day_ts = self._recent_complete_case_day()
        self.harness._append_daily_review_case(
            kind="reply", scene="private", inbound="问题", output="答非所问",
            outcome="prepared", ts=day_ts,
        )
        case_id = self.harness._daily_review_case_samples(date_key)["cases"][0]["case_id"]
        payload = json.loads(self.harness.llm_result)
        payload["case_reviews"] = [{
            "case_id": case_id,
            "verdict": "needs_attention",
            "confidence": 0.91,
            "dimensions": ["relevance"],
            "evidence": "输出没有回答问题",
            "reason": "答非所问",
        }]
        payload["corrections"][0]["confidence"] = 0.91
        payload["corrections"][0]["evidence_case_ids"] = [case_id]
        self.harness.llm_result = json.dumps(payload, ensure_ascii=False)
        report = await self.harness._ensure_daily_review(target_date=date_key)
        self.assertEqual(1, len(report["applied_safe_guidance"]))
        self.assertEqual([case_id], report["applied_safe_guidance"][0]["evidence_case_ids"])

    async def test_guidance_rolls_back_when_review_detects_side_effect(self) -> None:
        instruction = "回答前先确认用户真正的问题。"
        guidance_id = self.harness._daily_review_guidance_id("reply", instruction)
        self.harness.data["daily_review_active_guidance"] = {
            "active": True,
            "active_until": time.time() + 86400,
            "items": [{
                "guidance_id": guidance_id,
                "scope": "reply",
                "instruction": instruction,
                "support_days": 1,
                "evaluation_count": 0,
                "active_until": time.time() + 86400,
            }],
        }
        payload = json.loads(self.harness.llm_result)
        payload["corrections"] = []
        payload["guidance_evaluations"] = [{
            "guidance_id": guidance_id,
            "verdict": "worse",
            "confidence": 0.88,
            "evidence": "相关性问题增加",
        }]
        self.harness.llm_result = json.dumps(payload, ensure_ascii=False)
        report = await self.harness._ensure_daily_review(target_date="2026-07-28")
        active = self.harness.data["daily_review_active_guidance"]
        self.assertEqual([], active["items"])
        self.assertEqual("rolled_back", active["retired_items"][-1]["status"])
        self.assertEqual(1, report["guidance_lifecycle"]["rolled_back"])

    async def test_guidance_exits_after_two_confident_no_effect_reviews(self) -> None:
        instruction = "回复时优先完整表达。"
        guidance_id = self.harness._daily_review_guidance_id("reply", instruction)
        self.harness.data["daily_review_active_guidance"] = {
            "active": True,
            "active_until": time.time() + 3 * 86400,
            "items": [{
                "guidance_id": guidance_id,
                "scope": "reply",
                "instruction": instruction,
                "support_days": 1,
                "evaluation_count": 0,
                "active_until": time.time() + 3 * 86400,
            }],
        }
        evaluation = [{
            "guidance_id": guidance_id,
            "verdict": "unchanged",
            "confidence": 0.9,
            "evidence": "完整率没有变化",
        }]
        first = {"date": "2026-07-28", "generated_at": time.time(), "safe_guidance": [], "guidance_evaluations": evaluation}
        self.harness._activate_daily_review_guidance(first)
        self.assertEqual(1, len(self.harness.data["daily_review_active_guidance"]["items"]))
        second = {"date": "2026-07-29", "generated_at": time.time(), "safe_guidance": [], "guidance_evaluations": evaluation}
        self.harness._activate_daily_review_guidance(second)
        active = self.harness.data["daily_review_active_guidance"]
        self.assertEqual([], active["items"])
        self.assertEqual("no_effect", active["retired_items"][-1]["status"])

    async def test_scheduler_catches_up_bounded_gap_in_order(self) -> None:
        self.harness.data["daily_review_reports"] = [{"date": "2026-07-25"}]
        self.harness.data["daily_review_completed_day"] = "2026-07-25"
        current = datetime(2026, 7, 29, 4, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(
            ["2026-07-26", "2026-07-27", "2026-07-28"],
            self.harness._daily_review_pending_dates(now=current),
        )

    async def test_review_persists_once_and_only_applies_safe_prompt_guidance(self) -> None:
        report = await self.harness._ensure_daily_review(target_date="2026-07-28")
        second = await self.harness._ensure_daily_review(target_date="2026-07-28")
        self.assertIs(report, second)
        self.assertEqual(1, self.harness.llm_calls)
        self.assertEqual("completed", report["status"])
        self.assertEqual(1, len(report["applied_safe_guidance"]))
        self.assertNotIn("黑名单", json.dumps(report["applied_safe_guidance"], ensure_ascii=False))
        self.assertTrue(report["suggested_config_changes"][0]["requires_confirmation"])
        self.assertTrue(report["suggested_config_changes"][0]["valid"])
        self.assertEqual("TTS文本转换模型Provider ID", report["suggested_config_changes"][0]["label"])
        self.assertTrue(self.harness.data["daily_review_active_guidance"]["active"])

    def test_config_suggestions_validate_schema_keys(self) -> None:
        valid = self.harness._daily_review_config_suggestion({"key": "daily_review_time"})
        invalid = self.harness._daily_review_config_suggestion({"key": "diary_task_start_monitor"})
        self.assertTrue(valid["valid"])
        self.assertEqual("每日巡视时间", valid["label"])
        self.assertFalse(invalid["valid"])
        self.assertEqual("配置项不存在或已被移除", invalid["invalid_reason"])
        self.harness.data["daily_review_reports"] = [{
            "date": "2026-07-28",
            "suggested_config_changes": [{"key": "diary_task_start_monitor", "suggestion": "开启监控"}],
        }]
        status_item = self.harness._daily_review_status_payload()["reports"][0]["suggested_config_changes"][0]
        self.assertFalse(status_item["valid"])
        self.assertEqual("配置项不存在或已被移除", status_item["invalid_reason"])

    def test_prompt_limits_suggestions_to_real_config_keys(self) -> None:
        prompt = self.harness._daily_review_prompt({"model": {"errors": 1}, "tts": {"errors": 1}})
        self.assertIn("真实配置键白名单", prompt)
        self.assertIn("tts_conversion_provider_id", prompt)
        self.assertNotIn('"key":"配置键或功能名"', prompt)

    async def test_invalid_model_result_does_not_mark_review_complete(self) -> None:
        self.harness.llm_result = "not-json"
        with self.assertRaises(ValueError):
            await self.harness._ensure_daily_review(force=True, target_date="2026-07-28")
        self.assertEqual([], self.harness.data["daily_review_reports"])
        self.assertEqual("failed", self.harness.data["daily_review_last_attempt"]["status"])

    async def test_guidance_injection_is_soft_and_can_be_paused(self) -> None:
        target_date = (
            self.harness._daily_review_now().date() - timedelta(days=1)
        ).isoformat()
        await self.harness._ensure_daily_review(target_date=target_date)
        request = SimpleNamespace(system_prompt="原系统提示", prompt="当前问题")
        await self.harness._append_daily_review_guidance_to_request(object(), request)
        self.assertIn('<section title="每日巡视柔性纠偏">', request.system_prompt)
        self.assertIn("不能覆盖当前用户意图", request.system_prompt)
        self.assertNotIn("黑名单", request.system_prompt)

        self.harness.data["daily_review_active_guidance"]["active"] = False
        paused = SimpleNamespace(system_prompt="原系统提示", prompt="当前问题")
        await self.harness._append_daily_review_guidance_to_request(object(), paused)
        self.assertEqual("原系统提示", paused.system_prompt)

    async def test_scheduler_reviews_previous_complete_day_at_four(self) -> None:
        after_due = datetime(2026, 7, 29, 4, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("2026-07-28", self.harness._daily_review_target_date(now=after_due))
        self.assertEqual(0.0, self.harness._next_daily_review_due_in_seconds(after_due.timestamp()))

    async def test_scheduler_waits_until_four_when_previous_review_exists(self) -> None:
        before_due = datetime(2026, 7, 29, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.harness.data["daily_review_reports"] = [{"date": "2026-07-27"}]
        self.assertEqual("2026-07-27", self.harness._daily_review_target_date(now=before_due))
        self.assertEqual(3600.0, self.harness._next_daily_review_due_in_seconds(before_due.timestamp()))


class DailyReviewUiContractTests(unittest.TestCase):
    def test_panel_exposes_review_inside_experimental_workspace(self) -> None:
        html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        schema = (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        self.assertNotIn('data-tab="daily-review"', html)
        self.assertIn('data-tab="experimental"', html)
        self.assertIn('data-exp-tool-open="daily-review"', script)
        self.assertIn('data-daily-review-run', script)
        self.assertIn('data-daily-review-config-key', script)
        self.assertIn('无效或过期的配置建议', script)
        self.assertNotIn('<em>配置项不存在</em>', script)
        self.assertIn('fetchJson("/daily-review")', script)
        self.assertIn('postJson("/daily-review/run"', script)
        self.assertIn('DAILY_REVIEW_PROVIDER_ID', schema)
        self.assertIn('enable_daily_case_review_experiment', schema)
        self.assertIn('每日逐案复盘', script)


if __name__ == "__main__":
    unittest.main()
