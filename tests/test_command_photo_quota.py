# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from pathlib import Path

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.page_api_settings import PageSettingNormalizerMixin
from astrbot_plugin_private_companion.photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
)


ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime(2026, 7, 22, 12, 0, 0)


class _FakeEvent:
    def __init__(self) -> None:
        self.stopped = False

    def get_sender_id(self) -> str:
        return "10001"

    def stop_event(self) -> None:
        self.stopped = True


class _FakeGroupEvent(_FakeEvent):
    unified_msg_origin = "default:GroupMessage:group-1"

    @staticmethod
    def is_private_chat() -> bool:
        return False


class _QuotaHarness(CommandHandlersMixin):
    def __init__(self, limit: int, *, used: int = 0, day: str = "2026-07-22") -> None:
        self.command_photo_generation_max_daily = limit
        self.user = {
            "enabled": True,
            "command_photo_generated_today": used,
            "command_photo_generated_day": day,
        }

    def _environment_now(self) -> datetime:
        return TODAY

    @staticmethod
    def _cycle_status_text() -> str:
        return "六阶段周期：未开启"


class _ToolQuotaHarness(LlmToolActionsMixin, CommandHandlersMixin):
    def __init__(self, limit: int, *, used: int) -> None:
        self.enable_photo_text_action = True
        self.natural_language_photo_generation_mode = "tool_first"
        self.command_photo_generation_max_daily = limit
        self._data_lock = asyncio.Lock()
        self.generated = False
        self.replies: list[str] = []
        self.user = {
            "enabled": True,
            "command_photo_generated_today": used,
            "command_photo_generated_day": "2026-07-22",
        }

    def _environment_now(self) -> datetime:
        return TODAY

    def _photo_text_available(self) -> bool:
        return True

    def _get_user(self, _user_id: str) -> dict:
        return self.user

    def _is_target_private_user(self, _user_id: str, _user: dict) -> bool:
        return True

    async def _reply(self, _event: _FakeEvent, message: str) -> None:
        self.replies.append(message)

    async def _generate_photo_image(self, **_kwargs):
        self.generated = True
        return "test", "generated.png", "ok"


