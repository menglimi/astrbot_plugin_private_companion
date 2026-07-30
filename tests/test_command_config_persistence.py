# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin
from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _CommandConfigHarness(CommandHandlersMixin):
    def __init__(self, *, save_result: bool) -> None:
        self.save_result = save_result
        self.config: dict[str, Any] = {}
        self.photo_persona_reference_image_path = "old.png"
        self.photo_reference_library = ["old.png || 旧图"]
        self.example_setting = "old"
        self.external_image_api_endpoints = [
            {
                "name": "主用",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://one.example/v1",
                "api_key": "key-one",
                "model": "image-one",
                "size": "1024x1024",
                "timeout_seconds": 60,
            },
            {
                "name": "备选",
                "enabled": True,
                "platform": "openai",
                "base_url": "https://two.example/v1",
                "api_key": "key-two",
                "model": "image-two",
                "size": "1024x1024",
                "timeout_seconds": 90,
            },
        ]

    async def _save_config_if_possible(self) -> bool:
        return self.save_result

    @staticmethod
    def _normalize_external_image_api_endpoints(value: Any) -> list[dict[str, Any]]:
        return [dict(item) for item in list(value or [])]

    @staticmethod
    def _companion_manual_normalize_config_value(_key: str, value: Any) -> tuple[bool, Any, str]:
        return True, str(value), ""

    def _companion_manual_current_config_value(self, key: str) -> Any:
        return getattr(self, key)


class _WeatherCommandHarness(CommandHandlersMixin, DailyStateMixin):
    def __init__(self, *, save_result: bool = True) -> None:
        self.save_result = save_result
        self.weather_source = "qweather"
        self.weather_api_host = "weather.example.test"
        self.weather_token = "test-api-key"
        self.weather_location = "北京"
        self.config = {
            "environment": {"weather_location": "北京"},
            "weather_location": "北京",
        }
        self.data = {
            "qweather_location": {"old": True},
            "daily_weather": {"prompt": "旧天气"},
            "weather_alerts": {"alerts": [{"id": "old"}]},
            "weather_alert_awareness": {"last": "old"},
        }
        self._data_lock = asyncio.Lock()

    async def _save_config_if_possible(self) -> bool:
        return self.save_result

    def _save_data_sync(self) -> None:
        return None


class CommandConfigPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_reference_path_rolls_back_when_save_fails(self) -> None:
        harness = _CommandConfigHarness(save_result=False)
        harness.config["photo_persona_reference_image_path"] = "old.png"

        saved = await harness._set_photo_reference_config_path("new.png")

        self.assertFalse(saved)
        self.assertEqual("old.png", harness.photo_persona_reference_image_path)
        self.assertEqual("old.png", harness.config["photo_persona_reference_image_path"])

    async def test_photo_library_rolls_back_when_save_fails(self) -> None:
        harness = _CommandConfigHarness(save_result=False)
        harness.config["photo_reference_library"] = ["old.png || 旧图"]

        saved = await harness._set_photo_reference_library_config(["new.png || 新图"])

        self.assertFalse(saved)
        self.assertEqual(["old.png || 旧图"], harness.photo_reference_library)
        self.assertEqual(["old.png || 旧图"], harness.config["photo_reference_library"])

    async def test_manual_setting_rolls_back_when_save_fails(self) -> None:
        harness = _CommandConfigHarness(save_result=False)
        harness.config["example_setting"] = "old"

        ok, error, old, new = await harness._companion_manual_apply_config_value(
            "example_setting",
            "new",
        )

        self.assertFalse(ok)
        self.assertIn("已恢复", error)
        self.assertEqual("old", old)
        self.assertEqual("old", new)
        self.assertEqual("old", harness.example_setting)
        self.assertEqual("old", harness.config["example_setting"])

    async def test_image_api_queue_swap_rolls_back_when_save_fails(self) -> None:
        harness = _CommandConfigHarness(save_result=False)
        original_models = [item["model"] for item in harness.external_image_api_endpoints]

        message = await harness._swap_external_image_api_command_text()

        self.assertIn("已恢复原顺序", message)
        self.assertEqual(
            original_models,
            [item["model"] for item in harness.external_image_api_endpoints],
        )

    async def test_successful_photo_path_save_keeps_new_value(self) -> None:
        harness = _CommandConfigHarness(save_result=True)

        saved = await harness._set_photo_reference_config_path("new.png")

        self.assertTrue(saved)
        self.assertEqual("new.png", harness.photo_persona_reference_image_path)

    async def test_qweather_city_bind_resolves_then_persists_and_invalidates_cache(self) -> None:
        harness = _WeatherCommandHarness()
        resolved = {
            "location_id": "101020100",
            "lat": 31.2304,
            "lon": 121.4737,
            "label": "上海，上海市",
        }
        with patch.object(
            harness,
            "_fetch_qweather_location_lookup",
            AsyncMock(return_value=resolved),
        ) as lookup:
            message = await harness._qweather_location_command_text("绑定城市", "上海")

        lookup.assert_awaited_once_with("上海")
        self.assertIn("已绑定城市：上海，上海市", message)
        self.assertIn("101020100", message)
        self.assertEqual("上海", harness.weather_location)
        self.assertEqual("上海", harness.config["environment"]["weather_location"])
        self.assertEqual("上海", harness.config["weather_location"])
        self.assertNotIn("daily_weather", harness.data)
        self.assertEqual("101020100", harness.data["qweather_location"]["location_id"])
        self.assertEqual({}, harness.data["weather_alerts"])
        self.assertEqual({}, harness.data["weather_alert_awareness"])

    async def test_qweather_city_bind_failure_preserves_existing_setting(self) -> None:
        harness = _WeatherCommandHarness()
        with patch.object(
            harness,
            "_fetch_qweather_location_lookup",
            AsyncMock(return_value={}),
        ):
            message = await harness._qweather_location_command_text("绑定城市", "不存在的城市")

        self.assertIn("原城市未修改", message)
        self.assertEqual("北京", harness.weather_location)
        self.assertEqual("北京", harness.config["environment"]["weather_location"])

    async def test_qweather_city_bind_rolls_back_when_config_save_fails(self) -> None:
        harness = _WeatherCommandHarness(save_result=False)
        resolved = {
            "location_id": "101020100",
            "lat": 31.2304,
            "lon": 121.4737,
            "label": "上海，上海市",
        }
        with patch.object(
            harness,
            "_fetch_qweather_location_lookup",
            AsyncMock(return_value=resolved),
        ):
            message = await harness._qweather_location_command_text("绑定城市", "上海")

        self.assertIn("原城市仍然保留", message)
        self.assertEqual("北京", harness.weather_location)
        self.assertEqual("北京", harness.config["environment"]["weather_location"])
        self.assertEqual({"old": True}, harness.data["qweather_location"])

    async def test_qweather_city_command_checks_source_and_supports_view_unbind(self) -> None:
        harness = _WeatherCommandHarness()
        harness.weather_source = "openmeteo"
        message = await harness._qweather_location_command_text("绑定城市", "上海")
        self.assertIn("当前天气来源不是和风天气", message)

        harness.weather_source = "qweather"
        view = await harness._qweather_location_command_text("查看城市")
        self.assertIn("当前绑定城市：北京", view)
        unbound = await harness._qweather_location_command_text("解绑城市")
        self.assertIn("已清除绑定城市", unbound)
        self.assertEqual("", harness.weather_location)
        self.assertEqual("", harness.config["environment"]["weather_location"])

    async def test_photo_path_save_preserves_long_path_and_internal_spaces(self) -> None:
        harness = _CommandConfigHarness(save_result=True)
        path = '"C:/reference/' + ("nested folder/" * 22) + 'persona  original.png"'

        saved = await harness._set_photo_reference_config_path(path)

        expected = path[1:-1]
        self.assertGreater(len(expected), 260)
        self.assertTrue(saved)
        self.assertEqual(expected, harness.photo_persona_reference_image_path)
        self.assertIn("persona  original.png", harness.config["photo_persona_reference_image_path"])


if __name__ == "__main__":
    unittest.main()
