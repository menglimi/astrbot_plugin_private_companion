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
    newapi_models_url,
    newapi_pricing_url,
    newapi_status_url,
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
    extract_newapi_pricing_meta,
    find_candidates,
    infer_newapi_cluster_group,
    parse_newapi_status,
    resolve_newapi_currency_params,
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

    def test_newapi_status_currency_and_unit_scale_resolution(self):
        # 1) CNY site with 1:1 display ratio
        st_cny_1 = parse_newapi_status({
            "success": True,
            "data": {
                "system_name": "示例公益站",
                "quota_display_type": "CNY",
                "usd_exchange_rate": 1,
                "quota_per_unit": 500000,
                "price": 1.0,
            },
        })
        self.assertEqual(st_cny_1["system_name"], "示例公益站")
        self.assertEqual(resolve_newapi_currency_params(st_cny_1), ("CNY", 1.0, 500_000.0))

        # 2) CNY site with FX rate (e.g. 7.3)
        st_cny_fx = parse_newapi_status({
            "success": True,
            "data": {"quota_display_type": "CNY", "usd_exchange_rate": 7.3, "quota_per_unit": 500000},
        })
        self.assertEqual(resolve_newapi_currency_params(st_cny_fx), ("CNY", 7.3, 500_000.0))
        # Explicit USD override ignores CNY FX scale!
        self.assertEqual(resolve_newapi_currency_params(st_cny_fx, currency_override="USD"), ("USD", 1.0, 500_000.0))

        # 3) USD site keeps scale=1.0 even when usd_exchange_rate is populated
        st_usd = parse_newapi_status({
            "success": True,
            "data": {"quota_display_type": "USD", "usd_exchange_rate": 7.2, "quota_per_unit": 1000000},
        })
        self.assertEqual(resolve_newapi_currency_params(st_usd), ("USD", 1.0, 1_000_000.0))

        # 4) CUSTOM virtual token site (custom_currency_exchange_rate=10) MUST NOT multiply by 10!
        st_custom = parse_newapi_status({
            "success": True,
            "data": {
                "system_name": "Custom Token Relay",
                "quota_display_type": "CUSTOM",
                "custom_currency_symbol": "🍪",
                "custom_currency_exchange_rate": 10,
                "usd_exchange_rate": 7.2,
                "price": 1.0,
            },
        })
        self.assertEqual(st_custom["custom_currency_symbol"], "🍪")
        self.assertEqual(resolve_newapi_currency_params(st_custom), ("USD", 1.0, 500_000.0))
        self.assertEqual(resolve_newapi_currency_params(st_custom, currency_override="CNY"), ("CNY", 1.0, 500_000.0))

        # 5) Missing / invalid status safely defaults to USD, scale=1.0, quota_per_unit=500k
        self.assertEqual(resolve_newapi_currency_params(None), ("USD", 1.0, 500_000.0))
        self.assertEqual(resolve_newapi_currency_params({}), ("USD", 1.0, 500_000.0))

    def test_adapt_newapi_billing_expr_lowering_cny_and_safe_fallback(self):
        payload = {
            "success": True,
            "group_ratio": {"default": 1.0, "gemini": 0.245, "geminiultra": 0.5},
            "data": [
                {
                    "model_name": "gemini-3.6-flash",
                    "quota_type": 0,
                    "billing_mode": "tiered_expr",
                    "model_ratio": 0.0,
                    "completion_ratio": 0.0,
                    "billing_expr": "v1:tier('标准', p * 0.75 + cr * 0.075 + c * 3.75)",
                    "enable_groups": ["gemini", "geminiultra"],
                },
                {
                    "model_name": "gemini-3.6-flash-as",
                    "quota_type": 1,
                    "model_price": 0.01,
                    "enable_groups": ["按次"],
                },
                {
                    "model_name": "gemini-3.1-pro",
                    "quota_type": 0,
                    # Intentionally omit billing_mode to verify standalone billing_expr support too
                    "billing_expr": (
                        "v1:len <= 200000 ? tier('<=200K', p * 2.0 + cr * 0.2 + c * 12.0)"
                        " : tier('>200K', p * 4.0 + cr * 0.4 + c * 18.0)"
                    ),
                    "enable_groups": ["geminiultra"],
                },
                {
                    "model_name": "claude-opus-4-6",
                    "quota_type": 0,
                    "billing_mode": "tiered_expr",
                    "billing_expr": "v1:tier('标准', p * 5 + cr * 0.5 + cc * 6.25 + cc1h * 10 + c * 25)",
                    "enable_groups": ["default"],
                },
                {
                    "model_name": "deepseek-v4-pro",
                    "quota_type": 0,
                    "billing_mode": "tiered_expr",
                    "model_ratio": 1.0,
                    "completion_ratio": 2.0,
                    "billing_expr": "v1:is_between('00:30','08:30') ? p * 1 + c * 2 : p * 2 + c * 4",
                },
                {
                    "model_name": "nai-diffusion-5-full",
                    "quota_type": 0,
                    "billing_mode": "tiered_expr",
                    "model_ratio": 0.0,
                    "model_price": 0.0,
                    "billing_expr": "v1:u('width') * u('height') * 0.00001",
                },
                {
                    "model_name": "evil-expr-model",
                    "quota_type": 0,
                    "billing_mode": "tiered_expr",
                    "model_ratio": 0.0,
                    "billing_expr": "__import__('os').system('whoami')",
                },
            ],
        }
        diag: dict[str, int] = {}
        items = adapt_newapi(
            payload,
            source_id="newapi:cny-relay",
            source_label="示例公益站",
            currency="CNY",
            unit_scale=1.0,
            quota_per_unit=500_000.0,
            diagnostics=diag,
        )
        by_model = {item.model_id: item for item in items}

        # Linear billing_expr lowered losslessly to per_token in CNY
        flash = by_model["gemini-3.6-flash"]
        self.assertEqual(flash.rule["mode"], "per_token")
        self.assertEqual(flash.rule["currency"], "CNY")
        self.assertAlmostEqual(flash.rule["input"], 0.75)
        self.assertAlmostEqual(flash.rule["input_cached"], 0.075)
        self.assertAlmostEqual(flash.rule["output"], 3.75)

        # Per-turn fixed price in CNY
        flash_as = by_model["gemini-3.6-flash-as"]
        self.assertEqual(flash_as.rule["mode"], "per_turn")
        self.assertEqual(flash_as.rule["currency"], "CNY")
        self.assertAlmostEqual(flash_as.rule["price"], 0.01)

        # Tiered len <= 200000 billing_expr lowered losslessly to per_tier in CNY
        pro = by_model["gemini-3.1-pro"]
        self.assertEqual(pro.rule["mode"], "per_tier")
        self.assertEqual(pro.rule["currency"], "CNY")
        self.assertAlmostEqual(pro.rule["base"]["input"], 2.0)
        self.assertAlmostEqual(pro.rule["base"]["output"], 12.0)
        self.assertEqual(len(pro.rule["context_tiers"]), 1)
        self.assertEqual(pro.rule["context_tiers"][0]["threshold_tokens"], 200000)
        self.assertAlmostEqual(pro.rule["context_tiers"][0]["input"], 4.0)
        self.assertAlmostEqual(pro.rule["context_tiers"][0]["output"], 18.0)

        # Complex cc1h expr stays tiered_expr with CNY preserved
        opus = by_model["claude-opus-4-6"]
        self.assertEqual(opus.rule["mode"], "tiered_expr")
        self.assertEqual(opus.rule["currency"], "CNY")
        self.assertIn("cc1h", opus.rule["expr"])

        # Unsupported billing_expr with positive model_ratio falls back cleanly to classic ratio
        ds = by_model["deepseek-v4-pro"]
        self.assertEqual(ds.rule["mode"], "per_token")
        self.assertEqual(ds.rule["currency"], "CNY")
        self.assertAlmostEqual(ds.rule["input"], 2.0)
        self.assertAlmostEqual(ds.rule["output"], 4.0)

        # Unsupported billing_expr with 0 ratio/price is safely skipped (NOT mislabeled ¥0 free!)
        self.assertNotIn("nai-diffusion-5-full", by_model)
        self.assertNotIn("evil-expr-model", by_model)
        self.assertEqual(diag.get("skipped_unsupported_expr"), 2)

        # Non-default quota_per_unit (1_000_000) and unit_scale (7.3) apply cleanly to classic items
        scaled = adapt_newapi(
            {"success": True, "data": [{"model_name": "m1", "quota_type": 0, "model_ratio": 1.0, "completion_ratio": 3.0}]},
            source_id="newapi:custom-qpu",
            source_label="Custom",
            currency="CNY",
            unit_scale=7.3,
            quota_per_unit=1_000_000.0,
        )
        self.assertAlmostEqual(scaled[0].rule["input"], 7.3)
        self.assertAlmostEqual(scaled[0].rule["output"], 21.9)

    def test_candidate_scoring_anti_mismatch_per_call_and_bracket_prefix(self):
        # Asymmetric suffix guard blocks per-token models from matching -as/-once/-search/-image/-custom
        for cand_model in (
            "gemini-3.6-flash-as",
            "gemini-3.6-flash-once",
            "gemini-3.6-flash-search",
            "gemini-3.6-flash-image",
            "gemini-3.6-flash-custom",
        ):
            self.assertLess(
                score_candidate_match("gemini-3.6-flash", cand_model),
                0.85,
                f"gemini-3.6-flash must NOT auto-match {cand_model}",
            )

        # Per-call bracket tag [次] is preserved and never collapses into per-token model
        self.assertLess(
            score_candidate_match("[AN]gemini-4-hydrogen-high", "[次][AN]gemini-4-hydrogen-high"),
            0.85,
        )
        self.assertEqual(
            score_candidate_match("[次][AN]gemini-4-hydrogen-high", "[次][AN]gemini-4-hydrogen-high"),
            1.0,
        )
        # Normal decorative bracket prefixes still strip cleanly for matching
        self.assertGreaterEqual(
            score_candidate_match("[抗截断]gemini-3.6-flash", "gemini-3.6-flash"),
            0.90,
        )

        # select_auto_candidate: per_turn below 0.96 is refused even on own gateway; exact 1.0 per_turn succeeds
        cat_per_turn = {
            "prices": {
                "newapi:gw:gemini-3.6-flash-as": CatalogPrice(
                    price_key="newapi:gw:gemini-3.6-flash-as",
                    source_id="newapi:gw",
                    source_label="GW",
                    model_id="gemini-3.6-flash-as",
                    vendor="GW",
                    display_name="gemini-3.6-flash-as",
                    rule={"mode": "per_turn", "currency": "CNY", "price": 0.01},
                ).to_dict(),
            }
        }
        cands_token_query = find_candidates("gemini-3.6-flash", catalog=cat_per_turn, provider_id="gw")
        self.assertIsNone(select_auto_candidate(cands_token_query))
        cands_exact_as = find_candidates("gemini-3.6-flash-as", catalog=cat_per_turn, provider_id="gw")
        self.assertEqual(select_auto_candidate(cands_exact_as).price_key, "newapi:gw:gemini-3.6-flash-as")

        # Weak own-gateway prefix match (~0.88) must NOT beat 1.0 exact public match,
        # while strong own-gateway exact (1.0) wins over public 1.0
        cat_mixed = {
            "prices": {
                "modelsdev:google/gemini-3.6-flash-v2": CatalogPrice(
                    price_key="modelsdev:google/gemini-3.6-flash-v2",
                    source_id="modelsdev",
                    source_label="models.dev",
                    model_id="gemini-3.6-flash-v2",
                    vendor="Google",
                    display_name="Gemini 3.6 Flash v2",
                    rule={"mode": "per_token", "currency": "USD", "input": 0.15, "output": 0.6},
                ).to_dict(),
                "newapi:gw:gemini-3.6-flash": CatalogPrice(
                    price_key="newapi:gw:gemini-3.6-flash",
                    source_id="newapi:gw",
                    source_label="GW",
                    model_id="gemini-3.6-flash",
                    vendor="GW",
                    display_name="gemini-3.6-flash",
                    rule={"mode": "per_token", "currency": "CNY", "input": 0.75, "output": 3.75},
                ).to_dict(),
            }
        }
        cands_mixed = find_candidates("gemini-3.6-flash-v2", catalog=cat_mixed, provider_id="gw")
        self.assertEqual(select_auto_candidate(cands_mixed).source_id, "modelsdev")

        # Adding exact own-gateway match wins immediately
        cat_mixed["prices"]["newapi:gw:gemini-3.6-flash-v2"] = CatalogPrice(
            price_key="newapi:gw:gemini-3.6-flash-v2",
            source_id="newapi:gw",
            source_label="GW",
            model_id="gemini-3.6-flash-v2",
            vendor="GW",
            display_name="gemini-3.6-flash-v2",
            rule={"mode": "per_token", "currency": "CNY", "input": 0.75, "output": 3.75},
        ).to_dict()
        cands_exact_own = find_candidates("gemini-3.6-flash-v2", catalog=cat_mixed, provider_id="gw")
        self.assertEqual(select_auto_candidate(cands_exact_own).price_key, "newapi:gw:gemini-3.6-flash-v2")


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
            pricing_calls = [c for c in recorded_calls if c["url"].endswith("/api/pricing")]
            status_calls = [c for c in recorded_calls if c["url"].endswith("/api/status")]
            self.assertEqual(len(pricing_calls), 2)
            self.assertEqual(len(status_calls), 1)
            # Anonymous pricing, Bearer pricing, and anonymous status calls all enforce allow_redirects=False
            self.assertIs(pricing_calls[0].get("allow_redirects"), False)
            self.assertIs(pricing_calls[1].get("allow_redirects"), False)
            self.assertIs(status_calls[0].get("allow_redirects"), False)
            self.assertEqual(pricing_calls[1]["headers"]["Authorization"], "Bearer sk-secret-key-123")
            self.assertNotIn("Authorization", status_calls[0].get("headers") or {})
            # Proxy fallback applied without leaking into stored settings/catalog!
            self.assertEqual(pricing_calls[0]["proxy"], "http://proxyuser:proxypass@127.0.0.1:7890")
            raw_files = (settings_store.path.read_text("utf-8") + catalog_store.path.read_text("utf-8"))
            self.assertNotIn("sk-secret-key-123", raw_files)
            self.assertNotIn("proxyuser:proxypass", raw_files)

            # 3) Second provider sharing same api_base with NO key deduplicates to newapi:prov-a
            #    and preserves original label/provider_id AND cached prices!
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
            self.assertEqual(det2["status"]["model_count"], 1)  # Existing cached price preserved!

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

    async def test_newapi_same_domain_multi_cluster_group_inference_bearer_isolation_and_currency_override(self):
        self.assertEqual(newapi_status_url("https://cny-relay.example.test/v1"), "https://cny-relay.example.test/api/status")
        self.assertEqual(newapi_models_url("https://cny-relay.example.test/v1"), "https://cny-relay.example.test/v1/models")

        with TemporaryDirectory(prefix="companion-cat-multi-") as tmp:
            root = Path(tmp)
            settings_store = PricingSettingsStore(root)
            catalog_store = PriceCatalogStore(root)

            cny_pricing = {
                "success": True,
                "group_ratio": {"default": 1.0, "gemini": 0.245, "geminiultra": 0.5, "按次": 1.0},
                "usable_group": {"gemini": "Gemini 按量", "geminiultra": "Gemini Ultra", "按次": "按次计费"},
                "auto_groups": ["gemini", "geminiultra"],
                "data": [
                    {
                        "model_name": "gemini-3.6-flash",
                        "quota_type": 0,
                        "billing_mode": "tiered_expr",
                        "billing_expr": "v1:tier('标准', p * 0.75 + cr * 0.075 + c * 3.75)",
                        "enable_groups": ["gemini", "geminiultra"],
                    },
                    {
                        "model_name": "gemini-3.6-flash-lite",
                        "quota_type": 0,
                        "billing_mode": "tiered_expr",
                        "billing_expr": "v1:tier('标准', p * 0.3 + c * 1.2)",
                        "enable_groups": ["gemini"],
                    },
                    {
                        "model_name": "gemini-3.6-flash-as",
                        "quota_type": 1,
                        "model_price": 0.01,
                        "enable_groups": ["按次"],
                    },
                ],
            }
            cny_status = {
                "success": True,
                "data": {
                    "system_name": "示例公益站",
                    "quota_display_type": "CNY",
                    "usd_exchange_rate": 1,
                    "quota_per_unit": 500000,
                },
            }

            def public_handler(url, kwargs):
                if url.endswith("/api/pricing"):
                    if (kwargs.get("headers") or {}).get("If-None-Match") == '"etag-cny-v1"':
                        return _FakeHttpResponse(304, b"", headers={"ETag": '"etag-cny-v1"'})
                    return _FakeHttpResponse(
                        200,
                        json.dumps(cny_pricing).encode("utf-8"),
                        headers={"ETag": '"etag-cny-v1"'},
                    )
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(200, json.dumps(cny_status).encode("utf-8"))
                if url.endswith("/v1/models"):
                    auth = (kwargs.get("headers") or {}).get("Authorization", "")
                    if "sk-key-token" in auth:
                        return _FakeHttpResponse(
                            200,
                            json.dumps({"data": [{"id": "gemini-3.6-flash"}, {"id": "gemini-3.6-flash-lite"}]}).encode("utf-8"),
                        )
                    return _FakeHttpResponse(
                        200,
                        json.dumps({"data": [{"id": "gemini-3.6-flash-as"}]}).encode("utf-8"),
                    )
                return _FakeHttpResponse(404, b"not found")

            svc = PricingCatalogService(
                settings_store,
                catalog_store,
                session_factory=lambda _t: _FakeHttpSession(public_handler),
                retry_base_seconds=0.0,
            )

            # 1) Cluster A ("中转按次") probes first -> captures ALL 3 models in CNY & site system_name label!
            det_a = await svc.detect_newapi_source(
                provider_id="中转按次/gemini-3.6-flash-as",
                cluster_id="cluster-percall",
                api_base="https://cny-relay.example.test/v1",
                api_key="sk-key-percall",
                supplier_label="中转按次",
            )
            self.assertTrue(det_a["ok"])
            self.assertFalse(det_a["deduplicated"])
            self.assertEqual(det_a["models"], 3)
            self.assertEqual(det_a["currency"], "CNY")
            self.assertEqual(det_a["site_name"], "示例公益站")
            self.assertEqual(det_a["status"]["label"], "示例公益站")
            self.assertEqual(det_a["detected_group"], "按次")
            self.assertEqual(det_a["detected_group_ratio"], 1.0)

            # 2) Cluster B ("中转按量") on same domain deduplicates, infers group "gemini" (0.245x),
            #    and NEVER silently mutates pricing_multipliers!
            det_b = await svc.detect_newapi_source(
                provider_id="中转按量/gemini-3.6-flash",
                cluster_id="cluster-pertoken",
                api_base="https://cny-relay.example.test/v1",
                api_key="sk-key-token",
                supplier_label="中转按量",
            )
            self.assertTrue(det_b["ok"])
            self.assertTrue(det_b["deduplicated"])
            self.assertEqual(det_b["source_id"], det_a["source_id"])
            self.assertEqual(det_b["detected_group"], "gemini")
            self.assertAlmostEqual(det_b["detected_group_ratio"], 0.245)
            self.assertEqual(settings_store.read()["pricing_multipliers"], {})
            cat = catalog_store.read_safe()
            flash_price = cat["prices"][f"{det_a['source_id']}:gemini-3.6-flash"]
            self.assertEqual(flash_price["rule"]["mode"], "per_token")
            self.assertEqual(flash_price["rule"]["currency"], "CNY")
            self.assertAlmostEqual(flash_price["rule"]["input"], 0.75)

            # 3) Switching currency_override -> "USD" bypasses stale ETag & re-adapts cached prices to USD!
            settings_store.set_source_currency_override(det_a["source_id"], "USD")
            sync_res = await svc.sync_sources(source_ids=[det_a["source_id"]])
            self.assertEqual(sync_res["results"][0]["status"], "ok")
            self.assertEqual(sync_res["results"][0]["currency"], "USD")
            self.assertEqual(sync_res["results"][0]["currency_override"], "USD")
            cat_usd = catalog_store.read_safe()
            self.assertEqual(cat_usd["prices"][f"{det_a['source_id']}:gemini-3.6-flash"]["rule"]["currency"], "USD")

            # 4) Auth-gated Bearer gateway isolates distinct clusters with their own keys!
            def private_bearer_handler(url, kwargs):
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(404, b"no status")
                auth = (kwargs.get("headers") or {}).get("Authorization", "")
                if not auth:
                    return _FakeHttpResponse(401, b"auth required")
                if "sk-priv-a" in auth:
                    return _FakeHttpResponse(
                        200,
                        json.dumps({"success": True, "data": [{"model_name": "model-a", "quota_type": 0, "model_ratio": 1.0, "completion_ratio": 2.0}]}).encode("utf-8"),
                    )
                if "sk-priv-b" in auth:
                    return _FakeHttpResponse(
                        200,
                        json.dumps({"success": True, "data": [{"model_name": "model-b", "quota_type": 0, "model_ratio": 3.0, "completion_ratio": 2.0}]}).encode("utf-8"),
                    )
                return _FakeHttpResponse(403, b"forbidden")

            svc._session_factory = lambda _t: _FakeHttpSession(private_bearer_handler)
            p_a = await svc.detect_newapi_source(
                provider_id="priv-a/model-a",
                cluster_id="cluster-priv-a",
                api_base="https://private.example.test/v1",
                api_key="sk-priv-a",
                supplier_label="Private A",
            )
            p_b = await svc.detect_newapi_source(
                provider_id="priv-b/model-b",
                cluster_id="cluster-priv-b",
                api_base="https://private.example.test/v1",
                api_key="sk-priv-b",
                supplier_label="Private B",
            )
            self.assertTrue(p_a["ok"])
            self.assertTrue(p_b["ok"])
            self.assertFalse(p_b["deduplicated"])
            self.assertNotEqual(p_a["source_id"], p_b["source_id"])
            self.assertEqual(p_a["status"]["auth_mode"], "bearer")
            self.assertEqual(p_b["status"]["auth_mode"], "bearer")
            cat_priv = catalog_store.read_safe()
            self.assertIn(f"{p_a['source_id']}:model-a", cat_priv["prices"])
            self.assertIn(f"{p_b['source_id']}:model-b", cat_priv["prices"])


if __name__ == "__main__":
    unittest.main()
