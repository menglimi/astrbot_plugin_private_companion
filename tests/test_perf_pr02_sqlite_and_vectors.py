"""Regression tests for P0 SQLite indexes, WAL sidecars, bounded reader pool, and vector acceleration."""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import math
from pathlib import Path
import random
import sqlite3
import struct
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from PIL import Image

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA
from astrbot_host.core_reply_telemetry import (
    finish_core_reply_telemetry,
    observe_core_agent_begin,
    observe_core_reply_delivery,
    observe_core_tool_finished,
    observe_core_tool_started,
    prepare_core_reply_telemetry,
)
from astrbot_host.native_extensions.image_native.store import ImageStore
from companion_v2.conversation import native_history, native_observations
from companion_v2.conversation.native_observations import NativeConversationObservations
from companion_v2.conversation.native_turns import (
    NativeConversationTurns,
    context_rows,
    latest_role_contexts,
    turns_available,
)
from companion_v2.execution.models import TaskContext, digest, encode, stamp
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import (
    AuthenticatedHostPrincipal,
    ResolvedIdentity,
    RoleBinding,
)
from companion_v2.memory import recall_vectors
from companion_v2.memory.search import SearchIndex
from companion_v2.model_gateway.cache_diagnostics import checked_signature_record, signature_changes
from companion_v2.model_gateway.core_observations import (
    available as core_observations_available,
    observations_for_role,
)
from companion_v2.model_gateway.embedding import EmbeddingGateway, QueryEmbeddingCache
from companion_v2.model_gateway.semantic_search import CatalogSearch
from companion_v2.storage import sqlite_tuning
from companion_v2.storage.runtime import StorageRuntime, table_exists
from companion_v2.storage.semantic import SemanticStore, VECTOR_BLOB_MAGIC
from tests.multi_host_support import MultiFixture
from tests.test_core_reply_telemetry import NativeRunnerFixture, response as llm_response
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


def _explain(runtime: StorageRuntime, sql: str, params: tuple = ()) -> str:
    rows = runtime.read_all("EXPLAIN QUERY PLAN " + sql, params)
    return "\n".join(str(row["detail"]) for row in rows)


def _sample_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), "#123456").save(buf, format="PNG")
    return buf.getvalue()


def _make_context(index: int, kind: ScopeKind = ScopeKind.PRIVATE, inst: str = "pr02-inst") -> TaskContext:
    scope = ScopeRef(
        inst, "onebot", kind, f"native-{index}",
        "bot-a", "login-a", f"conv-{index}", f"sess-{index}",
    )
    principal = AuthenticatedHostPrincipal(inst, "host-a", "onebot", f"user-{index}", "bot-a", "login-a")
    actor = ResolvedIdentity(principal, SubjectRef(inst, SubjectKind.HUMAN, f"human-{index}"), f"b-{index}", 1, 1)
    role = SubjectRef(inst, SubjectKind.BOT, f"role-{index}")
    return TaskContext(actor, RoleBinding(scope, role, 1), AudienceRef(scope, (actor.subject,)))


