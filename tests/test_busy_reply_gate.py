# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot_plugin_private_companion.busy_reply_gate import BusyReplyGateMixin
from astrbot_plugin_private_companion.helpers import _flat_get
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


ROOT = Path(__file__).resolve().parents[1]


class BusyReplyHarness(BusyReplyGateMixin):
    enable_busy_reply_gate = True
    busy_reply_min_delay_seconds = 60
    busy_reply_max_delay_seconds = 300
    busy_reply_proactive_resume_buffer_minutes = 10

    def __init__(self, activity: str = "上午上课") -> None:
        self.now = datetime(2026, 7, 17, 10, 0, 0)
        self.item = {
            "key": "10:00",
            "time": "10:00",
            "activity": activity,
            "mood": "专注",
        }
        self.data = {
            "daily_plan": {"date": "2026-07-17", "items": [self.item]},
            "detail_enhanced_segments": {},
        }

    def _get_current_plan_item(self, _plan):
        return self.item

    @staticmethod
    def _normalized_plan_item_starts(_items):
        return [10 * 60]

    @staticmethod
    def _effective_plan_now_minutes(_plan_date):
        return 10 * 60

    @staticmethod
    def _plan_item_end_minutes(start_minutes, _item, *, next_start=None):
        return next_start if next_start is not None else start_minutes + 60

    def _environment_now(self):
        return self.now

    @staticmethod
    def _format_plan_item_for_prompt(item):
        return f"{item.get('time')} {item.get('activity')}"

    @staticmethod
    def _is_sleepy_plan_item(_item):
        return False


class BusyBridgeHarness(BusyReplyHarness, ProactiveMessageMixin):
    pass


class _PrivateBusyEvent:
    def __init__(self, text: str) -> None:
        self.message_str = text
        self.unified_msg_origin = "default:FriendMessage:10001"
        self.stopped = False

    @staticmethod
    def is_private_chat() -> bool:
        return True

    @staticmethod
    def get_sender_id() -> str:
        return "10001"

    def stop_event(self) -> None:
        self.stopped = True


class BusyReplyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_by_default_does_not_delay(self) -> None:
        harness = BusyReplyHarness()
        harness.enable_busy_reply_gate = False
        event = SimpleNamespace(message_str="在吗")

        with patch(
            "astrbot_plugin_private_companion.busy_reply_gate.asyncio.sleep",
            new=AsyncMock(),
        ) as sleeper:
            delay, reason = await harness._apply_busy_reply_gate_delay(event, is_private_chat=True)

        self.assertEqual((delay, reason), (0.0, "disabled"))
        sleeper.assert_not_awaited()

    def test_busy_and_leisure_schedule_classification(self) -> None:
        harness = BusyReplyHarness()

        for activity in ("上午上课", "准备考试", "开项目会议", "专注写代码"):
            with self.subTest(activity=activity):
                self.assertTrue(harness._busy_reply_item_is_busy({"activity": activity})[0])
        for activity in ("午休", "吃晚饭", "打游戏放松", "散步"):
            with self.subTest(activity=activity):
                self.assertFalse(harness._busy_reply_item_is_busy({"activity": activity})[0])

    def test_external_shared_activity_blocks_proactive_even_when_busy_gate_is_disabled(self) -> None:
        harness = BusyReplyHarness()
        harness.enable_busy_reply_gate = False
        harness._external_realtime_activities = {
            "together:room": {
                "user_id": "10001",
                "kind": "shared_watch",
                "expires_at": 2000.0,
            }
        }

        blocked_until = harness._busy_reply_proactive_block_until(
            {"user_id": "10001"},
            now=1000.0,
        )

        self.assertEqual(2000.0, blocked_until)
        context = harness._busy_reply_proactive_block_context(
            {"user_id": "10001"},
            now=1000.0,
        )
        self.assertEqual("external_realtime", context["kind"])

    def test_disabled_busy_gate_does_not_report_schedule_block(self) -> None:
        harness = BusyReplyHarness()
        harness.enable_busy_reply_gate = False

        context = harness._busy_reply_proactive_block_context({}, now=harness.now.timestamp())

        self.assertEqual(0.0, context["until"])
        self.assertEqual("disabled", context["kind"])

    def test_expired_external_shared_activity_is_cleaned(self) -> None:
        harness = BusyReplyHarness()
        harness._external_realtime_activities = {
            "together:room": {"user_id": "10001", "expires_at": 900.0}
        }

        blocked_until = harness._external_realtime_activity_block_until(
            {"user_id": "10001"},
            now=1000.0,
        )

        self.assertEqual(0.0, blocked_until)
        self.assertEqual({}, harness._external_realtime_activities)

    def test_detail_presence_busy_takes_priority(self) -> None:
        harness = BusyReplyHarness("整理东西")
        harness.data["detail_enhanced_segments"]["10:00"] = {
            "presence_status": {"mode": "busy", "custom_text": "专注中"}
        }

        busy, reason = harness._busy_reply_item_is_busy(harness.item)

        self.assertTrue(busy)
        self.assertEqual(reason, "presence:busy")

    async def test_private_delay_is_applied_only_once(self) -> None:
        harness = BusyReplyHarness()
        event = SimpleNamespace(
            message_str="讲完了吗",
            unified_msg_origin="default:FriendMessage:10001",
        )

        with (
            patch(
                "astrbot_plugin_private_companion.busy_reply_gate.random.uniform",
                return_value=180.0,
            ),
            patch(
                "astrbot_plugin_private_companion.busy_reply_gate.asyncio.sleep",
                new=AsyncMock(),
            ) as sleeper,
        ):
            first = await harness._apply_busy_reply_gate_delay(event, is_private_chat=True)
            second = await harness._apply_busy_reply_gate_delay(event, is_private_chat=True)

        self.assertEqual(first[0], 180.0)
        self.assertEqual(second, (0.0, "already_applied"))
        sleeper.assert_awaited_once_with(180.0)

    async def test_new_private_message_supersedes_older_busy_wait(self) -> None:
        harness = BusyReplyHarness()
        first = _PrivateBusyEvent("换了个壁纸")
        second = _PrivateBusyEvent("好看不？")
        harness._busy_reply_note_inbound_event(first)
        sleep_started = asyncio.Event()
        release_sleep = asyncio.Event()

        async def controlled_sleep(_delay: float) -> None:
            sleep_started.set()
            await release_sleep.wait()

        with (
            patch(
                "astrbot_plugin_private_companion.busy_reply_gate.random.uniform",
                return_value=180.0,
            ),
            patch(
                "astrbot_plugin_private_companion.busy_reply_gate.asyncio.sleep",
                side_effect=controlled_sleep,
            ),
        ):
            first_task = asyncio.create_task(
                harness._apply_busy_reply_gate_delay(first, is_private_chat=True)
            )
            await sleep_started.wait()
            harness._busy_reply_note_inbound_event(second)
            release_sleep.set()
            result = await first_task

        self.assertEqual(result, (180.0, "superseded_by_newer_private_message"))
        self.assertTrue(first.stopped)
        self.assertFalse(second.stopped)

    async def test_latest_private_message_keeps_normal_busy_delay(self) -> None:
        harness = BusyReplyHarness()
        event = _PrivateBusyEvent("好看不？")
        harness._busy_reply_note_inbound_event(event)

        with (
            patch(
                "astrbot_plugin_private_companion.busy_reply_gate.random.uniform",
                return_value=180.0,
            ),
            patch(
                "astrbot_plugin_private_companion.busy_reply_gate.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            result = await harness._apply_busy_reply_gate_delay(event, is_private_chat=True)

        self.assertEqual(result[0], 180.0)
        self.assertNotEqual(result[1], "superseded_by_newer_private_message")
        self.assertFalse(event.stopped)

    async def test_group_delay_is_capped_at_twelve_seconds(self) -> None:
        harness = BusyReplyHarness()
        event = SimpleNamespace(message_str="下课了吗", unified_msg_origin="group:1")

        with patch(
            "astrbot_plugin_private_companion.busy_reply_gate.asyncio.sleep",
            new=AsyncMock(),
        ) as sleeper:
            delay, _reason = await harness._apply_busy_reply_gate_delay(event, is_private_chat=False)

        self.assertEqual(delay, 12.0)
        sleeper.assert_awaited_once_with(12.0)

    async def test_urgent_and_management_messages_bypass_delay(self) -> None:
        harness = BusyReplyHarness()

        for text in ("救命，我呼吸困难", "/陪伴 状态", "陪伴设置繁忙闸门"):
            with self.subTest(text=text):
                event = SimpleNamespace(message_str=text)
                with patch(
                    "astrbot_plugin_private_companion.busy_reply_gate.asyncio.sleep",
                    new=AsyncMock(),
                ) as sleeper:
                    delay, reason = await harness._apply_busy_reply_gate_delay(
                        event,
                        is_private_chat=True,
                    )
                self.assertEqual(delay, 0.0)
                self.assertTrue(reason.startswith("bypass:"))
                sleeper.assert_not_awaited()

    def test_proactive_is_deferred_to_schedule_end_plus_buffer(self) -> None:
        harness = BusyReplyHarness()
        now = harness.now.timestamp()

        until = harness._busy_reply_proactive_block_until(
            {},
            now=now,
            reason="check_in",
            source="daily_story",
        )

        self.assertEqual(until, now + 70 * 60)
        user = {
            "next_proactive_at": now + 60,
            "planned_proactive_window_start_at": now + 60,
            "planned_proactive_best_until_at": now + 15 * 60,
            "planned_proactive_expire_at": now + 30 * 60,
        }
        self.assertTrue(harness._defer_proactive_for_busy(user, now=now, until=until))
        self.assertEqual(user["next_proactive_at"], until)
        self.assertEqual(user["planned_proactive_window_start_at"], until)
        self.assertFalse(harness._defer_proactive_for_busy(user, now=now + 3, until=until))

    def test_time_sensitive_proactive_sources_are_exempt(self) -> None:
        harness = BusyReplyHarness()
        now = harness.now.timestamp()
        cases = (
            ("timer", "timer"),
            ("memo_note_reminder", "memo_note"),
            ("environment_change", "environment_change"),
            ("check_in", "troubleshooting"),
            ("check_in", "simulation"),
        )

        for reason, source in cases:
            with self.subTest(reason=reason, source=source):
                self.assertEqual(
                    harness._busy_reply_proactive_block_until(
                        {},
                        now=now,
                        reason=reason,
                        source=source,
                    ),
                    0.0,
                )

    def test_external_realtime_activity_still_pauses_time_sensitive_event(self) -> None:
        harness = BusyReplyHarness()
        now = harness.now.timestamp()
        harness._external_realtime_activities = {
            "watch": {"user_id": "10001", "expires_at": now + 15 * 60}
        }

        context = harness._busy_reply_proactive_block_context(
            {"user_id": "10001"},
            now=now,
            reason="environment_change",
            source="environment_change",
        )

        self.assertEqual(context["kind"], "external_realtime")
        self.assertEqual(context["until"], now + 15 * 60)

    def test_busy_deferral_does_not_extend_short_lived_event_expiry(self) -> None:
        harness = BusyReplyHarness()
        harness._planned_proactive_timeliness_level = lambda _user: "urgent"
        now = harness.now.timestamp()
        expire_at = now + 20 * 60
        until = now + 10 * 60
        user = {
            "next_proactive_at": now + 60,
            "planned_proactive_source": "weather_alert",
            "planned_proactive_window_start_at": now + 60,
            "planned_proactive_best_until_at": now + 15 * 60,
            "planned_proactive_expire_at": expire_at,
        }

        self.assertTrue(harness._defer_proactive_for_busy(user, now=now, until=until))
        self.assertEqual(user["next_proactive_at"], until)
        self.assertEqual(user["planned_proactive_expire_at"], expire_at)

    def test_proactive_chat_preflight_reports_busy_schedule(self) -> None:
        harness = BusyBridgeHarness()

        reason = harness._proactive_chat_bridge_preflight_block_reason(
            {},
            now=harness.now.timestamp(),
        )

        self.assertEqual(reason, "bot_busy_schedule")


class BusyReplyGateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

    def test_busy_gate_is_grouped_after_rest_gate(self) -> None:
        rest_index = self.script.index('title: "休息回复闸门"')
        busy_index = self.script.index('title: "繁忙回复闸门"')
        self.assertGreater(busy_index, rest_index)

    def test_busy_children_follow_gate_visibility(self) -> None:
        self.assertIn(
            'if (busyChildren.has(settingKey)) return boolSetting("enable_busy_reply_gate")',
            self.script,
        )
        self.assertIn('"enable_busy_reply_gate",', self.script)


class BusyReplyGateConfigSaveTests(unittest.TestCase):
    def test_busy_gate_settings_survive_real_save_reload_and_second_save(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        rounds = (
            {
                "enable_busy_reply_gate": True,
                "busy_reply_min_delay_seconds": 90,
                "busy_reply_max_delay_seconds": 480,
                "busy_reply_proactive_resume_buffer_minutes": 25,
            },
            {
                "enable_busy_reply_gate": False,
                "busy_reply_min_delay_seconds": 60,
                "busy_reply_max_delay_seconds": 300,
                "busy_reply_proactive_resume_buffer_minutes": 10,
            },
        )

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)

            for expected in rounds:
                for key, value in expected.items():
                    normalized = api._normalize_setting_value(key, value)
                    api._apply_config_value(key, normalized, expected)
                    self.assertEqual(getattr(plugin, key), normalized, key)

                self.assertTrue(asyncio.run(api._save_config_if_possible()))
                reloaded = AstrBotConfig(str(config_path), schema=schema)
                plugin.config = reloaded

                for key, value in expected.items():
                    normalized = api._normalize_setting_value(key, value)
                    group_key = api._schema_group_for_key(key)
                    self.assertTrue(group_key, key)
                    self.assertEqual(reloaded[key], normalized, f"{key} flat read")
                    self.assertEqual(reloaded[group_key][key], normalized, f"{key} grouped read")
                    self.assertEqual(_flat_get(reloaded, key), normalized, f"{key} reload")

    def test_busy_delay_values_are_clamped_consistently(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None

        self.assertEqual(api._normalize_setting_value("busy_reply_min_delay_seconds", -1), 0)
        self.assertEqual(api._normalize_setting_value("busy_reply_max_delay_seconds", 1200), 900)
        self.assertEqual(api._normalize_setting_value("busy_reply_proactive_resume_buffer_minutes", 999), 120)

    def test_runtime_settings_do_not_expose_empty_busy_defaults_before_restart(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace(config={}))

        settings = api._runtime_settings()

        self.assertFalse(settings["enable_busy_reply_gate"])
        self.assertEqual(settings["busy_reply_min_delay_seconds"], 60)
        self.assertEqual(settings["busy_reply_max_delay_seconds"], 300)
        self.assertEqual(settings["busy_reply_proactive_resume_buffer_minutes"], 10)


if __name__ == "__main__":
    unittest.main()
