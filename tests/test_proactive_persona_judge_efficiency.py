# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


class _JudgeHarness(ProactiveEngineMixin):
    def __init__(self):
        self.data = {"token_usage": {"by_day_task": {}}}
        self.proactive_persona_judge_cache_minutes = 180
        self.proactive_persona_judge_max_daily = 12
        self.enable_llm_proactive_persona_judge = True
        self.default_nickname = "用户"
        self.llm_calls = 0

    def _get_default_persona_prompt(self):
        return "自然、尊重边界"

    def _format_worldview_adaptation_prompt(self):
        return ""

    def _private_user_role(self, user):
        return user.get("role", "primary")

    def _planned_proactive_semantics(self, user):
        return dict(user.get("semantics") or {
            "kind": "daily", "anchor_type": "topic", "need_layer": "social", "need_drive": "share",
            "score": 0.82, "pressure": 0.2, "risk": 0.05,
        })

    def _planned_proactive_persona_alignment(self, user, *, now=None):
        return dict(user.get("alignment") or {"score": 0.82, "blocker": False, "note": "自然"})

    async def _llm_call(self, *args, **kwargs):
        self.llm_calls += 1
        return '{"decision":"send","score":88,"reason":"自然"}'

    def _format_proactive_model_judge_prompt(self, user):
        return "judge"

    def _normalize_legacy_proactive_text(self, value, limit=40):
        return str(value or "")[:limit]

    def _task_provider(self, *args):
        return ""


def _user(**updates):
    value = {
        "role": "primary", "planned_proactive_source": "random", "planned_proactive_reason": "share",
        "planned_proactive_action": "message", "planned_proactive_topic": "刚看到一件有趣的小事",
        "planned_proactive_motive": "顺手分享一个具体片段", "planned_proactive_impulse_id": "first",
        "ignored_streak": 0,
    }
    value.update(updates)
    return value


class ProactivePersonaJudgeEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    def test_signature_ignores_impulse_identity(self):
        harness = _JudgeHarness()
        self.assertEqual(
            harness._planned_proactive_model_judge_signature(_user(planned_proactive_impulse_id="a")),
            harness._planned_proactive_model_judge_signature(_user(planned_proactive_impulse_id="b")),
        )

    def test_multi_entry_cache_survives_current_plan_fields_being_cleared(self):
        harness = _JudgeHarness()
        user = _user()
        signature = harness._planned_proactive_model_judge_signature(user)
        harness._cache_proactive_model_judgement(
            user, {"signature": signature, "decision": "send", "score": 88, "reason": "自然"}, now=1_000.0
        )
        user["planned_proactive_model_judge_signature"] = ""
        user["planned_proactive_model_judge_result"] = {}
        user["planned_proactive_model_judge_at"] = 0
        cached = harness._cached_proactive_model_judgement(user, signature=signature, now=1_100.0)
        self.assertEqual(cached["decision"], "send")

    async def test_high_confidence_candidate_skips_model(self):
        harness = _JudgeHarness()
        result = await harness._review_planned_proactive_with_model(_user(), now=1_000.0)
        self.assertEqual(result["decision"], "send")
        self.assertTrue(result["local"])
        self.assertEqual(harness.llm_calls, 0)

    async def test_zero_daily_limit_falls_back_without_calling_model(self):
        harness = _JudgeHarness()
        harness.proactive_persona_judge_max_daily = 0
        result = await harness._review_planned_proactive_with_model(_user(role="friend"), now=1_000.0)
        self.assertEqual(result["decision"], "send")
        self.assertTrue(result["local"])
        self.assertEqual(harness.llm_calls, 0)


if __name__ == "__main__":
    unittest.main()