class AdaptivePoolAndPragmaTests(unittest.TestCase):
    def test_memory_adaptive_pragmas_cover_low_mid_high_and_ultra_hosts(self):
        cases = (
            # (mem_bytes, cpu_count, tier, pool_size, writer_cache, reader_cache, mmap_size, journal_limit)
            (1 * 1024**3, 2, "low", 2, -8192, -8192, 0, 16 * 1024 * 1024),
            (2 * 1024**3, 2, "low", 2, -8192, -8192, 0, 16 * 1024 * 1024),
            (4 * 1024**3, 2, "medium", 3, -32768, -16384, 128 * 1024 * 1024, 32 * 1024 * 1024),
            (4 * 1024**3, 4, "medium", 4, -32768, -16384, 128 * 1024 * 1024, 32 * 1024 * 1024),
            (8 * 1024**3, 8, "high", 4, -65536, -65536, 256 * 1024 * 1024, 64 * 1024 * 1024),
            (32 * 1024**3, 16, "ultra", 8, -131072, -65536, 1024 * 1024 * 1024, 64 * 1024 * 1024),
        )
        for mem_bytes, cpus, tier, pool_size, writer_cache, reader_cache, mmap_size, j_limit in cases:
            with self.subTest(tier=tier, cpus=cpus), TemporaryDirectory(prefix=f"pr02-ram-{tier}-") as tmp:
                with (
                    patch.object(sqlite_tuning, "detect_total_memory_bytes", return_value=mem_bytes),
                    patch.object(sqlite_tuning, "detect_cpu_count", return_value=cpus),
                ):
                    runtime = StorageRuntime.open(data_root=Path(tmp), installation_id=f"ram-{tier}")
                try:
                    self.assertEqual(runtime.tuning_profile.tier, tier)
                    self.assertEqual(runtime.reader_pool_size, pool_size)
                    # Check writer connection PRAGMAs.
                    with runtime.transaction() as writer_conn:
                        self.assertEqual(writer_conn.execute("PRAGMA cache_size").fetchone()[0], writer_cache)
                        self.assertEqual(writer_conn.execute("PRAGMA mmap_size").fetchone()[0], mmap_size)
                        self.assertEqual(writer_conn.execute("PRAGMA journal_size_limit").fetchone()[0], j_limit)
                    # Check pooled reader connection PRAGMAs.
                    with runtime.read_snapshot() as reader_conn:
                        self.assertEqual(reader_conn.execute("PRAGMA cache_size").fetchone()[0], reader_cache)
                        self.assertEqual(reader_conn.execute("PRAGMA mmap_size").fetchone()[0], mmap_size)
                        self.assertEqual(reader_conn.execute("PRAGMA query_only").fetchone()[0], 1)
                finally:
                    runtime.close()

    def test_startup_wal_truncation_and_hot_covering_indexes_and_write_tracker(self):
        with TemporaryDirectory(prefix="pr02-wal-idx-") as tmp:
            root = Path(tmp)
            runtime = StorageRuntime.open(data_root=root, installation_id="wal-idx-inst")
            try:
                # Create optional core_reply table and verify covering/expression indexes.
                from companion_v2.model_gateway.core_observations import _ensure_schema as ensure_core
                ensure_core(runtime)
                plan_src = _explain(
                    runtime,
                    "SELECT payload_json, created_at FROM sources WHERE owner=? AND installation_id=? AND state='active'",
                    ("content.governance", "wal-idx-inst"),
                )
                self.assertIn("idx_sources_owner_inst_state", plan_src)
                plan_wm = _explain(
                    runtime,
                    "SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(revision), 0), "
                    "COALESCE(MAX(observed_at), ''), COALESCE(MAX(received_at), '') "
                    "FROM astrbot_core_reply_observations WHERE role_key = ? AND received_at < ?",
                    ("rk", "2026-11-23"),
                )
                self.assertIn("COVERING INDEX idx_core_reply_role_watermark", plan_wm)
                plan_diag = _explain(
                    runtime,
                    "SELECT record_json FROM astrbot_core_reply_observations "
                    "WHERE role_key = ? AND scope_kind = ? "
                    "AND json_extract(context_json, '$.role_binding.scope.native_scope_id') = ? "
                    "AND json_extract(context_json, '$.role_binding.scope.session_id') = ? "
                    "AND json_extract(context_json, '$.role_binding.scope.conversation_id') = ? "
                    "ORDER BY received_at DESC, turn_id DESC LIMIT 20",
                    ("rk", "private", "n1", "s1", "c1"),
                )
                self.assertIn("idx_core_reply_scope_lookup", plan_diag)

                # Verify per-table write tracker increments on repeated identical SQL statements.
                groups = (frozenset(("runtime_metadata",)), frozenset(("sources",)))
                before = runtime.table_write_epoch_snapshot(groups)
                with runtime.transaction() as conn:
                    for i in range(3):
                        conn.execute(
                            "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (f"tracker-test-{i}", "v"),
                        )
                after = runtime.table_write_epoch_snapshot(groups)
                self.assertGreaterEqual(after[0] - before[0], 3)
                self.assertEqual(after[1], before[1])
            finally:
                runtime.close()

            # Reopen and verify startup WAL truncation cleans up any uncheckpointed WAL cleanly.
            runtime2 = StorageRuntime.open(data_root=root, installation_id="wal-idx-inst")
            try:
                wal_path = Path(str(runtime2.database_path) + "-wal")
                wal_size = wal_path.stat().st_size if wal_path.exists() else 0
                self.assertLessEqual(wal_size, runtime2.tuning_profile.journal_size_limit)
            finally:
                runtime2.close()

    def test_bounded_reader_pool_caps_connections_supports_reentrant_reads_and_closes_cleanly(self):
        with TemporaryDirectory(prefix="pr02-pool-") as tmp:
            with patch.object(sqlite_tuning, "detect_total_memory_bytes", return_value=1 * 1024**3):
                runtime = StorageRuntime.open(data_root=Path(tmp), installation_id="pool-inst")
            seen_ids: set[int] = set()
            seen_conns: list[sqlite3.Connection] = []
            lock = threading.Lock()
            barrier = threading.Barrier(6)
            errors: list[BaseException] = []

            def worker():
                try:
                    barrier.wait(timeout=5)
                    for _ in range(4):
                        with runtime.read_snapshot() as conn:
                            # Nested re-entrant read_snapshot + read_one must reuse the same connection without deadlocking.
                            with runtime.read_snapshot() as inner:
                                self.assertIs(inner, conn)
                                val = runtime.read_one("SELECT 1", ())
                                self.assertEqual(val[0], 1)
                            with lock:
                                if id(conn) not in seen_ids:
                                    seen_ids.add(id(conn))
                                    seen_conns.append(conn)
                            time.sleep(0.005)
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            self.assertEqual(errors, [])
            self.assertLessEqual(len(seen_conns), runtime.reader_pool_size)
            self.assertGreaterEqual(len(seen_conns), 1)
            self.assertEqual(len(runtime._all_readers), len(seen_conns))

            runtime.close()
            for conn in seen_conns:
                with self.assertRaises(sqlite3.ProgrammingError):
                    conn.execute("SELECT 1")

    def test_table_exists_cache_and_pragma_optimize(self):
        with TemporaryDirectory(prefix="pr02-cache-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp), installation_id="cache-inst")
            try:
                sqlite_master_calls = []
                original_read_one = runtime.read_one

                def spy_read_one(stmt, params=()):
                    if "sqlite_master" in stmt:
                        sqlite_master_calls.append(stmt)
                    return original_read_one(stmt, params)

                with patch.object(runtime, "read_one", side_effect=spy_read_one):
                    # Table does not exist yet: first call probes sqlite_master, repeats hit cache.
                    self.assertFalse(turns_available(runtime))
                    self.assertFalse(turns_available(runtime))
                    self.assertFalse(core_observations_available(runtime))
                    self.assertEqual(len(sqlite_master_calls), 2)

                    # Creating the native tables invalidates the cache via ensure_schema.
                    NativeConversationTurns(runtime)
                    self.assertTrue(turns_available(runtime))
                    self.assertTrue(turns_available(runtime))
                    self.assertTrue(table_exists(runtime, "conversation_native_sends"))
                    self.assertTrue(table_exists(runtime, "conversation_native_sends"))
                    # Exactly 1 probe per newly checked table, then cached forever.
                    self.assertEqual(len(sqlite_master_calls), 4)

                # Repeating NativeConversationTurns() executes zero DDL transactions.
                with patch.object(runtime, "transaction", wraps=runtime.transaction) as tx_spy:
                    for _ in range(5):
                        NativeConversationTurns(runtime)
                self.assertEqual(tx_spy.call_count, 0)

                # Explicit and checkpoint-triggered optimize succeed cleanly.
                self.assertTrue(runtime.optimize(force=True))
                runtime._last_optimize_at = 0.0
                cp = runtime.checkpoint_passive()
                self.assertEqual(len(cp), 3)
                self.assertGreater(runtime._last_optimize_at, 0.0)
            finally:
                runtime.close()


class NativeIndexAndLegacyMigrationTests(unittest.TestCase):
    def test_legacy_native_tables_migrate_backfill_and_hit_covering_and_effective_indexes(self):
        with TemporaryDirectory(prefix="pr02-migrate-") as tmp:
            root = Path(tmp)
            runtime = StorageRuntime.open(data_root=root, installation_id="pr02-inst")
            try:
                priv_ctx = _make_context(1, ScopeKind.PRIVATE)
                grp_ctx = _make_context(2, ScopeKind.GROUP)
                # Simulate an older DB whose native tables lack scope_kind, role_binding_key, and effective_at.
                with runtime.transaction() as conn:
                    conn.execute("""CREATE TABLE conversation_native_turns (
                        turn_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, context_json TEXT NOT NULL,
                        role_instruction TEXT NOT NULL, input_observation_id TEXT,
                        observed_at TEXT NOT NULL, generation_state TEXT NOT NULL,
                        phase_at TEXT NOT NULL, last_sent_at TEXT
                    )""")
                    conn.execute("""CREATE TABLE conversation_native_observations (
                        observation_id TEXT PRIMARY KEY, context_key TEXT NOT NULL,
                        memory_owner TEXT NOT NULL, conversation_owner TEXT NOT NULL,
                        turn_id TEXT NOT NULL, event_id TEXT NOT NULL,
                        kind TEXT NOT NULL, delivery_state TEXT NOT NULL,
                        context_json TEXT NOT NULL, source_json TEXT NOT NULL,
                        content_hash TEXT NOT NULL, observed_at TEXT NOT NULL
                    )""")
                    conn.execute("""CREATE TABLE conversation_native_sends (
                        observation_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, sent_at TEXT NOT NULL
                    )""")
                    for idx in range(5):
                        ts = stamp(NOW - timedelta(minutes=10 - idx))
                        conn.execute(
                            "INSERT INTO conversation_native_turns VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, NULL)",
                            (f"p-{idx}", priv_ctx.key, encode(priv_ctx), "P" * 512, f"in-p-{idx}", ts, ts),
                        )
                        conn.execute(
                            "INSERT INTO conversation_native_turns VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, NULL)",
                            (f"g-{idx}", grp_ctx.key, encode(grp_ctx), "G" * 512, f"in-g-{idx}", ts, ts),
                        )
                    t_obs = stamp(NOW - timedelta(minutes=5))
                    t_sent = stamp(NOW - timedelta(minutes=1))
                    conn.execute(
                        "INSERT INTO conversation_native_observations VALUES (?, ?, ?, ?, ?, ?, 'assistant', 'send_returned', ?, '{}', 'h1', ?)",
                        ("obs-reply-1", priv_ctx.key, "mem-owner-1", "conv-1", "p-1", "evt-1", encode(priv_ctx), t_obs),
                    )
                    conn.execute(
                        "INSERT INTO conversation_native_sends VALUES (?, ?, ?)",
                        ("obs-reply-1", "p-1", t_sent),
                    )
                    conn.execute(
                        "INSERT INTO conversation_native_observations VALUES (?, ?, ?, ?, ?, ?, 'input', 'input_observed', ?, '{}', 'h2', ?)",
                        ("obs-input-1", priv_ctx.key, "mem-owner-1", "conv-1", "p-2", "evt-2", encode(priv_ctx), stamp(NOW - timedelta(minutes=3))),
                    )
            finally:
                runtime.close()

            # Reopening automatically migrates columns, backfills effective_at/scope_kind/role_binding_key, and builds indexes.
            reopened = StorageRuntime.open(data_root=root, installation_id="pr02-inst")
            try:
                obs_row = reopened.read_one(
                    "SELECT effective_at, scope_kind FROM conversation_native_observations WHERE observation_id = ?",
                    ("obs-reply-1",),
                )
                self.assertEqual(obs_row["effective_at"], t_sent)
                self.assertEqual(obs_row["scope_kind"], "private")

                # Trigger keeps effective_at synchronized when a new send is inserted directly.
                new_sent = stamp(NOW)
                with reopened.transaction() as conn:
                    conn.execute(
                        """INSERT INTO conversation_native_observations
                        (observation_id, context_key, memory_owner, conversation_owner, turn_id, event_id,
                         kind, delivery_state, context_json, source_json, content_hash, observed_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'assistant', 'send_returned', ?, '{}', 'h3', ?)""",
                        ("obs-reply-2", priv_ctx.key, "mem-owner-1", "conv-1", "p-3", "evt-3",
                         encode(priv_ctx), stamp(NOW - timedelta(minutes=4))),
                    )
                    conn.execute("INSERT INTO conversation_native_sends VALUES (?, ?, ?)", ("obs-reply-2", "p-3", new_sent))
                self.assertEqual(
                    reopened.read_one(
                        "SELECT effective_at FROM conversation_native_observations WHERE observation_id = ?",
                        ("obs-reply-2",),
                    )["effective_at"],
                    new_sent,
                )

                # Q1: latest_role_contexts uses COVERING INDEX idx_native_turns_kind_role_obs for both group & private.
                captured = []
                orig_read_all = reopened.read_all
                with patch.object(reopened, "read_all", side_effect=lambda s, p=(): (captured.append((s, p)), orig_read_all(s, p))[1]):
                    grp_rows = latest_role_contexts(reopened, kind="group", limit=8)
                    priv_rows = latest_role_contexts(reopened, kind="private", limit=8)
                self.assertEqual(len(grp_rows), 1)
                self.assertEqual(len(priv_rows), 1)
                for stmt, params in captured:
                    plan = _explain(reopened, stmt, params)
                    self.assertIn("COVERING INDEX idx_native_turns_kind_role_obs", plan, plan)
                    self.assertNotIn("SCAN conversation_native_turns", plan, plan)

                # Q6 (native_history.rows): hits idx_native_obs_ctx_effective with ZERO TEMP B-TREE!
                captured.clear()
                with patch.object(reopened, "read_all", side_effect=lambda s, p=(): (captured.append((s, p)), orig_read_all(s, p))[1]):
                    hist = native_history.rows(reopened, priv_ctx, limit=32)
                self.assertEqual([r["observation_id"] for r in hist], ["obs-reply-2", "obs-reply-1", "obs-input-1"])
                hist_plan = _explain(reopened, captured[-1][0], captured[-1][1])
                self.assertIn("idx_native_obs_ctx_effective", hist_plan, hist_plan)
                self.assertNotIn("TEMP B-TREE", hist_plan, hist_plan)

                # Q6b (native_observations.rows_for_context): hits idx_native_obs_owner_kind_effective with ZERO TEMP B-TREE!
                captured.clear()
                with patch.object(reopened, "read_all", side_effect=lambda s, p=(): (captured.append((s, p)), orig_read_all(s, p))[1]):
                    native_observations.rows_for_context(reopened, priv_ctx, limit=32)
                owner_plan = _explain(reopened, captured[-1][0], captured[-1][1])
                self.assertIn("idx_native_obs_owner_kind_effective", owner_plan, owner_plan)
                self.assertNotIn("TEMP B-TREE", owner_plan, owner_plan)

                # Q13 (recalls native input lookup by event_id): hits idx_native_obs_event_input!
                recall_plan = _explain(
                    reopened,
                    "SELECT source_json, context_json FROM conversation_native_observations "
                    "WHERE kind = 'input' AND event_id = ?",
                    ("evt-2",),
                )
                self.assertIn("idx_native_obs_event_input", recall_plan, recall_plan)
                self.assertNotIn("SCAN conversation_native_observations", recall_plan, recall_plan)
            finally:
                reopened.close()


class DialogueBatchingAndTelemetryCoalescingTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await self.start_host()
        self.queued = []

    async def test_native_history_dialogue_eliminates_n_plus_one_queries_with_exact_parity(self):
        observer = self.controller.session.core_reply_observations
        context = None
        for idx in range(16):
            event = self.event(identifier=f"n1-msg-{idx}", text=f"用户第{idx}条消息")
            prepared = (await self.controller.prepare_core_reply(event)).unwrap()
            await prepared["native_source_future"]
            context = prepared["context"]
            self.clock.advance(timedelta(seconds=1))
            await observer.output(prepared, f"助手第{idx}条回复", state="send_returned")
            self.clock.advance(timedelta(seconds=1))

        assert context is not None
        runtime = self.app.runtime
        sql_count = 0
        orig_read_one = runtime.read_one
        orig_read_all = runtime.read_all

        def count_one(stmt, params=()):
            nonlocal sql_count
            sql_count += 1
            return orig_read_one(stmt, params)

        def count_all(stmt, params=()):
            nonlocal sql_count
            sql_count += 1
            return orig_read_all(stmt, params)

        with patch.object(runtime, "read_one", side_effect=count_one), \
                patch.object(runtime, "read_all", side_effect=count_all):
            batched = native_history.dialogue(self.app.messages, context, self.clock.now(), limit=32)

        self.assertEqual(len(batched), 32)
        # Previously 32 messages triggered ~250+ queries; batched path must stay under 16 queries.
        self.assertLessEqual(sql_count, 16, f"expected batched dialogue <=16 SQL queries, got {sql_count}")

        # Verify exact parity with unbatched body() fallback.
        with patch.object(native_history, "_can_use_batched_dialogue", return_value=False):
            unbatched = native_history.dialogue(self.app.messages, context, self.clock.now(), limit=32)
        self.assertEqual(batched, unbatched)

    async def test_core_reply_telemetry_coalesces_writes_and_strips_context_hashes(self):
        event = self.event(identifier="coalesce-turn", text="latest prompt")
        prepared = (await self.controller.prepare_core_reply(event)).unwrap()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"history turn {i}"} for i in range(40)]
        req = NS(
            system_prompt="system prompt",
            prompt="latest prompt",
            contexts=history,
            model="real-model",
            func_tool=NS(tools=[NS(name="read_file", description="desc", parameters={"type": "object"}, active=True)]),
            image_urls=[],
            audio_urls=[],
            extra_user_content_parts=[],
        )
        prepare_core_reply_telemetry(event, req, application=self.app, submit=self.queued.append, now=self.clock.now)
        # Preparing and recording intermediate steps must NOT enqueue DB writes.
        self.assertEqual(len(self.queued), 0)

        runner = NativeRunnerFixture(event, req, [[llm_response(1000, 20)], [llm_response(1200, 30)]])
        runner.run_context.messages = [{"role": "system", "content": "system prompt"}, *history, {"role": "user", "content": "latest prompt"}]
        from astrbot.core.pipeline.process_stage.follow_up import _ACTIVE_AGENT_RUNNERS
        _ACTIVE_AGENT_RUNNERS[event.unified_msg_origin] = runner
        self.addCleanup(_ACTIVE_AGENT_RUNNERS.pop, event.unified_msg_origin, None)

        observe_core_agent_begin(event, runner.run_context)
        self.assertEqual(len(self.queued), 0)
        _ = [item async for item in runner.step()]
        observe_core_tool_started(event, req.func_tool.tools[0])
        observe_core_tool_finished(event, req.func_tool.tools[0])
        for _ in range(4):
            observe_core_reply_delivery(event, "started")
            observe_core_reply_delivery(event, "returned")
        # Still ZERO DB writes before terminal completion!
        self.assertEqual(len(self.queued), 0)

        _ = [item async for item in runner.step()]
        finish_core_reply_telemetry(event, {"send_attempts": 4, "send_returns": 4})
        # Coalesced into ONE single pending persistence job!
        self.assertEqual(len(self.queued), 1)

        for work in list(self.queued):
            work()
        saved = observations_for_role(self.app.runtime, prepared["context"])[0]
        self.assertEqual(saved["generation_state"], "completed")
        self.assertEqual(saved["delivery"]["returned"], 4)
        self.assertEqual(len(saved["requests"]), 2)
        # Detailed 256-entry context_hashes arrays are stripped, while digests & counts stay intact.
        self.assertEqual(saved["request_hook_signature"]["context_hashes"], [])
        self.assertEqual(saved["request_hook_signature"]["context_material_count"], 40)
        self.assertEqual(saved["requests"][0]["signature"]["context_hashes"], [])
        self.assertEqual(saved["requests"][0]["signature"]["context_material_count"], 40)

        # Compact signatures growing monotonically do not falsely trigger context_prefix_change.
        sig_a = checked_signature_record(saved["requests"][0]["signature"])
        sig_b = checked_signature_record({
            **saved["requests"][0]["signature"],
            "context_sequence_hash": digest("grown-seq"),
            "context_multiset_hash": digest("grown-multi"),
            "context_material_count": 42,
            "context_bytes": saved["requests"][0]["signature"]["context_bytes"] + 64,
        })
        self.assertIsNotNone(sig_a)
        self.assertIsNotNone(sig_b)
        self.assertNotIn("context_prefix_change", signature_changes(sig_b, sig_a))


