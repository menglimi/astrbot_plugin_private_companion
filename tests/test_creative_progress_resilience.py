# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.constants import (
    CREATIVE_FALLBACK_CHUNKS,
    CREATIVE_SIMILARITY_RETRIES,
)
from astrbot_plugin_private_companion.creative import CreativeMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


class _CreativeGenerationHarness(CreativeMixin):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.llm_calls = 0
        self.creative_direction_prompt = ""
        self.creative_provider_id = ""
        self.mai_style_provider_id = ""

    def _creative_persona_style_context(self) -> str:
        return "测试人格"

    @staticmethod
    def _creative_work_type(_project) -> str:
        return "短篇小说"

    @staticmethod
    def _creative_point_of_view(_project) -> str:
        return "第三人称有限视角"

    @staticmethod
    def _creative_work_output_rule(_work_type, _point_of_view) -> str:
        return "只写正文"

    async def _generate_outline_for_chunk(self, *_args, **_kwargs) -> str:
        return "- 推进新场景"

    async def _review_creative_chunk(self, *_args, **_kwargs):
        return {
            "passed": True,
            "persona_score": 9,
            "progress_score": 9,
            "repetition_score": 9,
        }

    async def _llm_call(self, *_args, **_kwargs) -> str:
        self.llm_calls += 1
        if self.responses:
            return self.responses.pop(0)
        return ""

    @staticmethod
    def _task_provider(*_args) -> str:
        return ""


class _CreativeAdvanceHarness(CreativeMixin):
    def __init__(self) -> None:
        self.enable_creative_writing = True
        self._data_lock = asyncio.Lock()
        self.data = {
            "users": {},
            "creative_projects": [
                {
                    "id": "project-1",
                    "title": "不会被固定句污染",
                    "status": "drafting",
                    "target_chars": 1000,
                    "current_chars": 120,
                    "next_advance_at": 0,
                    "draft_chunks": [{"text": "已有正文。", "chars": 5}],
                }
            ],
        }

    def _creative_has_pending_proactive_plan(self) -> bool:
        return False

    def _bot_currently_idle_for_creative_writing(self) -> bool:
        return True

    async def _maybe_start_creative_project(self, **_kwargs) -> bool:
        return False

    def _creative_chars_per_session(self) -> int:
        return 200

    async def _generate_creative_chunk(self, *_args, **_kwargs) -> str:
        return ""

    def _maybe_schedule_creative_share(self) -> bool:
        return False

    def _creative_cover_candidate_id(self) -> str:
        return ""

    def _save_data_sync(self) -> None:
        return None


class _DisabledCreativePlanHarness(ProactiveMixin, ProactiveEngineMixin):
    enable_creative_writing = False

    def __init__(self) -> None:
        self.saved = 0
        self.data = {
            "proactive_candidate_pool": [
                {"id": "creative-1", "user_id": "10001", "status": "accepted"}
            ]
        }

    @staticmethod
    def _recover_stale_proactive_sending(_user) -> None:
        return None

    @staticmethod
    def _user_enabled_for_proactive(_user_id, _user) -> bool:
        return True

    @staticmethod
    def _proactive_generation_disabled(_user=None) -> bool:
        return False

    @staticmethod
    def _effective_user_daily_limit(_user) -> int:
        return 4

    @staticmethod
    def _simulation_active(_user) -> bool:
        return False

    @staticmethod
    def _has_due_llm_timer(_user, *, now) -> bool:
        return False

    @staticmethod
    def _clear_planned_proactive_trigger(_user) -> None:
        return None

    def _schedule_data_save(self) -> None:
        self.saved += 1


class CreativeProgressResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_creative_writing_does_not_schedule_share(self) -> None:
        harness = CreativeMixin()
        harness.enable_creative_writing = False

        self.assertFalse(harness._maybe_schedule_creative_share())

    async def test_disabled_creative_writing_clears_stale_share_plan(self) -> None:
        harness = _DisabledCreativePlanHarness()
        user = {
            "user_id": "10001",
            "enabled": True,
            "umo": "default:FriendMessage:10001",
            "next_proactive_at": 12345,
            "planned_proactive_reason": "creative_share",
            "planned_proactive_action": "message",
            "planned_proactive_source": "creative_writing",
            "planned_candidate_id": "creative-1",
            "creative_share_context": {"title": "旧候选"},
        }

        allowed, reason = harness._should_send(user)

        self.assertFalse(allowed)
        self.assertEqual("创作功能未开启", reason)
        self.assertEqual(0, user["next_proactive_at"])
        self.assertEqual({}, user["creative_share_context"])
        self.assertFalse(
            any(
                item.get("status") in {"accepted", "planned", "deferred"}
                for item in harness.data["proactive_candidate_pool"]
                if isinstance(item, dict)
            )
        )
        self.assertEqual(1, harness.saved)

    async def test_similarity_guard_reaches_beyond_last_five_chunks(self) -> None:
        harness = CreativeMixin()
        repeated = "她终于推开那扇一直不敢碰的门，走廊尽头亮起了一盏灯。"
        recent_chunks = [
            {"text": repeated},
            {"text": "第一段不同的推进。"},
            {"text": "第二段不同的推进。"},
            {"text": "第三段不同的推进。"},
            {"text": "第四段不同的推进。"},
            {"text": "第五段不同的推进。"},
        ]

        self.assertTrue(harness._check_chunk_similarity(repeated, recent_chunks))

    async def test_repeated_final_retry_is_not_accepted(self) -> None:
        repeated = "风从窗边吹过，她把同一句话又写了一遍。"
        harness = _CreativeGenerationHarness(
            [repeated] * (CREATIVE_SIMILARITY_RETRIES + 1)
        )
        project = {
            "id": "project-1",
            "title": "重复测试",
            "premise": "测试",
            "tone": "安静",
            "target_chars": 1000,
            "current_chars": len(repeated),
            "draft_chunks": [{"text": repeated, "chars": len(repeated)}],
        }

        result = await harness._generate_creative_chunk(project, 180)

        self.assertEqual(result, "")
        self.assertEqual(harness.llm_calls, CREATIVE_SIMILARITY_RETRIES + 1)

    async def test_empty_generation_does_not_insert_generic_fallback(self) -> None:
        harness = _CreativeGenerationHarness([""] * (CREATIVE_SIMILARITY_RETRIES + 1))
        project = {
            "id": "project-1",
            "title": "空结果测试",
            "premise": "测试",
            "tone": "安静",
            "target_chars": 1000,
            "current_chars": 0,
            "draft_chunks": [],
        }

        result = await harness._generate_creative_chunk(project, 180)

        self.assertEqual(result, "")
        self.assertNotIn(result, CREATIVE_FALLBACK_CHUNKS)

    async def test_failed_quality_gate_defers_without_appending(self) -> None:
        harness = _CreativeAdvanceHarness()

        with patch("astrbot_plugin_private_companion.creative._now_ts", return_value=1000.0):
            await harness._maybe_advance_creative_projects()

        project = harness.data["creative_projects"][0]
        self.assertEqual(len(project["draft_chunks"]), 1)
        self.assertEqual(project["advance_failure_count"], 1)
        self.assertEqual(project["next_advance_at"], 1000.0 + 30 * 60)
        self.assertIn("重复", project["last_advance_error"])

    def test_future_timer_does_not_starve_creative_writing(self) -> None:
        harness = CreativeMixin()
        harness.data = {
            "users": {
                "10001": {
                    "planned_proactive_source": "timer",
                    "next_proactive_at": 1000.0 + 2 * 86400,
                }
            }
        }

        with patch("astrbot_plugin_private_companion.creative._now_ts", return_value=1000.0):
            self.assertFalse(harness._creative_has_pending_proactive_plan())
            harness.data["users"]["10001"]["next_proactive_at"] = 1000.0 + 10 * 60
            self.assertTrue(harness._creative_has_pending_proactive_plan())

    def test_legacy_cleanup_removes_only_exact_builtin_fallbacks(self) -> None:
        harness = CreativeMixin()
        real_text = "这是真正推进情节的一段正文。"
        harness.data = {
            "creative_projects": [
                {
                    "id": "project-1",
                    "status": "finished",
                    "current_chars": 999,
                    "draft_chunks": [
                        {"text": CREATIVE_FALLBACK_CHUNKS[0], "chars": len(CREATIVE_FALLBACK_CHUNKS[0])},
                        {"text": real_text, "chars": len(real_text)},
                    ],
                    "creative_memory_pool": [
                        {"content": CREATIVE_FALLBACK_CHUNKS[0]},
                        {"content": real_text},
                    ],
                }
            ]
        }

        changed = harness._cleanup_legacy_creative_fallback_chunks()

        project = harness.data["creative_projects"][0]
        self.assertTrue(changed)
        self.assertEqual([item["text"] for item in project["draft_chunks"]], [real_text])
        self.assertEqual(project["current_chars"], len(real_text))
        self.assertEqual([item["content"] for item in project["creative_memory_pool"]], [real_text])
        self.assertEqual(project["status"], "finished")


if __name__ == "__main__":
    unittest.main()
