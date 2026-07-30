# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _StateHarness(DailyStateMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self._daily_state_generation_lock = asyncio.Lock()
        self.enable_humanized_states = True
        self.data = {
            "state_conditions": [{"id": "old", "kind": "sleep"}],
            "state_generated_day": "2026-07-22",
            "daily_state": {"date": "2026-07-22", "marker": "old"},
        }
        self.generate_count = 0
        self.generate_error: Exception | None = None
        self.generation_started = asyncio.Event()
        self.generation_release = asyncio.Event()
        self.block_generation = False
        self.save_count = 0

    async def _ensure_weather_context(self, force: bool = False):
        return {"date": "2026-07-23", "prompt": "晴", "source": "test"}

    async def _ensure_yesterday_screen_diary_context(self, force: bool = False):
        return {}

    async def _ensure_yesterday_conversation_summary(self, force: bool = False):
        return {}

    async def _generate_state_conditions(self, _weather=None, *, deferred_state_updates=None):
        self.generate_count += 1
        self.generation_started.set()
        if self.block_generation:
            await self.generation_release.wait()
        if self.generate_error is not None:
            raise self.generate_error
        if isinstance(deferred_state_updates, dict):
            deferred_state_updates["dream_pick"] = ("测试梦境", "平稳", 0, 2)
        return [{"id": "new", "kind": "sleep"}]

    def _cleanup_expired_conditions(self) -> None:
        return None

    def _ensure_time_based_hunger_condition(self) -> None:
        return None

    def _compose_state_from_conditions(self, _weather=None):
        return {
            "date": "2026-07-23",
            "conditions": deepcopy(self.data.get("state_conditions", [])),
        }

    def _remember_daily_dream_pick(self, dream_pick) -> None:
        self.data["daily_dream_pick"] = dream_pick

    def _record_body_cycle_episode(self, condition) -> None:
        self.data["body_cycle_state"] = deepcopy(condition)

    def _save_data_sync(self) -> None:
        self.save_count += 1


class _DiaryHarness(DailyStateMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self._daily_diary_generation_lock = asyncio.Lock()
        self.enable_daily_diary = True
        self.max_diary_entries = 20
        self.data = {
            "bot_diaries": [],
            "diary_generated_day": "",
            "dream_fragments": [],
        }
        self.generated_diary = {
            "date": "2026-07-23",
            "summary": "刷新后的记录",
            "body": "刷新后的正文",
            "dream_fragments": [],
        }
        self.generate_count = 0
        self.generation_started = asyncio.Event()
        self.generation_release = asyncio.Event()
        self.block_generation = False
        self.save_count = 0

    def _is_daily_diary_due(self) -> bool:
        return True

    async def _generate_daily_diary(self):
        self.generate_count += 1
        self.generation_started.set()
        if self.block_generation:
            await self.generation_release.wait()
        return deepcopy(self.generated_diary)

    def _merge_dream_fragment_pool(self, fragments):
        return list(fragments or [])

    async def _memory_companion_record_dream_fragment(self, **_kwargs) -> None:
        return None

    def _save_data_sync(self) -> None:
        self.save_count += 1


class DailyStateGenerationSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_state_failure_preserves_previous_state(self) -> None:
        harness = _StateHarness()
        harness.generate_error = RuntimeError("dream provider failed")
        before = deepcopy(harness.data)

        with (
            patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"),
            self.assertRaisesRegex(RuntimeError, "dream provider failed"),
        ):
            await harness._ensure_daily_state(force=True)

        self.assertEqual(harness.data, before)
        self.assertEqual(harness.save_count, 0)

    async def test_state_model_wait_does_not_hold_global_data_lock(self) -> None:
        harness = _StateHarness()
        harness.block_generation = True
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            task = asyncio.create_task(harness._ensure_daily_state(force=True))
            await harness.generation_started.wait()

            await asyncio.wait_for(harness._data_lock.acquire(), timeout=0.2)
            harness._data_lock.release()
            harness.generation_release.set()
            await task

        self.assertEqual(harness.generate_count, 1)
        self.assertEqual(harness.data["state_generated_day"], "2026-07-23")

    async def test_concurrent_force_state_refreshes_share_one_generation(self) -> None:
        harness = _StateHarness()
        harness.block_generation = True
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            first = asyncio.create_task(harness._ensure_daily_state(force=True))
            await harness.generation_started.wait()
            second = asyncio.create_task(harness._ensure_daily_state(force=True))
            await asyncio.sleep(0)
            harness.generation_release.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(harness.generate_count, 1)
        self.assertEqual(first_result, second_result)
        self.assertEqual(
            [item.get("id") for item in harness.data["state_conditions"]],
            ["new"],
        )

    async def test_force_diary_refresh_replaces_all_entries_for_that_day(self) -> None:
        harness = _DiaryHarness()
        harness.data["bot_diaries"] = [
            {"date": "2026-07-22", "body": "前一天"},
            {"date": "2026-07-23", "body": "旧版本一"},
            {"date": "2026-07-23", "body": "旧版本二"},
        ]
        harness.data["diary_generated_day"] = "2026-07-23"

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            result = await harness._ensure_daily_diary(force=True)

        today_entries = [
            item for item in harness.data["bot_diaries"] if item.get("date") == "2026-07-23"
        ]
        self.assertEqual(len(today_entries), 1)
        self.assertEqual(today_entries[0]["body"], "刷新后的正文")
        self.assertEqual(result, today_entries[0])
        self.assertEqual(len(harness.data["bot_diaries"]), 2)

    async def test_concurrent_normal_diary_generation_runs_model_once(self) -> None:
        harness = _DiaryHarness()
        harness.block_generation = True
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            first = asyncio.create_task(harness._ensure_daily_diary(force=False))
            await harness.generation_started.wait()
            second = asyncio.create_task(harness._ensure_daily_diary(force=False))
            await asyncio.sleep(0)
            harness.generation_release.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(harness.generate_count, 1)
        self.assertIsInstance(first_result, dict)
        self.assertIsNone(second_result)
        self.assertEqual(len(harness.data["bot_diaries"]), 1)

    async def test_concurrent_force_diary_refreshes_share_one_generation(self) -> None:
        harness = _DiaryHarness()
        harness.block_generation = True
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            first = asyncio.create_task(harness._ensure_daily_diary(force=True))
            await harness.generation_started.wait()
            second = asyncio.create_task(harness._ensure_daily_diary(force=True))
            await asyncio.sleep(0)
            harness.generation_release.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(harness.generate_count, 1)
        self.assertEqual(first_result, second_result)
        self.assertEqual(len(harness.data["bot_diaries"]), 1)

    async def test_diary_marker_uses_generated_payload_day_after_rollover(self) -> None:
        harness = _DiaryHarness()
        harness.data["bot_diaries"] = [{"date": "2026-07-24", "body": "旧的次日版本"}]
        harness.data["diary_generated_day"] = "2026-07-23"
        harness.generated_diary["date"] = "2026-07-24"

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            await harness._ensure_daily_diary(force=True)

        self.assertEqual(harness.data["diary_generated_day"], "2026-07-24")
        next_day_entries = [
            item for item in harness.data["bot_diaries"] if item.get("date") == "2026-07-24"
        ]
        self.assertEqual(len(next_day_entries), 1)
        self.assertEqual(next_day_entries[0]["body"], "刷新后的正文")


if __name__ == "__main__":
    unittest.main()
