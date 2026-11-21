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
from companion_v2.execution.models import TaskContext, digest, encode
from companion_v2.expression.private_scope import private_people, role_private_contexts
from companion_v2.foundation.refs import AudienceRef
from companion_v2.host import tick_budget
from companion_v2.host.tick_budget import TickScheduler, TickUnit
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


if __name__ == "__main__":
    unittest.main()
