"""Host integration tests for PR 9b: QuotaWatchService, QuotaWebEndpoints, MultiStationBalancePort, and backup redaction."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.model_api import ModelWebAPI
from astrbot_host.quota_service import (
    QuotaWatchService,
    close_quota_watch_services,
    get_quota_watch_service,
    project_legacy_service_balance,
)
from astrbot_host.service_balance import (
    MultiStationBalancePort,
    configured_balance_port,
    propagate_balance_port,
)
from companion_v2.model_gateway.pricing import PricingSettingsStore
from companion_v2.model_gateway.quota import QuotaStationStore
from companion_v2.storage.backup import LEGACY_PRESERVES, OWNED_ROOT_DIRECTORIES, SECRET_KEYS
from tests.test_quota_domain_and_adapters import PNG_BYTES, _FakeResponse, _mock_factory


class _FakeQuery(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


class _FakeRequest:
    def __init__(self, *, username: str = "admin", body: dict | bytes | None = None, query: dict | None = None):
        self.username = username
        self.query = _FakeQuery(query or {})
        if isinstance(body, dict):
            self._body = json.dumps(body).encode("utf-8")
        elif isinstance(body, bytes):
            self._body = body
        else:
            self._body = b""

    async def body(self):
        return self._body


class _FakeBalanceEvents:
    def __init__(self):
        self.port = None
        self.updates = []

    def update_port(self, port):
        self.port = port
        self.updates.append(port)


class _FakeKeywordMaterials:
    def __init__(self):
        self.invalidated = []

    def invalidate(self, owner):
        self.invalidated.append(owner)


def _make_controller(tmp_dir: str, *, config: dict | None = None, providers: list | None = None):
    sources = providers or [
        {
            "id": "deepseek_official",
            "name": "DeepSeek 官方",
            "provider_type": "chat_completion",
            "enable": True,
            "api_base": "https://api.deepseek.com/v1",
            "key": ["secret-ds-provider-key-999"],
            "proxy": "",
        },
        {
            "id": "openrouter_official",
            "name": "OpenRouter",
            "provider_type": "chat_completion",
            "enable": True,
            "api_base": "https://openrouter.ai/api/v1",
            "key": ["secret-or-provider-key-888"],
            "proxy": "",
        },
    ]
    models = [
        {"id": "ds-chat", "provider_source_id": "deepseek_official", "provider_type": "chat_completion", "enable": True, "model": "deepseek-chat"},
        {"id": "or-claude", "provider_source_id": "openrouter_official", "provider_type": "chat_completion", "enable": True, "model": "claude-sonnet"},
    ]
    context = NS(
        provider_manager=NS(provider_sources_config=sources, providers_config=models),
        get_config=lambda: {"provider_sources": sources, "provider": models},
        get_all_providers=lambda: [],
    )
    runtime = NS(data_root=Path(tmp_dir), installation_id="inst-test")
    app = NS(
        runtime=runtime,
        balance_port=None,
        keyword_materials=_FakeKeywordMaterials(),
        contact_schedule=NS(balance_events=_FakeBalanceEvents()),
        router=NS(providers={}),
    )
    session = NS(application=app, profile=NS(target=None))
    return NS(
        _terminated=False,
        _control_lock=asyncio.Lock(),
        config=config if config is not None else {},
        context=context,
        runtime=runtime,
        data_root=Path(tmp_dir),
        session=session,
        last_session=session,
        now=lambda: datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc),
    )


class QuotaServiceAndBalanceBridgeTests(unittest.TestCase):
    def test_legacy_service_balance_projection_for_newapi_and_script(self):
        legacy_newapi = project_legacy_service_balance({
            "service_balance": {
                "url": "https://old-relay.example.test",
                "api_key": "nap_legacy_tok",
                "user_id": "101",
                "value_divisor": "500000",
            }
        })
        self.assertIsNotNone(legacy_newapi)
        self.assertEqual(legacy_newapi.id, "legacy-default")
        self.assertEqual(legacy_newapi.adapter_id, "newapi_user_id")
        self.assertEqual(legacy_newapi.user_id, "101")
        self.assertTrue(legacy_newapi.participate_in_awareness)

        legacy_script = project_legacy_service_balance({
            "service_balance": {
                "advanced_enabled": True,
                "advanced_from_astrbot": True,
                "advanced_provider_id": "deepseek_official",
                "advanced_script": "({request:{url:'{{baseUrl}}/b'},extractor:r=>({remaining:1})})",
            }
        })
        self.assertIsNotNone(legacy_script)
        self.assertEqual(legacy_script.adapter_id, "custom_script")
        self.assertEqual(legacy_script.source_mode, "astrbot_provider")
        self.assertEqual(legacy_script.astrbot_provider_id, "deepseek_official")

    def test_single_flight_and_debounce_and_close_cleanup(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                ctrl = _make_controller(tmp)
                store = QuotaStationStore(Path(tmp))
                _, _, tok = store.read()
                store.upsert_station(
                    tok,
                    {
                        "id": "ds",
                        "name": "DeepSeek",
                        "adapter_id": "deepseek",
                        "source_mode": "astrbot_provider",
                        "astrbot_provider_id": "deepseek_official",
                        "participate_in_awareness": True,
                    },
                )

                hit_count = [0]

                def routes(method, url, _kw):
                    if url.endswith("/user/balance"):
                        hit_count[0] += 1
                        return _FakeResponse(
                            200,
                            {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "88.00"}]},
                        )
                    return _FakeResponse(404, b"")

                factory, _ = _mock_factory(routes)
                service = QuotaWatchService(Path(tmp), ctrl, session_factory=factory, manual_cooldown_seconds=60.0)
                ctrl._quota_watch_services = {str(Path(tmp).resolve()): service}

                # Concurrent refresh_all calls collapse into single-flight (1 upstream balance call)
                r1, r2 = await asyncio.gather(service.refresh_all(force=True), service.refresh_all(force=True))
                self.assertEqual(hit_count[0], 1)
                self.assertTrue(r1["ds"].ok)
                self.assertAlmostEqual(r2["ds"].remaining, 88.0)

                # Debounced call (force=False) within cooldown does not re-hit upstream
                await service.refresh_all(force=False)
                self.assertEqual(hit_count[0], 1)

                # Shutdown cleans registry cleanly
                await close_quota_watch_services(ctrl)
                self.assertEqual(ctrl._quota_watch_services, {})

        asyncio.run(run())

    def test_multi_station_balance_port_all_three_modes_and_multi_role_propagation(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                ctrl = _make_controller(tmp)
                # Set deterministic FX rate = 7.20
                pstore = PricingSettingsStore(Path(tmp))
                pdoc = pstore.read()
                pstore.update(pdoc["revision"], {"display_currency": "CNY", "exchange_rate": {"mode": "manual", "manual_usd_cny": 7.2}})

                store = QuotaStationStore(Path(tmp))
                _, _, tok = store.read()
                _, _, _, tok = store.upsert_station(
                    tok,
                    {
                        "id": "ds",
                        "name": "DeepSeek 站",
                        "adapter_id": "deepseek",
                        "source_mode": "astrbot_provider",
                        "astrbot_provider_id": "deepseek_official",
                        "participate_in_awareness": True,
                    },
                )
                _, _, _, tok = store.upsert_station(
                    tok,
                    {
                        "id": "or",
                        "name": "OpenRouter 站",
                        "adapter_id": "openrouter",
                        "source_mode": "astrbot_provider",
                        "astrbot_provider_id": "openrouter_official",
                        "low_threshold": 5.0,
                        "critical_threshold": 2.0,
                        "participate_in_awareness": True,
                    },
                )

                factory, _ = _mock_factory({
                    ("GET", "https://api.deepseek.com/user/balance"): _FakeResponse(
                        200, {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "100.00"}]}
                    ),
                    ("GET", "https://openrouter.ai/api/v1/credits"): _FakeResponse(
                        200, {"data": {"total_credits": 20.0, "total_usage": 18.5}}
                    ),
                })
                service = get_quota_watch_service(ctrl, Path(tmp), session_factory=factory)
                self.assertIsNotNone(service)

                # Mode 1: primary (ds)
                _, _, tok = store.update_awareness(tok, {"mode": "primary", "primary_station_id": "ds"})
                port = configured_balance_port(ctrl.config, context=ctrl.context, runtime_root=Path(tmp), controller=ctrl)
                self.assertIsInstance(port, MultiStationBalancePort)
                obs_primary = await port.fetch()
                self.assertEqual(obs_primary.balance, Decimal("100.0"))
                self.assertEqual(obs_primary.unit, "CNY")
                self.assertEqual(obs_primary.plan_name, "DeepSeek 站")

                # Mode 2: aggregate_sum (100 CNY + 1.5 USD * 7.2 = 110.8 CNY)
                _, _, tok = store.update_awareness(tok, {"mode": "aggregate_sum", "target_currency": "CNY"})
                obs_agg = await port.fetch()
                self.assertAlmostEqual(float(obs_agg.balance), 110.8, places=2)
                self.assertEqual(obs_agg.unit, "CNY")
                self.assertIn("2 个站点", obs_agg.extra)

                # Mode 3: any_low -> picks OpenRouter (1.5 <= critical 2.0) over DeepSeek (100 CNY normal)
                _, _, tok = store.update_awareness(tok, {"mode": "any_low"})
                obs_low = await port.fetch()
                self.assertAlmostEqual(float(obs_low.balance), 1.5, places=2)
                self.assertEqual(obs_low.unit, "USD")
                self.assertEqual(obs_low.plan_name, "OpenRouter 站")

                # Multi-role propagation updates ALL sessions on controller
                app2 = NS(
                    keyword_materials=_FakeKeywordMaterials(),
                    contact_schedule=NS(balance_events=_FakeBalanceEvents()),
                    balance_port=None,
                )
                ctrl.sessions = {"role-1": ctrl.session, "role-2": NS(application=app2)}
                propagate_balance_port(ctrl, port)
                self.assertIs(ctrl.session.application.balance_port, port)
                self.assertIs(app2.balance_port, port)
                self.assertEqual(app2.keyword_materials.invalidated, ["service.balance"])
                self.assertEqual(len(app2.contact_schedule.balance_events.updates), 1)

                await close_quota_watch_services(ctrl)

        asyncio.run(run())

    def test_backup_policy_includes_quota_dir_and_scrubs_secrets(self):
        self.assertIn("quota", OWNED_ROOT_DIRECTORIES)
        self.assertIn("quota/", LEGACY_PRESERVES)
        self.assertIn("auth_token", SECRET_KEYS)
        self.assertIn("proxy_url", SECRET_KEYS)


class QuotaWebApiIntegrationTests(unittest.TestCase):
    def test_full_api_routes_crud_detect_test_exchange_and_secret_safety(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                ctrl = _make_controller(tmp)
                api = ModelWebAPI(ctrl)

                # Verify all 10 quota routes are mounted on ModelWebAPI
                route_paths = {path for path, _h, _m, _d in api.routes()}
                for suffix in (
                    "/quota",
                    "/quota/adapters",
                    "/quota/detect",
                    "/quota/test",
                    "/quota/exchange-token",
                    "/quota/stations",
                    "/quota/stations/delete",
                    "/quota/refresh",
                    "/quota/awareness",
                    "/quota/icon",
                ):
                    self.assertIn("/astrbot_plugin_private_companion/model" + suffix, route_paths)

                factory, _ = _mock_factory({
                    ("GET", "https://relay.example.test/api/status"): _FakeResponse(
                        200,
                        {"success": True, "data": {"system_name": "测试中转站", "quota_display_type": "CNY", "quota_per_unit": 500000, "logo": "/logo.png"}},
                    ),
                    ("GET", "https://relay.example.test/logo.png"): _FakeResponse(200, PNG_BYTES, {"Content-Type": "image/png"}),
                    ("GET", "https://relay.example.test/api/user/self"): _FakeResponse(
                        200,
                        {"success": True, "data": {"id": 9, "username": "demo", "quota": 5000000, "used_quota": 1000000, "group": "pro", "access_token": "tok-exchanged-555"}},
                    ),
                    ("GET", "https://relay.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                        200, {"success": True, "data": {"items": []}}
                    ),
                    ("POST", "https://relay.example.test/api/user/login"): _FakeResponse(
                        200, {"success": True, "data": {"id": 9, "require_2fa": False}}
                    ),
                })
                # Pre-bind mock session_factory on the host service
                get_quota_watch_service(ctrl, Path(tmp), session_factory=factory)

                async def fake_auth(allow_query_keys=()):
                    return (ctrl.session, NS(), NS(), None)

                with patch.object(api, "_run_authorized_read", side_effect=fake_auth):
                    # 1. GET /quota
                    with patch("astrbot_host.quota_api.request", _FakeRequest()):
                        res_dash = await api._quota_api.dashboard()
                    self.assertEqual(res_dash["status"], "ok")
                    edit_token = res_dash["data"]["edit_token"]
                    # Ensure AstrBot provider keys NEVER appear in dashboard response
                    dash_json = json.dumps(res_dash)
                    self.assertNotIn("secret-ds-provider-key-999", dash_json)
                    self.assertNotIn("secret-or-provider-key-888", dash_json)

                    # 2. POST /quota/detect
                    with patch("astrbot_host.quota_api.request", _FakeRequest(body={"base_url": "https://relay.example.test/v1"})):
                        res_det = await api._quota_api.detect()
                    self.assertEqual(res_det["status"], "ok")
                    self.assertEqual(res_det["data"]["adapter_id"], "newapi")
                    self.assertEqual(res_det["data"]["suggested_name"], "测试中转站")

                    # 3. POST /quota/exchange-token (password never saved!)
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={"base_url": "https://relay.example.test", "username": "demo", "password": "ephemeral-pw-never-stored"}),
                    ):
                        res_ex = await api._quota_api.exchange_token()
                    self.assertEqual(res_ex["status"], "ok")
                    self.assertEqual(res_ex["data"]["access_token"], "tok-exchanged-555")
                    self.assertEqual(res_ex["data"]["user_id"], "9")

                    # 4. POST /quota/test (draft test without saving)
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={"station": {"name": "中转", "adapter_id": "newapi", "base_url": "https://relay.example.test", "auth_token": "tok-exchanged-555"}}),
                    ):
                        res_test = await api._quota_api.test_draft()
                    self.assertEqual(res_test["status"], "ok")
                    self.assertTrue(res_test["data"]["snapshot"]["ok"])
                    self.assertAlmostEqual(res_test["data"]["snapshot"]["remaining"], 10.0)

                    # 5. POST /quota/stations (create + auto probe + live balance_port sync)
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={
                            "edit_token": edit_token,
                            "station": {
                                "name": "测试中转站",
                                "adapter_id": "newapi",
                                "base_url": "https://relay.example.test",
                                "auth_token": "tok-exchanged-555",
                                "proxy_url": "http://u:secret-proxy-pw@127.0.0.1:7890",
                                "participate_in_awareness": True,
                            },
                        }),
                    ):
                        res_save = await api._quota_api.save_station()
                    self.assertEqual(res_save["status"], "ok")
                    sid = res_save["data"]["station_id"]
                    edit_token = res_save["data"]["edit_token"]
                    save_json = json.dumps(res_save)
                    self.assertNotIn("tok-exchanged-555", save_json)
                    self.assertNotIn("secret-proxy-pw", save_json)
                    self.assertIsInstance(ctrl.session.application.balance_port, MultiStationBalancePort)

                    # Verify password never written anywhere under tmp
                    for p in Path(tmp).rglob("*"):
                        if p.is_file():
                            self.assertNotIn("ephemeral-pw-never-stored", p.read_text(encoding="utf-8", errors="ignore"))

                    # 6. GET /quota/icon
                    with patch("astrbot_host.quota_api.request", _FakeRequest(query={"id": sid})):
                        res_icon = await api._quota_api.icon()
                    self.assertEqual(res_icon["status"], "ok")
                    self.assertTrue(res_icon["data"]["available"])
                    self.assertTrue(res_icon["data"]["data_url"].startswith("data:image/png;base64,"))

                    # 7. POST /quota/awareness
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={"edit_token": edit_token, "awareness": {"mode": "aggregate_sum", "target_currency": "CNY"}}),
                    ):
                        res_aw = await api._quota_api.save_awareness()
                    self.assertEqual(res_aw["status"], "ok")
                    self.assertEqual(res_aw["data"]["awareness"]["mode"], "aggregate_sum")
                    edit_token = res_aw["data"]["edit_token"]

                    # 8. POST /quota/stations/delete
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={"edit_token": edit_token, "station_id": sid}),
                    ):
                        res_del = await api._quota_api.delete_station()
                    self.assertEqual(res_del["status"], "ok")
                    self.assertEqual(res_del["data"]["stations"], [])

                await close_quota_watch_services(ctrl)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
