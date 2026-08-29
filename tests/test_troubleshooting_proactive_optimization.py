# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


USER_ID = "100000001"
PLATFORM_INSTANCE = "测试官方实例"
OFFICIAL_OPENID = "test-openid-owner-002"
STALE_UMO = f"{PLATFORM_INSTANCE}:FriendMessage:{USER_ID}"
OFFICIAL_UMO = f"{PLATFORM_INSTANCE}:FriendMessage:{OFFICIAL_OPENID}"


class _ProactiveTestPlugin:
    def __init__(self) -> None:
        self.context = object()
        self._data_lock = asyncio.Lock()
        self.data = {
            "users": {
                USER_ID: {
                    "user_id": USER_ID,
                    "nickname": "测试用户",
                    "enabled": True,
                    "umo": STALE_UMO,
                }
            }
        }
        self.kick_calls = 0

    def _get_user(self, user_id: str):
        return self.data["users"][str(user_id)]

    @staticmethod
    def _private_user_role(_user, _user_id: str = "") -> str:
        return "owner"

    @staticmethod
    def _reset_planned_proactive_delivery_state(_user) -> None:
        return None

    @staticmethod
    def _proactive_impulse_default_window_seconds(_reason: str) -> tuple[float, float]:
        return 600.0, 300.0

    @staticmethod
    def _planned_proactive_semantics(_user) -> dict:
        return {
            "kind": "check_in",
            "anchor_type": "explicit_request",
            "score": 0.8,
            "note": "用户明确发起测试",
        }

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        return None

    @staticmethod
    def _private_delivery_umo_for_user_id(_user_id: str) -> str:
        return OFFICIAL_UMO

    async def _kick_proactive_loop_once(self) -> None:
        self.kick_calls += 1


class _TroubleshootingResultHarness(DailyStateMixin):
    def __init__(self) -> None:
        self.data = {"troubleshooting_test_results": {}}

    @staticmethod
    def _format_timestamp_elapsed(_value) -> str:
        return "刚刚"

    @staticmethod
    def _proactive_visible_text_preview(value) -> str:
        return str(value or "")[:220]


class _AutoTunePlugin:
    def __init__(self) -> None:
        self.enable_personality_iteration_experiment = True
        self.enable_personality_iteration_auto_tune = True
        self.proactive_intensity_preset = "balanced"
        self.max_daily_messages = 6
        self.idle_minutes = 30
        self.min_interval_minutes = 90
        self.proactive_persona_judge_send_threshold = 62
        self.proactive_review_strength = "balanced"
        self.config = {}
        self._data_lock = asyncio.Lock()
        self.data = {
            "personality_iteration_auto_tune": {
                "manual_values": {
                    "proactive_intensity_preset": "balanced",
                    "max_daily_messages": 8,
                    "idle_minutes": 30,
                    "min_interval_minutes": 90,
                    "proactive_persona_judge_send_threshold": 62,
                    "proactive_review_strength": "balanced",
                },
                "applied": {"max_daily_messages": 6},
            }
        }

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        return None


class TroubleshootingProactiveOptimizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_chain_test_uses_current_delivery_umo_and_tracks_waiting_state(self) -> None:
        plugin = _ProactiveTestPlugin()
        api = PrivateCompanionPageApi(plugin)

        result = await api._run_proactive_message_chain_test({"user_id": USER_ID, "delay_seconds": 5})

        self.assertTrue(result["pending"])
        self.assertEqual("waiting_schedule", result["outcome_type"])
        self.assertEqual(OFFICIAL_UMO, result["umo"])
        self.assertEqual(OFFICIAL_UMO, plugin.data["users"][USER_ID]["umo"])
        self.assertIn("已切换到当前有效投递会话", result["steps"][0]["detail"])
        task = plugin._troubleshooting_proactive_wakeup_tasks[USER_ID]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_scheduled_wakeup_kicks_proactive_loop_once_and_cleans_itself(self) -> None:
        plugin = _ProactiveTestPlugin()
        api = PrivateCompanionPageApi(plugin)

        task = api._schedule_troubleshooting_proactive_wakeup(USER_ID, time.time() + 0.01)

        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(1, plugin.kick_calls)
        self.assertNotIn(USER_ID, plugin._troubleshooting_proactive_wakeup_tasks)

    async def test_auto_tune_waits_for_stable_clear_observations_before_restore(self) -> None:
        plugin = _AutoTunePlugin()
        api = PrivateCompanionPageApi(plugin)
        api.PERSONALITY_AUTO_TUNE_RECOVERY_STREAK = 3
        api.PERSONALITY_AUTO_TUNE_RECOVERY_MIN_SECONDS = 0
        api._personality_iteration_suggestions = lambda _users, _groups: []
        api._normalize_setting_value = lambda _key, value: value
        api._apply_config_value = lambda key, value: setattr(plugin, key, value)
        api._save_config_if_possible = AsyncMock(return_value=True)

        first = await api._maybe_apply_personality_iteration_auto_tune({}, {})
        second = await api._maybe_apply_personality_iteration_auto_tune({}, {})

        self.assertTrue(first["pending_restore"])
        self.assertTrue(second["pending_restore"])
        self.assertEqual(6, plugin.max_daily_messages)

        third = await api._maybe_apply_personality_iteration_auto_tune({}, {})

        self.assertEqual(8, plugin.max_daily_messages)
        self.assertEqual(8, third["restored"]["max_daily_messages"])

    def test_intermediate_result_stays_pending_and_has_explicit_stage(self) -> None:
        harness = _TroubleshootingResultHarness()
        user = {
            "umo": OFFICIAL_UMO,
            "troubleshooting_proactive_started_at": time.time() - 1,
            "troubleshooting_proactive_steps": [],
        }

        harness._record_troubleshooting_proactive_result(
            USER_ID,
            user,
            ok=True,
            pending=True,
            outcome_type="reviewing",
            detail="主动消息已生成，准备发送前复核",
        )

        result = harness.data["troubleshooting_test_results"]["proactive_message"]
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertEqual("reviewing", result["outcome_type"])


if __name__ == "__main__":
    unittest.main()
