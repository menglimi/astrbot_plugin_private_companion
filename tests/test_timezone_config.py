# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.helpers import _normalize_timezone_name
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class TimezoneConfigTests(unittest.TestCase):
    def test_timezone_normalization_accepts_iana_and_rejects_invalid_names(self) -> None:
        self.assertEqual(_normalize_timezone_name("Asia/Tokyo"), "Asia/Tokyo")
        self.assertEqual(_normalize_timezone_name("invalid/timezone"), "Asia/Shanghai")
        self.assertEqual(_normalize_timezone_name(""), "Asia/Shanghai")

    def test_page_normalizes_main_and_deepseek_timezones(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        self.assertEqual(api._normalize_setting_value("environment_perception_timezone", "Asia/Tokyo"), "Asia/Tokyo")
        self.assertEqual(api._normalize_setting_value("environment_perception_timezone", "invalid/timezone"), "Asia/Shanghai")
        self.assertEqual(api._normalize_setting_value("deepseek_peak_timezone", "Europe/London"), "Europe/London")

    def test_applying_main_timezone_updates_runtime_date_boundary(self) -> None:
        plugin = SimpleNamespace(config={})
        api = PrivateCompanionPageApi(plugin)
        with patch("astrbot_plugin_private_companion.page_api._set_today_key_timezone") as set_timezone:
            api._apply_config_value("environment_perception_timezone", "Asia/Tokyo")
        self.assertEqual(plugin.environment_perception_timezone, "Asia/Tokyo")
        set_timezone.assert_called_once_with("Asia/Tokyo")
        stored = plugin.config.get("environment_perception_timezone")
        if stored is None:
            stored = plugin.config.get("basic_config", {}).get("environment_perception_timezone")
        self.assertEqual(stored, "Asia/Tokyo")


if __name__ == "__main__":
    unittest.main()
