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
    def test_memory_adaptive_pragmas_cover_low_mid_and_high_ram_hosts(self):
        cases = (
            (1 * 1024**3, "low", 2, -8192, 0),
            (4 * 1024**3, "medium", 3, -16384, 64 * 1024 * 1024),
            (16 * 1024**3, "high", 4, -16384, 128 * 1024 * 1024),
        )
        for mem_bytes, tier, pool_size, cache_size, mmap_size in cases:
            with self.subTest(tier=tier), TemporaryDirectory(prefix=f"pr02-ram-{tier}-") as tmp:
                with patch.object(sqlite_tuning, "detect_total_memory_bytes", return_value=mem_bytes):
                    runtime = StorageRuntime.open(data_root=Path(tmp), installation_id=f"ram-{tier}")
                try:
                    self.assertEqual(runtime.tuning_profile.tier, tier)
                    self.assertEqual(runtime.reader_pool_size, pool_size)
                    # Check writer connection PRAGMAs.
                    with runtime.transaction() as writer_conn:
                        self.assertEqual(writer_conn.execute("PRAGMA cache_size").fetchone()[0], cache_size)
                        self.assertEqual(writer_conn.execute("PRAGMA mmap_size").fetchone()[0], mmap_size)
                    # Check pooled reader connection PRAGMAs.
                    with runtime.read_snapshot() as reader_conn:
                        self.assertEqual(reader_conn.execute("PRAGMA cache_size").fetchone()[0], cache_size)
                        self.assertEqual(reader_conn.execute("PRAGMA mmap_size").fetchone()[0], mmap_size)
                        self.assertEqual(reader_conn.execute("PRAGMA query_only").fetchone()[0], 1)
                finally:
                    runtime.close()

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


if __name__ == "__main__":
    unittest.main()
