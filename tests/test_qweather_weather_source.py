"""QWeather current-weather adapter: code mapping, normalization and port selection."""

import asyncio
import base64
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from astrbot_host.qweather_credentials import QWeatherCredentials, credentials_from_environment
from astrbot_host.qweather_weather import (TOTAL_TIMEOUT_SECONDS, QWeatherWeatherPort,
                                           configured_qweather_weather_port,
                                           normalize_qweather_now, weather_code_for)
from astrbot_host.weather import OpenMeteoWeatherPort
from companion_v2.agenda.planning_config import WeatherLocation


LOCATION = WeatherLocation("角色所在城市", 31.23, 121.47)
NOW = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
API_KEY = "0123456789abcdef0123456789abcdef"


def body(**overrides):
    return {"code": "200", "now": {"temp": "22", "text": "小雨", "icon": "305",
        "humidity": "75", "windSpeed": "12", "obsTime": "2026-09-28T17:00+08:00", **overrides}}


class WeatherCodeMappingTests(unittest.TestCase):
    def test_known_icons_map_to_their_wmo_code(self):
        for icon, expected in ((100, 0), (104, 3), (305, 61), (400, 71), (900, 0), (901, 3)):
            with self.subTest(icon=icon):
                self.assertEqual(weather_code_for(icon), expected)

    def test_fog_haze_and_dust_share_the_fog_code_and_never_rime(self):
        """QWeather 501 is fog, not rime; 502-508 are haze, sand and dust.

        WMO has no dust code, so the family shares 45 and the rime code 48 is never
        produced by any QWeather icon.
        """
        for icon in (500, 501, 502, 503, 504, 507, 508, 509, 510, 511, 512, 513, 514, 515):
            with self.subTest(icon=icon):
                self.assertEqual(weather_code_for(icon), 45)

    def test_temperature_extremes_are_not_thunderstorms(self):
        """900 (热) / 901 (冷) describe temperature, not a sky, and certainly not thunder."""
        self.assertEqual(weather_code_for(900), 0)
        self.assertEqual(weather_code_for(901), 3)

    def test_unknown_icon_is_not_clear_sky(self):
        self.assertIsNone(weather_code_for(999))
        self.assertIsNone(weather_code_for("999"))
        self.assertIsNone(weather_code_for(950))  # no 9xx family guess either

    def test_unknown_icon_falls_back_to_its_family_not_to_failure(self):
        """A code this table has never seen must still yield a usable sky."""
        self.assertEqual(weather_code_for(199), 2)    # 1xx clear family
        self.assertEqual(weather_code_for(325), 80)   # 3xx rain family
        self.assertEqual(weather_code_for(499), 73)   # 4xx snow family
        self.assertEqual(weather_code_for(599), 45)   # 5xx fog family
        self.assertEqual(weather_code_for(1234), None)  # no family at all

    def test_unusable_values_are_rejected_rather_than_guessed(self):
        for value in (None, "x", "", -1, 3.5, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(weather_code_for(value))

    def test_numeric_string_is_accepted_because_the_api_returns_strings(self):
        self.assertEqual(weather_code_for("305"), weather_code_for(305))


class NormalizeTests(unittest.TestCase):
    def test_envelope_maps_onto_the_shared_snapshot_contract(self):
        snapshot = normalize_qweather_now(body(), LOCATION)
        self.assertEqual(snapshot.provider, "qweather")
        self.assertEqual(snapshot.location, LOCATION.name)
        self.assertEqual(snapshot.temperature_c, 22)
        self.assertEqual(snapshot.condition, "小雨")
        self.assertEqual(snapshot.humidity_pct, 75)
        self.assertEqual(snapshot.wind_kph, 12)
        self.assertEqual(snapshot.weather_code, 61)
        # The observation time is what the freshness window is measured against, so a
        # provider that omits it must fail rather than be stamped with "now".
        self.assertEqual(snapshot.observed_at, datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc))

    def test_missing_optional_fields_stay_absent_instead_of_becoming_zero(self):
        snapshot = normalize_qweather_now({"code": "200",
            "now": {"temp": 22, "text": "晴", "icon": "100", "obsTime": "2026-09-28T17:00+08:00"}},
            LOCATION)
        self.assertIsNone(snapshot.humidity_pct)
        self.assertIsNone(snapshot.wind_kph)
        self.assertEqual(snapshot.weather_code, 0)

    def test_bad_bodies_are_rejected(self):
        for payload in (
            {"code": "404", "now": body()["now"]},          # provider-reported failure
            {"code": "200"},                                 # no observation
            {"code": "200", "now": {**body()["now"], "temp": None}},
            {"code": "200", "now": {**body()["now"], "obsTime": ""}},
            {"code": "200", "now": {**body()["now"], "obsTime": "2026-09-28T17:00"}},  # naive
            {"code": "200", "now": {**body()["now"], "obsTime": "not-a-time"}},
            [],
            None,
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    normalize_qweather_now(payload, LOCATION)

    def test_blank_condition_text_still_yields_an_observation(self):
        """The sky is known from the icon; a missing label must not discard the reading."""
        snapshot = normalize_qweather_now({"code": "200", "now": {**body()["now"], "text": "  "}},
                                          LOCATION)
        self.assertEqual(snapshot.condition, "小雨")
        self.assertEqual(snapshot.weather_code, 61)

    def test_unknown_icon_is_never_published_as_clear_sky(self):
        # The provider's text is still a real label, but no code is claimed, so the
        # weather-change detector cannot see "晴" where nothing is actually known.
        snapshot = normalize_qweather_now({"code": "200",
            "now": {**body()["now"], "icon": "999", "text": "未知"}}, LOCATION)
        self.assertIsNone(snapshot.weather_code)
        self.assertEqual(snapshot.condition, "未知")

    def test_icons_that_are_not_sky_states_fail_closed_without_a_label(self):
        # Haze/dust have no label-bearing WMO code; 900/901/999 are not sky states.
        for icon in ("999", "900", "901", "502", "503", "504", "507", "508"):
            with self.subTest(icon=icon):
                with self.assertRaises(ValueError):
                    normalize_qweather_now({"code": "200",
                        "now": {**body()["now"], "icon": icon, "text": " "}}, LOCATION)

    def test_observation_with_neither_label_nor_usable_icon_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_qweather_now({"code": "200",
                "now": {"temp": 22, "text": "", "icon": "1234", "obsTime": "2026-09-28T17:00+08:00"}},
                LOCATION)


class QWeatherWeatherPortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.pem = self.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    def _port(self, **kwargs):
        fields = {"api_host": "example.xy.qweatherapi.com", "credential_id": "KID123",
                  "developer_id": "Q123456789", "project_id": "PROJECT123",
                  "private_key_pem": self.pem, **kwargs}
        return QWeatherWeatherPort(QWeatherCredentials(**fields), now=lambda: NOW)

    async def _fetch(self, port, payload):
        calls = []

        class Content:
            async def read(self, limit):
                calls.append(("limit", limit))
                return json.dumps(payload).encode("utf-8")

        class Reply:
            status = 200
            content_length = None
            content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

        class Session:
            def __init__(self, *, timeout, trust_env):
                calls.append(("options", timeout.total, trust_env))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, url, *, params, headers, allow_redirects):
                calls.append(("request", url, params, headers, allow_redirects))
                return Reply()

        with patch("astrbot_host.qweather_weather.aiohttp.ClientSession", Session):
            snapshot = await port.fetch(LOCATION)
        return snapshot, calls

    async def test_location_uses_longitude_first_query_parameter_and_bounded_body(self):
        snapshot, calls = await self._fetch(self._port(), body())
        request = next(item for item in calls if item[0] == "request")
        self.assertEqual(request[1], "https://example.xy.qweatherapi.com/v7/weather/now")
        self.assertEqual(request[2], {"location": "121.47,31.23", "lang": "zh"})
        self.assertFalse(request[4])
        self.assertEqual(next(item for item in calls if item[0] == "options")[1:],
                         (TOTAL_TIMEOUT_SECONDS, False))
        self.assertEqual(next(item for item in calls if item[0] == "limit")[1], 65537)
        self.assertEqual(snapshot.weather_code, 61)

    async def test_api_key_header_is_mutually_exclusive_with_jwt(self):
        port = QWeatherWeatherPort(QWeatherCredentials(
            api_host="example.xy.qweatherapi.com", api_key=API_KEY), now=lambda: NOW)
        _, calls = await self._fetch(port, body())
        request = next(item for item in calls if item[0] == "request")
        self.assertEqual(request[3], {"X-QW-Api-Key": API_KEY})

    async def test_non_qweather_destination_is_refused(self):
        with self.assertRaises(ValueError):
            self._port(api_host="localhost")

    async def test_fetch_requires_an_explicit_role_location(self):
        with self.assertRaises(ValueError):
            await self._port().fetch("31.23,121.47")


