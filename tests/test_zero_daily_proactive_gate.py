# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


class _ZeroLimitHarness(ProactiveMixin, ProactiveEngineMixin, DailyStateMixin):
    def __init__(self) -> None:
        self.max_daily_messages = 0
        self.config = {"max_daily_messages": 0}
        self.data = {
            "users": {
                "10001": {
                    "user_id": "10001",
                    "enabled": True,
                    "umo": "default:FriendMessage:10001",
                    "next_proactive_at": 12345,
                    "planned_proactive_reason": "check_in",
                    "planned_proactive_action": "message",
                    "planned_proactive_source": "troubleshooting",
                    "planned_candidate_id": "candidate-1",
                    "proactive_impulses": [{"id": "impulse-1", "state": "queued"}],
                    "pending_followup_event": {"reason": "check_in"},
                    "suspended_proactive": {"active": True},
                    "pending_proactive_send_retry": {"active": True},
                    "proactive_sending": True,
                }
            },
            "proactive_candidate_pool": [
                {"id": "candidate-1", "user_id": "10001", "status": "accepted"},
                {"id": "old-sent", "user_id": "10001", "status": "sent"},
            ],
        }
        self._data_lock = asyncio.Lock()
        self.saved = 0
        self.maintenance_calls = 0

    def _proactive_intensity_ignores_daily_limit(self) -> bool:
        return True

    def _effective_proactive_int(self, _key, value, **_kwargs) -> int:
        return int(value)

    def _private_user_role(self, _user, _user_id="") -> str:
        return "owner"

    def _user_enabled_for_proactive(self, _user_id, _user) -> bool:
        return True

    def _recover_stale_proactive_sending(self, _user) -> None:
        return None

    def _clear_planned_proactive_trigger(self, user) -> None:
        user["planned_proactive_trigger_message_id"] = ""
        user["planned_proactive_trigger_umo"] = ""
        user["planned_proactive_trigger_ts"] = 0

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1

    async def _maybe_settle_skill_growth(self):
        self.maintenance_calls += 1

    async def _maybe_trigger_bilibili_boredom_watch(self):
        self.maintenance_calls += 1

    async def _maybe_trigger_web_exploration(self):
        self.maintenance_calls += 1

    async def _maybe_track_ai_daily(self):
        self.maintenance_calls += 1

    async def _maybe_trigger_news_boredom_read(self):
        self.maintenance_calls += 1

    async def _maybe_trigger_jm_cosmos_boredom_read(self):
        self.maintenance_calls += 1

    async def _maybe_publish_qzone_life_post(self):
        self.maintenance_calls += 1

    async def _maybe_process_qzone_comment_inbox(self):
        self.maintenance_calls += 1

    async def _maybe_schedule_private_reading_recommendation_request(self):
        self.maintenance_calls += 1


class ZeroDailyProactiveGateTests(unittest.IsolatedAsyncioTestCase):
    def test_zero_global_limit_wins_over_high_intensity(self) -> None:
        harness = _ZeroLimitHarness()
        user = harness.data["users"]["10001"]
        self.assertEqual(harness._runtime_max_daily_messages(), 0)
        self.assertEqual(harness._effective_user_daily_limit(user), 0)
        self.assertTrue(harness._proactive_generation_disabled(user))

    def test_zero_limit_rejects_new_impulses_and_candidates(self) -> None:
        harness = _ZeroLimitHarness()
        user = harness.data["users"]["10001"]
        impulse = harness._queue_proactive_impulse(
            user,
            {"reason": "check_in", "source": "random", "topic": "new thought"},
        )
        candidate = harness._record_proactive_candidate(
            "10001",
            {"reason": "check_in", "source": "random", "topic": "new candidate"},
            status="accepted",
            user=user,
        )
        self.assertEqual(impulse, {})
        self.assertEqual(candidate, {})
        self.assertEqual(len(harness.data["proactive_candidate_pool"]), 2)

    def test_zero_limit_blocks_simulation_and_troubleshooting_send(self) -> None:
        harness = _ZeroLimitHarness()
        user = harness.data["users"]["10001"]
        allowed, reason = harness._should_send(user)
        self.assertFalse(allowed)
        self.assertIn("每日上限为 0", reason)
        self.assertEqual(user["next_proactive_at"], 0)
        self.assertEqual(user["proactive_impulses"], [])
        self.assertEqual(user["pending_followup_event"], {})
        self.assertFalse(user["proactive_sending"])

    async def test_zero_limit_tick_clears_existing_active_state_and_skips_maintenance(self) -> None:
        harness = _ZeroLimitHarness()
        await harness._tick()
        await harness._run_proactive_maintenance_tasks()

        user = harness.data["users"]["10001"]
        self.assertEqual(user["next_proactive_at"], 0)
        self.assertEqual(user["proactive_impulses"], [])
        self.assertEqual(harness.data["proactive_candidate_pool"][0]["status"], "blocked")
        self.assertEqual(harness.data["proactive_candidate_pool"][1]["status"], "sent")
        self.assertTrue(harness.data["proactive_runtime"]["generation_disabled"])
        self.assertEqual(harness.maintenance_calls, 0)
        self.assertGreaterEqual(harness.saved, 1)

    async def test_zero_limit_keeps_official_timer_unchanged(self) -> None:
        harness = _ZeroLimitHarness()
        timer = {
            "id": "timer-1",
            "backend": "astrbot_cron",
            "status": "scheduled",
            "job_id": "cron-job-1",
            "scheduled_ts": time.time() + 3600,
        }
        harness.data["users"]["10001"]["llm_timer_event"] = dict(timer)

        await harness._tick()

        self.assertEqual(harness.data["users"]["10001"]["llm_timer_event"], timer)

    async def test_positive_limit_immediately_kicks_scheduler(self) -> None:
        kicked = asyncio.Event()

        async def kick() -> None:
            kicked.set()

        plugin = SimpleNamespace(config=None, max_daily_messages=0, _kick_proactive_loop_once=kick)
        api = PrivateCompanionPageApi(plugin)
        api._apply_config_value("max_daily_messages", 4)

        await asyncio.wait_for(kicked.wait(), timeout=1.0)
        self.assertEqual(plugin.max_daily_messages, 4)


if __name__ == "__main__":
    unittest.main()
