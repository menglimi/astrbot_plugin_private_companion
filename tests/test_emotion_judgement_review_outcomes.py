# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


ROOT = Path(__file__).resolve().parents[1]


class _JudgementHarness(UserMemoryMixin):
    enable_intent_emotion_analysis = True

    def __init__(self, raw: str | Exception) -> None:
        self.raw = raw
        self.data = {
            "users": {
                "user-1": {
                    "pending_emotion_judgement": {
                        "text": "这张图里是什么？",
                    },
                    "last_emotion_judgement_error": "legacy-error",
                }
            }
        }
        self._data_lock = asyncio.Lock()
        self.applied_intents: list[dict] = []
        self.saved = 0

    def _emotion_judgement_provider_id(self) -> str:
        return "review-provider"

    async def _llm_call(self, *_args, **_kwargs) -> str:
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw

    @staticmethod
    def _extract_json_payload(raw: str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][user_id]

    def _update_relationship_state_from_intent(self, _user: dict, intent: dict) -> None:
        self.applied_intents.append(dict(intent))

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class _ConcurrentJudgementHarness(_JudgementHarness):
    def __init__(self) -> None:
        super().__init__("")
        self.started = [asyncio.Event(), asyncio.Event()]
        self.release = [asyncio.Event(), asyncio.Event()]
        self.call_count = 0

    async def _llm_call(self, *_args, **_kwargs) -> str:
        index = self.call_count
        self.call_count += 1
        self.started[index].set()
        await self.release[index].wait()
        if index == 0:
            return (
                '{"event":"hurt","target":"bot","intensity":72,'
                '"confidence":0.91,"reason":"旧任务结果"}'
            )
        return (
            '{"event":"neutral","target":"none","intensity":0,'
            '"confidence":0.93,"reason":"新任务结果"}'
        )


def _local_intent() -> dict:
    return {
        "intent": "chat",
        "emotion_event": "neutral",
        "emotion_target": "none",
        "emotion_intensity": 0,
        "emotion_confidence": 0.5,
        "source": "local",
        "text": "这张图里是什么？",
    }


class EmotionJudgementReviewOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_low_confidence_result_keeps_local_without_failure(self) -> None:
        harness = _JudgementHarness(
            '{"event":"neutral","target":"none","intensity":0,'
            '"confidence":0.62,"reason":"Neutral image question."}'
        )

        await harness._refine_inbound_emotion_with_model(
            "user-1",
            "这张图里是什么？",
            _local_intent(),
        )

        user = harness._get_user("user-1")
        self.assertNotIn("last_emotion_judgement_error", user)
        self.assertEqual("kept_local", user["last_emotion_judgement"]["status"])
        self.assertEqual("low_confidence", user["last_emotion_judgement"]["outcome"])
        self.assertEqual(0.62, user["last_emotion_judgement"]["confidence"])
        self.assertEqual("local", harness.applied_intents[-1]["source"])

    async def test_applied_result_clears_an_older_failure(self) -> None:
        harness = _JudgementHarness(
            '{"event":"neutral","target":"none","intensity":0,'
            '"confidence":0.91,"reason":"Neutral question."}'
        )

        await harness._refine_inbound_emotion_with_model(
            "user-1",
            "这张图里是什么？",
            _local_intent(),
        )

        user = harness._get_user("user-1")
        self.assertNotIn("last_emotion_judgement_error", user)
        self.assertEqual("applied", user["last_emotion_judgement"]["status"])
        self.assertTrue(harness.applied_intents[-1]["llm_emotion_judgement"])

    async def test_invalid_response_records_only_a_stable_error_code(self) -> None:
        raw = '{"event":"neutral","confidence":0.9,"reason":"PRIVATE RAW RESPONSE"}'
        harness = _JudgementHarness(raw)

        await harness._refine_inbound_emotion_with_model(
            "user-1",
            "这张图里是什么？",
            _local_intent(),
        )

        user = harness._get_user("user-1")
        self.assertEqual("invalid_response", user["last_emotion_judgement_error"])
        self.assertNotIn("PRIVATE RAW RESPONSE", json.dumps(user, ensure_ascii=False))
        self.assertEqual("failed", user["last_emotion_judgement"]["status"])

    async def test_request_exception_does_not_persist_exception_text(self) -> None:
        harness = _JudgementHarness(OSError("PRIVATE PATH C:/secret/review.json"))

        with patch("astrbot_plugin_private_companion.user_memory.logger.debug") as debug_log:
            await harness._refine_inbound_emotion_with_model(
                "user-1",
                "这张图里是什么？",
                _local_intent(),
            )

        user = harness._get_user("user-1")
        self.assertEqual("request_failed", user["last_emotion_judgement_error"])
        self.assertNotIn("PRIVATE PATH", json.dumps(user, ensure_ascii=False))
        self.assertNotIn("PRIVATE PATH", repr(debug_log.call_args))
        self.assertIn("OSError", repr(debug_log.call_args))

    async def test_stale_review_cannot_overwrite_a_new_identical_message(self) -> None:
        harness = _ConcurrentJudgementHarness()
        old_local = _local_intent()
        old_local["source"] = "old-local"
        new_local = _local_intent()
        new_local["source"] = "new-local"
        user = harness._get_user("user-1")
        user["pending_emotion_judgement"] = {
            "review_id": "old-review",
            "text": "相同消息",
        }

        old_task = asyncio.create_task(
            harness._refine_inbound_emotion_with_model(
                "user-1",
                "相同消息",
                old_local,
                review_id="old-review",
            )
        )
        await harness.started[0].wait()
        user["pending_emotion_judgement"] = {
            "review_id": "new-review",
            "text": "相同消息",
        }
        new_task = asyncio.create_task(
            harness._refine_inbound_emotion_with_model(
                "user-1",
                "相同消息",
                new_local,
                review_id="new-review",
            )
        )
        await harness.started[1].wait()

        harness.release[0].set()
        await old_task
        self.assertEqual(
            "new-review",
            user["pending_emotion_judgement"]["review_id"],
        )
        harness.release[1].set()
        await new_task

        self.assertEqual({}, user["pending_emotion_judgement"])
        self.assertEqual("neutral", user["last_emotion_judgement"]["event"])
        self.assertEqual("new-local", harness.applied_intents[-1]["source"])

    def test_page_api_hides_legacy_raw_json_error(self) -> None:
        api = object.__new__(PrivateCompanionPageApi)
        legacy = (
            '{"event":"neutral","target":"none","intensity":0,'
            '"confidence":0.62,"reason":"truncated'
        )

        self.assertEqual("", api._emotion_judgement_error_summary(legacy))
        self.assertEqual(
            "模型返回格式无效",
            api._emotion_judgement_error_summary("invalid_response"),
        )

    def test_panel_copies_share_structured_review_rendering(self) -> None:
        english_path = ROOT / "pages" / "companion-panel" / "app.js"
        chinese_path = ROOT / "pages" / "陪伴面板" / "app.js"
        english = english_path.read_text(encoding="utf-8")
        chinese = chinese_path.read_text(encoding="utf-8")

        self.assertEqual(english, chinese)
        self.assertIn("function emotionJudgementStatusText", english)
        self.assertIn("置信度不足，保留本地判断", english)
        self.assertIn('Object.prototype.hasOwnProperty.call(value, "confidence")', english)


if __name__ == "__main__":
    unittest.main()
