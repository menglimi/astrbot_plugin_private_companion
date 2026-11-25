"""Regression tests for MainThread SQLite unblocking:

1. ContactSchedule.advance_async offloads tick SQLite queries to a worker
   thread while keeping asyncio.create_task on the event loop, and uses a covering-index
   loose-index-scan for latest private completed native turns.
2. private_scope & scope_family skip legacy json_extract full-table scans once all rows
   carry context_hash (after deduplication), while still detecting newly inserted unhashed rows.
3. ensure_image_access uses a read-only fast path on already-provisioned turns and
   ensure_image_access_async offloads execution off the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest

from tests import astrbot_data_root  # noqa: F401
from astrbot_host.image_access import (
    IMAGE_CAPABILITIES,
    IMAGE_EXTENSION,
    ensure_image_access,
    ensure_image_access_async,
)
from companion_v2.capabilities.models import CapabilityPackageDescriptor
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.native_observations import NativeConversationObservations
from companion_v2.conversation.native_turns import NativeConversationTurns
from companion_v2.conversation.scope_family import private_scope_keys
from companion_v2.execution.models import TaskContext, encode, stamp
from companion_v2.expression.private_scope import _distinct_role_private_contexts
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AudienceRef,
    ScopeKind,
    ScopeRef,
    SubjectKind,
    SubjectRef,
)
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.proactive.scheduling import CONTACT_KIND, ContactSchedule
from companion_v2.storage.native_dedup import (
    clear_dedup_caches,
    dedup_native_storage,
    has_unhashed_context_rows,
)
from companion_v2.storage.runtime import StorageRuntime

INST = "offload-test-inst"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _make_context(idx: int, *, kind: ScopeKind = ScopeKind.PRIVATE, conv_suffix: str = "") -> TaskContext:
    scope = ScopeRef(
        INST,
        "onebot",
        kind,
        f"native-{idx}",
        "bot-1",
        "acct-1",
        f"conv-{idx}{conv_suffix}",
        f"sess-{idx}{conv_suffix}",
    )
    subject = SubjectRef(INST, SubjectKind.HUMAN, f"human-{idx}")
    principal = AuthenticatedHostPrincipal(INST, "host-1", "onebot", f"qq-{idx}", "bot-1", "acct-1")
    actor = ResolvedIdentity(principal, subject, f"bind-{idx}", 1, 1)
    role = SubjectRef(INST, SubjectKind.BOT, "role-1")
    return TaskContext(actor, RoleBinding(scope, role, 1), AudienceRef(scope, (subject,)))


class ProactiveTickOffloadAndCoveringScanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_dedup_caches()
        self.addCleanup(clear_dedup_caches)

    def _build_schedule(self, runtime: StorageRuntime) -> ContactSchedule:
        coord = object.__new__(ContactSchedule)
        coord.runtime = runtime
        coord._closed = False
        coord._advancing = False
        coord._planning = None
        coord._workers = {}
        coord._judges = {}
        coord._maintenance_contexts = {}
        coord._cursor = 0
        coord._native_cursor = 0
        coord._role_cursor = 0
        coord.role_bindings = dict
        coord._diagnostics_cleaned_at = None
        coord._account_available = lambda _ctx: True
        coord.notify = lambda: None
        coord.news_events = NS(maintain=lambda *_a, **_k: None)
        coord.exploration = None
        coord.tasks = NS(
            now=lambda: NOW,
            check_generation=lambda: Outcome.success(1),
            limits=NS(background_slots=3, retained_tasks=10000),
        )
        coord.contacts = NS(
            gateway=NS(router=NS(profiles={CONTACT_KIND: object()})),
            judge_runner=None,
            preferences=NS(read=lambda *_a, **_k: Outcome.failed("none", "none")),
        )
        return coord

    def test_native_rows_uses_covering_index_and_picks_latest_completed_private_turn(self):
        with TemporaryDirectory(prefix="proactive-scan-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id=INST
        ) as runtime:
            turns = NativeConversationTurns(runtime)

            c1 = _make_context(1, kind=ScopeKind.PRIVATE)
            c2 = _make_context(2, kind=ScopeKind.PRIVATE)
            g1 = _make_context(3, kind=ScopeKind.GROUP)

            # Insert older + newer turns for c1, a running turn after completed for c2, and a group turn.
            def add_turn(turn_id, ctx, at, *, complete=False):
                inp = NS(at=at, instruction="role prompt")
                turns.admit(ctx, inp, turn_id, NS(source_id="src-" + turn_id))
                if complete:
                    turns.mark(
                        {"context": ctx, "turn_id": turn_id, "native_input": inp, "instruction": "role prompt"},
                        "completed",
                        at=at + timedelta(seconds=1),
                    )

            add_turn("t1-old", c1, NOW - timedelta(minutes=10), complete=True)
            add_turn("t1-new", c1, NOW - timedelta(minutes=2), complete=True)
            add_turn("t2-old", c2, NOW - timedelta(minutes=5), complete=True)
            add_turn("t2-open", c2, NOW, complete=False)
            add_turn("g1-done", g1, NOW, complete=True)

            coord = self._build_schedule(runtime)
            rows = coord._native_rows(0)
            # Only t1-new qualifies (c2's latest row is still 'prepared'; g1 is 'group').
            self.assertEqual([r["turn_id"] for r in rows], ["t1-new"])
            # Cursor past t1-new yields empty.
            self.assertEqual(coord._native_rows(rows[0]["sequence"]), ())

            # Verify SQLite query plan uses covering index conversation_native_turn_context_recent_idx.
            plan_rows = runtime.read_all(
                """EXPLAIN QUERY PLAN
                WITH RECURSIVE distinct_ctx(context_key) AS (
                    SELECT MIN(context_key) FROM conversation_native_turns
                    UNION ALL
                    SELECT (
                        SELECT MIN(t.context_key) FROM conversation_native_turns t
                        WHERE t.context_key > d.context_key
                    )
                    FROM distinct_ctx d
                    WHERE d.context_key IS NOT NULL
                ),
                latest_ids(latest_rowid) AS (
                    SELECT (
                        SELECT t.rowid FROM conversation_native_turns t
                        WHERE t.context_key = d.context_key
                        ORDER BY t.observed_at DESC, t.rowid DESC
                        LIMIT 1
                    )
                    FROM distinct_ctx d
                    WHERE d.context_key IS NOT NULL
                )
                SELECT n.rowid AS sequence, n.*
                FROM latest_ids l
                JOIN conversation_native_turns n ON n.rowid = l.latest_rowid
                WHERE n.rowid > ? AND n.scope_kind = 'private'
                  AND n.generation_state = 'completed'
                ORDER BY n.rowid ASC LIMIT 16""",
                (0,),
            )
            plan_text = "\n".join(str(r["detail"]) for r in plan_rows)
            self.assertIn("COVERING INDEX conversation_native_turn_context_recent_idx", plan_text)

    async def test_advance_async_offloads_begin_tick_off_event_loop_thread(self):
        with TemporaryDirectory(prefix="proactive-offload-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id=INST
        ) as runtime:
            coord = self._build_schedule(runtime)

            loop_thread = threading.get_ident()
            offload_threads: list[int] = []
            original_offload = coord._begin_tick_offload

            def spy_offload(*args, **kwargs):
                offload_threads.append(threading.get_ident())
                return original_offload(*args, **kwargs)

            coord._begin_tick_offload = spy_offload
            await coord.advance_async()
            self.assertEqual(len(offload_threads), 1)
            self.assertNotEqual(offload_threads[0], loop_thread)

    async def test_advance_async_reads_prefs_off_loop_and_dispatches_maintain_on_event_loop(self):
        with TemporaryDirectory(prefix="proactive-maintain-loop-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id=INST
        ) as runtime:
            coord = self._build_schedule(runtime)
            ctx = _make_context(1, kind=ScopeKind.PRIVATE)
            coord._maintenance_contexts = {ctx.key: ctx}

            loop_thread = threading.get_ident()
            pref_read_threads: list[int] = []
            maintain_threads: list[tuple[str, int]] = []
            spawned_tasks: list[asyncio.Task] = []

            def fake_pref_read(context, *, observed_at):
                pref_read_threads.append(threading.get_ident())
                return Outcome.success(NS(context=context, observed_at=observed_at))

            def fake_news_maintain(context, prefs):
                maintain_threads.append(("news", threading.get_ident()))
                # Must succeed without RuntimeError: no running event loop.
                spawned_tasks.append(asyncio.create_task(asyncio.sleep(0)))

            def fake_exploration_maintain(context, prefs):
                maintain_threads.append(("exploration", threading.get_ident()))
                spawned_tasks.append(asyncio.create_task(asyncio.sleep(0)))

            coord.contacts = NS(
                gateway=NS(router=NS(profiles={CONTACT_KIND: object()})),
                judge_runner=None,
                preferences=NS(read=fake_pref_read),
            )
            coord.news_events = NS(maintain=fake_news_maintain)
            coord.exploration = NS(maintain=fake_exploration_maintain)

            await coord.advance_async()
            if spawned_tasks:
                await asyncio.gather(*spawned_tasks)

            self.assertEqual(len(pref_read_threads), 1)
            self.assertNotEqual(pref_read_threads[0], loop_thread)
            self.assertEqual(maintain_threads, [("news", loop_thread), ("exploration", loop_thread)])


class DedupFallbackSkipTests(unittest.TestCase):
    def setUp(self):
        clear_dedup_caches()
        self.addCleanup(clear_dedup_caches)

    def test_private_scope_and_scope_family_skip_json_extract_after_dedup(self):
        with TemporaryDirectory(prefix="dedup-skip-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id=INST
        ) as runtime:
            turns = NativeConversationTurns(runtime)
            ctx1 = _make_context(1, conv_suffix="-a")
            ctx2 = _make_context(1, conv_suffix="-b")
            turns.admit(ctx1, NS(at=NOW, instruction="sys"), "t-1", NS(source_id="s1"))
            turns.admit(ctx2, NS(at=NOW, instruction="sys"), "t-2", NS(source_id="s2"))
            with runtime.transaction() as conn:
                conn.execute(
                    """INSERT INTO conversation_native_observations
                       (observation_id, context_key, memory_owner, conversation_owner, turn_id, event_id,
                        kind, delivery_state, context_json, source_json, content_hash, observed_at,
                        effective_at, scope_kind, role_binding_key, context_hash)
                       VALUES (?, ?, ?, ?, ?, ?, 'input', 'input_observed', ?, ?, ?, ?, ?, 'private', '', '')""",
                    (
                        "obs-1", ctx1.key, "mem-owner", "conv-owner", "t-1", "ev-1",
                        encode(ctx1), '{"source_id":"s1"}', "h1", stamp(NOW), stamp(NOW),
                    ),
                )
                # Force legacy unhashed turn row to simulate pre-compaction state.
                conn.execute(
                    "UPDATE conversation_native_turns SET context_json = ?, context_hash = '' WHERE turn_id = 't-1'",
                    (encode(ctx1),),
                )

            self.assertTrue(has_unhashed_context_rows(runtime, "conversation_native_turns"))
            self.assertTrue(has_unhashed_context_rows(runtime, "conversation_native_observations", kind="input"))

            # Run lossless dedup -> all rows now carry context_hash.
            dedup_native_storage(runtime)
            self.assertFalse(has_unhashed_context_rows(runtime, "conversation_native_turns"))
            self.assertFalse(has_unhashed_context_rows(runtime, "conversation_native_observations", kind="input"))

            # Spy on runtime.read_all and confirm ZERO json_extract queries hit native tables.
            executed_sqls: list[str] = []
            original_read_all = runtime.read_all

            def spy_read_all(statement, parameters):
                executed_sqls.append(statement)
                return original_read_all(statement, parameters)

            runtime.read_all = spy_read_all
            try:
                found_private = _distinct_role_private_contexts(runtime, ctx1)
                self.assertTrue(any(c.key == ctx1.key for c in found_private))

                family = private_scope_keys(runtime, ctx1.scope, ctx1.role_binding.role)
                self.assertGreaterEqual(len(family), 2)
            finally:
                runtime.read_all = original_read_all

            for sql in executed_sqls:
                if "conversation_native_observations" in sql or "conversation_native_turns" in sql:
                    self.assertNotIn("json_extract(", sql, f"Unexpected fallback scan: {sql}")

            # Inserting a fresh unhashed row invalidates the epoch cache immediately.
            with runtime.transaction() as conn:
                conn.execute(
                    "UPDATE conversation_native_turns SET context_hash = '' WHERE turn_id = 't-2'"
                )
            self.assertTrue(has_unhashed_context_rows(runtime, "conversation_native_turns"))

    def test_unhashed_cache_is_instance_scoped_and_immune_to_id_reuse_aba(self):
        import builtins
        from unittest.mock import patch

        with TemporaryDirectory(prefix="dedup-aba-a-") as dir_a, TemporaryDirectory(prefix="dedup-aba-b-") as dir_b:
            # Prepare dir_b with a pre-existing legacy unhashed row, then close it so reopening starts at epoch (0, 0).
            ctx_b = _make_context(9, conv_suffix="-legacy")
            with StorageRuntime.open(data_root=Path(dir_b), installation_id=INST) as prep_b:
                turns_b = NativeConversationTurns(prep_b)
                turns_b.admit(ctx_b, NS(at=NOW, instruction="sys"), "t-legacy", NS(source_id="sb"))
                with prep_b.transaction() as conn:
                    conn.execute(
                        "UPDATE conversation_native_turns SET context_json = ?, context_hash = '' WHERE turn_id = 't-legacy'",
                        (encode(ctx_b),),
                    )

            # Open runtime_a where every row is hashed, populate its per-instance False cache, then close it.
            ctx_a = _make_context(8, conv_suffix="-clean")
            runtime_a = StorageRuntime.open(data_root=Path(dir_a), installation_id=INST)
            try:
                NativeConversationTurns(runtime_a).admit(ctx_a, NS(at=NOW, instruction="sys"), "t-clean", NS(source_id="sa"))
                self.assertFalse(has_unhashed_context_rows(runtime_a, "conversation_native_turns"))
                self.assertTrue(bool(runtime_a._unhashed_context_cache))
            finally:
                runtime_a.close()
            self.assertEqual(runtime_a._unhashed_context_cache, {})

            # Without calling clear_dedup_caches(), open runtime_b at epoch (0, 0) and simulate identical id() reuse.
            with StorageRuntime.open(data_root=Path(dir_b), installation_id=INST) as runtime_b:
                with patch.object(builtins, "id", return_value=0xDEADBEEF):
                    self.assertTrue(has_unhashed_context_rows(runtime_b, "conversation_native_turns"))

                # After deduplicating runtime_b, cache reflects False and clear_dedup_caches() invalidates cleanly.
                dedup_native_storage(runtime_b)
                self.assertFalse(has_unhashed_context_rows(runtime_b, "conversation_native_turns"))
                clear_dedup_caches()
                self.assertFalse(has_unhashed_context_rows(runtime_b, "conversation_native_turns"))


class ImageAccessAsyncAndFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_fast_path_skips_write_transaction_and_async_offloads_thread(self):
        with TemporaryDirectory(prefix="img-access-") as tmp, StorageRuntime.open(
            data_root=Path(tmp), installation_id=INST
        ) as runtime:
            ctx = _make_context(1, kind=ScopeKind.PRIVATE)

            # Register active image extension installation so ensure_image_access provisions grants.
            repo = CapabilityRepository(runtime)
            pkg = CapabilityPackageDescriptor(
                IMAGE_EXTENSION, "native-image", "companion", "1", "image-hash", IMAGE_CAPABILITIES,
            )
            repo.register_descriptor(pkg, registered_at=NOW).unwrap()
            repo.acquire(
                extension_id=IMAGE_EXTENSION,
                package_id=pkg.package_id,
                package_version=pkg.package_version,
                expected_revision=0,
                observed_at=NOW,
            ).unwrap()

            # First call provisions missing grants off the event loop via ensure_image_access_async.
            loop_thread = threading.get_ident()
            tx_threads: list[int] = []
            original_tx = runtime.transaction

            def spy_tx(*args, **kwargs):
                tx_threads.append(threading.get_ident())
                return original_tx(*args, **kwargs)

            runtime.transaction = spy_tx
            try:
                await ensure_image_access_async(runtime, ctx, observed_at=NOW)
                self.assertGreaterEqual(len(tx_threads), 1)
                self.assertTrue(all(tid != loop_thread for tid in tx_threads))

                # Second call sees everything already provisioned -> ZERO write transactions!
                tx_threads.clear()
                ensure_image_access(runtime, ctx, observed_at=NOW)
                self.assertEqual(tx_threads, [])
            finally:
                runtime.transaction = original_tx


if __name__ == "__main__":
    unittest.main()
