"""Global host usage reads real SQLite counters without joining V2 role data."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import json
import unittest
from unittest.mock import patch

from tests import test_astrbot_staging_host as _sdk
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from astrbot.core.db.po import ProviderStat
from astrbot_host.host_token_usage import read_astrbot_token_usage


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone(timedelta(hours=8)))


class HostTokenUsageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory(prefix="companion-host-usage-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + str(Path(self.temporary.name) / "host.db"))
        async with self.engine.begin() as connection:
            await connection.run_sync(ProviderStat.__table__.create)
        self.database = NS(get_db=lambda: AsyncSession(self.engine, expire_on_commit=False))
        self.controller = NS(context=NS(get_db=lambda: self.database))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temporary.cleanup()

    async def insert(self, **changes):
        values = dict(created_at=NOW.astimezone(timezone.utc).replace(tzinfo=None),
            umo="private-session-must-not-leave-db", conversation_id="conversation-must-not-leave-db",
            provider_id="provider-a", provider_model="model-a", token_input_other=100,
            token_input_cached=40, token_output=20)
        values.update(changes)
        async with self.database.get_db() as session:
            session.add(ProviderStat(**values))
            await session.commit()

    async def test_all_sessions_include_history_and_keep_local_day_provider_and_latency_exact(self):
        await self.insert(created_at=datetime(2026, 10, 14, 16, 10), start_time=10, end_time=11)
        await self.insert(created_at=datetime(2026, 10, 15, 1), provider_id="provider-b",
            umo="group-session-must-not-leave-db", token_input_other=200, token_input_cached=0,
            token_output=10, status="error", start_time=20, end_time=22)
        await self.insert(created_at=datetime(2026, 10, 14, 15, 55), token_input_other=20,
                          token_input_cached=0, token_output=5)
        await self.insert(created_at=datetime(2025, 10, 1), token_input_other=99,
                          token_input_cached=0, token_output=0)
        await self.insert(agent_type="external", token_input_other=9999)
        await self.insert(created_at=datetime(2026, 10, 15, 5), token_input_other=8888)
        statements = []
        event.listen(self.engine.sync_engine, "before_cursor_execute",
                     lambda _connection, _cursor, statement, *_: statements.append(statement))
        report = await read_astrbot_token_usage(self.controller, now=NOW)
        self.assertTrue(report["available"], report)
        self.assertFalse(report["counted_in_private_companion_budget"])
        self.assertEqual((report["totals"]["calls"], report["totals"]["tokens"]), (4, 494))
        self.assertEqual(report["totals"]["avg_latency_ms"], 1500)
        self.assertEqual(report["totals"]["failures"], 1)
        self.assertEqual([(row["day"], row["tokens"]) for row in report["daily"]],
                         [("2026-10-14", 25), ("2026-10-15", 370)])
        self.assertEqual(report["daily"][1]["providers"][0]["key"], "provider-b")
        self.assertEqual(sum(row["tokens"] for row in report["by_provider"]), 494)
        self.assertEqual(report["recent"][0]["state"], "discarded")
        self.assertTrue(report["recent"][0]["at"].endswith("+08:00"))
        self.assertIsNone(report["totals"]["cost_microusd"])
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements))
        serialized = json.dumps(report)
        self.assertNotIn("must-not-leave-db", serialized)
        self.assertNotIn("conversation_id", serialized)

    async def test_zero_persisted_usage_is_unknown_and_does_not_invent_cache_receipts(self):
        await self.insert(token_input_other=0, token_input_cached=0, token_output=0, status="aborted")
        report = await read_astrbot_token_usage(self.controller, now=NOW)
        totals = report["totals"]
        self.assertEqual((totals["calls"], totals["unconfirmed_calls"]), (1, 1))
        for key in ("tokens", "reported_tokens", "input_tokens", "output_tokens", "cached_input_tokens",
                    "cache_creation_tokens", "avg_latency_ms", "cost_microusd"):
            self.assertIsNone(totals[key], key)
        self.assertEqual(totals["cache_reported_calls"], 0)
        self.assertFalse(totals["cache_hit_reported"])
        self.assertEqual(report["recent"][0]["state"], "unknown")
        self.assertEqual(report["recent"][0]["usage_availability"], "unavailable")

    async def test_known_cache_tokens_and_missing_receipts_are_distinguished(self):
        await self.insert()
        await self.insert(token_input_other=0, token_input_cached=0, token_output=0)
        report = await read_astrbot_token_usage(self.controller, now=NOW)
        totals = report["totals"]
        self.assertEqual((totals["tokens"], totals["unconfirmed_calls"]), (160, 1))
        self.assertEqual(totals["cached_input_tokens"], 40)
        self.assertEqual(totals["cache_telemetry_calls"], 1)
        self.assertEqual(totals["cache_reported_calls"], 0)
        self.assertIsNone(report["recent"][1]["cache_hit_rate"])

    async def test_empty_host_ledger_is_connected_with_zero_usage(self):
        report = await read_astrbot_token_usage(self.controller, now=NOW)
        self.assertTrue(report["available"], report)
        self.assertEqual((report["totals"]["calls"], report["totals"]["tokens"]), (0, 0))
        self.assertEqual(report["daily"], [])
        self.assertEqual(report["recent"], [])

    async def test_recent_is_bounded_and_orders_equal_timestamps_by_latest_record(self):
        for index in range(85):
            await self.insert(provider_model=f"model-{index}")
        report = await read_astrbot_token_usage(self.controller, now=NOW)
        self.assertEqual(report["totals"]["calls"], 85)
        self.assertEqual(len(report["recent"]), 80)
        self.assertEqual(report["recent"][0]["model"], "model-84")
        self.assertEqual(report["recent"][-1]["model"], "model-5")

    async def test_unavailable_host_and_failed_database_never_substitute_role_usage(self):
        report = await read_astrbot_token_usage(NS(context=NS()), now=NOW)
        self.assertFalse(report["available"])
        self.assertNotIn("totals", report)

        def fail():
            raise RuntimeError("sensitive database details")

        self.controller.context.get_db = fail
        report = await read_astrbot_token_usage(self.controller, now=NOW)
        self.assertFalse(report["available"])
        self.assertNotIn("sensitive", json.dumps(report))

    async def test_file_stat_cache_hits_and_invalidates_immediately_on_insert(self):
        db_file = Path(self.temporary.name) / "host.db"
        self.database.db_path = str(db_file)
        recent_utc = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
        await self.insert(created_at=recent_utc, token_input_other=100, token_input_cached=0, token_output=20)
        statements = []
        event.listen(self.engine.sync_engine, "before_cursor_execute",
                     lambda _connection, _cursor, statement, *_: statements.append(statement))
        first = await read_astrbot_token_usage(self.controller)
        self.assertEqual(first["totals"]["tokens"], 120)
        first_queries = len(statements)
        self.assertGreater(first_queries, 0)

        # Second call with unchanged file stat must hit cache with 0 SQL queries and return an isolated copy.
        first["totals"]["tokens"] = -1
        second = await read_astrbot_token_usage(self.controller)
        self.assertEqual(len(statements), first_queries)
        self.assertEqual(second["totals"]["tokens"], 120)

        # Inserting a new row updates file stat/WAL and immediately invalidates the cache.
        await self.insert(created_at=recent_utc, token_input_other=200, token_input_cached=0, token_output=30)
        third = await read_astrbot_token_usage(self.controller)
        self.assertGreater(len(statements), first_queries)
        self.assertEqual(third["totals"]["tokens"], 350)

    async def test_file_stat_cache_expires_when_a_future_record_becomes_visible(self):
        self.database.db_path = str(Path(self.temporary.name) / "host.db")
        before = NOW.astimezone(timezone.utc)
        after = before + timedelta(minutes=1)
        await self.insert(created_at=after.replace(tzinfo=None))

        with patch("astrbot_host.host_token_usage.datetime") as clock:
            clock.now.side_effect = [before, after, before]
            first = await read_astrbot_token_usage(self.controller)
            second = await read_astrbot_token_usage(self.controller)
            rewound = await read_astrbot_token_usage(self.controller)
        self.assertEqual(first["totals"]["calls"], 0)
        self.assertEqual(second["totals"]["calls"], 1)
        self.assertEqual(rewound["totals"]["calls"], 0)
