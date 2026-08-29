# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.config_migration import migrate_flat_config_into_schema_groups


class WeatherQWeatherConfigMigrationTests(unittest.TestCase):
    """Migration coverage for the shared QWeather weather configuration."""

    @staticmethod
    def _schema_path(folder: str) -> Path:
        path = Path(folder) / "schema.json"
        items = {
            "weather_source": {"type": "string", "default": "qweather"},
            "weather_api_host": {"type": "string", "default": ""},
            "weather_token": {"type": "string", "default": ""},
            "weather_api_key": {"type": "string", "default": ""},
            "weather_city": {"type": "string", "default": ""},
            "weather_amap_api_key": {"type": "string", "default": ""},
            "weather_amap_city": {"type": "string", "default": ""},
            "weather_alert_api_host": {"type": "string", "default": ""},
            "weather_alert_token": {"type": "string", "default": ""},
            "weather_alert_api_key": {"type": "string", "default": ""},
        }
        path.write_text(
            json.dumps(
                {"weather_config": {"type": "object", "items": items}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def _migrate(self, config: dict) -> dict:
        with tempfile.TemporaryDirectory() as folder:
            migrate_flat_config_into_schema_groups(
                config,
                schema_path=self._schema_path(folder),
                save=False,
            )
        return config

    def test_alert_host_and_api_key_fallback_to_shared_qweather_fields(self) -> None:
        api_key = "01234567" + "89abcdef0123456789abcdef"
        config = {
            "weather_alert_api_host": "https://tenant.qweatherapi.com",
            "weather_alert_api_key": api_key,
            "weather_config": {},
        }

        self._migrate(config)

        weather = config["weather_config"]
        self.assertEqual("https://tenant.qweatherapi.com", weather["weather_api_host"])
        self.assertEqual(api_key, weather["weather_token"])
        self.assertEqual("qweather", weather["weather_source"])
        self.assertEqual(api_key, config["weather_token"])
        self.assertEqual("", weather.get("weather_alert_api_host", ""))
        self.assertEqual("", weather.get("weather_alert_token", ""))
        self.assertEqual("", config["weather_alert_api_key"])

    def test_new_generic_credential_wins_over_old_alert_alias(self) -> None:
        config = {
            "weather_api_host": "https://new.qweatherapi.com",
            "weather_token": "new-token",
            "weather_alert_api_host": "https://old.qweatherapi.com",
            "weather_alert_token": "old-token",
            "weather_config": {},
        }

        self._migrate(config)

        weather = config["weather_config"]
        self.assertEqual("https://new.qweatherapi.com", weather["weather_api_host"])
        self.assertEqual("new-token", weather["weather_token"])

    def test_grouped_generic_credential_wins_over_stale_flat_copy(self) -> None:
        config = {
            "weather_api_host": "https://stale.qweatherapi.com",
            "weather_token": "stale-token",
            "weather_config": {
                "weather_api_host": "https://visible.qweatherapi.com",
                "weather_token": "visible-token",
            },
        }

        self._migrate(config)

        weather = config["weather_config"]
        self.assertEqual("https://visible.qweatherapi.com", weather["weather_api_host"])
        self.assertEqual("visible-token", weather["weather_token"])
        self.assertEqual("https://visible.qweatherapi.com", config["weather_api_host"])
        self.assertEqual("visible-token", config["weather_token"])

    def test_legacy_openweather_fields_infer_openweather_when_source_missing(self) -> None:
        config = {
            "weather_api_key": "openweather-key",
            "weather_city": "Beijing,CN",
            "weather_config": {},
        }

        self._migrate(config)

        self.assertEqual("openweathermap", config["weather_config"]["weather_source"])
        self.assertEqual("openweathermap", config["weather_source"])

    def test_legacy_amap_fields_infer_amap_when_source_missing(self) -> None:
        config = {
            "weather_amap_api_key": "amap-key",
            "weather_amap_city": "110101",
            "weather_config": {},
        }

        self._migrate(config)

        self.assertEqual("amap", config["weather_config"]["weather_source"])
        self.assertEqual("amap", config["weather_source"])

    def test_missing_source_and_legacy_provider_fields_default_to_qweather(self) -> None:
        config = {"weather_config": {}}

        self._migrate(config)

        self.assertEqual("qweather", config["weather_config"]["weather_source"])
        self.assertEqual("qweather", config["weather_source"])

    def test_empty_old_openweather_default_migrates_to_qweather(self) -> None:
        config = {
            "weather_source": "openweathermap",
            "weather_api_key": "",
            "weather_city": "",
            "weather_config": {
                "weather_source": "openweathermap",
                "weather_api_key": "",
                "weather_city": "",
            },
        }

        self._migrate(config)

        self.assertEqual("qweather", config["weather_config"]["weather_source"])
        self.assertEqual("qweather", config["weather_source"])

    def test_explicit_old_source_is_preserved(self) -> None:
        config = {
            "weather_source": "openmeteo",
            "weather_config": {},
        }

        self._migrate(config)

        self.assertEqual("openmeteo", config["weather_config"]["weather_source"])
        self.assertEqual("openmeteo", config["weather_source"])

    def test_visible_legacy_source_wins_over_flat_qweather_default(self) -> None:
        config = {
            "weather_source": "qweather",
            "weather_config": {"weather_source": "openweathermap"},
        }

        self._migrate(config)

        self.assertEqual("openweathermap", config["weather_config"]["weather_source"])
        self.assertEqual("openweathermap", config["weather_source"])

    def test_visible_group_source_wins_over_stale_flat_copy(self) -> None:
        config = {
            "weather_source": "openweathermap",
            "weather_config": {"weather_source": "qweather"},
        }

        self._migrate(config)

        self.assertEqual("qweather", config["weather_config"]["weather_source"])
        self.assertEqual("qweather", config["weather_source"])


if __name__ == "__main__":
    unittest.main()
