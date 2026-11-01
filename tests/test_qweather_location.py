"""QWeather geocoding keeps city-name setup compatible with weather and alerts."""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from astrbot_host.qweather_alerts import QWeatherAlertPort
from astrbot_host.qweather_credentials import QWeatherCredentials
from astrbot_host.qweather_location import resolve_qweather_location
from astrbot_host.qweather_weather import QWeatherWeatherPort
from companion_v2.agenda.planning_config import WeatherLocation


NOW = datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)
API_HOST = "example.xy.qweatherapi.com"
API_KEY = "0123456789abcdef0123456789abcdef"
GEOCODED = {"code": "200", "location": [{"name": "测试区域", "lat": "12.34", "lon": "56.78"}]}
VIENNA = {"code": "200", "location": [{
    "name": "维也纳", "id": "101010100", "lat": "48.20849", "lon": "16.37208",
    "adm2": "维也纳", "adm1": "维也纳", "country": "奥地利"}]}


class Reply:
    status = 200
    content_length = None

    def __init__(self, value):
        self.content = self
        self.raw = json.dumps(value, ensure_ascii=False).encode("utf-8")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def read(self, limit):
        return self.raw[:limit]


class Session:
    def __init__(self, replies, calls):
        self.replies, self.calls = iter(replies), calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.replies)


class QWeatherLocationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.credentials = QWeatherCredentials(api_host=API_HOST, api_key=API_KEY)
        self.location = WeatherLocation("测试省测试市测试区域", None, None, "测试市", "测试区域")

    async def test_current_weather_resolves_name_with_qweather_geo_api(self):
        calls = []
        session = Session([Reply(GEOCODED), Reply({"code": "200", "now": {
            "temp": "22", "text": "小雨", "icon": "305", "humidity": "75",
            "windSpeed": "12", "obsTime": "2026-09-30T15:00+08:00"}})], calls)
        port = QWeatherWeatherPort(self.credentials, now=lambda: NOW)
        with patch("astrbot_host.qweather_weather.aiohttp.ClientSession", return_value=session):
            snapshot = await port.fetch(self.location)

        self.assertEqual(snapshot.provider, "qweather")
        self.assertEqual(snapshot.weather_code, 61)
        self.assertEqual(calls[0][0], f"https://{API_HOST}/geo/v2/city/lookup")
        # No "range": the lookup must not be confined to one country.
        self.assertEqual(calls[0][1]["params"], {"location": self.location.name,
            "number": 10, "lang": "zh", "adm": "测试市"})
        self.assertEqual(calls[1][0], f"https://{API_HOST}/v7/weather/now/56.78,12.34")

    async def test_a_role_abroad_resolves_without_a_country_filter(self):
        """``range=cn`` made the whole chain unavailable for any role outside China."""
        calls = []
        session = Session([Reply(VIENNA), Reply({"code": "200", "now": {
            "temp": "8", "text": "多云", "icon": "101", "humidity": "72",
            "windSpeed": "15", "obsTime": "2026-09-30T09:00+02:00"}})], calls)
        location = WeatherLocation("维也纳", None, None)
        port = QWeatherWeatherPort(self.credentials, now=lambda: NOW)
        with patch("astrbot_host.qweather_weather.aiohttp.ClientSession", return_value=session):
            snapshot = await port.fetch(location)

        self.assertNotIn("range", calls[0][1]["params"])
        self.assertEqual(snapshot.location, "维也纳")
        self.assertEqual(snapshot.weather_code, 2)
        self.assertEqual(calls[1][1]["params"], {"location": "16.37,48.21"})

    async def test_a_same_named_domestic_place_still_wins_over_a_foreign_one(self):
        """Dropping the filter must not let a foreign hit shadow the usual result."""
        domestic = {"name": "同名", "lat": "31.23", "lon": "121.47",
                    "adm2": "同名市", "adm1": "某省", "country": "中国"}
        foreign = {"name": "同名", "lat": "48.20", "lon": "16.37",
                   "adm2": "同名", "adm1": "同名", "country": "奥地利"}
        calls = []
        session = Session([Reply({"code": "200", "location": [foreign, domestic]})], calls)

        resolved = await resolve_qweather_location(session, WeatherLocation("同名", None, None),
                                                  self.credentials, NOW)

        self.assertEqual((resolved.latitude, resolved.longitude), (31.23, 121.47))

    async def test_official_alerts_resolve_name_before_querying_qweather(self):
        alerts = {"metadata": {"zeroResult": True}, "alerts": []}
        calls = []
        session = Session([Reply(GEOCODED), Reply(alerts)], calls)
        port = QWeatherAlertPort(self.credentials, now=lambda: NOW)
        with patch("astrbot_host.qweather_alerts.aiohttp.ClientSession", return_value=session):
            observation = await port.fetch(self.location)

        self.assertEqual(observation.alerts, ())
        self.assertEqual(calls[0][0], f"https://{API_HOST}/geo/v2/city/lookup")
        self.assertEqual(calls[1][0], f"https://{API_HOST}/weatheralert/v1/current/12.34/56.78")

    async def test_unresolvable_name_fails_closed_before_weather_request(self):
        calls = []
        session = Session([Reply({"code": "200", "location": []})], calls)
        port = QWeatherWeatherPort(self.credentials, now=lambda: NOW)
        with patch("astrbot_host.qweather_weather.aiohttp.ClientSession", return_value=session):
            with self.assertRaisesRegex(ValueError, "could not be resolved"):
                await port.fetch(self.location)
        self.assertEqual(len(calls), 1)
