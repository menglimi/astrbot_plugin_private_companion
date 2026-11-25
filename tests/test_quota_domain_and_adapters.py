"""Deterministic offline unit tests for PR 9a: quota domain store, FX summary, SSRF, logo cache, and protocol adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from companion_v2.model_gateway.quota import (
    QuotaAwarenessPolicy,
    QuotaSettingsError,
    QuotaSnapshot,
    QuotaSnapshotCache,
    QuotaStationSpec,
    QuotaStationStore,
    convert_currency_amount,
    evaluate_station_health,
    normalize_currency_symbol,
    redact_quota_text,
    slugify_station_id,
    summarize_quota_portfolio,
)
from companion_v2.proactive.balance_events import BalanceObservation
from astrbot_host.quota_adapters import (
    AdapterProbeContext,
    QuotaHttpError,
    StationLogoCache,
    default_registry,
    detect_station,
    exchange_newapi_login_for_token,
    extract_html_icon_links,
    fetch_recent_consumption_logs,
    http_request_public_bytes,
    sanitize_safe_svg,
    sniff_safe_logo,
    sniff_safe_raster_image,
    validate_target_url,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 24
ICO_BYTES = b"\x00\x00\x01\x00" + b"\x00" * 32
GIF_BYTES = b"GIF89a" + b"\x00" * 24
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML_BYTES = b"<!DOCTYPE html><html><body>Login required</body></html>"


class _FakeContent:
    def __init__(self, body: bytes):
        self._body = body

    async def iter_chunked(self, size: int):
        for idx in range(0, len(self._body), size):
            yield self._body[idx : idx + size]


class _FakeResponse:
    def __init__(self, status: int, body: bytes | dict, headers: dict[str, str] | None = None):
        self.status = status
        if isinstance(body, dict):
            self._body = json.dumps(body).encode("utf-8")
            default_headers = {"Content-Type": "application/json"}
        else:
            self._body = body
            default_headers = {}
        self.headers = {**default_headers, **(headers or {})}
        self.content_length = len(self._body)
        self.content = _FakeContent(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def request(self, method: str, url: str, **kwargs):
        return self._handler(method, url, kwargs)


def _mock_factory(routes):
    calls: list[tuple[str, str, dict]] = []

    def factory(**_session_kwargs):
        def handler(method: str, url: str, kwargs: dict):
            calls.append((method, url, kwargs))
            result = routes(method, url, kwargs) if callable(routes) else routes.get((method, url))
            if isinstance(result, Exception):
                raise result
            if result is None:
                return _FakeResponse(404, {"error": "not found"})
            return result

        return _FakeSession(handler)

    return factory, calls


class QuotaDomainTests(unittest.TestCase):
    def test_domain_module_has_zero_host_or_network_imports(self):
        # Verify companion_v2.model_gateway.quota imports zero astrbot/host/network modules via AST.
        import ast
        import companion_v2.model_gateway.quota as quota_mod

        tree = ast.parse(Path(quota_mod.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("aiohttp", "astrbot", "astrbot_host", "requests", "httpx"):
            self.assertFalse(
                any(mod == forbidden or mod.startswith(forbidden + ".") for mod in imported),
                f"forbidden import found: {forbidden} in {sorted(imported)}",
            )

    def test_currency_normalization_and_fx_conversion(self):
        self.assertEqual(normalize_currency_symbol("CNY", "¤"), "¥")
        self.assertEqual(normalize_currency_symbol("USD", "¤"), "$")
        self.assertEqual(normalize_currency_symbol("CREDIT", "¤"), "")
        self.assertEqual(normalize_currency_symbol("CNY", "RMB"), "RMB")

        self.assertAlmostEqual(convert_currency_amount(10.0, "USD", "CNY", 7.2), 72.0, places=4)
        self.assertAlmostEqual(convert_currency_amount(72.0, "CNY", "USD", 7.2), 10.0, places=4)
        self.assertIsNone(convert_currency_amount(500.0, "CREDIT", "CNY", 7.2))
        self.assertIsNone(convert_currency_amount(99999.0, "TOKENS", "CNY", 7.2))

    def test_portfolio_summary_converts_cny_and_usd_accurately_without_mixing_non_fiat(self):
        s_cny = QuotaStationSpec(id="st-cny", name="CNY站", adapter_id="deepseek", base_url="https://api.deepseek.com")
        s_usd = QuotaStationSpec(id="st-usd", name="USD站", adapter_id="openrouter", base_url="https://openrouter.ai")
        s_cred = QuotaStationSpec(id="st-cred", name="积分站", adapter_id="newapi", base_url="https://relay.example.test")
        s_off = QuotaStationSpec(id="st-off", name="停用站", adapter_id="newapi", base_url="https://off.example.test", enabled=False)

        snaps = {
            "st-cny": QuotaSnapshot(station_id="st-cny", ok=True, remaining=100.0, used=20.0, total=120.0, display_type="CNY", checked_at="2026-11-24T10:00:00+00:00"),
            "st-usd": QuotaSnapshot(station_id="st-usd", ok=True, remaining=10.0, used=95.0, total=105.0, display_type="USD", checked_at="2026-11-24T10:05:00+00:00"),
            "st-cred": QuotaSnapshot(station_id="st-cred", ok=True, remaining=5000.0, display_type="CREDIT", checked_at="2026-11-24T10:01:00+00:00"),
        }
        summary = summarize_quota_portfolio([s_cny, s_usd, s_cred, s_off], snaps, display_currency="CNY", usd_cny_rate=7.25)
        self.assertAlmostEqual(summary["fiat_remaining"], 100.0 + 10.0 * 7.25, places=3)
        self.assertEqual(summary["cny_station_count"], 1)
        self.assertEqual(summary["usd_station_count"], 1)
        self.assertEqual(summary["non_fiat_station_count"], 1)
        self.assertEqual(summary["disabled_count"], 1)
        # st-usd remaining 10 / 105 = 9.52% <= default 10% -> low
        self.assertEqual(summary["health_by_id"]["st-usd"], "low")
        self.assertEqual(summary["health_by_id"]["st-cny"], "normal")
        self.assertEqual(summary["health_by_id"]["st-off"], "disabled")
        self.assertEqual(summary["latest_checked_at"], "2026-11-24T10:05:00+00:00")

    def test_redaction_strips_tokens_and_proxy_passwords(self):
        msg = "failed proxy=http://alice:p%40ssSecret99@10.0.0.2:7890 token=demo-tok-abcdef\r\nline2"
        redacted = redact_quota_text(msg, ("demo-tok-abcdef", "http://alice:p%40ssSecret99@10.0.0.2:7890"))
        self.assertNotIn("demo-tok-abcdef", redacted)
        self.assertNotIn("p%40ssSecret99", redacted)
        self.assertNotIn("p@ssSecret99", redacted)
        self.assertNotIn("\n", redacted)

    def test_store_atomic_crud_optimistic_lock_and_secret_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = QuotaStationStore(Path(tmp))
            stations, awareness, tok0 = store.read()
            self.assertEqual(stations, [])
            self.assertEqual(awareness.mode, "primary")

            # Create station
            created, stations, _, tok1 = store.upsert_station(
                tok0,
                {
                    "name": "测试中转站",
                    "adapter_id": "newapi_user_id",
                    "base_url": "https://relay.example.test",
                    "auth_token": "synthetic-access-token-1",
                    "user_id": "42",
                    "proxy_url": "http://u:secret-pw@127.0.0.1:7890",
                    "participate_in_awareness": True,
                },
            )
            self.assertEqual(len(stations), 1)
            self.assertEqual(created.auth_token, "synthetic-access-token-1")
            public = created.to_public_dict()
            self.assertNotIn("auth_token", public)
            self.assertNotIn("proxy_url", public)
            self.assertTrue(public["has_token"])
            self.assertTrue(public["has_proxy"])

            # Stale edit_token rejected
            with self.assertRaises(QuotaSettingsError) as ctx:
                store.upsert_station(tok0, {"id": created.id, "name": "改名", "adapter_id": "newapi_user_id", "base_url": "https://relay.example.test"})
            self.assertEqual(ctx.exception.code, "quota_config_conflict")

            # Empty token/proxy preserves existing secrets
            updated, _, _, tok2 = store.upsert_station(
                tok1,
                {
                    "id": created.id,
                    "name": "改名中转站",
                    "adapter_id": "newapi_user_id",
                    "base_url": "https://relay.example.test",
                    "auth_token": "",
                    "proxy_url": "",
                    "user_id": "42",
                },
            )
            self.assertEqual(updated.auth_token, "synthetic-access-token-1")
            self.assertEqual(updated.proxy_url, "http://u:secret-pw@127.0.0.1:7890")

            # Explicit clear removes secret
            cleared, _, _, tok3 = store.upsert_station(
                tok2,
                {
                    "id": created.id,
                    "name": "改名中转站",
                    "adapter_id": "newapi_user_id",
                    "base_url": "https://relay.example.test",
                    "clear_auth_token": True,
                    "clear_proxy_url": True,
                    "user_id": "42",
                },
            )
            self.assertEqual(cleared.auth_token, "")
            self.assertEqual(cleared.proxy_url, "")

            # Export strips all secrets
            exported = store.export_redacted_document()
            self.assertEqual(exported["stations"][0]["auth_token"], "")
            self.assertEqual(exported["stations"][0]["proxy_url"], "")

            # Awareness policy update & delete cleans up primary_station_id
            _, aw1, tok4 = store.update_awareness(tok3, {"mode": "primary", "primary_station_id": created.id})
            self.assertEqual(aw1.primary_station_id, created.id)
            stations_after, aw2, _ = store.delete_station(tok4, created.id)
            self.assertEqual(stations_after, [])
            self.assertEqual(aw2.primary_station_id, "")

    def test_snapshot_cache_persists_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = QuotaSnapshotCache(Path(tmp))
            snap1 = QuotaSnapshot(station_id="s1", ok=True, remaining=12.34, display_type="CNY", checked_at="2026-11-24T10:00:00+00:00")
            snap2 = QuotaSnapshot(station_id="s2", ok=False, error="boom", checked_at="2026-11-24T10:00:00+00:00")
            cache.put_many([snap1, snap2])

            reloaded = QuotaSnapshotCache(Path(tmp))
            all_snaps, updated = reloaded.all()
            self.assertEqual(set(all_snaps.keys()), {"s1", "s2"})
            self.assertTrue(updated)
            reloaded.prune(["s1"])
            self.assertEqual(set(reloaded.all()[0].keys()), {"s1"})

    def test_slugify_and_validation_edges(self):
        self.assertEqual(slugify_station_id("My Relay", {"my-relay"}), "my-relay-2")
        self.assertTrue(slugify_station_id("中文站点").startswith("station-"))
        with self.assertRaises(QuotaSettingsError) as ctx:
            QuotaStationSpec(id="s1", name="HTTP站", adapter_id="newapi", base_url="http://10.0.0.5:3000", allow_http=False)
        self.assertEqual(ctx.exception.code, "quota_http_not_allowed")
        with self.assertRaises(QuotaSettingsError):
            QuotaStationSpec(id="s2", name="坏阈值", adapter_id="newapi", base_url="https://a.example.test", low_threshold=5.0, critical_threshold=10.0)


class SecurityAndLogoTests(unittest.TestCase):
    def test_ssrf_and_http_guards(self):
        # Cloud metadata blocked even with allow_http=True!
        for url in (
            "http://169.254.169.254/latest/meta-data",
            "https://169.254.169.254/latest/meta-data",
            "http://100.100.100.200/latest",
            "https://metadata.google.internal/computeMetadata/v1/",
        ):
            with self.subTest(url=url), self.assertRaises(QuotaHttpError) as ctx:
                validate_target_url(url, allow_http=True)
            self.assertEqual(ctx.exception.code, "ssrf_blocked")

        # Private/loopback blocked by default, allowed when allow_http=True
        with self.assertRaises(QuotaHttpError) as ctx:
            validate_target_url("https://127.0.0.1:8317", allow_http=False)
        self.assertEqual(ctx.exception.code, "ssrf_blocked")
        self.assertEqual(validate_target_url("http://127.0.0.1:8317", allow_http=True), "http://127.0.0.1:8317")

    def test_logo_magic_bytes_rejects_html_and_svg(self):
        self.assertEqual(sniff_safe_raster_image(PNG_BYTES), "image/png")
        self.assertEqual(sniff_safe_raster_image(JPEG_BYTES), "image/jpeg")
        self.assertEqual(sniff_safe_raster_image(WEBP_BYTES), "image/webp")
        self.assertEqual(sniff_safe_raster_image(ICO_BYTES), "image/x-icon")
        self.assertEqual(sniff_safe_raster_image(GIF_BYTES), "image/gif")
        self.assertIsNone(sniff_safe_raster_image(HTML_BYTES))
        self.assertIsNone(sniff_safe_raster_image(SVG_BYTES))
        self.assertIsNone(sniff_safe_raster_image(b'<?xml version="1.0"?><svg></svg>'))

    def test_logo_cache_fallback_ttl_and_negative_cache(self):
        async def run():
            now_box = [datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)]
            cache = StationLogoCache(now=lambda: now_box[0])

            # Published logo returns disguised HTML -> falls back to /favicon.ico (ICO)
            factory, calls = _mock_factory({
                ("GET", "https://relay.example.test/brand.png"): _FakeResponse(200, HTML_BYTES, {"Content-Type": "image/png"}),
                ("GET", "https://relay.example.test/favicon.ico"): _FakeResponse(200, ICO_BYTES, {"Content-Type": "image/x-icon"}),
            })
            ver, err = await cache.refresh("st1", "https://relay.example.test", "/brand.png", session_factory=factory)
            self.assertTrue(ver)
            self.assertEqual(err, "")
            self.assertTrue(cache.get("st1").data_url().startswith("data:image/x-icon;base64,"))
            calls_after_first = len(calls)
            self.assertGreaterEqual(calls_after_first, 2)

            # Second call within 24h hits cache (0 new requests)
            ver2, _ = await cache.refresh("st1", "https://relay.example.test", "/brand.png", session_factory=factory)
            self.assertEqual(ver2, ver)
            self.assertEqual(len(calls), calls_after_first)

            # Broken logo negative cache prevents retry within 1h
            bad_factory, bad_calls = _mock_factory({
                ("GET", "https://dead.example.test/logo.svg"): _FakeResponse(200, SVG_BYTES, {"Content-Type": "image/svg+xml"}),
                ("GET", "https://dead.example.test/favicon.ico"): _FakeResponse(404, b"missing"),
            })
            ver_bad, err_bad = await cache.refresh("st2", "https://dead.example.test", "/logo.svg", session_factory=bad_factory)
            self.assertEqual(ver_bad, "")
            self.assertTrue(err_bad)
            first_count = len(bad_calls)
            await cache.refresh("st2", "https://dead.example.test", "/logo.svg", session_factory=bad_factory)
            self.assertEqual(len(bad_calls), first_count)

            # After >1h, negative cache expires and retries
            now_box[0] = now_box[0] + timedelta(hours=2)
            await cache.refresh("st2", "https://dead.example.test", "/logo.svg", session_factory=bad_factory)
            self.assertGreater(len(bad_calls), first_count)

        asyncio.run(run())


class QuotaAdaptersAndDetectorTests(unittest.TestCase):
    def test_registry_contains_all_expected_adapters_with_chinese_metadata(self):
        reg = default_registry()
        descs = reg.list_descriptors()
        ids = {d["id"] for d in descs}
        self.assertTrue(
            {"newapi", "newapi_user_id", "deepseek", "openrouter", "siliconflow", "moonshot", "sub2api", "openai_billing", "custom_script", "unsupported_official"}.issubset(ids)
        )
        for d in descs:
            self.assertTrue(d["name"])
            self.assertTrue(d["summary"])
            self.assertIsInstance(d["fields"], list)

    def test_newapi_full_probe_with_millisecond_log_and_independent_error_isolation(self):
        async def run():
            reg = default_registry()
            adapter = reg.get("newapi")
            spec = QuotaStationSpec(id="na1", name="演示站", adapter_id="newapi", base_url="https://relay.example.test")
            logo_cache = StationLogoCache()

            factory, _ = _mock_factory({
                ("GET", "https://relay.example.test/api/status"): _FakeResponse(
                    200,
                    {"success": True, "data": {"quota_display_type": "CNY", "quota_per_unit": 500000, "custom_currency_symbol": "¤", "logo": "/logo.png"}},
                ),
                ("GET", "https://relay.example.test/logo.png"): _FakeResponse(200, PNG_BYTES, {"Content-Type": "image/png"}),
                ("GET", "https://relay.example.test/api/user/self"): _FakeResponse(
                    200,
                    {"success": True, "data": {"id": 7, "username": "alice", "display_name": "Alice", "quota": 2500000, "used_quota": 500000, "group": "vip"}},
                ),
                ("GET", "https://relay.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                    200,
                    {"success": True, "data": {"items": [{"created_at": 1794193299000, "type": 2, "model_name": "claude-sonnet", "quota": 2500}]}},
                ),
            })
            ctx = AdapterProbeContext(base_url=spec.base_url, auth_token="tok-user-1", session_factory=factory, logo_cache=logo_cache)
            snap = await adapter.probe(spec, ctx)
            self.assertTrue(snap.ok)
            self.assertAlmostEqual(snap.remaining, 5.0, places=4)
            self.assertAlmostEqual(snap.used, 1.0, places=4)
            self.assertAlmostEqual(snap.total, 6.0, places=4)
            self.assertEqual(snap.display_type, "CNY")
            self.assertEqual(snap.symbol, "¥")
            self.assertEqual(snap.group_name, "vip")
            self.assertEqual(snap.last_used_model, "claude-sonnet")
            self.assertAlmostEqual(snap.last_used_cost, 0.005, places=6)
            self.assertTrue(snap.last_used_at.startswith("2026-"))
            self.assertTrue(snap.logo_version)

        asyncio.run(run())

    def test_newapi_diagnoses_missing_user_id_and_sk_key_and_blocks_redirects(self):
        async def run():
            adapter = default_registry().get("newapi")
            spec = QuotaStationSpec(id="na2", name="神人站", adapter_id="newapi", base_url="https://fork.example.test")

            # Missing New-Api-User in HTTP 200 success=false body
            f_uid, _ = _mock_factory({
                ("GET", "https://fork.example.test/api/status"): _FakeResponse(200, {"success": True, "data": {"quota_display_type": "USD"}}),
                ("GET", "https://fork.example.test/api/user/self"): _FakeResponse(200, {"success": False, "message": "New-Api-User header not provided"}),
                ("GET", "https://fork.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(200, {"success": False, "message": "New-Api-User header not provided"}),
            })
            snap_uid = await adapter.probe(spec, AdapterProbeContext(base_url=spec.base_url, auth_token="tok-1", session_factory=f_uid))
            self.assertFalse(snap_uid.ok)
            self.assertEqual(snap_uid.error_code, "requires_user_id")
            self.assertEqual(snap_uid.detected_header, "New-Api-User")

            # sk- channel key rejected
            f_sk, _ = _mock_factory({
                ("GET", "https://fork.example.test/api/status"): _FakeResponse(200, {"success": True, "data": {"quota_display_type": "USD"}}),
                ("GET", "https://fork.example.test/api/user/self"): _FakeResponse(403, {"error": "ACCESS_TOKEN_SCOPE_DENIED"}),
                ("GET", "https://fork.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(403, {"error": "ACCESS_TOKEN_SCOPE_DENIED"}),
            })
            snap_sk = await adapter.probe(spec, AdapterProbeContext(base_url=spec.base_url, auth_token="bad-channel-tok", session_factory=f_sk))
            self.assertFalse(snap_sk.ok)
            self.assertEqual(snap_sk.error_code, "sk_key_rejected")

            # Redirect blocked
            f_redir, _ = _mock_factory({
                ("GET", "https://fork.example.test/api/status"): _FakeResponse(404, b""),
                ("GET", "https://fork.example.test/api/user/self"): _FakeResponse(302, b"", {"Location": "https://evil.example.net/steal"}),
                ("GET", "https://fork.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(302, b"", {"Location": "https://evil.example.net/steal"}),
            })
            snap_redir = await adapter.probe(spec, AdapterProbeContext(base_url=spec.base_url, auth_token="tok-secret", session_factory=f_redir))
            self.assertFalse(snap_redir.ok)
            self.assertEqual(snap_redir.error_code, "redirect_blocked")

        asyncio.run(run())

    def test_newapi_one_time_login_exchange_and_2fa_guard(self):
        async def run():
            f_ok, _ = _mock_factory({
                ("POST", "https://relay.example.test/api/user/login"): _FakeResponse(200, {"success": True, "data": {"id": 88, "require_2fa": False}}),
                ("GET", "https://relay.example.test/api/user/self"): _FakeResponse(200, {"success": True, "data": {"id": 88, "access_token": "exchanged-tok-999"}}),
            })
            res = await exchange_newapi_login_for_token("https://relay.example.test", "bob", "my-pass-123", session_factory=f_ok)
            self.assertEqual(res["access_token"], "exchanged-tok-999")
            self.assertEqual(res["user_id"], "88")

            f_2fa, _ = _mock_factory({
                ("POST", "https://relay.example.test/api/user/login"): _FakeResponse(200, {"success": True, "data": {"require_2fa": True}}),
            })
            with self.assertRaises(QuotaHttpError) as ctx:
                await exchange_newapi_login_for_token("https://relay.example.test", "bob", "my-pass-123", session_factory=f_2fa)
            self.assertEqual(ctx.exception.code, "login_2fa_required")
            self.assertNotIn("my-pass-123", str(ctx.exception))

        asyncio.run(run())

    def test_official_and_generic_adapters(self):
        async def run():
            reg = default_registry()

            # DeepSeek
            f_ds, _ = _mock_factory({
                ("GET", "https://api.deepseek.com/user/balance"): _FakeResponse(
                    200,
                    {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "42.50", "granted_balance": "2.50", "topped_up_balance": "40.00"}]},
                ),
            })
            ds = await reg.get("deepseek").probe(
                QuotaStationSpec(id="ds", name="DeepSeek", adapter_id="deepseek", base_url="https://api.deepseek.com"),
                AdapterProbeContext(base_url="https://api.deepseek.com", astrbot_api_key="ds-tok", session_factory=f_ds),
            )
            self.assertTrue(ds.ok)
            self.assertAlmostEqual(ds.remaining, 42.5)
            self.assertEqual(ds.display_type, "CNY")
            self.assertIn("充值", ds.extra)

            # OpenRouter fallback from /credits 403 -> /auth/key
            f_or, _ = _mock_factory({
                ("GET", "https://openrouter.ai/api/v1/credits"): _FakeResponse(403, {"error": "forbidden"}),
                ("GET", "https://openrouter.ai/api/v1/auth/key"): _FakeResponse(
                    200, {"data": {"label": "main-key", "limit": 20.0, "limit_remaining": 15.25, "usage": 4.75, "is_free_tier": False}}
                ),
            })
            ors = await reg.get("openrouter").probe(
                QuotaStationSpec(id="or", name="OpenRouter", adapter_id="openrouter", base_url="https://openrouter.ai/api/v1"),
                AdapterProbeContext(base_url="https://openrouter.ai/api/v1", astrbot_api_key="or-tok", session_factory=f_or),
            )
            self.assertTrue(ors.ok)
            self.assertTrue(ors.is_key_scoped)
            self.assertAlmostEqual(ors.remaining, 15.25)

            # SiliconFlow
            f_sf, _ = _mock_factory({
                ("GET", "https://api.siliconflow.cn/v1/user/info"): _FakeResponse(
                    200, {"data": {"name": "tester", "balance": "3.0", "chargeBalance": "17.0", "totalBalance": "20.0"}}
                ),
            })
            sfs = await reg.get("siliconflow").probe(
                QuotaStationSpec(id="sf", name="硅基流动", adapter_id="siliconflow", base_url="https://api.siliconflow.cn/v1"),
                AdapterProbeContext(base_url="https://api.siliconflow.cn/v1", astrbot_api_key="sf-tok", session_factory=f_sf),
            )
            self.assertTrue(sfs.ok)
            self.assertAlmostEqual(sfs.remaining, 20.0)

            # Moonshot
            f_ms, _ = _mock_factory({
                ("GET", "https://api.moonshot.cn/v1/users/me/balance"): _FakeResponse(
                    200, {"data": {"available_balance": 18.6, "cash_balance": 15.0, "voucher_balance": 3.6}}
                ),
            })
            mss = await reg.get("moonshot").probe(
                QuotaStationSpec(id="ms", name="Kimi", adapter_id="moonshot", base_url="https://api.moonshot.cn/v1"),
                AdapterProbeContext(base_url="https://api.moonshot.cn/v1", astrbot_api_key="ms-tok", session_factory=f_ms),
            )
            self.assertTrue(mss.ok)
            self.assertAlmostEqual(mss.remaining, 18.6)

            # Sub2API
            f_sub, _ = _mock_factory({
                ("GET", "https://sub.example.test/v1/usage"): _FakeResponse(
                    200, {"isValid": True, "planName": "Pro", "unit": "USD", "quota": {"limit": 50.0, "used": 12.5}}
                ),
            })
            subs = await reg.get("sub2api").probe(
                QuotaStationSpec(id="sub", name="Sub2API", adapter_id="sub2api", base_url="https://sub.example.test/v1"),
                AdapterProbeContext(base_url="https://sub.example.test/v1", astrbot_api_key="sub-tok", session_factory=f_sub),
            )
            self.assertTrue(subs.ok)
            self.assertAlmostEqual(subs.remaining, 37.5)
            self.assertEqual(subs.group_name, "Pro")

            # OpenAI Billing (cents -> USD)
            def billing_routes(method, url, _kw):
                if url.endswith("/v1/dashboard/billing/subscription"):
                    return _FakeResponse(200, {"hard_limit_usd": 30.0})
                if "/v1/dashboard/billing/usage?" in url:
                    return _FakeResponse(200, {"total_usage": 750})
                return _FakeResponse(404, b"")

            f_oai, _ = _mock_factory(billing_routes)
            oais = await reg.get("openai_billing").probe(
                QuotaStationSpec(id="oai", name="兼容站", adapter_id="openai_billing", base_url="https://compat.example.test/v1"),
                AdapterProbeContext(base_url="https://compat.example.test/v1", astrbot_api_key="oai-tok", session_factory=f_oai),
            )
            self.assertTrue(oais.ok)
            self.assertTrue(oais.is_key_scoped)
            self.assertAlmostEqual(oais.remaining, 22.5)
            self.assertAlmostEqual(oais.used, 7.5)

            # Unsupported Official makes 0 calls
            f_zero, zero_calls = _mock_factory({})
            unsup = await reg.get("unsupported_official").probe(
                QuotaStationSpec(id="u1", name="OpenAI", adapter_id="unsupported_official", base_url="https://api.openai.com/v1"),
                AdapterProbeContext(base_url="https://api.openai.com/v1", session_factory=f_zero),
            )
            self.assertFalse(unsup.ok)
            self.assertEqual(unsup.error_code, "unsupported_official")
            self.assertEqual(zero_calls, [])

            # Custom Script delegates cleanly to ScriptServiceBalancePort
            fake_obs = BalanceObservation(Decimal("12.34"), Decimal("20.00"), datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc), unit="CNY", used=Decimal("7.66"), plan_name="自定义")
            with patch("astrbot_host.quota_adapters.script_adapter.ScriptServiceBalancePort") as mock_port_cls:
                mock_port_cls.return_value.fetch = AsyncMock(return_value=fake_obs)
                sc = await reg.get("custom_script").probe(
                    QuotaStationSpec(id="sc1", name="脚本站", adapter_id="custom_script", base_url="https://custom.example.test", custom_script="({request:{url:'https://custom.example.test/b'},extractor:r=>({remaining:1})})"),
                    AdapterProbeContext(base_url="https://custom.example.test", custom_script="({request:{url:'https://custom.example.test/b'},extractor:r=>({remaining:1})})"),
                )
            self.assertTrue(sc.ok)
            self.assertAlmostEqual(sc.remaining, 12.34)
            self.assertEqual(sc.display_type, "CNY")
            self.assertEqual(sc.group_name, "自定义")

        asyncio.run(run())

    def test_detector_identifies_official_unsupported_and_newapi_derivative_uid(self):
        async def run():
            # Instant official host (0 network calls)
            f0, calls0 = _mock_factory({})
            r_ds = await detect_station("https://api.deepseek.com/v1", session_factory=f0)
            self.assertEqual(r_ds.adapter_id, "deepseek")
            self.assertEqual(r_ds.confidence, "high")
            self.assertEqual(calls0, [])

            r_oai = await detect_station("https://api.openai.com/v1", session_factory=f0)
            self.assertEqual(r_oai.adapter_id, "unsupported_official")
            self.assertEqual(calls0, [])

            # NewAPI derivative requiring New-Api-User auto-upgraded to newapi_user_id
            f_fork, _ = _mock_factory({
                ("GET", "https://shenren.example.test/api/status"): _FakeResponse(
                    200, {"success": True, "data": {"system_name": "神人中转", "quota_display_type": "CNY", "quota_per_unit": 500000, "logo": "/logo.png", "turnstile_check": True}}
                ),
                ("GET", "https://shenren.example.test/api/user/self"): _FakeResponse(
                    401, {"success": False, "message": "New-Api-User header not provided"}
                ),
            })
            r_fork = await detect_station("https://shenren.example.test/v1", session_factory=f_fork)
            self.assertEqual(r_fork.adapter_id, "newapi_user_id")
            self.assertTrue(r_fork.requires_user_id)
            self.assertEqual(r_fork.user_id_header, "New-Api-User")
            self.assertTrue(r_fork.turnstile_enabled)
            self.assertEqual(r_fork.suggested_name, "神人中转")

            # Same-origin Sub2API detection with API key
            f_sub, _ = _mock_factory({
                ("GET", "https://sub.example.test/api/status"): _FakeResponse(404, b""),
                ("GET", "https://sub.example.test/v1/usage"): _FakeResponse(200, {"isValid": True, "remaining": 9.5, "unit": "USD"}),
            })
            r_sub = await detect_station("https://sub.example.test/v1", api_key="key-1", session_factory=f_sub)
            self.assertEqual(r_sub.adapter_id, "sub2api")
            self.assertEqual(r_sub.confidence, "high")

            # CUSTOM / POINTS quota_display_type maps to CREDIT (never USD)
            f_custom, _ = _mock_factory({
                ("GET", "https://points.example.test/api/status"): _FakeResponse(
                    200, {"success": True, "data": {"system_name": "积分站", "quota_display_type": "CUSTOM", "custom_currency_symbol": "积分", "quota_per_unit": 1000}}
                ),
                ("GET", "https://points.example.test/api/user/self"): _FakeResponse(401, {"success": False, "message": "unauthorized"}),
            })
            r_custom = await detect_station("https://points.example.test", session_factory=f_custom)
            self.assertEqual(r_custom.display_type, "CREDIT")
            self.assertEqual(r_custom.symbol, "积分")

        asyncio.run(run())

    def test_ssrf_blocks_ipv4_mapped_link_local_metadata_proxy_and_cross_origin_lan_logo(self):
        from astrbot_host.quota_adapters.base import QuotaHttpError, validate_proxy_url, validate_target_url
        from astrbot_host.quota_adapters.logo import resolve_logo_url

        # IPv4-mapped IPv6 and link-local addresses must stay blocked even with allow_http=True
        for blocked in (
            "http://[::ffff:169.254.169.254]/latest/meta-data/",
            "http://[::ffff:100.100.100.200]/latest/meta-data/",
            "http://169.254.12.34/metadata",
            "http://[fe80::1]/metadata",
        ):
            with self.assertRaises(QuotaHttpError) as ctx:
                validate_target_url(blocked, allow_http=True)
            self.assertEqual(ctx.exception.code, "ssrf_blocked")

        # Proxy URL must also block cloud metadata & link-local hosts
        for blocked_proxy in (
            "http://[::ffff:169.254.169.254]:8080",
            "socks5://169.254.169.254:1080",
            "http://[fe80::1]:8080",
        ):
            with self.assertRaises(QuotaHttpError) as ctx:
                validate_proxy_url(blocked_proxy)
            self.assertEqual(ctx.exception.code, "ssrf_blocked")

        # Cross-origin LAN logo on allow_http=True station must be rejected, while same-origin & public HTTPS pass
        self.assertEqual(
            resolve_logo_url("http://192.168.1.10:3000", "http://127.0.0.1:8080/internal.png", allow_http=True),
            "",
        )
        self.assertEqual(
            resolve_logo_url("http://192.168.1.10:3000", "http://192.168.1.20:3000/other.png", allow_http=True),
            "",
        )
        self.assertEqual(
            resolve_logo_url("http://192.168.1.10:3000", "/logo.png", allow_http=True),
            "http://192.168.1.10:3000/logo.png",
        )
        self.assertEqual(
            resolve_logo_url("http://192.168.1.10:3000", "https://cdn.example.test/logo.png", allow_http=True),
            "https://cdn.example.test/logo.png",
        )

    def test_newapi_custom_display_type_and_login_exchange_hardening(self):
        from astrbot_host.quota_adapters.base import QuotaHttpError
        from astrbot_host.quota_adapters.newapi import exchange_newapi_login_for_token

        async def run():
            reg = default_registry()
            # CUSTOM points station must stay CREDIT and not inflate fiat summary
            f_custom, _ = _mock_factory({
                ("GET", "https://points.example.test/api/status"): _FakeResponse(
                    200, {"success": True, "data": {"quota_display_type": "CUSTOM", "custom_currency_symbol": "点", "quota_per_unit": 1000}}
                ),
                ("GET", "https://points.example.test/api/user/self"): _FakeResponse(
                    200, {"success": True, "data": {"quota": 50000, "used_quota": 10000}}
                ),
                ("GET", "https://points.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(200, {"success": True, "data": {"items": []}}),
            })
            spec = QuotaStationSpec(id="pts", name="积分站", adapter_id="newapi", base_url="https://points.example.test", auth_token="tok-1")
            snap = await reg.get("newapi").probe(spec, AdapterProbeContext(base_url=spec.base_url, auth_token="tok-1", session_factory=f_custom))
            self.assertTrue(snap.ok)
            self.assertEqual(snap.display_type, "CREDIT")
            self.assertEqual(snap.symbol, "点")
            summary = summarize_quota_portfolio([spec], {"pts": snap}, display_currency="CNY", usd_cny_rate=7.2)
            self.assertEqual(summary["fiat_remaining"], 0.0)
            self.assertEqual(summary["non_fiat_station_count"], 1)

            # Login exchange: echoed password redacted in error
            f_err, _ = _mock_factory({
                ("POST", "https://login.example.test/api/user/login"): _FakeResponse(
                    200, {"success": False, "message": "Invalid password secret-pass-999 for alice"}
                ),
            })
            with self.assertRaises(QuotaHttpError) as ctx:
                await exchange_newapi_login_for_token("https://login.example.test", "alice", "secret-pass-999", session_factory=f_err)
            self.assertNotIn("secret-pass-999", str(ctx.exception))

            # Login exchange: invalid header falls back to New-Api-User & existing token sets regenerated=False
            f_existing, calls_existing = _mock_factory({
                ("POST", "https://login.example.test/api/user/login"): _FakeResponse(200, {"success": True, "data": {"id": 42}}),
                ("GET", "https://login.example.test/api/user/self"): _FakeResponse(
                    200, {"success": True, "data": {"id": 42, "access_token": "existing-tok-42"}}
                ),
            })
            res_existing = await exchange_newapi_login_for_token(
                "https://login.example.test",
                "alice",
                "pw-1",
                user_id_header="Bad Header\r\nX-Inject: 1",
                session_factory=f_existing,
            )
            self.assertEqual(res_existing["access_token"], "existing-tok-42")
            self.assertFalse(res_existing["regenerated"])
            self_call_headers = calls_existing[1][2]["headers"]
            self.assertEqual(self_call_headers.get("New-Api-User"), "42")
            self.assertNotIn("Bad Header\r\nX-Inject: 1", self_call_headers)

            # Login exchange: fallback to /api/user/token sets regenerated=True
            f_regen, _ = _mock_factory({
                ("POST", "https://login.example.test/api/user/login"): _FakeResponse(200, {"success": True, "data": {"id": 42}}),
                ("GET", "https://login.example.test/api/user/self"): _FakeResponse(200, {"success": True, "data": {"id": 42, "access_token": ""}}),
                ("GET", "https://login.example.test/api/user/token"): _FakeResponse(200, {"success": True, "data": "minted-tok-42"}),
            })
            res_regen = await exchange_newapi_login_for_token("https://login.example.test", "alice", "pw-1", session_factory=f_regen)
            self.assertEqual(res_regen["access_token"], "minted-tok-42")
            self.assertTrue(res_regen["regenerated"])

        asyncio.run(run())

    def test_newapi_four_mode_currency_multipliers_and_override_isolation(self):
        async def run():
            reg = default_registry()
            adapter = reg.get("newapi")

            # 1) CUSTOM mode with custom_currency_exchange_rate=10 (e.g. cookie credits + recharge ref)
            f_custom10, _ = _mock_factory({
                ("GET", "https://cookie.example.test/api/status"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "quota_display_type": "CUSTOM",
                            "quota_per_unit": 500000,
                            "custom_currency_symbol": "🍪",
                            "custom_currency_exchange_rate": 10,
                            "usd_exchange_rate": 1,
                            "price": 1,
                        },
                    },
                ),
                ("GET", "https://cookie.example.test/api/user/self"): _FakeResponse(
                    200,
                    {"success": True, "data": {"quota": 36649000, "used_quota": 130736000, "group": "default"}},
                ),
                ("GET", "https://cookie.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                    200,
                    {"success": True, "data": {"items": [{"created_at": 1794193299, "model_name": "gpt-5", "quota": 2500}]}},
                ),
            })
            spec_c10 = QuotaStationSpec(id="c10", name="饼干站", adapter_id="newapi", base_url="https://cookie.example.test")
            snap_c10 = await adapter.probe(spec_c10, AdapterProbeContext(base_url=spec_c10.base_url, auth_token="tok-1", session_factory=f_custom10))
            self.assertTrue(snap_c10.ok)
            self.assertEqual(snap_c10.display_type, "CREDIT")
            self.assertEqual(snap_c10.symbol, "🍪")
            self.assertAlmostEqual(snap_c10.remaining, 732.98, places=4)
            self.assertAlmostEqual(snap_c10.used, 2614.72, places=4)
            self.assertAlmostEqual(snap_c10.total, 3347.70, places=4)
            self.assertAlmostEqual(snap_c10.last_used_cost, 0.05, places=6)
            self.assertIn("10 🍪", snap_c10.extra)
            self.assertIn("¥73.30", snap_c10.extra)
            summary = summarize_quota_portfolio([spec_c10], {"c10": snap_c10}, display_currency="CNY", usd_cny_rate=7.2)
            self.assertEqual(summary["fiat_remaining"], 0.0)
            self.assertEqual(summary["non_fiat_station_count"], 1)

            # 2) CUSTOM mode with usd_exchange_rate=10, custom_currency_exchange_rate=1 must use 1 (never usd_rate)
            f_custom1, _ = _mock_factory({
                ("GET", "https://diamond.example.test/api/status"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "quota_display_type": "CUSTOM",
                            "quota_per_unit": 500000,
                            "custom_currency_symbol": "◆",
                            "usd_exchange_rate": 10,
                            "custom_currency_exchange_rate": 1,
                        },
                    },
                ),
                ("GET", "https://diamond.example.test/api/user/self"): _FakeResponse(
                    200, {"success": True, "data": {"quota": 5000000, "used_quota": 1000000}}
                ),
                ("GET", "https://diamond.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(200, {"success": True, "data": {"items": []}}),
            })
            spec_c1 = QuotaStationSpec(id="c1", name="星桥", adapter_id="newapi", base_url="https://diamond.example.test")
            snap_c1 = await adapter.probe(spec_c1, AdapterProbeContext(base_url=spec_c1.base_url, auth_token="tok-1", session_factory=f_custom1))
            self.assertAlmostEqual(snap_c1.remaining, 10.0, places=4)
            self.assertAlmostEqual(snap_c1.used, 2.0, places=4)
            self.assertEqual(snap_c1.symbol, "◆")

            # 3) Manual override spec.display_type="CNY" on a CUSTOM site must NOT crosstalk usd_exchange_rate or custom symbol
            snap_override = await adapter.probe(
                spec_c1,
                AdapterProbeContext(base_url=spec_c1.base_url, auth_token="tok-1", display_type="CNY", session_factory=f_custom1),
            )
            self.assertEqual(snap_override.display_type, "CNY")
            self.assertEqual(snap_override.symbol, "¥")
            self.assertAlmostEqual(snap_override.remaining, 10.0, places=4)
            self.assertEqual(snap_override.extra, "")

            # 4) CNY mode with usd_exchange_rate=7.3 multiplies balance and last_used_cost by 7.3
            f_cny73, _ = _mock_factory({
                ("GET", "https://cny73.example.test/api/status"): _FakeResponse(
                    200,
                    {"success": True, "data": {"quota_display_type": "CNY", "quota_per_unit": 500000, "usd_exchange_rate": 7.3}},
                ),
                ("GET", "https://cny73.example.test/api/user/self"): _FakeResponse(
                    200, {"success": True, "data": {"quota": 1000000, "used_quota": 500000}}
                ),
                ("GET", "https://cny73.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                    200, {"success": True, "data": {"items": [{"created_at": 1794193299, "model_name": "deepseek", "quota": 50000}]}}
                ),
            })
            spec_cny = QuotaStationSpec(id="cny73", name="汇率CNY站", adapter_id="newapi", base_url="https://cny73.example.test")
            snap_cny = await adapter.probe(spec_cny, AdapterProbeContext(base_url=spec_cny.base_url, auth_token="tok-1", session_factory=f_cny73))
            self.assertEqual(snap_cny.display_type, "CNY")
            self.assertEqual(snap_cny.symbol, "¥")
            self.assertAlmostEqual(snap_cny.remaining, 14.6, places=4)
            self.assertAlmostEqual(snap_cny.used, 7.3, places=4)
            self.assertAlmostEqual(snap_cny.last_used_cost, 0.73, places=6)

            # 5) CNY mode with usd_exchange_rate=1 stays 1x
            f_cny1, _ = _mock_factory({
                ("GET", "https://cny1.example.test/api/status"): _FakeResponse(
                    200, {"success": True, "data": {"quota_display_type": "CNY", "quota_per_unit": 500000, "usd_exchange_rate": 1}}
                ),
                ("GET", "https://cny1.example.test/api/user/self"): _FakeResponse(
                    200, {"success": True, "data": {"quota": 31805000, "used_quota": 0}}
                ),
                ("GET", "https://cny1.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(200, {"success": True, "data": {"items": []}}),
            })
            snap_cny1 = await adapter.probe(
                QuotaStationSpec(id="cny1", name="原币CNY站", adapter_id="newapi", base_url="https://cny1.example.test"),
                AdapterProbeContext(base_url="https://cny1.example.test", auth_token="tok-1", session_factory=f_cny1),
            )
            self.assertAlmostEqual(snap_cny1.remaining, 63.61, places=4)

            # 6) TOKENS mode (display_in_currency=False with empty quota_display_type) keeps raw quota for balance AND log
            f_tok, _ = _mock_factory({
                ("GET", "https://tok.example.test/api/status"): _FakeResponse(
                    200, {"success": True, "data": {"system_name": "Token站", "quota_display_type": "", "display_in_currency": False, "quota_per_unit": 500000}}
                ),
                ("GET", "https://tok.example.test/api/user/self"): _FakeResponse(
                    200, {"success": True, "data": {"quota": 123456, "used_quota": 6544}}
                ),
                ("GET", "https://tok.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                    200, {"success": True, "data": {"items": [{"created_at": 1794193299, "model_name": "gpt-4o", "quota": 2500}]}}
                ),
            })
            det_tok = await detect_station("https://tok.example.test", session_factory=f_tok)
            self.assertEqual(det_tok.display_type, "TOKENS")
            snap_tok = await adapter.probe(
                QuotaStationSpec(id="tok", name="Token站", adapter_id="newapi", base_url="https://tok.example.test"),
                AdapterProbeContext(base_url="https://tok.example.test", auth_token="tok-1", session_factory=f_tok),
            )
            self.assertEqual(snap_tok.display_type, "TOKENS")
            self.assertAlmostEqual(snap_tok.remaining, 123456.0, places=4)
            self.assertAlmostEqual(snap_tok.used, 6544.0, places=4)
            self.assertAlmostEqual(snap_tok.last_used_cost, 2500.0, places=4)

        asyncio.run(run())

    def test_logo_1mb_png_safe_svg_sanitizer_redirects_and_discovery(self):
        async def run():
            # 1) 310 KiB PNG accepted (<= 1 MiB); > 1 MiB rejected
            large_valid_png = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (310 * 1024))
            oversized_png = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (1100 * 1024))
            cache = StationLogoCache()
            f_png, _ = _mock_factory({
                ("GET", "https://wiki.syuan.org/logo.png"): _FakeResponse(200, large_valid_png, {"Content-Type": "image/png"}),
                ("GET", "https://huge.example.test/logo.png"): _FakeResponse(200, oversized_png, {"Content-Type": "image/png"}),
            })
            ver_310, err_310 = await cache.refresh("syuan", "https://aiapi.syuan.org", "https://wiki.syuan.org/logo.png", session_factory=f_png)
            self.assertTrue(ver_310)
            self.assertEqual(err_310, "")
            self.assertTrue(cache.get("syuan").data_url().startswith("data:image/png;base64,"))

            ver_huge, err_huge = await cache.refresh("huge", "https://huge.example.test", "/logo.png", session_factory=f_png)
            self.assertEqual(ver_huge, "")
            self.assertTrue(err_huge)

            # 2) Safe static pixel-cat SVG accepted; malicious SVGs rejected
            cat_svg = (
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                b'<defs><linearGradient id="fur"><stop offset="0%" stop-color="#8ecae6"/></linearGradient></defs>'
                b'<rect x="2" y="2" width="12" height="12" fill="url(#fur)"/>'
                b'<use href="#fur"/><path d="M4 6h8" stroke="#111"/></svg>'
            )
            mime_svg, clean_svg = sniff_safe_logo(cat_svg)
            self.assertEqual(mime_svg, "image/svg+xml")
            self.assertIn(b"<svg", clean_svg)

            malicious_svgs = [
                b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body><iframe src="x"/></body></foreignObject></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect width="10" height="10"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="x" to="1"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><set attributeName="fill" to="red"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="https://evil.test/x.svg#a"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><use href="data:image/svg+xml;base64,PHN2Zz4="/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://evil.test/g#a)"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="background:url(http://evil.test/leak)"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:\\75rl(http://evil.test/x)"/></svg>',
                b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>',
            ]
            for bad in malicious_svgs:
                with self.subTest(bad=bad[:60]):
                    self.assertIsNone(sanitize_safe_svg(bad))

            # 3) Homepage <link rel="icon"> discovery works even on >64 KiB HTML pages (prefix read)
            big_html = (
                b'<!doctype html><html><head><link rel="icon" type="image/svg+xml" href="/brand/cat.svg"></head><body>'
                + (b"x" * (90 * 1024))
                + b"</body></html>"
            )
            self.assertEqual(
                extract_html_icon_links(big_html, "https://icon-demo.example.test/"),
                ["https://icon-demo.example.test/brand/cat.svg"],
            )
            f_cat, _ = _mock_factory({
                ("GET", "https://icon-demo.example.test/"): _FakeResponse(200, big_html, {"Content-Type": "text/html"}),
                ("GET", "https://icon-demo.example.test/brand/cat.svg"): _FakeResponse(200, cat_svg, {"Content-Type": "image/svg+xml"}),
            })
            ver_cat, err_cat = await cache.refresh("icon_demo", "https://icon-demo.example.test", "", session_factory=f_cat)
            self.assertTrue(ver_cat)
            self.assertEqual(err_cat, "")
            self.assertTrue(cache.get("icon_demo").data_url().startswith("data:image/svg+xml;base64,"))

            # 4) Public redirects: 2 safe HTTPS hops pass; redirect to metadata/loopback/LAN or >2 hops blocked; auth stripped
            f_redir, redir_calls = _mock_factory({
                ("GET", "https://deepseek.com/favicon.ico"): _FakeResponse(301, b"", {"Location": "https://www.deepseek.com/favicon.ico"}),
                ("GET", "https://www.deepseek.com/favicon.ico"): _FakeResponse(302, b"", {"Location": "/static/icon.ico"}),
                ("GET", "https://www.deepseek.com/static/icon.ico"): _FakeResponse(200, ICO_BYTES, {"Content-Type": "image/x-icon"}),
                ("GET", "https://evil.example.test/meta"): _FakeResponse(302, b"", {"Location": "http://169.254.169.254/latest/meta-data"}),
                ("GET", "https://evil.example.test/loop"): _FakeResponse(302, b"", {"Location": "https://127.0.0.1/secret"}),
                ("GET", "https://loop3.example.test/a"): _FakeResponse(302, b"", {"Location": "/b"}),
                ("GET", "https://loop3.example.test/b"): _FakeResponse(302, b"", {"Location": "/c"}),
                ("GET", "https://loop3.example.test/c"): _FakeResponse(302, b"", {"Location": "/d"}),
            })
            st, _hdrs, body, final_url = await http_request_public_bytes(
                "https://deepseek.com/favicon.ico",
                headers={"Authorization": "Bearer must-not-send", "Cookie": "sid=bad", "Accept": "*/*"},
                session_factory=f_redir,
            )
            self.assertEqual(st, 200)
            self.assertEqual(body, ICO_BYTES)
            self.assertEqual(final_url, "https://www.deepseek.com/static/icon.ico")
            for _m, _u, kw in redir_calls[:3]:
                lower_keys = {k.lower() for k in kw.get("headers", {})}
                self.assertNotIn("authorization", lower_keys)
                self.assertNotIn("cookie", lower_keys)

            with self.assertRaises(QuotaHttpError) as ctx_meta:
                await http_request_public_bytes("https://evil.example.test/meta", session_factory=f_redir)
            self.assertEqual(ctx_meta.exception.code, "ssrf_blocked")

            with self.assertRaises(QuotaHttpError) as ctx_loop:
                await http_request_public_bytes("https://evil.example.test/loop", session_factory=f_redir)
            self.assertEqual(ctx_loop.exception.code, "ssrf_blocked")

            with self.assertRaises(QuotaHttpError) as ctx_max:
                await http_request_public_bytes("https://loop3.example.test/a", max_redirects=2, session_factory=f_redir)
            self.assertEqual(ctx_max.exception.code, "too_many_redirects")

            # 5) Official DeepSeek & generic api.* host fallback
            f_api, _ = _mock_factory({
                ("GET", "https://api.deepseek.com/user/balance"): _FakeResponse(
                    200, {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "10.00"}]}
                ),
                ("GET", "https://fe-static.deepseek.com/chat/icon-180.png"): _FakeResponse(200, PNG_BYTES, {"Content-Type": "image/png"}),
                ("GET", "https://api.customrelay.test/"): _FakeResponse(401, b"unauthorized"),
                ("GET", "https://www.customrelay.test/"): _FakeResponse(404, b""),
                ("GET", "https://www.customrelay.test/favicon.ico"): _FakeResponse(200, ICO_BYTES, {"Content-Type": "image/x-icon"}),
            })
            ds_snap = await default_registry().get("deepseek").probe(
                QuotaStationSpec(id="ds-logo", name="DeepSeek", adapter_id="deepseek", base_url="https://api.deepseek.com"),
                AdapterProbeContext(base_url="https://api.deepseek.com", astrbot_api_key="ds-tok", session_factory=f_api, logo_cache=cache),
            )
            self.assertTrue(ds_snap.ok)
            self.assertTrue(ds_snap.logo_version)
            self.assertTrue(cache.get("ds-logo").data_url().startswith("data:image/png;base64,"))

            ver_fb, err_fb = await cache.refresh("api-fb", "https://api.customrelay.test", "", session_factory=f_api)
            self.assertTrue(ver_fb)
            self.assertEqual(err_fb, "")
            self.assertTrue(cache.get("api-fb").data_url().startswith("data:image/x-icon;base64,"))

        asyncio.run(run())

    def test_dual_unit_fiat_conversion_topup_subscriptions_and_paginated_logs(self):
        async def run():
            reg = default_registry()
            adapter = reg.get("newapi")
            now_dt = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)

            f_sta1n, _ = _mock_factory({
                ("GET", "https://cookie.example.test/api/status"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "system_name": "STA1N",
                            "server_address": "https://cookie.example.test",
                            "quota_display_type": "CUSTOM",
                            "quota_per_unit": 500000,
                            "custom_currency_symbol": "🍪",
                            "custom_currency_exchange_rate": 10,
                            "usd_exchange_rate": 10,
                            "price": 1,
                        },
                    },
                ),
                ("GET", "https://cookie.example.test/api/user/self"): _FakeResponse(
                    200,
                    {"success": True, "data": {"quota": 36649000, "used_quota": 130736000, "group": "default"}},
                ),
                ("GET", "https://cookie.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                    200,
                    {"success": True, "data": {"items": [{"created_at": 1795470000, "model_name": "gemini-4", "quota": 2500}]}},
                ),
                ("GET", "https://cookie.example.test/api/user/topup/info"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "price": 1,
                            "enable_stripe_topup": False,
                            "pay_methods": [{"type": "alipay", "name": "支付宝"}, {"type": "wxpay", "name": "微信"}],
                        },
                    },
                ),
                ("GET", "https://cookie.example.test/api/subscription/self"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": [
                            {
                                "plan": {"title": "Pro"},
                                "subscription": {
                                    "status": "active",
                                    "amount_total": 70_000_000,
                                    "amount_used": 11_000_000,
                                    "end_time": int(now_dt.timestamp()) + 86400 * 10,
                                },
                            },
                            {
                                "plan": {"title": "Max"},
                                "subscription": {
                                    "status": "active",
                                    "amount_total": 225_000_000,
                                    "amount_used": 75_000_000,
                                    "end_time": int(now_dt.timestamp()) + 86400 * 20,
                                },
                            },
                            {
                                "plan": {"title": "ExpiredOld"},
                                "subscription": {
                                    "status": "expired",
                                    "amount_total": 50_000_000,
                                    "amount_used": 0,
                                    "end_time": int(now_dt.timestamp()) - 3600,
                                },
                            },
                        ],
                    },
                ),
            })

            # 1) Auto conversion (10 🍪 = ¥1) + active subscriptions (4180 🍪 / ¥418)
            spec = QuotaStationSpec(
                id="sta1n",
                name="STA1N",
                adapter_id="newapi",
                base_url="https://cookie.example.test",
                display_unit_mode="fiat",
            )
            snap = await adapter.probe(
                spec,
                AdapterProbeContext(base_url=spec.base_url, auth_token="tok-1", now=lambda: now_dt, session_factory=f_sta1n),
            )
            self.assertTrue(snap.ok)
            self.assertEqual(snap.inferred_fiat_currency, "CNY")
            self.assertAlmostEqual(snap.custom_exchange_rate, 10.0)
            self.assertAlmostEqual(snap.site_recharge_price, 1.0)
            self.assertAlmostEqual(snap.remaining, 732.98, places=4)
            self.assertAlmostEqual(snap.fiat_remaining, 73.298, places=4)
            self.assertAlmostEqual(snap.fiat_used, 261.472, places=4)
            self.assertAlmostEqual(snap.fiat_total, 334.77, places=4)
            self.assertAlmostEqual(snap.fiat_last_used_cost, 0.005, places=6)
            self.assertIn("10 🍪 = ¥1", snap.extra)
            self.assertEqual(snap.server_address, "https://cookie.example.test")
            self.assertEqual(len(snap.subscriptions), 2)
            sub_rem_sum = sum(s["remaining"] for s in snap.subscriptions)
            sub_fiat_sum = sum(s["fiat_remaining"] for s in snap.subscriptions)
            self.assertAlmostEqual(sub_rem_sum, 4180.0, places=2)
            self.assertAlmostEqual(sub_fiat_sum, 418.0, places=2)

            # Portfolio keeps pure fiat_remaining untouched while exposing fiat_remaining_including_custom
            s_cny = QuotaStationSpec(id="cny1", name="纯人民币站", adapter_id="deepseek", base_url="https://api.deepseek.com")
            snap_cny = QuotaSnapshot(station_id="cny1", ok=True, remaining=100.0, display_type="CNY", checked_at=now_dt.isoformat())
            summary = summarize_quota_portfolio([s_cny, spec], {"cny1": snap_cny, "sta1n": snap}, display_currency="CNY", usd_cny_rate=7.2)
            self.assertAlmostEqual(summary["fiat_remaining"], 100.0, places=3)
            self.assertAlmostEqual(summary["fiat_remaining_including_custom"], 173.298, places=3)
            self.assertEqual(summary["custom_converted_count"], 1)

            # 2) User 0.8x recharge override (12.5 🍪 = ¥1)
            spec_disc = QuotaStationSpec(
                id="sta1n",
                name="STA1N",
                adapter_id="newapi",
                base_url="https://cookie.example.test",
                fiat_currency="CNY",
                fiat_per_base_unit=0.8,
                display_unit_mode="fiat",
            )
            snap_disc = await adapter.probe(
                spec_disc,
                AdapterProbeContext(
                    base_url=spec_disc.base_url,
                    auth_token="tok-1",
                    fiat_currency=spec_disc.fiat_currency,
                    fiat_per_base_unit=spec_disc.fiat_per_base_unit,
                    now=lambda: now_dt,
                    session_factory=f_sta1n,
                ),
            )
            self.assertAlmostEqual(snap_disc.fiat_remaining, 73.298 * 0.8, places=4)
            self.assertIn("12.5 🍪 = ¥1", snap_disc.extra)

            # 3) Disk roundtrip preserves subscriptions & fiat derived fields
            with tempfile.TemporaryDirectory() as tmp:
                scache = QuotaSnapshotCache(Path(tmp))
                scache.put(snap)
                reloaded = QuotaSnapshotCache(Path(tmp)).get("sta1n")
                self.assertIsNotNone(reloaded)
                self.assertAlmostEqual(reloaded.fiat_remaining, 73.298, places=4)
                self.assertEqual(len(reloaded.subscriptions), 2)

                st_store = QuotaStationStore(Path(tmp))
                _, _, tok0 = st_store.read()
                created, _, _, tok1 = st_store.upsert_station(
                    tok0,
                    {
                        "id": "sta1n",
                        "name": "STA1N",
                        "adapter_id": "newapi",
                        "base_url": "https://cookie.example.test",
                        "fiat_currency": "CNY",
                        "fiat_per_base_unit": 0.8,
                        "display_unit_mode": "fiat",
                    },
                )
                self.assertEqual(created.display_unit_mode, "fiat")
                self.assertAlmostEqual(created.fiat_per_base_unit, 0.8)
                # Partial edit without dual-unit fields preserves existing settings
                edited, _, _, _ = st_store.upsert_station(
                    tok1,
                    {"id": "sta1n", "name": "STA1N 改名", "adapter_id": "newapi", "base_url": "https://cookie.example.test"},
                )
                self.assertEqual(edited.display_unit_mode, "fiat")
                self.assertAlmostEqual(edited.fiat_per_base_unit, 0.8)

            # 4) Paginated read-only consumption logs parse billing_source, cache_tokens, group_ratio & token_name
            f_logs, _ = _mock_factory({
                ("GET", "https://cookie.example.test/api/log/self?p=1&page_size=2&type=2&start_timestamp=1795000000"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "items": [
                                {
                                    "created_at": 1795470000,
                                    "model_name": "gemini-4-hydrogen",
                                    "token_name": "companion-key",
                                    "group": "default",
                                    "prompt_tokens": 10000,
                                    "completion_tokens": 500,
                                    "quota": 50000,
                                    "other": json.dumps({
                                        "billing_source": "subscription",
                                        "cache_tokens": 8000,
                                        "group_ratio": 1.0,
                                        "user_group_ratio": -1,
                                    }),
                                },
                                {
                                    "created_at": 1795460000,
                                    "model_name": "claude-sonnet",
                                    "token_name": "other-key",
                                    "group": "vip",
                                    "prompt_tokens": 2000,
                                    "completion_tokens": 1000,
                                    "quota": 100000,
                                    "other": json.dumps({
                                        "billing_source": "wallet",
                                        "cache_tokens": 500,
                                        "group_ratio": 0.65,
                                    }),
                                },
                            ]
                        },
                    },
                ),
                ("GET", "https://cookie.example.test/api/log/self?p=2&page_size=2&type=2&start_timestamp=1795000000"): _FakeResponse(
                    200, {"success": True, "data": {"items": []}}
                ),
            })
            logs_res = await fetch_recent_consumption_logs(
                "https://cookie.example.test",
                token="tok-1",
                per_unit=500000.0,
                display_type="CREDIT",
                display_multiplier=10.0,
                symbol="🍪",
                inferred_fiat_currency="CNY",
                effective_fiat_pbu=1.0,
                max_pages=3,
                page_size=2,
                start_timestamp=1795000000,
                session_factory=f_logs,
            )
            self.assertFalse(logs_res["counted_in_private_companion_budget"])
            self.assertEqual(len(logs_res["rows"]), 2)
            self.assertEqual(logs_res["rows"][0]["billing_source"], "subscription")
            self.assertEqual(logs_res["rows"][0]["cache_tokens"], 8000)
            self.assertAlmostEqual(logs_res["rows"][0]["cost"], 1.0, places=4)
            self.assertAlmostEqual(logs_res["rows"][0]["fiat_cost"], 0.1, places=4)
            self.assertEqual(logs_res["rows"][1]["billing_source"], "wallet")
            self.assertAlmostEqual(logs_res["rows"][1]["group_ratio"], 0.65, places=4)

            # 5) Overseas Stripe CUSTOM station with default RMB price=7.3 must NOT apply 7.3x as USD rate
            f_stripe, _ = _mock_factory({
                ("GET", "https://stripe-custom.example.test/api/status"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "system_name": "Global Relay",
                            "server_address": "https://stripe-custom.example.test",
                            "quota_display_type": "CUSTOM",
                            "quota_per_unit": 500000,
                            "custom_currency_symbol": "★",
                            "custom_currency_exchange_rate": 10,
                            "usd_exchange_rate": 7.3,
                            "price": 7.3,
                        },
                    },
                ),
                ("GET", "https://stripe-custom.example.test/api/user/self"): _FakeResponse(
                    200,
                    {"success": True, "data": {"quota": 5_000_000, "used_quota": 1_000_000, "group": "default"}},
                ),
                ("GET", "https://stripe-custom.example.test/api/log/self?p=1&page_size=1&type=2"): _FakeResponse(
                    200, {"success": True, "data": {"items": []}}
                ),
                ("GET", "https://stripe-custom.example.test/api/user/topup/info"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "price": 7.3,
                            "enable_stripe_topup": True,
                            "pay_methods": [{"type": "stripe", "name": "Stripe"}],
                        },
                    },
                ),
                ("GET", "https://stripe-custom.example.test/api/subscription/self"): _FakeResponse(
                    404, {"success": False}
                ),
            })
            spec_stripe = QuotaStationSpec(
                id="stripe-custom",
                name="Global Relay",
                adapter_id="newapi",
                base_url="https://stripe-custom.example.test",
            )
            snap_stripe = await adapter.probe(
                spec_stripe,
                AdapterProbeContext(base_url=spec_stripe.base_url, auth_token="tok-stripe", now=lambda: now_dt, session_factory=f_stripe),
            )
            self.assertTrue(snap_stripe.ok)
            self.assertEqual(snap_stripe.display_type, "CREDIT")
            self.assertEqual(snap_stripe.inferred_fiat_currency, "USD")
            self.assertAlmostEqual(snap_stripe.remaining, 100.0, places=4)
            self.assertIsNone(snap_stripe.fiat_remaining, "Default RMB price=7.3 must never inflate USD CUSTOM balance 7.3x")
            self.assertIsNone(snap_stripe.fiat_used)
            self.assertIsNone(snap_stripe.fiat_total)
            self.assertNotIn("$73", snap_stripe.extra)
            self.assertNotIn("参考充值约", snap_stripe.extra)

            # Explicit user fiat_per_base_unit override on USD CUSTOM station still works deterministically
            snap_stripe_manual = await adapter.probe(
                spec_stripe,
                AdapterProbeContext(
                    base_url=spec_stripe.base_url,
                    auth_token="tok-stripe",
                    fiat_currency="USD",
                    fiat_per_base_unit=1.0,
                    now=lambda: now_dt,
                    session_factory=f_stripe,
                ),
            )
            self.assertAlmostEqual(snap_stripe_manual.fiat_remaining, 10.0, places=4)
            self.assertIn("10 ★ = $1", snap_stripe_manual.extra)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
