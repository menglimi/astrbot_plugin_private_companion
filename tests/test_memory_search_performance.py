"""Cache contention and scaling contracts, using only temporary V2 data."""

from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from companion_v2.memory.query import MemoryQuery
from companion_v2.memory.models import AtomRef
from companion_v2.memory.search import SearchIndex
from companion_v2.memory.service import MemoryService
from tests.memory_support import MemoryFixture


@contextmanager
def immediate_sqlite():
    """Avoid the old ten-second busy wait while reproducing error handling."""
    connect = sqlite3.connect
    def immediate(*args, **kwargs):
        return connect(*args, **{**kwargs, "timeout": 0})
    with patch("companion_v2.memory.search.sqlite3.connect", side_effect=immediate):
        yield


class MemorySearchPerformanceTests(MemoryFixture, unittest.IsolatedAsyncioTestCase):
    def test_warm_fact_recall_can_read_while_another_connection_owns_cache_write_lock(self):
        fact = self.remember()
        self.query().unwrap()
        with closing(sqlite3.connect(self.recall.index.path)) as other, immediate_sqlite():
            other.execute("BEGIN IMMEDIATE")
            result = self.query()
        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(result.unwrap()["items"][0]["atom_id"], fact.atom_id)

    def test_busy_cold_cache_preserves_data_and_reports_temporary_unavailability(self):
        fact = self.remember()
        with closing(sqlite3.connect(self.recall.index.path)) as other, immediate_sqlite():
            other.execute("BEGIN IMMEDIATE")
            result = self.query()
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "memory_index_busy")
        self.assertEqual(self.runtime.read_one("SELECT calls FROM memory_sessions WHERE session_id = 'query'", ())["calls"], 0)
        self.assertEqual(self.query().unwrap()["items"][0]["atom_id"], fact.atom_id)
        self.assertTrue(self.memory.read(self.context, fact).is_success)

    def test_ready_cache_reopen_does_not_need_a_write_lock(self):
        fact = self.remember()
        self.query().unwrap()
        with closing(sqlite3.connect(self.recall.index.path)) as other:
            other.execute("BEGIN IMMEDIATE")
            self.recall = MemoryQuery(self.memory)
            self.assertEqual(self.query().unwrap()["items"][0]["atom_id"], fact.atom_id)

    def test_busy_projection_commit_rolls_back_delta_then_retry_reads_current_revision(self):
        original = self.proposed()
        fact = self.remember(original)
        self.query().unwrap()
        self.memory.submit(self.context, replace(original, operation="correct", target=fact,
            object="喝茶", representation="用户后来改喝茶。", evidence=(self.evidence("后来改喝茶"),)),
            operation_key="correction").unwrap()
        with closing(sqlite3.connect(self.recall.index.path)) as reader, \
                closing(sqlite3.connect(self.recall.index.path)) as writer, immediate_sqlite():
            reader.execute("BEGIN")
            before = reader.execute("SELECT state_json FROM fact_sync").fetchone()[0]
            writer.execute("BEGIN IMMEDIATE")
            result = self.query("喝茶")
            self.assertEqual(result.failure.code, "memory_index_busy")
            self.assertEqual(reader.execute("SELECT state_json FROM fact_sync").fetchone()[0], before)
        with closing(sqlite3.connect(self.recall.index.path)) as check:
            self.assertEqual(check.execute("SELECT state_json FROM fact_sync").fetchone()[0], before)
        result = self.query("喝茶").unwrap()
        self.assertEqual(result["items"][0]["revision"], 2)
        self.assertTrue(self.memory.read(self.context, AtomRef(fact.atom_id, 2)).is_success)

    def test_warm_cache_read_under_write_contention_still_rechecks_withdrawal(self):
        original = self.proposed()
        self.remember(original)
        self.query().unwrap()
        self.memory.sources.revoke(original.evidence[0].source, expected_governance_revision=1,
                                   observed_at=self.clock.now()).unwrap()
        with closing(sqlite3.connect(self.recall.index.path)) as other:
            other.execute("BEGIN IMMEDIATE")
            result = self.query().unwrap()
        self.assertEqual(result["items"], [])
        self.assertTrue(result["unavailable_sources"])
        self.assertFalse(result["complete"])

    def test_forgetting_commits_when_cache_is_busy_and_replay_finishes_cleanup(self):
        fact = self.remember()
        self.query().unwrap()
        proof = (self.evidence("忘掉这条记忆"),)
        with closing(sqlite3.connect(self.recall.index.path)) as other:
            other.execute("BEGIN IMMEDIATE")
            first = self.memory.forget(self.context, fact, evidence=proof, operation_key="busy-forget").unwrap()
        self.assertEqual(first["search_projection"], "cleanup_pending")
        self.assertFalse(self.memory.read(self.context, fact).is_success)
        replay = self.memory.forget(self.context, fact, evidence=proof, operation_key="busy-forget").unwrap()
        self.assertEqual(replay["operation_id"], first["operation_id"])
        self.assertNotIn("search_projection", replay)
        self.assertEqual(self.query().unwrap()["items"], [])

    def test_locked_legacy_cache_initialization_never_renames_it_as_corrupt(self):
        fact = self.remember()
        self.query().unwrap()
        with closing(sqlite3.connect(self.recall.index.path)) as other:
            other.execute("PRAGMA user_version = 0")
            other.execute("BEGIN EXCLUSIVE")
            with immediate_sqlite(), patch.object(Path, "replace", side_effect=AssertionError("healthy cache was renamed")):
                query = MemoryQuery(self.memory)
                result = query.query(self.context, "query", query_intent="咖啡")
                self.assertEqual(result.failure.code, "memory_index_busy")
        self.assertEqual(query.query(self.context, "query", query_intent="咖啡").unwrap()["items"][0]["atom_id"], fact.atom_id)
        self.assertEqual(tuple(self.recall.index.path.parent.glob("memory-search.sqlite3.corrupt-*")), ())

    def test_io_error_is_not_misclassified_as_corruption(self):
        error = sqlite3.OperationalError("synthetic I/O failure")
        error.sqlite_errorcode = sqlite3.SQLITE_IOERR
        with patch.object(SearchIndex, "_initialize", side_effect=error), patch.object(Path, "replace") as rename:
            with self.assertRaises(sqlite3.OperationalError):
                SearchIndex(self.runtime.data_root)
            rename.assert_not_called()

    def test_new_fact_projection_work_does_not_grow_linearly_with_cached_corpus(self):
        fact = self.remember()
        base = dict(self.memory._head(self.context, fact))
        index = self.recall.fact_index
        with closing(self.recall.index._open()) as db, db:
            for number in range(256):
                index._put(db, "synthetic", {**base, "atom_id": "synthetic-" + str(number)})
            def batch(start):
                ticks = []
                db.set_progress_handler(lambda: ticks.append(1) and 0, 100)
                try:
                    for number in range(start, start + 32):
                        index._put(db, "synthetic", {**base, "atom_id": "synthetic-" + str(number)})
                finally:
                    db.set_progress_handler(None, 0)
                return len(ticks)
            small = batch(256)
            for number in range(288, 4096):
                index._put(db, "synthetic", {**base, "atom_id": "synthetic-" + str(number)})
            large = batch(4096)
        self.assertLess(large, small * 4, (small, large))

    def test_cache_lock_keeps_pinned_memory_and_tells_role_recall_is_incomplete(self):
        pinned = self.remember(self.proposed(context_mode="pinned"))
        service = MemoryService(self.tasks)
        self.remember(replace(self.proposed(), predicate="其他偏好"), key="other")
        with closing(sqlite3.connect(self.recall.index.path)) as other, immediate_sqlite():
            other.execute("BEGIN IMMEDIATE")
            snapshot = service.context.capture(self.context, "locked-cache", (), self.deadline)
        self.assertEqual(snapshot["state"], "ready")
        self.assertEqual(snapshot["core_ids"], [pinned.atom_id])
        self.assertEqual(snapshot["recall_unavailable"], "memory_index_busy")
        materials = service.context.contributions(snapshot, self.context)
        self.assertTrue(any(item.purpose == "pinned_memory" for item in materials))
        self.assertTrue(any(item.purpose == "memory_coverage" and "暂不可用" in item.payload for item in materials))


if __name__ == "__main__":
    unittest.main()
