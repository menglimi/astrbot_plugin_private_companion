"""Tests for pure price catalog adapters, candidate scorer, atomic catalog store,
and proxy-safe multi-source HTTP sync / New API probing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import aiohttp

from astrbot_host.pricing_sources import (
    MAX_CATALOG_RESPONSE_BYTES,
    PricingCatalogService,
    derive_newapi_root,
    newapi_pricing_url,
    normalized_origin,
    same_origin,
)
from companion_v2.model_gateway.pricing import PricingSettingsStore
from companion_v2.model_gateway.pricing_catalog import (
    CatalogPrice,
    PriceCatalogStore,
    SourceStatus,
    adapt_litellm,
    adapt_modelsdev,
    adapt_newapi,
    adapt_openrouter,
    find_candidates,
    score_candidate_match,
    select_auto_candidate,
)


class _ChunkedContent:
    """Simulates real aiohttp StreamReader yielding multiple small chunks via iter_chunked."""

    def __init__(self, payload: bytes, chunk_size: int = 16_384):
        self._payload = payload
        self._chunk_size = chunk_size

    async def iter_chunked(self, n: int):
        step = min(self._chunk_size, n)
        for idx in range(0, len(self._payload), step):
            yield self._payload[idx : idx + step]


class _FakeHttpResponse:
    def __init__(self, status: int, payload: bytes = b"", headers: dict | None = None, chunk_size: int = 16_384):
        self.status = status
        self.headers = headers or {}
        self.content = _ChunkedContent(payload, chunk_size=chunk_size)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeHttpSession:
    def __init__(self, handler):
        self._handler = handler
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._handler(url, kwargs)


class CatalogAdaptersAndScorerTests(unittest.TestCase):
    def test_adapt_modelsdev_per_token_and_per_tier_with_fast_priority(self):
        payload = {
            "openai": {
                "name": "OpenAI",
                "models": {
                    "gpt-4o": {
                        "id": "gpt-4o",
                        "name": "GPT-4o",
                        "cost": {"input": 2.5, "output": 10.0, "cache_read": 1.25},
                        "limit": {"context": 128000, "output": 16384},
                    },
                },
            },
            "google": {
                "name": "Google",
                "models": {
                    "gemini-2.5-pro": {
                        "id": "gemini-2.5-pro",
                        "name": "Gemini 2.5 Pro",
                        "cost": {
                            "input": 1.25,
                            "output": 10.0,
                            "cache_read": 0.125,
                            "context_over_200k": {"input": 2.5, "output": 15.0, "cache_read": 0.25},
                        },
                        "experimental": {
                            "modes": {
                                "fast": {"cost": {"input": 2.5, "output": 20.0}},
                            },
                        },
                    },
                },
            },
        }
        items = adapt_modelsdev(payload, updated_at="2026-11-21T00:00:00Z")
        by_key = {item.price_key: item for item in items}
        self.assertIn("modelsdev:openai/gpt-4o", by_key)
        self.assertEqual(by_key["modelsdev:openai/gpt-4o"].rule["mode"], "per_token")
        self.assertEqual(by_key["modelsdev:openai/gpt-4o"].rule["input"], 2.5)

        gem = by_key["modelsdev:google/gemini-2.5-pro"]
        self.assertEqual(gem.rule["mode"], "per_tier")
        self.assertEqual(gem.rule["context_tiers"][0]["threshold_tokens"], 200_000)
        self.assertEqual(gem.rule["context_tiers"][0]["input"], 2.5)
        self.assertEqual(gem.rule["service_tiers"][0]["match"], "priority")
        self.assertEqual(gem.rule["service_tiers"][0]["input_multiplier"], 2.0)

    def test_adapt_litellm_and_openrouter(self):
        litellm_payload = {
            "sample_spec": {"input_cost_per_token": 0.00001, "output_cost_per_token": 0.00002},
            "text-embedding-3-small": {"mode": "embedding", "input_cost_per_token": 0.00000002},
            "claude-sonnet-4-5": {
                "mode": "chat",
                "litellm_provider": "anthropic",
                "input_cost_per_token": 3e-6,
                "output_cost_per_token": 15e-6,
                "cache_read_input_token_cost": 0.3e-6,
                "cache_creation_input_token_cost": 3.75e-6,
                "input_cost_per_token_above_200k_tokens": 6e-6,
                "output_cost_per_token_above_200k_tokens": 22.5e-6,
            },
        }
        litellm_items = adapt_litellm(litellm_payload)
        self.assertEqual(len(litellm_items), 1)
        sonnet = litellm_items[0]
        self.assertEqual(sonnet.rule["mode"], "per_tier")
        self.assertEqual(sonnet.rule["base"]["input"], 3.0)
        self.assertEqual(sonnet.rule["base"]["cache_creation"], 3.75)
        self.assertEqual(sonnet.rule["context_tiers"][0]["threshold_tokens"], 200_000)
        self.assertEqual(sonnet.rule["context_tiers"][0]["input"], 6.0)

        openrouter_payload = {
            "data": [
                {
                    "id": "openai/gpt-4o-mini",
                    "name": "GPT-4o Mini",
                    "context_length": 128000,
                    "pricing": {"prompt": "0.00000015", "completion": "0.0000006", "input_cache_read": "0.000000075"},
                },
                {
                    "id": "dynamic/unpriced",
                    "pricing": {"prompt": "-1", "completion": "-1"},
                },
            ]
        }
        or_items = adapt_openrouter(openrouter_payload)
        self.assertEqual(len(or_items), 1)
        self.assertEqual(or_items[0].rule["input"], 0.15)
        self.assertEqual(or_items[0].rule["output"], 0.6)
        self.assertEqual(or_items[0].rule["input_cached"], 0.075)

    def test_adapt_newapi_all_billing_modes_and_ignores_group_ratio(self):
        payload = {
            "success": True,
            "group_ratio": {"default": 1, "vip": 0.5},
            "data": [
                {
                    "model_name": "gpt-4o",
                    "quota_type": 0,
                    "model_ratio": 1.25,  # 1.25 * 2 = $2.5/1M
                    "completion_ratio": 4.0,  # 2.5 * 4 = $10/1M
                    "cache_ratio": 0.5,  # $1.25/1M
                    # create_cache_ratio omitted -> defaults to 1.25x ($3.125/1M)
                    "enable_groups": ["vip"],
                },
                {
                    "model_name": "free-write-model",
                    "quota_type": 0,
                    "model_ratio": 1.0,
                    "completion_ratio": 2.0,
                    "create_cache_ratio": 0.0,  # Explicit 0 preserved!
                },
                {
                    "model_name": "fixed-turn-model",
                    "quota_type": 1,
                    "model_price": 0.02,
                },
                {
                    "model_name": "expr-valid-model",
                    "billing_mode": "tiered_expr",
                    "quota_conversion": "v1:len > 200000 ? p * 4 + c * 16 : p * 2 + c * 8",
                },
                {
                    "model_name": "expr-camel-model",
                    "billing_mode": "tiered_expr",
                    "quotaConversion": "v1:p * 2 + c * 8",
                },
                {
                    "model_name": "expr-malicious-model",
                    "billing_mode": "tiered_expr",
                    "quota_conversion": "__import__('os').system('echo bad')",
                },
            ],
        }
        items = adapt_newapi(payload, source_id="newapi:gateway-1", source_label="Gateway 1")
        by_model = {item.model_id: item for item in items}
        self.assertNotIn("expr-malicious-model", by_model)  # Rejected at pre-validation!
        self.assertEqual(by_model["gpt-4o"].rule["input"], 2.5)
        self.assertEqual(by_model["gpt-4o"].rule["output"], 10.0)
        self.assertEqual(by_model["gpt-4o"].rule["input_cached"], 1.25)
        self.assertEqual(by_model["gpt-4o"].rule["cache_creation"], 3.125)
        self.assertEqual(by_model["free-write-model"].rule["cache_creation"], 0.0)
        self.assertEqual(by_model["fixed-turn-model"].rule["mode"], "per_turn")
        self.assertEqual(by_model["fixed-turn-model"].rule["price"], 0.02)
        self.assertEqual(by_model["expr-valid-model"].rule["mode"], "tiered_expr")
        self.assertEqual(by_model["expr-camel-model"].rule["mode"], "tiered_expr")
        self.assertEqual(by_model["expr-camel-model"].rule["expr"], "v1:p * 2 + c * 8")

    def test_candidate_scoring_variant_guard_and_own_gateway_pinning(self):
        # Variant guard prevents gpt-4o-mini from auto-matching gpt-4o
        self.assertLess(score_candidate_match("gpt-4o-mini", "gpt-4o"), 0.85)
        self.assertLess(score_candidate_match("gemini-2.5-flash-lite", "gemini-2.5-flash"), 0.85)
        self.assertGreaterEqual(score_candidate_match("openai/gpt-4o-2024-11-20", "gpt-4o"), 0.85)
        self.assertGreaterEqual(score_candidate_match("anthropic/claude-3.5-sonnet", "claude-3-5-sonnet"), 0.90)

        catalog = {
            "prices": {
                "modelsdev:openai/gpt-4o": CatalogPrice(
                    price_key="modelsdev:openai/gpt-4o", source_id="modelsdev", source_label="models.dev",
                    model_id="gpt-4o", vendor="OpenAI", display_name="GPT-4o",
                    rule={"mode": "per_token", "currency": "USD", "input": 2.5, "output": 10.0},
                ).to_dict(),
                "newapi:gw1:gpt-4o": CatalogPrice(
                    price_key="newapi:gw1:gpt-4o", source_id="newapi:gw1", source_label="GW1",
                    model_id="gpt-4o", vendor="GW1", display_name="gpt-4o",
                    rule={"mode": "per_token", "currency": "USD", "input": 1.0, "output": 4.0},
                ).to_dict(),
            }
        }
        cands = find_candidates("gpt-4o", catalog=catalog, provider_id="gw1")
        self.assertEqual(cands[0].source_id, "newapi:gw1")
        self.assertTrue(cands[0].is_own_gateway)
        auto = select_auto_candidate(cands)
        self.assertEqual(auto.price_key, "newapi:gw1:gpt-4o")


class PricingCatalogServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_multi_chunk_no_truncation_and_16mb_cap(self):
        with TemporaryDirectory(prefix="companion-cat-stream-") as tmp:
            root = Path(tmp)
            settings_store = PricingSettingsStore(root)
            catalog_store = PriceCatalogStore(root)
            # Generate a ~180 KB JSON payload split across tiny 8 KB chunks to verify zero truncation!
            models_dict = {
                f"model-{i}": {"id": f"model-{i}", "name": f"Model {i}", "cost": {"input": 1.0, "output": 2.0}}
                for i in range(1200)
            }
            large_bytes = json.dumps({"openai": {"name": "OpenAI", "models": models_dict}}).encode("utf-8")
            self.assertGreater(len(large_bytes), 100_000)

            def handler(url, _kwargs):
                if "models.dev" in url:
                    return _FakeHttpResponse(200, large_bytes, headers={"ETag": '"v1-etag"'}, chunk_size=8192)
                if "litellm" in url:
                    # Exceed 16 MB cap!
                    return _FakeHttpResponse(200, b"x" * (MAX_CATALOG_RESPONSE_BYTES + 1024), chunk_size=65536)
                return _FakeHttpResponse(200, json.dumps({"data": []}).encode("utf-8"))

            session = _FakeHttpSession(handler)
            svc = PricingCatalogService(
                settings_store,
                catalog_store,
                session_factory=lambda _t: session,
                retry_base_seconds=0.0,
                sleep_fn=lambda _s: asyncio.sleep(0),
            )
            res = await svc.sync_sources()
            by_sid = {item["source_id"]: item for item in res["results"]}
            self.assertEqual(by_sid["modelsdev"]["status"], "ok")
            self.assertEqual(by_sid["modelsdev"]["model_count"], 1200)
            self.assertEqual(by_sid["modelsdev"]["etag"], '"v1-etag"')
            # 16 MB cap rejected cleanly without crashing other sources
            self.assertEqual(by_sid["litellm"]["status"], "error")
            self.assertIn("16 MB", by_sid["litellm"]["error"])
            # Atomic write leaves no stray temp files
            files = sorted(p.name for p in catalog_store.directory.iterdir())
            self.assertEqual(files, ["catalog.json"])

            # Next sync sends If-None-Match and honors 304 preserving all 1200 cached models!
            seen_headers = {}

            def handler_304(url, kwargs):
                if "models.dev" in url:
                    seen_headers.update(kwargs.get("headers") or {})
                    return _FakeHttpResponse(304, b"", headers={"ETag": '"v1-etag"'})
                return _FakeHttpResponse(503, b"down")

            svc._session_factory = lambda _t: _FakeHttpSession(handler_304)
            res2 = await svc.sync_sources(source_ids=["modelsdev"])
            self.assertEqual(seen_headers.get("If-None-Match"), '"v1-etag"')
            self.assertEqual(res2["results"][0]["status"], "ok")
            self.assertEqual(res2["results"][0]["model_count"], 1200)
            self.assertEqual(len(catalog_store.read_safe()["prices"]), 1200)

    async def test_transient_retry_with_jitter_recovers_on_third_attempt(self):
        with TemporaryDirectory(prefix="companion-cat-retry-") as tmp:
            root = Path(tmp)
            attempts = {"count": 0}
            sleeps: list[float] = []

            async def fake_sleep(delay: float):
                sleeps.append(delay)

            def handler(_url, _kwargs):
                attempts["count"] += 1
                if attempts["count"] <= 2:
                    raise aiohttp.ClientConnectionError("transient dns error")
                payload = {"data": [{"id": "openai/gpt-4o", "pricing": {"prompt": "0.0000025", "completion": "0.00001"}}]}
                return _FakeHttpResponse(200, json.dumps(payload).encode("utf-8"))

            svc = PricingCatalogService(
                PricingSettingsStore(root),
                PriceCatalogStore(root),
                session_factory=lambda _t: _FakeHttpSession(handler),
                max_retries=2,
                retry_base_seconds=0.1,
                sleep_fn=fake_sleep,
            )
            res = await svc.sync_sources(source_ids=["openrouter"])
            self.assertEqual(attempts["count"], 3)
            self.assertEqual(len(sleeps), 2)
            self.assertEqual(res["results"][0]["status"], "ok")
            self.assertEqual(res["results"][0]["model_count"], 1)

    async def test_newapi_same_origin_bearer_guard_allow_redirects_false_and_url_dedup(self):
        self.assertEqual(derive_newapi_root("https://api.example.test/v1/"), "https://api.example.test")
        self.assertEqual(newapi_pricing_url("https://user:pass@api.example.test:8443/sub/v1"), "https://api.example.test:8443/sub/api/pricing")
        self.assertTrue(same_origin("https://api.example.test/api/pricing", "https://api.example.test:443/v1"))
        self.assertFalse(same_origin("https://evil.example.test/api/pricing", "https://api.example.test/v1"))
        self.assertEqual(normalized_origin("http://127.0.0.1/v1"), ("http", "127.0.0.1", 80))

        with TemporaryDirectory(prefix="companion-cat-newapi-") as tmp:
            root = Path(tmp)
            settings_store = PricingSettingsStore(root)
            catalog_store = PriceCatalogStore(root)
            recorded_calls: list[dict] = []

            def handler(url, kwargs):
                recorded_calls.append({"url": url, **kwargs})
                auth = (kwargs.get("headers") or {}).get("Authorization", "")
                if not auth:
                    # Anonymous returns 0 models -> triggers same-origin Bearer retry!
                    return _FakeHttpResponse(200, json.dumps({"success": True, "data": []}).encode("utf-8"))
                return _FakeHttpResponse(
                    200,
                    json.dumps({
                        "success": True,
                        "data": [{"model_name": "gpt-4o", "quota_type": 0, "model_ratio": 1.0, "completion_ratio": 3.0}],
                    }).encode("utf-8"),
                )

            svc = PricingCatalogService(
                settings_store,
                catalog_store,
                session_factory=lambda _t: _FakeHttpSession(handler),
                proxy_resolver=lambda: "http://proxyuser:proxypass@127.0.0.1:7890",
                retry_base_seconds=0.0,
            )

            # 1) Direct cross-origin Bearer refusal
            with self.assertRaises(ValueError):
                async with _FakeHttpSession(handler) as sess:
                    await svc._fetch_json_with_retry(
                        sess,
                        "https://other.example.test/api/pricing",
                        bearer_key="sk-secret-key",
                        origin_guard_base="https://api.example.test/v1",
                    )

            # 2) Probe provider A -> anonymous 0 models -> retries WITH same-origin Bearer + allow_redirects=False!
            det1 = await svc.detect_newapi_source(
                provider_id="prov-a",
                api_base="https://api.example.test/v1",
                api_key="sk-secret-key-123",
                supplier_label="Example Relay",
            )
            self.assertTrue(det1["ok"])
            self.assertFalse(det1["deduplicated"])
            self.assertEqual(det1["source_id"], "newapi:prov-a")
            self.assertEqual(det1["models"], 1)
            self.assertFalse(det1["needs_key"])
            self.assertEqual(len(recorded_calls), 2)
            # Both anonymous and Bearer calls enforce allow_redirects=False
            self.assertIs(recorded_calls[0].get("allow_redirects"), False)
            self.assertIs(recorded_calls[1].get("allow_redirects"), False)
            self.assertEqual(recorded_calls[1]["headers"]["Authorization"], "Bearer sk-secret-key-123")
            # Proxy fallback applied without leaking into stored settings/catalog!
            self.assertEqual(recorded_calls[0]["proxy"], "http://proxyuser:proxypass@127.0.0.1:7890")
            raw_files = (settings_store.path.read_text("utf-8") + catalog_store.path.read_text("utf-8"))
            self.assertNotIn("sk-secret-key-123", raw_files)
            self.assertNotIn("proxyuser:proxypass", raw_files)

            # 3) Second provider sharing same api_base deduplicates to newapi:prov-a & preserves original label/provider_id!
            det2 = await svc.detect_newapi_source(
                provider_id="prov-b",
                api_base="https://api.example.test/v1/",
                api_key="",
                supplier_label="Example Relay B",
            )
            self.assertTrue(det2["ok"])
            self.assertTrue(det2["deduplicated"])
            self.assertEqual(det2["source_id"], "newapi:prov-a")
            self.assertTrue(det2["needs_key"])  # Anonymous returned 0 models and no key passed
            self.assertEqual(det2["status"]["label"], "Example Relay")
            self.assertEqual(det2["status"]["provider_id"], "prov-a")

            # 4) Anonymous 401 -> Bearer retry ALSO 401 -> returns needs_key=True cleanly!
            svc._session_factory = lambda _t: _FakeHttpSession(
                lambda _u, _kw: _FakeHttpResponse(401, b"unauthorized")
            )
            det401 = await svc.detect_newapi_source(
                provider_id="prov-auth-fail",
                api_base="https://auth.example.test/v1",
                api_key="sk-bad-key",
                supplier_label="Auth Gateway",
            )
            self.assertFalse(det401["ok"])
            self.assertTrue(det401["needs_key"])


if __name__ == "__main__":
    unittest.main()