class SidecarWalAndVectorAccelerationTests(unittest.IsolatedAsyncioTestCase):
    def test_image_store_uses_wal_and_reads_never_block_behind_write_transaction(self):
        with TemporaryDirectory(prefix="pr02-img-") as tmp:
            store = ImageStore(Path(tmp), None, identity_key="img-id")
            with store.read_db() as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 1)
                self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], ImageStore.BUSY_TIMEOUT_MS)
                self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)

            req = {
                "scope_key": "scope-a",
                "request_id": "req-1",
                "origin_key": "origin-a",
                "invocation_id": "inv-1",
                "operation": "generate",
            }
            op, fresh = store.begin(req)
            self.assertTrue(fresh)
            store.state("scope-a", op["id"], "dispatching")
            done = store.complete("scope-a", op["id"], (_sample_png(),), {"ok": True})
            art_id = done["receipt"]["artifacts"][0]["artifact_id"]

            # Hold an exclusive write lock on a background thread; pure reads must complete immediately!
            entered = threading.Event()
            release = threading.Event()
            read_ok = threading.Event()

            def hold_write():
                with store.db() as db:
                    db.execute("UPDATE operations SET updated = updated WHERE id = ?", (op["id"],))
                    entered.set()
                    release.wait(timeout=3)

            writer = threading.Thread(target=hold_write)
            writer.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                got = store.get("scope-a", op["id"])
                by_req = store.by_request("scope-a", "req-1")
                meta, raw = store.artifact("scope-a", art_id)
                self.assertEqual(got["id"], op["id"])
                self.assertEqual(by_req["id"], op["id"])
                self.assertEqual(meta["artifact_id"], art_id)
                self.assertGreater(len(raw), 0)
                read_ok.set()
            finally:
                release.set()
                writer.join(timeout=3)
            self.assertTrue(read_ok.is_set())

    def test_search_index_enables_wal_and_concurrent_reader_does_not_block_writes(self):
        with TemporaryDirectory(prefix="pr02-search-") as tmp:
            idx = SearchIndex(Path(tmp))
            with closing(idx._open()) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 1)
            idx.sync("owner-1", [{"id": "doc-1", "text": "咖啡和猫"}])
            # In WAL mode, a concurrent BEGIN reader does NOT block a subsequent write commit.
            with closing(sqlite3.connect(idx.path)) as reader:
                reader.execute("BEGIN")
                count_before = reader.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                self.assertEqual(count_before, 1)
                idx.sync("owner-1", [
                    {"id": "doc-1", "text": "咖啡和猫"},
                    {"id": "doc-2", "text": "热茶和狗"},
                ])
                # Reader keeps its consistent snapshot while writer committed cleanly.
                self.assertEqual(reader.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            with closing(idx._open()) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)

    def test_batch_cosines_f32_numpy_matches_python_fallback_and_skips_invalid_rows(self):
        rng = random.Random(42)
        dims = 64
        query = tuple(rng.uniform(-1.0, 1.0) for _ in range(dims))
        q_norm = math.hypot(*query)
        unit_q = tuple(v / q_norm for v in query)
        row_ids = []
        blobs = []
        for idx in range(128):
            vec = [rng.uniform(-1.0, 1.0) for _ in range(dims)]
            row_ids.append(f"doc-{idx}")
            blobs.append(struct.pack("<" + "f" * dims, *vec))
        # Add edge cases: zero vector, NaN vector, malformed byte length.
        row_ids.extend(["zero", "nan", "short"])
        blobs.extend([
            struct.pack("<" + "f" * dims, *([0.0] * dims)),
            struct.pack("<" + "f" * dims, *([float("nan")] + [1.0] * (dims - 1))),
            b"\x00\x01\x02",
        ])

        np_scores = dict(recall_vectors.batch_cosines_f32(unit_q, row_ids, blobs, dims))
        with patch.object(recall_vectors, "_NP", None):
            py_scores = dict(recall_vectors.batch_cosines_f32(unit_q, row_ids, blobs, dims))

        self.assertEqual(set(np_scores), set(py_scores))
        self.assertEqual(len(np_scores), 128)
        for key in py_scores:
            self.assertAlmostEqual(np_scores[key], py_scores[key], places=5)

    async def test_semantic_store_uses_wal_compact_f16_blobs_and_reads_legacy_json(self):
        with TemporaryDirectory(prefix="pr02-sem-") as tmp:
            store = SemanticStore(Path(tmp))
            with store.db(read_only=True) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 1)
                self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], SemanticStore.BUSY_TIMEOUT_MS)

            item_new = store.put("o1", "reaction", "r-new", {"text": "抱抱安慰"})
            item_old = store.put("o1", "reaction", "r-old", {"text": "摸摸头"})
            vec_new = tuple((1.0 if i == 0 else 0.0) for i in range(128))
            vec_old = tuple((0.0 if i == 0 else (1.0 if i == 1 else 0.0)) for i in range(128))

            self.assertTrue(store.put_vector(item_new, "emb-v1", vec_new))
            # Verify raw DB column stores compact float16 BLOB with magic prefix.
            with store.db(read_only=True) as db:
                raw_new = db.execute("SELECT vector FROM vectors WHERE id='r-new'").fetchone()["vector"]
            self.assertIsInstance(raw_new, bytes)
            self.assertTrue(raw_new.startswith(VECTOR_BLOB_MAGIC))
            self.assertEqual(len(raw_new), 4 + 128 * 2)

            # Insert a legacy JSON TEXT vector directly to verify seamless backward-compatible reads.
            with store.db() as db:
                db.execute(
                    "INSERT INTO vectors VALUES (?,?,?,?,?,?,?)",
                    ("o1", "reaction", "r-old", item_old["revision"], "emb-v1",
                     digest(item_old["payload"]), encode(vec_old)),
                )

            decoded_new = store.vector(item_new, "emb-v1")
            decoded_old = store.vector(item_old, "emb-v1")
            self.assertEqual(len(decoded_new), 128)
            self.assertAlmostEqual(decoded_new[0], 1.0, places=3)
            self.assertEqual(len(decoded_old), 128)
            self.assertAlmostEqual(decoded_old[1], 1.0, places=3)

            # CatalogSearch seamlessly ranks mixed float16 BLOB + legacy JSON vectors via vectorized dot product.
            class StubPort:
                def binding(self, identifier, kind):
                    return "emb-v1"
                async def embed(self, identifier, texts):
                    return [list(vec_new) for _ in texts]

            search = CatalogSearch(store, EmbeddingGateway(StubPort()), lambda item, context: True)
            result = await search.search("o1", "reaction", "需要抱抱", limit=5)
            self.assertEqual(result["mode"], "hybrid")
            self.assertEqual(result["embedded"], 2)
            self.assertEqual(result["items"][0]["id"], "r-new")

    async def test_query_embedding_cache_lru_ttl_and_singleflight_coalescing(self):
        now = [100.0]
        cache = QueryEmbeddingCache(max_entries=2, ttl_seconds=60.0, clock=lambda: now[0])
        cache.put(("a", "m", "k1"), (1.0, 0.0))
        cache.put(("a", "m", "k2"), (0.0, 1.0))
        # Touch k1 so k2 becomes LRU.
        self.assertEqual(cache.get(("a", "m", "k1")), (1.0, 0.0))
        cache.put(("a", "m", "k3"), (0.5, 0.5))
        self.assertIsNone(cache.get(("a", "m", "k2")))
        self.assertEqual(cache.get(("a", "m", "k1")), (1.0, 0.0))
        # Advance past TTL -> expires cleanly.
        now[0] += 61.0
        self.assertIsNone(cache.get(("a", "m", "k1")))

        # Concurrent singleflight deduplication in EmbeddingGateway.
        started = asyncio.Event()
        release = asyncio.Event()
        provider_calls = 0

        class SlowPort:
            def binding(self, identifier, kind):
                return "emb-singleflight"
            async def embed(self, identifier, texts):
                nonlocal provider_calls
                provider_calls += 1
                started.set()
                await release.wait()
                return [[1.0, 0.0] for _ in texts]

        gateway = EmbeddingGateway(SlowPort())
        task1 = asyncio.create_task(gateway.embed("owner-a", ["hello"]))
        await started.wait()
        task2 = asyncio.create_task(gateway.embed("owner-a", ["hello"]))
        await asyncio.sleep(0)
        release.set()
        res1, res2 = await asyncio.gather(task1, task2)
        self.assertEqual(res1, res2)
        self.assertEqual(provider_calls, 1)
        self.assertGreaterEqual(gateway.metrics["cache_hits"], 1)

    def test_has_table_wal_read_snapshot_prevents_negative_poisoning_and_stale_false_positive(self):
        with TemporaryDirectory(prefix="pr02-snap-cache-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id="pr02-inst")
            try:
                # 1. Negative-cache poisoning guard: snapshot pinned at epoch 0 must NOT poison shared cache.
                snap_pinned = threading.Event()
                ddl_committed = threading.Event()
                snap_results: dict[str, bool] = {}

                def reader_in_epoch_0():
                    with runtime.read_snapshot() as conn:
                        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                        snap_pinned.set()
                        ddl_committed.wait(timeout=3)
                        snap_results["saw_alpha"] = runtime.has_table("late_alpha_tbl")

                t = threading.Thread(target=reader_in_epoch_0)
                t.start()
                try:
                    self.assertTrue(snap_pinned.wait(timeout=2))
                    with runtime.transaction() as conn:
                        conn.execute("CREATE TABLE late_alpha_tbl (id INTEGER PRIMARY KEY)")
                    ddl_committed.set()
                finally:
                    t.join(timeout=3)

                self.assertFalse(snap_results["saw_alpha"])
                # Main thread at epoch 1 MUST observe True (never poisoned with False!).
                self.assertTrue(runtime.has_table("late_alpha_tbl"))

                # 2. Stale-snapshot false-positive guard: positive cache written at epoch 2
                # must NOT mislead a snapshot pinned at epoch 1.
                snap2_pinned = threading.Event()
                cached_epoch2 = threading.Event()
                snap2_results: dict[str, bool] = {}

                def reader_in_epoch_1():
                    with runtime.read_snapshot() as conn:
                        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                        snap2_pinned.set()
                        cached_epoch2.wait(timeout=3)
                        snap2_results["saw_beta"] = runtime.has_table("late_beta_tbl")

                t2 = threading.Thread(target=reader_in_epoch_1)
                t2.start()
                try:
                    self.assertTrue(snap2_pinned.wait(timeout=2))
                    with runtime.transaction() as conn:
                        conn.execute("CREATE TABLE late_beta_tbl (id INTEGER PRIMARY KEY)")
                    self.assertTrue(runtime.has_table("late_beta_tbl"))
                    cached_epoch2.set()
                finally:
                    t2.join(timeout=3)

                self.assertFalse(snap2_results["saw_beta"])
                self.assertTrue(runtime.has_table("late_beta_tbl"))
            finally:
                runtime.close()

    def test_ensure_schema_outer_rollback_does_not_poison_initialized_schemas(self):
        with TemporaryDirectory(prefix="pr02-rollback-schema-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id="pr02-inst")
            try:
                def init_temp_table(conn):
                    conn.execute("CREATE TABLE rollback_probe_tbl (id INTEGER PRIMARY KEY)")

                with self.assertRaises(RuntimeError):
                    with runtime.transaction():
                        runtime.ensure_schema("rollback_probe.v1", init_temp_table)
                        raise RuntimeError("abort outer tx")

                self.assertFalse(runtime.has_table("rollback_probe_tbl"))
                # Calling ensure_schema again at outermost depth MUST recreate the table.
                ran = runtime.ensure_schema("rollback_probe.v1", init_temp_table)
                self.assertTrue(ran)
                self.assertTrue(runtime.has_table("rollback_probe_tbl"))
                # Subsequent call is now a cached no-op.
                self.assertFalse(runtime.ensure_schema("rollback_probe.v1", init_temp_table))
            finally:
                runtime.close()

    def test_reader_connection_non_sqlite_error_releases_counters_and_does_not_deadlock_close(self):
        with TemporaryDirectory(prefix="pr02-reader-err-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp) / "v2", installation_id="pr02-inst")
            try:
                with patch("companion_v2.storage.runtime.sqlite3.connect", side_effect=OSError("fd limit")):
                    with self.assertRaises(OSError):
                        runtime.read_one("SELECT 1", ())
                self.assertEqual(runtime._creating_readers, 0)
                self.assertEqual(runtime._active_readers, 0)
            finally:
                # Must return promptly without deadlocking on leaked counters.
                runtime.close()

    async def test_embedding_singleflight_leader_cancel_and_error_handling(self):
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        calls = 0

        class CancelPort:
            def binding(self, identifier, kind):
                return "emb-cancel"
            async def embed(self, identifier, texts):
                nonlocal calls
                calls += 1
                if calls == 1:
                    first_started.set()
                    await first_release.wait()
                return [[0.0, 1.0] for _ in texts]

        gateway = EmbeddingGateway(CancelPort())
        t1 = asyncio.create_task(gateway.embed("owner-c", ["retry-me"]))
        await first_started.wait()
        t2 = asyncio.create_task(gateway.embed("owner-c", ["retry-me"]))
        await asyncio.sleep(0)
        t1.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t1
        # Innocent waiter t2 must NOT receive CancelledError and must succeed by re-fetching cleanly.
        binding, vectors = await t2
        self.assertEqual(binding, "emb-cancel")
        self.assertEqual(len(vectors), 1)
        self.assertAlmostEqual(vectors[0][1], 1.0, places=5)
        self.assertEqual(calls, 2)
        self.assertEqual(gateway.metrics["cache_hits"], 0)

        # Provider failure must not falsely increment cache_hits for concurrent waiters.
        err_started = asyncio.Event()
        err_release = asyncio.Event()

        class FailingPort:
            def binding(self, identifier, kind):
                return "emb-fail"
            async def embed(self, identifier, texts):
                err_started.set()
                await err_release.wait()
                raise RuntimeError("upstream down")

        fail_gw = EmbeddingGateway(FailingPort())
        f1 = asyncio.create_task(fail_gw.embed("owner-c", ["boom"]))
        await err_started.wait()
        f2 = asyncio.create_task(fail_gw.embed("owner-c", ["boom"]))
        await asyncio.sleep(0)
        err_release.set()
        with self.assertRaises(RuntimeError):
            await f1
        with self.assertRaises(RuntimeError):
            await f2
        self.assertEqual(fail_gw.metrics["cache_hits"], 0)

    def test_decode_vector_f32_rejects_malformed_json_and_vectors_for_items_chunks_in(self):
        from companion_v2.storage.semantic import decode_vector_f32

        for corrupt in (
            b'{"not": "a list"}',
            b'["bad", "num"]',
            b"123",
            b"[]",
            '{"not": "a list"}',
            '["bad", "num"]',
            "[null, 1.0]",
            '["NaN"]',
            VECTOR_BLOB_MAGIC + b"\x00",
            VECTOR_BLOB_MAGIC,
        ):
            self.assertIsNone(decode_vector_f32(corrupt), f"expected None for {corrupt!r}")

        with TemporaryDirectory(prefix="pr02-sem-chunk-") as tmp:
            store = SemanticStore(Path(tmp))
            # Test >500 items to exercise IN (...) chunking cleanly.
            items = []
            vec = (1.0, 0.0, 0.0, 0.0)
            for i in range(550):
                it = store.put("o-chunk", "reaction", f"r-{i}", {"text": f"t-{i}"})
                store.put_vector(it, "emb-c", vec)
                items.append(it)
            fetched = store.vectors_for_items("o-chunk", "reaction", "emb-c", items)
            self.assertEqual(len(fetched), 550)
            # Verify the 3 read_only methods work cleanly.
            self.assertEqual(store.slang_candidate_counts("o-chunk"), {})
            self.assertIn("totals", store.reaction_collection_summary("o-chunk"))
            self.assertEqual(store.unclaimed_legacy_reaction_owners("o-chunk"), ())


class MaintenanceDomainEpochAndJournalBatchTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.host_instance = await self.start()
        self.app = self.host_instance.application
        batch = self.host_instance.receive(self.event("pr02-epoch-seed")).unwrap()
        self.advance(self.app)
        await self.done(self.app, batch)
        self.role_context = self.context(self.app, batch)

    async def test_unrelated_and_core_writes_skip_legacy_watermark_while_legacy_write_invalidates(self):
        from companion_v2.model_gateway import maintenance_cache as mc_mod
        from companion_v2.model_gateway.core_observations import record_observation
        from companion_v2.model_gateway.management import ModelManagement
        from tests.test_maintenance_perf_cache import _make_observation

        app = self.app
        ctx = self.role_context
        operator = self.core
        manager = ModelManagement(app, ctx)
        mc_mod.clear_maintenance_cache(app.runtime)

        # Seed 1 historical + 1 today core observation and warm caches.
        record_observation(app.runtime, ctx, _make_observation("t-hist-1", app.now() - timedelta(days=2)))
        record_observation(app.runtime, ctx, _make_observation("t-today-1", app.now() - timedelta(minutes=10)))
        cold = manager.maintenance(operator).unwrap()
        base_calls = cold["usage_sources"]["astrbot_core"]["totals"]["calls"]
        self.assertEqual(base_calls, 2)

        # Unrelated write (runtime_metadata) must NOT scan legacy or aux watermarks!
        with app.runtime.transaction() as conn:
            conn.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("unrelated.heartbeat", '{"ok":true}'),
            )
        with (
            patch.object(mc_mod, "read_legacy_watermark", side_effect=AssertionError("must skip legacy wm")),
            patch.object(mc_mod, "read_aux_maintenance_watermark", side_effect=AssertionError("must skip aux wm")),
        ):
            warm = manager.maintenance(operator).unwrap()
        self.assertEqual(warm["usage_sources"]["astrbot_core"]["totals"]["calls"], 2)

        # New core observation invalidates snapshot, but MUST STILL skip read_legacy_watermark and aux!
        record_observation(app.runtime, ctx, _make_observation("t-today-2", app.now() - timedelta(minutes=2)))
        with (
            patch.object(mc_mod, "read_legacy_watermark", side_effect=AssertionError("must skip legacy wm on core turn")),
            patch.object(mc_mod, "read_aux_maintenance_watermark", side_effect=AssertionError("must skip aux wm on core turn")),
        ):
            updated = manager.maintenance(operator).unwrap()
        self.assertEqual(updated["usage_sources"]["astrbot_core"]["totals"]["calls"], 3)

        # In-process write to a legacy table MUST invalidate BOTH legacy AND aux watermarks
        # (read_aux_maintenance_watermark digests model_calls/model_requests/failures/diagnostics).
        with app.runtime.transaction() as conn:
            conn.execute("DELETE FROM model_call_diagnostics WHERE 1 = 0")
        seen_legacy_calls = []
        seen_aux_calls = []
        orig_read_legacy = mc_mod.read_legacy_watermark
        orig_read_aux = mc_mod.read_aux_maintenance_watermark

        def _spy_legacy(rt):
            seen_legacy_calls.append(1)
            return orig_read_legacy(rt)

        def _spy_aux(*args, **kwargs):
            seen_aux_calls.append(1)
            return orig_read_aux(*args, **kwargs)

        with (
            patch.object(mc_mod, "read_legacy_watermark", side_effect=_spy_legacy),
            patch.object(mc_mod, "read_aux_maintenance_watermark", side_effect=_spy_aux),
        ):
            manager.maintenance(operator).unwrap()
        self.assertGreaterEqual(len(seen_legacy_calls), 1)
        self.assertGreaterEqual(len(seen_aux_calls), 1)

    async def test_journal_chain_validation_deduplicates_queries_and_still_observes_revocation(self):
        from companion_v2.foundation.refs import RoleOwnerRef, SourceRef, VersionRef
        from companion_v2.role_work.journal.models import (
            JournalContext,
            JournalDraft,
            JournalKey,
            JournalKind,
            JournalOrigin,
            JournalRef,
        )
        from companion_v2.role_work.life.management import RoleLifeManagement
        from companion_v2.sources.models import SourceRecord

        app = self.app
        ctx = self.role_context
        operator = self.core
        owner = RoleOwnerRef(ctx.scope.installation_id, ctx.scope.bot_id, ctx.role_binding.role.subject_id)
        now = app.now()

        # Publish 3 shared sources and build a 10-day chained journal ancestry.
        shared_sources = []
        for idx in range(3):
            sid = f"j-src-{idx}"
            ref = SourceRef(sid, "conversation.native_input", ctx.scope, VersionRef(sid, 1), now - timedelta(days=20))
            rec = SourceRecord(ref, ctx.audience, digest(f"body-{idx}"), f"body-{idx}")
            self.assertTrue(app.role_journal.sources.publish(rec, observed_at=now - timedelta(days=20)).is_success)
            shared_sources.append(ref)

        prev_refs: list[JournalRef] = []
        for day_i in range(1, 11):
            day_str = f"2026-03-{day_i:02d}"
            jkey = JournalKey(owner, JournalKind.DIARY, day_str)
            jctx = JournalContext(jkey, ctx.scope, "Asia/Shanghai")
            draft = JournalDraft(
                context=jctx,
                origin=JournalOrigin.GENERATED,
                body=f"Day {day_i} body",
                sources=tuple(shared_sources),
                journals=tuple(prev_refs[-3:]),
            )
            receipt = app.role_journal.save(
                draft,
                expected_revision=0,
                operation_id=f"op-j-{day_i}",
                observed_at=now - timedelta(days=11 - day_i),
            )
            prev_refs.append(JournalRef(jkey, receipt.head.revision))

        # Count read_one calls during RoleLifeManagement.status(): must be O(N), not O(N^2).
        manager = RoleLifeManagement(app, ctx)
        orig_read_one = app.runtime.read_one
        source_reads = []

        def _counting_read_one(sql, params):
            if "FROM sources WHERE" in sql:
                source_reads.append(params)
            return orig_read_one(sql, params)

        with patch.object(app.runtime, "read_one", side_effect=_counting_read_one):
            status = manager.status(operator)
        self.assertEqual(len(status["journals"]), 10)
        self.assertTrue(all(j["unavailable_reason"] is None for j in status["journals"]))
        # 3 unique sources across 10 chained diaries -> strictly 3 source reads (not 30+!).
        self.assertEqual(len(source_reads), 3)

        # Revoking one shared source must immediately surface unavailable_reason!
        revoked = app.role_journal.sources.revoke(
            shared_sources[0],
            expected_governance_revision=1,
            observed_at=now,
        )
        self.assertTrue(revoked.is_success)
        status_after = manager.status(operator)
        self.assertTrue(all(j["unavailable_reason"] == "source_revoked" for j in status_after["journals"]))

    async def test_validation_batch_clears_cached_validations_on_owner_guard_change_and_bg_write(self):
        from companion_v2.execution.models import TaskError
        from companion_v2.foundation.refs import RoleOwnerRef, SourceRef, VersionRef
        from companion_v2.role_work.journal.models import (
            JournalContext,
            JournalDraft,
            JournalKey,
            JournalKind,
            JournalOrigin,
        )
        from companion_v2.sources.dependencies import validation_batch
        from companion_v2.sources.governance import bind
        from companion_v2.sources.models import SourceRecord

        app = self.app
        ctx = self.role_context
        now = app.now()
        owner = RoleOwnerRef(ctx.scope.installation_id, ctx.scope.bot_id, ctx.role_binding.role.subject_id)

        ref = SourceRef("guard-mid-src", "dynamic.guarded.owner", ctx.scope, VersionRef("guard-mid-src", 1), now)
        rec = SourceRecord(ref, ctx.audience, digest("guard-body"), "guard-body")
        app.role_journal.sources.publish(rec, observed_at=now).unwrap()

        jkey = JournalKey(owner, JournalKind.DIARY, "2026-03-20")
        jctx = JournalContext(jkey, ctx.scope, "Asia/Shanghai")
        app.role_journal.save(
            JournalDraft(context=jctx, origin=JournalOrigin.GENERATED, body="Guarded", sources=(ref,), journals=()),
            expected_revision=0,
            operation_id="op-guard-mid",
            observed_at=now,
        )

        # 1. Within a single validation_batch, registering a rejecting owner guard MUST invalidate
        #    sources.validated_refs, sources.validated_audience, and role_journal.validated immediately.
        recipient = ctx.audience.recipients[0]
        with app.runtime.read_snapshot(), validation_batch(app.runtime, now):
            app.role_journal.sources.validate_reference(ref, observed_at=now).unwrap()
            app.role_journal.sources.validate_reference_for_audience(
                ref, recipient=recipient, observed_at=now
            ).unwrap()
            self.assertEqual(app.role_journal.get(jkey, observed_at=now).draft.body, "Guarded")

            def _deny(record, at):
                raise TaskError("owner_denied_mid_batch", "newly bound guard rejects source")

            guard = bind(app.runtime, "dynamic.guarded.owner", _deny)
            try:
                res_ref = app.role_journal.sources.validate_reference(ref, observed_at=now)
                self.assertFalse(res_ref.is_success)
                self.assertEqual(res_ref.failure.code, "owner_denied_mid_batch")

                res_aud = app.role_journal.sources.validate_reference_for_audience(
                    ref, recipient=recipient, observed_at=now
                )
                self.assertFalse(res_aud.is_success)
                self.assertEqual(res_aud.failure.code, "owner_denied_mid_batch")

                with self.assertRaises(TaskError) as j_err:
                    app.role_journal.get(jkey, observed_at=now)
                self.assertEqual(j_err.exception.code, "owner_denied_mid_batch")
            finally:
                guard.close()

        # 2. RoleJournalService._read_only_batch() must stay active on a reader thread while a
        #    background thread holds runtime.transaction(), and still disable itself on the writer thread.
        tx_entered = threading.Event()
        tx_release = threading.Event()

        def _bg_writer():
            with app.runtime.transaction():
                tx_entered.set()
                tx_release.wait(timeout=3.0)

        writer_thread = threading.Thread(target=_bg_writer)
        writer_thread.start()
        try:
            self.assertTrue(tx_entered.wait(timeout=2.0))
            with app.runtime.read_snapshot(), validation_batch(app.runtime, now):
                self.assertIsNotNone(app.role_journal._read_only_batch(now))
        finally:
            tx_release.set()
            writer_thread.join(timeout=3.0)

        with app.runtime.transaction(), validation_batch(app.runtime, now):
            self.assertIsNone(app.role_journal._read_only_batch(now))

    async def test_cold_start_persistent_core_history_disk_cache_rollover_and_signature_validation(self):
        from companion_v2.model_gateway import maintenance_cache as mc_mod
        from companion_v2.model_gateway.core_observations import record_observation
        from companion_v2.model_gateway.management import ModelManagement
        from companion_v2.model_gateway.models import CacheSignature
        from tests.test_maintenance_perf_cache import _make_observation

        # 1. Verify C-level CacheSignature validation retains 100% strict rejection semantics.
        good_hex = "a" * 64
        class _StrSub(str):
            pass

        valid_sig = CacheSignature(
            model_hash=good_hex,
            system_hash=good_hex,
            system_parts=(good_hex,),
            tools_hash=good_hex,
            tool_parts=(good_hex,),
            context_hashes=(good_hex,),
            context_bytes=128,
            context_observed=True,
        )
        self.assertEqual(valid_sig.context_bytes, 128)
        for bad_kwargs in (
            {"model_hash": "A" * 64},
            {"model_hash": "g" * 64},
            {"model_hash": "a" * 63},
            {"model_hash": "\u00e9" * 64},
            {"model_hash": _StrSub(good_hex)},
            {"context_bytes": True},
            {"context_bytes": -1},
            {"context_bytes": 2**31},
        ):
            params = {
                "model_hash": good_hex,
                "system_hash": good_hex,
                "system_parts": (good_hex,),
                "tools_hash": good_hex,
                "tool_parts": (good_hex,),
                "context_hashes": (good_hex,),
                "context_bytes": 128,
                "context_observed": True,
                **bad_kwargs,
            }
            with self.assertRaises(ValueError, msg=f"expected rejection for {bad_kwargs!r}"):
                CacheSignature(**params)

        # 2. Seed multi-day core observations and verify persistent disk cache survives RAM clear
        #    (simulated process restart) with zero historical SQL scan & exact parity!
        app = self.app
        ctx = self.role_context
        operator = self.core
        manager = ModelManagement(app, ctx)
        mc_mod.clear_maintenance_cache(app.runtime)

        now = app.now()
        record_observation(app.runtime, ctx, _make_observation("disk-h-1", now - timedelta(days=3)))
        record_observation(app.runtime, ctx, _make_observation("disk-h-2", now - timedelta(days=2)))
        record_observation(app.runtime, ctx, _make_observation("disk-h-3", now - timedelta(days=1)))
        record_observation(app.runtime, ctx, _make_observation("disk-t-1", now - timedelta(minutes=5)))

        first = manager.maintenance(operator).unwrap()
        self.assertEqual(first["usage_sources"]["astrbot_core"]["totals"]["calls"], 4)
        cache_dir = Path(app.runtime.data_root) / mc_mod.DISK_CORE_HISTORY_DIRNAME
        disk_files = list(cache_dir.glob("core-hist-v*.zlib"))
        self.assertEqual(len(disk_files), 1)

        # Simulate process restart: wipe ONLY in-memory cache (keep disk file intact!).
        mc_mod.runtime_maintenance_cache(app.runtime).clear()
        with patch.object(
            mc_mod,
            "_query_core_partition_rows_chunked",
            side_effect=AssertionError("restart cold boot must hit disk cache without scanning historical rows"),
        ):
            restarted = manager.maintenance(operator).unwrap()
        self.assertEqual(restarted["usage_sources"]["astrbot_core"]["totals"], first["usage_sources"]["astrbot_core"]["totals"])
        self.assertEqual(restarted["usage_sources"]["astrbot_core"]["coverage"], first["usage_sources"]["astrbot_core"]["coverage"])
        self.assertEqual(mc_mod.maintenance_cache_stats(app.runtime)["disk_day_cache_hits"], 1)

        # 3. Cross-restart midnight rollover: advance clock +1 day, wipe RAM, verify disk_advance
        #    only scans the 1-day gap [prev_cutoff, next_cutoff) and reaches exact parity!
        self.clock.advance(timedelta(days=1))
        record_observation(app.runtime, ctx, _make_observation("disk-t-next", app.now() - timedelta(minutes=2)))
        mc_mod.runtime_maintenance_cache(app.runtime).clear()
        gap_bounds = []
        orig_chunked = mc_mod._query_core_partition_rows_chunked

        def _spy_chunked(*args, **kwargs):
            gap_bounds.append((kwargs.get("received_ge"), kwargs.get("received_lt")))
            return orig_chunked(*args, **kwargs)

        with patch.object(mc_mod, "_query_core_partition_rows_chunked", side_effect=_spy_chunked):
            rolled = manager.maintenance(operator).unwrap()
        self.assertEqual(rolled["usage_sources"]["astrbot_core"]["totals"]["calls"], 5)
        self.assertEqual(len(gap_bounds), 1)
        self.assertIsNotNone(gap_bounds[0][0], "cross-restart rollover must query bounded 1-day gap, not full history")
        self.assertEqual(mc_mod.maintenance_cache_stats(app.runtime)["day_cache_advances"], 1)

        # Verify exact parity with uncached full computation.
        with patch("companion_v2.model_gateway.management.runtime_maintenance_cache", return_value=None):
            uncached = manager.maintenance(operator).unwrap()
        self.assertEqual(rolled["usage_sources"]["astrbot_core"]["totals"], uncached["usage_sources"]["astrbot_core"]["totals"])
        self.assertEqual(rolled["usage_sources"]["astrbot_core"]["cache_diagnostics"], uncached["usage_sources"]["astrbot_core"]["cache_diagnostics"])

    async def test_malformed_disk_core_history_cache_falls_back_silently(self):
        import copy
        import zlib
        from companion_v2.model_gateway import maintenance_cache as mc_mod
        from companion_v2.model_gateway.core_observations import record_observation
        from companion_v2.model_gateway.management import ModelManagement
        from tests.test_maintenance_perf_cache import _make_observation

        app = self.app
        ctx = self.role_context
        operator = self.core
        manager = ModelManagement(app, ctx)
        mc_mod.clear_maintenance_cache(app.runtime)

        now = app.now()
        record_observation(app.runtime, ctx, _make_observation("mal-h-1", now - timedelta(days=2)))
        record_observation(app.runtime, ctx, _make_observation("mal-t-1", now - timedelta(minutes=5)))

        baseline = manager.maintenance(operator).unwrap()
        cache_dir = Path(app.runtime.data_root) / mc_mod.DISK_CORE_HISTORY_DIRNAME
        disk_files = list(cache_dir.glob("core-hist-v*.zlib"))
        self.assertEqual(len(disk_files), 1)
        disk_path = disk_files[0]
        valid_payload = json.loads(zlib.decompress(disk_path.read_bytes()).decode("utf-8"))

        def _mutate(fn):
            doc = copy.deepcopy(valid_payload)
            fn(doc)
            return zlib.compress(json.dumps(doc).encode("utf-8"), level=1)

        corrupt_blobs = [
            ("truncated_bytes", disk_path.read_bytes()[:12]),
            ("non_dict_json", zlib.compress(b"[]", level=1)),
            ("empty_max_sort_key", _mutate(lambda d: d["cache_diag"].__setitem__("max_sort_key", []))),
            ("daily_int_bucket", _mutate(lambda d: d["cache_diag"].__setitem__("daily", {"all": 1}))),
            ("daily_int_day", _mutate(lambda d: d["cache_diag"]["daily"]["all"].__setitem__("2026-01-01", 1))),
            ("core_days_str", _mutate(lambda d: d.__setitem__("core_days", "2026-11-22"))),
            ("avail_counts_str", _mutate(lambda d: d.__setitem__("availability_counts", "12345678"))),
            ("avail_counts_bools", _mutate(lambda d: d.__setitem__("availability_counts", [True] * 8))),
            ("private_7d_str", _mutate(lambda d: d.__setitem__("private_7d", "not-a-list"))),
            ("usage_by_day_list", _mutate(lambda d: d["usage_report"].__setitem__("by_day", []))),
            ("usage_hourly_short", _mutate(lambda d: d["usage_report"].__setitem__("hourly", [["2026-01-01"]]))),
        ]

        for label, blob in corrupt_blobs:
            with self.subTest(case=label):
                disk_path.write_bytes(blob)
                mc_mod.runtime_maintenance_cache(app.runtime).clear()
                recovered = manager.maintenance(operator).unwrap()
                self.assertEqual(
                    recovered["usage_sources"]["astrbot_core"]["totals"],
                    baseline["usage_sources"]["astrbot_core"]["totals"],
                )
                self.assertEqual(
                    mc_mod.maintenance_cache_stats(app.runtime)["disk_day_cache_hits"],
                    0,
                )

    async def test_external_sqlite_inplace_edit_invalidates_cold_start_prewarm_and_disk_save(self):
        from companion_v2.model_gateway import maintenance_cache as mc_mod
        from companion_v2.model_gateway.core_observations import record_observation
        from companion_v2.model_gateway.management import ModelManagement
        from companion_v2.storage.runtime import StorageRuntime
        from tests.test_maintenance_perf_cache import _make_observation

        app = self.app
        ctx = self.role_context
        operator = self.core
        manager = ModelManagement(app, ctx)
        mc_mod.clear_maintenance_cache(app.runtime)

        now = app.now()
        obs = _make_observation("ext-hist-1", now - timedelta(days=2))
        obs["requests"][0]["usage"]["tokens"] = 150
        obs["requests"][0]["usage"]["input_tokens"] = 100
        obs["requests"][0]["usage"]["output_tokens"] = 50
        record_observation(app.runtime, ctx, obs)

        first = manager.maintenance(operator).unwrap()
        self.assertEqual(first["usage_sources"]["astrbot_core"]["totals"]["tokens"], 150)

        def _external_inplace_replace(old_tokens: int, new_tokens: int):
            conn = sqlite3.connect(str(app.runtime.database_path))
            try:
                row = conn.execute(
                    "SELECT turn_id, record_json FROM astrbot_core_reply_observations WHERE turn_id = ?",
                    ("ext-hist-1",),
                ).fetchone()
                self.assertIsNotNone(row)
                updated = json.loads(row[1])
                updated["requests"][0]["usage"]["tokens"] = new_tokens
                conn.execute(
                    "UPDATE astrbot_core_reply_observations SET record_json = ? WHERE turn_id = ?",
                    (json.dumps(updated, ensure_ascii=False), "ext-hist-1"),
                )
                conn.commit()
            finally:
                conn.close()

        # Case A: External in-place edit BEFORE first cold start on an open runtime
        # (disk cache already exists from prior run; COUNT/revision/timestamps/length unchanged!).
        fresh_runtime = StorageRuntime.open(data_root=app.runtime.data_root, installation_id=app.runtime.installation_id)
        try:
            _external_inplace_replace(150, 777)
            orig_runtime = app.runtime
            app.runtime = fresh_runtime
            try:
                after_pre_cold = manager.maintenance(operator).unwrap()
                self.assertEqual(after_pre_cold["usage_sources"]["astrbot_core"]["totals"]["tokens"], 777)
                self.assertEqual(mc_mod.maintenance_cache_stats(fresh_runtime)["disk_day_cache_hits"], 0)
            finally:
                app.runtime = orig_runtime
        finally:
            fresh_runtime.close()

        # Sync original runtime cache cleanly and re-seed disk cache at 777.
        mc_mod.clear_maintenance_cache(app.runtime)
        at_777 = manager.maintenance(operator).unwrap()
        self.assertEqual(at_777["usage_sources"]["astrbot_core"]["totals"]["tokens"], 777)

        # Case B: External in-place edit DURING prewarm_core_history() (data_version advances mid-prewarm).
        mc_mod.runtime_maintenance_cache(app.runtime).clear()
        orig_load_disk = mc_mod._load_disk_core_history
        injected = {"done": False}

        def _load_disk_with_mid_edit(*args, **kwargs):
            result = orig_load_disk(*args, **kwargs)
            if not injected["done"]:
                injected["done"] = True
                _external_inplace_replace(777, 9999)
            return result

        with patch.object(mc_mod, "_load_disk_core_history", side_effect=_load_disk_with_mid_edit):
            during_prewarm = manager.maintenance(operator).unwrap()
        self.assertEqual(during_prewarm["usage_sources"]["astrbot_core"]["totals"]["tokens"], 9999)

        # Second call (RAM hit) and simulated restart (disk hit) must both return 9999, never stale 777!
        second_call = manager.maintenance(operator).unwrap()
        self.assertEqual(second_call["usage_sources"]["astrbot_core"]["totals"]["tokens"], 9999)
        mc_mod.runtime_maintenance_cache(app.runtime).clear()
        restarted_call = manager.maintenance(operator).unwrap()
        self.assertEqual(restarted_call["usage_sources"]["astrbot_core"]["totals"]["tokens"], 9999)

        # Case C: save_disk_core_history() refuses to resurrect stale disk cache when clear_epoch
        # or PRAGMA data_version generation has moved on.
        cache = mc_mod.runtime_maintenance_cache(app.runtime)
        stale_epoch = cache.clear_epoch()
        stale_gen = mc_mod.read_shared_db_token(app.runtime).generation
        dummy_entry = next(iter(cache._days.values()))
        mc_mod.clear_maintenance_cache(app.runtime)
        cache_dir = Path(app.runtime.data_root) / mc_mod.DISK_CORE_HISTORY_DIRNAME
        self.assertEqual(list(cache_dir.glob("core-hist-v*.zlib")), [])

        saved_after_clear = mc_mod.save_disk_core_history(
            app.runtime,
            role_key_value=mc_mod.read_core_history_watermark.__name__,
            history_cutoff=app.now().date().isoformat(),
            tz_offset_seconds=0,
            pricing_key=("k",),
            eff_start=app.now() - timedelta(days=6),
            entry=dummy_entry,
            expected_generation=stale_gen,
            expected_clear_epoch=stale_epoch,
        )
        self.assertFalse(saved_after_clear)
        self.assertEqual(list(cache_dir.glob("core-hist-v*.zlib")), [])

        cur_epoch = cache.clear_epoch()
        _external_inplace_replace(9999, 8888)
        saved_after_ext = mc_mod.save_disk_core_history(
            app.runtime,
            role_key_value=mc_mod.read_core_history_watermark.__name__,
            history_cutoff=app.now().date().isoformat(),
            tz_offset_seconds=0,
            pricing_key=("k",),
            eff_start=app.now() - timedelta(days=6),
            entry=dummy_entry,
            expected_generation=stale_gen,
            expected_clear_epoch=cur_epoch,
        )
        self.assertFalse(saved_after_ext)
        self.assertEqual(list(cache_dir.glob("core-hist-v*.zlib")), [])


if __name__ == "__main__":
    unittest.main()
