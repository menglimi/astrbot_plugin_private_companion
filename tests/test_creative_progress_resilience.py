# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from astrbot_plugin_private_companion.constants import (
    CREATIVE_FALLBACK_CHUNKS,
    CREATIVE_LEGACY_FALLBACK_CHUNKS,
    CREATIVE_SIMILARITY_RETRIES,
)
from astrbot_plugin_private_companion import creative as creative_module
from astrbot_plugin_private_companion.creative import CreativeMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.qzone_publish import QzonePublishMixin


class _CreativeGenerationHarness(CreativeMixin):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.llm_calls = 0
        self.creative_direction_prompt = ""
        self.creative_provider_id = ""
        self.creative_review_provider_id = ""
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
    def _extract_json_payload(raw_text: str):
        return json.loads(raw_text)

    @staticmethod
    def _task_provider(*_args) -> str:
        return ""


class _CreativePersonaContextHarness(CreativeMixin):
    def __init__(self) -> None:
        self.schedule_persona_prompt = ""
        self.default_style = ""
        self.bot_name = ""

    @staticmethod
    def _get_default_persona_prompt() -> str:
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

    def _save_data_sync(self, **_kwargs) -> None:
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

    def _schedule_data_save(self, *_args, **_kwargs) -> None:
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

    def test_legacy_female_fallbacks_remain_cleanup_only_compatibility_data(self) -> None:
        harness = CreativeMixin()
        for old_text in CREATIVE_LEGACY_FALLBACK_CHUNKS:
            self.assertTrue(harness._is_legacy_creative_fallback_chunk(old_text))
        self.assertFalse(any(old_text in CREATIVE_FALLBACK_CHUNKS for old_text in CREATIVE_LEGACY_FALLBACK_CHUNKS))

    def test_creative_persona_context_does_not_bind_soft_traits_to_gender(self) -> None:
        context = _CreativePersonaContextHarness()._creative_persona_style_context()

        self.assertIn("性别与代词边界", context)
        self.assertIn("未指定时不要默认女性或男性", context)
        self.assertIn("温柔、细腻、理性、锋利等表达气质不绑定任何性别", context)


