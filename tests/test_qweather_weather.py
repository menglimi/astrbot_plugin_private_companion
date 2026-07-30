# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.constants import _DATA_STORE_KEYS
from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, **_kwargs):
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


class _WeatherHarness(DailyStateMixin):
    weather_source = "qweather"
    weather_api_host = "weather.example.test"
    weather_token = "header.payload.signature"
    weather_lat = 39.9042
    weather_lon = 116.4074

    def __init__(self):
        self.data = {}


class QWeatherWeatherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.harness = _WeatherHarness()

    def test_url_uses_longitude_then_latitude(self):
        parsed = urlparse(self.harness._build_qweather_weather_url())
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v7/weather/now")
        self.assertEqual(query["location"], ["116.4074,39.9042"])
        self.assertEqual(query["lang"], ["zh-Hans"])
        self.assertEqual(query["unit"], ["m"])

    def test_legacy_alert_fields_are_runtime_fallbacks(self):
        self.harness.weather_api_host = ""
        self.harness.weather_token = ""
        self.harness.weather_alert_api_host = "legacy.example.test"
        self.harness.weather_alert_token = "legacy-api-key"

        parsed = urlparse(self.harness._build_qweather_weather_url())
        self.assertEqual(parsed.netloc, "legacy.example.test")
        headers = self.harness._qweather_weather_headers()
        self.assertEqual(headers["X-QW-Api-Key"], "legacy-api-key")
        self.assertNotIn("Authorization", headers)

    async def test_missing_location_or_credentials_does_not_send_request(self):
        self.harness.weather_token = ""
        with patch("aiohttp.ClientSession") as session:
            result = await self.harness._fetch_qweather_weather()
        session.assert_not_called()
        self.assertEqual(result, {"prompt": "", "source": ""})
        self.harness.weather_token = "test-token"
        self.harness.weather_lat = 0
        self.harness.weather_lon = 0
        self.assertEqual(self.harness._build_qweather_weather_url(), "")

    async def test_jwt_and_api_key_auth_are_mutually_exclusive(self):
        capture = {}
        payload = {"code": "200", "now": {"text": "晴", "temp": "26"}}

        with patch("aiohttp.ClientSession", new=lambda **kwargs: _FakeSession(capture, payload, **kwargs)):
            result = await self.harness._fetch_qweather_weather()
        self.assertEqual(result["source"], "qweather")
        self.assertEqual(capture["kwargs"]["headers"]["Authorization"], "Bearer header.payload.signature")
        self.assertNotIn("X-QW-Api-Key", capture["kwargs"]["headers"])

        capture.clear()
        self.harness.weather_token = "0123456789abcdef0123456789abcdef"
        with patch("aiohttp.ClientSession", new=lambda **kwargs: _FakeSession(capture, payload, **kwargs)):
            result = await self.harness._fetch_qweather_weather()
        self.assertEqual(result["source"], "qweather")
        self.assertEqual(capture["kwargs"]["headers"]["X-QW-Api-Key"], self.harness.weather_token)
        self.assertNotIn("Authorization", capture["kwargs"]["headers"])

    async def test_provider_error_or_invalid_now_falls_back_with_empty_result(self):
        capture = {}
        payload = {"code": "401", "now": {"text": "晴", "temp": "26"}}
        with patch("aiohttp.ClientSession", new=lambda **kwargs: _FakeSession(capture, payload, **kwargs)):
            result = await self.harness._fetch_qweather_weather()
        self.assertEqual(result, {"prompt": "", "source": ""})

        self.assertEqual(
            self.harness._parse_qweather_weather_payload({"code": "200", "now": {"text": "晴", "temp": "bad"}}),
            {"prompt": "", "source": ""},
        )

        capture.clear()
        with patch(
            "aiohttp.ClientSession",
            new=lambda **kwargs: _FakeSession(capture, {"code": "200"}, status=403, **kwargs),
        ):
            result = await self.harness._fetch_qweather_weather()
        self.assertEqual(result, {"prompt": "", "source": ""})

    async def test_source_dispatches_to_qweather(self):
        with patch.object(
            self.harness,
            "_fetch_qweather_weather",
            return_value={"prompt": "当前天气 晴，约 26°C。", "source": "qweather"},
        ) as fetch:
            result = await self.harness._fetch_own_weather_prompt()
        fetch.assert_awaited_once()
        self.assertEqual(result["source"], "qweather")

    def test_host_normalization_strips_geo_lookup_path(self):
        self.assertEqual(
            self.harness._normalize_qweather_api_host(
                "https://weather.example.test/geo/v2/city/lookup?location=北京"
            ),
            "https://weather.example.test",
        )
        self.assertEqual(self.harness._normalize_qweather_api_host("http://weather.example.test"), "")
        self.assertEqual(
            self.harness._normalize_qweather_api_host("http://127.0.0.1:8080/geo/v2"),
            "http://127.0.0.1:8080",
        )

    def test_city_lookup_url_encodes_city_and_district_administration(self):
        self.harness.weather_location = "北京"
        parsed = urlparse(self.harness._build_qweather_geo_lookup_url())
        self.assertEqual(parsed.path, "/geo/v2/city/lookup")
        self.assertEqual(parse_qs(parsed.query), {"location": ["北京"], "number": ["1"], "lang": ["zh"]})

        self.harness.weather_location = "朝阳区,北京"
        parsed = urlparse(self.harness._build_qweather_geo_lookup_url())
        query = parse_qs(parsed.query)
        self.assertEqual(query["location"], ["朝阳区"])
        self.assertEqual(query["adm"], ["北京"])

    def test_geo_payload_is_parsed_to_shared_location(self):
        parsed = self.harness._parse_qweather_location_payload(
            {
                "code": "200",
                "location": [
                    {
                        "id": "101010100",
                        "name": "北京",
                        "adm1": "北京市",
                        "adm2": "北京",
                        "lat": "39.90499",
                        "lon": "116.40529",
                    }
                ],
            }
        )
        self.assertEqual(parsed["location_id"], "101010100")
        self.assertEqual(parsed["lat"], 39.90499)
        self.assertEqual(parsed["lon"], 116.40529)
        self.assertEqual(parsed["label"], "北京，北京市")

    async def test_geo_lookup_uses_shared_auth_and_parses_response(self):
        self.harness.weather_location = "北京"
        capture = {}
        payload = {
            "code": "200",
            "location": [
                {
                    "id": "101010100",
                    "name": "北京",
                    "adm1": "北京市",
                    "adm2": "北京",
                    "lat": "39.90499",
                    "lon": "116.40529",
                }
            ],
        }
        with patch("aiohttp.ClientSession", new=lambda **kwargs: _FakeSession(capture, payload, **kwargs)):
            resolved = await self.harness._fetch_qweather_location_lookup("北京")
        parsed = urlparse(capture["endpoint"])
        self.assertEqual(parsed.path, "/geo/v2/city/lookup")
        self.assertEqual(parse_qs(parsed.query)["location"], ["北京"])
        self.assertEqual(capture["kwargs"]["headers"]["Authorization"], "Bearer header.payload.signature")
        self.assertNotIn("X-QW-Api-Key", capture["kwargs"]["headers"])
        self.assertFalse(capture["kwargs"]["allow_redirects"])
        self.assertEqual(resolved["location_id"], "101010100")

    async def test_city_resolution_is_cached_and_weather_uses_location_id(self):
        self.harness.weather_location = "北京"
        lookup = {
            "location_id": "101010100",
            "lat": 39.90499,
            "lon": 116.40529,
            "label": "北京，北京市",
        }
        with patch.object(self.harness, "_fetch_qweather_location_lookup", AsyncMock(return_value=lookup)) as fetch:
            first = await self.harness._resolve_qweather_location()
            second = self.harness._qweather_location_snapshot()
        fetch.assert_awaited_once_with("北京")
        self.assertEqual(first, second)
        self.assertEqual(self.harness.data["qweather_location"]["location_id"], "101010100")
        query = parse_qs(urlparse(self.harness._build_qweather_weather_url(first)).query)
        self.assertEqual(query["location"], ["101010100"])

    async def test_location_change_invalidates_cached_resolution(self):
        self.harness.weather_location = "北京"
        first = {
            "location_id": "101010100",
            "lat": 39.90499,
            "lon": 116.40529,
            "label": "北京",
        }
        second = {
            "location_id": "101020100",
            "lat": 31.2304,
            "lon": 121.4737,
            "label": "上海",
        }
        with patch.object(
            self.harness,
            "_fetch_qweather_location_lookup",
            AsyncMock(side_effect=[first, second]),
        ) as fetch:
            await self.harness._resolve_qweather_location()
            first_key = self.harness.data["qweather_location"]["config_key"]
            self.harness.weather_location = "上海"
            resolved = await self.harness._resolve_qweather_location()
        self.assertEqual(fetch.await_count, 2)
        self.assertNotEqual(first_key, self.harness.data["qweather_location"]["config_key"])
        self.assertEqual(resolved["location_id"], "101020100")

    async def test_concurrent_weather_and_alert_resolution_share_one_lookup(self):
        self.harness.weather_location = "北京"
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def lookup(_query):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {
                "location_id": "101010100",
                "lat": 39.90499,
                "lon": 116.40529,
                "label": "北京",
            }

        with patch.object(self.harness, "_fetch_qweather_location_lookup", side_effect=lookup):
            first = asyncio.create_task(self.harness._resolve_qweather_location())
            await started.wait()
            second = asyncio.create_task(self.harness._resolve_qweather_location())
            await asyncio.sleep(0)
            release.set()
            resolved = await asyncio.gather(first, second)
        self.assertEqual(calls, 1)
        self.assertEqual(resolved[0]["location_id"], resolved[1]["location_id"])

    async def test_location_changed_during_lookup_discards_old_result_and_retries(self):
        self.harness.weather_location = "北京"
        started = asyncio.Event()
        release = asyncio.Event()
        queries = []

        async def lookup(query):
            queries.append(query)
            if query == "北京":
                started.set()
                await release.wait()
                return {
                    "location_id": "101010100",
                    "lat": 39.90499,
                    "lon": 116.40529,
                    "label": "北京",
                }
            return {
                "location_id": "101020100",
                "lat": 31.2304,
                "lon": 121.4737,
                "label": "上海",
            }

        with patch.object(self.harness, "_fetch_qweather_location_lookup", side_effect=lookup):
            task = asyncio.create_task(self.harness._resolve_qweather_location())
            await started.wait()
            self.harness.weather_location = "上海"
            release.set()
            resolved = await task
        self.assertEqual(queries, ["北京", "上海"])
        self.assertEqual(resolved["location_id"], "101020100")
        self.assertEqual(self.harness.data["qweather_location"]["label"], "上海")
        self.assertEqual(
            self.harness.data["qweather_location"]["config_key"],
            self.harness._qweather_location_cache_key(),
        )

    async def test_lookup_failure_preserves_matching_stale_cache_only(self):
        self.harness.weather_location = "北京"
        await self.harness._store_qweather_location(
            {
                "location_id": "101010100",
                "lat": 39.90499,
                "lon": 116.40529,
                "label": "北京",
            }
        )
        self.harness.data["qweather_location"]["fetched_ts"] = 1
        with patch.object(self.harness, "_fetch_qweather_location_lookup", AsyncMock(return_value={})):
            retained = await self.harness._resolve_qweather_location()
            self.harness.weather_location = "上海"
            moved = await self.harness._resolve_qweather_location()
        self.assertEqual(retained["location_id"], "101010100")
        self.assertEqual(moved, {})
        self.assertEqual(self.harness.data["qweather_location"]["location_id"], "101010100")

    async def test_explicit_longitude_latitude_does_not_call_geoapi(self):
        self.harness.weather_location = "116.41,39.92"
        with patch.object(self.harness, "_fetch_qweather_location_lookup", AsyncMock()) as fetch:
            resolved = await self.harness._resolve_qweather_location()
        fetch.assert_not_awaited()
        self.assertEqual((resolved["lat"], resolved["lon"]), (39.92, 116.41))
        query = parse_qs(urlparse(self.harness._build_qweather_weather_url(resolved)).query)
        self.assertEqual(query["location"], ["116.41,39.92"])

    async def test_location_id_weather_survives_temporary_geoapi_failure(self):
        self.harness.weather_location = "101010100"
        with patch.object(self.harness, "_fetch_qweather_location_lookup", AsyncMock(return_value={})):
            resolved = await self.harness._resolve_qweather_location()
        self.assertEqual(resolved["location_id"], "101010100")
        self.assertIsNone(resolved["lat"])
        query = parse_qs(urlparse(self.harness._build_qweather_weather_url(resolved)).query)
        self.assertEqual(query["location"], ["101010100"])
        self.assertEqual(self.harness._build_qweather_alert_url(resolved), "")

    async def test_alert_uses_coordinates_resolved_from_location_id(self):
        self.harness.weather_location = "101010100"
        resolved_lookup = {
            "location_id": "101010100",
            "lat": 39.90499,
            "lon": 116.40529,
            "label": "北京",
        }
        with patch.object(
            self.harness,
            "_fetch_qweather_location_lookup",
            AsyncMock(return_value=resolved_lookup),
        ):
            resolved = await self.harness._resolve_qweather_location()
        parsed = urlparse(self.harness._build_qweather_alert_url(resolved))
        self.assertEqual(parsed.path, "/weatheralert/v1/current/39.9/116.41")

    def test_empty_weather_location_falls_back_to_legacy_coordinates(self):
        self.harness.weather_location = ""
        resolved = self.harness._qweather_location_snapshot()
        self.assertEqual((resolved["lat"], resolved["lon"]), (39.9042, 116.4074))
        parsed = urlparse(self.harness._build_qweather_weather_url())
        self.assertEqual(parse_qs(parsed.query)["location"], ["116.4074,39.9042"])

    def test_weather_cache_key_changes_with_location_without_credentials(self):
        self.harness.weather_location = "北京"
        first = self.harness._weather_context_config_key()
        self.harness.weather_token = "a.different.credential"
        self.assertEqual(first, self.harness._weather_context_config_key())
        self.harness.weather_location = "上海"
        self.assertNotEqual(first, self.harness._weather_context_config_key())

    def test_resolved_location_cache_is_part_of_persistent_store_defaults(self):
        self.assertIn("qweather_location", _DATA_STORE_KEYS)
        self.assertEqual(CoreStoreMixin()._new_store()["qweather_location"], {})
        restored = CoreStoreMixin._ensure_store_defaults({})
        self.assertEqual(restored["qweather_location"], {})


if __name__ == "__main__":
    unittest.main()
