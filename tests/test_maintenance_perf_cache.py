"""Correctness, invalidation, concurrency, host-resilience, and benchmark tests for maintenance caching."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from astrbot.api.web import PluginMultiDict, PluginRequest, bind_request_context
import astrbot_host.model_api as model_api
from astrbot_host.model_api import PREFIX
from companion_v2.diagnostics.inbound import InboundNoReplyLedger
from companion_v2.execution.models import digest, encode
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import ConversationRoute, PersonaBinding, PersonaSelector, SelectorKind
from companion_v2.model_gateway.budget import RELEASE_RECEIPT_PREFIX
from companion_v2.model_gateway.core_observations import record_observation
from companion_v2.model_gateway.maintenance_cache import (
    DAY_CACHE_MAX_ENTRIES,
    LEGACY_CACHE_MAX_ENTRIES,
    SNAPSHOT_CACHE_MAX_ENTRIES,
    clear_maintenance_cache,
    maintenance_cache_stats,
    runtime_maintenance_cache,
)
from companion_v2.model_gateway.management import ModelManagement, _legacy_matched_calls_cte
from companion_v2.model_gateway.pricing import PricingSettingsStore
from companion_v2.storage.runtime import StorageOpenError
from tests import test_astrbot_staging_host as support
from tests.test_host_application import HostFixture


def _make_signature(seed: int, *, context_count: int = 16, tool_count: int = 8) -> dict:
    ctx_hashes = [digest(f"ctx-{seed}-{i}") for i in range(context_count)]
    tool_parts = [digest(f"tool-{seed}-{i}") for i in range(tool_count)]
    sys_parts = [digest(f"sys-{seed}")]
    return {
        "version": 5,
        "model_hash": digest(f"model-{seed % 3}"),
        "system_hash": digest(sys_parts),
        "system_parts": sys_parts,
        "tools_hash": digest(tool_parts),
        "tool_parts": tool_parts,
        "context_sequence_hash": digest(ctx_hashes),
        "context_multiset_hash": digest(sorted(ctx_hashes)),
        "context_hashes": ctx_hashes,
        "context_material_count": len(ctx_hashes),
        "context_chars": 2400 + seed * 10,
        "context_bytes": 4800 + seed * 10,
        "context_observed": True,
        "estimates_observed": True,
        "context_est_tokens": 900 + seed,
        "system_est_tokens": 400,
        "tool_est_tokens": 300,
        "message_estimates_observed": True,
        "message_est_tokens": 200,
        "message_chars": 600,
        "message_bytes": 900,
        "request_estimate_observed": True,
        "request_est_tokens": 1800 + seed,
        "context_availability_observed": True,
        "context_availability_hash": digest({"deferred": [], "unavailable": []}),
        "deferred_material_count": 0,
        "unavailable_material_count": 0,
        "request_stats_observed": True,
        "message_count": 12,
        "system_message_count": 1,
        "system_chars": 1000,
        "system_bytes": 1500,
        "tool_count": len(tool_parts),
        "tool_bytes": 2048,
        "image_count": 0,
        "audio_count": 0,
        "video_count": 0,
    }


def _make_observation(
    turn_id: str,
    received_dt: datetime,
    *,
    revision: int = 1,
    provider: str = "core-provider-a",
    input_tokens: int = 12000,
    cached_tokens: int = 9000,
    output_tokens: int = 180,
    sig_seed: int = 1,
    context_count: int = 16,
    tool_count: int = 8,
    finished_dt: datetime | None = None,
    observed_dt: datetime | None = None,
) -> dict:
    started = (received_dt + timedelta(seconds=1)).isoformat()
    finished = (finished_dt or (received_dt + timedelta(seconds=3))).isoformat()
    observed = (observed_dt or (received_dt + timedelta(seconds=4))).isoformat()
    delivered = (received_dt + timedelta(seconds=4)).isoformat()
    return {
        "version": 1,
        "revision": revision,
        "turn_id": turn_id,
        "event_id": "evt-" + turn_id,
        "execution_path": "astrbot_core",
        "received_at": received_dt.isoformat(),
        "waiting_llm_at": (received_dt + timedelta(milliseconds=200)).isoformat(),
        "request_hook_at": (received_dt + timedelta(milliseconds=400)).isoformat(),
        "agent_started_at": (received_dt + timedelta(milliseconds=500)).isoformat(),
        "observed_at": observed,
        "generation_state": "completed",
        "generation_finished_at": finished,
        "request_observation_available": True,
        "request_observation_complete": True,
        "tool_requery_observed": False,
        "ttft_ms": 250,
        "runner_duration_ms": 2500,
        "usage_scope": "provider_response",
        "signature_scope": "provider_request_hook",
        "request_hook_signature": _make_signature(sig_seed, context_count=context_count, tool_count=tool_count),
        "preparation_steps": [
            {"name": "context_build", "state": "returned", "elapsed_ms": 12},
            {"name": "prompt_injection", "state": "returned", "elapsed_ms": 5},
        ],
        "materials": [{"owner": "memory", "purpose": "recall", "state": "ready", "elapsed_ms": 8, "chars": 420}],
        "offered_tools": ["read_file", "search_web"],
        "tools": [],
        "delivery": {
            "state": "returned",
            "attempts": 1,
            "returned": 1,
            "failed": 0,
            "unknown": 0,
            "first_started_at": finished,
            "first_returned_at": delivered,
            "last_returned_at": delivered,
        },
        "requests": [
            {
                "position": 1,
                "state": "returned",
                "started_at": started,
                "finished_at": finished,
                "latency_ms": 2000,
                "ttft_ms": 250,
                "provider": provider,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tokens": input_tokens + output_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_creation_tokens": 0,
                    "availability": "provider_report",
                    "cache_availability": "provider_report",
                    "cost_availability": "unavailable",
                },
                "signature": _make_signature(sig_seed, context_count=context_count, tool_count=tool_count),
                "signature_scope": "provider_request_hook",
                "measurement_basis": "provider_response",
            }
        ],
    }


def _strip_timestamps(report: dict) -> dict:
    """Compare everything except the intentionally fresh generated_at timestamps."""
    cloned = json.loads(json.dumps(report))
    cloned.pop("generated_at", None)
    if isinstance(cloned.get("reply_efficiency"), dict):
        cloned["reply_efficiency"].pop("generated_at", None)
    return cloned


class MaintenancePerfCacheTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host_instance = await self.start()
        self.app = self.host_instance.application
        batch = self.host_instance.receive(self.event("perf-cache-seed")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        self.role_context = self.context(self.app, batch)
        self.manager = ModelManagement(self.app, self.role_context)

    def _fresh_cold_maintenance(self, manager=None, *, pricing_revision=None) -> dict:
        target = manager or self.manager
        clear_maintenance_cache(self.app.runtime)
        return target.maintenance(self.core, pricing_revision=pricing_revision).unwrap()

    def _with_scope(self, context, scope, *, role=None, binding=None):
        resolved_binding = binding if binding is not None else replace(
            context.role_binding,
            scope=scope,
            role=role if role is not None else context.role_binding.role,
        )
        return replace(
            context,
            audience=replace(context.audience, scope=scope),
            role_binding=resolved_binding,
        )

    def _seed_multi_day_observations(self, *, days: int = 5, per_day: int = 3, today_turns: int = 2):
        now = self.app.now()
        base_day = now.replace(hour=10, minute=0, second=0, microsecond=0)
        seq = 0
        # Historical days (strictly before today)
        for day_offset in range(days, 0, -1):
            day_base = base_day - timedelta(days=day_offset)
            for idx in range(per_day):
                seq += 1
                dt = day_base + timedelta(minutes=idx * 15)
                group_scope = replace(
                    self.role_context.scope,
                    kind=ScopeKind.GROUP,
                    native_scope_id="group-chat-1",
                    conversation_id="group-chat-1",
                    session_id="group-chat-1",
                )
                ctx = self.role_context if idx % 2 == 0 else self._with_scope(self.role_context, group_scope)
                obs = _make_observation(
                    f"hist-turn-{day_offset}-{idx}",
                    dt,
                    provider="core-provider-a" if seq % 4 else "core-provider-b",
                    input_tokens=10000 + seq * 100,
                    cached_tokens=8000 if seq % 3 else 3000,
                    output_tokens=150 + seq,
                    sig_seed=seq // 2,
                )
                record_observation(self.app.runtime, ctx, obs)
        # Today's turns (before now)
        today_start = now.replace(hour=0, minute=10, second=0, microsecond=0)
        for idx in range(today_turns):
            seq += 1
            dt = min(today_start + timedelta(minutes=idx * 5), now - timedelta(seconds=30 - idx * 5))
            obs = _make_observation(
                f"today-turn-{idx}",
                dt,
                provider="core-provider-a",
                input_tokens=14000 + idx * 500,
                cached_tokens=11000 - idx * 1000,
                output_tokens=200 + idx * 10,
                sig_seed=100 + idx,
            )
            record_observation(self.app.runtime, self.role_context, obs)

    async def test_warm_snapshot_hit_parity_fresh_timestamps_and_mutation_isolation(self):
        self._seed_multi_day_observations(days=4, per_day=3, today_turns=2)
        clear_maintenance_cache(self.app.runtime)

        cold = self.manager.maintenance(self.core).unwrap()
        stats_after_cold = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats_after_cold["snapshot_misses"], 1)
        self.assertEqual(stats_after_cold["snapshot_hits"], 0)

        # Mutate returned dict deeply and ensure cache stays uncorrupted.
        cold_copy_for_compare = json.loads(json.dumps(cold))
        cold["summary"]["tokens"] = -999999
        cold["recent"].clear()
        cold["usage_sources"]["astrbot_core"]["cache_diagnostics"]["recent_events"].clear()

        # Advance clock by 45s on the same local day (simulating 45s poll).
        self.advance_seconds(45)
        warm = ModelManagement(self.app, self.role_context).maintenance(self.core).unwrap()
        stats_after_warm = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats_after_warm["snapshot_hits"], 1)
        self.assertEqual(warm["generated_at"], self.app.now().isoformat())
        self.assertEqual(warm["reply_efficiency"]["generated_at"], self.app.now().isoformat())
        self.assertNotEqual(warm["generated_at"], cold_copy_for_compare["generated_at"])
        self.assertEqual(_strip_timestamps(warm), _strip_timestamps(cold_copy_for_compare))

        # Write to an unrelated table (runtime_metadata): db_write_token changes,
        # rich watermark still matches, so snapshot cache STILL hits!
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("unrelated.heartbeat.v1", '{"ok":true}'),
            )
        self.advance_seconds(45)
        after_unrelated = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_hits"], 2)
        self.assertEqual(_strip_timestamps(after_unrelated), _strip_timestamps(cold_copy_for_compare))

    def advance_seconds(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))

    async def test_incremental_new_today_turn_and_revision_update_exact_parity(self):
        self._seed_multi_day_observations(days=5, per_day=3, today_turns=1)
        clear_maintenance_cache(self.app.runtime)

        # Cold run populates both snapshot and historical-day caches.
        first = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["day_cache_misses"], 1)

        # Append a new turn today -> snapshot misses, historical-day cache AND legacy cache HIT!
        self.advance_seconds(60)
        new_turn_dt = self.app.now() - timedelta(seconds=10)
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation(
                "today-turn-incremental-new",
                new_turn_dt,
                revision=1,
                provider="core-provider-b",
                input_tokens=18000,
                cached_tokens=6000,
                output_tokens=320,
                sig_seed=777,
            ),
        )
        incremental = self.manager.maintenance(self.core).unwrap()
        stats = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats["day_cache_hits"], 1)
        self.assertEqual(stats["legacy_cache_hits"], 1)
        self.assertGreater(incremental["totals"]["tokens"], first["totals"]["tokens"])

        # Compare byte-for-byte against a completely cold uncached run!
        cold_expected = self._fresh_cold_maintenance()
        self.assertEqual(incremental, cold_expected)

        # Re-populate cache, then perform an in-place revision=2 update on the same today turn.
        self.manager.maintenance(self.core).unwrap()
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation(
                "today-turn-incremental-new",
                new_turn_dt,
                revision=2,
                provider="core-provider-b",
                input_tokens=19500,
                cached_tokens=12000,
                output_tokens=450,
                sig_seed=778,
                observed_dt=new_turn_dt + timedelta(seconds=9),
            ),
        )
        updated = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(updated, self._fresh_cold_maintenance())

    async def test_midnight_day_rollover_advances_day_cache_with_exact_parity(self):
        self._seed_multi_day_observations(days=7, per_day=2, today_turns=2)
        clear_maintenance_cache(self.app.runtime)

        day1 = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["day_cache_misses"], 1)

        # Roll clock past midnight into the next calendar day!
        next_day = (self.app.now() + timedelta(days=1)).replace(hour=1, minute=15, second=0, microsecond=0)
        self.clock.set(next_day)

        # Record a brand-new turn on Day 2 (comparing against Day 1's last signature across midnight!).
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation(
                "day2-first-turn",
                next_day - timedelta(seconds=15),
                provider="core-provider-a",
                input_tokens=16000,
                cached_tokens=13000,
                output_tokens=210,
                sig_seed=901,
            ),
        )
        day2_incremental = self.manager.maintenance(self.core).unwrap()
        stats = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats["day_cache_advances"], 1)
        self.assertEqual(stats["day_cache_misses"], 1)
        self.assertNotEqual(day2_incremental["daily"][-1]["key"], day1["daily"][-1]["key"])

        day2_cold = self._fresh_cold_maintenance()
        self.assertEqual(day2_incremental, day2_cold)

    async def test_historical_backfill_and_in_place_historical_update_invalidate_day_cache(self):
        self._seed_multi_day_observations(days=4, per_day=2, today_turns=1)
        clear_maintenance_cache(self.app.runtime)
        self.manager.maintenance(self.core).unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["day_cache_misses"], 1)

        # Backfill a new observation two days in the past!
        backfill_dt = (self.app.now() - timedelta(days=2)).replace(hour=14, minute=30, second=0, microsecond=0)
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation(
                "backfill-turn-2d-ago",
                backfill_dt,
                revision=1,
                provider="core-provider-c",
                input_tokens=22000,
                cached_tokens=11000,
                output_tokens=300,
                sig_seed=555,
            ),
        )
        after_backfill = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["day_cache_misses"], 2)
        self.assertEqual(after_backfill, self._fresh_cold_maintenance())

        # Re-warm, then update that historical row IN-PLACE (revision=2, same rowid, same count!).
        self.manager.maintenance(self.core).unwrap()
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation(
                "backfill-turn-2d-ago",
                backfill_dt,
                revision=2,
                provider="core-provider-c",
                input_tokens=25000,
                cached_tokens=15000,
                output_tokens=400,
                sig_seed=556,
                observed_dt=backfill_dt + timedelta(seconds=25),
            ),
        )
        after_hist_update = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(after_hist_update, self._fresh_cold_maintenance())

    async def test_pricing_revision_and_pricing_file_changes_invalidate_immediately(self):
        self._seed_multi_day_observations(days=2, per_day=2, today_turns=1)
        clear_maintenance_cache(self.app.runtime)

        first = self.manager.maintenance(self.core, pricing_revision="rev-1").unwrap()
        self.manager.maintenance(self.core, pricing_revision="rev-1").unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_hits"], 1)

        # Changing pricing_revision MUST miss immediately.
        second = self.manager.maintenance(self.core, pricing_revision="rev-2").unwrap()
        stats = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats["snapshot_hits"], 1)
        self.assertEqual(stats["snapshot_misses"], 2)
        self.assertEqual(_strip_timestamps(first), _strip_timestamps(second))

        # Touching/writing <runtime.data_root>/pricing/settings.json MUST also invalidate immediately!
        pricing_dir = Path(self.app.runtime.data_root) / "pricing"
        pricing_dir.mkdir(parents=True, exist_ok=True)
        settings_path = pricing_dir / "settings.json"
        settings_path.write_text('{"revision": 1}', encoding="utf-8")
        self.manager.maintenance(self.core, pricing_revision="rev-2").unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_misses"], 3)

        settings_path.write_text('{"revision": 2, "display_currency": "CNY"}', encoding="utf-8")
        self.manager.maintenance(self.core, pricing_revision="rev-2").unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_misses"], 4)

    async def test_scheduled_per_request_cost_matches_uncached_across_midnight_and_price_change(self):
        now = datetime(2026, 11, 22, 23, 45, tzinfo=timezone.utc)
        self.clock.set(now)
        store = PricingSettingsStore(self.app.runtime.data_root)
        provider = "core-provider-a"
        rule = {"mode": "per_request", "currency": "USD", "price": 0.002}
        settings = store.update(store.read_safe()["revision"], {
            "use_builtin_presets": False,
            "pricing_rules": {provider: rule},
            "pricing_schedules": {provider: {
                "enabled": True, "timezone": "UTC", "periods": [{
                    "days": [1, 2, 3, 4, 5, 6, 7], "all_day": False,
                    "start": "23:00", "end": "01:00", "mode": "multiplier", "multiplier": 0.5,
                }],
            }},
        })

        def add_turn(turn_id, received):
            observation = _make_observation(turn_id, received, provider=provider)
            first_request = observation["requests"][0]
            observation["requests"].append({**first_request, "position": 2})
            record_observation(self.app.runtime, self.role_context, observation)

        def assert_priced(total, expected_calls):
            actual = self.manager.maintenance(self.core).unwrap()
            with patch("companion_v2.model_gateway.management.runtime_maintenance_cache", return_value=None):
                uncached = self.manager.maintenance(self.core).unwrap()
            self.assertEqual(actual, uncached)
            core = actual["usage_sources"]["astrbot_core"]
            self.assertEqual(core["cost_availability"], "estimated_local")
            self.assertEqual(core["totals"]["estimated_cost_microusd"], total)
            self.assertEqual(sum(call["cost_basis"] == "estimated_local" for call in core["recent"]), expected_calls)
            self.assertEqual(sum(call["cost_microusd"] for call in core["recent"]), total)

        add_turn("regular-price", now - timedelta(days=1, hours=1))
        add_turn("evening-discount", now - timedelta(minutes=15))
        assert_priced(3000, 4)
        assert_priced(3000, 4)
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_hits"], 1)

        self.clock.set(now + timedelta(minutes=30))
        add_turn("morning-discount", now + timedelta(minutes=25))
        assert_priced(4000, 6)
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["day_cache_advances"], 1)

        store.update(settings["revision"], {"pricing_rules": {provider: {**rule, "price": 0.004}}})
        assert_priced(8000, 6)

    async def test_multi_role_isolation_and_maintenance_table_mutations(self):
        self._seed_multi_day_observations(days=2, per_day=2, today_turns=1)

        # Create a second distinct role context sharing the SAME StorageRuntime.
        other_role = replace(self.role_context.role_binding.role, subject_id="role-second")
        other_scope = replace(
            self.role_context.scope,
            native_scope_id="private-chat-second",
            conversation_id="private-chat-second",
            session_id="private-chat-second",
        )
        self.personas.register_route(ConversationRoute(other_scope, user=self.important.subject), observed_at=self.app.now())
        self.personas.set_binding(
            PersonaBinding(
                "role-second-binding",
                PersonaSelector(
                    other_scope.installation_id,
                    SelectorKind.CONVERSATION,
                    bot_id=other_scope.bot_id,
                    platform=other_scope.platform,
                    account_id=other_scope.account_id,
                    conversation_kind=other_scope.kind,
                    conversation_id=other_scope.conversation_id,
                ),
                other_role,
                0,
                self.app.now(),
                "fixture",
                "admin",
            ),
            expected_revision=0,
            observed_at=self.app.now(),
        )
        resolved_role = self.personas.resolve(other_scope, observed_at=self.app.now()).unwrap()
        other_context = self._with_scope(self.role_context, other_scope, binding=resolved_role.binding)
        record_observation(
            self.app.runtime,
            other_context,
            _make_observation(
                "other-role-turn-1",
                self.app.now() - timedelta(minutes=2),
                provider="other-role-provider",
                input_tokens=5000,
                cached_tokens=2000,
                output_tokens=100,
                sig_seed=42,
            ),
        )

        clear_maintenance_cache(self.app.runtime)
        other_manager = ModelManagement(self.app, other_context)
        rep_a = self.manager.maintenance(self.core).unwrap()
        rep_b = other_manager.maintenance(self.core).unwrap()
        self.assertNotEqual(rep_a["totals"]["tokens"], rep_b["totals"]["tokens"])
        self.assertTrue(all(item["provider"] != "other-role-provider" for item in rep_a["usage_sources"]["astrbot_core"]["recent"]))
        self.assertTrue(any(item["provider"] == "other-role-provider" for item in rep_b["usage_sources"]["astrbot_core"]["recent"]))

        # Verify warm hits stay isolated per role.
        rep_a2 = self.manager.maintenance(self.core).unwrap()
        rep_b2 = other_manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_a, rep_a2)
        self.assertEqual(rep_b, rep_b2)
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_hits"], 2)

        # Verify inbound_no_replies mutation invalidates snapshot cache immediately.
        InboundNoReplyLedger(self.app.runtime).record(
            reason_code="session_reply_disabled",
            observed_at=self.app.now(),
            channel="private",
            origin_key=self.role_context.key,
            event_ref="f" * 32,
        )
        after_no_reply = self.manager.maintenance(self.core).unwrap()
        self.assertTrue(any(row["event_ref"] == "f" * 32 for row in after_no_reply["runtime"]["inbound_no_replies"]))

        # Verify in-memory ledger.policy toggle invalidates snapshot cache immediately.
        self.app.gateway.ledger.policy = replace(self.app.gateway.ledger.policy, enabled=False)
        after_policy = self.manager.maintenance(self.core).unwrap()
        self.assertFalse(after_policy["budget"]["hard_limit_enabled"])
        self.assertIsNone(after_policy["budget"]["remaining_tokens"])

    async def test_midnight_straddling_out_of_order_turn_and_future_observation_fallback(self):
        # Move to 01:00 of the next day so DeterministicClock moves strictly forward.
        now = (self.app.now() + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
        self.clock.set(now)
        # Turn A received before midnight, finished at 00:05 today (crosses midnight!).
        turn_a_recv = now.replace(hour=0, minute=0, second=0) - timedelta(seconds=10)
        turn_a_fin = now.replace(hour=0, minute=5, second=0)
        # Turn B received at 00:01 today, finished at 00:02 today (earlier finished_at than Turn A!).
        turn_b_recv = now.replace(hour=0, minute=1, second=0)
        turn_b_fin = now.replace(hour=0, minute=2, second=0)

        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation("straddle-a", turn_a_recv, finished_dt=turn_a_fin, sig_seed=10),
        )
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation("today-b", turn_b_recv, finished_dt=turn_b_fin, sig_seed=11),
        )
        # Also insert a future-dated turn (+30 min) that must stay hidden until clock advances past it.
        future_dt = now + timedelta(minutes=30)
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation("future-c", future_dt, sig_seed=12),
        )

        clear_maintenance_cache(self.app.runtime)
        cached_now = self.manager.maintenance(self.core).unwrap()
        # Compare against direct uncached computation (cache bypassed via None runtime cache).
        with patch("companion_v2.model_gateway.management.runtime_maintenance_cache", return_value=None):
            direct_now = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(cached_now, direct_now)
        self.assertEqual(cached_now["usage_sources"]["astrbot_core"]["coverage"]["turns"], 2)

        # Advance clock past future_dt -> snapshot must NOT serve stale 2-turn payload!
        self.clock.set(future_dt + timedelta(minutes=1))
        after_future = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(after_future["usage_sources"]["astrbot_core"]["coverage"]["turns"], 3)
        with patch("companion_v2.model_gateway.management.runtime_maintenance_cache", return_value=None):
            direct_after = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(after_future, direct_after)

    async def test_thread_safety_and_bounded_lru_eviction(self):
        self._seed_multi_day_observations(days=3, per_day=2, today_turns=2)
        clear_maintenance_cache(self.app.runtime)

        # Exercise > max_entries distinct pricing_revisions to verify strict LRU bounds.
        for idx in range(SNAPSHOT_CACHE_MAX_ENTRIES + 9):
            self.manager.maintenance(self.core, pricing_revision=f"rev-{idx}").unwrap()
        stats = maintenance_cache_stats(self.app.runtime)
        self.assertLessEqual(stats["snapshot_entries"], SNAPSHOT_CACHE_MAX_ENTRIES)
        self.assertLessEqual(stats["day_entries"], DAY_CACHE_MAX_ENTRIES)
        self.assertLessEqual(stats["legacy_entries"], LEGACY_CACHE_MAX_ENTRIES)
        self.assertEqual(stats["snapshot_entries"], SNAPSHOT_CACHE_MAX_ENTRIES)
        self.assertEqual(stats["day_entries"], DAY_CACHE_MAX_ENTRIES)

        # Concurrent multi-threaded readers + concurrent observation writer.
        errors = []
        results = []
        results_lock = threading.Lock()

        def reader_worker(worker_id: int):
            try:
                mgr = ModelManagement(self.app, self.role_context)
                for step in range(8):
                    res = mgr.maintenance(self.core, pricing_revision=f"concurrent-{step % 2}").unwrap()
                    with results_lock:
                        results.append(res["usage_sources"]["astrbot_core"]["coverage"]["turns"])
            except BaseException as exc:  # pragma: no cover
                errors.append((worker_id, exc))

        def writer_worker():
            try:
                for step in range(4):
                    record_observation(
                        self.app.runtime,
                        self.role_context,
                        _make_observation(
                            f"concurrent-turn-{step}",
                            self.app.now() - timedelta(seconds=20 - step),
                            sig_seed=300 + step,
                        ),
                    )
                    time.sleep(0.005)
            except BaseException as exc:  # pragma: no cover
                errors.append(("writer", exc))

        workers = [threading.Thread(target=reader_worker, args=(i,)) for i in range(4)]
        workers.append(threading.Thread(target=writer_worker))
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive())
        # Keep Thread references alive until runtime.close() closes their thread-local SQLite handles.
        self._keep_threads = workers

        self.assertEqual(errors, [])
        self.assertTrue(results)
        # Final state after all writes must match cold computation exactly.
        final_cached = self.manager.maintenance(self.core, pricing_revision="concurrent-0").unwrap()
        self.assertEqual(final_cached, self._fresh_cold_maintenance(pricing_revision="concurrent-0"))

    async def test_cross_thread_fast_hits_and_external_sqlite_update_invalidation(self):
        self._seed_multi_day_observations(days=2, per_day=2, today_turns=2)
        clear_maintenance_cache(self.app.runtime)
        cold = self.manager.maintenance(self.core).unwrap()

        # Distinct worker threads (matching asyncio.to_thread pools) must hit the O(1) fast path
        # without falling back to rich legacy watermarks despite separate per-thread SQLite handles.
        thread_results = []
        thread_errors = []

        def poll_worker():
            try:
                thread_results.append(self.manager.maintenance(self.core).unwrap())
            except BaseException as exc:  # pragma: no cover - surfaced below
                thread_errors.append(exc)

        workers = [threading.Thread(target=poll_worker) for _ in range(3)]
        with patch(
            "companion_v2.model_gateway.maintenance_cache.read_legacy_watermark",
            side_effect=AssertionError("cross-thread warm poll must not fall back to rich watermark"),
        ):
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=15)
                self.assertFalse(worker.is_alive())
        self._keep_threads = workers

        self.assertEqual(thread_errors, [])
        self.assertEqual(len(thread_results), 3)
        for rep in thread_results:
            self.assertEqual(_strip_timestamps(rep), _strip_timestamps(cold))
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_hits"], 3)

        # An external raw sqlite3.connect() in-place UPDATE (bypassing StorageRuntime._connection)
        # must still invalidate the snapshot cache immediately.
        ext_conn = sqlite3.connect(str(self.app.runtime.database_path), timeout=5.0)
        try:
            ext_conn.execute("UPDATE model_budget_accounts SET token_limit = token_limit + 777")
            ext_conn.commit()
        finally:
            ext_conn.close()

        after_ext = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(after_ext["budget"]["limit_tokens"], cold["budget"]["limit_tokens"] + 777)
        self.assertEqual(after_ext, self._fresh_cold_maintenance())

        # External edits can leave revision, timestamps and JSON length unchanged.
        with sqlite3.connect(str(self.app.runtime.database_path)) as ext_conn:
            ext_conn.execute("""
                UPDATE astrbot_core_reply_observations
                SET record_json = json_set(record_json, '$.requests[0].usage.tokens',
                    json_extract(record_json, '$.requests[0].usage.tokens') + 10)
                WHERE turn_id IN ('hist-turn-1-0', 'today-turn-0')
            """)
        ext_conn.close()
        after_usage_edit = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(after_usage_edit["totals"]["tokens"], after_ext["totals"]["tokens"] + 20)
        self.assertEqual(after_usage_edit, self._fresh_cold_maintenance())

    async def test_in_place_auxiliary_and_legacy_mutations_never_serve_stale_snapshots(self):
        # Create a second conversation batch so we have an older batch + a newer batch (with larger timestamps).
        self.advance_seconds(120)
        batch2 = self.host_instance.receive(self.event("perf-cache-seed-2")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch2)

        batches = self.app.runtime.read_all(
            "SELECT batch_id, generation_task_id, created_at FROM conversation_batches "
            "WHERE origin_key = ? ORDER BY created_at ASC",
            (self.role_context.key,),
        )
        self.assertGreaterEqual(len(batches), 2)
        older_batch_id = batches[0]["batch_id"]
        older_task_id = batches[0]["generation_task_id"]
        newer_batch_id = batches[-1]["batch_id"]
        newer_task_id = batches[-1]["generation_task_id"]

        # Seed a tool call, an extension invocation, a failure, a diagnostic, and a released reservation on real rows.
        older_call = self.app.runtime.read_one(
            "SELECT call_id, attempt_id FROM model_calls WHERE task_id = ? ORDER BY position LIMIT 1",
            (older_task_id,),
        )
        newer_call = self.app.runtime.read_one(
            "SELECT call_id FROM model_calls WHERE task_id = ? ORDER BY position LIMIT 1",
            (newer_task_id,),
        )
        self.assertIsNotNone(older_call)
        self.assertIsNotNone(newer_call)
        now_iso = self.app.now().isoformat()
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE conversation_flows SET state = 'collecting' WHERE batch_id = ?",
                (newer_batch_id,),
            )
            conn.execute(
                "INSERT INTO conversation_tool_calls "
                "(task_id, tool_call_id, batch_id, sequence, model_call_id, attempt_id, action, arguments_json, "
                "state, effect, created_at, finished_at) "
                "VALUES (?, 'tc-1', ?, 1, ?, ?, 'search', '{}', 'started', 'none', ?, NULL)",
                (older_task_id, older_batch_id, older_call["call_id"], older_call["attempt_id"], now_iso),
            )
            conn.execute(
                "INSERT INTO extension_tool_invocations "
                "(invocation_id, parent_task_id, tool_call_id, batch_id, task_id, action, descriptor_json, "
                "provider_json, arguments_json, state, effect, created_at, finished_at) "
                "VALUES ('inv-1', ?, 'tc-1', ?, 'ext-task-1', 'search', '{}', '{}', '{}', 'queued', 'none', ?, NULL)",
                (older_task_id, older_batch_id, now_iso),
            )
            conn.execute(
                "INSERT INTO model_call_failures (call_id, code, observed_at) "
                "VALUES (?, 'provider_request_failed', ?)",
                (older_call["call_id"], now_iso),
            )
            diag_a = json.dumps({
                "version": 2, "completion_present": True, "finish_reason": "stop",
                "parse_state": "validated", "raw": None, "parsed": None, "visible": None,
                "failure_code": None, "error_type": None, "error_code": None, "status_code": 200,
            }, sort_keys=True)
            conn.execute(
                "INSERT OR REPLACE INTO model_call_diagnostics (call_id, summary_json, observed_at) "
                "VALUES (?, ?, ?)",
                (older_call["call_id"], diag_a, now_iso),
            )
            # Add a non-main-chain released reservation row so budget.released_cost_microusd is exercised.
            role_address = [
                self.role_context.scope.installation_id,
                self.role_context.scope.bot_id,
                asdict(self.role_context.role_binding.role),
            ]
            today_account = digest(["role", role_address, self.app.now().date().isoformat()])
            conn.execute(
                "INSERT INTO execution_tasks "
                "(task_id, origin_key, kind, lane, dedupe_key, request_hash, revision, state, generation, attempt_no, "
                "attempt_id, next_run_at, deadline_at, created_at, updated_at, record_json) "
                "VALUES ('bg-rel-task', ?, 'memory.reflect', 'background', 'dedupe-bg-rel', 'req-hash-bg', 1, "
                "'failed', 1, 1, 'bg-rel-att', ?, ?, ?, ?, '{}')",
                (self.role_context.key, now_iso, now_iso, now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO execution_attempts "
                "(attempt_id, task_id, attempt_no, generation, lane, kind, origin_key, scope_key, slot_held, lease_until, record_json) "
                "VALUES ('bg-rel-att', 'bg-rel-task', 1, 1, 'background', 'memory.reflect', ?, 'scope-bg', 0, ?, '{}')",
                (self.role_context.key, now_iso),
            )
            conn.execute(
                "INSERT INTO model_requests (task_id, request_hash, plan_json, created_at) "
                "VALUES ('bg-rel-task', 'req-hash-bg', '{}', ?)",
                (now_iso,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO model_budget_accounts "
                "(account_key, policy_hash, token_limit, cost_limit, held_tokens, held_cost, spent_tokens, spent_cost) "
                "VALUES (?, 'policy-hash-bg', 100000, 100000, 0, 0, 0, 0)",
                (today_account,),
            )
            conn.execute(
                "INSERT INTO model_calls "
                "(call_id, task_id, attempt_id, position, state, provider_json, reserved_tokens, reserved_cost, "
                "actual_tokens, actual_cost, receipt_ref, started_at, finished_at) "
                "VALUES ('bg-rel-call', 'bg-rel-task', 'bg-rel-att', 0, 'discarded', '{\"provider\":\"p\"}', "
                "100, 500, NULL, NULL, ?, ?, ?)",
                (RELEASE_RECEIPT_PREFIX + "test-1", now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO model_call_accounts (call_id, account_key) VALUES ('bg-rel-call', ?)",
                (today_account,),
            )

        clear_maintenance_cache(self.app.runtime)
        baseline = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(baseline["runtime"]["conversation"][0]["flow_state"], "collecting")
        self.assertEqual(baseline["budget"]["released_cost_microusd"], 500)

        # 1a. conversation_flows: same-length state transition 'collecting'(10) -> 'generating'(10)
        with self.app.runtime.transaction() as conn:
            conn.execute("UPDATE conversation_flows SET state = 'generating' WHERE batch_id = ?", (newer_batch_id,))
        rep = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep["runtime"]["conversation"][0]["flow_state"], "generating")
        self.assertEqual(rep, self._fresh_cold_maintenance())

        # 1b. conversation_flows: updating deadline_at surfaces in runtime.debounce_trace
        new_deadline = (self.app.now() + timedelta(minutes=9)).isoformat()
        with self.app.runtime.transaction() as conn:
            conn.execute("UPDATE conversation_flows SET deadline_at = ? WHERE batch_id = ?", (new_deadline, newer_batch_id))
        rep = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep["runtime"]["debounce_trace"][0]["deadline_at"], new_deadline)
        self.assertEqual(rep, self._fresh_cold_maintenance())

        # 2. conversation_parts: older batch part 'pending' -> 'dispatched' with dispatched_at < newer batch's MAX
        older_part = self.app.runtime.read_one(
            "SELECT part_id, dispatched_at FROM conversation_parts WHERE batch_id = ? ORDER BY sequence LIMIT 1",
            (older_batch_id,),
        )
        self.assertIsNotNone(older_part)
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE conversation_parts SET state = 'pending', dispatched_at = NULL WHERE batch_id = ?",
                (older_batch_id,),
            )
        rep_pending = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_pending, self._fresh_cold_maintenance())
        earlier_dispatched = (self.app.now() - timedelta(seconds=90)).isoformat()
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE conversation_parts SET state = 'dispatched', dispatched_at = ? WHERE part_id = ?",
                (earlier_dispatched, older_part["part_id"]),
            )
        rep_dispatched = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_dispatched, self._fresh_cold_maintenance())
        older_eff = next(item for item in rep_dispatched["reply_efficiency"]["recent"] if item["batch_id"] == older_batch_id)
        self.assertEqual(older_eff["timestamps"]["first_dispatched_at"], earlier_dispatched)

        # 3a. conversation_tool_calls: state 'started' -> 'failed' with finished_at still NULL
        # (and extension_tool_invocations: state 'queued' -> 'running' with finished_at still NULL)
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE extension_tool_invocations SET state = 'running' WHERE invocation_id = 'inv-1'"
            )
        rep_ext = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_ext, self._fresh_cold_maintenance())
        older_eff_ext = next(item for item in rep_ext["reply_efficiency"]["recent"] if item["batch_id"] == older_batch_id)
        self.assertEqual(older_eff_ext["trace"]["tool_calls"][0]["state"], "running")

        with self.app.runtime.transaction() as conn:
            conn.execute("DELETE FROM extension_tool_invocations WHERE invocation_id = 'inv-1'")
        self.manager.maintenance(self.core).unwrap()
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE conversation_tool_calls SET state = 'failed' WHERE tool_call_id = 'tc-1'"
            )
        rep_tc = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_tc, self._fresh_cold_maintenance())
        older_eff_tc = next(item for item in rep_tc["reply_efficiency"]["recent"] if item["batch_id"] == older_batch_id)
        self.assertEqual(older_eff_tc["trace"]["tool_calls"][0]["state"], "failed")

        # 4a. conversation_batches: state 'ready' -> 'cancelled'
        with self.app.runtime.transaction() as conn:
            conn.execute("UPDATE conversation_batches SET state = 'cancelled' WHERE batch_id = ?", (newer_batch_id,))
        rep_batch_state = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_batch_state["runtime"]["conversation"][0]["batch_state"], "cancelled")
        self.assertEqual(rep_batch_state, self._fresh_cold_maintenance())

        # 4b. conversation_batches: same-length contributions_json update WITHOUT revision bump
        contrib_a = json.dumps({"materials": [{"owner": "alpha", "purpose": "mem", "state": "ready"}]})
        contrib_b = json.dumps({"materials": [{"owner": "bravo", "purpose": "mem", "state": "ready"}]})
        self.assertEqual(len(contrib_a), len(contrib_b))
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE conversation_batches SET contributions_json = ? WHERE batch_id = ?",
                (contrib_a, newer_batch_id),
            )
        rep_mat_a = self.manager.maintenance(self.core).unwrap()
        self.assertTrue(any(m["owner"] == "alpha" for m in rep_mat_a["runtime"]["prompt_materials"]))
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE conversation_batches SET contributions_json = ? WHERE batch_id = ?",
                (contrib_b, newer_batch_id),
            )
        rep_mat_b = self.manager.maintenance(self.core).unwrap()
        self.assertTrue(any(m["owner"] == "bravo" for m in rep_mat_b["runtime"]["prompt_materials"]))
        self.assertFalse(any(m["owner"] == "alpha" for m in rep_mat_b["runtime"]["prompt_materials"]))
        self.assertEqual(rep_mat_b, self._fresh_cold_maintenance())

        # 5a. model_calls.reserved_cost in-place update -> budget.released_cost_microusd
        with self.app.runtime.transaction() as conn:
            conn.execute("UPDATE model_calls SET reserved_cost = 9999 WHERE call_id = 'bg-rel-call'")
        rep_cost = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(rep_cost["budget"]["released_cost_microusd"], 9999)
        self.assertEqual(rep_cost, self._fresh_cold_maintenance())

        # 5b. model_call_failures: same-length code update 'provider_request_failed'(23) -> 'provider_recheck_failed'(23)
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE model_call_failures SET code = 'provider_recheck_failed' WHERE call_id = ?",
                (older_call["call_id"],),
            )
        rep_fail = self.manager.maintenance(self.core).unwrap()
        self.assertTrue(any(row.get("failure") == "provider_recheck_failed" for row in rep_fail["recent"]))
        self.assertEqual(rep_fail, self._fresh_cold_maintenance())

        # 5c. model_call_diagnostics: same-length summary_json in-place update (status_code 200 -> 503)
        diag_b = json.dumps({
            "version": 2, "completion_present": True, "finish_reason": "stop",
            "parse_state": "validated", "raw": None, "parsed": None, "visible": None,
            "failure_code": None, "error_type": None, "error_code": None, "status_code": 503,
        }, sort_keys=True)
        self.assertEqual(len(diag_a), len(diag_b))
        with self.app.runtime.transaction() as conn:
            conn.execute(
                "UPDATE model_call_diagnostics SET summary_json = ? WHERE call_id = ?",
                (diag_b, older_call["call_id"]),
            )
        rep_diag = self.manager.maintenance(self.core).unwrap()
        self.assertTrue(
            any((row.get("response_diagnostics") or {}).get("status_code") == 503 for row in rep_diag["recent"])
        )
        self.assertEqual(rep_diag, self._fresh_cold_maintenance())

    async def test_union_cte_avoids_full_scan_and_matches_legacy_or_results(self):
        # Verify EXPLAIN QUERY PLAN uses execution_origin_idx + UNION instead of scanning model_calls with correlated EXISTS.
        matched_cte = _legacy_matched_calls_cte("?")
        sql = (
            f"EXPLAIN QUERY PLAN WITH {matched_cte} "
            "SELECT c.call_id FROM matched m "
            "JOIN model_calls c ON c.call_id = m.call_id "
            "JOIN model_requests r ON r.task_id = c.task_id "
            "JOIN execution_tasks t ON t.task_id = c.task_id"
        )
        plan = [row["detail"] for row in self.app.runtime.read_all(sql, ("acct-1", "conversation.reply", self.role_context.key))]
        joined = "\n".join(plan)
        self.assertIn("UNION", joined)
        self.assertTrue(
            "idx_execution_tasks_origin_kind_id" in joined or "execution_origin_idx" in joined,
            f"expected execution_tasks origin index in query plan, got:\n{joined}",
        )
        self.assertNotIn("CORRELATED SCALAR SUBQUERY", joined)

    async def test_synthetic_large_dataset_speedup_benchmark_and_parity(self):
        # Build a realistic 14-day synthetic dataset with 256-hash CacheSignature blobs (~45 KB/row).
        now = (self.app.now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        self.clock.set(now)
        days = 14
        turns_per_day = 18  # 252 historical turns (~11 MB JSON) + 3 today turns
        seq = 0
        for day_offset in range(days, 0, -1):
            day_base = now - timedelta(days=day_offset)
            for idx in range(turns_per_day):
                seq += 1
                dt = day_base.replace(hour=9, minute=0) + timedelta(minutes=idx * 10)
                obs = _make_observation(
                    f"bench-hist-{day_offset}-{idx}",
                    dt,
                    provider="bench-provider-a" if seq % 5 else "bench-provider-b",
                    input_tokens=30000 + seq * 50,
                    cached_tokens=25000 if seq % 4 else 10000,
                    output_tokens=250,
                    sig_seed=seq // 3,
                    context_count=256,
                    tool_count=64,
                )
                record_observation(self.app.runtime, self.role_context, obs)
        for idx in range(3):
            dt = now - timedelta(minutes=30 - idx * 5)
            record_observation(
                self.app.runtime,
                self.role_context,
                _make_observation(
                    f"bench-today-{idx}",
                    dt,
                    provider="bench-provider-a",
                    input_tokens=35000,
                    cached_tokens=28000,
                    output_tokens=300,
                    sig_seed=900 + idx,
                    context_count=256,
                    tool_count=64,
                ),
            )

        clear_maintenance_cache(self.app.runtime)
        t0 = time.perf_counter()
        cold = self.manager.maintenance(self.core).unwrap()
        cold_ms = (time.perf_counter() - t0) * 1000

        # Warm unchanged poll.
        self.advance_seconds(45)
        t0 = time.perf_counter()
        warm = self.manager.maintenance(self.core).unwrap()
        warm_ms = (time.perf_counter() - t0) * 1000

        # Incremental new turn today.
        self.advance_seconds(45)
        record_observation(
            self.app.runtime,
            self.role_context,
            _make_observation(
                "bench-today-new",
                self.app.now() - timedelta(seconds=5),
                provider="bench-provider-a",
                input_tokens=36000,
                cached_tokens=29000,
                output_tokens=310,
                sig_seed=999,
                context_count=256,
                tool_count=64,
            ),
        )
        t0 = time.perf_counter()
        incremental = self.manager.maintenance(self.core).unwrap()
        incremental_ms = (time.perf_counter() - t0) * 1000

        stats = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats["snapshot_hits"], 1)
        self.assertEqual(stats["day_cache_hits"], 1)
        self.assertEqual(stats["legacy_cache_hits"], 1)
        self.assertEqual(_strip_timestamps(warm), _strip_timestamps(cold))
        self.assertEqual(incremental, self._fresh_cold_maintenance())
        # Warm snapshot hit must be dramatically faster than cold full JSON parse.
        self.assertLess(warm_ms, cold_ms / 5, f"cold={cold_ms:.2f}ms warm={warm_ms:.2f}ms")
        # Incremental today turn must avoid parsing the 14 historical days and be clearly faster than cold.
        self.assertLess(incremental_ms, cold_ms / 2, f"cold={cold_ms:.2f}ms inc={incremental_ms:.2f}ms")

    async def test_unrelated_write_during_cold_build_caches_and_promotes_o1_hit(self):
        self._seed_multi_day_observations(days=3, per_day=2, today_turns=1)
        clear_maintenance_cache(self.app.runtime)

        # Simulate periodic scheduler heartbeat writing to an unrelated table MID-BUILD!
        from companion_v2.model_gateway import maintenance_cache as mc_mod
        orig_fold = mc_mod._fold_core_observations
        injected = {"done": False}

        def write_heartbeat():
            with self.app.runtime.transaction() as writer:
                writer.execute(
                    "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("unrelated.concurrent.heartbeat", '{"tick":1}'),
                )

        def fold_with_concurrent_heartbeat(*args, **kwargs):
            if not injected["done"]:
                injected["done"] = True
                with ThreadPoolExecutor(max_workers=1) as worker:
                    worker.submit(write_heartbeat).result(timeout=15)
            return orig_fold(*args, **kwargs)

        with patch.object(mc_mod, "_fold_core_observations", side_effect=fold_with_concurrent_heartbeat):
            cold = self.manager.maintenance(self.core).unwrap()

        stats_after_cold = maintenance_cache_stats(self.app.runtime)
        self.assertEqual(stats_after_cold["snapshot_entries"], 1)
        self.assertEqual(stats_after_cold["day_entries"], 1)
        self.assertEqual(stats_after_cold["legacy_entries"], 1)

        # Because promote_snapshot_token verified rich_watermark right after read_snapshot closed,
        # the VERY NEXT warm call must hit the O(1) fast path without reading legacy watermark!
        with patch(
            "companion_v2.model_gateway.maintenance_cache.read_legacy_watermark",
            side_effect=AssertionError("promoted warm call must hit O(1) fast path"),
        ):
            warm = self.manager.maintenance(self.core).unwrap()
        self.assertEqual(maintenance_cache_stats(self.app.runtime)["snapshot_hits"], 1)
        self.assertEqual(_strip_timestamps(warm), _strip_timestamps(cold))

        # Negative case: if a REAL maintenance table changes mid-build on another thread,
        # the next call MUST NOT serve stale data and must reflect the new observation!
        clear_maintenance_cache(self.app.runtime)
        injected_real = {"done": False}
        new_turn_dt = self.app.now() - timedelta(seconds=5)

        def fold_with_concurrent_observation(*args, **kwargs):
            if not injected_real["done"]:
                injected_real["done"] = True
                writer_err = []

                def _write_on_other_thread():
                    try:
                        record_observation(
                            self.app.runtime,
                            self.role_context,
                            _make_observation(
                                "mid-build-real-turn",
                                new_turn_dt,
                                provider="core-provider-b",
                                input_tokens=20000,
                                cached_tokens=5000,
                                output_tokens=400,
                                sig_seed=888,
                            ),
                        )
                    except BaseException as exc:  # pragma: no cover
                        writer_err.append(exc)

                t = threading.Thread(target=_write_on_other_thread)
                t.start()
                t.join(timeout=10)
                self.assertFalse(t.is_alive())
                self.assertEqual(writer_err, [])
            return orig_fold(*args, **kwargs)

        with patch.object(mc_mod, "_fold_core_observations", side_effect=fold_with_concurrent_observation):
            during_write = self.manager.maintenance(self.core).unwrap()
        after_write = self.manager.maintenance(self.core).unwrap()
        self.assertGreater(after_write["totals"]["tokens"], during_write["totals"]["tokens"])
        self.assertEqual(after_write, self._fresh_cold_maintenance())


class ModelAPIResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="companion-maint-api-resilience-")
        self.addCleanup(temporary.cleanup)
        self.native = support.WebContext()
        types = support.NativeAdapterTypes("4.27.2", support.FakeEvent, support.FakeProvider, NS, NS, support.FakeReverseApi)
        self.controller = support.AstrBotStagingController(
            self.native, support.host_config(), Path(temporary.name), native_types=types, now=lambda: support.NOW
        )
        self.addAsyncCleanup(self.controller.terminate)
        self.controller.register_routes()
        self.routes = {path: handler for path, handler, _, _ in self.native.registered_web_apis}

    async def _call(self, route: str, *, payload: bytes | None = None, username: str = "admin"):
        raw_req = Request({
            "type": "http",
            "method": "POST" if payload is not None else "GET",
            "path": route,
            "headers": [],
            "query_string": b"",
        })

        async def _body():
            return payload

        fake_request = NS(
            username=username,
            path=route,
            query=PluginMultiDict([]),
            body=_body if payload is not None else None,
        )
        with patch.object(model_api, "request", fake_request):
            with bind_request_context(PluginRequest(raw_req, username=username)):
                return await self.routes[route]()

    async def test_read_token_sources_is_offloaded_to_worker_thread(self):
        (await self.controller.initialize_profile()).unwrap()
        loop_thread_id = threading.get_ident()
        observed_threads = []

        def fake_read_token_sources(controller):
            observed_threads.append(threading.get_ident())
            return {"together": {"available": True, "totals": {"tokens": 12}}}

        main = {"available": True, "totals": {"tokens": 42}, "counted_in_private_companion_budget": False}
        with patch("astrbot_host.model_api.read_token_sources", side_effect=fake_read_token_sources), \
                patch("astrbot_host.model_api.read_astrbot_token_usage", new=AsyncMock(return_value=main)):
            response = await self._call(PREFIX + "/maintenance")

        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(len(observed_threads), 1)
        self.assertNotEqual(observed_threads[0], loop_thread_id)

    async def test_storage_open_error_and_sqlite_error_return_structured_unavailable(self):
        (await self.controller.initialize_profile()).unwrap()

        for exc in (
            StorageOpenError("V2 storage runtime is closed /secret/path.sqlite3"),
            sqlite3.OperationalError("database is locked"),
        ):
            with self.subTest(exc_type=type(exc).__name__):
                with patch.object(ModelManagement, "maintenance", side_effect=exc):
                    response = await self._call(PREFIX + "/maintenance")
                self.assertEqual(response["status"], "error")
                self.assertEqual(response["message"], "model_storage_unavailable")
                self.assertEqual(response["data"]["code"], "model_storage_unavailable")
                self.assertNotIn("/secret/path", json.dumps(response))
                self.assertNotEqual(response["message"], "plugin_web_internal_error")

        # Also verify POST /model/test/storage catches StorageOpenError / sqlite3.Error cleanly.
        with patch.object(ModelManagement, "check_storage", side_effect=sqlite3.OperationalError("database is busy")):
            storage_resp = await self._call(PREFIX + "/test/storage", payload=b'{"check":"sqlite"}')
        self.assertEqual(storage_resp["status"], "error")
        self.assertEqual(storage_resp["message"], "model_storage_unavailable")
        self.assertEqual(storage_resp["data"]["code"], "model_storage_unavailable")
