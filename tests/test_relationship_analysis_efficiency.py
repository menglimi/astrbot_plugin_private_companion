# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


ROOT = Path(__file__).resolve().parents[1]


class _RelationshipHarness(UserMemoryMixin):
    def __init__(self, *, persona: str = "自然、尊重边界") -> None:
        self.data = {
            "users": {
                "user-1": {
                    "umo": "default:FriendMessage:user-1",
                    "inbound_count": 1,
                    "proactive_sent_count": 0,
                    "reply_count": 0,
                    "ignored_streak": 0,
                    "relationship_score": 3,
                    "last_user_message": "你好",
                    "last_user_message_at": 1_000.0,
                }
            }
        }
        self._data_lock = asyncio.Lock()
        self.relationship_analysis_provider_id = "relationship-provider"
        self.mai_style_provider_id = "fallback-provider"
        self.relationship_analysis_min_interval_minutes = 45
        self.relationship_analysis_interaction_batch = 8
        self.relationship_analysis_max_stale_hours = 8
        self.enable_relationship_analysis = True
        self.persona = persona
        self.llm_calls = 0
        self.prompts: list[str] = []
        self.saved = 0
        self.llm_started = asyncio.Event()
        self.llm_release: asyncio.Event | None = None

    def _get_user(self, user_id: str):
        return self.data["users"][user_id]

    def _save_data_sync(self) -> None:
        self.saved += 1

    async def _refresh_default_persona_prompt(self, _umo: str = "") -> str:
        return self.persona

    async def _llm_call(self, prompt: str, **_kwargs) -> str:
        self.llm_calls += 1
        self.prompts.append(prompt)
        self.llm_started.set()
        if self.llm_release is not None:
            await self.llm_release.wait()
        return '{"level":"熟悉","preference":"普通","score":55,"note":"互动稳定，保持自然距离"}'

    @staticmethod
    def _task_provider(primary: str, fallback: str = "") -> str:
        return primary or fallback

    @staticmethod
    def _extract_json_payload(text: str):
        return json.loads(text)


class RelationshipAnalysisEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    def test_independent_toggle_is_exposed_in_schema_panel_and_page_api(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        grouped = schema["emotion_relationship_config"]["items"]["enable_relationship_analysis"]
        panel = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")

        self.assertTrue(grouped["default"])
        self.assertIn("关闭后不再产生", grouped["hint"])
        self.assertIn('key: "enable_relationship_analysis"', panel)
        self.assertIn('"enable_relationship_analysis"', page_api)

    async def test_initial_analysis_runs_once_and_ordinary_followup_reuses_it(self) -> None:
        harness = _RelationshipHarness()
        user = harness._get_user("user-1")

        first = await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")
        second = await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(harness.llm_calls, 1)
        self.assertEqual(user["persona_relationship"]["analysis_reason"], "initial")
        self.assertIn("source_metrics", user["persona_relationship"])

    async def test_accumulated_interactions_refresh_with_full_persona_context(self) -> None:
        harness = _RelationshipHarness(persona="人格开头" + "设定" * 2_000 + "人格结尾")
        user = harness._get_user("user-1")
        await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")

        profile = user["persona_relationship"]
        profile["analyzed_at_ts"] -= 60 * 60
        user["inbound_count"] += 8
        user["last_user_message"] = "最近聊了不少日常"
        refreshed = await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")

        self.assertTrue(refreshed)
        self.assertEqual(harness.llm_calls, 2)
        self.assertEqual(user["persona_relationship"]["analysis_reason"], "interaction_batch")
        self.assertIn(harness.persona, harness.prompts[0])
        self.assertIn(harness.persona, harness.prompts[1])
        self.assertNotIn("中间设定本轮省略", harness.prompts[1])

    async def test_disabled_analysis_never_calls_model(self) -> None:
        harness = _RelationshipHarness()
        harness.enable_relationship_analysis = False
        user = harness._get_user("user-1")

        refreshed = await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")

        self.assertFalse(refreshed)
        self.assertEqual(harness.llm_calls, 0)
        self.assertNotIn("persona_relationship", user)

    async def test_durable_boundary_can_refresh_without_waiting_for_normal_interval(self) -> None:
        harness = _RelationshipHarness()
        user = harness._get_user("user-1")
        await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")

        user["last_user_message"] = "以后不要再这样叫我"
        user["last_user_message_at"] = 2_000.0
        user["intent_profile"] = {
            "intent": "boundary",
            "source": "durable_boundary_rule",
            "confidence": 0.9,
            "boundary_durable": True,
        }
        refreshed = await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")

        self.assertTrue(refreshed)
        self.assertEqual(harness.llm_calls, 2)
        self.assertEqual(user["persona_relationship"]["analysis_reason"], "durable_boundary")

    async def test_concurrent_initial_requests_are_coalesced_by_background_lock(self) -> None:
        harness = _RelationshipHarness()
        harness.llm_release = asyncio.Event()
        user = harness._get_user("user-1")

        first_task = asyncio.create_task(
            harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")
        )
        await harness.llm_started.wait()
        second = await harness._refresh_persona_relationship("user-1", dict(user), trigger="inbound")
        harness.llm_release.set()
        first = await first_task

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(harness.llm_calls, 1)


if __name__ == "__main__":
    unittest.main()
