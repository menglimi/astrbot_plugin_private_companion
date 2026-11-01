"""Official-alert source contract with synthetic QWeather responses only."""

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from astrbot_host.qweather_alerts import QWeatherAlertPort, configured_qweather_alert_port
from astrbot_host.qweather_credentials import QWeatherCredentials
from companion_v2.agenda.planning_config import WeatherLocation
from companion_v2.proactive.weather_alerts import active_official_alerts, normalize_qweather_alerts


NOW = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)
LOCATION = WeatherLocation("角色所在地", 31.23, 121.47)
ATTRIBUTIONS = ["https://developer.qweather.com/attribution.html", "当前预警数据可能存在延迟，以官方发布为准。"]


def alert(identifier="first", *, kind="alert", supersedes=(), effective=None, expires=None):
    return {"id": identifier, "senderName": "当地气象台", "issuedTime": (NOW - timedelta(minutes=5)).isoformat(),
            "messageType": {"code": kind, "supersedes": list(supersedes)}, "eventType": {"name": "大风", "code": "1006"},
            "severity": "moderate", "effectiveTime": (effective or NOW - timedelta(minutes=5)).isoformat(),
            "expireTime": (expires or NOW + timedelta(hours=1)).isoformat(),
            "headline": "大风预警", "description": "请注意防范", "instruction": "留意官方更新"}


def response(*alerts):
    return {"metadata": {"zeroResult": not alerts, "attributions": ATTRIBUTIONS}, "alerts": list(alerts)}


