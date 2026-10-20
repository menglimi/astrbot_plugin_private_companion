# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class OpenLoopAutoResolveHarness(UserMemoryMixin):
    enable_open_loop_tracking = True
    dialogue_episode_provider_id = ""
    mai_style_provider_id = ""
    open_loop_auto_resolve_window_hours = 72

    def __init__(self, verdict: dict | None = None) -> None:
        self._data_lock = asyncio.Lock()
        self.user_id = "owner-1"
        self.verdict = verdict if verdict is not None else {
            "decision": "resolved",
            "reason": "用户表示知道了",
            "updated_text": "",
        }
        self.llm_calls = 0
        self.retries: list[str] = []
        self.last_prompt = ""
        self.data = {"users": {self.user_id: {"user_id": self.user_id, "relationship_role": "owner"}}}

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][user_id]

    @staticmethod
    def _extract_json_payload(value: str) -> dict:
        return json.loads(value)

    @staticmethod
    def _task_provider(*_args, **_kwargs) -> str:
        return ""

    def _save_data_sync(self, **_kwargs) -> None:
        return None

    async def _try_acquire_user_background_task(self, *_args, **_kwargs) -> bool:
        return True

    async def _mark_user_background_retry(self, _user_id, _task, _now, error) -> None:
        self.retries.append(str(error))

    async def _llm_call(self, prompt: str, **_kwargs) -> str:
        self.llm_calls += 1
        self.last_prompt = prompt
        return json.dumps(self.verdict, ensure_ascii=False)

    def make_followup_context(
        self,
        text: str = "周日组会的结果没细说",
        *,
        reply: str = "好的，知道了",
        asked_seconds_ago: float = 60,
    ) -> dict:
        user = self._get_user(self.user_id)
        user["last_user_message"] = reply
        user["open_loops"] = [{
            "text": text,
            "status": "待自然延续",
            "created_ts": time.time() - 3 * 86400,
        }]
        user["open_loop_followup_context"] = {
            "text": text,
            "created_ts": time.time() - 3 * 86400,
            "picked_ts": time.time() - asked_seconds_ago - 300,
            "asked_ts": time.time() - asked_seconds_ago,
        }
        return user


class OpenLoopAutoResolveTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolved_verdict_closes_loop_and_clears_context(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context(reply="好的，知道了")
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        current = harness._get_user(harness.user_id)
        self.assertEqual("已完成", current["open_loops"][0]["status"])
        self.assertIn("resolved_ts", current["open_loops"][0])
        self.assertEqual({}, current["open_loop_followup_context"])
        self.assertEqual(1, harness.llm_calls)
        self.assertIn("周日组会的结果没细说", harness.last_prompt)
        self.assertIn("好的，知道了", harness.last_prompt)

    async def test_cancelled_verdict_closes_loop(self):
        harness = OpenLoopAutoResolveHarness(
            verdict={"decision": "cancelled", "reason": "用户不想聊", "updated_text": ""}
        )
        user = harness.make_followup_context(reply="别问了，不想聊这个")
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        current = harness._get_user(harness.user_id)
        self.assertEqual("已取消", current["open_loops"][0]["status"])
        self.assertEqual({}, current["open_loop_followup_context"])

    async def test_keep_verdict_retains_context_for_later_replies(self):
        harness = OpenLoopAutoResolveHarness(
            verdict={"decision": "keep", "reason": "用户没回应这个话题", "updated_text": ""}
        )
        user = harness.make_followup_context(reply="今天在忙别的")
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        current = harness._get_user(harness.user_id)
        self.assertEqual("待自然延续", current["open_loops"][0]["status"])
        self.assertTrue(current["open_loop_followup_context"])

    async def test_keep_verdict_can_update_loop_text(self):
        harness = OpenLoopAutoResolveHarness(
            verdict={"decision": "keep", "reason": "还有下文", "updated_text": "他说过两天再说"}
        )
        user = harness.make_followup_context(reply="还没出结果，过两天再说")
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        current = harness._get_user(harness.user_id)
        self.assertEqual("他说过两天再说", current["open_loops"][0]["text"])
        self.assertEqual("他说过两天再说", current["open_loop_followup_context"]["text"])

    async def test_stale_asked_context_is_cleared_without_model_call(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context(asked_seconds_ago=4 * 86400)
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        self.assertEqual({}, harness._get_user(harness.user_id)["open_loop_followup_context"])
        self.assertEqual(0, harness.llm_calls)

    async def test_pending_ask_without_asked_ts_is_left_for_after_send(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context()
        user["open_loop_followup_context"].pop("asked_ts")
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        self.assertTrue(harness._get_user(harness.user_id)["open_loop_followup_context"])
        self.assertEqual(0, harness.llm_calls)

    async def test_legacy_context_without_ask_bookkeeping_is_cleared(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context()
        user["open_loop_followup_context"] = {
            "text": "周日组会的结果没细说",
            "created_ts": time.time() - 20 * 86400,
        }
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        self.assertEqual({}, harness._get_user(harness.user_id)["open_loop_followup_context"])
        self.assertEqual(0, harness.llm_calls)

    async def test_empty_inbound_text_skips_judgement(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context(reply="")
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        self.assertTrue(harness._get_user(harness.user_id)["open_loop_followup_context"])
        self.assertEqual(0, harness.llm_calls)

    async def test_invalid_model_output_keeps_context_and_marks_retry(self):
        class BrokenHarness(OpenLoopAutoResolveHarness):
            async def _llm_call(self, prompt: str, **_kwargs) -> str:
                self.llm_calls += 1
                return "[]"

        harness = BrokenHarness()
        user = harness.make_followup_context()
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        self.assertTrue(harness._get_user(harness.user_id)["open_loop_followup_context"])
        self.assertEqual(["invalid_json"], harness.retries)

    async def test_already_closed_loop_clears_context_without_model_call(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context()
        user["open_loops"][0]["status"] = "已完成"
        await harness._maybe_resolve_open_loops_after_reply(harness.user_id, user)
        self.assertEqual({}, harness._get_user(harness.user_id)["open_loop_followup_context"])
        self.assertEqual(0, harness.llm_calls)

    def test_stamp_helper_only_touches_open_loop_followup_reason(self):
        harness = OpenLoopAutoResolveHarness()
        user = harness.make_followup_context()
        user["open_loop_followup_context"].pop("asked_ts")
        harness._stamp_open_loop_followup_asked(user, "check_in", 123.0)
        self.assertNotIn("asked_ts", user["open_loop_followup_context"])
        harness._stamp_open_loop_followup_asked(user, "open_loop_followup", 456.0)
        self.assertEqual(456.0, user["open_loop_followup_context"]["asked_ts"])


ROOT = Path(__file__).resolve().parents[1]


class OpenLoopWiringTests(unittest.TestCase):
    def test_asked_timestamp_is_stamped_only_on_send_success_paths(self):
        source = (ROOT / "daily_state_tick.py").read_text(encoding="utf-8")
        self.assertEqual(2, source.count("self._stamp_open_loop_followup_asked("))

    def test_picker_records_picked_ts_for_delivery_stamp(self):
        source = (ROOT / "proactive_engine.py").read_text(encoding="utf-8")
        self.assertEqual(1, source.count('"picked_ts": check_now'))

    def test_inbound_pipeline_submits_reply_judgement(self):
        source = (ROOT / "message_pipeline.py").read_text(encoding="utf-8")
        self.assertEqual(
            1,
            source.count("_maybe_resolve_open_loops_after_reply(user_id, user_snapshot)"),
        )


if __name__ == "__main__":
    unittest.main()