class FetchBudgetTests(unittest.IsolatedAsyncioTestCase):
    """One total budget covers the geo lookup and the observation together."""

    async def test_slow_geo_lookup_does_not_start_a_fresh_timeout_for_the_observation(self):
        completed = []
        geocoded = {"code": "200",
                    "location": [{"name": "角色所在地", "lat": "31.23", "lon": "121.47"}]}

        class SlowReply:
            status = 200
            content_length = None

            def __init__(self, payload, index):
                self.raw = json.dumps(payload).encode("utf-8")
                self.content = self
                self.index = index

            async def __aenter__(self):
                await asyncio.sleep(0.05 if self.index == 0 else 0.4)
                return self

            async def __aexit__(self, *_):
                return None

            async def read(self, limit):
                completed.append(self.index)
                return self.raw[:limit]

        class Session:
            def __init__(self, *, timeout, trust_env):
                self.replies = [SlowReply(geocoded, 0), SlowReply(body(), 1)]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, url, **kwargs):
                return self.replies.pop(0)

        port = QWeatherWeatherPort(QWeatherCredentials(
            api_host="example.xy.qweatherapi.com", api_key=API_KEY), now=lambda: NOW)
        location = WeatherLocation("角色所在地", None, None)
        with patch("astrbot_host.qweather_weather.aiohttp.ClientSession", Session), patch(
                "astrbot_host.qweather_weather.TOTAL_TIMEOUT_SECONDS", 0.2, create=True):
            with self.assertRaisesRegex(ValueError, "timed out"):
                await port.fetch(location)

        # The lookup fitted inside the budget; the observation got the remainder, not
        # another full timeout, so it was cancelled and never completed.
        self.assertEqual(completed, [0])

    def test_the_adapter_budget_fits_inside_the_callers_weather_window(self):
        # companion_v2/agenda/weather_reading.py waits 6s for the whole weather read.
        self.assertLess(TOTAL_TIMEOUT_SECONDS, 6)


