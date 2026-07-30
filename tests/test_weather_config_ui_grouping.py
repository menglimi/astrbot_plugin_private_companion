# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WeatherConfigUiGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        cls.page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")
        cls.schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))

    def test_runtime_settings_no_longer_contains_weather_form(self) -> None:
        self.assertNotIn('name="enable_weather_context"', self.html)
        self.assertNotIn('name="weather_api_key"', self.html)
        self.assertNotIn('name="weather_city"', self.html)
        self.assertIn("仅保留未归入功能开关的主动节奏与资源配额", self.html)

    def test_weather_is_embedded_in_environment_perception(self) -> None:
        self.assertIn('enable_weather_context: "enable_environment_perception"', self.script)
        self.assertIn('enable_environment_change_proactive: "enable_environment_perception"', self.script)
        self.assertIn('title: "天气上下文"', self.script)
        self.assertIn('"weather_source", "weather_api_host", "weather_token", "weather_location"', self.script)
        self.assertIn('"weather_amap_api_key", "weather_amap_city", "weather_lat", "weather_lon"', self.script)
        self.assertIn('title: "高级定位与兼容来源"', self.script)

    def test_weather_source_controls_exist_in_schema_and_companion_panel(self) -> None:
        weather = self.schema["weather_config"]["items"]

        self.assertEqual(["qweather", "openweathermap", "openmeteo", "amap"], weather["weather_source"]["options"])
        self.assertEqual("qweather", weather["weather_source"]["default"])
        self.assertEqual("amap", weather["weather_amap_api_key"]["condition"]["weather_source"])
        self.assertEqual("amap", weather["weather_amap_city"]["condition"]["weather_source"])
        self.assertIn('weather_source: { type: "select"', self.script)
        self.assertIn('weather_api_host: { type: "text" }', self.script)
        self.assertIn('weather_token: { type: "password" }', self.script)
        self.assertIn('weather_location: { type: "text" }', self.script)
        self.assertIn('weather_amap_api_key: { type: "password"', self.script)
        self.assertIn('weatherSource !== "openweathermap"', self.script)
        self.assertIn('weatherSource !== "amap"', self.script)

    def test_qweather_defaults_to_shared_host_and_credential_fields(self) -> None:
        weather = self.schema["weather_config"]["items"]

        for key in ("weather_api_host", "weather_token", "weather_location"):
            self.assertEqual("", weather[key]["default"], key)
            self.assertEqual({"enable_weather_context": True}, weather[key]["condition"], key)
        self.assertIn("和风天气（推荐）", weather["weather_source"]["labels"])
        self.assertIn("专属 API Host", weather["weather_api_host"]["hint"])
        self.assertIn("https://console.qweather.com/setting?lang=zh", weather["weather_api_host"]["hint"])
        self.assertIn("不要手动添加 Bearer 前缀", weather["weather_token"]["hint"])
        self.assertIn("https://console.qweather.com/project?lang=zh", weather["weather_token"]["hint"])
        self.assertEqual("天气地点（城市/区县/LocationID）", weather["weather_location"]["description"])
        for example in ("北京", "朝阳区,北京", "101010100", "116.41,39.92"):
            self.assertIn(example, weather["weather_location"]["hint"])

    def test_weather_location_keeps_flat_compatibility_key_and_legacy_coordinates(self) -> None:
        self.assertEqual("", self.schema["weather_location"]["default"])
        self.assertTrue(self.schema["weather_location"]["invisible"])
        weather = self.schema["weather_config"]["items"]
        self.assertEqual("float", weather["weather_lat"]["type"])
        self.assertEqual("float", weather["weather_lon"]["type"])
        advanced_section = self.script.split('title: "高级定位与兼容来源"', 1)[1].split('title: "余额与补给"', 1)[0]
        self.assertIn('keys: ["weather_lat", "weather_lon"', advanced_section)

    def test_weather_alert_controls_are_optional_and_scoped(self) -> None:
        weather = self.schema["weather_config"]["items"]

        self.assertFalse(weather["enable_weather_alerts"]["default"])
        self.assertEqual("", weather["weather_alert_api_host"]["default"])
        self.assertEqual("", weather["weather_alert_token"]["default"])
        self.assertTrue(weather["weather_alert_api_host"]["invisible"])
        self.assertTrue(weather["weather_alert_token"]["invisible"])
        self.assertEqual(10, weather["weather_alert_refresh_minutes"]["default"])
        self.assertEqual([5, 60, 5], [
            weather["weather_alert_refresh_minutes"]["slider"]["min"],
            weather["weather_alert_refresh_minutes"]["slider"]["max"],
            weather["weather_alert_refresh_minutes"]["slider"]["step"],
        ])
        self.assertEqual(["blue", "yellow", "orange", "red", "all"], weather["weather_alert_min_severity"]["options"])
        for key in ("weather_alert_refresh_minutes", "weather_alert_min_severity"):
            self.assertEqual(
                {"enable_weather_context": True, "enable_weather_alerts": True},
                weather[key]["condition"],
                key,
            )
        self.assertIn('enable_weather_alerts: { type: "checkbox" }', self.script)
        self.assertIn('weather_alert_min_severity: { type: "select"', self.script)
        self.assertIn("weather_api_host", self.script)
        self.assertIn("weather_token", self.script)
        self.assertIn("气象预警已启用", self.script)

    def test_legacy_weather_alert_key_is_hidden_compatibility_field(self) -> None:
        self.assertEqual("", self.schema["weather_alert_api_key"]["default"])
        self.assertTrue(self.schema["weather_alert_api_key"]["invisible"])
        for key in ("weather_api_host", "weather_token", "weather_alert_api_host", "weather_alert_token"):
            self.assertIn(key, self.schema["weather_config"]["items"] if key.startswith("weather_") and key not in {"weather_alert_api_key"} else self.schema)
        self.assertTrue(self.schema["weather_api_host"]["invisible"])
        self.assertTrue(self.schema["weather_token"]["invisible"])
        self.assertIn('"weather_alert_api_host"', self.script)
        self.assertIn('"weather_alert_token"', self.script)

    def test_environment_weather_section_keeps_status_and_conditional_controls(self) -> None:
        self.assertIn("function environmentWeatherStatusHtml()", self.script)
        self.assertIn("weatherChildren.has(settingKey)", self.script)
        self.assertIn('["enable_weather_context", "enable_weather_alerts", "enable_environment_change_proactive"]', self.script)
        self.assertIn('const hasWeatherLocation = Boolean(weatherLocation) || hasCoordinates;', self.script)
        self.assertIn('const locationLabel = String(cache.location_label || "").trim();', self.script)
        self.assertIn('已解析地点：${locationLabel}', self.script)
        self.assertIn('"location_label": location_label', self.page_api)
        self.assertIn('weather_source == "qweather" or bool(', self.page_api)
        self.assertIn('status = "和风天气已就绪：接口凭据和天气地点均已配置。";', self.script)
        self.assertNotIn('status = `和风天气已就绪：${weatherApiHost}', self.script)
        self.assertNotIn('status = `和风天气已就绪：${weatherToken}', self.script)

    def test_qweather_console_links_are_between_host_and_credential_fields(self) -> None:
        self.assertIn("function qweatherConsoleLinksHtml()", self.script)
        self.assertIn('name === "weather_api_host" && weatherSource === "qweather"', self.script)
        self.assertIn('item.key === "enable_weather_context" && setting.key === "weather_api_host"', self.script)
        self.assertIn('href="https://console.qweather.com/setting?lang=zh"', self.script)
        self.assertIn('href="https://console.qweather.com/project?lang=zh"', self.script)
        self.assertIn('target="_blank" rel="noopener noreferrer"', self.script)

    def test_setup_guide_contains_minimal_qweather_fields_and_optional_alert(self) -> None:
        self.assertIn('key: "enable_weather_context"', self.script)
        self.assertIn('title: "和风天气（推荐）"', self.script)
        self.assertIn('key: "weather_api_host", type: "text"', self.script)
        self.assertIn('key: "weather_token", type: "password"', self.script)
        self.assertIn('key: "weather_location", type: "text"', self.script)
        self.assertIn('key: "enable_weather_alerts", type: "bool"', self.script)
        self.assertIn('showWhen: (draft) => Boolean(draft.enable_weather_alerts)', self.script)
        self.assertIn('weather_api_host: ""', self.script)
        self.assertIn('weather_token: ""', self.script)
        self.assertIn('weather_location: ""', self.script)
        guide = self.script.split('title: "和风天气（推荐）"', 1)[1].split('key: "enable_expression_learning"', 1)[0]
        self.assertNotIn('key: "weather_lat"', guide)
        self.assertNotIn('key: "weather_lon"', guide)


if __name__ == "__main__":
    unittest.main()