class AlertContractTests(unittest.TestCase):
    def test_v1_current_response_shape_normalizes_known_severity_casing(self):
        row = {**alert(), "severity": "Moderate", "color": {"code": "orange"}}
        payload = {"code": "200", "metadata": {"tag": "tag-1", "zeroResult": False,
                   "attributions": ATTRIBUTIONS}, "alerts": [row]}
        observation = normalize_qweather_alerts(payload, LOCATION, NOW)
        self.assertEqual(observation.alerts[0].severity, "moderate")
        self.assertEqual(observation.attributions, tuple(ATTRIBUTIONS))

    def test_v1_cached_snapshot_is_not_a_fresh_official_response(self):
        cached = {"alerts": [], "attributions": [], "zero_result": True,
                  "fetched_ts": NOW.timestamp(), "source": "qweather"}
        with self.assertRaises(ValueError):
            normalize_qweather_alerts(cached, LOCATION, NOW)

    def test_live_zero_result_can_omit_attributions(self):
        payload = {"metadata": {"tag": "empty", "zeroResult": True}, "alerts": []}
        observation = normalize_qweather_alerts(payload, LOCATION, NOW)
        self.assertEqual((observation.alerts, observation.attributions), ((), ()))

    def test_update_cancel_expiry_location_and_attribution(self):
        observation = normalize_qweather_alerts(response(
            alert(), alert("update", kind="update", supersedes=("first",)),
            alert("canceled", kind="cancel", supersedes=("update",)),
            alert("expired", expires=NOW - timedelta(seconds=1)),
            alert("live")), LOCATION, NOW)
        self.assertEqual(observation.attributions, tuple(ATTRIBUTIONS))
        self.assertEqual([item.identifier for item in active_official_alerts(observation, LOCATION, NOW)], ["live"])
        self.assertEqual(active_official_alerts(observation, WeatherLocation("新地点", 31.23, 121.47), NOW), ())
        self.assertEqual(active_official_alerts(observation, LOCATION, NOW + timedelta(minutes=11)), ())

    def test_unknown_or_inconsistent_response_fails_closed(self):
        for payload in (
            {"metadata": {"zeroResult": True, "attributions": ATTRIBUTIONS}, "alerts": [alert()]},
            {"metadata": {"zeroResult": False}, "alerts": [alert()]},
            {"metadata": {"zeroResult": False, "attributions": []}, "alerts": [alert()]},
            response(alert("same"), alert("same")),
            response({**alert(), "messageType": {"code": "invented", "supersedes": []}}),
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_qweather_alerts(payload, LOCATION, NOW)


class QWeatherPortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        private_pem = self.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self.port = QWeatherAlertPort(QWeatherCredentials(api_host="example.xy.qweatherapi.com",
            credential_id="KID123", developer_id="Q123456789", project_id="PROJECT123",
            private_key_pem=private_pem), now=lambda: NOW)

    async def test_fixed_target_signed_short_lived_jwt_and_bounded_response(self):
        calls = []

        class Content:
            async def read(self, limit):
                calls.append(("limit", limit))
                return json.dumps(response(alert())).encode("utf-8")

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
                self.assert_options = (timeout.total, trust_env)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, url, *, headers, allow_redirects):
                calls.append((url, headers, allow_redirects, self.assert_options))
                return Reply()

        with patch("astrbot_host.qweather_alerts.aiohttp.ClientSession", Session):
            observation = await self.port.fetch(LOCATION)
        url, headers, redirects, options = calls[0]
        self.assertEqual(url, "https://example.xy.qweatherapi.com/weatheralert/v1/current/31.23/121.47")
        self.assertEqual((redirects, options), (False, (6, False)))
        token = headers["Authorization"].removeprefix("Bearer ")
        header, payload, signature = token.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.key.public_key().verify(decode(signature), (header + "." + payload).encode("ascii"))
        self.assertEqual(json.loads(decode(header)), {"alg": "EdDSA", "kid": "KID123"})
        self.assertEqual(json.loads(decode(payload)), {"iss": "Q123456789", "sub": "PROJECT123",
            "iat": int(NOW.timestamp()) - 30, "exp": int(NOW.timestamp()) + 870})
        self.assertEqual(observation.attributions, tuple(ATTRIBUTIONS))

    async def test_name_only_location_keeps_the_configured_location_in_its_observation(self):
        """A geocoded query point must not replace the configured role location.

        A location may be configured by name alone. Resolving it is an addressing
        detail of the request; if the resolved coordinates became the observation's
        location, the downstream guards would compare it with the configured one
        field by field, never match, and drop every official alert.
        """
        location = WeatherLocation("角色所在地", None, None, "测试市", "测试区域")
        geocoded = {"code": "200", "location": [{"name": "角色所在地", "lat": "31.23", "lon": "121.47"}]}
        calls = []

        def reply(raw):
            class Content:
                async def read(self, limit):
                    return raw

            class Reply:
                status = 200
                content_length = None
                content = Content()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    return None

            return Reply()

        class Session:
            def __init__(self, *, timeout, trust_env):
                self.pending = [reply(json.dumps(geocoded).encode("utf-8")),
                                reply(json.dumps(response(alert())).encode("utf-8"))]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, url, *, headers, allow_redirects, params=None):
                calls.append((url, params))
                return self.pending.pop(0)

        with patch("astrbot_host.qweather_alerts.aiohttp.ClientSession", Session):
            observation = await self.port.fetch(location)

        self.assertEqual(calls[0][1]["location"], "角色所在地")
        self.assertEqual(calls[1][0],
                         "https://example.xy.qweatherapi.com/weatheralert/v1/current/31.23/121.47")
        self.assertEqual(observation.location, location)
        self.assertEqual((observation.location.latitude, observation.location.longitude), (None, None))
        self.assertEqual(len(active_official_alerts(observation, location, NOW)), 1)

    async def test_host_rejects_non_qweather_destination(self):
        with self.assertRaises(ValueError):
            QWeatherCredentials(api_host="localhost", credential_id="KID123",
                developer_id="Q123456789", project_id="PROJECT123", private_key_pem=b"invalid")

    async def test_api_key_uses_exclusive_header_and_normalizes_zero_result(self):
        calls = []

        class Content:
            async def read(self, limit):
                calls.append(("limit", limit))
                return json.dumps({"metadata": {"zeroResult": True}, "alerts": []}).encode("utf-8")

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
                self.options = (timeout.total, trust_env)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            def get(self, url, *, headers, allow_redirects):
                calls.append((url, headers, allow_redirects, self.options))
                return Reply()

        api_key = "0123456789abcdef0123456789abcdef"
        port = QWeatherAlertPort(QWeatherCredentials(api_host="example.xy.qweatherapi.com",
            api_key=api_key), now=lambda: NOW)
        with patch("astrbot_host.qweather_alerts.aiohttp.ClientSession", Session):
            observation = await port.fetch(LOCATION)
        url, headers, redirects, options = calls[0]
        self.assertEqual(url, "https://example.xy.qweatherapi.com/weatheralert/v1/current/31.23/121.47")
        self.assertEqual(headers, {"X-QW-Api-Key": api_key})
        self.assertEqual((redirects, options), (False, (6, False)))
        self.assertEqual(observation.alerts, ())
        self.assertEqual(observation.attributions, ())

    def test_host_environment_rejects_partial_mixed_and_invalid_auth(self):
        api_key = "0123456789abcdef0123456789abcdef"
        host = "example.xy.qweatherapi.com"
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_qweather_alert_port())
        valid = {"QWEATHER_ALERT_API_HOST": host, "QWEATHER_ALERT_API_KEY": api_key}
        with patch.dict(os.environ, valid, clear=True):
            self.assertIsInstance(configured_qweather_alert_port(), QWeatherAlertPort)
        for values in (
            {"QWEATHER_ALERT_API_KEY": api_key},
            {"QWEATHER_ALERT_API_HOST": host},
            {**valid, "QWEATHER_ALERT_PROJECT_ID": "PROJECT123"},
            {"QWEATHER_ALERT_API_HOST": host, "QWEATHER_ALERT_API_KEY": "not-a-key"},
            {"QWEATHER_ALERT_API_HOST": host, "QWEATHER_ALERT_PROJECT_ID": "PROJECT123"},
        ):
            with self.subTest(fields=tuple(values)), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError):
                    configured_qweather_alert_port()

    def test_neutral_environment_names_win_over_the_alert_only_generation(self):
        """An installation that migrates to the neutral names must not be ignored."""
        old_key = "0123456789abcdef0123456789abcdef"
        new_key = "fedcba9876543210fedcba9876543210"
        # The older names alone keep working, so upgrading the plugin changes nothing.
        with patch.dict(os.environ, {"QWEATHER_ALERT_API_HOST": "old.xy.qweatherapi.com",
                                     "QWEATHER_ALERT_API_KEY": old_key}, clear=True):
            self.assertIsInstance(configured_qweather_alert_port(), QWeatherAlertPort)
        # A complete neutral set takes over as a set, never field by field.
        with patch.dict(os.environ, {"QWEATHER_ALERT_API_HOST": "old.xy.qweatherapi.com",
                                     "QWEATHER_ALERT_API_KEY": old_key,
                                     "QWEATHER_API_HOST": "new.xy.qweatherapi.com",
                                     "QWEATHER_API_KEY": new_key}, clear=True):
            port = configured_qweather_alert_port()
        self.assertEqual(port._credentials.host, "new.xy.qweatherapi.com")
        # A neutral set that is merely partial must not half-adopt the older values.
        with patch.dict(os.environ, {"QWEATHER_ALERT_API_HOST": "old.xy.qweatherapi.com",
                                     "QWEATHER_ALERT_API_KEY": old_key,
                                     "QWEATHER_API_KEY": new_key}, clear=True):
            with self.assertRaises(ValueError):
                configured_qweather_alert_port()


if __name__ == "__main__":
    unittest.main()
