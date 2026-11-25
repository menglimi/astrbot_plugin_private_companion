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
    find_newapi_sources_needing_backfill,
    newapi_models_url,
    newapi_pricing_url,
    newapi_status_url,
    normalized_origin,
    same_origin,
)
from companion_v2.model_gateway.pricing import PricingSettingsStore
from companion_v2.model_gateway.pricing_catalog import (
    NEWAPI_SITE_META_SCHEMA_VERSION,
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
    parse_newapi_topup_info,
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
        # 1) Level 1: CNY site with 1:1 display ratio
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
        self.assertEqual(st_cny_1["schema_version"], NEWAPI_SITE_META_SCHEMA_VERSION)
        self.assertEqual(st_cny_1["currency_source"], "status_cny")
        self.assertEqual(resolve_newapi_currency_params(st_cny_1), ("CNY", 1.0, 500_000.0))

        # 1B) Level 1 protection: CNY with usd_exchange_rate=1 and default price=7.3 MUST NOT multiply by price!
        st_cny_default_price = parse_newapi_status({
            "success": True,
            "data": {
                "system_name": "默认充值价中文站",
                "quota_display_type": "CNY",
                "usd_exchange_rate": 1,
                "quota_per_unit": 500000,
                "price": 7.3,
            },
        })
        self.assertEqual(resolve_newapi_currency_params(st_cny_default_price), ("CNY", 1.0, 500_000.0))

        # 2) Level 1: CNY site with FX rate (e.g. 7.3)
        st_cny_fx = parse_newapi_status({
            "success": True,
            "data": {"quota_display_type": "CNY", "usd_exchange_rate": 7.3, "quota_per_unit": 500000},
        })
        self.assertEqual(resolve_newapi_currency_params(st_cny_fx), ("CNY", 7.3, 500_000.0))
        # Explicit USD override ignores CNY FX scale!
        self.assertEqual(resolve_newapi_currency_params(st_cny_fx, currency_override="USD"), ("USD", 1.0, 500_000.0))

        # 3) Level 3: genuine USD site keeps scale=1.0 even when usd_exchange_rate & default price are populated
        st_usd = parse_newapi_status({
            "success": True,
            "data": {"quota_display_type": "USD", "usd_exchange_rate": 7.2, "price": 7.3, "quota_per_unit": 1000000},
        })
        self.assertEqual(resolve_newapi_currency_params(st_usd), ("USD", 1.0, 1_000_000.0))
        self.assertIsNone(st_usd["cny_recharge_hint"])

        # 3B) Level 3: Chinese USD-labeled discount relay stays USD (never mutates silently) and exposes gentle hint
        st_usd_cn_hint = parse_newapi_status({
            "success": True,
            "data": {
                "system_name": "中文折扣中转",
                "quota_display_type": "USD",
                "usd_exchange_rate": 7.2,
                "price": 2.0,
            },
        })
        self.assertEqual(resolve_newapi_currency_params(st_usd_cn_hint), ("USD", 1.0, 500_000.0))
        self.assertEqual(
            st_usd_cn_hint["cny_recharge_hint"],
            {"recharge_price": 2.0, "suggested_currency": "CNY"},
        )

        # 4) Level 2: CUSTOM virtual token site with non-USD symbol + low RMB recharge price -> CNY (NEVER * 10!)
        st_custom_cookie = parse_newapi_status({
            "success": True,
            "data": {
                "system_name": "Cookie Token Relay",
                "quota_display_type": "CUSTOM",
                "custom_currency_symbol": "🍪",
                "custom_currency_exchange_rate": 10,
                "usd_exchange_rate": 10,
                "price": 1.0,
            },
        })
        self.assertEqual(st_custom_cookie["custom_currency_symbol"], "🍪")
        self.assertEqual(st_custom_cookie["site_recharge_price"], 1.0)
        self.assertEqual(st_custom_cookie["currency_source"], "status_custom_cny")
        self.assertEqual(resolve_newapi_currency_params(st_custom_cookie), ("CNY", 1.0, 500_000.0))
        # Explicit user override to USD or custom fiat_per_base_unit (e.g. 0.8 = 80% cost)
        self.assertEqual(resolve_newapi_currency_params(st_custom_cookie, currency_override="USD"), ("USD", 1.0, 500_000.0))
        self.assertEqual(
            resolve_newapi_currency_params(st_custom_cookie, fiat_per_base_unit_override=0.8),
            ("CNY", 0.8, 500_000.0),
        )

        # 4B) Level 2: Diamond CUSTOM site (custom_rate=1, price=1) -> CNY, scale=1.0
        st_custom_diamond = parse_newapi_status({
            "success": True,
            "data": {
                "system_name": "Diamond Relay",
                "quota_display_type": "CUSTOM",
                "custom_currency_symbol": "◆",
                "custom_currency_exchange_rate": 1,
                "usd_exchange_rate": 10,
                "price": 1.0,
            },
        })
        self.assertEqual(st_custom_diamond["currency_source"], "status_custom_cny")
        self.assertEqual(resolve_newapi_currency_params(st_custom_diamond), ("CNY", 1.0, 500_000.0))

        # 4C) Level 2: Authenticated topup_info RMB confirmation overrides even ASCII custom symbol
        topup_rmb = parse_newapi_topup_info({
            "success": True,
            "data": {
                "enable_online_topup": True,
                "enable_stripe_topup": False,
                "enable_creem_topup": False,
                "pay_methods": [{"type": "alipay", "name": "支付宝"}, {"type": "wxpay", "name": "微信支付"}],
                "discount": {"100": 0.95, "3000": 0.8},
                "topup_link": "https://secret-merchant.example/pay?key=leak",
            },
        })
        self.assertTrue(topup_rmb["rmb_topup_confirmed"])
        self.assertNotIn("topup_link", topup_rmb)
        st_custom_topup = parse_newapi_status(
            {
                "success": True,
                "data": {
                    "system_name": "Ascii Credits",
                    "quota_display_type": "CUSTOM",
                    "custom_currency_symbol": "CR",
                    "custom_currency_exchange_rate": 10,
                    "price": 1.0,
                },
            },
            topup_info=topup_rmb,
        )
        self.assertEqual(st_custom_topup["currency_source"], "topup_custom_cny")
        self.assertEqual(resolve_newapi_currency_params(st_custom_topup), ("CNY", 1.0, 500_000.0))

        # 4D) Level 2 protection: Overseas English CUSTOM site with default price=7.3 or Stripe stays USD!
        topup_stripe = parse_newapi_topup_info({
            "success": True,
            "data": {
                "enable_online_topup": True,
                "enable_stripe_topup": True,
                "pay_methods": [{"type": "alipay", "name": "支付宝"}],
            },
        })
        self.assertFalse(topup_stripe["rmb_topup_confirmed"])
        st_custom_overseas = parse_newapi_status(
            {
                "success": True,
                "data": {
                    "system_name": "Global AI Credits",
                    "quota_display_type": "CUSTOM",
                    "custom_currency_symbol": "CR",
                    "custom_currency_exchange_rate": 100,
                    "price": 7.3,
                },
            },
            topup_info=topup_stripe,
        )
        self.assertEqual(st_custom_overseas["currency_source"], "status_custom")
        self.assertEqual(resolve_newapi_currency_params(st_custom_overseas), ("USD", 1.0, 500_000.0))

        # 4E) Defense-in-depth: quota station with fiat_currency="USD" carrying NewAPI default RMB price=7.3
        # MUST NOT inflate USD model prices by 7.3x!
        st_usd_from_quota_default_price = {
            **st_custom_overseas,
            "quota_fiat_currency": "USD",
            "quota_fiat_per_base_unit": 7.3,
        }
        self.assertEqual(resolve_newapi_currency_params(st_usd_from_quota_default_price), ("USD", 1.0, 500_000.0))
        # Distinct explicit USD per-base rate (not equal to RMB recharge_price) is still honored
        self.assertEqual(
            resolve_newapi_currency_params({**st_custom_overseas, "quota_fiat_currency": "USD", "quota_fiat_per_base_unit": 0.5}),
            ("USD", 0.5, 500_000.0),
        )
        self.assertEqual(
            resolve_newapi_currency_params(st_usd_from_quota_default_price, fiat_per_base_unit_override=0.5),
            ("USD", 0.5, 500_000.0),
        )

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

        # Legacy dict quota_conversion: malformed cluster_multiplier never crashes batch,
        # and explicit currency="USD" is NOT double-scaled by CNY unit_scale=7.3!
        legacy_dict_payload = {
            "success": True,
            "data": [
                {
                    "model_name": "dict-explicit-usd",
                    "billing_mode": "tiered_expr",
                    "quota_conversion": {
                        "expr": "v1:p * 2 + c * 8",
                        "currency": "USD",
                        "cluster_multiplier": 1.0,
                    },
                },
                {
                    "model_name": "dict-inherit-cny-null-cm",
                    "billing_mode": "tiered_expr",
                    "quota_conversion": {
                        "expr": "v1:p * 2 + c * 8",
                        "cluster_multiplier": None,
                    },
                },
                {
                    "model_name": "dict-bad-cm-skipped",
                    "billing_mode": "tiered_expr",
                    "model_ratio": 0.0,
                    "quota_conversion": {
                        "expr": "v1:p * 2 + c * 8",
                        "cluster_multiplier": "not-a-number",
                    },
                },
            ],
        }
        legacy_diag: dict[str, int] = {}
        legacy_items = adapt_newapi(
            legacy_dict_payload,
            source_id="newapi:legacy-dict",
            source_label="Legacy",
            currency="CNY",
            unit_scale=7.3,
            diagnostics=legacy_diag,
        )
        legacy_by_model = {item.model_id: item for item in legacy_items}
        self.assertEqual(legacy_by_model["dict-explicit-usd"].rule["currency"], "USD")
        self.assertAlmostEqual(legacy_by_model["dict-explicit-usd"].rule.get("cluster_multiplier", 1.0), 1.0)
        self.assertEqual(legacy_by_model["dict-inherit-cny-null-cm"].rule["currency"], "CNY")
        self.assertAlmostEqual(legacy_by_model["dict-inherit-cny-null-cm"].rule.get("cluster_multiplier", 1.0), 7.3)
        self.assertNotIn("dict-bad-cm-skipped", legacy_by_model)
        self.assertEqual(legacy_diag.get("skipped_unsupported_expr"), 1)

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
                cluster_id="relay-example",
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

            # 3) Second provider in the SAME cluster sharing same api_base with NO key deduplicates to newapi:prov-a
            #    and preserves original label/provider_id AND cached prices!
            det2 = await svc.detect_newapi_source(
                provider_id="prov-b",
                cluster_id="relay-example",
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

            # 3B) Different cluster with NO key on an auth-gated Bearer gateway must NOT hijack newapi:prov-a!
            det3 = await svc.detect_newapi_source(
                provider_id="prov-c",
                cluster_id="relay-other",
                api_base="https://api.example.test/v1/",
                api_key="",
                supplier_label="Other Cluster",
            )
            self.assertTrue(det3["ok"])
            self.assertFalse(det3["deduplicated"])
            self.assertEqual(det3["source_id"], "newapi:prov-c")
            self.assertTrue(det3["needs_key"])

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

            # 5) Legacy upgraded source without auth_mode MUST NOT be overwritten by a distinct Bearer cluster!
            settings_store.upsert_newapi_source(
                "newapi:legacy-old",
                url="https://private.example.test/api/pricing",
                provider_id="legacy-old",
                label="Legacy Old",
                enabled=True,
                auth_mode=None,
                cluster_id=None,
            )
            p_c = await svc.detect_newapi_source(
                provider_id="priv-c/model-a",
                cluster_id="cluster-priv-c",
                api_base="https://private.example.test/v1",
                api_key="sk-priv-a",
                supplier_label="Private C",
            )
            self.assertTrue(p_c["ok"])
            self.assertFalse(p_c["deduplicated"])
            self.assertEqual(p_c["source_id"], "newapi:priv-c/model-a")

            # 6) Bearer sync MUST NEVER borrow another cluster's API key AND preserves cached prices when key is missing!
            other_cluster_conns = {
                "priv-b/model-b": {
                    "api_base": "https://private.example.test/v1",
                    "api_key": "sk-priv-b",
                    "proxy": "",
                    "cluster_id": "cluster-priv-b",
                },
            }
            borrowed = svc._find_connection_for_newapi_source(
                other_cluster_conns,
                provider_id="priv-a/model-a",
                cluster_id="cluster-priv-a",
                pricing_url="https://private.example.test/api/pricing",
                auth_mode="bearer",
            )
            self.assertEqual(borrowed, {})

            # Simulate anonymous returning 200 empty data[] while cluster-a's connection is absent:
            # sync_sources must NOT borrow cluster-b's key AND must preserve model-a in cache!
            def empty_anon_handler(url, kwargs):
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(404, b"no status")
                auth = (kwargs.get("headers") or {}).get("Authorization", "")
                if "sk-priv-b" in auth:
                    raise AssertionError("Bearer sync illegally borrowed another cluster's API key!")
                return _FakeHttpResponse(200, json.dumps({"success": True, "data": []}).encode("utf-8"))

            svc._session_factory = lambda _t: _FakeHttpSession(empty_anon_handler)
            res_missing = await svc.sync_sources(
                source_ids=[p_a["source_id"]],
                connections=other_cluster_conns,
            )
            st_missing = res_missing["results"][0]
            self.assertEqual(st_missing["status"], "ok")
            self.assertTrue(st_missing["needs_key"])
            self.assertEqual(st_missing["model_count"], 1)
            self.assertIn(f"{p_a['source_id']}:model-a", catalog_store.read_safe()["prices"])

    async def test_level4_legacy_newapi_backfill_and_quota_station_resolver(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_store = PricingSettingsStore(root)
            catalog_store = PriceCatalogStore(root)
            fixed_now = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)

            # Seed a legacy pre-v2 New API source (no site_meta, stale USD per-turn price) with recent last_auto_sync_at
            settings_store.upsert_newapi_source(
                "newapi:legacy-cny",
                url="https://cn-relay.example.test/api/pricing",
                provider_id="legacy-cny",
                label="LegacyCN",
                enabled=True,
                cluster_id="cluster-legacy-cn",
            )
            settings_store.record_auto_sync_at("2026-11-25T00:30:00+00:00")
            catalog_store.save_source_result(
                SourceStatus(
                    source_id="newapi:legacy-cny",
                    kind="newapi",
                    label="LegacyCN",
                    url="https://cn-relay.example.test/api/pricing",
                    enabled=True,
                    status="ok",
                    model_count=1,
                    last_sync_at="2026-11-24T09:30:00+00:00",
                    last_ok_at="2026-11-24T09:30:00+00:00",
                    etag='"stale-etag"',
                    currency="USD",
                    site_meta=None,
                ),
                [
                    CatalogPrice(
                        price_key="newapi:legacy-cny:demo-per-call",
                        source_id="newapi:legacy-cny",
                        source_label="LegacyCN",
                        model_id="demo-per-call",
                        vendor="newapi",
                        display_name="demo-per-call",
                        rule={"mode": "per_turn", "currency": "USD", "price": 0.115, "multiplier": 1.0},
                    )
                ],
                updated_at="2026-11-24T09:30:00+00:00",
            )

            self.assertEqual(
                find_newapi_sources_needing_backfill(settings_store.read_safe(), catalog_store.read_safe()),
                ["newapi:legacy-cny"],
            )

            seen_etags: list[str | None] = []

            def backfill_handler(url, kwargs):
                headers = kwargs.get("headers") or {}
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(
                        200,
                        json.dumps({
                            "success": True,
                            "data": {
                                "system_name": "CN Primary Relay",
                                "quota_display_type": "CNY",
                                "usd_exchange_rate": 1,
                                "quota_per_unit": 500000,
                                "price": 1,
                            },
                        }).encode("utf-8"),
                    )
                if url.endswith("/api/pricing"):
                    seen_etags.append(headers.get("If-None-Match"))
                    return _FakeHttpResponse(
                        200,
                        json.dumps({
                            "success": True,
                            "group_ratio": {"default": 1.0},
                            "data": [
                                {"model_name": "demo-per-call", "quota_type": 1, "model_price": 0.115},
                            ],
                        }).encode("utf-8"),
                        headers={"ETag": '"fresh-v2"'},
                    )
                return _FakeHttpResponse(404, b"not found")

            svc = PricingCatalogService(
                settings_store,
                catalog_store,
                now=lambda: fixed_now,
                session_factory=lambda _t: _FakeHttpSession(backfill_handler),
                debounce_seconds=0.0,
            )

            # Even though last_auto_sync_at was only 30 min ago, Level 4 schedules a one-shot backfill!
            self.assertTrue(svc.maybe_schedule_daily_sync())
            self.assertIsNotNone(svc._bg_task)
            await svc._bg_task

            # Old ETag was bypassed, price is now CNY ¥0.115, schema_version == 2, last_auto_sync_at untouched
            self.assertEqual(seen_etags, [None])
            cat_after = catalog_store.read_safe()
            src_after = cat_after["sources"]["newapi:legacy-cny"]
            self.assertEqual(src_after["currency"], "CNY")
            self.assertEqual(src_after["site_meta"]["schema_version"], NEWAPI_SITE_META_SCHEMA_VERSION)
            self.assertEqual(cat_after["prices"]["newapi:legacy-cny:demo-per-call"]["rule"]["currency"], "CNY")
            self.assertEqual(cat_after["prices"]["newapi:legacy-cny:demo-per-call"]["rule"]["price"], 0.115)
            self.assertEqual(settings_store.read_safe()["price_sync"]["last_auto_sync_at"], "2026-11-25T00:30:00+00:00")
            # Second call MUST NOT re-schedule (no infinite loop!)
            self.assertFalse(svc.maybe_schedule_daily_sync())

            # Quota station resolver: VIP unlock via server_address alias, topup RMB, fallback, zero leak
            secret_station_token = "sys-tok-super-secret-value-999"
            secret_uid = "884812"
            auth_calls: list[dict] = []

            def station_auth_handler(url, kwargs):
                headers = kwargs.get("headers") or {}
                auth = headers.get("Authorization", "")
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(
                        200,
                        json.dumps({
                            "success": True,
                            "data": {
                                "system_name": "Cookie VIP Relay",
                                "quota_display_type": "CUSTOM",
                                "custom_currency_symbol": "🍪",
                                "custom_currency_exchange_rate": 10,
                                "usd_exchange_rate": 10,
                                "quota_per_unit": 500000,
                                "price": 1,
                            },
                        }).encode("utf-8"),
                    )
                if url.endswith("/api/user/topup/info"):
                    auth_calls.append({"url": url, "auth": auth, "uid": headers.get("New-Api-User"), "redirects": kwargs.get("allow_redirects")})
                    if secret_station_token in auth:
                        return _FakeHttpResponse(
                            200,
                            json.dumps({
                                "success": True,
                                "data": {
                                    "enable_online_topup": True,
                                    "enable_stripe_topup": False,
                                    "enable_creem_topup": False,
                                    "pay_methods": [{"type": "alipay", "name": "支付宝"}],
                                    "discount": {"1000": 0.8},
                                },
                            }).encode("utf-8"),
                        )
                    return _FakeHttpResponse(401, b"unauthorized")
                if url.endswith("/api/pricing"):
                    auth_calls.append({"url": url, "auth": auth, "uid": headers.get("New-Api-User"), "redirects": kwargs.get("allow_redirects")})
                    if secret_station_token in auth:
                        return _FakeHttpResponse(
                            200,
                            json.dumps({
                                "success": True,
                                "group_ratio": {"default": 1.0, "vip": 0.8, "Plus": 0.08},
                                "data": [
                                    {
                                        "model_name": "pro-hydrogen",
                                        "quota_type": 0,
                                        "model_ratio": 0.375,
                                        "completion_ratio": 5,
                                        "cache_ratio": 0.05,
                                        "enable_groups": ["default", "vip"],
                                    }
                                ],
                            }).encode("utf-8"),
                        )
                    return _FakeHttpResponse(
                        200,
                        json.dumps({
                            "success": True,
                            "group_ratio": {"default": 1.0, "Plus": 0.1},
                            "data": [
                                {
                                    "model_name": "pro-hydrogen",
                                    "quota_type": 0,
                                    "model_ratio": 0.375,
                                    "completion_ratio": 5,
                                    "cache_ratio": 0.05,
                                    "enable_groups": ["default"],
                                }
                            ],
                        }).encode("utf-8"),
                    )
                return _FakeHttpResponse(404, b"not found")

            svc._session_factory = lambda _t: _FakeHttpSession(station_auth_handler)
            svc.set_quota_station_resolver(lambda origin: {
                "station_id": "st-cookie",
                "station_name": "Cookie 站点",
                "adapter_id": "newapi",
                "base_url": "https://console.cookie.example.test",
                "server_address": "https://cookie.example.test",
                "auth_token": secret_station_token,
                "user_id": secret_uid,
                "user_id_header": "New-Api-User",
                "proxy_url": "",
                "allow_http": False,
                "group_name": "vip",
                "observed_group_ratios": {"vip": 0.8},
                "fiat_currency": "",
                "fiat_per_base_unit": None,
                "custom_currency_exchange_rate": 10.0,
                "custom_currency_symbol": "🍪",
            } if "cookie.example.test" in origin else None)

            det = await svc.detect_newapi_source(
                provider_id="cookie/pro-hydrogen",
                cluster_id="cookie",
                api_base="https://cookie.example.test/v1",
                supplier_label="Cookie",
            )
            self.assertTrue(det["ok"])
            self.assertTrue(det["quota_station_linked"])
            self.assertEqual(det["currency"], "CNY")
            self.assertEqual(det["currency_source"], "topup_custom_cny")
            self.assertEqual(det["detected_group"], "vip")
            self.assertEqual(det["detected_group_ratio"], 0.8)
            self.assertEqual(det["available_groups"]["Plus"], 0.08)
            self.assertTrue(all(call["redirects"] is False for call in auth_calls))
            self.assertTrue(any(call["uid"] == secret_uid for call in auth_calls))

            # Price is ¥0.75 / ¥0.0375 / ¥3.75 (NOT $0.75 and NOT ×10!)
            cat_cookie = catalog_store.read_safe()
            cookie_rule = cat_cookie["prices"][f"{det['source_id']}:pro-hydrogen"]["rule"]
            self.assertEqual(cookie_rule["currency"], "CNY")
            self.assertEqual(cookie_rule["input"], 0.75)
            self.assertEqual(cookie_rule["input_cached"], 0.0375)
            self.assertEqual(cookie_rule["output"], 3.75)

            # Security invariant: NEVER persist station auth_token or user_id on disk!
            settings_raw = (root / "pricing" / "settings.json").read_text(encoding="utf-8")
            catalog_raw = (root / "pricing" / "catalog.json").read_text(encoding="utf-8")
            self.assertNotIn(secret_station_token, settings_raw)
            self.assertNotIn(secret_station_token, catalog_raw)
            self.assertNotIn(secret_uid, settings_raw)
            self.assertNotIn(secret_uid, catalog_raw)

            # User custom fiat_per_base_unit=0.8 (12.5 🍪 = ¥1, 80% cost) scales CNY to ¥0.60 / ¥0.03 / ¥3.00!
            settings_store.set_source_currency_override(
                det["source_id"], "auto", fiat_per_base_unit=0.8
            )
            res_scaled = await svc.sync_sources(source_ids=[det["source_id"]])
            self.assertEqual(res_scaled["ok_count"], 1)
            scaled_rule = catalog_store.read_safe()["prices"][f"{det['source_id']}:pro-hydrogen"]["rule"]
            self.assertEqual(scaled_rule["currency"], "CNY")
            self.assertEqual(scaled_rule["input"], 0.6)
            self.assertEqual(scaled_rule["input_cached"], 0.03)
            self.assertEqual(scaled_rule["output"], 3.0)

            # Reset fiat_per_base_unit=None restores face value ¥0.75
            settings_store.set_source_currency_override(
                det["source_id"], "auto", fiat_per_base_unit=None
            )
            await svc.sync_sources(source_ids=[det["source_id"]])
            restored_rule = catalog_store.read_safe()["prices"][f"{det['source_id']}:pro-hydrogen"]["rule"]
            self.assertEqual(restored_rule["input"], 0.75)

            # Switching currency_override without explicit fiat_per_base_unit auto-clears stale cross-currency rate
            settings_store.set_source_currency_override(
                det["source_id"], "CNY", fiat_per_base_unit=2.0
            )
            self.assertEqual(
                settings_store.read_safe()["price_sources"][det["source_id"]].get("fiat_per_base_unit"),
                2.0,
            )
            settings_store.set_source_currency_override(det["source_id"], "USD")
            self.assertNotIn(
                "fiat_per_base_unit",
                settings_store.read_safe()["price_sources"][det["source_id"]],
            )
            await svc.sync_sources(source_ids=[det["source_id"]])
            usd_clean_rule = catalog_store.read_safe()["prices"][f"{det['source_id']}:pro-hydrogen"]["rule"]
            self.assertEqual(usd_clean_rule["currency"], "USD")
            self.assertEqual(usd_clean_rule["input"], 0.75)
            # Restore override back to auto before unbinding test
            settings_store.set_source_currency_override(det["source_id"], "auto")

            # Unbinding / disabling a previously linked Quota Watch station MUST bypass ETag
            # and clear all stale quota_*, topup_*, observed_group_ratios, and quota_watch detected_groups
            # while preserving non-quota /v1/models detected_groups!
            cat_pre_unbind = catalog_store.read_safe()
            pre_src = cat_pre_unbind["sources"][det["source_id"]]
            pre_meta = dict(pre_src["site_meta"])
            pre_meta.setdefault("detected_groups", {})["other-cluster"] = {
                "group": "gemini",
                "ratio": 0.245,
                "confidence": "exact",
                "model_count": 6,
            }
            catalog_store.save_source_result(
                SourceStatus(
                    source_id=det["source_id"],
                    kind="newapi",
                    label=pre_src["label"],
                    url=pre_src["url"],
                    enabled=True,
                    status="ok",
                    model_count=1,
                    last_sync_at=pre_src["last_sync_at"],
                    last_ok_at=pre_src["last_ok_at"],
                    etag='"linked-etag"',
                    currency="CNY",
                    site_meta=pre_meta,
                ),
                None,
                updated_at=pre_src["last_sync_at"],
                preserve_existing_prices=True,
            )

            unbind_etags: list[str | None] = []

            def unbind_handler(url, kwargs):
                headers = kwargs.get("headers") or {}
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(
                        200,
                        json.dumps({
                            "success": True,
                            "data": {
                                "system_name": "Cookie VIP Relay",
                                "quota_display_type": "CUSTOM",
                                "custom_currency_symbol": "🍪",
                                "custom_currency_exchange_rate": 10,
                                "usd_exchange_rate": 10,
                                "quota_per_unit": 500000,
                                "price": 1,
                            },
                        }).encode("utf-8"),
                    )
                if url.endswith("/api/pricing"):
                    unbind_etags.append(headers.get("If-None-Match"))
                    return _FakeHttpResponse(
                        200,
                        json.dumps({
                            "success": True,
                            "group_ratio": {"default": 1.0, "Plus": 0.1},
                            "data": [
                                {
                                    "model_name": "pro-hydrogen",
                                    "quota_type": 0,
                                    "model_ratio": 0.375,
                                    "completion_ratio": 5,
                                    "cache_ratio": 0.05,
                                    "enable_groups": ["default"],
                                }
                            ],
                        }).encode("utf-8"),
                    )
                return _FakeHttpResponse(404, b"not found")

            svc._session_factory = lambda _t: _FakeHttpSession(unbind_handler)
            svc.set_quota_station_resolver(lambda _origin: None)
            res_unbind = await svc.sync_sources(source_ids=[det["source_id"]])
            self.assertEqual(res_unbind["ok_count"], 1)
            self.assertEqual(unbind_etags, [None])
            meta_after_unbind = catalog_store.read_safe()["sources"][det["source_id"]]["site_meta"]
            for stale_key in (
                "quota_station_id",
                "quota_station_name",
                "quota_user_group",
                "quota_fiat_currency",
                "quota_fiat_per_base_unit",
                "observed_group_ratios",
                "topup_pay_methods",
                "topup_discount_tiers",
            ):
                self.assertNotIn(stale_key, meta_after_unbind)
            self.assertFalse(meta_after_unbind.get("topup_rmb_confirmed"))
            self.assertEqual(meta_after_unbind.get("currency_source"), "status_custom_cny")
            self.assertNotIn("__station__", meta_after_unbind.get("detected_groups") or {})
            self.assertNotIn("cookie", meta_after_unbind.get("detected_groups") or {})
            self.assertIn("other-cluster", meta_after_unbind.get("detected_groups") or {})

            # Cross-origin station URL refuses to send token and gracefully falls back to anonymous!
            cross_auth_seen: list[str] = []

            def cross_origin_handler(url, kwargs):
                auth = (kwargs.get("headers") or {}).get("Authorization", "")
                if auth:
                    cross_auth_seen.append(auth)
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(200, json.dumps({"success": True, "data": {"quota_display_type": "USD"}}).encode("utf-8"))
                return _FakeHttpResponse(200, json.dumps({"success": True, "data": [{"model_name": "m1", "quota_type": 0, "model_ratio": 1.0, "completion_ratio": 2.0}]}).encode("utf-8"))

            svc._session_factory = lambda _t: _FakeHttpSession(cross_origin_handler)
            svc.set_quota_station_resolver(lambda _origin: {
                "station_id": "evil-cross",
                "station_name": "Other Domain",
                "base_url": "https://other-host.example.test",
                "server_address": "https://other-host.example.test",
                "auth_token": secret_station_token,
                "user_id": secret_uid,
            })
            det_cross = await svc.detect_newapi_source(
                provider_id="victim/m1",
                cluster_id="victim",
                api_base="https://victim.example.test/v1",
            )
            self.assertTrue(det_cross["ok"])
            self.assertEqual(cross_auth_seen, [])

            # Expired station token (401) gracefully falls back to anonymous pricing without error!
            def expired_token_handler(url, kwargs):
                auth = (kwargs.get("headers") or {}).get("Authorization", "")
                if url.endswith("/api/status"):
                    return _FakeHttpResponse(200, json.dumps({"success": True, "data": {"quota_display_type": "CNY", "usd_exchange_rate": 1}}).encode("utf-8"))
                if url.endswith("/api/user/topup/info"):
                    return _FakeHttpResponse(401, b"expired")
                if secret_station_token in auth:
                    return _FakeHttpResponse(401, b"expired")
                return _FakeHttpResponse(200, json.dumps({"success": True, "data": [{"model_name": "m2", "quota_type": 0, "model_ratio": 0.5, "completion_ratio": 2.0}]}).encode("utf-8"))

            svc._session_factory = lambda _t: _FakeHttpSession(expired_token_handler)
            svc.set_quota_station_resolver(lambda _origin: {
                "station_id": "expired-st",
                "station_name": "Expired",
                "base_url": "https://fallback.example.test",
                "server_address": "https://fallback.example.test",
                "auth_token": secret_station_token,
            })
            det_fb = await svc.detect_newapi_source(
                provider_id="fb/m2",
                cluster_id="fb",
                api_base="https://fallback.example.test/v1",
            )
            self.assertTrue(det_fb["ok"])
            self.assertEqual(det_fb["currency"], "CNY")
            self.assertEqual(det_fb["models"], 1)


if __name__ == "__main__":
    unittest.main()
