from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import (
    InboxRepository,
    MigrationLedger,
    NamespaceRepository,
    OperationRepository,
)
from companion_v2.storage.runtime import SCHEMA_VERSION, StorageOpenError, StoragePathError, StorageRuntime


NOW = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
INSTALLATION_ID = "installation-alpha"


def private_scope() -> ScopeRef:
    return ScopeRef(
        installation_id=INSTALLATION_ID,
        platform="onebot",
        kind=ScopeKind.PRIVATE,
        native_scope_id="private-a",
    )


def operation() -> OperationRef:
    return OperationRef(
        operation_id="operation-a",
        owner="conversation",
        scope=private_scope(),
        version=VersionRef(lineage_id="operation-lineage-a", revision=1),
        idempotency_key="operation-key-a",
    )


class StorageRuntimeTests(unittest.TestCase):
    def test_wal_reader_does_not_wait_for_writer_and_never_sees_uncommitted_data(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            writer_entered = threading.Event()
            release_writer = threading.Event()
            reader_finished = threading.Event()
            writer_errors = []
            reader_values = []
            try:
                with runtime.transaction() as connection:
                    connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                                       ("test.concurrent_read", "committed"))

                def write_then_rollback():
                    try:
                        with runtime.transaction() as connection:
                            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                                               ("uncommitted", "test.concurrent_read"))
                            writer_entered.set()
                            if not release_writer.wait(3):
                                raise AssertionError("reader did not complete during the write")
                            raise ValueError("rollback test")
                    except ValueError:
                        pass
                    except BaseException as error:
                        writer_errors.append(error)

                def read_during_write():
                    try:
                        row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                               ("test.concurrent_read",))
                        reader_values.append(row["value"])
                    except BaseException as error:
                        writer_errors.append(error)
                    finally:
                        reader_finished.set()

                writer = threading.Thread(target=write_then_rollback)
                writer.start()
                self.assertTrue(writer_entered.wait(1))
                reader = threading.Thread(target=read_during_write)
                reader.start()
                completed_without_writer = reader_finished.wait(0.5)
                release_writer.set()
                writer.join(2)
                reader.join(2)

                self.assertTrue(completed_without_writer, "WAL read waited for the writer transaction")
                self.assertFalse(writer.is_alive())
                self.assertFalse(reader.is_alive())
                self.assertEqual(writer_errors, [])
                self.assertEqual(reader_values, ["committed"])
                self.assertEqual(runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                                  ("test.concurrent_read",))["value"], "committed")
            finally:
                release_writer.set()
                runtime.close()

    def test_reads_inside_writer_transaction_keep_uncommitted_visibility(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            try:
                with runtime.transaction() as connection:
                    connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                                       ("test.transaction_read", "before"))
                with runtime.transaction() as connection:
                    connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                                       ("after", "test.transaction_read"))
                    self.assertEqual(runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                                      ("test.transaction_read",))["value"], "after")
                    with runtime.read_snapshot():
                        self.assertEqual(runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                                          ("test.transaction_read",))["value"], "after")
                with runtime.read_snapshot():
                    with self.assertRaises(StorageOpenError):
                        with runtime.transaction():
                            pass
            finally:
                runtime.close()

    def test_wal_checkpoints_run_outside_commits_without_weakening_durability(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            try:
                self.assertEqual(runtime.read_one("PRAGMA wal_autocheckpoint", ())[0], 0)
                self.assertEqual(runtime.read_one("PRAGMA synchronous", ())[0], 2)
                result = runtime.checkpoint_passive()
                self.assertEqual(len(result), 3)
                self.assertGreaterEqual(result[1], result[2])
            finally:
                runtime.close()

    def test_runtime_added_indexes_do_not_block_reopening_the_database(self) -> None:
        """Native conversation code adds optimization indexes at runtime.

        Reopening must require the declared indexes, not reject the database for
        carrying an extra one; strict equality left the plugin unable to start.
        """
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            database = runtime.database_path
            runtime.close()

            connection = sqlite3.connect(database)
            try:
                connection.execute("""CREATE INDEX conversation_batches_extra_idx
                    ON conversation_batches(json_extract(context_json, '$.role_binding'))""")
                connection.commit()
            finally:
                connection.close()

            reopened = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            reopened.close()

    def test_current_schema_reopen_skips_full_integrity_scan(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            runtime.close()

            with patch("companion_v2.storage.runtime._validate_integrity") as integrity_check:
                reopened = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            try:
                integrity_check.assert_not_called()
                self.assertEqual(reopened.read_one(
                    "SELECT value FROM runtime_metadata WHERE key = ?", ("schema_version",))["value"],
                    SCHEMA_VERSION)
            finally:
                reopened.close()

    def test_slow_sqlite_diagnostics_include_safe_timing_fields(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            runtime.SLOW_OPERATION_SECONDS = 0
            try:
                with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
                    runtime.read_one("SELECT ? AS value", ("row-value-that-stays-out",))
                message = captured.output[0]
                self.assertIn("operation=read_one", message)
                self.assertIn("lock_wait_ms=", message)
                self.assertIn("caller=test_storage_runtime.py:", message)
                # The statement names the query that was slow; the parameters are
                # the stored data and never reach the log. Without the statement a
                # stall report can only say "read_one", which is what made the
                # 30-second stalls so expensive to attribute.
                self.assertIn("sql='SELECT ? AS value'", message)
                self.assertNotIn("row-value-that-stays-out", message)

                with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
                    with runtime.read_snapshot():
                        runtime.read_one("SELECT 1", ())
                self.assertEqual(len(captured.output), 1)
                self.assertIn("operation=read_one", captured.output[0])

                with self.assertLogs("companion_v2.storage.runtime", level="INFO") as captured:
                    with runtime.transaction():
                        pass
                self.assertIn("transaction body slow", captured.output[0])
                self.assertIn("caller=test_storage_runtime.py:", captured.output[0])
                self.assertIn("begin_ms=", captured.output[0])
                self.assertIn("body_ms=", captured.output[0])
                self.assertIn("commit_ms=", captured.output[0])
            finally:
                runtime.SLOW_OPERATION_SECONDS = 1.0
                runtime.close()

    def test_namespace_operation_outbox_and_inbox_survive_a_restart(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            namespaces = NamespaceRepository(runtime)
            operations = OperationRepository(runtime)
            inbox = InboxRepository(runtime)

            first_namespace = namespaces.ensure(
                owner="conversation", scope=private_scope(), created_at=NOW
            )
            same_namespace = namespaces.ensure(
                owner="conversation", scope=private_scope(), created_at=NOW
            )
            first = operations.reserve(
                operation(),
                parameter_hash="parameters-a",
                observed_at=NOW,
                outbox_part_id="part-a",
                outbox_payload_hash="payload-a",
            )
            first_inbox = inbox.accept(
                consumer="conversation", event_id="event-a", payload_hash="event-payload-a", observed_at=NOW
            )

            self.assertTrue(runtime.database_path.exists())
            self.assertEqual(first_namespace.namespace_id, same_namespace.namespace_id)
            self.assertFalse(first.unwrap().replayed)
            self.assertEqual(len(operations.pending_outbox()), 1)
            self.assertFalse(first_inbox.unwrap().replayed)
            runtime.close()

            restarted = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            restarted_operations = OperationRepository(restarted)
            restarted_inbox = InboxRepository(restarted)
            replay = restarted_operations.reserve(
                operation(),
                parameter_hash="parameters-a",
                observed_at=NOW,
                outbox_part_id="part-a",
                outbox_payload_hash="payload-a",
            )
            replayed_inbox = restarted_inbox.accept(
                consumer="conversation", event_id="event-a", payload_hash="event-payload-a", observed_at=NOW
            )

            self.assertTrue(replay.unwrap().replayed)
            self.assertTrue(replayed_inbox.unwrap().replayed)
            self.assertEqual(len(restarted_operations.pending_outbox()), 1)
            restarted.close()

    def test_idempotency_and_state_conflicts_are_explicit(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            operations = OperationRepository(runtime)
            first = operations.reserve(operation(), parameter_hash="parameters-a", observed_at=NOW)
            conflict = operations.reserve(operation(), parameter_hash="parameters-b", observed_at=NOW)
            completed = operations.transition(
                "operation-a",
                expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED,
                observed_at=NOW,
            )
            stale_transition = operations.transition(
                "operation-a",
                expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED,
                observed_at=NOW,
            )

            self.assertTrue(first.is_success)
            self.assertEqual(conflict.failure.code, "idempotency_key_conflict")
            self.assertEqual(completed.unwrap().state, OperationState.COMPLETED)
            self.assertEqual(stale_transition.failure.code, "operation_state_conflict")
            runtime.close()

    def test_outbox_acceptance_persists_the_platform_receipt_across_restart(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-outbox-receipt-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            operations = OperationRepository(runtime)
            operations.reserve(operation(), parameter_hash="parameters-a", observed_at=NOW,
                               outbox_part_id="part-a", outbox_payload_hash="payload-a").unwrap()
            dispatched = operations.transition_outbox("operation-a", "part-a",
                expected_operation_state=OperationState.ACCEPTED,
                next_operation_state=OperationState.UNKNOWN,
                expected_outbox_state="pending", next_outbox_state="unknown", observed_at=NOW)
            self.assertTrue(dispatched.is_success)

            missing_receipt = operations.transition_outbox("operation-a", "part-a",
                expected_operation_state=OperationState.UNKNOWN,
                next_operation_state=OperationState.COMPLETED,
                expected_outbox_state="unknown", next_outbox_state="accepted", observed_at=NOW + timedelta(seconds=1))
            self.assertEqual(missing_receipt.failure.code, "outbox_receipt_invalid")

            completed = operations.transition_outbox("operation-a", "part-a",
                expected_operation_state=OperationState.UNKNOWN,
                next_operation_state=OperationState.COMPLETED,
                expected_outbox_state="unknown", next_outbox_state="accepted",
                observed_at=NOW + timedelta(seconds=1), receipt_ref="onebot-message:248610")
            self.assertTrue(completed.is_success)
            self.assertEqual(operations.outbox("operation-a", "part-a").unwrap().receipt_ref,
                             "onebot-message:248610")
            runtime.close()

            with StorageRuntime.open(data_root=Path(temporary) / "v2-data",
                                     installation_id=INSTALLATION_ID) as restarted:
                self.assertEqual(OperationRepository(restarted).outbox("operation-a", "part-a").unwrap().receipt_ref,
                                 "onebot-message:248610")

    def test_checkpoint_compare_and_swap_preserves_recovery_cursor(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            ledger = MigrationLedger(runtime)
            created = ledger.advance(
                checkpoint_id="checkpoint-a",
                owner="legacy-private",
                source_fingerprint="snapshot-hash-a",
                cursor="source-a:1",
                state="baseline_imported",
                expected_revision=0,
                observed_at=NOW,
            )
            conflict = ledger.advance(
                checkpoint_id="checkpoint-a",
                owner="legacy-private",
                source_fingerprint="snapshot-hash-a",
                cursor="source-a:2",
                state="baseline_imported",
                expected_revision=0,
                observed_at=NOW,
            )
            advanced = ledger.advance(
                checkpoint_id="checkpoint-a",
                owner="legacy-private",
                source_fingerprint="snapshot-hash-a",
                cursor="source-a:2",
                state="final_reconciled",
                expected_revision=1,
                observed_at=NOW,
            )

            self.assertEqual(created.unwrap().revision, 1)
            self.assertEqual(conflict.failure.code, "checkpoint_revision_conflict")
            self.assertEqual(advanced.unwrap().cursor, "source-a:2")
            self.assertEqual(advanced.unwrap().revision, 2)
            runtime.close()

    def test_runtime_refuses_v1_directory_and_foreign_installation(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            runtime.close()
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=root, installation_id="installation-beta")
            with self.assertRaises(StoragePathError):
                StorageRuntime.open(
                    data_root=Path(temporary) / "astrbot_plugin_private_companion",
                    installation_id=INSTALLATION_ID,
                )

    def test_takeover_child_and_fresh_public_name_can_open_without_touching_legacy_data(self):
        with TemporaryDirectory(prefix="companion-storage-takeover-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            public.mkdir()
            source = public / "companions.json"
            source.write_bytes(b'{"legacy":true}\n')
            source_bytes = source.read_bytes()
            with self.assertRaises(StoragePathError):
                StorageRuntime.open(data_root=public / "profiles" / "main" / "runtime",
                                    installation_id=INSTALLATION_ID)
            child = public / "companion-7" / "profiles" / "main" / "runtime"
            runtime = StorageRuntime.open(data_root=child, installation_id=INSTALLATION_ID)
            runtime.close()
            runtime = StorageRuntime.open(data_root=child, installation_id=INSTALLATION_ID)
            runtime.close()
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertFalse((public / StorageRuntime.DATABASE_FILENAME).exists())
            source.unlink()
            runtime = StorageRuntime.open(data_root=public / "profiles" / "main" / "runtime",
                                          installation_id=INSTALLATION_ID)
            runtime.close()

    def test_fresh_public_name_runtime_is_allowed_without_legacy_markers(self):
        with TemporaryDirectory(prefix="companion-storage-fresh-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            public.mkdir()
            runtime = StorageRuntime.open(data_root=public / "runtime",
                                          installation_id=INSTALLATION_ID)
            runtime.close()
            self.assertTrue((public / "runtime" / StorageRuntime.DATABASE_FILENAME).is_file())

    def test_plugin_code_directory_cannot_become_the_runtime_root(self):
        with TemporaryDirectory(prefix="companion-storage-code-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            public.mkdir()
            (public / "main.py").write_text("", encoding="utf-8")
            with self.assertRaises(StoragePathError):
                StorageRuntime.open(data_root=public / "runtime", installation_id=INSTALLATION_ID)

    def test_legacy_marker_root_still_rejects_a_direct_runtime_child(self):
        with TemporaryDirectory(prefix="companion-storage-legacy-runtime-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            public.mkdir()
            (public / "companions.json").write_bytes(b'{"legacy":true}\n')
            with self.assertRaises(StoragePathError):
                StorageRuntime.open(data_root=public / "runtime", installation_id=INSTALLATION_ID)

    def test_isolated_child_rejects_legacy_markers_inside_its_boundary(self):
        with TemporaryDirectory(prefix="companion-storage-boundary-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            child = public / "companion-7"
            child.mkdir(parents=True)
            (child / "companions.db").write_bytes(b"legacy sentinel")
            for root in (child, child / "profiles" / "main" / "runtime", public / "arbitrary"):
                with self.assertRaises(StoragePathError):
                    StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            foreign = Path(temporary) / "astrbot_plugin_remember_you" / "companion-7" / "runtime"
            with self.assertRaises(StoragePathError):
                StorageRuntime.open(data_root=foreign, installation_id=INSTALLATION_ID)

    def test_existing_owned_profiles_allow_parent_legacy_files_but_not_runtime_contamination(self):
        for marker in (".companion-v2-storage-owner.json", ".companion-v2-storage-location.json"):
            with self.subTest(marker=marker), TemporaryDirectory() as temporary:
                public = Path(temporary) / "astrbot_plugin_private_companion"
                public.mkdir()
                source = public / "companions.json"
                source.write_bytes(b'{"legacy":true}\n')
                (public / marker).write_text("{}\n", encoding="utf-8")
                root = public / "profiles" / "main" / "runtime"
                runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
                runtime.close()
                runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
                runtime.close()
                self.assertEqual(source.read_bytes(), b'{"legacy":true}\n')
                (root / "companions.db").write_bytes(b"legacy sentinel")
                with self.assertRaises(StoragePathError):
                    StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)

    def test_runtime_fails_closed_for_a_partially_initialized_database(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            root = Path(temporary) / "v2-data"
            runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            database_path = runtime.database_path
            runtime.close()
            connection = sqlite3.connect(database_path)
            connection.execute("DROP TABLE outbox")
            connection.commit()
            connection.close()

            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)

    def test_fresh_public_name_installations_runtime_is_allowed(self):
        with TemporaryDirectory(prefix="companion-storage-installations-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            public.mkdir()
            root = public / "installations" / "installation-a" / "runtime"
            runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION_ID)
            runtime.close()
            self.assertTrue((root / StorageRuntime.DATABASE_FILENAME).is_file())

    def test_plugin_code_directory_installations_runtime_is_rejected(self):
        with TemporaryDirectory(prefix="companion-storage-code-installations-") as temporary:
            public = Path(temporary) / "astrbot_plugin_private_companion"
            public.mkdir()
            (public / "main.py").write_text("", encoding="utf-8")
            with self.assertRaises(StoragePathError):
                StorageRuntime.open(data_root=public / "installations" / "installation-a" / "runtime",
                                    installation_id=INSTALLATION_ID)


class SlowOperationLogRateLimitTests(unittest.TestCase):
    """Rate limiting for StorageRuntime._report_slow_operation.

    These cases never call ``open()``: the behaviour under test only reads the
    limiter's own state plus SLOW_OPERATION_SECONDS, and bootstrapping all schema
    migrations would add seconds per case without touching a line here. The clock
    is injected through ``_slow_operation_clock`` so a window can be advanced
    deterministically instead of by sleeping.
    """

    def setUp(self) -> None:
        self._temporary = TemporaryDirectory(prefix="companion-v2-slowlog-")
        self._clock = [1000.0]

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _runtime(self, *, interval: float = 60.0) -> StorageRuntime:
        runtime = StorageRuntime(data_root=Path(self._temporary.name) / "v2-data",
                                 installation_id=INSTALLATION_ID)
        runtime.SLOW_OPERATION_SECONDS = 0
        runtime.SLOW_OPERATION_LOG_INTERVAL_SECONDS = interval
        runtime._slow_operation_clock = lambda: self._clock[0]
        return runtime

    def _record_slow(self, runtime: StorageRuntime, operation: str, elapsed_ms: int) -> None:
        """Emit one slow event from a single stable call site.

        _report_slow_operation keys its rate limit on ``file:line`` of the first
        frame outside runtime.py/contextlib.py, so a call written inline in a test
        would give every invocation its own line number and therefore its own
        window. Routing events through this one line reproduces the real shape:
        the same query, repeated from the same place in the code.
        """
        runtime._report_slow_operation(operation, 0.0, 0.0, elapsed_ms / 1000)

    def _record_slow_variant(self, runtime: StorageRuntime, operation: str, elapsed_ms: int) -> None:
        """A second, deliberately distinct call site for the same operation."""
        runtime._report_slow_operation(operation, 0.0, 0.0, elapsed_ms / 1000)

    def test_first_line_keeps_original_format_and_later_ones_stay_silent(self) -> None:
        runtime = self._runtime()
        with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
            for _ in range(5):
                self._record_slow(runtime, "read_one", 2)
                self._clock[0] += 1.0
        self.assertEqual(len(captured.output), 1, captured.output)
        first = captured.output[0]
        # Nothing was dropped yet, so the line is the old format exactly: no new
        # field appears until the rate limit has actually suppressed something.
        self.assertIn("V2 SQLite slow operation=read_one elapsed_ms=2 lock_wait_ms=0 held_ms=2 ", first)
        self.assertIn("caller=test_storage_runtime.py:", first)
        self.assertNotIn("suppressed=", first)
        self.assertNotIn("max_elapsed_ms=", first)

    def test_a_long_statement_is_collapsed_and_truncated_to_one_line(self) -> None:
        runtime = self._runtime()
        statement = "SELECT c.call_id\n    FROM model_calls c\n    WHERE " + "x" * 400
        with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
            runtime._report_slow_operation("read_all", 0.0, 0.0, 0.002, statement=statement)
        line = captured.output[0]
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn("\n", line)
        self.assertIn("sql=", line)
        self.assertLess(len(line), 400)
        self.assertTrue(line.endswith("…'"), line)

    def test_expired_window_reports_suppressed_count_and_max_elapsed(self) -> None:
        runtime = self._runtime()
        self._record_slow(runtime, "read_all", 4)  # opens the first window and logs once
        for step, elapsed in ((1.0, 10), (2.0, 30), (3.0, 7)):
            self._clock[0] += step
            self._record_slow(runtime, "read_all", elapsed)
        # Still inside the 60 s window: the event above is swallowed silently.
        with self.assertNoLogs("companion_v2.storage.runtime", level="WARNING"):
            self._clock[0] += 1.0
            self._record_slow(runtime, "read_all", 11)
        # Cross the window: the next event reports what the window swallowed.
        self._clock[0] += 60.0
        with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
            self._record_slow(runtime, "read_all", 12)
        self.assertEqual(len(captured.output), 1, captured.output)
        summary = captured.output[0]
        self.assertIn("operation=read_all", summary)
        self.assertIn("elapsed_ms=12", summary)
        self.assertIn("lock_wait_ms=0", summary)
        self.assertIn("held_ms=12", summary)
        self.assertIn("caller=test_storage_runtime.py:", summary)
        # Four events were swallowed; 30 ms was the worst of them.
        self.assertIn("suppressed=4", summary)
        self.assertIn("max_elapsed_ms=30", summary)
        # Every original field keeps its position; the new ones are appended last.
        self.assertLess(summary.index("caller="), summary.index("suppressed="))
        self.assertLess(summary.index("suppressed="), summary.index("max_elapsed_ms="))

        # The summary opened a fresh window: counters must not carry over.
        self._clock[0] += 1.0
        with self.assertNoLogs("companion_v2.storage.runtime", level="WARNING"):
            self._record_slow(runtime, "read_all", 11)

    def test_keys_are_independent_per_operation_and_caller(self) -> None:
        runtime = self._runtime()
        with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
            self._record_slow(runtime, "read_one", 2)              # (read_one, call site A)
            self._record_slow(runtime, "read_one", 3)              # swallowed
            self._record_slow_variant(runtime, "read_one", 2)      # (read_one, call site B)
            self._record_slow_variant(runtime, "read_one", 3)      # swallowed separately
            self._record_slow(runtime, "read_out_of_snapshot", 2)  # call site A, other operation
            self._record_slow(runtime, "read_out_of_snapshot", 3)  # swallowed separately
        self.assertEqual(len(captured.output), 3, captured.output)
        keys = sorted(runtime._slow_operation_log_state)
        self.assertEqual([operation for operation, _ in keys],
                         ["read_one", "read_one", "read_out_of_snapshot"])
        # Two distinct call sites are involved: the shared helper and the variant.
        self.assertEqual(len({caller for _, caller in keys}), 2)
        self.assertEqual(len({caller for operation, caller in keys if operation == "read_one"}), 2)
        # Each key swallowed exactly its own repeat, never another key's.
        self.assertEqual(sorted(state.suppressed
                                for state in runtime._slow_operation_log_state.values()), [1, 1, 1])

    def test_state_is_capped_but_never_loses_a_first_line(self) -> None:
        runtime = self._runtime()
        runtime.SLOW_OPERATION_LOG_MAX_KEYS = 8
        with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
            for index in range(50):
                self._clock[0] += 0.001
                # Distinct keys via distinct operation names, all inside one
                # window so none of them expires naturally.
                runtime._report_slow_operation(f"op_{index}", 0.0, 0.0, 0.002)
        self.assertLessEqual(len(runtime._slow_operation_log_state), 8)
        # Eviction may cost a key one extra line later; it must never swallow the
        # first line that the cap exists to protect.
        self.assertEqual(len(captured.output), 50)

    def test_concurrent_callers_share_one_window_without_losing_counts(self) -> None:
        runtime = self._runtime()
        workers = 8
        per_worker = 200
        # Spread the threads over many windows so both the "first event" branch and
        # the "bump the counters" branch are exercised concurrently. Every window
        # must still account for exactly one unsuppressed event.
        windows = 20

        def hammer() -> None:
            for index in range(per_worker):
                self._record_slow(runtime, f"op_{index % windows}", 5)

        with self.assertLogs("companion_v2.storage.runtime", level="WARNING") as captured:
            threads = [threading.Thread(target=hammer) for _ in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        total = workers * per_worker
        # One line per window for the very first event, everything else swallowed,
        # and no event may be lost by a non-atomic ``+=`` on the shared counter.
        self.assertEqual(len(captured.output), windows, len(captured.output))
        self.assertEqual(sum(state.suppressed for state in runtime._slow_operation_log_state.values()),
                         total - windows)

    def test_every_state_access_happens_while_the_rate_limit_lock_is_held(self) -> None:
        runtime = self._runtime()
        observed: list[bool] = []
        runtime_state = runtime._slow_operation_log_state
        runtime_lock = runtime._slow_operation_log_lock

        class GuardedState(dict):
            """Fails if the limiter touches its state outside the critical section."""

            def get(self, key, default=None):
                observed.append(runtime_lock.locked())
                return super().get(key, default)

            def __setitem__(self, key, value):
                observed.append(runtime_lock.locked())
                return super().__setitem__(key, value)

            def __delitem__(self, key):
                observed.append(runtime_lock.locked())
                return super().__delitem__(key)

            def __len__(self):
                observed.append(runtime_lock.locked())
                return super().__len__()

        guarded = GuardedState(runtime_state)
        runtime._slow_operation_log_state = guarded

        self._record_slow(runtime, "read_one", 2)   # new key: __len__, __setitem__
        self._record_slow(runtime, "read_one", 3)   # existing key: get + counter bump
        self._record_slow(runtime, "read_two", 2)   # another new key
        self._record_slow(runtime, "read_two", 3)

        self.assertTrue(observed, "the limiter never touched its state")
        self.assertTrue(all(observed), "state was read or written outside the lock")

    def test_checkpoint_warning_path_is_unchanged(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-storage-") as temporary:
            runtime = StorageRuntime.open(data_root=Path(temporary) / "v2-data", installation_id=INSTALLATION_ID)
            try:
                # checkpoint_passive keeps its own single-line WAL warning and does
                # not route through the limiter. Whatever the schema bootstrap
                # recorded is irrelevant; the checkpoint itself must add no key and
                # emit no warning.
                runtime._slow_operation_log_state.clear()
                with self.assertNoLogs("companion_v2.storage.runtime", level="WARNING"):
                    result = runtime.checkpoint_passive()
                self.assertEqual(len(result), 3)
                self.assertEqual(runtime._slow_operation_log_state, {})
            finally:
                runtime.close()
