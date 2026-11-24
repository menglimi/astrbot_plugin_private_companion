"""Comprehensive tests for Pricing v2 engine, hybrid repricing, FX streaming,
strict active-first / token-DESC inventory sorting, and all 11 pricing API routes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot.api.web import PluginMultiDict, PluginRequest, bind_request_context
from starlette.requests import Request

from astrbot_host.exchange_rate import (
    MAX_RESPONSE_BYTES,
    ExchangeRateService,
    parse_usd_cny_payload,
)
from astrbot_host.model_api import PREFIX
from astrbot_host.pricing_inventory import build_pricing_payload
from astrbot_host.token_sources import _bucket
from companion_v2.adapters.astrbot.provider import AstrBotTextProvider, UsageTariff
from companion_v2.execution.models import ProviderBinding
from companion_v2.model_gateway.core_observations import record_observation
from companion_v2.model_gateway.management import ModelManagement
from companion_v2.model_gateway.pricing import (
    DEFAULT_MODEL_TARIFFS,
    PricingSettingsError,
    PricingSettingsStore,
    convert_microusd,
    effective_usd_cny_rate,
    estimate_cost_microusd,
    evaluate_call_cost_microusd,
    is_rate_stale,
    match_builtin_tariff,
    resolve_effective_pricing,
    resolve_tariff,
    tariff_resolver_for,
)
from companion_v2.model_gateway.pricing_catalog import CatalogPrice, PriceCatalogStore, SourceStatus
from tests import test_astrbot_staging_host as support
from tests.test_host_application import HostFixture


class _FakeChunkedContent:
    def __init__(self, payload: bytes, chunk_size: int = 16):
        self._payload = payload
        self._chunk_size = chunk_size

    async def iter_chunked(self, n: int):
        step = min(self._chunk_size, n)
        for i in range(0, len(self._payload), step):
            yield self._payload[i : i + step]


class _FakeResponse:
    def __init__(self, status: int, payload: bytes, *, chunk_size: int = 16, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeChunkedContent(payload, chunk_size=chunk_size)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler
        self.closed = False
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._handler(url)


class UsageTariffTests(unittest.TestCase):
    def test_cache_creation_falls_back_to_input_when_none_and_supports_explicit_zero_and_custom_rate(self):
        fallback = UsageTariff(2_000_000, 500_000, 8_000_000)
        amt = fallback.amount(1_000_000, 0, 0, input_tokens=1_000_000,
                              cached_input_tokens=0, cache_creation_tokens=400_000)
        self.assertEqual(amt.cost_microusd, 2_000_000)
        self.assertEqual(fallback.document(), {"input": 2_000_000, "cached_input": 500_000, "output": 8_000_000})

        free_write = UsageTariff(2_000_000, 500_000, 8_000_000, cache_creation=0)
        amt_free = free_write.amount(1_000_000, 0, 0, input_tokens=1_000_000,
                                     cached_input_tokens=0, cache_creation_tokens=400_000)
        self.assertEqual(amt_free.cost_microusd, 1_200_000)
        self.assertEqual(free_write.document()["cache_creation"], 0)

        claude = UsageTariff(3_000_000, 300_000, 15_000_000, cache_creation=3_750_000)
        amt_claude = claude.amount(800_000, 200_000, 100_000, input_tokens=1_000_000,
                                   cached_input_tokens=200_000, cache_creation_tokens=400_000)
        expected = int(0.4 * 3_000_000 + 0.2 * 300_000 + 0.4 * 3_750_000 + 0.1 * 15_000_000)
        self.assertEqual(amt_claude.cost_microusd, expected)

    def test_unconfigured_tariff_records_none_cost_and_reserves_zero_integer_cost(self):
        tariff = UsageTariff.unconfigured()
        self.assertFalse(tariff.configured)
        actual = tariff.amount(500, 100, 200, input_tokens=600, cached_input_tokens=100)
        self.assertEqual(actual.tokens, 800)
        self.assertIsNone(actual.cost_microusd)
        reserved = tariff.reserve_amount(600, 200)
        self.assertEqual(reserved.tokens, 800)
        self.assertEqual(reserved.cost_microusd, 0)
        self.assertIs(type(reserved.cost_microusd), int)


class PricingV2EngineAndStoreTests(unittest.TestCase):
    def test_builtin_catalog_matching_and_boundary_precision(self):
        self.assertGreaterEqual(len(DEFAULT_MODEL_TARIFFS), 80)
        self.assertEqual(match_builtin_tariff("openai/gpt-4o-mini-2024-07-18").preset_id, "gpt-4o-mini")
        self.assertEqual(match_builtin_tariff("gpt-4o-2024-11-20").preset_id, "gpt-4o")
        self.assertEqual(match_builtin_tariff("anthropic/claude-3.5-sonnet-20241022").preset_id, "claude-3-5-sonnet")
        self.assertEqual(match_builtin_tariff("[relay] claude-sonnet-4-5-20250929:thinking").preset_id, "claude-sonnet-4-5")
        self.assertEqual(match_builtin_tariff("google/gemini-2.5-flash-lite-preview-06-17").preset_id, "gemini-2.5-flash-lite")
        self.assertEqual(match_builtin_tariff("models/gemini-2.5-pro-exp-03-25").preset_id, "gemini-2.5-pro")
        self.assertEqual(match_builtin_tariff("deepseek/deepseek-reasoner").preset_id, "deepseek-reasoner")
        self.assertEqual(match_builtin_tariff("qwen/qwen3-max-latest").preset_id, "qwen3-max")
        self.assertEqual(match_builtin_tariff("glm-5").preset_id, "glm-5")
        self.assertEqual(match_builtin_tariff("kimi-k2.5").preset_id, "kimi-k2.5")
        self.assertIsNone(match_builtin_tariff("real-model"))
        self.assertIsNone(match_builtin_tariff("my-private-finetune-v9"))

    def test_v1_backward_compat_store_atomic_persistence_and_migration(self):
        with TemporaryDirectory(prefix="companion-pricing-") as tmp:
            store = PricingSettingsStore(Path(tmp))
            initial = store.read()
            self.assertEqual(initial["display_currency"], "CNY")
            self.assertEqual(initial["revision"], "initial")
            self.assertEqual(effective_usd_cny_rate(initial), 7.2)

            saved = store.update("initial", {
                "display_currency": "USD",
                "exchange_rate": {"mode": "manual", "manual_usd_cny": 7.35},
                "provider_tariffs": {
                    "relay-a": {
                        "currency": "USD",
                        "input_per_million": 2.0,
                        "cached_input_per_million": 0.5,
                        "cache_creation_per_million": 2.5,
                        "output_per_million": 8.0,
                        "multiplier": 0.25,
                        "free": False,
                    },
                    "cn-direct": {
                        "currency": "CNY",
                        "input_per_million": 7.35,
                        "cached_input_per_million": None,
                        "cache_creation_per_million": 0,
                        "output_per_million": 14.7,
                        "multiplier": 1.0,
                        "free": False,
                    },
                    "free-tier": {"currency": "USD", "free": True, "multiplier": 1.0},
                },
            })
            self.assertNotEqual(saved["revision"], "initial")
            self.assertEqual(saved["display_currency"], "USD")
            self.assertEqual(effective_usd_cny_rate(saved), 7.35)
            self.assertEqual([p.name for p in store.directory.iterdir()], ["settings.json"])

            with self.assertRaises(PricingSettingsError) as ctx:
                store.update("initial", {"display_currency": "CNY"})
            self.assertEqual(ctx.exception.code, "pricing_config_conflict")

            with self.assertRaises(PricingSettingsError):
                store.update(saved["revision"], {"exchange_rate": {"mode": "manual", "manual_usd_cny": 0.5}})
            with self.assertRaises(PricingSettingsError):
                store.update(saved["revision"], {"exchange_rate": {"mode": "manual", "manual_usd_cny": 150.0}})
            with self.assertRaises(PricingSettingsError):
                store.update(saved["revision"], {"exchange_rate": {"mode": "manual", "manual_usd_cny": None}})
            with self.assertRaises(PricingSettingsError):
                store.update(saved["revision"], {"provider_tariffs": {"bad": {"currency": "EUR", "input_per_million": 1, "output_per_million": 1}}})
            with self.assertRaises(PricingSettingsError):
                store.update(saved["revision"], {"provider_tariffs": {"bad": {"currency": "USD", "input_per_million": 1, "output_per_million": 1, "multiplier": 0}}})

            before_rev = saved["revision"]
            after_fx = store.record_fetched_rate(7.18, source="open.er-api.com", fetched_at="2026-11-21T00:00:00+00:00")
            self.assertEqual(after_fx["revision"], before_rev)
            self.assertEqual(after_fx["exchange_rate"]["cached_usd_cny"], 7.18)

            r_relay = resolve_tariff("relay-a", "unknown-model", saved)
            self.assertEqual(r_relay.source, "provider_override")
            self.assertEqual((r_relay.tariff.input, r_relay.tariff.cached_input, r_relay.tariff.cache_creation, r_relay.tariff.output),
                             (500_000, 125_000, 625_000, 2_000_000))

            r_cn = resolve_tariff("cn-direct", "unknown-model", saved)
            self.assertEqual((r_cn.tariff.input, r_cn.tariff.cached_input, r_cn.tariff.cache_creation, r_cn.tariff.output),
                             (1_000_000, 1_000_000, 0, 2_000_000))

            r_free = resolve_tariff("free-tier", "gpt-4o", saved)
            self.assertEqual(r_free.source, "provider_free")
            self.assertEqual(estimate_cost_microusd(input_tokens=100_000, output_tokens=50_000, tariff=r_free.tariff), 0)

            r_preset = resolve_tariff("unlisted", "gpt-4o", saved)
            self.assertEqual(r_preset.source, "builtin_preset")
            self.assertEqual(r_preset.preset_id, "gpt-4o")

            self.assertIsNone(convert_microusd(None, "CNY", 7.2))
            self.assertAlmostEqual(convert_microusd(1_000_000, "USD", 7.2), 1.0, places=6)
            self.assertAlmostEqual(convert_microusd(1_000_000, "CNY", 7.25), 7.25, places=6)

    def test_v2_all_5_modes_6_tier_resolution_configured_inheritance_schedule_and_cluster_multiplier(self):
        with TemporaryDirectory(prefix="companion-v2-engine-") as tmp:
            root = Path(tmp)
            settings_store = PricingSettingsStore(root)
            catalog_store = PriceCatalogStore(root)

            # Seed catalog with modelsdev gpt-4o ($2.5/$10) and gemini-2.5-pro (per_tier)
            catalog_store.save_source_result(
                SourceStatus(source_id="modelsdev", kind="public", label="models.dev", url="https://models.dev/catalog.json", status="ok", model_count=2),
                [
                    CatalogPrice(
                        price_key="modelsdev:openai/gpt-4o", source_id="modelsdev", source_label="models.dev",
                        model_id="gpt-4o", vendor="OpenAI", display_name="GPT-4o",
                        rule={"mode": "per_token", "currency": "USD", "input": 2.5, "input_cached": 1.25, "cache_creation": None, "output": 10.0, "multiplier": 1.0, "free": False},
                    ),
                    CatalogPrice(
                        price_key="modelsdev:google/gemini-2.5-pro", source_id="modelsdev", source_label="models.dev",
                        model_id="gemini-2.5-pro", vendor="Google", display_name="Gemini 2.5 Pro",
                        rule={
                            "mode": "per_tier",
                            "currency": "USD",
                            "base": {"input": 1.25, "input_cached": 0.125, "cache_creation": None, "output": 10.0},
                            "context_tiers": [{"threshold_tokens": 200_000, "input": 2.5, "input_cached": 0.25, "cache_creation": None, "output": 15.0}],
                            "service_tiers": [{"match": "priority", "input_multiplier": 2.0, "output_multiplier": 2.0, "input_cached_multiplier": 2.0, "cache_creation_multiplier": None, "input": None, "input_cached": None, "cache_creation": None, "output": None}],
                            "multiplier": 1.0,
                        },
                    ),
                ],
            )
            catalog = catalog_store.read_safe()

            settings = settings_store.update("initial", {
                "exchange_rate": {"mode": "manual", "manual_usd_cny": 7.2},
                "pricing_rules": {
                    # Exact model override in CNY: blank input -> inherits $2.5*7.2=18 CNY; explicit input_cached=0 -> FREE!
                    "p1|gpt-4o": {
                        "mode": "per_token",
                        "currency": "CNY",
                        "input": None,
                        "input_cached": 0.0,
                        "cache_creation": None,
                        "output": 72.0,
                        "configured": {"input": False, "input_cached": True, "cache_creation": False, "output": True},
                    },
                    "p-expr|custom": {
                        "mode": "tiered_expr",
                        "expr": "v1:len > 200000 ? p * 4 + c * 16 : p * 2 + c * 8",
                    },
                    "p-expr-cny|gemini-3.6-flash": {
                        "mode": "tiered_expr",
                        "currency": "CNY",
                        "expr": "v1:tier('标准', p * 0.75 + cr * 0.075 + c * 3.75)",
                    },
                    "p-turn": {"mode": "per_turn", "currency": "USD", "price": 0.01},
                    "p-req": {"mode": "per_request", "currency": "USD", "price": 0.05},
                },
                "pricing_multipliers": {
                    "cluster-half": 0.5,
                    "cluster-free": 0.0,
                },
                "pricing_schedules": {
                    "p1|gpt-4o": {
                        "enabled": True,
                        "timezone": "Asia/Shanghai",
                        "periods": [
                            {"id": "night", "days": [1, 2, 3, 4, 5, 6, 7], "all_day": False, "start": "23:00", "end": "07:00", "mode": "multiplier", "multiplier": 0.5},
                        ],
                    }
                },
            })

            # 1) Cross-currency inheritance + explicit 0 cached + schedule (0.5x) + cluster multiplier (0.5x applied LAST!)
            # Daytime: input inherited 18 CNY ($2.5), cached explicit 0 ($0), output 72 CNY ($10). Cluster 0.5x -> $1.25 in, $0 cached, $5 out!
            eff_day = resolve_effective_pricing(
                "p1", "gpt-4o", settings, catalog=catalog, cluster_id="cluster-half",
                timestamp="2026-11-23T06:00:00Z",  # CST 14:00
            )
            self.assertEqual(eff_day.source, "model_override")
            self.assertEqual(eff_day.rule["input"], 9.0)  # 18 * 0.5
            self.assertEqual(eff_day.rule["input_cached"], 0.0)
            self.assertEqual(eff_day.rule["output"], 36.0)  # 72 * 0.5
            cost_day = evaluate_call_cost_microusd(
                eff_day, usd_cny_rate=7.2, input_tokens=1_000_000, cached_input_tokens=500_000, output_tokens=100_000,
            )
            # 500k uncached * $1.25 + 500k cached * $0 + 100k out * $5 = 625k + 500k = 1_125_000 microUSD
            self.assertEqual(cost_day, 1_125_000)

            # Nighttime CST 23:30 (UTC 15:30): schedule 0.5x + cluster 0.5x = 0.25x -> 562_500 microUSD
            eff_night = resolve_effective_pricing(
                "p1", "gpt-4o", settings, catalog=catalog, cluster_id="cluster-half",
                timestamp="2026-11-23T15:30:00Z",
            )
            self.assertEqual(eff_night.schedule_period_id, "night")
            cost_night = evaluate_call_cost_microusd(
                eff_night, usd_cny_rate=7.2, input_tokens=1_000_000, cached_input_tokens=500_000, output_tokens=100_000,
            )
            self.assertEqual(cost_night, 562_500)

            # 2) per_tier with cluster multiplier: preserves service_tier *_multiplier untouched!
            eff_tier = resolve_effective_pricing(
                "p-gem", "gemini-2.5-pro", settings, catalog=catalog, cluster_id="cluster-half",
            )
            self.assertEqual(eff_tier.source, "catalog_auto")
            self.assertEqual(eff_tier.rule["base"]["input"], 0.625)  # 1.25 * 0.5
            self.assertEqual(eff_tier.rule["context_tiers"][0]["input"], 1.25)  # 2.5 * 0.5
            self.assertEqual(eff_tier.rule["service_tiers"][0]["input_multiplier"], 2.0)  # PRESERVED!
            # Long context (>200k) + priority (2x): input = 1.25*2 = $2.5/1M, out = 7.5*2 = $15/1M
            cost_tier = evaluate_call_cost_microusd(
                eff_tier, usd_cny_rate=7.2, input_tokens=250_000, output_tokens=10_000, service_tier="PRIORITY",
            )
            self.assertEqual(cost_tier, 250_000 * 2.5 + 10_000 * 15)  # 625k + 150k = 775_000

            # 3) tiered_expr chains cluster_multiplier; 0x cluster makes it free (0, NOT None!),
            #    and chained schedule_mult * cluster_mult > 100 evaluates cleanly without error!
            eff_expr_half = resolve_effective_pricing("p-expr", "custom", settings, catalog=catalog, cluster_id="cluster-half")
            self.assertEqual(
                evaluate_call_cost_microusd(eff_expr_half, input_tokens=100_000, output_tokens=10_000),
                int((100_000 * 2 + 10_000 * 8) * 0.5),
            )
            eff_expr_free = resolve_effective_pricing("p-expr", "custom", settings, catalog=catalog, cluster_id="cluster-free")
            self.assertEqual(evaluate_call_cost_microusd(eff_expr_free, input_tokens=100_000, output_tokens=10_000), 0)

            # 3B) tiered_expr in CNY divides by usd_cny_rate (never overcounts by FX rate!)
            eff_expr_cny = resolve_effective_pricing(
                "p-expr-cny", "gemini-3.6-flash", settings, catalog=catalog, cluster_id="cluster-half",
            )
            self.assertEqual(eff_expr_cny.rule["currency"], "CNY")
            # Raw CNY for 1M uncached input + 1M output = 0.75 + 3.75 = 4.50 CNY; * 0.5 cluster = 2.25 CNY; / 7.2 = $0.3125 = 312_500 microUSD
            self.assertEqual(
                evaluate_call_cost_microusd(
                    eff_expr_cny, usd_cny_rate=7.2, input_tokens=1_000_000, output_tokens=1_000_000,
                ),
                312_500,
            )

            settings_chained = settings_store.update(settings["revision"], {
                "pricing_multipliers": {"cluster-half": 0.5, "cluster-free": 0.0, "cluster-x20": 20.0},
                "pricing_schedules": {
                    **settings["pricing_schedules"],
                    "p-expr|custom": {
                        "enabled": True, "timezone": "UTC",
                        "periods": [{"id": "peak", "days": [1, 2, 3, 4, 5, 6, 7], "all_day": True, "mode": "multiplier", "multiplier": 10.0}],
                    },
                },
            })
            eff_chained = resolve_effective_pricing(
                "p-expr", "custom", settings_chained, catalog=catalog, cluster_id="cluster-x20",
                timestamp="2026-11-23T12:00:00Z",
            )
            self.assertEqual(eff_chained.rule["cluster_multiplier"], 200.0)
            self.assertEqual(
                evaluate_call_cost_microusd(eff_chained, input_tokens=1_000, output_tokens=100),
                (1_000 * 2 + 100 * 8) * 200,
            )

            # 4) own_source_ids consistency across provider_needs_per_call_repricing, resolve_tariff, and pid|<other_model>
            from companion_v2.model_gateway.pricing import provider_needs_per_call_repricing
            dual_catalog = {
                "prices": {
                    "newapi:a_other:shared-m": CatalogPrice(
                        price_key="newapi:a_other:shared-m", source_id="newapi:a_other", source_label="Other",
                        model_id="shared-m", vendor="Other", display_name="shared-m",
                        rule={"mode": "per_token", "currency": "USD", "input": 9.0, "output": 18.0, "multiplier": 1.0, "free": False},
                    ).to_dict(),
                    "newapi:z_shared:shared-m": CatalogPrice(
                        price_key="newapi:z_shared:shared-m", source_id="newapi:z_shared", source_label="Shared",
                        model_id="shared-m", vendor="Shared", display_name="shared-m",
                        rule={"mode": "tiered_expr", "currency": "USD", "expr": "p * 1 + c * 2", "tiers": [], "default_expr": None},
                    ).to_dict(),
                    "newapi:z_shared:token-m": CatalogPrice(
                        price_key="newapi:z_shared:token-m", source_id="newapi:z_shared", source_label="Shared",
                        model_id="token-m", vendor="Shared", display_name="token-m",
                        rule={"mode": "per_token", "currency": "USD", "input": 1.0, "output": 2.0, "multiplier": 1.0, "free": False},
                    ).to_dict(),
                    "newapi:a_other:token-m": CatalogPrice(
                        price_key="newapi:a_other:token-m", source_id="newapi:a_other", source_label="Other",
                        model_id="token-m", vendor="Other", display_name="token-m",
                        rule={"mode": "per_token", "currency": "USD", "input": 9.0, "output": 18.0, "multiplier": 1.0, "free": False},
                    ).to_dict(),
                }
            }
            dual_settings = {
                **settings,
                "price_sources": {
                    "newapi:a_other": {"enabled": True, "kind": "newapi", "label": "Other", "url": "https://a.example/api/pricing", "provider_id": "a_other"},
                    "newapi:z_shared": {"enabled": True, "kind": "newapi", "label": "Shared", "url": "https://z.example/api/pricing", "provider_id": "z_shared"},
                },
            }
            # URL-deduped p2 sharing newapi:z_shared sees tiered_expr -> needs per-call repricing!
            self.assertTrue(provider_needs_per_call_repricing(
                "p2", "shared-m", dual_settings, catalog=dual_catalog, own_source_ids=frozenset({"newapi:z_shared"}),
            ))
            # resolve_tariff with own_source_ids agrees with resolve_effective_pricing ($1/1M, NOT $9/1M!)
            rt = resolve_tariff("p2", "token-m", dual_settings, catalog=dual_catalog, own_source_ids=frozenset({"newapi:z_shared"}))
            self.assertEqual(rt.tariff.input, 1_000_000)
            # Non-primary model schedule or tiered_expr under p_hist|other-m triggers per-call repricing!
            self.assertTrue(provider_needs_per_call_repricing("p-expr", "primary-m", settings_chained, catalog=catalog))

            # 5) per_turn & per_request
            eff_turn = resolve_effective_pricing("p-turn", "", settings, catalog=catalog)
            self.assertEqual(evaluate_call_cost_microusd(eff_turn, calls=3), 30_000)
            eff_req = resolve_effective_pricing("p-req", "", settings, catalog=catalog)
            self.assertEqual(evaluate_call_cost_microusd(eff_req, requests=2), 100_000)

    def test_external_token_source_bucket_uses_none_cost_when_absent(self):
        bucket = _bucket({"calls": 3, "total_tokens": 900, "prompt_tokens": 700, "completion_tokens": 200})
        self.assertIsNone(bucket["cost_microusd"])


class HybridRepricingTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host_instance = await self.start()
        self.app = self.host_instance.application
        batch = self.host_instance.receive(self.event("pricing-reprice-context")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        self.role_context = self.context(self.app, batch)
        self.manager = ModelManagement(self.app, self.role_context)

    async def test_hybrid_repricing_per_request_per_turn_schedule_and_cluster_multiplier(self):
        app = self.app
        context = self.role_context
        # Use timestamps strictly <= app.now(): CST 14:00 (daytime) and CST 23:30 (nighttime)
        now = app.now()
        day_dt = now.astimezone(timezone.utc) - timedelta(days=1)
        day_ts = day_dt.replace(hour=6, minute=0, second=0, microsecond=0).isoformat()
        night_ts = day_dt.replace(hour=15, minute=30, second=0, microsecond=0).isoformat()

        call = app.runtime.read_one("SELECT call_id, task_id FROM model_calls LIMIT 1", ())
        provider_id = next(iter(app.router.providers))
        with app.runtime.transaction() as conn:
            conn.execute("UPDATE execution_tasks SET kind = 'calendar.plan' WHERE task_id = ?", (call["task_id"],))
            conn.execute("""UPDATE model_calls
                SET actual_tokens = 1500, actual_cost = 0,
                    input_tokens = 1000, cached_input_tokens = 200,
                    cache_creation_tokens = 100, output_tokens = 500,
                    finished_at = ?,
                    provider_json = ?
                WHERE call_id = ?""", (night_ts, json.dumps({"provider": provider_id}), call["call_id"]))

        # Record a 2-request tool-requery astrbot_core turn (1 turn_id, 2 LLM requests!)
        record_observation(app.runtime, context, {
            "version": 1, "revision": 1, "turn_id": "core-turn-multi",
            "event_id": "evt-multi", "execution_path": "astrbot_core",
            "received_at": day_ts, "observed_at": day_ts,
            "generation_state": "completed",
            "requests": [
                {
                    "position": 1, "state": "returned",
                    "started_at": day_ts, "finished_at": day_ts,
                    "latency_ms": 200, "provider": "core-req-provider",
                    "measurement_basis": "provider_response",
                    "usage": {
                        "input_tokens": 1000, "cached_input_tokens": 0,
                        "cache_creation_tokens": 0, "output_tokens": 200,
                        "tokens": 1200, "availability": "reported",
                        "cache_availability": "reported", "cost_availability": "unavailable",
                    },
                },
                {
                    "position": 2, "state": "returned",
                    "started_at": day_ts, "finished_at": day_ts,
                    "latency_ms": 200, "provider": "core-req-provider",
                    "measurement_basis": "provider_response",
                    "usage": {
                        "input_tokens": 1500, "cached_input_tokens": 500,
                        "cache_creation_tokens": 0, "output_tokens": 300,
                        "tokens": 1800, "availability": "reported",
                        "cache_availability": "reported", "cost_availability": "unavailable",
                    },
                },
            ],
            "tools": [],
        })

        # 1) Unconfigured -> None cost_microusd & cost_basis="unconfigured"
        unpriced = self.manager.maintenance(self.core).unwrap()
        self.assertIsNone(unpriced["totals"]["cost_microusd"])
        self.assertEqual(unpriced["totals"]["cost_basis"], "unconfigured")

        # 2) Configure: plugin provider with schedule (night 0.5x) + cluster multiplier 0.5x;
        #    core-req-provider with per_request $0.05 (must charge 1x per turn_id, NOT 2x!)
        store = PricingSettingsStore(app.runtime.data_root)
        store.update("initial", {
            "pricing_rules": {
                provider_id: {
                    "mode": "per_token", "currency": "USD",
                    "input": 4.0, "input_cached": 1.0, "cache_creation": 5.0, "output": 20.0,
                },
                "core-req-provider": {
                    "mode": "per_request", "currency": "USD", "price": 0.05,
                },
            },
            "pricing_schedules": {
                provider_id: {
                    "enabled": True,
                    "timezone": "Asia/Shanghai",
                    "periods": [
                        {"id": "night", "days": [1, 2, 3, 4, 5, 6, 7], "all_day": False, "start": "23:00", "end": "07:00", "mode": "multiplier", "multiplier": 0.5},
                    ],
                },
            },
            "pricing_multipliers": {"cluster-main": 0.5},
        })

        repriced = self.manager.maintenance(
            self.core,
            provider_metadata={provider_id: {"model": "custom-model", "cluster_id": "cluster-main"}},
        ).unwrap()

        # Plugin call at CST 23:30: night 0.5x * cluster 0.5x = 0.25x -> ($1 in, $0.25 cached, $1.25 creation, $5 out)
        # 700 uncached * 1 + 200 cached * 0.25 + 100 creation * 1.25 + 500 out * 5 = 700 + 50 + 125 + 2500 = 3375 microUSD
        self.assertEqual(repriced["usage_sources"]["plugin"]["totals"]["cost_microusd"], 3375)
        self.assertEqual(repriced["usage_sources"]["plugin"]["totals"]["cost_basis"], "estimated_local")

        # Core turn has 2 LLM requests under 1 turn_id -> per_request bills $0.05 = 50_000 microUSD ONCE!
        self.assertEqual(repriced["usage_sources"]["astrbot_core"]["totals"]["cost_microusd"], 50_000)
        self.assertEqual(repriced["totals"]["cost_microusd"], 53_375)

        # 3) Switch core-req-provider to per_turn $0.01 -> bills 2 calls = 20_000 microUSD!
        rev2 = store.read()["revision"]
        store.update(rev2, {
            "pricing_rules": {
                "core-req-provider": {"mode": "per_turn", "currency": "USD", "price": 0.01},
            },
            "pricing_schedules": {},
        })
        repriced_turn = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(repriced_turn["usage_sources"]["astrbot_core"]["totals"]["cost_microusd"], 20_000)

    async def test_dynamic_tariff_resolver_picks_up_updated_prices_without_restart(self):
        with TemporaryDirectory(prefix="companion-dyn-tariff-") as tmp:
            root = Path(tmp)
            resolver = tariff_resolver_for(root)
            native = support.FakeProvider()
            native.provider_config["id"] = "dyn-provider"
            native.model_name = "custom-local-model"
            context = NS(get_provider_by_id=lambda pid: native if pid == "dyn-provider" else None)
            adapter = AstrBotTextProvider(
                context, bridge=None,
                binding=ProviderBinding("model.text", "dyn-provider", "4.27.2", "fp"),
                provider_id="dyn-provider", version="4.27.2",
                provider_type=support.FakeProvider, function_tool_type=NS, tool_set_type=NS,
                tariff=UsageTariff.unconfigured(), tariff_resolver=resolver,
            )
            raw_resp = {"usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}}
            first = adapter._usage(raw_resp)
            self.assertEqual(first.tokens, 1500)
            self.assertIsNone(first.cost_microusd)

            PricingSettingsStore(root).update("initial", {
                "provider_tariffs": {
                    "dyn-provider": {
                        "currency": "USD", "input_per_million": 1.0, "output_per_million": 4.0,
                        "multiplier": 1.0, "free": False,
                    },
                },
            })
            second = adapter._usage(raw_resp)
            self.assertEqual(second.cost_microusd, 3000)


class ExchangeRateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_multi_chunk_fx_primary_fallback_proxy_and_swr(self):
        with TemporaryDirectory(prefix="companion-fx-") as tmp:
            store = PricingSettingsStore(Path(tmp))
            clock = [datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)]
            body = json.dumps({"result": "success", "rates": {"CNY": 7.265}}).encode()
            seen_sessions: list[_FakeSession] = []

            def make_session(handler):
                sess = _FakeSession(handler)
                seen_sessions.append(sess)
                return sess

            def primary_ok(url):
                if "open.er-api.com" in url:
                    # Tiny 7-byte chunks -> verifies iter_chunked streaming concatenation!
                    return _FakeResponse(200, body, chunk_size=7)
                raise AssertionError("fallback should not be called when primary succeeds")

            service = ExchangeRateService(
                store,
                now=lambda: clock[0],
                session_factory=lambda _t: make_session(primary_ok),
                proxy_resolver=lambda: "http://127.0.0.1:7890",
                debounce_seconds=0,
            )
            res1 = await service.sync_now()
            self.assertTrue(res1["synced"])
            self.assertEqual(res1["usd_cny_rate"], 7.265)
            self.assertEqual(seen_sessions[-1].calls[0]["proxy"], "http://127.0.0.1:7890")

            # Oversized streaming response rejected cleanly
            def oversized(_url):
                return _FakeResponse(200, b"x" * (MAX_RESPONSE_BYTES + 100), chunk_size=8192)

            service_big = ExchangeRateService(
                store, now=lambda: clock[0], session_factory=lambda _t: _FakeSession(oversized), debounce_seconds=0
            )
            res_big = await service_big.sync_now()
            self.assertFalse(res_big["synced"])
            self.assertEqual(res_big["usd_cny_rate"], 7.265)  # Previous cached rate preserved

            # Fallback works on timeout
            def timeout_fallback(url):
                if "open.er-api.com" in url:
                    raise asyncio.TimeoutError()
                return _FakeResponse(200, json.dumps({"rates": {"CNY": 7.281}}).encode(), chunk_size=5)

            service2 = ExchangeRateService(
                store, now=lambda: clock[0], session_factory=lambda _t: _FakeSession(timeout_fallback), debounce_seconds=0
            )
            res2 = await service2.sync_now()
            self.assertTrue(res2["synced"])
            self.assertTrue(res2["fallback_used"])
            self.assertEqual(res2["source"], "frankfurter.app")
            self.assertEqual(res2["usd_cny_rate"], 7.281)

            # SWR TTL checks
            self.assertFalse(is_rate_stale(store.read(), now=clock[0]))
            clock[0] += timedelta(hours=13)
            self.assertTrue(is_rate_stale(store.read(), now=clock[0]))
            with self.assertRaises(ValueError):
                parse_usd_cny_payload(b"x" * (MAX_RESPONSE_BYTES + 1), source="open.er-api.com")


class PricingV2InventorySortingAndApiRoutesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-pricing-api-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider, NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(self.native, support.host_config(), Path(temporary.name),
                                                           native_types=types, now=lambda: support.NOW)
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {(path, m): handler for path, handler, methods, _ in self.native.registered_web_apis for m in methods}

    async def call(self, route, *, method="GET", payload=None, username="admin", query=None):
        import astrbot_host.model_api as model_api
        raw = payload if isinstance(payload, bytes) else (json.dumps(payload).encode("utf-8") if payload is not None else b"")
        request = Request({"type": "http", "method": method, "path": route, "headers": [], "query_string": b""})
        pairs = list(query.items()) if isinstance(query, dict) else query or []

        async def _body():
            return raw

        fake_request = NS(username=username, path=route, query=PluginMultiDict(pairs), body=_body)
        with patch.object(model_api, "request", fake_request):
            with bind_request_context(PluginRequest(request, username=username)):
                return await self.routes[(route, method)]()

    async def test_all_11_pricing_routes_strict_sorting_and_zero_secret_leaks(self):
        (await self.controller.initialize_profile()).unwrap()
        expected_routes = [
            (PREFIX + "/pricing", "GET"),
            (PREFIX + "/pricing", "POST"),
            (PREFIX + "/pricing/sync-rates", "POST"),
            (PREFIX + "/pricing/sync", "POST"),
            (PREFIX + "/pricing/sources/toggle", "POST"),
            (PREFIX + "/pricing/sources/detect", "POST"),
            (PREFIX + "/pricing/sources/currency", "POST"),
            (PREFIX + "/pricing/select", "POST"),
            (PREFIX + "/pricing/select/reset", "POST"),
            (PREFIX + "/pricing/expr/validate", "POST"),
            (PREFIX + "/pricing/reset", "POST"),
            (PREFIX + "/pricing/residue/clear", "POST"),
        ]
        for key in expected_routes:
            self.assertIn(key, self.routes, f"Missing route {key}")

        # Configure rich multi-supplier AstrBot inventory WITH secrets to prove ZERO leak!
        secret_key = "sk-top-secret-never-leak-9999"
        secret_proxy = "http://proxyuser:secretpass@127.0.0.1:7890"
        self.native.get_config = lambda: {
            "http_proxy": secret_proxy,
            "provider_sources": [
                {
                    "id": "sup-high", "name": "Supplier High",
                    "api_base": "https://relay-a.example.test/v1",
                    "key": [secret_key], "proxy": secret_proxy,
                },
                {
                    "id": "sup-low", "name": "Supplier Low",
                    "api_base": "https://relay-b.example.test/v1",
                    "key": [secret_key],
                },
                {
                    "id": "sup-unused", "name": "AAA Unused Supplier",
                    "api_base": "https://unused.example.test/v1",
                    "key": [secret_key],
                },
            ],
            "provider": [
                {"id": "native-model", "provider_source_id": "sup-low", "model": "custom-unpriced-a", "enable": True},
                {"id": "high-tok-b", "provider_source_id": "sup-high", "model": "custom-unpriced-b", "enable": True},
                {"id": "high-tok-a", "provider_source_id": "sup-high", "model": "gpt-4o", "enable": True},
                {"id": "high-unused", "provider_source_id": "sup-high", "model": "gpt-4o-mini", "enable": True},
                {"id": "idle-prov", "provider_source_id": "sup-unused", "model": "gpt-4o", "enable": True},
            ],
        }

        # Seed usage in astrbot_core_reply_observations: high-tok-b (50k unpriced), high-tok-a (20k priced),
        # deleted-heavy (80k unpriced deleted!), deleted-light (5k deleted!)
        runtime = self.controller.runtime
        target_ctx = self.controller.session.profile.target
        now_iso = support.NOW.isoformat()
        for idx, (pid, toks) in enumerate([
            ("high-tok-b", 50_000),
            ("high-tok-a", 20_000),
            ("deleted-heavy", 80_000),
            ("deleted-light", 5_000),
        ]):
            record_observation(runtime, target_ctx, {
                "version": 1, "revision": 1, "turn_id": f"turn-sort-{idx}",
                "event_id": f"evt-sort-{idx}", "execution_path": "astrbot_core",
                "received_at": now_iso, "observed_at": now_iso,
                "generation_state": "completed",
                "requests": [{
                    "position": 1, "state": "returned",
                    "started_at": now_iso, "finished_at": now_iso,
                    "latency_ms": 100, "provider": pid,
                    "measurement_basis": "provider_response",
                    "usage": {
                        "input_tokens": toks, "cached_input_tokens": 0,
                        "cache_creation_tokens": 0, "output_tokens": 0,
                        "tokens": toks, "availability": "reported",
                        "cache_availability": "reported", "cost_availability": "unavailable",
                    },
                }],
                "tools": [],
            })

        # 1) GET /pricing -> verify strict sorting + ZERO secret leaks!
        got = await self.call(PREFIX + "/pricing", method="GET")
        self.assertEqual(got["status"], "ok", got)
        serialized = json.dumps(got, ensure_ascii=False)
        self.assertNotIn(secret_key, serialized)
        self.assertNotIn("secretpass", serialized)
        self.assertNotIn("proxyuser", serialized)

        data = got["data"]
        self.assertEqual(data["version"], 2)
        # Clusters sorted active-first (sup-high 70k > sup-low bound 0 > sup-unused idle!)
        cluster_ids = [c["cluster_id"] for c in data["clusters"]]
        self.assertEqual(cluster_ids, ["sup-high", "sup-low", "sup-unused"])
        # Inside sup-high: active sorted by total_tokens DESC (high-tok-b 50k -> high-tok-a 20k), unused AFTER (high-unused)!
        sup_high_pids = [p["id"] for p in data["clusters"][0]["providers"]]
        self.assertEqual(sup_high_pids, ["high-tok-b", "high-tok-a", "high-unused"])
        # Deleted providers sorted by total_tokens DESC
        self.assertEqual([d["id"] for d in data["deleted_providers"]], ["deleted-heavy", "deleted-light"])
        # Unpriced alerts sorted by total_tokens DESC, tagging deleted=True for !configured!
        self.assertEqual([u["provider_id"] for u in data["unpriced"]], ["deleted-heavy", "high-tok-b", "deleted-light"])
        self.assertTrue(data["unpriced"][0]["deleted"])
        self.assertFalse(data["unpriced"][1]["deleted"])

        # 2) POST /pricing -> save v2 rules, multipliers, schedules, timezone + verify conflict
        saved = await self.call(PREFIX + "/pricing", method="POST", payload={
            "revision": data["revision"],
            "changes": {
                "display_currency": "USD",
                "pricing_timezone": "Asia/Shanghai",
                "pricing_multipliers": {"sup-high": 0.5, "deleted-heavy": 0.8},
                "pricing_rules": {
                    "high-tok-b|custom-unpriced-b": {"mode": "per_turn", "currency": "USD", "price": 0.02},
                    "deleted-heavy": {"mode": "per_token", "currency": "USD", "input": 1.0, "output": 2.0},
                },
                "pricing_schedules": {
                    "high-tok-b": {
                        "enabled": True,
                        "timezone": "Asia/Shanghai",
                        "periods": [
                            {"id": "offpeak", "days": [1, 2, 3, 4, 5, 6, 7], "all_day": False, "start": "23:00", "end": "07:00", "mode": "multiplier", "multiplier": 0.5},
                        ],
                    },
                    "deleted-light": {
                        "enabled": True,
                        "timezone": "UTC",
                        "periods": [{"id": "d", "days": [1], "all_day": True, "mode": "multiplier", "multiplier": 0.5}],
                    },
                },
            },
        })
        self.assertEqual(saved["status"], "ok", saved)
        rev1 = saved["data"]["revision"]
        self.assertEqual(saved["data"]["pricing_multipliers"]["sup-high"], 0.5)
        # high-tok-b is now priced -> removed from unpriced alerts!
        self.assertNotIn("high-tok-b", [u["provider_id"] for u in saved["data"]["unpriced"]])

        conflict = await self.call(PREFIX + "/pricing", method="POST", payload={
            "revision": "initial", "changes": {"display_currency": "CNY"},
        })
        self.assertEqual(conflict["status"], "error")
        self.assertEqual(conflict["message"], "pricing_config_conflict")

        # 3) POST /pricing/sync-rates
        fx_svc = self.controller.web_api.model._exchange_rate_service(runtime.data_root)
        fx_svc._session_factory = lambda _t: _FakeSession(
            lambda _u: _FakeResponse(200, json.dumps({"result": "success", "rates": {"CNY": 7.31}}).encode())
        )
        synced_fx = await self.call(PREFIX + "/pricing/sync-rates", method="POST", payload={})
        self.assertEqual(synced_fx["status"], "ok", synced_fx)
        self.assertTrue(synced_fx["data"]["sync"]["synced"])
        self.assertEqual(synced_fx["data"]["exchange_rate"]["cached_usd_cny"], 7.31)

        # 4) POST /pricing/sync (mocked catalog sources)
        cat_svc = self.controller.web_api.model._pricing_catalog_service(runtime.data_root, self.controller.session)

        def catalog_handler(url):
            if "models.dev" in url:
                return _FakeResponse(200, json.dumps({
                    "openai": {"name": "OpenAI", "models": {"gpt-4o": {"id": "gpt-4o", "cost": {"input": 2.5, "output": 10.0}}}},
                }).encode())
            if "litellm" in url:
                return _FakeResponse(200, json.dumps({
                    "gpt-4o": {"mode": "chat", "input_cost_per_token": 2.4e-6, "output_cost_per_token": 9.5e-6},
                }).encode())
            return _FakeResponse(200, json.dumps({"data": []}).encode())

        cat_svc._session_factory = lambda _t: _FakeSession(catalog_handler)
        synced_cat = await self.call(PREFIX + "/pricing/sync", method="POST", payload={})
        self.assertEqual(synced_cat["status"], "ok", synced_cat)
        self.assertEqual(synced_cat["data"]["sync"]["ok_count"], 3)

        # 5) POST /pricing/sources/toggle
        toggled = await self.call(PREFIX + "/pricing/sources/toggle", method="POST", payload={
            "source_id": "openrouter", "enabled": False,
        })
        self.assertEqual(toggled["status"], "ok", toggled)
        or_status = next(s for s in toggled["data"]["sources"] if s["source_id"] == "openrouter")
        self.assertFalse(or_status["enabled"])
        self.assertEqual(or_status["status"], "disabled")

        # 6) POST /pricing/sources/detect (New API probe + URL dedup!)
        cat_svc._session_factory = lambda _t: _FakeSession(
            lambda _u: _FakeResponse(200, json.dumps({
                "success": True,
                "data": [{"model_name": "gpt-4o", "quota_type": 0, "model_ratio": 1.0, "completion_ratio": 4.0}],
            }).encode())
        )
        det = await self.call(PREFIX + "/pricing/sources/detect", method="POST", payload={"provider_id": "high-tok-a"})
        self.assertEqual(det["status"], "ok", det)
        self.assertTrue(det["data"]["detection"]["ok"])
        self.assertEqual(det["data"]["detection"]["source_id"], "newapi:high-tok-a")
        self.assertEqual(det["data"]["detection"]["models"], 1)
        # Second provider in same supplier (same api_base) deduplicates!
        det_dup = await self.call(PREFIX + "/pricing/sources/detect", method="POST", payload={"provider_id": "high-tok-b"})
        self.assertTrue(det_dup["data"]["detection"]["deduplicated"])
        self.assertEqual(det_dup["data"]["detection"]["source_id"], "newapi:high-tok-a")

        # 6B) POST /pricing/sources/currency (override CNY, reset auto, reject invalid currency & public source)
        cur_cny = await self.call(PREFIX + "/pricing/sources/currency", method="POST", payload={
            "source_id": "newapi:high-tok-a",
            "currency_override": "CNY",
        })
        self.assertEqual(cur_cny["status"], "ok", cur_cny)
        newapi_src = next(s for s in cur_cny["data"]["sources"] if s["source_id"] == "newapi:high-tok-a")
        self.assertEqual(newapi_src["currency"], "CNY")
        self.assertEqual(newapi_src["currency_override"], "CNY")

        cur_auto = await self.call(PREFIX + "/pricing/sources/currency", method="POST", payload={
            "source_id": "newapi:high-tok-a",
            "currency_override": "auto",
        })
        self.assertEqual(cur_auto["status"], "ok", cur_auto)
        newapi_src_auto = next(s for s in cur_auto["data"]["sources"] if s["source_id"] == "newapi:high-tok-a")
        self.assertEqual(newapi_src_auto["currency_override"], "auto")
        self.assertEqual(newapi_src_auto["currency"], "USD")
        self.assertNotIn("currency_override", PricingSettingsStore(runtime.data_root).read()["price_sources"]["newapi:high-tok-a"])

        cur_bad = await self.call(PREFIX + "/pricing/sources/currency", method="POST", payload={
            "source_id": "newapi:high-tok-a",
            "currency_override": "EUR",
        })
        self.assertEqual(cur_bad["status"], "error")
        cur_pub = await self.call(PREFIX + "/pricing/sources/currency", method="POST", payload={
            "source_id": "modelsdev",
            "currency_override": "CNY",
        })
        self.assertEqual(cur_pub["status"], "error")

        # 7) POST /pricing/select & 8) POST /pricing/select/reset
        selected = await self.call(PREFIX + "/pricing/select", method="POST", payload={
            "provider_id": "high-tok-a", "model": "gpt-4o", "price_key": "litellm:gpt-4o",
        })
        self.assertEqual(selected["status"], "ok", selected)
        self.assertEqual(selected["data"]["price_selections"]["high-tok-a"]["gpt-4o"], "litellm:gpt-4o")

        sel_reset = await self.call(PREFIX + "/pricing/select/reset", method="POST", payload={
            "provider_id": "high-tok-a", "model": "gpt-4o",
        })
        self.assertEqual(sel_reset["status"], "ok", sel_reset)
        self.assertNotIn("high-tok-a", sel_reset["data"]["price_selections"])

        # 9) POST /pricing/expr/validate (valid preview + attack rejection)
        val_ok = await self.call(PREFIX + "/pricing/expr/validate", method="POST", payload={
            "expr": "v1:len > 200000 ? p * 4 + c * 16 : p * 2 + c * 8",
            "sample": {"p": 100_000, "c": 10_000},
        })
        self.assertEqual(val_ok["status"], "ok", val_ok)
        self.assertTrue(val_ok["data"]["valid"])
        self.assertEqual(val_ok["data"]["usd_cny_rate"], 7.31)
        self.assertAlmostEqual(val_ok["data"]["samples"][0]["cost_usd"], 0.28, places=6)

        val_cny = await self.call(PREFIX + "/pricing/expr/validate", method="POST", payload={
            "expr": "v1:tier('标准', p * 0.75 + cr * 0.075 + c * 3.75)",
            "currency": "CNY",
            "sample": {"p": 1_000_000, "c": 1_000_000},
        })
        self.assertEqual(val_cny["status"], "ok", val_cny)
        self.assertTrue(val_cny["data"]["valid"])
        self.assertEqual(val_cny["data"]["currency"], "CNY")
        self.assertEqual(val_cny["data"]["samples"][0]["matched_tier"], "标准")
        self.assertAlmostEqual(val_cny["data"]["samples"][0]["cost_cny"], 4.5, places=6)
        self.assertAlmostEqual(val_cny["data"]["samples"][0]["cost_usd"], 4.5 / 7.31, places=5)

        val_bad_cur = await self.call(PREFIX + "/pricing/expr/validate", method="POST", payload={
            "expr": "v1:p * 2 + c * 8",
            "currency": "JPY",
        })
        self.assertEqual(val_bad_cur["status"], "error")

        val_bad = await self.call(PREFIX + "/pricing/expr/validate", method="POST", payload={
            "expr": "().__class__.__mro__",
        })
        self.assertEqual(val_bad["status"], "ok", val_bad)
        self.assertFalse(val_bad["data"]["valid"])

        val_unicode = await self.call(PREFIX + "/pricing/expr/validate", method="POST", payload={
            "expr": "p * 单价",
        })
        self.assertEqual(val_unicode["status"], "ok", val_unicode)
        self.assertFalse(val_unicode["data"]["valid"])

        # 10) POST /pricing/residue/clear -> clears deleted-heavy & deleted-light ONLY, keeps sup-high & high-tok-b!
        cleared = await self.call(PREFIX + "/pricing/residue/clear", method="POST", payload={})
        self.assertEqual(cleared["status"], "ok", cleared)
        self.assertEqual(cleared["data"]["cleared_provider_ids"], ["deleted-heavy", "deleted-light"])
        self.assertNotIn("deleted-heavy", cleared["data"]["pricing_rules"])
        self.assertNotIn("deleted-heavy", cleared["data"]["pricing_multipliers"])
        self.assertNotIn("deleted-light", cleared["data"]["pricing_schedules"])
        self.assertIn("high-tok-b|custom-unpriced-b", cleared["data"]["pricing_rules"])
        self.assertIn("sup-high", cleared["data"]["pricing_multipliers"])

        # 11) POST /pricing/reset -> clears custom rules/schedules/multipliers/selections while preserving FX!
        reset_res = await self.call(PREFIX + "/pricing/reset", method="POST", payload={"scope": "all"})
        self.assertEqual(reset_res["status"], "ok", reset_res)
        self.assertEqual(reset_res["data"]["pricing_rules"], {})
        self.assertEqual(reset_res["data"]["pricing_schedules"], {})
        self.assertEqual(reset_res["data"]["pricing_multipliers"], {})
        self.assertEqual(reset_res["data"]["exchange_rate"]["cached_usd_cny"], 7.31)

        # GET /model/maintenance embeds v2 pricing metadata without blocking
        maint = await self.call(PREFIX + "/maintenance", method="GET")
        self.assertEqual(maint["status"], "ok", maint)
        self.assertEqual(maint["data"]["pricing"]["version"], 2)
        self.assertEqual(maint["data"]["pricing"]["usd_cny_rate"], 7.31)

    def test_newapi_source_id_accepts_real_provider_ids_with_brackets_slashes_and_cjk(self):
        with TemporaryDirectory(prefix="companion-pricing-sid-") as tmp:
            store = PricingSettingsStore(Path(tmp))
            for pid in (
                "relay-a/[AN]gemini-2.5-pro-high",
                "relay-b/［抗截断］gemini-2.5-flash",
                "火山方舟/deepseek-v3",
            ):
                sid = f"newapi:{pid}"
                saved = store.upsert_newapi_source(
                    sid,
                    url="https://gateway.example.com/api/pricing",
                    provider_id=pid,
                    label=pid,
                    enabled=True,
                )
                self.assertIn(sid, saved["price_sources"])
                self.assertTrue(saved["price_sources"][sid]["enabled"])
                toggled = store.set_source_enabled(sid, False, revision=saved["revision"])
                self.assertFalse(toggled["price_sources"][sid]["enabled"])

            # Still rejects path traversal, control chars, whitespace padding, and unknown non-newapi sources!
            cur = store.read()
            for bad_sid in ("newapi:../escape", "newapi:bad\nid", "newapi: ", " newapi:ok", "evil:source"):
                with self.assertRaises(PricingSettingsError):
                    store.update(
                        cur["revision"],
                        {"price_sources": {bad_sid: {"enabled": True, "kind": "newapi", "url": "https://a.example/api/pricing"}}},
                    )

    async def test_maintenance_memoizes_per_pair_pricing_and_skips_redundant_catalog_fallback(self):
        from companion_v2.model_gateway import management as mgmt_mod
        from companion_v2.model_gateway import pricing as pricing_mod
        from companion_v2.model_gateway.pricing_catalog import _match_features, score_candidate_match

        # 1) Complete user per_token rule must not trigger catalog fallback scan.
        full_settings = {
            "version": 2,
            "use_builtin_presets": True,
            "pricing_rules": {
                "prov-full|gpt-4o": {
                    "mode": "per_token",
                    "currency": "USD",
                    "input": 2.0,
                    "input_cached": 0.5,
                    "cache_creation": 2.5,
                    "output": 8.0,
                    "multiplier": 1.0,
                    "free": False,
                    "configured": {
                        "input": True,
                        "input_cached": True,
                        "cache_creation": True,
                        "output": True,
                    },
                },
            },
        }
        with patch.object(
            pricing_mod,
            "_resolve_non_user_fallback",
            side_effect=AssertionError("complete rule must not scan catalog fallback"),
        ):
            eff = pricing_mod.resolve_effective_pricing("prov-full", "gpt-4o", full_settings, catalog={"prices": {}})
        self.assertIsNotNone(eff)
        self.assertEqual(eff.source, "model_override")

        # 2) _match_features lru_cache hits across repeated score_candidate_match calls.
        _match_features.cache_clear()
        s1 = score_candidate_match("openai/gpt-4o-2024-11-20", "gpt-4o")
        s2 = score_candidate_match("openai/gpt-4o-2024-11-20", "gpt-4o")
        self.assertEqual(s1, s2)
        self.assertGreaterEqual(_match_features.cache_info().hits, 2)

        # 3) ModelManagement.maintenance() resolves each unique (provider, model) pair ONCE across many core rows!
        (await self.controller.initialize_profile()).unwrap()
        session = self.controller.session
        runtime = self.controller.runtime
        target_ctx = session.profile.target
        from companion_v2.model_gateway.core_observations import record_observation
        from datetime import timedelta

        base_dt = self.controller.now() - timedelta(hours=2)
        for i in range(6):
            dt = base_dt + timedelta(minutes=i * 5)
            record_observation(
                runtime,
                target_ctx,
                {
                    "version": 1,
                    "revision": 1,
                    "turn_id": f"memo-turn-{i}",
                    "event_id": f"memo-evt-{i}",
                    "execution_path": "astrbot_core",
                    "received_at": dt.isoformat(),
                    "observed_at": (dt + timedelta(seconds=2)).isoformat(),
                    "generation_state": "completed",
                    "generation_finished_at": (dt + timedelta(seconds=1)).isoformat(),
                    "requests": [
                        {
                            "position": 1,
                            "state": "returned",
                            "started_at": dt.isoformat(),
                            "finished_at": (dt + timedelta(seconds=1)).isoformat(),
                            "provider": "memo-prov",
                            "usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 200,
                                "output_tokens": 100,
                                "tokens": 1100,
                                "availability": "provider_report",
                            },
                        }
                    ],
                },
            )

        orig_resolve = mgmt_mod.resolve_pricing_rule
        call_counts = {}

        def counting_resolve(pid, model="", *args, **kwargs):
            call_counts[(pid, model)] = call_counts.get((pid, model), 0) + 1
            return orig_resolve(pid, model, *args, **kwargs)

        with patch.object(mgmt_mod, "resolve_pricing_rule", side_effect=counting_resolve):
            maint_res = await self.call(PREFIX + "/maintenance", method="GET")
        self.assertEqual(maint_res["status"], "ok", maint_res)
        self.assertEqual(call_counts.get(("memo-prov", ""), 0), 1)


if __name__ == "__main__":
    unittest.main()
