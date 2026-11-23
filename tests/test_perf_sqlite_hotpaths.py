"""Regression tests for SQLite hotpath indexes, cache decoupling, bounded state DAG, and tick safety."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from io import BytesIO
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from companion_v2.conversation.media import native_generated_album
from companion_v2.conversation.native_observations import NativeConversationObservations
from companion_v2.conversation.native_tool_results import (
    PREFIX as TOOL_RESULT_PREFIX,
    PREFIX_END as TOOL_RESULT_PREFIX_END,
)
from companion_v2.conversation.native_turns import NativeConversationTurns, latest_role_contexts
from companion_v2.conversation.scope_family import private_scope_keys
from companion_v2.execution.models import TaskContext, digest, encode
from companion_v2.expression.private_scope import private_people, role_private_contexts
from companion_v2.expression.style_service import (
    KIND as EXPRESSION_LEARN_KIND,
    ExpressionStyleService,
)
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.host import tick_budget
from companion_v2.host.tick_budget import TickScheduler, TickUnit
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity, RoleBinding
from companion_v2.model_gateway.core_observations import record_observation
from companion_v2.model_gateway.maintenance_cache import (
    clear_maintenance_cache,
    maintenance_cache_stats,
)
from companion_v2.model_gateway.management import ModelManagement
from companion_v2.model_gateway.pricing import PricingSettingsStore
from companion_v2.model_gateway.pricing_catalog import PriceCatalogStore
from companion_v2.references.management import (
    REFERENCE_THUMBNAIL_BYTES,
    ReferenceMaterialManagement,
    _THUMBNAIL_CACHE,
)
from companion_v2.role_work.state.service import MAX_ROLE_STATE_ANCESTOR_DEPTH
from companion_v2.storage.codec import encode_timestamp
from companion_v2.storage.runtime import StorageRuntime
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_state import StateFixture, calendar_data, state_data


def _query_plan_lines(runtime: StorageRuntime, sql: str, params: tuple = ()) -> list[str]:
    rows = runtime.read_all("EXPLAIN QUERY PLAN " + sql, params)
    return [str(row["detail"]) for row in rows]


class StorageAndIndexHotpathTests(unittest.TestCase):
    def test_storage_runtime_uses_wal_normal_sync_and_execution_tasks_covering_index(self):
        with TemporaryDirectory(prefix="companion-perf-idx-") as tmp:
            root = Path(tmp)
            runtime = StorageRuntime.open(data_root=root, installation_id="perf-install")
            try:
                self.assertEqual(runtime.read_one("PRAGMA journal_mode", ())[0].lower(), "wal")
                self.assertEqual(runtime.read_one("PRAGMA synchronous", ())[0], 1)

                plan = _query_plan_lines(
                    runtime,
                    """SELECT task_id, state, record_json FROM execution_tasks
                       WHERE origin_key = ? AND kind = ? ORDER BY task_id DESC LIMIT 1""",
                    ("origin-a", "role.journal.write"),
                )
                joined = "\n".join(plan)
                self.assertIn("idx_execution_tasks_origin_kind_id", joined, joined)
                self.assertNotIn("SCAN execution_tasks", joined, joined)
                self.assertNotIn("USE TEMP B-TREE", joined, joined)

                # Simulate reopening an older DB that lacks the runtime performance index.
                with runtime.transaction() as connection:
                    connection.execute("DROP INDEX idx_execution_tasks_origin_kind_id")
            finally:
                runtime.close()

            reopened = StorageRuntime.open(data_root=root, installation_id="perf-install")
            try:
                idx = reopened.read_one(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                    ("idx_execution_tasks_origin_kind_id",),
                )
                self.assertIsNotNone(idx)
                self.assertEqual(reopened.read_one("PRAGMA synchronous", ())[0], 1)
            finally:
                reopened.close()

    def test_runtime_metadata_prefix_range_uses_primary_key_index_without_scan(self):
        with TemporaryDirectory(prefix="companion-perf-meta-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp), installation_id="perf-meta")
            try:
                with runtime.transaction() as connection:
                    connection.execute(
                        "INSERT INTO runtime_metadata (key, value) VALUES (?, ?)",
                        (TOOL_RESULT_PREFIX + "op-1", json.dumps({"ok": True})),
                    )
                    connection.execute(
                        "INSERT INTO runtime_metadata (key, value) VALUES (?, ?)",
                        (TOOL_RESULT_PREFIX_END + "outside", json.dumps({"ok": False})),
                    )
                    connection.execute(
                        "INSERT INTO runtime_metadata (key, value) VALUES (?, ?)",
                        ("unrelated.key", json.dumps({"ok": False})),
                    )

                matched = runtime.read_all(
                    "SELECT key, value FROM runtime_metadata WHERE key >= ? AND key < ? ORDER BY key ASC",
                    (TOOL_RESULT_PREFIX, TOOL_RESULT_PREFIX_END),
                )
                self.assertEqual([row["key"] for row in matched], [TOOL_RESULT_PREFIX + "op-1"])

                plan = _query_plan_lines(
                    runtime,
                    """SELECT rowid AS scan_id, json_extract(value, '$.invocation_id') AS invocation_id
                       FROM runtime_metadata
                       WHERE key >= ? AND key < ? AND rowid > ?
                         AND json_extract(value, '$.origin_key') = ?
                         AND json_extract(value, '$.action') IN (?)
                         AND json_extract(value, '$.task_id') IS NOT NULL
                       ORDER BY rowid ASC LIMIT ?""",
                    (TOOL_RESULT_PREFIX, TOOL_RESULT_PREFIX_END, 0, "origin-1", "image.generate", 32),
                )
                joined = "\n".join(plan)
                self.assertIn("SEARCH runtime_metadata", joined, joined)
                self.assertNotIn("SCAN runtime_metadata", joined, joined)

                album_plan = _query_plan_lines(
                    runtime,
                    """SELECT m.key, m.value FROM runtime_metadata m
                       WHERE m.key >= ? AND m.key < ?""",
                    (TOOL_RESULT_PREFIX, TOOL_RESULT_PREFIX_END),
                )
                album_joined = "\n".join(album_plan)
                self.assertIn("SEARCH m", album_joined, album_joined)
                self.assertNotIn("SCAN m", album_joined, album_joined)
            finally:
                runtime.close()


class AlbumAndThumbnailCacheTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_native_album_origin_keys_computed_once_per_page_and_uses_loose_index_scan(self):
        host = self.host()
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        runtime = app.runtime
        NativeConversationObservations(app.messages)
        plan = _query_plan_lines(
            runtime,
            """WITH RECURSIVE keys(k) AS (
                   SELECT MIN(context_key) FROM conversation_native_turns
                   UNION ALL
                   SELECT (SELECT MIN(t.context_key) FROM conversation_native_turns t WHERE t.context_key > keys.k)
                   FROM keys WHERE keys.k IS NOT NULL
               )
               SELECT n.context_key, n.context_json
               FROM conversation_native_turns n
               WHERE n.rowid IN (
                   SELECT (SELECT t.rowid FROM conversation_native_turns t
                           WHERE t.context_key = keys.k
                           ORDER BY t.observed_at DESC, t.rowid DESC LIMIT 1)
                   FROM keys WHERE keys.k IS NOT NULL
               )""",
        )
        joined = "\n".join(plan)
        self.assertIn("conversation_native_turn_context_recent_idx", joined, joined)

        with patch.object(
            native_generated_album, "_origin_keys", wraps=native_generated_album._origin_keys
        ) as spy:
            items, _has_more = native_generated_album.page(app.messages.generated_images, context, NOW)
            self.assertEqual(items, [])
            self.assertEqual(spy.call_count, 1)

    async def test_reference_thumbnail_cache_hits_and_invalidates_on_content_change(self):
        buf1 = BytesIO()
        Image.new("RGB", (64, 64), "#336699").save(buf1, format="PNG")
        raw1 = buf1.getvalue()

        buf2 = BytesIO()
        Image.new("RGB", (64, 64), "#993333").save(buf2, format="PNG")
        raw2 = buf2.getvalue()

        _THUMBNAIL_CACHE.clear()
        with patch("companion_v2.references.management.Image.open", wraps=Image.open) as open_spy:
            first = ReferenceMaterialManagement._thumbnail(raw1, byte_limit=REFERENCE_THUMBNAIL_BYTES)
            second = ReferenceMaterialManagement._thumbnail(raw1, byte_limit=REFERENCE_THUMBNAIL_BYTES)
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("data:image/"))
            self.assertEqual(open_spy.call_count, 1)

            third = ReferenceMaterialManagement._thumbnail(raw2, byte_limit=REFERENCE_THUMBNAIL_BYTES)
            self.assertNotEqual(first, third)
            self.assertEqual(open_spy.call_count, 2)


class MaintenancePricingCacheDecouplingTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_pricing_and_fx_refresh_reuses_raw_day_cache_without_rescanning_history(self):
        host = self.host()
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        runtime = app.runtime
        clear_maintenance_cache(runtime)

        pricing_store = PricingSettingsStore(runtime.data_root)
        initial_settings = pricing_store.read_safe()
        pricing_store.update(
            initial_settings["revision"],
            {
                "display_currency": "CNY",
                "use_builtin_presets": True,
                "exchange_rate": {"mode": "manual", "manual_usd_cny": 7.0},
            },
        )

        # Insert 3 historical-day rows (before today's cutoff) and 1 today row.
        for day_offset in (3, 2, 1, 0):
            ts = NOW - timedelta(days=day_offset, hours=1)
            sig = {
                "version": 5,
                "model_hash": digest("gpt-4o"),
                "system_hash": digest("sys"),
                "system_parts": [digest("sys")],
                "tools_hash": digest("tools"),
                "tool_parts": [digest("tool-1")],
                "context_sequence_hash": digest("ctx"),
                "context_multiset_hash": digest("ctx"),
                "context_hashes": [digest("ctx-1"), digest("ctx-2")],
                "context_material_count": 2,
                "context_chars": 200,
                "context_bytes": 200,
                "context_observed": True,
                "estimates_observed": True,
                "context_est_tokens": 50,
                "system_est_tokens": 50,
                "tool_est_tokens": 20,
                "message_estimates_observed": True,
                "message_est_tokens": 50,
                "message_chars": 100,
                "message_bytes": 100,
                "request_estimate_observed": True,
                "request_est_tokens": 170,
                "context_availability_observed": True,
                "context_availability_hash": digest({"deferred": [], "unavailable": []}),
                "deferred_material_count": 0,
                "unavailable_material_count": 0,
                "request_stats_observed": True,
                "message_count": 2,
                "system_message_count": 1,
                "system_chars": 100,
                "system_bytes": 100,
                "tool_count": 1,
                "tool_bytes": 64,
                "image_count": 0,
                "audio_count": 0,
                "video_count": 0,
            }
            obs = {
                "version": 1,
                "revision": 1,
                "turn_id": f"turn-day-{day_offset}",
                "event_id": f"evt-day-{day_offset}",
                "execution_path": "astrbot_core",
                "received_at": ts.isoformat(),
                "observed_at": (ts + timedelta(seconds=3)).isoformat(),
                "generation_state": "completed",
                "generation_finished_at": (ts + timedelta(seconds=3)).isoformat(),
                "request_observation_available": True,
                "request_observation_complete": True,
                "tool_requery_observed": False,
                "request_hook_signature": sig,
                "requests": [
                    {
                        "position": 1,
                        "state": "succeeded",
                        "provider": "core-provider-a",
                        "started_at": (ts + timedelta(seconds=1)).isoformat(),
                        "finished_at": (ts + timedelta(seconds=3)).isoformat(),
                        "latency_ms": 2000,
                        "measurement_basis": "provider_response",
                        "usage": {
                            "input_tokens": 1_000_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 500_000,
                            "tokens": 1_500_000,
                        },
                        "signature": sig,
                    }
                ],
            }
            record_observation(runtime, context, obs)

        manager = ModelManagement(app, context)
        provider_meta = {
            "core-provider-a": {
                "id": "core-provider-a",
                "model": "gpt-4o",
                "provider_type": "openai",
                "cluster_id": "cluster-a",
            }
        }
        first = manager.maintenance(self.core, provider_metadata=provider_meta).unwrap()
        stats_after_cold = maintenance_cache_stats(runtime)
        self.assertEqual(stats_after_cold["raw_day_cache_misses"], 1)
        self.assertEqual(stats_after_cold["day_cache_misses"], 1)
        self.assertAlmostEqual(first["pricing"]["usd_cny_rate"], 7.0, places=4)
        first_cost_microusd = first["totals"]["cost_microusd"]
        self.assertGreater(first_cost_microusd, 0)

        # Refresh FX rate (7.0 -> 14.0) and cluster multiplier (1.0 -> 2.0) WITHOUT touching SQLite observations.
        updated_settings = pricing_store.read_safe()
        pricing_store.update(
            updated_settings["revision"],
            {
                "exchange_rate": {"mode": "manual", "manual_usd_cny": 14.0},
                "pricing_multipliers": {"cluster-a": 2.0},
            },
        )

        historical_scans = []
        original_read_all = runtime.read_all

        def spying_read_all(sql, params=()):
            if "astrbot_core_reply_observations" in sql and "received_at < ?" in sql:
                historical_scans.append((sql, params))
            return original_read_all(sql, params)

        with patch.object(runtime, "read_all", side_effect=spying_read_all):
            second = manager.maintenance(self.core, provider_metadata=provider_meta).unwrap()

        stats_after_fx = maintenance_cache_stats(runtime)
        self.assertEqual(historical_scans, [], "FX/pricing refresh must not rescan historical SQLite observations")
        self.assertEqual(stats_after_fx["raw_day_cache_hits"], stats_after_cold["raw_day_cache_hits"] + 1)
        self.assertEqual(stats_after_fx["raw_day_cache_misses"], stats_after_cold["raw_day_cache_misses"])
        self.assertAlmostEqual(second["pricing"]["usd_cny_rate"], 14.0, places=4)
        self.assertAlmostEqual(second["totals"]["cost_microusd"], first_cost_microusd * 2.0, delta=10)


class RoleStateBoundedChainAndCalendarDedupTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_role_state_dependency_chain_stays_bounded_and_preserves_recent_revocation(self):
        _host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, hunger_enabled=True, health_enabled=True)

        recent_evidence = None
        for idx in range(25):
            self.clock.advance(timedelta(minutes=10))
            ev = self.source(app, context, identifier=f"msg-ev-{idx}")
            if idx == 20:
                recent_evidence = ev
            current = app.role_state.records.get(context, "current")
            rev = current["revision"] if current else 0
            deps = (ev,)
            if current and current.get("sources"):
                deps = (current["sources"][0], ev)
            app.role_state.write(
                context,
                data=state_data(energy=max(10, 90 - idx), hunger="平稳"),
                expected_revision=rev,
                operation_key=f"chained-state-{idx}",
                sources=deps,
                operator=self.core,
                reason="human_edit",
            )

        latest = app.role_state.records.get(context, "current")
        latest_ref = latest["sources"][0]
        rows = app.runtime.read_all(
            """WITH RECURSIVE walk(id, lineage, rev, depth) AS (
                   VALUES (?, ?, ?, 0)
                   UNION ALL
                   SELECT d.input_id, d.input_lineage, d.input_revision, w.depth + 1
                   FROM source_dependencies d
                   JOIN walk w
                     ON d.dependent_id = w.id
                    AND d.dependent_lineage = w.lineage
                    AND d.dependent_revision = w.rev
                   WHERE w.depth < 100
               )
               SELECT MAX(w.depth) AS max_depth,
                      COUNT(DISTINCT w.id || ':' || w.lineage || ':' || w.rev) AS node_count,
                      MAX(CASE WHEN s.owner IN ('role.state', 'role.state.cycle') THEN w.depth ELSE 0 END) AS max_state_depth
               FROM walk w
               LEFT JOIN sources s
                 ON s.source_id = w.id AND s.lineage_id = w.lineage AND s.source_revision = w.rev""",
            (latest_ref["source_id"], latest_ref["version"]["lineage_id"], latest_ref["version"]["revision"]),
        )
        stats = rows[0]
        self.assertLessEqual(stats["max_state_depth"], MAX_ROLE_STATE_ANCESTOR_DEPTH)
        self.assertLessEqual(stats["max_depth"], MAX_ROLE_STATE_ANCESTOR_DEPTH + 1)
        self.assertLess(stats["node_count"], 40)

        # Revoking a recent evidence source retained by the checkpoint still invalidates current state.
        self.assertIsNotNone(recent_evidence)
        self.revoke_source(app, recent_evidence)
        with self.assertRaises(Exception):
            app.role_state.read(context, operator=self.core, at=self.clock.now())

    async def test_state_status_deduplicates_calendar_evidence_across_inventory_and_occurrences(self):
        _host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, calendar_enabled=True)
        shared_source = self.source(app, context, identifier="cal-shared-ev")

        for idx in range(4):
            before = manager.status(self.core).unwrap()
            manager.command(
                self.core,
                dict(
                    action="calendar.create",
                    operation_key=f"cal-create-{idx}",
                    view_token=before["create_token"],
                    expected_revision=0,
                    id=f"evt-{idx}",
                    data=calendar_data(title=f"事件 {idx}"),
                    sources=(shared_source,),
                ),
            ).unwrap()

        with patch.object(
            app.calendar.access.sources,
            "validate_reference_for_audience",
            wraps=app.calendar.access.sources.validate_reference_for_audience,
        ) as validate_spy, patch.object(
            app.calendar.access,
            "check",
            wraps=app.calendar.access.check,
        ) as check_spy:
            status = manager.status(self.core).unwrap()
            self.assertEqual(len(status["calendar"]["items"]), 4)
            self.assertGreaterEqual(len(status["occurrences"]["items"]), 4)
            # 4 events * 2 calls (inventory + occurrences) = 8 validations previously; now deduplicated to 1.
            self.assertEqual(validate_spy.call_count, 1)
            # calendar.read access check runs once across inventory + occurrences within the batch.
            self.assertEqual(check_spy.call_count, 1)


class TickBudgetAndPrivateScopeTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_tick_scheduler_offload_does_not_block_event_loop_and_warns_on_slow_sync_unit(self):
        entered = threading.Event()
        release = threading.Event()
        loop_ticks = 0

        def blocking_unit():
            entered.set()
            if not release.wait(5):
                raise AssertionError("offloaded unit was not released")
            return "offloaded-ok"

        scheduler = TickScheduler((TickUnit("heavy-io", blocking_unit, offload=True),), budget_seconds=1.0)
        task = asyncio.create_task(scheduler.advance())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
            # While the offloaded unit is blocked on a worker thread, the event loop stays responsive.
            for _ in range(3):
                await asyncio.sleep(0.005)
                loop_ticks += 1
            self.assertEqual(loop_ticks, 3)
            self.assertFalse(task.done())
        finally:
            release.set()
        report = await task
        self.assertTrue(report.completed)
        self.assertEqual(report.results["heavy-io"], "offloaded-ok")

        # Slow synchronous unit logs a rate-limited warning.
        def slow_sync():
            time.sleep(0.03)
            return "slow"

        sync_scheduler = TickScheduler((TickUnit("slow-sync", slow_sync),), budget_seconds=1.0)
        with patch.object(tick_budget, "SLOW_UNIT_WARNING_SECONDS", 0.01), \
                patch.object(tick_budget, "SLOW_UNIT_LOG_INTERVAL_SECONDS", 60.0), \
                self.assertLogs(tick_budget.logger, level=logging.WARNING) as captured:
            await sync_scheduler.advance()
            await sync_scheduler.advance()
        self.assertEqual(len(captured.output), 1)
        self.assertIn("slow-sync", captured.output[0])

    async def test_private_scope_deduplicates_before_json_decode_and_scans_once(self):
        host = self.host()
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        runtime = app.runtime
        ctx_json = encode(context)
        with runtime.transaction() as connection:
            for idx in range(30):
                connection.execute(
                    """INSERT INTO conversation_batches (
                           batch_id, origin_key, context_json, role_instruction,
                           state, revision, created_at, contributions_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"batch-{idx}",
                        context.key,
                        ctx_json,
                        "role prompt",
                        "ready",
                        1,
                        encode_timestamp(NOW - timedelta(minutes=idx)),
                        "[]",
                    ),
                )

        with patch.object(TaskContext, "restore", wraps=TaskContext.restore) as restore_spy:
            people = private_people(runtime, app.tasks.identity, context, NOW)
            self.assertEqual(len(people), 1)
            self.assertEqual(restore_spy.call_count, 1)

        with patch.object(TaskContext, "restore", wraps=TaskContext.restore) as restore_spy:
            contexts = role_private_contexts(runtime, app.tasks.identity, app.messages, context, NOW)
            self.assertLessEqual(restore_spy.call_count, 1)
            self.assertTrue(all(item.key == context.key for item in contexts))

    async def test_expression_style_not_due_fast_path_skips_active_signal_count_and_write_tx(self):
        host = self.host()
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        service = app.expression_style
        service._advance_cursor(service._cursor_key(context), NOW)
        check_at = NOW + timedelta(minutes=10)

        with patch.object(service, "_active_signal_count", wraps=service._active_signal_count) as count_spy, \
                patch.object(service, "prepare", wraps=service.prepare) as prepare_spy, \
                patch.object(app.runtime, "transaction", wraps=app.runtime.transaction) as tx_spy:
            started = service._scan(context, "role prompt", check_at)

        self.assertFalse(started)
        self.assertEqual(count_spy.call_count, 0)
        self.assertEqual(prepare_spy.call_count, 0)
        self.assertEqual(tx_spy.call_count, 0)
        self.assertNotIn(context.key, service.next_checks)

    async def test_expression_style_unchanged_empty_window_skips_repeat_prepare_until_new_input(self):
        host = self.host()
        app = host.application
        app.tasks.start(expected_generation=0).unwrap()
        app.state = "running"
        NativeConversationObservations(app.messages)
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        with app.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO conversation_batches (
                       batch_id, origin_key, context_json, role_instruction,
                       state, revision, created_at, contributions_json
                   ) VALUES (?, ?, ?, ?, 'ready', 1, ?, '[]')""",
                ("batch-empty-1", context.key, encode(context), "role prompt", encode_timestamp(NOW)),
            )
        app.state_maintenance.bindings[context.key] = (context, "role prompt", None)
        service = app.expression_style
        service.gateway = type("G", (), {"router": type("R", (), {"profiles": {EXPRESSION_LEARN_KIND: object()}})()})()

        original_prepare = ExpressionStyleService.prepare
        prepare_calls = []

        def tracked_prepare(self_obj, ctx, instruction, at):
            prepare_calls.append(ctx.key)
            return original_prepare(self_obj, ctx, instruction, at)

        with patch.object(ExpressionStyleService, "prepare", autospec=True, side_effect=tracked_prepare):
            # Pass 1: prepares once and records insufficient_samples without mutating next_checks.
            service._next_discovery_at = None
            service.advance()
            self.assertEqual(len(prepare_calls), 1)
            self.assertEqual(service.results[service._work_key(context)]["state"], "insufficient_samples")
            self.assertNotIn(context.key, service.next_checks)

            # Pass 2: unchanged window watermark skips prepare completely.
            self.clock.advance(timedelta(seconds=61))
            service.advance()
            self.assertEqual(len(prepare_calls), 1)
            self.assertNotIn(context.key, service.next_checks)

            # Pass 3: a new input observation invalidates the watermark immediately.
            with app.runtime.transaction() as connection:
                connection.execute(
                    """INSERT INTO conversation_native_observations (
                           observation_id, context_key, memory_owner, conversation_owner,
                           turn_id, event_id, kind, delivery_state, context_json,
                           source_json, content_hash, observed_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'input', 'input_observed', ?, '{}', 'h', ?)""",
                    (
                        "obs-new-1",
                        context.key,
                        "mem-owner",
                        "conv-owner",
                        "turn-new-1",
                        "evt-new-1",
                        encode(context),
                        encode_timestamp(self.clock.now()),
                    ),
                )
            self.clock.advance(timedelta(seconds=61))
            service.advance()
            self.assertEqual(len(prepare_calls), 2)

            # Approving a sample with a lexicographically smaller content_hash while a
            # larger-hash sample remains pending must still invalidate review_marker immediately.
            review_policy = type(
                "ReviewPolicy",
                (),
                {
                    "private_learning_enabled": True,
                    "group_learning_enabled": False,
                    "window_hours": 720,
                    "sample_limit": 25,
                    "min_samples": 2,
                    "group_min_samples": 20,
                    "sample_chars": 300,
                    "sample_review_enabled": True,
                    "group_new_messages": 20,
                    "group_member_mode": "all",
                    "group_member_ids": "",
                },
            )()
            reviews = service.samples.reviews
            reviews.write(
                context,
                {
                    "handle": "input:sample-high",
                    "content_hash": "ffff" + "0" * 60,
                    "state": "pending",
                    "revision": 1,
                    "seen_at": encode_timestamp(self.clock.now()),
                    "decided_at": None,
                    "decided_by": None,
                    "decision_key": None,
                },
            )
            sample_low = {
                "handle": "input:sample-low",
                "content_hash": "0001" + "0" * 60,
                "state": "pending",
                "revision": 1,
                "seen_at": encode_timestamp(self.clock.now()),
                "decided_at": None,
                "decided_by": None,
                "decision_key": None,
            }
            reviews.write(context, sample_low)
            before_wm = service._empty_prepare_watermark(context, review_policy, self.clock.now())
            self.assertIsNotNone(before_wm)
            self.clock.advance(timedelta(seconds=5))
            reviews.write(
                context,
                {
                    **sample_low,
                    "state": "approved",
                    "revision": 2,
                    "decided_at": encode_timestamp(self.clock.now()),
                    "decided_by": "operator",
                    "decision_key": "dec-low-1",
                },
            )
            after_wm = service._empty_prepare_watermark(context, review_policy, self.clock.now())
            self.assertNotEqual(before_wm, after_wm)

            # Even when sample_review_enabled=False, rejecting or restoring a sample
            # changes eligible() results and must invalidate the empty-prepare watermark.
            no_review_policy = type(
                "NoReviewPolicy",
                (),
                {
                    "private_learning_enabled": True,
                    "group_learning_enabled": False,
                    "window_hours": 720,
                    "sample_limit": 25,
                    "min_samples": 2,
                    "group_min_samples": 20,
                    "sample_chars": 300,
                    "sample_review_enabled": False,
                    "group_new_messages": 20,
                    "group_member_mode": "all",
                    "group_member_ids": "",
                },
            )()
            wm_before_reject = service._empty_prepare_watermark(context, no_review_policy, self.clock.now())
            self.assertIsNotNone(wm_before_reject)
            self.clock.advance(timedelta(seconds=5))
            reviews.write(
                context,
                {
                    **sample_low,
                    "state": "rejected",
                    "revision": 3,
                    "decided_at": encode_timestamp(self.clock.now()),
                    "decided_by": "operator",
                    "decision_key": "dec-low-reject",
                },
            )
            wm_after_reject = service._empty_prepare_watermark(context, no_review_policy, self.clock.now())
            self.assertNotEqual(wm_before_reject, wm_after_reject)

    async def test_expression_style_advance_async_offloads_scan_off_event_loop(self):
        host = self.host()
        app = host.application
        app.state = "running"
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        app.state_maintenance.bindings[context.key] = (context, "role prompt", None)
        service = app.expression_style
        service.gateway = type("G", (), {"router": type("R", (), {"profiles": {EXPRESSION_LEARN_KIND: object()}})()})()
        main_thread_id = threading.get_ident()
        scan_thread_ids = []
        entered = threading.Event()
        release = threading.Event()

        def blocking_group_candidates(at, limit=8):
            scan_thread_ids.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise AssertionError("offloaded expression scan was not released")
            return ()

        async def fake_run(ctx, instruction):
            return {"state": "succeeded"}

        fake_job = {
            "context": context,
            "instruction": "role prompt",
            "at": NOW,
            "policy": type("P", (), {"learning_mode": "balanced"})(),
            "material_hash": "mat-async-1",
        }
        loop_ticks = 0
        with patch.object(service, "_group_candidates", side_effect=blocking_group_candidates), \
                patch.object(service, "prepare", return_value=fake_job), \
                patch.object(service, "run", side_effect=fake_run):
            service._next_discovery_at = None
            task = asyncio.create_task(service.advance_async())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                for _ in range(3):
                    await asyncio.sleep(0.005)
                    loop_ticks += 1
                self.assertEqual(loop_ticks, 3)
                self.assertFalse(task.done())
            finally:
                release.set()
            await task

        self.assertEqual(len(scan_thread_ids), 1)
        self.assertNotEqual(scan_thread_ids[0], main_thread_id)
        self.assertIn(service._work_key(context), service.workers)
        await asyncio.gather(*tuple(service.workers.values()), return_exceptions=True)

    async def test_application_and_expression_close_drain_inflight_threads_before_closing_runtime(self):
        host = self.host()
        app = host.application
        app.state = "running"
        app.owns_runtime = True
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        app.state_maintenance.bindings[context.key] = (context, "role prompt", None)
        service = app.expression_style
        service.gateway = type("G", (), {"router": type("R", (), {"profiles": {EXPRESSION_LEARN_KIND: object()}})()})()

        events = []
        expr_entered = threading.Event()
        expr_release = threading.Event()
        reclaim_entered = threading.Event()
        reclaim_release = threading.Event()

        def blocking_expr_scan(bindings, at, *, defer_launch=False):
            expr_entered.set()
            expr_release.wait(5)
            events.append("expr_done")
            return ()

        def blocking_reclaim(tasks):
            reclaim_entered.set()
            reclaim_release.wait(5)
            events.append("reclaim_done")

        orig_close = app.runtime.close

        def tracked_runtime_close():
            events.append("runtime_closed")
            return orig_close()

        with patch.object(service, "_discover_and_scan_sync", side_effect=blocking_expr_scan), \
                patch.object(app.gateway.ledger, "reclaim_expired", side_effect=blocking_reclaim), \
                patch.object(app.runtime, "close", side_effect=tracked_runtime_close):
            service._next_discovery_at = None
            t_expr = asyncio.create_task(service.advance_async())
            t_reclaim = asyncio.create_task(app._reclaim_expired_reservations())
            self.assertTrue(await asyncio.to_thread(expr_entered.wait, 5))
            self.assertTrue(await asyncio.to_thread(reclaim_entered.wait, 5))

            # Cancel callers (simulating _driver cancellation) and immediately call app.close().
            t_expr.cancel()
            t_reclaim.cancel()
            await asyncio.gather(t_expr, t_reclaim, return_exceptions=True)

            close_task = asyncio.create_task(app.close())
            await asyncio.sleep(0.03)
            self.assertFalse(close_task.done(), "app.close() must wait for in-flight worker threads")
            self.assertNotIn("runtime_closed", events)

            expr_release.set()
            reclaim_release.set()
            await asyncio.wait_for(close_task, timeout=5.0)

        self.assertIn("expr_done", events)
        self.assertIn("reclaim_done", events)
        self.assertIn("runtime_closed", events)
        self.assertLess(events.index("expr_done"), events.index("runtime_closed"))
        self.assertLess(events.index("reclaim_done"), events.index("runtime_closed"))

    async def test_group_candidates_uses_partial_group_indexes_and_scope_family_deduplicates_keys(self):
        host = self.host()
        app = host.application
        runtime = app.runtime
        NativeConversationTurns(runtime)

        priv_ctx = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        group_scope = ScopeRef(
            SCOPE.installation_id, SCOPE.platform, ScopeKind.GROUP, "group-9001",
            SCOPE.bot_id, SCOPE.account_id, "group-conv-1", "group-sess-1",
        )
        group_actor = ResolvedIdentity(
            AuthenticatedHostPrincipal(
                SCOPE.installation_id, "host-a", SCOPE.platform, "member-1", SCOPE.bot_id, SCOPE.account_id,
            ),
            SubjectRef(SCOPE.installation_id, SubjectKind.HUMAN, "human-group-1"),
            "binding-g1", 1, 1,
        )
        group_ctx = TaskContext(
            group_actor,
            RoleBinding(group_scope, priv_ctx.role_binding.role, 1),
            AudienceRef(group_scope, (group_actor.subject,)),
        )
        with runtime.transaction() as connection:
            for idx in range(25):
                connection.execute(
                    """INSERT INTO conversation_native_turns (
                           turn_id, context_key, context_json, role_instruction,
                           input_observation_id, observed_at, generation_state, phase_at, last_sent_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, NULL)""",
                    (
                        f"priv-turn-{idx}",
                        priv_ctx.key,
                        encode(priv_ctx),
                        "P" * 4096,
                        f"in-p-{idx}",
                        encode_timestamp(NOW - timedelta(minutes=idx)),
                        encode_timestamp(NOW - timedelta(minutes=idx)),
                    ),
                )
            for idx in range(4):
                connection.execute(
                    """INSERT INTO conversation_native_turns (
                           turn_id, context_key, context_json, role_instruction,
                           input_observation_id, observed_at, generation_state, phase_at, last_sent_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, NULL)""",
                    (
                        f"grp-turn-{idx}",
                        group_ctx.key,
                        encode(group_ctx),
                        "G" * 4096,
                        f"in-g-{idx}",
                        encode_timestamp(NOW - timedelta(minutes=idx)),
                        encode_timestamp(NOW - timedelta(minutes=idx)),
                    ),
                )

        captured_sql = []
        original_read_all = runtime.read_all

        def spy_read_all(statement, parameters):
            captured_sql.append((statement, tuple(parameters)))
            return original_read_all(statement, parameters)

        with patch.object(runtime, "read_all", side_effect=spy_read_all):
            rows = latest_role_contexts(runtime, kind="group", limit=8)
            candidates = app.expression_style._group_candidates(NOW, limit=8)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].key, group_ctx.key)
        turn_plans = [
            " | ".join(_query_plan_lines(runtime, stmt, params))
            for stmt, params in captured_sql
            if "FROM conversation_native_turns" in stmt
        ]
        batch_plans = [
            " | ".join(_query_plan_lines(runtime, stmt, params))
            for stmt, params in captured_sql
            if "FROM conversation_batches" in stmt
        ]
        self.assertTrue(
            turn_plans and all("COVERING INDEX idx_native_turns_kind_role_obs" in p for p in turn_plans),
            turn_plans,
        )
        self.assertTrue(batch_plans and all("idx_conversation_batches_group_role" in p for p in batch_plans), batch_plans)

        # scope_family deduplicates repeated context_key rows before TaskContext.restore.
        with patch.object(TaskContext, "restore", wraps=TaskContext.restore) as restore_spy:
            keys = private_scope_keys(runtime, priv_ctx.scope, priv_ctx.role_binding.role)
        self.assertEqual(len(keys), 1)
        self.assertEqual(restore_spy.call_count, 1)

    async def test_tick_scheduler_supports_async_units_aggregates_slow_warnings_and_activity_readiness(self):
        # Async unit using to_thread does not emit a false main-thread slow warning.
        async def async_offloaded():
            await asyncio.to_thread(time.sleep, 0.025)
            return "async-ok"

        async_scheduler = TickScheduler((TickUnit("async-unit", async_offloaded),), budget_seconds=1.0)
        with patch.object(tick_budget, "SLOW_UNIT_WARNING_SECONDS", 0.01):
            with self.assertNoLogs(tick_budget.logger, level=logging.WARNING):
                report = await async_scheduler.advance()
        self.assertTrue(report.completed)
        self.assertEqual(report.results["async-unit"], "async-ok")

        # Repeated slow sync warnings are rate-limited to 300s and report suppressed count on next emit.
        self.assertGreaterEqual(tick_budget.SLOW_UNIT_LOG_INTERVAL_SECONDS, 300.0)

        def slow_sync():
            time.sleep(0.02)
            return "slow"

        sync_scheduler = TickScheduler((TickUnit("slow-sync", slow_sync),), budget_seconds=1.0)
        with patch.object(tick_budget, "SLOW_UNIT_WARNING_SECONDS", 0.01), \
                patch.object(tick_budget, "SLOW_UNIT_LOG_INTERVAL_SECONDS", 300.0), \
                self.assertLogs(tick_budget.logger, level=logging.WARNING) as captured:
            await sync_scheduler.advance()  # logged immediately
            await sync_scheduler.advance()  # suppressed #1
            await sync_scheduler.advance()  # suppressed #2
            # Force interval expiry to verify aggregated suppressed count.
            sync_scheduler._slow_log_at["slow-sync"] -= 301.0
            await sync_scheduler.advance()  # logged with suppressed=2

        self.assertEqual(len(captured.output), 2)
        self.assertIn("suppressed=2", captured.output[1])

        # MemoryActivityIngestion.has_due_bindings gates idle 1s ticks cleanly during the 30s cooldown.
        host = self.host()
        app = host.application
        context = TaskContext(
            self.important,
            self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap(),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        windows = ((context, "prompt", None),)
        self.assertTrue(app.memory.activity.has_due_bindings(windows))
        app.memory.activity._last_advance[context.key] = self.clock.now()
        self.assertFalse(app.memory.activity.has_due_bindings(windows))
        self.clock.advance(timedelta(seconds=31))
        self.assertTrue(app.memory.activity.has_due_bindings(windows))


if __name__ == "__main__":
    unittest.main()
