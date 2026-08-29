# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return self.payload


class _FakeSession:
    def __init__(self, capture, payload, status: int = 200, **_kwargs):
        self.capture = capture
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, endpoint, **kwargs):
        self.capture.update({"endpoint": endpoint, "kwargs": kwargs})
        return _FakeResponse(self.payload, self.status)


class _WeatherAlertHarness(DailyStateMixin):
    enable_weather_alerts = True
    weather_alert_api_host = "alerts.example.test"
    weather_alert_token = "header.payload.signature"
    weather_alert_refresh_minutes = 10
    weather_lat = 39.92
    weather_lon = 116.41

    def __init__(self) -> None:
        self.data = {}
        self.saved = 0

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class WeatherAlertTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.harness = _WeatherAlertHarness()

    def test_current_payload_is_normalized_and_colors_are_filterable(self) -> None:
        payload = {
            "code": "200",
            "metadata": {
                "tag": "tag-1",
                "zeroResult": False,
                "attributions": [
                    "https://developer.qweather.com/attribution.html",
                    "当前预警数据可能存在延迟或信息过时，以官方数据发布为准。",
                ],
            },
            "alerts": [
                {
                    "id": "alert-1",
                    "senderName": "北京市气象台",
                    "issuedTime": "2026-07-26T08:00+08:00",
                    "messageType": {"code": "update", "supersedes": ["old-1"]},
                    "eventType": {"name": "雷电", "code": "1103"},
                    "severity": "Moderate",
                    "color": {"code": "orange"},
                    "effectiveTime": "2026-07-26T08:00+08:00",
                    "expireTime": "2026-07-26T18:00+08:00",
                    "headline": "雷电橙色预警",
                    "description": "请注意防范。\n减少户外活动。",
                    "instruction": "做好防护",
                }
            ],
        }

        parsed = self.harness._parse_qweather_alert_payload(payload)

        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["provider_tag"], "tag-1")
        self.assertEqual(2, len(parsed["provider_attributions"]))
        alert = parsed["alerts"][0]
        self.assertEqual(alert["id"], "alert-1")
        self.assertEqual(alert["sender"], "北京市气象台")
        self.assertEqual(alert["event"], "雷电")
        self.assertEqual(alert["event_code"], "1103")
        self.assertEqual(alert["color"], "橙色")
        self.assertEqual(alert["color_code"], "orange")
        self.assertEqual(alert["supersedes"], ["old-1"])
        self.assertNotIn("\n", alert["description"])
        self.assertEqual(len(self.harness._filter_weather_alerts([alert], "red")), 0)
        self.assertEqual(len(self.harness._filter_weather_alerts([alert], "orange")), 1)

    def test_yellow_alert_with_global_severity_does_not_pass_orange_threshold(self) -> None:
        # 回归：qweather 国际 severity 档位与国内颜色错位一档（黄色预警 severity=moderate），
        # 旧代码 max(color, severity) 让黄色顶穿 orange 阈值（实测 08-14 黄色暴雨/雷电被误发）。
        # 修复后颜色等级为准：黄色=1 < orange=2，必须被挡。
        yellow = {"id": "y1", "color": "黄色", "color_code": "yellow", "severity": "moderate"}
        orange = {"id": "o1", "color": "橙色", "color_code": "orange", "severity": "severe"}
        blue = {"id": "b1", "color": "蓝色", "color_code": "blue", "severity": "minor"}
        self.assertEqual(len(self.harness._filter_weather_alerts([yellow], "orange")), 0)
        self.assertEqual(len(self.harness._filter_weather_alerts([orange], "orange")), 1)
        self.assertEqual(len(self.harness._filter_weather_alerts([blue], "orange")), 0)
        self.assertEqual(len(self.harness._filter_weather_alerts([yellow, orange], "orange")), 1)
        # 颜色缺失（非中文/全球预警源）时退回 severity 兜底
        sev_only = {"id": "s1", "severity": "extreme"}
        self.assertEqual(len(self.harness._filter_weather_alerts([sev_only], "orange")), 1)
        self.assertEqual(len(self.harness._filter_weather_alerts([sev_only], "red")), 1)

    def test_legacy_warning_payload_and_duplicate_revision_are_supported(self) -> None:
        old_payload = {
            "code": "200",
            "warning": [
                {
                    "id": "legacy-1",
                    "sender": "地方气象台",
                    "pubTime": "2026-07-26T07:00+08:00",
                    "typeName": "暴雨",
                    "level": "黄色",
                    "title": "暴雨黄色预警",
                },
                {
                    "id": "legacy-1",
                    "sender": "地方气象台",
                    "pubTime": "2026-07-26T08:00+08:00",
                    "typeName": "暴雨",
                    "level": "黄色",
                    "title": "暴雨黄色预警更新",
                    "description": "请减少外出。",
                },
            ],
        }

        parsed = self.harness._parse_qweather_alert_payload(old_payload)
        deduped = self.harness._dedupe_weather_alerts(parsed["alerts"])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["issued_time"], "2026-07-26T08:00+08:00")
        self.assertEqual(deduped[0]["color"], "黄色")
        self.assertEqual(self.harness._normalize_qweather_alert_payload(old_payload)[0]["id"], "legacy-1")

    def test_same_id_instruction_or_expiry_change_is_a_new_revision(self) -> None:
        base = self.harness._normalize_qweather_alert_payload(
            {
                "alerts": [
                    {
                        "id": "same-id",
                        "issuedTime": "2026-07-26T08:00+08:00",
                        "eventType": {"name": "大风"},
                        "color": {"code": "yellow"},
                        "expireTime": "2026-07-26T18:00+08:00",
                        "instruction": "关好门窗",
                    }
                ]
            }
        )
        changed = self.harness._normalize_qweather_alert_payload(
            {
                "alerts": [
                    {
                        "id": "same-id",
                        "issuedTime": "2026-07-26T08:00+08:00",
                        "eventType": {"name": "大风"},
                        "color": {"code": "yellow"},
                        "expireTime": "2026-07-26T20:00+08:00",
                        "instruction": "减少户外停留",
                    }
                ]
            }
        )

        merged = self.harness._merge_weather_alert_cache({}, base)
        revision = self.harness._merge_weather_alert_cache(merged, changed)
        self.assertEqual(revision["updated_alert_ids"], ["same-id"])

    async def test_current_api_url_and_bearer_header(self) -> None:
        capture = {}
        payload = {"code": "200", "metadata": {"zeroResult": True}, "alerts": []}

        def session_factory(**kwargs):
            return _FakeSession(capture, payload, **kwargs)

        with patch("aiohttp.ClientSession", new=session_factory):
            result = await self.harness._fetch_qweather_alerts()

        self.assertTrue(result["ok"])
        self.assertEqual(
            capture["endpoint"],
            "https://alerts.example.test/weatheralert/v1/current/39.92/116.41?localTime=true&lang=zh",
        )
        self.assertEqual(
            capture["kwargs"]["headers"]["Authorization"],
            "Bearer header.payload.signature",
        )
        self.assertNotIn("X-QW-Api-Key", capture["kwargs"]["headers"])
        self.assertEqual(capture["kwargs"]["headers"]["Accept"], "application/json")

    def test_current_api_rounds_coordinates_to_documented_precision(self) -> None:
        self.harness.weather_lat = 39.9042
        self.harness.weather_lon = 116.4074
        self.assertEqual(
            self.harness._build_qweather_alert_url(),
            "https://alerts.example.test/weatheralert/v1/current/39.9/116.41?localTime=true&lang=zh",
        )

    async def test_api_key_header_is_used_without_bearer(self) -> None:
        capture = {}
        payload = {"code": "200", "metadata": {"zeroResult": True}, "alerts": []}
        api_key = "01234567" + "89abcdef0123456789abcdef"
        self.harness.weather_alert_token = api_key

        def session_factory(**kwargs):
            return _FakeSession(capture, payload, **kwargs)

        with patch("aiohttp.ClientSession", new=session_factory):
            result = await self.harness._fetch_qweather_alerts()

        self.assertTrue(result["ok"])
        headers = capture["kwargs"]["headers"]
        self.assertEqual(headers["X-QW-Api-Key"], api_key)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["Accept"], "application/json")

    def test_bearer_prefix_is_normalized_without_changing_auth_mode(self) -> None:
        self.harness.weather_alert_token = "Bearer header.payload.signature"
        headers = self.harness._qweather_alert_headers()
        self.assertEqual(headers["Authorization"], "Bearer header.payload.signature")
        self.assertNotIn("X-QW-Api-Key", headers)

    async def test_cache_hit_and_outage_preserve_last_success(self) -> None:
        capture = {}
        payload = {
            "code": "200",
            "alerts": [
                {
                    "id": "cached-1",
                    "eventType": {"name": "大风"},
                    "color": {"code": "yellow"},
                    "headline": "大风黄色预警",
                }
            ],
        }

        def successful_session(**kwargs):
            return _FakeSession(capture, payload, **kwargs)

        with patch("aiohttp.ClientSession", new=successful_session):
            first = await self.harness._ensure_weather_alert_context(force=True)

        self.assertTrue(first["refreshed"])
        self.assertEqual([item["id"] for item in first["alerts"]], ["cached-1"])
        self.assertEqual(self.harness.saved, 1)
        self.assertEqual(len(capture), 2)

        capture.clear()
        with patch("aiohttp.ClientSession", new=lambda **kwargs: _FakeSession(capture, payload, **kwargs)):
            second = await self.harness._ensure_weather_alert_context(force=False)
        self.assertFalse(second["refreshed"])
        self.assertEqual(capture, {})

        def failing_session(**kwargs):
            return _FakeSession(capture, {}, status=503, **kwargs)

        with patch("aiohttp.ClientSession", new=failing_session):
            failed = await self.harness._ensure_weather_alert_context(force=True)
        self.assertFalse(failed["refreshed"])
        self.assertTrue(failed["stale"])
        self.assertEqual(failed["alerts"][0]["id"], "cached-1")
        self.assertEqual(failed["error"], "http_503")

        self.harness.weather_lat = 31.23
        with patch("aiohttp.ClientSession", new=failing_session):
            moved = await self.harness._ensure_weather_alert_context(force=True)
        self.assertEqual([], moved["alerts"])
        self.assertTrue(moved["stale"])
        self.assertEqual(moved["error"], "http_503")

    async def test_missing_configuration_does_not_make_http_request(self) -> None:
        self.harness.weather_alert_token = ""
        result = await self.harness._fetch_qweather_alerts()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_configured")


if __name__ == "__main__":
    unittest.main()