class PortSelectionTests(unittest.TestCase):
    def test_no_credentials_keeps_the_zero_configuration_source(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(configured_qweather_weather_port(), OpenMeteoWeatherPort)

    def test_credentials_select_qweather_as_the_default(self):
        for prefix in ("QWEATHER_", "QWEATHER_ALERT_"):
            with self.subTest(prefix=prefix), patch.dict(os.environ, {
                    prefix + "API_HOST": "example.xy.qweatherapi.com",
                    prefix + "API_KEY": API_KEY}, clear=True):
                self.assertIsInstance(configured_qweather_weather_port(), QWeatherWeatherPort)

    def test_a_partial_credential_set_fails_loudly(self):
        """Falling back to Open-Meteo here would silently publish the wrong location."""
        with patch.dict(os.environ, {"QWEATHER_API_HOST": "example.xy.qweatherapi.com"}, clear=True):
            with self.assertRaises(ValueError):
                configured_qweather_weather_port()


class ConfigSignedJwtTests(unittest.TestCase):
    def test_private_key_config_mints_kid_and_sub_without_a_manual_token(self):
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
        credentials = credentials_from_environment(label="weather", config={"weather": {
            "api_host": "example.xy.qweatherapi.com",
            "jwt_credential_id": "KID123", "jwt_project_id": "PROJECT123",
            "jwt_developer_id": "Q123456789", "jwt_private_key": pem,
        }})
        token = credentials.headers(NOW)["Authorization"].removeprefix("Bearer ")
        header, payload, signature = token.split(".")
        decode = lambda part: json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        self.assertEqual(decode(header), {"alg": "EdDSA", "kid": "KID123"})
        claims = decode(payload)
        self.assertEqual(claims["sub"], "PROJECT123")
        self.assertEqual(claims["iss"], "Q123456789")
        self.assertEqual(claims["iat"], int(NOW.timestamp()) - 30)
        self.assertEqual(claims["exp"], int(NOW.timestamp()) + 870)
        self.assertTrue(signature)


if __name__ == "__main__":
    unittest.main()
