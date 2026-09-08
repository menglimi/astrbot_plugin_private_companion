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
        self.enable_cycle_state = True
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

    def _get_default_persona_prompt(self, _umo: str = "") -> str:
        return "人类女性角色"

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

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_count += 1


class _CyclePersistenceHarness(_StateHarness):
    """Small harness for asserting the daily-state save contract."""

    def __init__(self) -> None:
        super().__init__()
        self.cleanup_results: list[set[str]] = []
        self.rebuild_body_cycle = False
        self.compose_count = 0
        self.save_requests: list[dict[str, set[str]]] = []

    def _cleanup_expired_conditions(self) -> set[str]:
        result = self.cleanup_results.pop(0) if self.cleanup_results else set()
        if "body_cycle_state" in result:
            self.data.pop("body_cycle_state", None)
        return set(result)

    async def _generate_state_conditions(
        self,
        _weather=None,
        *,
        deferred_state_updates=None,
    ):
        if self.rebuild_body_cycle and isinstance(deferred_state_updates, dict):
            deferred_state_updates["body_cycle_conditions"] = [
                {"id": "rebuilt", "kind": "body_cycle"}
            ]
        return []

    def _compose_state_from_conditions(self, _weather=None):
        self.compose_count += 1
        return {
            "date": "2026-07-23",
            "conditions": deepcopy(self.data.get("state_conditions", [])),
            "refresh": self.compose_count,
        }

    def _save_data_sync(self, **kwargs) -> None:
        self.save_count += 1
        self.save_requests.append(
            {
                "sections": set(kwargs.get("sections") or ()),
                "deleted_sections": set(kwargs.get("deleted_sections") or ()),
            }
        )


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

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_count += 1


class DailyStateGenerationSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_cleanup_emits_tombstone_once_without_stale_changed_section(self) -> None:
        harness = _CyclePersistenceHarness()
        harness.data.update(
            {
                "daily_state": {"date": "2026-07-23"},
                "state_generated_day": "2026-07-23",
                "body_cycle_state": {"cycle_anchor_ts": 1},
            }
        )
        harness.cleanup_results = [{"body_cycle_state"}, set()]

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            await harness._ensure_daily_state_once()
            await harness._ensure_daily_state_once()

        self.assertEqual(2, len(harness.save_requests))
        first, second = harness.save_requests
        self.assertNotIn("body_cycle_state", first["sections"])
        self.assertEqual({"body_cycle_state"}, first["deleted_sections"])
        self.assertNotIn("body_cycle_state", second["sections"])
        self.assertEqual(set(), second["deleted_sections"])

    async def test_passive_fast_cycle_cleanup_does_not_reintroduce_missing_section(self) -> None:
        harness = _CyclePersistenceHarness()
        harness.data.update(
            {
                "daily_state": {"date": "2026-07-23", "refresh": 0},
                "daily_weather": {"date": "2026-07-23", "prompt": "晴"},
                "body_cycle_state": {"cycle_anchor_ts": 1},
            }
        )
        harness.cleanup_results = [{"body_cycle_state"}, set()]

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            await harness._ensure_daily_state_once(passive_fast=True)
            await harness._ensure_daily_state_once(passive_fast=True)

        self.assertEqual(2, len(harness.save_requests))
        first, second = harness.save_requests
        self.assertNotIn("body_cycle_state", first["sections"])
        self.assertEqual({"body_cycle_state"}, first["deleted_sections"])
        self.assertNotIn("body_cycle_state", second["sections"])
        self.assertEqual(set(), second["deleted_sections"])

    async def test_force_cycle_refresh_keeps_missing_section_out_of_final_changed_set(self) -> None:
        harness = _CyclePersistenceHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": 1}
        harness.cleanup_results = [{"body_cycle_state"}, set()]

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            await harness._ensure_daily_state_once(force=True)
            await harness._ensure_daily_state_once(force=True)

        self.assertEqual(2, len(harness.save_requests))
        first, second = harness.save_requests
        self.assertNotIn("body_cycle_state", first["sections"])
        self.assertEqual({"body_cycle_state"}, first["deleted_sections"])
        self.assertNotIn("body_cycle_state", second["sections"])
        self.assertEqual(set(), second["deleted_sections"])

    async def test_rebuilt_cycle_state_supersedes_cleanup_tombstone(self) -> None:
        harness = _CyclePersistenceHarness()
        harness.data["body_cycle_state"] = {"cycle_anchor_ts": 1}
        harness.cleanup_results = [{"body_cycle_state"}]
        harness.rebuild_body_cycle = True

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            await harness._ensure_daily_state_once(force=True)

        self.assertEqual(1, len(harness.save_requests))
        request = harness.save_requests[0]
        self.assertIn("body_cycle_state", request["sections"])
        self.assertEqual(set(), request["deleted_sections"])
        self.assertEqual("rebuilt", harness.data["body_cycle_state"]["id"])

    async def test_passive_fast_discards_cached_period_when_humanized_states_disabled(self) -> None:
        harness = _StateHarness()
        harness.enable_humanized_states = False
        harness.data["daily_state"] = {
            "date": "2026-07-23",
            "energy": 42,
            "mood_bias": "疲惫",
            "body_cycle": "处于生理期,身体舒适度与能量偏低",
            "conditions": [
                {
                    "kind": "body_cycle",
                    "phase": "period",
                    "label": "处于生理期",
                    "energy_delta": -10,
                }
            ],
        }

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            state = await harness._ensure_daily_state_once(passive_fast=True)

        self.assertEqual(state.get("body_cycle"), "不处于生理期")
        self.assertEqual(state.get("conditions", []), [])
        self.assertEqual(
            harness._format_active_period_boundary_prompt_section(state).content,
            "",
        )

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

    async def test_next_generation_preserves_legacy_dictionary_diaries(self) -> None:
        harness = _DiaryHarness()
        harness.data["bot_diaries"] = {
            "2026-07-21": {
                "content": "旧格式正文一",
                "metadata": {"mood": "平静"},
            },
            "2026/7/22": "旧格式正文二",
        }

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            result = await harness._ensure_daily_diary(force=False)

        self.assertIsInstance(result, dict)
        self.assertIsInstance(harness.data["bot_diaries"], list)
        self.assertEqual(3, len(harness.data["bot_diaries"]))
        self.assertEqual(
            {
                "date": "2026-07-21",
                "content": "旧格式正文一",
                "metadata": {"mood": "平静"},
            },
            harness.data["bot_diaries"][0],
        )
        self.assertEqual(
            {"date": "2026/7/22", "body": "旧格式正文二"},
            harness.data["bot_diaries"][1],
        )
        self.assertEqual("刷新后的正文", harness.data["bot_diaries"][2]["body"])

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

    async def test_manually_deleted_today_diary_is_not_automatically_recreated(self) -> None:
        harness = _DiaryHarness()
        harness.data["daily_diary_deleted_days"] = ["2026-07-23"]

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            result = await harness._ensure_daily_diary(force=False)

        self.assertIsNone(result)
        self.assertEqual(0, harness.generate_count)
        self.assertEqual([], harness.data["bot_diaries"])

    async def test_force_refresh_restores_deleted_today_diary(self) -> None:
        harness = _DiaryHarness()
        harness.data["daily_diary_deleted_days"] = ["2026-07-23"]

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            result = await harness._ensure_daily_diary(force=True)

        self.assertIsInstance(result, dict)
        self.assertEqual(1, harness.generate_count)
        self.assertEqual([], harness.data["daily_diary_deleted_days"])
        self.assertEqual(["2026-07-23"], [item["date"] for item in harness.data["bot_diaries"]])

    async def test_deleted_day_does_not_block_next_day_generation(self) -> None:
        harness = _DiaryHarness()
        harness.data["daily_diary_deleted_days"] = ["2026-07-23"]

        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            await harness._ensure_daily_diary(force=False)

        harness.generated_diary["date"] = "2026-07-24"
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-24"):
            result = await harness._ensure_daily_diary(force=False)

        self.assertIsInstance(result, dict)
        self.assertEqual(1, harness.generate_count)
        self.assertEqual(["2026-07-23"], harness.data["daily_diary_deleted_days"])
        self.assertEqual(["2026-07-24"], [item["date"] for item in harness.data["bot_diaries"]])

    async def test_delete_during_generation_discards_inflight_result(self) -> None:
        harness = _DiaryHarness()
        harness.block_generation = True
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            task = asyncio.create_task(harness._ensure_daily_diary(force=False))
            await harness.generation_started.wait()
            async with harness._data_lock:
                harness.data["daily_diary_deleted_days"] = ["2026-07-23"]
            harness.generation_release.set()
            result = await task

        self.assertIsNone(result)
        self.assertEqual([], harness.data["bot_diaries"])
        self.assertEqual("", harness.data["diary_generated_day"])

    async def test_delete_during_force_refresh_wins_over_inflight_result(self) -> None:
        harness = _DiaryHarness()
        harness.data["bot_diaries"] = [
            {"date": "2026-07-23", "body": "刷新前的正文"},
        ]
        harness.data["diary_generated_day"] = "2026-07-23"
        harness.block_generation = True
        with patch("astrbot_plugin_private_companion.daily_state._today_key", return_value="2026-07-23"):
            task = asyncio.create_task(harness._ensure_daily_diary(force=True))
            await harness.generation_started.wait()
            async with harness._data_lock:
                harness.data["bot_diaries"] = []
                harness.data["daily_diary_deleted_days"] = ["2026-07-23"]
                harness.data["daily_diary_delete_revision"] = 1
            harness.generation_release.set()
            result = await task

        self.assertIsNone(result)
        self.assertEqual([], harness.data["bot_diaries"])
        self.assertEqual(["2026-07-23"], harness.data["daily_diary_deleted_days"])
        self.assertEqual(1, harness.data["daily_diary_delete_revision"])


if __name__ == "__main__":
    unittest.main()
