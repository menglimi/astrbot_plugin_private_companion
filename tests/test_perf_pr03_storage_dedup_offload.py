"""Regression and benchmark tests for storage deduplication, cold archiving, and media offload:

1. Content-addressed deduplication (``role_instruction_blobs`` & ``conversation_contexts``)
2. Cold telemetry archiving (``telemetry-archive.sqlite3`` & ``core_reply_daily_rollups``)
3. Media filesystem offload, ``auto_vacuum = INCREMENTAL``, write-behind micro-batching,
   and one-click database health/compaction APIs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import struct
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zlib

from astrbot_host.native_extensions.image_native.store import (
    NATIVE_IMAGE_FILE_MARKER,
    ImageStore,
    migrate_native_image_store_at,
)
from astrbot_host.pricing_inventory import collect_usage_consumption
from astrbot_host.storage_api import StorageWebAPI
from companion_v2.conversation import native_history
from companion_v2.conversation.messages import ConversationMessages
from companion_v2.conversation.native_observations import (
    NativeConversationObservations,
    input_rows_for_context,
    rows_for_context,
)
from companion_v2.conversation.native_turns import (
    NativeConversationTurns,
    context_rows,
    latest,
    latest_input,
    latest_role_contexts,
    latest_scope,
)
from companion_v2.conversation.scope_family import group_scope_keys, private_scope_keys
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, stamp
from companion_v2.expression.private_scope import private_people
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AudienceRef,
    ScopeKind,
    ScopeRef,
    SubjectKind,
    SubjectRef,
)
from companion_v2.identity_policy.models import (
    AuthenticatedHostPrincipal,
    ResolvedIdentity,
    RoleBinding,
)
from companion_v2.model_gateway.core_observations import (
    observations_for_role,
    record_observation,
    role_key as core_role_key,
)
from companion_v2.model_gateway.maintenance_cache import runtime_maintenance_cache
from companion_v2.model_gateway.usage import CORE_CHAIN_KIND
from companion_v2.storage.compaction import compact_and_slim_databases, database_health_report
from companion_v2.storage.native_dedup import (
    CONTEXT_REF_PREFIX,
    ROLE_BLOB_PREFIX,
    clear_dedup_caches,
    dedup_native_storage,
    is_context_ref,
    is_role_blob_ref,
    resolve_context_json,
)
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.storage.semantic import REACTION_FILE_MARKER, SemanticStore
from companion_v2.storage.telemetry_archive import (
    TelemetryArchiveStore,
    archive_cold_telemetry,
)


INSTALLATION_ID = "pr03-storage-inst"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def _sample_png(width: int = 2, height: int = 2, rgb: tuple[int, int, int] = (20, 140, 220)) -> bytes:
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _make_context(index: int, kind: ScopeKind = ScopeKind.PRIVATE) -> TaskContext:
    scope = ScopeRef(
        INSTALLATION_ID,
        "onebot",
        kind,
        f"native-{index}",
        "bot-a",
        "login-a",
        f"conv-{index}",
        f"sess-{index}",
    )
    subject = SubjectRef(INSTALLATION_ID, SubjectKind.HUMAN, f"user-{index}")
    return TaskContext(
        ResolvedIdentity(
            AuthenticatedHostPrincipal(
                INSTALLATION_ID,
                "host-a",
                "onebot",
                f"acct-{index}",
                "bot-a",
                "login-a",
            ),
            subject,
            f"binding-{index}",
            1,
            1,
        ),
        RoleBinding(scope, SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a"), 1),
        AudienceRef(scope, (subject,)),
    )


def _make_core_record(turn_id: str, received_at: datetime, *, provider: str = "prov-a",
                      input_tokens: int = 1000, cached_tokens: int = 400, output_tokens: int = 200) -> dict:
    ts = stamp(received_at)
    return {
        "version": 1,
        "revision": 2,
        "turn_id": turn_id,
        "event_id": f"ev-{turn_id}",
        "execution_path": "astrbot_core",
        "received_at": ts,
        "waiting_llm_at": ts,
        "request_hook_at": ts,
        "observed_at": ts,
        "generation_state": "completed",
        "generation_finished_at": ts,
        "request_observation_available": True,
        "request_observation_complete": True,
        "tool_requery_observed": False,
        "requests": [
            {
                "position": 1,
                "state": "completed",
                "started_at": ts,
                "finished_at": ts,
                "latency_ms": 320,
                "ttft_ms": 110,
                "provider": provider,
                "measurement_basis": "provider_response",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tokens": input_tokens + output_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_creation_tokens": 0,
                    "availability": "reported",
                    "cache_availability": "reported",
                    "cost_availability": "unavailable",
                },
            }
        ],
        "tools": [],
        "ttft_ms": 110,
        "runner_duration_ms": 350,
        "usage_scope": "provider_response",
        "delivery": {"state": "returned", "attempts": 1, "returned": 1},
    }


class _DummyRequest:
    username = "dashboard-admin"
    query: dict = {}
    payload = b""

    async def body(self):
        return self.payload


class NativeDedupTests(unittest.TestCase):
    def test_native_turns_and_observations_deduplicate_losslessly_and_migrate_legacy_rows(self):
        with TemporaryDirectory(prefix="pr03-dedup-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id=INSTALLATION_ID)
            try:
                turns = NativeConversationTurns(runtime)
                priv = _make_context(1, ScopeKind.PRIVATE)
                grp = _make_context(2, ScopeKind.GROUP)
                big_prompt = ("你是一个温柔且细心的陪伴者，牢记每一次约定的细节。" * 240).strip()
                self.assertGreater(len(big_prompt.encode("utf-8")), 6000)

                for i in range(25):
                    ctx = priv if i % 2 == 0 else grp
                    at = NOW + timedelta(seconds=i * 10)
                    native_input = NS(at=at, instruction=big_prompt)
                    ref = NS(source_id=f"src-{i}")
                    turn_id = f"turn-{i}"
                    turns.admit(ctx, native_input, turn_id, ref)
                    turns.mark(
                        {"context": ctx, "turn_id": turn_id, "native_input": native_input, "instruction": big_prompt},
                        "completed" if i < 23 else "running",
                        at=at + timedelta(seconds=1),
                    )

                # Verify conflict check still rejects modified prompt on the same turn_id.
                with self.assertRaises(TaskError) as conflict_err:
                    turns.admit(priv, NS(at=NOW, instruction=big_prompt + " tampered"), "turn-0", NS(source_id="src-0"))
                self.assertEqual(conflict_err.exception.code, "native_turn_conflict")

                # Verify raw DB stores compact pointers (69B blob ref & 79B ctx ref) and only 1 role blob.
                raw_turn = runtime.read_one(
                    "SELECT role_instruction, context_json, context_hash, scope_kind, role_binding_key "
                    "FROM conversation_native_turns WHERE turn_id = 'turn-0'",
                    (),
                )
                self.assertTrue(is_role_blob_ref(raw_turn["role_instruction"]))
                self.assertTrue(is_context_ref(raw_turn["context_json"]))
                self.assertEqual(len(raw_turn["context_hash"]), 64)
                self.assertEqual(raw_turn["scope_kind"], "private")
                self.assertEqual(raw_turn["role_binding_key"], encode(priv.role_binding))
                self.assertEqual(runtime.read_one("SELECT COUNT(*) FROM role_instruction_blobs", ())[0], 1)
                self.assertEqual(runtime.read_one("SELECT COUNT(*) FROM conversation_contexts", ())[0], 2)

                # Clear process caches to prove cold DB resolution works across all read paths.
                clear_dedup_caches()

                got_latest = latest(runtime, priv.key)
                self.assertEqual(got_latest["role_instruction"], big_prompt)
                self.assertEqual(TaskContext.restore(json.loads(got_latest["context_json"])), priv)

                got_scope = latest_scope(runtime, priv.scope)
                self.assertEqual(got_scope["turn_id"], "turn-24")
                self.assertEqual(got_scope["role_instruction"], big_prompt)

                got_input = latest_input(runtime, priv.key)
                self.assertEqual(got_input["turn_id"], "turn-22")
                self.assertEqual(got_input["role_instruction"], big_prompt)

                open_priv = context_rows(runtime, kind="private", open_only=True)
                self.assertEqual([r["turn_id"] for r in open_priv], ["turn-24"])
                self.assertEqual(open_priv[0]["role_instruction"], big_prompt)

                all_rows = context_rows(runtime)
                self.assertEqual(len(all_rows), 2)
                self.assertTrue(all(r["role_instruction"] == big_prompt for r in all_rows))

                role_ctxs = latest_role_contexts(runtime, kind="group", limit=4)
                self.assertEqual(len(role_ctxs), 1)
                self.assertEqual(TaskContext.restore(json.loads(role_ctxs[0]["context_json"])), grp)

                self.assertIn(digest(grp.scope), group_scope_keys(runtime, grp.scope, grp.role_binding.role))
                self.assertIn(digest(priv.scope), private_scope_keys(runtime, priv.scope, priv.role_binding.role))

                # Legacy inline row migration: raw-INSERT un-deduped turn & observation, then run dedup_native_storage.
                legacy_ctx = _make_context(99, ScopeKind.PRIVATE)
                legacy_prompt = ("历史遗留的长人格提示词内容" * 80).strip()
                with runtime.transaction() as conn:
                    conn.execute(
                        """INSERT INTO conversation_native_turns
                           (turn_id, context_key, context_json, role_instruction, input_observation_id,
                            observed_at, generation_state, phase_at, last_sent_at)
                           VALUES ('legacy-turn-1', ?, ?, ?, 'in-legacy', ?, 'completed', ?, NULL)""",
                        (legacy_ctx.key, encode(legacy_ctx), legacy_prompt, stamp(NOW), stamp(NOW)),
                    )
                    conn.execute(
                        """INSERT INTO conversation_native_observations
                           (observation_id, context_key, memory_owner, conversation_owner, turn_id, event_id,
                            kind, delivery_state, context_json, source_json, content_hash, observed_at)
                           VALUES ('legacy-obs-1', ?, 'mo', 'co', 'legacy-turn-1', 'ev-legacy',
                                   'input', 'input_observed', ?, '{}', 'ch', ?)""",
                        (legacy_ctx.key, encode(legacy_ctx), stamp(NOW)),
                    )

                stats = dedup_native_storage(runtime)
                self.assertEqual(stats["role_blobs_migrated"], 1)
                self.assertEqual(stats["turn_contexts_migrated"], 1)
                self.assertEqual(stats["obs_contexts_migrated"], 1)
                self.assertGreater(stats["bytes_saved_estimate"], 2500)

                # Triggers MUST preserve scope_kind & role_binding_key after dedup UPDATE!
                migrated_turn = runtime.read_one(
                    "SELECT scope_kind, role_binding_key, context_json, role_instruction "
                    "FROM conversation_native_turns WHERE turn_id = 'legacy-turn-1'",
                    (),
                )
                self.assertEqual(migrated_turn["scope_kind"], "private")
                self.assertEqual(migrated_turn["role_binding_key"], encode(legacy_ctx.role_binding))
                self.assertTrue(is_context_ref(migrated_turn["context_json"]))
                self.assertTrue(is_role_blob_ref(migrated_turn["role_instruction"]))

                migrated_obs = runtime.read_one(
                    "SELECT scope_kind, role_binding_key, context_json "
                    "FROM conversation_native_observations WHERE observation_id = 'legacy-obs-1'",
                    (),
                )
                self.assertEqual(migrated_obs["scope_kind"], "private")
                self.assertEqual(migrated_obs["role_binding_key"], encode(legacy_ctx.role_binding))
                self.assertTrue(is_context_ref(migrated_obs["context_json"]))

                clear_dedup_caches()
                self.assertEqual(latest(runtime, legacy_ctx.key)["role_instruction"], legacy_prompt)

                # private_people still discovers the deduped observation cleanly.
                identity = NS(
                    resolve_authenticated=lambda principal, observed_at: Outcome.success(legacy_ctx.actor),
                    resolve_role=lambda scope, observed_at: Outcome.success(legacy_ctx.role_binding),
                )
                people = private_people(runtime, identity, legacy_ctx, NOW)
                self.assertEqual(len(people), 1)
                self.assertEqual(people[0]["subject_id"], legacy_ctx.actor.subject.subject_id)

                # Second dedup pass is a clean idempotent no-op.
                second = dedup_native_storage(runtime)
                self.assertEqual(second["role_blobs_migrated"], 0)
                self.assertEqual(second["turn_contexts_migrated"], 0)
                self.assertEqual(second["obs_contexts_migrated"], 0)
            finally:
                runtime.close()

    def test_native_reply_diagnostics_uses_covering_indexes_across_mixed_legacy_and_deduped_rows(self):
        from companion_v2.conversation.native_reply_diagnostics import recent as native_recent

        with TemporaryDirectory(prefix="pr03-diag-idx-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id=INSTALLATION_ID)
            try:
                ctx = _make_context(1, ScopeKind.PRIVATE)
                other_ctx = _make_context(2, ScopeKind.PRIVATE)
                rk = core_role_key(ctx)

                # Seed 3 deduped observations (stored via record_observation -> context_hash populated).
                for idx, delta_min in ((1, 10), (3, 30), (5, 50)):
                    record_observation(
                        runtime,
                        ctx,
                        _make_core_record(f"turn-{idx}", NOW + timedelta(minutes=delta_min)),
                    )
                # Also seed another scope so filtering is proven.
                record_observation(
                    runtime,
                    other_ctx,
                    _make_core_record("other-turn-99", NOW + timedelta(minutes=99)),
                )
                # Raw-INSERT 2 legacy un-deduped observations (inline context_json, empty context_hash)
                # interleaved in timestamp order with the deduped rows.
                with runtime.transaction() as conn:
                    for idx, delta_min in ((2, 20), (4, 40)):
                        ts = stamp(NOW + timedelta(minutes=delta_min))
                        rec = _make_core_record(f"turn-{idx}", NOW + timedelta(minutes=delta_min))
                        conn.execute(
                            """INSERT INTO astrbot_core_reply_observations
                               (turn_id, revision, role_key, context_key, scope_kind,
                                context_json, received_at, observed_at, record_json, context_hash)
                               VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, '')""",
                            (
                                f"turn-{idx}",
                                rk,
                                ctx.key,
                                ctx.scope.kind.value,
                                encode(ctx),
                                ts,
                                ts,
                                json.dumps(rec),
                            ),
                        )

                # Verify both split SQL branches hit their covering indexes without scanning the table.
                eqp_dedup = [
                    row["detail"]
                    for row in runtime.read_all(
                        """EXPLAIN QUERY PLAN
                           SELECT turn_id, received_at FROM astrbot_core_reply_observations
                           WHERE role_key = ? AND scope_kind = ? AND context_hash IN (?)
                           ORDER BY received_at DESC, turn_id DESC LIMIT 5""",
                        (rk, ctx.scope.kind.value, "h1"),
                    )
                ]
                eqp_legacy = [
                    row["detail"]
                    for row in runtime.read_all(
                        """EXPLAIN QUERY PLAN
                           SELECT turn_id, received_at FROM astrbot_core_reply_observations
                           WHERE role_key = ? AND scope_kind = ?
                             AND json_extract(context_json, '$.role_binding.scope.native_scope_id') = ?
                             AND json_extract(context_json, '$.role_binding.scope.session_id') = ?
                             AND json_extract(context_json, '$.role_binding.scope.conversation_id') = ?
                           ORDER BY received_at DESC, turn_id DESC LIMIT 5""",
                        (
                            rk,
                            ctx.scope.kind.value,
                            ctx.scope.native_scope_id,
                            ctx.scope.session_id,
                            ctx.scope.conversation_id,
                        ),
                    )
                ]
                self.assertTrue(
                    any("COVERING INDEX idx_core_reply_role_scope_ctxhash" in line for line in eqp_dedup),
                    eqp_dedup,
                )
                self.assertTrue(
                    any("COVERING INDEX idx_core_reply_scope_lookup" in line for line in eqp_legacy),
                    eqp_legacy,
                )
                self.assertFalse(any("SCAN astrbot_core_reply_observations" in line for line in eqp_dedup + eqp_legacy))

                # Verify exact merged DESC ordering and limit enforcement across mixed rows.
                top3 = native_recent(runtime, ctx.scope, ctx.role_binding.role, limit=3)
                self.assertEqual([item["batch_id"] for item in top3], ["turn-5", "turn-4", "turn-3"])
                all_five = native_recent(runtime, ctx.scope, ctx.role_binding.role, limit=10)
                self.assertEqual(
                    [item["batch_id"] for item in all_five],
                    ["turn-5", "turn-4", "turn-3", "turn-2", "turn-1"],
                )
                self.assertEqual(native_recent(runtime, ctx.scope, ctx.role_binding.role, limit=0), [])
            finally:
                runtime.close()


class ColdTelemetryArchiveTests(unittest.TestCase):
    def test_archive_cold_telemetry_preserves_usage_fx_recompute_and_pricing_inventory(self):
        with TemporaryDirectory(prefix="pr03-archive-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id=INSTALLATION_ID)
            try:
                ctx = _make_context(1, ScopeKind.PRIVATE)
                rk = core_role_key(ctx)

                # Insert 4 cold core observations (20 and 19 days old) + 2 recent observations (2 days old).
                cold_day1 = NOW - timedelta(days=20)
                cold_day2 = NOW - timedelta(days=19)
                recent_day = NOW - timedelta(days=2)
                for idx, (at, prov, inp, cached, out) in enumerate((
                    (cold_day1, "prov-a", 1000, 400, 200),
                    (cold_day1 + timedelta(minutes=5), "prov-a", 2000, 1000, 300),
                    (cold_day2, "prov-b", 1500, 500, 250),
                    (cold_day2 + timedelta(hours=1), "prov-a", 500, 0, 100),
                    (recent_day, "prov-a", 800, 200, 150),
                    (recent_day + timedelta(minutes=10), "prov-b", 600, 100, 100),
                )):
                    record_observation(
                        runtime,
                        ctx,
                        _make_core_record(
                            f"core-turn-{idx}",
                            at,
                            provider=prov,
                            input_tokens=inp,
                            cached_tokens=cached,
                            output_tokens=out,
                        ),
                    )

                # Insert 1 cold and 1 fresh laya_decision_events row with large JSON payloads.
                big_req = json.dumps({"prompt": "X" * 4096})
                big_resp = json.dumps({"decision": "reply", "notes": "Y" * 2048})
                cold_eid = "a" * 32
                fresh_eid = "b" * 32
                with runtime.transaction() as conn:
                    conn.execute(
                        """INSERT INTO laya_decision_events
                           (event_id, origin_key, scope_key, source, status, mode,
                            enabled_fields_json, observed_at, request_json, response_json)
                           VALUES (?, 'origin-1', 'scope-1', 'conversation', 'ok', 'local', '{}', ?, ?, ?),
                                  (?, 'origin-1', 'scope-1', 'conversation', 'ok', 'local', '{}', ?, ?, ?)""",
                        (
                            cold_eid, stamp(NOW - timedelta(days=21)), big_req, big_resp,
                            fresh_eid, stamp(NOW - timedelta(days=1)), big_req, big_resp,
                        ),
                    )

                def _make_enricher(rate: float):
                    # Synthetic linear pricing: $1 per 1000 tokens * rate (in micro-USD).
                    def enrich(rows):
                        for row in rows:
                            tok = int(row.get("actual_tokens") or 0)
                            calls = max(1, int(row.get("_rollup_calls") or 1))
                            est = int(round(tok * rate))
                            yield (
                                row,
                                {
                                    "cost_microusd": est,
                                    "recorded_cost_calls": 0,
                                    "recorded_cost_microusd": 0,
                                    "estimated_cost_calls": calls,
                                    "estimated_cost_microusd": est,
                                    "unpriced_token_calls": 0,
                                },
                                {**row, "actual_cost": est, "estimated_cost_microusd": est},
                            )
                    return enrich

                cache = runtime_maintenance_cache(runtime)
                history_cutoff = NOW.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                before_state = cache.load_partitioned_core(
                    runtime,
                    ctx,
                    role_key_value=rk,
                    history_cutoff=history_cutoff,
                    tz_offset_seconds=0,
                    cache_version=("v1", 7.0),
                    raw_cache_version="raw-v1",
                    enrich_core_rows=_make_enricher(7.0),
                    eff_start=NOW - timedelta(days=7),
                    eff_now=NOW,
                    core_available=True,
                )
                before_inv, _ = collect_usage_consumption(runtime, target_context=ctx)

                # Archive rows older than 14 days.
                arch_stats = archive_cold_telemetry(runtime, ttl_days=14, now=NOW)
                self.assertEqual(arch_stats["core_rows_archived"], 4)
                self.assertEqual(arch_stats["core_rollup_days_updated"], 2)
                self.assertEqual(arch_stats["laya_payloads_archived"], 1)
                self.assertGreater(arch_stats["core_bytes_reclaimed_estimate"], 0)
                self.assertGreater(arch_stats["laya_bytes_reclaimed_estimate"], 6000)

                # Only 2 recent core rows remain live; archive DB has all 4 cold rows with full context_json.
                self.assertEqual(
                    runtime.read_one("SELECT COUNT(*) FROM astrbot_core_reply_observations", ())[0], 2
                )
                archive = TelemetryArchiveStore(runtime.data_root)
                archived_turn0 = archive.core_observation("core-turn-0")
                self.assertIsNotNone(archived_turn0)
                self.assertEqual(TaskContext.restore(json.loads(archived_turn0["context_json"])), ctx)

                # Cold Laya payload nulled in live DB, preserved in archive; fresh Laya payload untouched.
                live_cold_laya = runtime.read_one(
                    "SELECT status, request_json, response_json FROM laya_decision_events WHERE event_id = ?",
                    (cold_eid,),
                )
                self.assertEqual(live_cold_laya["status"], "ok")
                self.assertIsNone(live_cold_laya["request_json"])
                self.assertIsNone(live_cold_laya["response_json"])
                archived_laya = archive.laya_decision_payload(cold_eid)
                self.assertEqual(archived_laya["request_json"], big_req)
                self.assertEqual(archived_laya["response_json"], big_resp)
                live_fresh_laya = runtime.read_one(
                    "SELECT request_json FROM laya_decision_events WHERE event_id = ?", (fresh_eid,)
                )
                self.assertEqual(live_fresh_laya["request_json"], big_req)

                # Maintenance report AFTER archiving matches BEFORE archiving identically (same FX rate 7.0).
                after_state = cache.load_partitioned_core(
                    runtime,
                    ctx,
                    role_key_value=rk,
                    history_cutoff=history_cutoff,
                    tz_offset_seconds=0,
                    cache_version=("v1", 7.0),
                    raw_cache_version="raw-v1",
                    enrich_core_rows=_make_enricher(7.0),
                    eff_start=NOW - timedelta(days=7),
                    eff_now=NOW,
                    core_available=True,
                )
                self.assertEqual(after_state.core_days, before_state.core_days)
                self.assertEqual(after_state.availability, before_state.availability)
                self.assertEqual(after_state.usage_report.by_day, before_state.usage_report.by_day)
                self.assertEqual(after_state.usage_report.by_provider, before_state.usage_report.by_provider)
                self.assertEqual(after_state.usage_report.hourly, before_state.usage_report.hourly)

                # Changing FX rate (7.0 -> 10.0) recomputes archived historical costs accurately from rollups!
                fx_state = cache.load_partitioned_core(
                    runtime,
                    ctx,
                    role_key_value=rk,
                    history_cutoff=history_cutoff,
                    tz_offset_seconds=0,
                    cache_version=("v1", 10.0),
                    raw_cache_version="raw-v1",
                    enrich_core_rows=_make_enricher(10.0),
                    eff_start=NOW - timedelta(days=7),
                    eff_now=NOW,
                    core_available=True,
                )
                total_tokens = sum(v["tokens"] for v in fx_state.usage_report.by_day.values())
                total_cost = sum(v["cost_microusd"] for v in fx_state.usage_report.by_day.values())
                self.assertEqual(total_cost, total_tokens * 10)

                # pricing_inventory lifetime aggregation matches pre-archive totals 100%!
                after_inv, _ = collect_usage_consumption(runtime, target_context=ctx)
                self.assertEqual(after_inv, before_inv)

                # Second archive pass is idempotent (0 rows archived, no double-counting).
                second_stats = archive_cold_telemetry(runtime, ttl_days=14, now=NOW)
                self.assertEqual(second_stats["core_rows_archived"], 0)
                self.assertEqual(second_stats["laya_payloads_archived"], 0)
            finally:
                runtime.close()

    def test_archive_retry_after_sidecar_commit_still_populates_rollups_and_stays_idempotent(self):
        with TemporaryDirectory(prefix="pr03-archive-retry-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id=INSTALLATION_ID)
            try:
                ctx = _make_context(99)
                rk = core_role_key(ctx)
                day_at = NOW - timedelta(days=20)
                for idx, (inp, out) in enumerate(((400, 120), (600, 180))):
                    record_observation(
                        runtime,
                        ctx,
                        _make_core_record(
                            f"retry-turn-{idx}",
                            day_at + timedelta(minutes=idx * 5),
                            provider="prov-retry",
                            input_tokens=inp,
                            cached_tokens=50,
                            output_tokens=out,
                        ),
                    )

                # Simulate crash after sidecar commit and before primary transaction commit:
                # sidecar already holds both rows while primary table still has them and no rollups exist.
                archive = TelemetryArchiveStore(runtime.data_root)
                live_rows = runtime.read_all(
                    "SELECT rowid, turn_id, revision, role_key, context_key, scope_kind, "
                    "context_json, received_at, observed_at, record_json "
                    "FROM astrbot_core_reply_observations ORDER BY rowid ASC",
                    (),
                )
                pre_archived = [
                    {
                        "turn_id": row["turn_id"],
                        "revision": row["revision"],
                        "role_key": row["role_key"],
                        "context_key": row["context_key"],
                        "scope_kind": row["scope_kind"],
                        "context_json": resolve_context_json(runtime, row["context_json"]),
                        "received_at": row["received_at"],
                        "observed_at": row["observed_at"],
                        "record_json": row["record_json"],
                    }
                    for row in live_rows
                ]
                newly = archive.archive_core_rows(pre_archived, archived_at=stamp(NOW))
                self.assertEqual(len(newly), 2)
                self.assertEqual(
                    runtime.read_one("SELECT COUNT(*) FROM core_reply_daily_rollups", ())[0], 0
                )

                # Retry archive: primary rows must still be folded into core_reply_daily_rollups and deleted.
                retry_stats = archive_cold_telemetry(runtime, ttl_days=14, now=NOW)
                self.assertEqual(retry_stats["core_rows_archived"], 2)
                self.assertEqual(retry_stats["core_rollup_days_updated"], 1)
                self.assertEqual(
                    runtime.read_one("SELECT COUNT(*) FROM astrbot_core_reply_observations", ())[0], 0
                )
                rollup_row = runtime.read_one(
                    "SELECT source_row_count, archived_row_count, rollup_json "
                    "FROM core_reply_daily_rollups WHERE role_key = ?",
                    (rk,),
                )
                self.assertIsNotNone(rollup_row)
                self.assertEqual(rollup_row["source_row_count"], 2)
                self.assertEqual(rollup_row["archived_row_count"], 2)
                payload = json.loads(rollup_row["rollup_json"])
                self.assertEqual(payload["provider_totals"]["prov-retry"]["calls"], 2)
                self.assertEqual(payload["provider_totals"]["prov-retry"]["input_tokens"], 1000)
                self.assertEqual(payload["provider_totals"]["prov-retry"]["output_tokens"], 300)

                # Subsequent retry is strictly idempotent.
                again = archive_cold_telemetry(runtime, ttl_days=14, now=NOW)
                self.assertEqual(again["core_rows_archived"], 0)
                self.assertEqual(again["core_rollup_days_updated"], 0)
                rollup_after = runtime.read_one(
                    "SELECT source_row_count, archived_row_count, rollup_json "
                    "FROM core_reply_daily_rollups WHERE role_key = ?",
                    (rk,),
                )
                self.assertEqual(dict(rollup_after), dict(rollup_row))
            finally:
                runtime.close()


class MediaOffloadVacuumBatcherAndApiTests(unittest.IsolatedAsyncioTestCase):
    def test_native_image_store_offloads_to_files_uses_incremental_vacuum_and_migrates_legacy(self):
        with TemporaryDirectory(prefix="pr03-nimg-") as tmp:
            img_dir = Path(tmp) / "image_native"
            store = ImageStore(img_dir, None, identity_key="img-id-1")
            with store.read_db() as db:
                self.assertEqual(db.execute("PRAGMA auto_vacuum").fetchone()[0], 2)

            png1 = _sample_png(4, 4, (10, 20, 30))
            req = {
                "scope_key": "scope-a",
                "request_id": "req-offload-1",
                "origin_key": "origin-a",
                "invocation_id": "inv-1",
                "operation": "generate",
            }
            op, _ = store.begin(req)
            store.state("scope-a", op["id"], "dispatching")
            done = store.complete("scope-a", op["id"], (png1,), {"ok": True})
            art_id = done["receipt"]["artifacts"][0]["artifact_id"]

            # Raw SQLite data column stores compact file marker, NOT the raw PNG bytes.
            with store.read_db() as db:
                raw_col = bytes(db.execute("SELECT data FROM artifacts WHERE id=?", (art_id,)).fetchone()["data"])
            self.assertTrue(raw_col.startswith(NATIVE_IMAGE_FILE_MARKER))
            self.assertLess(len(raw_col), 100)

            meta, loaded = store.artifact("scope-a", art_id)
            self.assertEqual(loaded, png1)
            self.assertEqual(meta["artifact_id"], art_id)

            # Simulate a legacy inline BLOB row and verify both pre- and post-migration reads succeed
            # without rotating runtime generation!
            gen_before = store.runtime_id
            with store.db() as db:
                db.execute("UPDATE artifacts SET data = ? WHERE id = ?", (png1, art_id))
            _, legacy_loaded = store.artifact("scope-a", art_id)
            self.assertEqual(legacy_loaded, png1)

            mig = migrate_native_image_store_at(img_dir)
            self.assertEqual(mig["migrated"], 1)
            self.assertEqual(store.runtime_id, gen_before)
            _, post_mig_loaded = store.artifact("scope-a", art_id)
            self.assertEqual(post_mig_loaded, png1)

            # Media GC protects freshly written unreferenced files under default grace window,
            # while reclaiming aged orphans (or when min_age_seconds=0).
            orphan_dir = store.media_root / "ff"
            orphan_dir.mkdir(parents=True, exist_ok=True)
            orphan_file = orphan_dir / ("f" * 64 + ".png")
            orphan_file.write_bytes(png1)
            gc_guarded = store.gc_unreferenced_media()
            self.assertEqual(gc_guarded["removed_files"], 0)
            self.assertTrue(orphan_file.is_file())
            old_ts = time.time() - 600.0
            os.utime(orphan_file, (old_ts, old_ts))
            gc_aged = store.gc_unreferenced_media()
            self.assertEqual(gc_aged["removed_files"], 1)
            self.assertFalse(orphan_file.exists())
            self.assertEqual(store.artifact("scope-a", art_id)[1], png1)

    def test_semantic_store_offloads_reactions_and_frames_and_protects_shared_files(self):
        with TemporaryDirectory(prefix="pr03-sem-media-") as tmp:
            root = Path(tmp)
            store = SemanticStore(root)
            with store.db(read_only=True) as db:
                self.assertEqual(db.execute("PRAGMA auto_vacuum").fetchone()[0], 2)

            main_png = _sample_png(6, 6, (200, 50, 80))
            frame0 = _sample_png(4, 4, (10, 200, 30))
            frame1 = _sample_png(4, 4, (30, 10, 200))
            payload = {"descriptor": {"mime_type": "image/png"}, "text": "开心"}
            item = store.put(
                "owner-a",
                "reaction",
                "react-1",
                payload,
                content=main_png,
                frames=[(0, frame0), (1, frame1)],
            )
            self.assertEqual(item["content"], main_png)
            self.assertEqual(store.reaction_frames("owner-a", "react-1"), (frame0, frame1))

            # Raw SQLite columns carry compact file markers.
            with store.db(read_only=True) as db:
                raw_item = bytes(db.execute("SELECT content FROM items WHERE owner='owner-a' AND id='react-1'").fetchone()[0])
                raw_frames = [bytes(r[0]) for r in db.execute("SELECT content FROM reaction_frames ORDER BY position")]
            self.assertTrue(raw_item.startswith(REACTION_FILE_MARKER))
            self.assertTrue(all(f.startswith(REACTION_FILE_MARKER) for f in raw_frames))

            # Promote to owner-b, then delete owner-a: shared content-addressed files must stay readable for owner-b!
            self.assertTrue(store.promote_reaction_owner("owner-a", "owner-b"))
            store.delete("owner-a", "reaction", "react-1", expected=item["revision"])
            self.assertEqual(store.get("owner-b", "reaction", "react-1")["content"], main_png)
            self.assertEqual(store.reaction_frames("owner-b", "react-1"), (frame0, frame1))
            listed = store.list("owner-b", "reaction")
            self.assertEqual(listed[0]["content"], main_png)
            paged, _ = store.list_recent_page("owner-b", "reaction")
            self.assertEqual(paged[0]["content"], main_png)

            # Overwrite with legacy inline BLOBs, verify readable, then migrate cleanly.
            with store.db() as db:
                db.execute("UPDATE items SET content = ? WHERE owner='owner-b' AND id='react-1'", (main_png,))
                db.execute("UPDATE reaction_frames SET content = ? WHERE owner='owner-b' AND id='react-1' AND position=0", (frame0,))
            self.assertEqual(store.get("owner-b", "reaction", "react-1")["content"], main_png)
            mig = store.migrate_reaction_blobs_to_files()
            self.assertEqual(mig["items_migrated"], 1)
            self.assertEqual(mig["frames_migrated"], 1)
            self.assertEqual(store.get("owner-b", "reaction", "react-1")["content"], main_png)
            self.assertEqual(store.reaction_frames("owner-b", "react-1"), (frame0, frame1))

            # Deleting the last owner: default GC grace window protects freshly written files,
            # while min_age_seconds=0 reclaims all 3 unreferenced files cleanly.
            rev_b = store.get("owner-b", "reaction", "react-1")["revision"]
            store.delete("owner-b", "reaction", "react-1", expected=rev_b)
            gc_guarded = store.gc_unreferenced_reaction_files()
            self.assertEqual(gc_guarded["removed_files"], 0)
            gc_res = store.gc_unreferenced_reaction_files(min_age_seconds=0)
            self.assertEqual(gc_res["removed_files"], 3)

    def test_runtime_incremental_auto_vacuum_and_write_behind_micro_batching(self):
        with TemporaryDirectory(prefix="pr03-batch-vac-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id=INSTALLATION_ID)
            try:
                self.assertEqual(runtime.read_one("PRAGMA auto_vacuum", ())[0], 2)

                # Create a scratch table, write & delete data to generate freelist pages, then incremental_vacuum.
                with runtime.transaction() as conn:
                    conn.execute("CREATE TABLE wb_scratch (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
                    conn.executemany(
                        "INSERT INTO wb_scratch VALUES (?, ?)",
                        [(f"seed-{i}", "Z" * 1024) for i in range(120)],
                    )
                with runtime.transaction() as conn:
                    conn.execute("DELETE FROM wb_scratch")
                freelist_before = runtime.read_one("PRAGMA freelist_count", ())[0]
                self.assertGreater(freelist_before, 0)
                freed = runtime.incremental_vacuum(pages=256)
                self.assertGreater(freed, 0)

                # Exercise write-behind micro-batching: coalescing by key + SAVEPOINT isolation + flush.
                for i in range(15):
                    runtime.enqueue_batched_write(
                        lambda c, idx=i: c.execute(
                            "INSERT OR REPLACE INTO wb_scratch VALUES (?, ?)", (f"item-{idx}", f"val-{idx}")
                        )
                    )
                # Duplicate key coalesces into latest value.
                runtime.enqueue_batched_write(
                    lambda c: c.execute("INSERT OR REPLACE INTO wb_scratch VALUES ('coalesce', 'old')"),
                    key="k-coalesce",
                )
                runtime.enqueue_batched_write(
                    lambda c: c.execute("INSERT OR REPLACE INTO wb_scratch VALUES ('coalesce', 'latest')"),
                    key="k-coalesce",
                )
                # Failing callback must NOT roll back healthy siblings in the same batch.
                def _bad_cb(conn):
                    raise RuntimeError("isolated callback failure")
                runtime.enqueue_batched_write(_bad_cb)

                runtime.flush_batched_writes(timeout=5.0)
                stats = runtime.batched_write_stats()
                self.assertGreaterEqual(stats["flushed_items"], 16)
                self.assertLess(stats["flushed_batches"], 16)
                self.assertEqual(stats["errors"], 1)
                self.assertEqual(
                    runtime.read_one("SELECT v FROM wb_scratch WHERE k = 'coalesce'", ())[0],
                    "latest",
                )
                self.assertEqual(
                    runtime.read_one("SELECT COUNT(*) FROM wb_scratch", ())[0],
                    16,
                )

                # Verify close() flushes final queued writes before tearing down connections.
                runtime.enqueue_batched_write(
                    lambda c: c.execute("INSERT OR REPLACE INTO wb_scratch VALUES ('on-close', 'saved')")
                )
            finally:
                runtime.close()

            reopened = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id=INSTALLATION_ID)
            try:
                self.assertEqual(
                    reopened.read_one("SELECT v FROM wb_scratch WHERE k = 'on-close'", ())[0],
                    "saved",
                )
            finally:
                reopened.close()

    async def test_one_click_health_compaction_and_storage_web_api_endpoints(self):
        with TemporaryDirectory(prefix="pr03-compact-api-") as tmp:
            root = Path(tmp)
            data_root = root / "runtime"
            runtime = StorageRuntime.open(data_root=data_root, installation_id=INSTALLATION_ID)
            try:
                # Seed legacy un-deduped native turns, cold core telemetry, legacy reaction BLOB, and native image BLOB.
                NativeConversationTurns(runtime)
                ctx = _make_context(1, ScopeKind.PRIVATE)
                big_prompt = ("冗余人格提示词" * 300).strip()
                with runtime.transaction() as conn:
                    for i in range(10):
                        conn.execute(
                            """INSERT INTO conversation_native_turns
                               (turn_id, context_key, context_json, role_instruction, input_observation_id,
                                observed_at, generation_state, phase_at, last_sent_at)
                               VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, NULL)""",
                            (f"old-t-{i}", ctx.key, encode(ctx), big_prompt, f"in-{i}",
                             stamp(NOW - timedelta(days=5)), stamp(NOW - timedelta(days=5))),
                        )
                record_observation(
                    runtime, ctx, _make_core_record("old-core-1", NOW - timedelta(days=25))
                )
                sem = SemanticStore(data_root)
                png = _sample_png(8, 8, (100, 150, 200))
                sem.put("o1", "reaction", "r1", {"descriptor": {"mime_type": "image/png"}}, content=png)
                with sem.db() as db:
                    db.execute("UPDATE items SET content = ? WHERE owner='o1' AND id='r1'", (png,))

                img_dir = data_root / "native_extensions" / "image_native"
                nimg = ImageStore(img_dir, None, identity_key="compact-img")
                op, _ = nimg.begin({
                    "scope_key": "s", "request_id": "r1", "origin_key": "o",
                    "invocation_id": "i", "operation": "generate",
                })
                nimg.state("s", op["id"], "dispatching")
                done = nimg.complete("s", op["id"], (png,), {"ok": True})
                art_id = done["receipt"]["artifacts"][0]["artifact_id"]
                with nimg.db() as db:
                    db.execute("UPDATE artifacts SET data = ? WHERE id = ?", (png, art_id))

                controller = NS(
                    data_root=root,
                    default_data_root=root,
                    config={"storage": {}},
                    runtime=runtime,
                    _control_lock=asyncio.Lock(),
                    profile_root=root,
                    settings=NS(installation_id=INSTALLATION_ID),
                    now=lambda: NOW,
                )
                api = StorageWebAPI(controller)
                req = _DummyRequest()
                # Check registered routes include /health and /compact.
                route_paths = {r[0] for r in api.routes()}
                self.assertIn("/astrbot_plugin_private_companion/storage/health", route_paths)
                self.assertIn("/astrbot_plugin_private_companion/storage/compact", route_paths)

                with patch("astrbot_host.storage_api.request", req):
                    health_resp = await api.health()
                self.assertEqual(health_resp["status"], "ok")
                health_data = health_resp["data"]
                self.assertEqual(health_data["status"], "compact_recommended")
                self.assertEqual(health_data["opportunities"]["un_deduped_native_turns"], 10)
                self.assertEqual(health_data["opportunities"]["cold_core_observations"], 1)
                self.assertEqual(health_data["opportunities"]["inline_reaction_blobs"], 1)
                self.assertEqual(health_data["opportunities"]["inline_native_image_blobs"], 1)

                # Reject a full rewrite before compaction changes any stored data.
                req.payload = json.dumps({"ttl_days": 14, "vacuum": True}).encode("utf-8")
                for disk_usage in ({"return_value": NS(free=0)}, {"side_effect": OSError}):
                    with (
                        patch("astrbot_host.storage_api.request", req),
                        patch("companion_v2.storage.compaction.shutil.disk_usage", **disk_usage),
                    ):
                        rejected = await api.compact()
                    self.assertEqual(rejected["status"], "error")
                    self.assertEqual(rejected["data"]["code"], "storage_insufficient_disk_for_vacuum")
                self.assertEqual(database_health_report(runtime)["opportunities"]["un_deduped_native_turns"], 10)

                # Invoke one-click compaction via POST /storage/compact.
                with patch("astrbot_host.storage_api.request", req):
                    compact_resp = await api.compact()
                self.assertEqual(compact_resp["status"], "ok")
                cdata = compact_resp["data"]
                self.assertEqual(cdata["steps"]["dedup"]["role_blobs_migrated"], 10)
                self.assertEqual(cdata["steps"]["archive"]["core_rows_archived"], 1)
                self.assertEqual(cdata["steps"]["media_offload"]["reactions"]["items_migrated"], 1)
                self.assertEqual(cdata["steps"]["media_offload"]["native_images"]["migrated"], 1)
                self.assertEqual(cdata["after"]["status"], "healthy")
                self.assertEqual(cdata["after"]["opportunities"]["un_deduped_native_turns"], 0)
                self.assertEqual(cdata["after"]["opportunities"]["cold_core_observations"], 0)
                self.assertEqual(cdata["after"]["opportunities"]["inline_reaction_blobs"], 0)
                self.assertEqual(cdata["after"]["opportunities"]["inline_native_image_blobs"], 0)

                # Also verify POST /storage with action="compact" and invalid option rejection.
                req.payload = json.dumps({"action": "compact", "ttl_days": 14}).encode("utf-8")
                with patch("astrbot_host.storage_api.request", req):
                    action_resp = await api.update()
                self.assertEqual(action_resp["status"], "ok")
                self.assertIn("compact_result", action_resp["data"])

                req.payload = json.dumps({"ttl_days": 0}).encode("utf-8")
                with patch("astrbot_host.storage_api.request", req):
                    bad_resp = await api.compact()
                self.assertEqual(bad_resp["status"], "error")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