class _ExtractHarness(_CreativeGenerationHarness):
    """Capture prompts so prompt-level regressions fail loudly."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(responses)
        self.prompts: list[str] = []

    async def _llm_call(self, prompt: str, **kwargs) -> str:
        self.prompts.append(str(prompt))
        return await super()._llm_call(prompt, **kwargs)


class _StyleScoreRejectHarness(_CreativeGenerationHarness):
    async def _review_creative_chunk(self, *_args, **_kwargs):
        return {
            "passed": True,
            "persona_score": 9,
            "progress_score": 9,
            "repetition_score": 9,
            "style_score": 3,
        }


class CreativePersonaPolishTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_prompt_contains_craft_rules_and_story_clock(self) -> None:
        harness = _ExtractHarness(["楼道里的声控灯亮了一下，又灭了。她数到第七级台阶。"])
        project = {
            "id": "project-craft",
            "title": "工艺断言",
            "premise": "测试",
            "tone": "安静",
            "target_chars": 1000,
            "current_chars": 0,
            "draft_chunks": [],
        }

        await harness._generate_creative_chunk(project, 180)

        prompt = harness.prompts[0]
        self.assertIn("写作工艺", prompt)
        self.assertIn("升华", prompt)
        self.assertIn("感到一阵", prompt)
        self.assertIn("故事内时间", prompt)
        self.assertIn("不能无声倒流", prompt)
        # 直接以作者人格写作，而不是“模拟拟人化 Bot”的元指令框架。
        self.assertNotIn("模拟拟人化 Bot", prompt)

    async def test_missing_style_score_keeps_legacy_review_output_passing(self) -> None:
        harness = _CreativeGenerationHarness(["晾在阳台的衬衫还在滴水，楼下有人骑车按了两下铃。"])
        project = {
            "id": "project-legacy-review",
            "title": "旧版审校兼容",
            "premise": "测试",
            "tone": "安静",
            "target_chars": 1000,
            "current_chars": 0,
            "draft_chunks": [],
        }

        result = await harness._generate_creative_chunk(project, 180)

        self.assertTrue(result)
        self.assertEqual(harness.llm_calls, 1)

    async def test_low_style_score_rejects_ai_flavored_chunk(self) -> None:
        harness = _StyleScoreRejectHarness(["时光如流水般匆匆逝去，岁月在心底刻下深深的痕迹。"] * (CREATIVE_SIMILARITY_RETRIES + 1))
        project = {
            "id": "project-style",
            "title": "AI味拦截",
            "premise": "测试",
            "tone": "安静",
            "target_chars": 1000,
            "current_chars": 0,
            "draft_chunks": [],
        }

        result = await harness._generate_creative_chunk(project, 180)

        self.assertEqual(result, "")
        self.assertEqual(harness.llm_calls, CREATIVE_SIMILARITY_RETRIES + 1)

    async def test_extraction_updates_story_time(self) -> None:
        harness = _CreativeGenerationHarness(
            [json.dumps({"mainline_direction": "她终于推开那扇门", "story_time": "雨停后的第二天清晨", "next_direction": "写她走出楼道"}, ensure_ascii=False)]
        )
        project = {
            "id": "project-clock",
            "title": "故事时钟",
            "premise": "测试",
            "tone": "安静",
            "story_bible": {"mainline_direction": "旧主线", "story_time": "雨夜"},
        }
        story_bible = harness._get_or_create_story_bible(project)

        extract = await harness._post_generation_extract(project, story_bible, "新的正文片段。", 3)

        self.assertEqual(extract.get("story_time"), "雨停后的第二天清晨")
        self.assertEqual(story_bible["story_time"], "雨停后的第二天清晨")

    async def test_extraction_keeps_old_story_time_when_absent(self) -> None:
        harness = _CreativeGenerationHarness(
            [json.dumps({"mainline_direction": "推进了一步"}, ensure_ascii=False)]
        )
        project = {
            "id": "project-clock-keep",
            "title": "保留旧时间",
            "premise": "测试",
            "tone": "安静",
            "story_bible": {"mainline_direction": "旧主线", "story_time": "雨夜"},
        }
        story_bible = harness._get_or_create_story_bible(project)

        await harness._post_generation_extract(project, story_bible, "新的正文片段。", 2)

        self.assertEqual(story_bible["story_time"], "雨夜")

    def test_story_bible_template_backfills_story_time_for_legacy_projects(self) -> None:
        harness = CreativeMixin()
        project = {"premise": "旧项目", "next_hint": "继续", "story_bible": {"mainline_direction": "只有旧字段"}}

        story_bible = harness._get_or_create_story_bible(project)

        self.assertIn("story_time", story_bible)
        self.assertEqual(story_bible["story_time"], "")

    def test_chunk_budget_shrinks_for_poetry_and_essays(self) -> None:
        harness = CreativeMixin()
        poem = {"work_type": "短诗", "target_chars": 300}
        essay = {"work_type": "生活随笔", "target_chars": 2000}
        novel = {"work_type": "短篇小说", "target_chars": 4000}

        self.assertLessEqual(harness._creative_chunk_budget_for(poem, 900), 150)
        self.assertLessEqual(harness._creative_chunk_budget_for(essay, 900), 400)
        self.assertEqual(harness._creative_chunk_budget_for(novel, 900), 900)

    def test_advance_gap_shorter_in_evening_than_morning(self) -> None:
        harness = CreativeMixin()
        harness.schedule_persona_prompt = ""
        harness.default_style = ""
        harness.bot_name = ""
        evening = datetime(2026, 8, 12, 21, 0).timestamp()
        morning = datetime(2026, 8, 12, 8, 0).timestamp()
        noon = datetime(2026, 8, 12, 13, 0).timestamp()

        with patch.object(creative_module.random, "random", return_value=0.99), \
                patch.object(creative_module.random, "randint", side_effect=lambda low, high: high):
            evening_gap = harness._creative_advance_gap_minutes({}, evening)
            morning_gap = harness._creative_advance_gap_minutes({}, morning)
            noon_gap = harness._creative_advance_gap_minutes({}, noon)

        self.assertLessEqual(evening_gap, int(320 * 0.62))
        self.assertGreaterEqual(morning_gap, int(95 * 1.8))
        self.assertGreater(morning_gap, evening_gap)
        self.assertEqual(noon_gap, 320)

    def test_advance_gap_persona_multiplier(self) -> None:
        slow = CreativeMixin()
        slow.schedule_persona_prompt = "慢热寡言，说话不多"
        energetic = CreativeMixin()
        energetic.schedule_persona_prompt = "活泼话多，元气急性子"
        noon = datetime(2026, 8, 12, 13, 0).timestamp()

        with patch.object(creative_module.random, "random", return_value=0.99), \
                patch.object(creative_module.random, "randint", side_effect=lambda low, high: high):
            slow_gap = slow._creative_advance_gap_minutes({}, noon)
            energetic_gap = energetic._creative_advance_gap_minutes({}, noon)

        self.assertEqual(slow_gap, int(320 * 1.35))
        self.assertEqual(energetic_gap, int(320 * 0.8))

    def test_advance_gap_uses_active_persona_style(self) -> None:
        class PersonaCreative(CreativeMixin):
            schedule_persona_prompt = ""
            default_style = ""
            bot_name = "主人格"

            def persona_setting(self, key, default=None):
                values = {
                    "schedule_persona_prompt": "慢热寡言",
                    "default_style": "",
                    "bot_name": "次人格",
                }
                return values.get(key, getattr(self, key, default))

        harness = PersonaCreative()
        noon = datetime(2026, 8, 12, 13, 0).timestamp()
        with patch.object(creative_module.random, "random", return_value=0.99), \
                patch.object(creative_module.random, "randint", side_effect=lambda low, high: high):
            self.assertEqual(int(320 * 1.35), harness._creative_advance_gap_minutes({}, noon))
        self.assertEqual("", harness.schedule_persona_prompt)

    def test_advance_gap_occasional_burst_does_not_chain(self) -> None:
        harness = CreativeMixin()
        harness.schedule_persona_prompt = ""
        harness.default_style = ""
        harness.bot_name = ""
        noon = datetime(2026, 8, 12, 13, 0).timestamp()
        project: dict = {}

        with patch.object(creative_module.random, "random", return_value=0.01), \
                patch.object(creative_module.random, "randint", side_effect=lambda low, high: high):
            burst_gap = harness._creative_advance_gap_minutes(project, noon)
            chained_gap = harness._creative_advance_gap_minutes(project, noon + 60)

        self.assertEqual(burst_gap, 60)
        self.assertGreater(project.get("last_creative_burst_at", 0), 0)
        # 3 小时内的第二次调用不再触发爆发档，回到普通区间。
        self.assertGreater(chained_gap, 60)


class QzonePublishStylePromptTests(unittest.TestCase):
    def test_life_post_style_prompt_bans_template_openings(self) -> None:
        prompt = QzonePublishMixin()._qzone_publish_style_prompt()

        self.assertIn("今天也是", prompt)
        self.assertIn("只写一个具体画面加一个动作", prompt)
        self.assertIn("不像这个人格会说的话", prompt)


if __name__ == "__main__":
    unittest.main()
