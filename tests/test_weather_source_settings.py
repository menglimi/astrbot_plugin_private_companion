"""Host weather source saves, live application and bounded cache behavior."""

import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from tests import test_host_options_api as host_fixture
from astrbot_host.weather_settings import CachedWeatherPort, configured_weather_ports
from companion_v2.agenda.planning_config import WeatherLocation
from companion_v2.agenda.weather_tools import WeatherTools


KEY = "0123456789abcdef0123456789abcdef"
ROTATED_KEY = "abcdef0123456789abcdef0123456789"
HOST = "example.re.qweatherapi.com"


class WeatherSourceSettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        host_fixture.HostOptionsWebAPITests.setUp(self)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.application = NS(calendar_planning=NS(weather_port=None), contact_schedule=NS(**{
            name: NS(port=None, retry_at={"old": 1}) for name in
            ("weather_events", "weather_change_events", "official_alert_events")}))
        self.controller.session = NS(application=self.application)

    call = host_fixture.HostOptionsWebAPITests.call

    async def source(self):
        response = await self.call("weather_status")
        self.assertEqual(response["status"], "ok", response)
        return response["data"]

    async def save(self, changes):
        current = await self.source()
        return await self.call("weather", {"edit_token": current["edit_token"], "changes": changes})

    async def test_source_credentials_save_without_readback_and_apply_to_all_consumers(self):
        response = await self.save({"provider": "qweather", "api_host": "https://" + HOST + "/",
                                    "credential": KEY, "refresh_minutes": 30})
        self.assertEqual(response["status"], "ok", response)
        self.assertNotIn(KEY, json.dumps(response))
        self.assertEqual(self.config["weather"]["credential"], KEY)
        self.assertEqual(self.config["weather"]["api_host"], HOST)
        self.assertEqual(response["data"]["actual_provider"], "qweather")
        self.assertTrue(response["data"]["alerts_ready"])
        weather = self.application.calendar_planning.weather_port
        for name in ("weather_events", "weather_change_events"):
            self.assertIs(getattr(self.application.contact_schedule, name).port, weather)
            self.assertEqual(getattr(self.application.contact_schedule, name).retry_at, {})
        self.assertEqual(weather.refresh_minutes, 30)
        # Rebuilding from the saved configuration exercises the restart path.
        restored, alert = configured_weather_ports(self.config)
        self.assertEqual(restored.port._credentials.host, HOST)
        self.assertEqual(alert._credentials.host, HOST)
        self.assertNotIn(KEY, json.dumps(await self.source()))

    async def test_model_weather_tool_follows_source_save_rotation_and_clear(self):
        # 装配时 LocalRoleTools 与规划页拿到同一个端口，且 WeatherTools 把它冻在
        # 实例上（conversation/tool_loop.py、agenda/weather_tools.py）。保存后必须
        # 一起换成新端口，否则 weather.current 仍走旧数据源与旧凭据。
        previous, _alerts = configured_weather_ports(self.config)
        previous.cache[WeatherLocation("旧地点", 31.2, 121.4)] = (0, object())
        self.application.tools = NS(weather=WeatherTools(previous, None))
        tool = self.application.tools.weather
        self.assertIs(tool.port, previous)

        response = await self.save({"provider": "qweather", "api_host": HOST, "credential": KEY})
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["apply_mode"], "immediate")
        self.assertIs(tool.port, self.application.calendar_planning.weather_port)
        self.assertIsNot(tool.port, previous)
        self.assertEqual(tool.port.provider, "qweather")
        self.assertEqual(tool.port.port._credentials.host, HOST)
        self.assertEqual(tool.port.cache, {})
        self.assertEqual(previous.cache, {})

        # 轮换凭据后工具请求头里是新 Key，而不是构造时那一份。
        response = await self.save({"credential": ROTATED_KEY})
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(tool.port.port._credentials.headers(datetime.now(timezone.utc)),
                         {"X-QW-Api-Key": ROTATED_KEY})
        self.assertIs(tool.port, self.application.calendar_planning.weather_port)

        # 清除页面凭据后工具不再持有已删除的 Key，并随数据源退回 Open-Meteo。
        cleared = await self.save({"clear_credentials": True})
        self.assertEqual(cleared["status"], "ok", cleared)
        self.assertEqual(cleared["data"]["credential_source"], "none")
        self.assertIs(tool.port, self.application.calendar_planning.weather_port)
        self.assertEqual(tool.port.provider, "open-meteo")
        self.assertIsNone(getattr(tool.port.port, "_credentials", None))

    async def test_blank_credential_retains_secret_and_source_change_replaces_cache(self):
        await self.save({"api_host": HOST, "credential": KEY})
        previous = self.application.calendar_planning.weather_port
        previous.cache["old"] = (0, object())
        response = await self.save({"credential": "", "refresh_minutes": 10})
        self.assertEqual(response["status"], "ok", response)
        current = self.application.calendar_planning.weather_port
        self.assertIsNot(current, previous)
        self.assertEqual(current.cache, {})
        self.assertEqual(self.config["weather"]["credential"], KEY)
        response = await self.save({"provider": "open-meteo"})
        self.assertEqual(response["data"]["actual_provider"], "open-meteo")
        self.assertTrue(response["data"]["alerts_ready"])

    async def test_jwt_token_uses_bearer_header_and_clear_restores_environment(self):
        from datetime import datetime, timezone
        token = "eyJhbGciOiJFZERTQSJ9.eyJleHAiOjQxMDI0NDQ4MDB9.fixture_signature"
        response = await self.save({"api_host": HOST, "credential": token})
        self.assertEqual(response["status"], "ok", response)
        self.assertNotIn(token, json.dumps(response))
        credential = self.application.calendar_planning.weather_port.port._credentials
        self.assertEqual(credential.headers(datetime.now(timezone.utc)), {"Authorization": "Bearer " + token})
        with patch.dict(os.environ, {"QWEATHER_API_HOST": "env.re.qweatherapi.com", "QWEATHER_API_KEY": KEY}):
            cleared = await self.save({"clear_credentials": True})
            self.assertEqual(cleared["status"], "ok", cleared)
            self.assertEqual(cleared["data"]["credential_source"], "environment")
            self.assertEqual(self.config["weather"]["credential"], "")

    async def test_private_key_fields_save_without_readback_and_mint_short_lived_jwt(self):
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
        response = await self.save({"provider": "qweather", "api_host": HOST,
            "jwt_credential_id": "KID123", "jwt_project_id": "PROJECT123",
            "jwt_developer_id": "Q123456789", "jwt_private_key": pem})
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(response["data"]["credential_kind"], "signed_jwt")
        self.assertNotIn(pem, json.dumps(response))
        self.assertNotIn("PRIVATE KEY", json.dumps(response))
        credential = self.application.calendar_planning.weather_port.port._credentials
        token = credential.headers(datetime.now(timezone.utc))["Authorization"].removeprefix("Bearer ")
        header, payload, _ = token.split(".")
        decode = lambda part: json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        self.assertEqual(decode(header)["kid"], "KID123")
        self.assertEqual(decode(payload)["sub"], "PROJECT123")
        self.assertEqual(decode(payload)["iss"], "Q123456789")
        restored, _ = configured_weather_ports(self.config)
        self.assertEqual(restored.port._credentials._credential_id, "KID123")

    async def test_private_key_and_token_fields_are_mutually_exclusive(self):
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode("ascii")
        response = await self.save({"api_host": HOST, "credential": KEY,
            "jwt_credential_id": "KID123", "jwt_project_id": "PROJECT123",
            "jwt_private_key": pem})
        self.assertEqual(response["message"], "weather_source_invalid")
        self.assertEqual(self.saved, [])

    async def test_invalid_fields_cannot_save_or_replace_live_ports(self):
        for changes in ({"api_host": "localhost", "credential": KEY},
                        {"api_host": "https://" + HOST + "/v7/weather/now", "credential": KEY},
                        {"api_host": HOST, "credential": "Bearer bad.token.value"},
                        {"provider": "qweather"}, {"refresh_minutes": True},
                        {"refresh_minutes": 0}, {"refresh_minutes": 121}, {"provider": "unlisted"}):
            with self.subTest(changes=tuple(changes)):
                response = await self.save(changes)
                self.assertEqual(response["message"], "weather_source_invalid", response)
        unknown = await self.save({"reply_probability": 1})
        self.assertEqual(unknown["message"], "host_options_request_invalid")
        self.assertEqual(self.saved, [])
        self.assertIsNone(self.application.calendar_planning.weather_port)

    async def test_environment_host_edit_requires_matching_credential(self):
        with patch.dict(os.environ, {"QWEATHER_API_HOST": HOST, "QWEATHER_API_KEY": KEY}):
            response = await self.save({"refresh_minutes": 15})
            self.assertEqual(response["status"], "ok", response)
            self.assertEqual(response["data"]["credential_source"], "environment")
            response = await self.save({"api_host": "new.re.qweatherapi.com"})
            self.assertEqual(response["message"], "weather_source_credential_required")
            self.config["weather"].update(api_host="", credential="")
            response = await self.save({"credential": KEY})
            self.assertEqual(response["status"], "ok", response)
            self.assertEqual(self.config["weather"]["api_host"], HOST)

    async def test_conflict_and_failed_persistence_leave_original_ports(self):
        current = await self.source()
        await self.save({"refresh_minutes": 15})
        previous = self.application.calendar_planning.weather_port
        response = await self.call("weather", {"edit_token": current["edit_token"], "changes": {"refresh_minutes": 20}})
        self.assertEqual(response["message"], "host_options_conflict")
        with patch.object(self.controller, "save_plugin_config", side_effect=OSError("fixture write failure")):
            response = await self.save({"refresh_minutes": 20})
        self.assertEqual(response["message"], "host_options_save_failed")
        self.assertIs(self.application.calendar_planning.weather_port, previous)
        self.assertEqual(self.config["weather"]["refresh_minutes"], 15)

    async def test_api_key_identity_cannot_write_host_credentials(self):
        current = await self.source()
        response = await self.call("weather", {"edit_token": current["edit_token"],
                                   "changes": {"api_host": HOST, "credential": KEY}}, username="api_key:fixture")
        self.assertEqual(response["message"], "dashboard_identity_required")
        self.assertEqual(self.saved, [])


class WeatherSourceCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_reads_share_cache_then_expire_or_refresh(self):
        location = WeatherLocation("合成地点", 31.2, 121.4)
        clock = [0]
        source = NS(provider="synthetic", fetch=AsyncMock(return_value=object()))
        port = CachedWeatherPort(source, 10, clock=lambda: clock[0])
        first, second = await asyncio.gather(port.fetch(location), port.fetch(location))
        self.assertIs(first, second)
        self.assertEqual(source.fetch.await_count, 1)
        clock[0] = 599
        await port.fetch(location)
        self.assertEqual(source.fetch.await_count, 1)
        clock[0] = 600
        await port.fetch(location)
        self.assertEqual(source.fetch.await_count, 2)
        await port.refresh(location)
        self.assertEqual(source.fetch.await_count, 3)

    async def test_failed_read_does_not_cache_failure(self):
        source = NS(provider="synthetic", fetch=AsyncMock(side_effect=[ValueError("fixture failure"), object()]))
        port = CachedWeatherPort(source, 10)
        with self.assertRaises(ValueError):
            await port.fetch("location")
        await port.fetch("location")
        self.assertEqual(source.fetch.await_count, 2)
        self.assertEqual(port.pending, {})


if __name__ == "__main__":
    unittest.main()
