# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _PassiveReviewHarness(UserMemoryMixin):
    enable_passive_response_review = False
    enable_response_self_review = True
    passive_review_mode = "full"
    passive_review_strength = "strict"
    response_review_provider_id = "review"
    mai_style_provider_id = ""
    response_review_max_chars = 260

    def __init__(self) -> None:
        self.llm_called = False

    @staticmethod
    def _environment_now() -> datetime:
        return datetime(2026, 7, 15, 15, 30)

    async def _llm_call(self, *_args, **_kwargs):
        self.llm_called = True
        return "不应调用"


class _LenientPassiveReviewHarness(_PassiveReviewHarness):
    enable_passive_response_review = True
    passive_review_strength = "lenient"

    @staticmethod
    def _response_review_flags(*_args, **_kwargs):
        return ["repeats_last_bot_message"]

    async def _llm_call(self, *_args, **_kwargs):
        self.llm_called = True
        return self._response_review_drop_marker()

    @staticmethod
    async def _resolve_proactive_persona_prompt(_user):
        return ""

    @staticmethod
    def _format_private_fact_attribution_guard(*_args, **_kwargs):
        return ""

    @staticmethod
    def _format_reply_style_prompt():
        return ""

    @staticmethod
    def _task_provider(*_args):
        return "review"


class ReviewDecouplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabling_passive_review_skips_model_even_when_legacy_alias_is_on(self):
        harness = _PassiveReviewHarness()

        result = await harness._review_and_rewrite_response({}, "继续讲", "这是正常回复。")

        self.assertEqual(result, "这是正常回复。")
        self.assertFalse(harness.llm_called)

    async def test_lenient_passive_review_does_not_drop_reply(self):
        harness = _LenientPassiveReviewHarness()
        user = {"last_companion_message": "上一条回复"}

        result = await harness._review_and_rewrite_response(user, "继续讲", "这是正常回复。")

        self.assertEqual(result, "这是正常回复。")
        self.assertTrue(harness.llm_called)


if __name__ == "__main__":
    unittest.main()