class CommandPhotoQuotaTests(unittest.IsolatedAsyncioTestCase):
    def test_minus_one_means_unlimited_even_after_previous_generations(self) -> None:
        harness = _QuotaHarness(-1, used=99)

        self.assertIsNone(harness._command_photo_quota_left(harness.user))

    def test_zero_means_user_photo_generation_is_disabled(self) -> None:
        harness = _QuotaHarness(0, used=0)

        self.assertEqual(harness._command_photo_quota_left(harness.user), 0)

    def test_positive_limit_uses_current_day_counter(self) -> None:
        harness = _QuotaHarness(3, used=3)

        self.assertEqual(harness._command_photo_quota_left(harness.user), 0)

    def test_previous_day_counter_does_not_consume_today_quota(self) -> None:
        harness = _QuotaHarness(3, used=20, day="2026-07-21")

        self.assertEqual(harness._command_photo_quota_left(harness.user), 3)

    def test_block_message_distinguishes_disabled_from_exhausted(self) -> None:
        disabled = _QuotaHarness(0)
        exhausted = _QuotaHarness(2, used=2)

        self.assertIn("管理员已关闭", disabled._command_photo_quota_block_message())
        self.assertIn("为 0", disabled._command_photo_quota_block_message())
        self.assertIn("额度用完", exhausted._command_photo_quota_block_message())
        self.assertIn("设为 -1", exhausted._command_photo_quota_block_message())

    def test_manual_config_and_snapshot_describe_all_three_states(self) -> None:
        harness = _QuotaHarness(-1)

        specs = harness._companion_manual_config_specs()
        self.assertEqual(specs["command_photo_generation_max_daily"]["min"], -1)
        for key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values():
            with self.subTest(key=key):
                self.assertEqual(-1, specs[key]["min"])
                self.assertEqual(100, specs[key]["max"])
                self.assertEqual(
                    "-1（不限量）",
                    harness._companion_manual_format_config_item_value(key, -1),
                )
                self.assertEqual(
                    "0（不允许）",
                    harness._companion_manual_format_config_item_value(key, 0),
                )
                self.assertEqual(
                    "6 次",
                    harness._companion_manual_format_config_item_value(key, 6),
                )
                self.assertIn(
                    "用户请求生图",
                    harness._companion_manual_config_location(key),
                )
        self.assertEqual(
            harness._companion_manual_format_config_item_value(
                "command_photo_generation_max_daily",
                -1,
            ),
            "-1（不限量）",
        )
        self.assertIn("不限量（-1）", "\n".join(harness._companion_manual_setting_snapshot()))

        harness.command_photo_generation_max_daily = 0
        self.assertEqual(
            harness._companion_manual_format_config_item_value(
                "command_photo_generation_max_daily",
                0,
            ),
            "0（不允许）",
        )
        self.assertIn("不允许（0）", "\n".join(harness._companion_manual_setting_snapshot()))

        harness.command_photo_generation_max_daily = 3
        self.assertEqual(
            harness._companion_manual_format_config_item_value(
                "command_photo_generation_max_daily",
                3,
            ),
            "3 次",
        )

        aliases = harness._companion_manual_config_aliases()
        self.assertEqual(
            "photo_generation_private_owner_max_daily",
            aliases["主要用户私聊生图上限"],
        )
        self.assertEqual(
            "photo_generation_private_friend_max_daily",
            aliases["其他陪伴用户私聊生图上限"],
        )
        self.assertEqual(
            "photo_generation_group_max_daily",
            aliases["群聊生图上限"],
        )
        self.assertEqual(
            "photo_generation_proactive_max_daily",
            aliases["Bot 主动生图上限"],
        )

    async def test_tool_stops_before_backend_when_user_quota_is_exhausted(self) -> None:
        harness = _ToolQuotaHarness(2, used=2)

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张夏日海边照片",
            )
        )

        self.assertEqual(payload["status"], "quota_exhausted")
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertIn("额度用完", payload["message"])
        self.assertFalse(harness.generated)

    async def test_tool_stops_before_backend_when_user_photo_generation_is_disabled(self) -> None:
        harness = _ToolQuotaHarness(0, used=0)

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张夏日海边照片",
            )
        )

        self.assertEqual(payload["status"], "quota_exhausted")
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertIn("管理员已关闭", payload["message"])
        self.assertFalse(harness.generated)

    async def test_tool_with_unlimited_quota_reaches_backend(self) -> None:
        harness = _ToolQuotaHarness(-1, used=999)

        await harness._pc_generate_photo_impl(
            _FakeEvent(),
            prompt="画一张夏日海边照片",
            send=False,
        )

        self.assertTrue(harness.generated)

    async def test_command_stops_before_backend_when_user_photo_generation_is_disabled(self) -> None:
        harness = _ToolQuotaHarness(0, used=0)
        event = _FakeEvent()

        handled = await harness._handle_companion_photo_command(
            event,
            "10001",
            "生图",
            "画一张夏日海边照片",
        )

        self.assertTrue(handled)
        self.assertTrue(event.stopped)
        self.assertEqual(len(harness.replies), 1)
        self.assertIn("管理员已关闭", harness.replies[0])
        self.assertFalse(harness.generated)

    async def test_tool_uses_scope_and_quota_instead_of_legacy_target_permission(self) -> None:
        harness = _ToolQuotaHarness(0, used=0)
        harness._is_target_private_user = lambda _user_id, _user: False

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张夏日海边照片",
            )
        )

        self.assertEqual(payload["status"], "quota_exhausted")
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertFalse(harness.generated)

    async def test_group_sender_is_not_created_as_private_user(self) -> None:
        harness = _ToolQuotaHarness(0, used=0)
        harness.data = {"users": {}}
        harness._is_target_private_user = lambda _user_id, _user: False

        def fail_if_group_sender_is_created(_user_id: str) -> dict:
            raise AssertionError("group sender must not be created in private users")

        harness._get_user = fail_if_group_sender_is_created

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeGroupEvent(),
                prompt="画一张夏日海边照片",
            )
        )

        self.assertEqual(payload["status"], "unauthorized")
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertEqual({}, harness.data["users"])

    def test_config_is_visible_persistable_and_uses_three_state_semantics(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        setting = schema["photo_action_config"]["items"]["command_photo_generation_max_daily"]
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")
        page_api_settings = (ROOT / "page_api_settings.py").read_text(encoding="utf-8")

        self.assertEqual(setting["default"], -1)
        self.assertEqual(setting["slider"]["min"], -1)
        self.assertIn("-1 表示不限量", setting["hint"])
        self.assertIn("0 表示不允许", setting["hint"])
        section_start = script.index('title: "用户请求生图"')
        section_end = script.index("\n    },", section_start)
        user_photo_section = script[section_start:section_end]
        self.assertNotIn('"photo_generation_allowed_scopes"', user_photo_section)
        for key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values():
            self.assertIn(f'"{key}"', user_photo_section)
        self.assertIn('"command_photo_generation_max_daily"', user_photo_section)
        self.assertGreaterEqual(
            (page_api + page_api_settings).count('"command_photo_generation_max_daily"'),
            3,
        )

        api = PrivateCompanionPageApi(None)
        self.assertIsInstance(api, PageSettingNormalizerMixin)
        self.assertEqual(api._normalize_setting_value("command_photo_generation_max_daily", -5), -1)
        self.assertEqual(api._normalize_setting_value("command_photo_generation_max_daily", 0), 0)
        self.assertEqual(api._normalize_setting_value("command_photo_generation_max_daily", 200), 100)
        self.assertEqual(api._normalize_setting_value("command_photo_generation_max_daily", None), -1)
        for key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values():
            with self.subTest(key=key):
                scope_setting = schema["photo_action_config"]["items"][key]
                self.assertEqual(-1, scope_setting["default"])
                self.assertEqual(-1, scope_setting["slider"]["min"])
                self.assertEqual(100, scope_setting["slider"]["max"])
                self.assertEqual(-1, api._normalize_setting_value(key, -5))
                self.assertEqual(0, api._normalize_setting_value(key, 0))
                self.assertEqual(100, api._normalize_setting_value(key, 200))
                self.assertEqual(-1, api._normalize_setting_value(key, None))


if __name__ == "__main__":
    unittest.main()
