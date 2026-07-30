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


ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime(2026, 7, 22, 12, 0, 0)


class _FakeEvent:
    def get_sender_id(self) -> str:
        return "10001"


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


class _ToolQuotaHarness(LlmToolActionsMixin, CommandHandlersMixin):
    def __init__(self, limit: int, *, used: int) -> None:
        self.enable_photo_text_action = True
        self.natural_language_photo_generation_mode = "tool_first"
        self.command_photo_generation_max_daily = limit
        self._data_lock = asyncio.Lock()
        self.generated = False
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

    async def _generate_photo_image(self, **_kwargs):
        self.generated = True
        return "test", "generated.png", "ok"


class CommandPhotoQuotaTests(unittest.IsolatedAsyncioTestCase):
    def test_zero_means_unlimited_even_after_previous_generations(self) -> None:
        harness = _QuotaHarness(0, used=99)

        self.assertIsNone(harness._command_photo_quota_left(harness.user))

    def test_positive_limit_uses_current_day_counter(self) -> None:
        harness = _QuotaHarness(3, used=3)

        self.assertEqual(harness._command_photo_quota_left(harness.user), 0)

    def test_previous_day_counter_does_not_consume_today_quota(self) -> None:
        harness = _QuotaHarness(3, used=20, day="2026-07-21")

        self.assertEqual(harness._command_photo_quota_left(harness.user), 3)

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
        self.assertFalse(harness.generated)

    async def test_tool_rejects_non_target_user_before_backend(self) -> None:
        harness = _ToolQuotaHarness(0, used=0)
        harness._is_target_private_user = lambda _user_id, _user: False

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="画一张夏日海边照片",
            )
        )

        self.assertEqual(payload["status"], "unauthorized")
        self.assertFalse(payload["generated"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["must_not_claim_sent"])
        self.assertFalse(harness.generated)

    def test_config_is_visible_persistable_and_zero_based(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        setting = schema["photo_action_config"]["items"]["command_photo_generation_max_daily"]
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")

        self.assertEqual(setting["default"], 0)
        self.assertIn("0 表示不限量", setting["hint"])
        self.assertIn('title: "用户请求生图"', script)
        self.assertIn('key: "command_photo_generation_max_daily"', script)
        self.assertGreaterEqual(page_api.count('"command_photo_generation_max_daily"'), 3)

        api = PrivateCompanionPageApi(None)
        self.assertEqual(api._normalize_setting_value("command_photo_generation_max_daily", -5), 0)
        self.assertEqual(api._normalize_setting_value("command_photo_generation_max_daily", 200), 100)


if __name__ == "__main__":
    unittest.main()
