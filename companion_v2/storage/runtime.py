"""A V2-owned SQLite lifecycle with fail-closed schema upgrades."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import sys
from threading import RLock, local
import time
from typing import Iterator

from .schema import (
    SCHEMA_VERSION_V67, MIGRATION_V66_TO_V67_ID, MIGRATION_V66_TO_V67_FINGERPRINT,
    V67_MIGRATION_STATEMENTS, V67_REQUIRED_TABLES, V67_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V66, MIGRATION_V65_TO_V66_ID, MIGRATION_V65_TO_V66_FINGERPRINT,
    V66_MIGRATION_STATEMENTS, V66_REQUIRED_TABLES, V66_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V65, MIGRATION_V64_TO_V65_ID, MIGRATION_V64_TO_V65_FINGERPRINT,
    V65_MIGRATION_STATEMENTS, V65_REQUIRED_TABLES, V65_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V64, MIGRATION_V63_TO_V64_ID, MIGRATION_V63_TO_V64_FINGERPRINT,
    V64_MIGRATION_STATEMENTS, V64_REQUIRED_TABLES, V64_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V63, MIGRATION_V62_TO_V63_ID, MIGRATION_V62_TO_V63_FINGERPRINT,
    V63_MIGRATION_STATEMENTS, V63_REQUIRED_TABLES, V63_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V62, MIGRATION_V61_TO_V62_ID, MIGRATION_V61_TO_V62_FINGERPRINT,
    V62_MIGRATION_STATEMENTS, V62_REQUIRED_TABLES, V62_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V61, MIGRATION_V60_TO_V61_ID, MIGRATION_V60_TO_V61_FINGERPRINT,
    V61_MIGRATION_STATEMENTS, V61_REQUIRED_TABLES, V61_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V60, MIGRATION_V59_TO_V60_ID, MIGRATION_V59_TO_V60_FINGERPRINT,
    V60_MIGRATION_STATEMENTS, V60_REQUIRED_TABLES, V60_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V59, MIGRATION_V58_TO_V59_ID, MIGRATION_V58_TO_V59_FINGERPRINT,
    V59_MIGRATION_STATEMENTS, V59_REQUIRED_TABLES, V59_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V58, MIGRATION_V57_TO_V58_ID, MIGRATION_V57_TO_V58_FINGERPRINT,
    V58_MIGRATION_STATEMENTS, V58_REQUIRED_TABLES, V58_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V57, MIGRATION_V56_TO_V57_ID, MIGRATION_V56_TO_V57_FINGERPRINT,
    V57_MIGRATION_STATEMENTS, V57_REQUIRED_TABLES, V57_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V56, MIGRATION_V55_TO_V56_ID, MIGRATION_V55_TO_V56_FINGERPRINT,
    V56_MIGRATION_STATEMENTS, V56_REQUIRED_TABLES, V56_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V55, MIGRATION_V54_TO_V55_ID, MIGRATION_V54_TO_V55_FINGERPRINT,
    V55_MIGRATION_STATEMENTS, V55_REQUIRED_TABLES, V55_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V54, MIGRATION_V53_TO_V54_ID, MIGRATION_V53_TO_V54_FINGERPRINT,
    V54_MIGRATION_STATEMENTS, V54_REQUIRED_TABLES, V54_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V53, MIGRATION_V52_TO_V53_ID, MIGRATION_V52_TO_V53_FINGERPRINT,
    V53_MIGRATION_STATEMENTS, V53_REQUIRED_TABLES, V53_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V52, MIGRATION_V51_TO_V52_ID, MIGRATION_V51_TO_V52_FINGERPRINT,
    V52_MIGRATION_STATEMENTS, V52_REQUIRED_TABLES, V52_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V51, MIGRATION_V50_TO_V51_ID, MIGRATION_V50_TO_V51_FINGERPRINT,
    V51_MIGRATION_STATEMENTS, V51_REQUIRED_TABLES, V51_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V50, MIGRATION_V49_TO_V50_ID, MIGRATION_V49_TO_V50_FINGERPRINT,
    V50_MIGRATION_STATEMENTS, V50_REQUIRED_TABLES, V50_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V49, MIGRATION_V48_TO_V49_ID, MIGRATION_V48_TO_V49_FINGERPRINT,
    V49_MIGRATION_STATEMENTS, V49_REQUIRED_TABLES, V49_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V48, MIGRATION_V47_TO_V48_ID, MIGRATION_V47_TO_V48_FINGERPRINT,
    V48_MIGRATION_STATEMENTS, V48_REQUIRED_TABLES, V48_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V47, MIGRATION_V46_TO_V47_ID, MIGRATION_V46_TO_V47_FINGERPRINT,
    V47_MIGRATION_STATEMENTS, V47_REQUIRED_TABLES, V47_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V46, MIGRATION_V45_TO_V46_ID, MIGRATION_V45_TO_V46_FINGERPRINT,
    V46_MIGRATION_STATEMENTS, V46_REQUIRED_TABLES, V46_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V45, MIGRATION_V44_TO_V45_ID, MIGRATION_V44_TO_V45_FINGERPRINT,
    V45_MIGRATION_STATEMENTS, V45_REQUIRED_TABLES, V45_SCHEMA_STATEMENTS, PERSONA_CRAFT_TRIGGERS,
    SCHEMA_VERSION_V44, MIGRATION_V43_TO_V44_ID, MIGRATION_V43_TO_V44_FINGERPRINT,
    V44_MIGRATION_STATEMENTS, V44_REQUIRED_TABLES, V44_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V43, MIGRATION_V42_TO_V43_ID, MIGRATION_V42_TO_V43_FINGERPRINT,
    V43_MIGRATION_STATEMENTS, V43_REQUIRED_TABLES, V43_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V42, MIGRATION_V41_TO_V42_ID, MIGRATION_V41_TO_V42_FINGERPRINT,
    V42_MIGRATION_STATEMENTS, V42_REQUIRED_TABLES, V42_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V41, MIGRATION_V40_TO_V41_ID, MIGRATION_V40_TO_V41_FINGERPRINT,
    V41_MIGRATION_STATEMENTS, V41_REQUIRED_TABLES, V41_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V40, MIGRATION_V39_TO_V40_ID, MIGRATION_V39_TO_V40_FINGERPRINT,
    V40_MIGRATION_STATEMENTS, V40_REQUIRED_TABLES, V40_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V39, MIGRATION_V38_TO_V39_ID, MIGRATION_V38_TO_V39_FINGERPRINT,
    V39_MIGRATION_STATEMENTS, V39_REQUIRED_TABLES, V39_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V38, MIGRATION_V37_TO_V38_ID, MIGRATION_V37_TO_V38_FINGERPRINT,
    V38_MIGRATION_STATEMENTS, V38_REQUIRED_TABLES, V38_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V37, MIGRATION_V36_TO_V37_ID, MIGRATION_V36_TO_V37_FINGERPRINT,
    V37_MIGRATION_STATEMENTS, V37_REQUIRED_TABLES, V37_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V36, MIGRATION_V35_TO_V36_ID, MIGRATION_V35_TO_V36_FINGERPRINT,
    V36_MIGRATION_STATEMENTS, V36_REQUIRED_TABLES, V36_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V35, MIGRATION_V34_TO_V35_ID, MIGRATION_V34_TO_V35_FINGERPRINT,
    V35_MIGRATION_STATEMENTS, V35_REQUIRED_TABLES, V35_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V34, MIGRATION_V33_TO_V34_ID, MIGRATION_V33_TO_V34_FINGERPRINT,
    V34_MIGRATION_STATEMENTS, V34_REQUIRED_TABLES, V34_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V33, MIGRATION_V32_TO_V33_ID, MIGRATION_V32_TO_V33_FINGERPRINT,
    V33_MIGRATION_STATEMENTS, V33_REQUIRED_TABLES, V33_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V32, MIGRATION_V31_TO_V32_ID, MIGRATION_V31_TO_V32_FINGERPRINT,
    V32_MIGRATION_STATEMENTS, V32_REQUIRED_TABLES, V32_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V31, MIGRATION_V30_TO_V31_ID, MIGRATION_V30_TO_V31_FINGERPRINT,
    V31_MIGRATION_STATEMENTS, V31_REQUIRED_TABLES, V31_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V30, MIGRATION_V29_TO_V30_ID, MIGRATION_V29_TO_V30_FINGERPRINT,
    V30_MIGRATION_STATEMENTS, V30_REQUIRED_TABLES, V30_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V29, MIGRATION_V28_TO_V29_ID, MIGRATION_V28_TO_V29_FINGERPRINT,
    V29_MIGRATION_STATEMENTS, V29_REQUIRED_TABLES, V29_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V28, MIGRATION_V27_TO_V28_ID, MIGRATION_V27_TO_V28_FINGERPRINT,
    V28_MIGRATION_STATEMENTS, V28_REQUIRED_TABLES, V28_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V27, MIGRATION_V26_TO_V27_ID, MIGRATION_V26_TO_V27_FINGERPRINT,
    V27_MIGRATION_STATEMENTS, V27_REQUIRED_TABLES, V27_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V26, MIGRATION_V25_TO_V26_ID, MIGRATION_V25_TO_V26_FINGERPRINT,
    V26_MIGRATION_STATEMENTS, V26_REQUIRED_TABLES, V26_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V25, MIGRATION_V24_TO_V25_ID, MIGRATION_V24_TO_V25_FINGERPRINT,
    V25_MIGRATION_STATEMENTS, V25_REQUIRED_TABLES, V25_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V24, MIGRATION_V23_TO_V24_ID, MIGRATION_V23_TO_V24_FINGERPRINT,
    V24_MIGRATION_STATEMENTS, V24_REQUIRED_TABLES, V24_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V22, SCHEMA_VERSION_V23, MIGRATION_V22_TO_V23_ID, MIGRATION_V22_TO_V23_FINGERPRINT,
    V23_MIGRATION_STATEMENTS, V23_REQUIRED_TABLES, V23_SCHEMA_STATEMENTS,
    MIGRATION_V21_TO_V22_FINGERPRINT, MIGRATION_V21_TO_V22_ID,
    SCHEMA_VERSION_V21, V21_SCHEMA_STATEMENTS,
    V22_MIGRATION_STATEMENTS, V22_REQUIRED_TABLES, V22_SCHEMA_STATEMENTS,
    MIGRATION_V20_TO_V21_FINGERPRINT, MIGRATION_V20_TO_V21_ID,
    SCHEMA_VERSION_V20, V20_SCHEMA_STATEMENTS,
    V21_MIGRATION_STATEMENTS, V21_REQUIRED_TABLES, V21_SCHEMA_STATEMENTS,
    SCHEMA_VERSION_V19, V19_SCHEMA_STATEMENTS,
    MIGRATION_V19_TO_V20_ID, MIGRATION_V19_TO_V20_FINGERPRINT,
    V20_MIGRATION_STATEMENTS, V20_REQUIRED_TABLES,
    MIGRATION_V1_TO_V2_FINGERPRINT,
    MIGRATION_V1_TO_V2_ID,
    MIGRATION_V2_TO_V3_FINGERPRINT,
    MIGRATION_V2_TO_V3_ID,
    MIGRATION_V3_TO_V4_FINGERPRINT,
    MIGRATION_V3_TO_V4_ID,
    MIGRATION_V4_TO_V5_FINGERPRINT,
    MIGRATION_V4_TO_V5_ID,
    MIGRATION_V5_TO_V6_FINGERPRINT,
    MIGRATION_V5_TO_V6_ID,
    MIGRATION_V6_TO_V7_FINGERPRINT,
    MIGRATION_V6_TO_V7_ID,
    MIGRATION_V7_TO_V8_FINGERPRINT,
    MIGRATION_V7_TO_V8_ID,
    MIGRATION_V8_TO_V9_FINGERPRINT,
    MIGRATION_V8_TO_V9_ID,
    MIGRATION_V9_TO_V10_FINGERPRINT,
    MIGRATION_V9_TO_V10_ID,
    MIGRATION_V10_TO_V11_ID,
    MIGRATION_V10_TO_V11_FINGERPRINT,
    MIGRATION_V11_TO_V12_ID,
    MIGRATION_V11_TO_V12_FINGERPRINT,
    MIGRATION_V12_TO_V13_ID,
    MIGRATION_V12_TO_V13_FINGERPRINT,
    MIGRATION_V13_TO_V14_ID,
    MIGRATION_V13_TO_V14_FINGERPRINT,
    MIGRATION_V14_TO_V15_ID,
    MIGRATION_V14_TO_V15_FINGERPRINT,
    MIGRATION_V15_TO_V16_ID,
    MIGRATION_V15_TO_V16_FINGERPRINT,
    MIGRATION_V16_TO_V17_ID,
    MIGRATION_V16_TO_V17_FINGERPRINT,
    MIGRATION_V17_TO_V18_ID,
    MIGRATION_V17_TO_V18_FINGERPRINT,
    MIGRATION_V18_TO_V19_ID,
    MIGRATION_V18_TO_V19_FINGERPRINT,
    SCHEMA_VERSION_V18,
    V18_SCHEMA_STATEMENTS,
    V19_MIGRATION_STATEMENTS,
    V19_REQUIRED_TABLES,
    SCHEMA_VERSION_V19,
    V19_SCHEMA_STATEMENTS,
    MIGRATION_V19_TO_V20_ID,
    MIGRATION_V19_TO_V20_FINGERPRINT,
    V20_MIGRATION_STATEMENTS,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    SCHEMA_VERSION_V3,
    SCHEMA_VERSION_V4,
    SCHEMA_VERSION_V5,
    SCHEMA_VERSION_V6,
    SCHEMA_VERSION_V7,
    SCHEMA_VERSION_V8,
    SCHEMA_VERSION_V9,
    SCHEMA_VERSION_V10,
    SCHEMA_VERSION_V11,
    SCHEMA_VERSION_V12,
    SCHEMA_VERSION_V13,
    SCHEMA_VERSION_V14,
    SCHEMA_VERSION_V15,
    SCHEMA_VERSION_V16,
    SCHEMA_VERSION_V17,
    V1_REQUIRED_TABLES,
    V1_SCHEMA_STATEMENTS,
    V2_MIGRATION_STATEMENTS,
    V2_REQUIRED_TABLES,
    V3_MIGRATION_STATEMENTS,
    V3_REQUIRED_TABLES,
    V4_MIGRATION_STATEMENTS,
    V4_REQUIRED_TABLES,
    V5_MIGRATION_STATEMENTS,
    V5_REQUIRED_TABLES,
    V5_SCHEMA_STATEMENTS,
    V6_REQUIRED_TABLES,
    V6_TABLE_STATEMENTS,
    V6_INDEX_STATEMENTS,
    V6_SCHEMA_STATEMENTS,
    V7_MIGRATION_STATEMENTS,
    V7_REQUIRED_TABLES,
    V7_SCHEMA_STATEMENTS,
    V8_MIGRATION_STATEMENTS,
    V8_REQUIRED_TABLES,
    V8_SCHEMA_STATEMENTS,
    V9_MIGRATION_STATEMENTS,
    V9_REQUIRED_TABLES,
    V9_SCHEMA_STATEMENTS,
    V10_MIGRATION_STATEMENTS,
    V10_REQUIRED_TABLES,
    V10_SCHEMA_STATEMENTS,
    V11_MIGRATION_STATEMENTS,
    V11_REQUIRED_TABLES,
    V11_SCHEMA_STATEMENTS,
    V12_MIGRATION_STATEMENTS,
    V12_REQUIRED_TABLES,
    V12_SCHEMA_STATEMENTS,
    V13_MIGRATION_STATEMENTS,
    V13_REQUIRED_TABLES,
    V13_SCHEMA_STATEMENTS,
    V14_MIGRATION_STATEMENTS,
    V14_REQUIRED_TABLES,
    V14_SCHEMA_STATEMENTS,
    V15_MIGRATION_STATEMENTS,
    V15_REQUIRED_TABLES,
    V15_SCHEMA_STATEMENTS,
    V16_MIGRATION_STATEMENTS,
    V16_REQUIRED_TABLES,
    V16_SCHEMA_STATEMENTS,
    V17_MIGRATION_STATEMENTS,
    V17_REQUIRED_TABLES,
    V17_SCHEMA_STATEMENTS,
    V18_MIGRATION_STATEMENTS,
    V18_REQUIRED_TABLES,
    SCHEMA_STATEMENTS,
)


class StorageOpenError(RuntimeError):
    """The V2 store could not be opened or did not match the requested installation."""


class StorageTransactionNestingError(StorageOpenError):
    """A durable receipt requires ownership of the actual outer COMMIT."""


class StoragePathError(ValueError):
    """The proposed V2 data root violates the source/data isolation boundary."""


LEGACY_PLUGIN_DIRECTORY_NAMES = frozenset(
    {
        "astrbot_plugin_private_companion",
        "astrbot_plugin_remember_you",
    }
)

logger = logging.getLogger(__name__)


class StorageRuntime:
    """Own the V2 database connection; repositories never open their own connection."""

    DATABASE_FILENAME = "companion-v2.sqlite3"
    SLOW_OPERATION_SECONDS = 1.0

    def __init__(self, *, data_root: Path, installation_id: str) -> None:
        if not installation_id or installation_id != installation_id.strip():
            raise StoragePathError("installation_id must be a non-blank opaque string")
        self._data_root = _validate_data_root(data_root)
        self._installation_id = installation_id
        self._database_path = self._data_root / self.DATABASE_FILENAME
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()
        self._read_snapshot_diagnostics = local()
        self._savepoint_sequence = 0
        self._write_depth = 0

    @classmethod
    def open(cls, *, data_root: str | Path, installation_id: str) -> "StorageRuntime":
        runtime = cls(data_root=Path(data_root), installation_id=installation_id)
        runtime._open_connection()
        return runtime

    @property
    def data_root(self) -> Path:
        return self._data_root

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def installation_id(self) -> str:
        return self._installation_id

    def _report_slow_operation(self, operation: str, started: float, lock_acquired: float,
                               finished: float, *, details: str = "") -> None:
        elapsed_ms = round((finished - started) * 1000)
        if elapsed_ms < self.SLOW_OPERATION_SECONDS * 1000:
            return
        caller = sys._getframe(1)
        while caller is not None and Path(caller.f_code.co_filename).name in {"runtime.py", "contextlib.py"}:
            caller = caller.f_back
        if caller is None:
            caller = sys._getframe(1)
        caller_name = f"{Path(caller.f_code.co_filename).name}:{caller.f_lineno}"
        detail_suffix = " " + details if details else ""
        logger.warning(
            "V2 SQLite slow operation=%s elapsed_ms=%d lock_wait_ms=%d held_ms=%d caller=%s%s",
            operation, elapsed_ms, round((lock_acquired - started) * 1000),
            round((finished - lock_acquired) * 1000), caller_name, detail_suffix,
        )

    def close(self) -> None:
        started = time.perf_counter()
        self._lock.acquire()
        lock_acquired = time.perf_counter()
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            finished = time.perf_counter()
            self._lock.release()
            self._report_slow_operation("close", started, lock_acquired, finished)

    def __enter__(self) -> "StorageRuntime":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def checkpoint_passive(self) -> tuple[int, int, int]:
        """Checkpoint committed WAL pages on a maintenance connection."""
        started = time.perf_counter()
        with self._lock:
            self._require_connection()
        checkpoint = None
        result = None
        try:
            checkpoint = sqlite3.connect(str(self._database_path), timeout=0.0, isolation_level=None)
            checkpoint.execute("PRAGMA synchronous = FULL")
            row = checkpoint.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            result = tuple(int(value) for value in row)
            return result
        finally:
            if checkpoint is not None:
                checkpoint.close()
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            if elapsed_ms >= self.SLOW_OPERATION_SECONDS * 1000:
                details = "" if result is None else (
                    f"busy={result[0]} wal_frames={result[1]} checkpointed_frames={result[2]}")
                logger.warning("V2 SQLite WAL checkpoint slow elapsed_ms=%d mode=PASSIVE%s",
                               elapsed_ms, " " + details if details else "")

    @contextmanager
    def transaction(self, *, require_outermost: bool = False) -> Iterator[sqlite3.Connection]:
        """Compose repository writes; nested success is not an outer commit."""

        started = time.perf_counter()
        self._lock.acquire()
        lock_acquired = time.perf_counter()
        begin_ms = body_ms = commit_ms = rollback_ms = 0
        try:
            connection = self._require_connection()
            nested = connection.in_transaction
            if nested and require_outermost:
                raise StorageTransactionNestingError("this operation must own its outer transaction")
            if nested and self._write_depth == 0:
                raise StorageOpenError("a read snapshot cannot be promoted to a write transaction")
            self._savepoint_sequence += 1
            savepoint = f"v2_write_{self._savepoint_sequence}"
            begin_started = time.perf_counter()
            connection.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            begin_ms = round((time.perf_counter() - begin_started) * 1000)
            self._write_depth += 1
            try:
                body_started = time.perf_counter()
                yield connection
                body_ms = round((time.perf_counter() - body_started) * 1000)
                commit_started = time.perf_counter()
                connection.execute(f"RELEASE SAVEPOINT {savepoint}" if nested else "COMMIT")
                commit_ms = round((time.perf_counter() - commit_started) * 1000)
            except BaseException:
                body_ms = round((time.perf_counter() - body_started) * 1000)
                if connection.in_transaction:
                    rollback_started = time.perf_counter()
                    if nested:
                        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    else:
                        connection.execute("ROLLBACK")
                    rollback_ms = round((time.perf_counter() - rollback_started) * 1000)
                raise
            finally:
                self._write_depth -= 1
        finally:
            finished = time.perf_counter()
            self._lock.release()
            self._report_slow_operation(
                "transaction", started, lock_acquired, finished,
                details=f"begin_ms={begin_ms} body_ms={body_ms} commit_ms={commit_ms} rollback_ms={rollback_ms}",
            )

    @contextmanager
    def read_only_snapshot(self) -> Iterator[sqlite3.Connection]:
        """A synchronous projection that cannot borrow or promote a write transaction."""

        with self._lock:
            connection = self._require_connection()
            if self._write_depth:
                raise StorageOpenError("a read-only projection cannot borrow a write transaction")
            previous = connection.execute("PRAGMA query_only").fetchone()[0]
            connection.execute("PRAGMA query_only = ON")
            try:
                with self.read_snapshot():
                    yield connection
            finally:
                connection.execute("PRAGMA query_only = ON" if previous else "PRAGMA query_only = OFF")

    @contextmanager
    def read_snapshot(self) -> Iterator[sqlite3.Connection]:
        """Keep related reads on one SQLite snapshot, also inside an owned write."""

        started = time.perf_counter()
        self._lock.acquire()
        lock_acquired = time.perf_counter()
        diagnostic = getattr(self._read_snapshot_diagnostics, "current", None)
        owns_diagnostic = diagnostic is None
        if owns_diagnostic:
            diagnostic = {"slow_child": False}
            self._read_snapshot_diagnostics.current = diagnostic
        try:
            connection = self._require_connection()
            if connection.in_transaction:
                yield connection
                return
            connection.execute("BEGIN")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            finished = time.perf_counter()
            self._lock.release()
            if owns_diagnostic:
                try:
                    if not diagnostic["slow_child"]:
                        self._report_slow_operation("read_snapshot", started, lock_acquired, finished)
                finally:
                    del self._read_snapshot_diagnostics.current

    def read_one(self, statement: str, parameters: tuple[object, ...]) -> sqlite3.Row | None:
        started = time.perf_counter()
        self._lock.acquire()
        lock_acquired = time.perf_counter()
        finished = None
        try:
            result = self._require_connection().execute(statement, parameters).fetchone()
            finished = time.perf_counter()
            return result
        finally:
            if finished is None:
                finished = time.perf_counter()
            self._lock.release()
            diagnostic = getattr(self._read_snapshot_diagnostics, "current", None)
            if diagnostic is not None and finished - started >= self.SLOW_OPERATION_SECONDS:
                diagnostic["slow_child"] = True
            self._report_slow_operation("read_one", started, lock_acquired, finished)

    def read_all(self, statement: str, parameters: tuple[object, ...]) -> tuple[sqlite3.Row, ...]:
        started = time.perf_counter()
        self._lock.acquire()
        lock_acquired = time.perf_counter()
        finished = None
        try:
            result = tuple(self._require_connection().execute(statement, parameters).fetchall())
            finished = time.perf_counter()
            return result
        finally:
            if finished is None:
                finished = time.perf_counter()
            self._lock.release()
            diagnostic = getattr(self._read_snapshot_diagnostics, "current", None)
            if diagnostic is not None and finished - started >= self.SLOW_OPERATION_SECONDS:
                diagnostic["slow_child"] = True
            self._report_slow_operation("read_all", started, lock_acquired, finished)

    def _open_connection(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(self._database_path),
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA wal_autocheckpoint = 0")
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            raise StorageOpenError("unable to open V2 SQLite store") from error
        self._connection = connection
        try:
            # SQLite's documented table rebuild requires foreign keys disabled
            # before BEGIN. Initialization holds the only unpublished connection;
            # every migration checks references before commit and restores them.
            connection.execute("PRAGMA foreign_keys = OFF")
            try:
                self._initialize_schema()
            finally:
                connection.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as error:
            self.close()
            raise StorageOpenError("V2 schema validation or migration failed") from error
        except BaseException:
            self.close()
            raise

    def _initialize_schema(self) -> None:
        with self.transaction() as connection:
            existing_tables = _existing_tables(connection)
            if not existing_tables:
                for statement in V1_SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                    ("installation_id", self._installation_id),
                )
                connection.execute(
                    "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", SCHEMA_VERSION_V1),
                )
                self._migrate_v1_to_v2(connection)
                self._migrate_v2_to_v3(connection)
                self._migrate_v3_to_v4(connection)
                self._migrate_v4_to_v5(connection)
                self._migrate_v5_to_v6(connection)
                self._migrate_v6_to_v7(connection)
                self._migrate_v7_to_v8(connection)
                self._migrate_v8_to_v9(connection)
                self._migrate_v9_to_v10(connection)
                self._migrate_v10_to_v11(connection)
                self._migrate_v11_to_v12(connection)
                self._migrate_v12_to_v13(connection)
                self._migrate_v13_to_v14(connection)
                self._migrate_v14_to_v15(connection)
                self._migrate_v15_to_v16(connection)
                self._migrate_v16_to_v17(connection)
                self._migrate_v17_to_v18(connection)
                self._migrate_v18_to_v19(connection)
                self._migrate_v19_to_v20(connection)
                self._migrate_v20_to_v21(connection)
                self._migrate_v21_to_v22(connection)
                self._migrate_v22_to_v23(connection)
                self._migrate_v23_to_v24(connection)
                self._migrate_v24_to_v25(connection)
                self._migrate_v25_to_v26(connection)
                self._migrate_v26_to_v27(connection)
                self._migrate_v27_to_v28(connection)
                self._migrate_v28_to_v29(connection)
                self._migrate_v29_to_v30(connection)
                self._migrate_v30_to_v31(connection)
                self._migrate_v31_to_v32(connection)
                self._migrate_v32_to_v33(connection)
                self._migrate_v33_to_v34(connection)
                self._migrate_v34_to_v35(connection)
                self._migrate_v35_to_v36(connection)
                self._migrate_v36_to_v37(connection)
                self._migrate_v37_to_v38(connection)
                self._migrate_v38_to_v39(connection)
                self._migrate_v39_to_v40(connection)
                self._migrate_v40_to_v41(connection)
                self._migrate_v41_to_v42(connection)
                self._migrate_v42_to_v43(connection)
                self._migrate_v43_to_v44(connection)
                self._migrate_v44_to_v45(connection)
                self._migrate_v45_to_v46(connection)
                self._migrate_v46_to_v47(connection)
                self._migrate_v47_to_v48(connection)
                self._migrate_v48_to_v49(connection)
                self._migrate_v49_to_v50(connection)
                self._migrate_v50_to_v51(connection)
                self._migrate_v51_to_v52(connection)
                self._migrate_v52_to_v53(connection)
                self._migrate_v53_to_v54(connection)
                self._migrate_v54_to_v55(connection)
                self._migrate_v55_to_v56(connection)
                self._migrate_v56_to_v57(connection)
                self._migrate_v57_to_v58(connection)
                self._migrate_v58_to_v59(connection)
                self._migrate_v59_to_v60(connection)
                self._migrate_v60_to_v61(connection)
                self._migrate_v61_to_v62(connection)
                self._migrate_v62_to_v63(connection)
                self._migrate_v63_to_v64(connection)
                self._migrate_v64_to_v65(connection)
                self._migrate_v65_to_v66(connection)
                self._migrate_v66_to_v67(connection)
                return
            if "runtime_metadata" not in existing_tables:
                raise StorageOpenError("V2 data root is partially initialized")
            stored_installation = connection.execute(
                "SELECT value FROM runtime_metadata WHERE key = ?",
                ("installation_id",),
            ).fetchone()
            if stored_installation is None:
                raise StorageOpenError("V2 data root is missing installation identity")
            if stored_installation["value"] != self._installation_id:
                raise StorageOpenError("V2 data root belongs to another installation")
            stored_schema = connection.execute(
                "SELECT value FROM runtime_metadata WHERE key = ?",
                ("schema_version",),
            ).fetchone()
            if stored_schema is None:
                raise StorageOpenError("V2 data root is missing schema version")
            version = stored_schema["value"]
            current_schema_on_open = version == SCHEMA_VERSION
            if version == SCHEMA_VERSION_V1:
                missing = V1_REQUIRED_TABLES.difference(existing_tables)
                v2_tables_present = V23_REQUIRED_TABLES.difference(V1_REQUIRED_TABLES).intersection(
                    existing_tables
                )
                if missing or v2_tables_present:
                    raise StorageOpenError("V2 data root has an incomplete v1-to-v2 migration")
                _validate_table_shapes(connection, V1_SCHEMA_STATEMENTS, V1_REQUIRED_TABLES)
                _validate_integrity(connection)
                self._migrate_v1_to_v2(connection)
                version = SCHEMA_VERSION_V2
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V2:
                if V2_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V2_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v2-to-v3 migration")
                _validate_table_shapes(
                    connection, V1_SCHEMA_STATEMENTS + V2_MIGRATION_STATEMENTS, V2_REQUIRED_TABLES,
                )
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._migrate_v2_to_v3(connection)
                version = SCHEMA_VERSION_V3
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V3:
                if V3_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V3_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v3-to-v4 migration")
                _validate_table_shapes(
                    connection, V1_SCHEMA_STATEMENTS + V2_MIGRATION_STATEMENTS + V3_MIGRATION_STATEMENTS,
                    V3_REQUIRED_TABLES,
                )
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._migrate_v3_to_v4(connection)
                version = SCHEMA_VERSION_V4
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V4:
                if V4_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V4_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v4-to-v5 migration")
                _validate_table_shapes(
                    connection, V1_SCHEMA_STATEMENTS + V2_MIGRATION_STATEMENTS
                    + V3_MIGRATION_STATEMENTS + V4_MIGRATION_STATEMENTS, V4_REQUIRED_TABLES,
                )
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._validate_v4_migration_record(connection)
                self._migrate_v4_to_v5(connection)
                version = SCHEMA_VERSION_V5
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V5:
                if V5_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V5_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v5-to-v6 migration")
                _validate_table_shapes(connection, V5_SCHEMA_STATEMENTS, V5_REQUIRED_TABLES)
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._validate_v4_migration_record(connection)
                self._validate_v5_migration_record(connection)
                self._migrate_v5_to_v6(connection)
                version = SCHEMA_VERSION_V6
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V6:
                if V6_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V6_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v6-to-v7 migration")
                _validate_table_shapes(connection, V6_SCHEMA_STATEMENTS, V6_REQUIRED_TABLES)
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._validate_v4_migration_record(connection)
                self._validate_v5_migration_record(connection)
                self._validate_v6_migration_record(connection)
                self._migrate_v6_to_v7(connection)
                version = SCHEMA_VERSION_V7
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V7:
                if V7_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V7_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v7-to-v8 migration")
                _validate_table_shapes(connection, V7_SCHEMA_STATEMENTS, V7_REQUIRED_TABLES)
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._validate_v4_migration_record(connection)
                self._validate_v5_migration_record(connection)
                self._validate_v6_migration_record(connection)
                self._validate_v7_migration_record(connection)
                self._migrate_v7_to_v8(connection)
                version = SCHEMA_VERSION_V8
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V8:
                if V8_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V8_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v8-to-v9 migration")
                _validate_table_shapes(connection, V8_SCHEMA_STATEMENTS, V8_REQUIRED_TABLES)
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._validate_v4_migration_record(connection)
                self._validate_v5_migration_record(connection)
                self._validate_v6_migration_record(connection)
                self._validate_v7_migration_record(connection)
                self._validate_v8_migration_record(connection)
                self._migrate_v8_to_v9(connection)
                version = SCHEMA_VERSION_V9
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V9:
                if V9_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V9_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v9-to-v10 migration")
                _validate_table_shapes(connection, V9_SCHEMA_STATEMENTS, V9_REQUIRED_TABLES)
                _validate_integrity(connection)
                self._validate_v2_migration_record(connection)
                self._validate_v3_migration_record(connection)
                self._validate_v4_migration_record(connection)
                self._validate_v5_migration_record(connection)
                self._validate_v6_migration_record(connection)
                self._validate_v7_migration_record(connection)
                self._validate_v8_migration_record(connection)
                self._validate_v9_migration_record(connection)
                self._migrate_v9_to_v10(connection)
                version = SCHEMA_VERSION_V10
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V10:
                if V10_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V10_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v10-to-v11 migration")
                _validate_table_shapes(connection, V10_SCHEMA_STATEMENTS, V10_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 11):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v10_to_v11(connection)
                version = SCHEMA_VERSION_V11
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V11:
                if V11_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V11_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v11-to-v12 migration")
                _validate_table_shapes(connection, V11_SCHEMA_STATEMENTS, V11_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 12):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v11_to_v12(connection)
                version = SCHEMA_VERSION_V12
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V12:
                if V12_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V12_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v12-to-v13 migration")
                _validate_table_shapes(connection, V12_SCHEMA_STATEMENTS, V12_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 13):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v12_to_v13(connection)
                version = SCHEMA_VERSION_V13
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V13:
                if V13_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V13_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v13-to-v14 migration")
                _validate_table_shapes(connection, V13_SCHEMA_STATEMENTS, V13_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 14):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v13_to_v14(connection)
                version = SCHEMA_VERSION_V14
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V14:
                if V14_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V14_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v14-to-v15 migration")
                _validate_table_shapes(connection, V14_SCHEMA_STATEMENTS, V14_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 15):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v14_to_v15(connection)
                version = SCHEMA_VERSION_V15
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V15:
                if V15_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V15_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v15-to-v16 migration")
                _validate_table_shapes(connection, V15_SCHEMA_STATEMENTS, V15_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 16):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v15_to_v16(connection)
                version = SCHEMA_VERSION_V16
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V16:
                if V16_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V16_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v16-to-v17 migration")
                _validate_table_shapes(connection, V16_SCHEMA_STATEMENTS, V16_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 17):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v16_to_v17(connection)
                version = SCHEMA_VERSION_V17
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V17:
                if V17_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V17_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v17-to-v18 migration")
                _validate_table_shapes(connection, V17_SCHEMA_STATEMENTS, V17_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 18):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v17_to_v18(connection)
                version = SCHEMA_VERSION_V18
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V18:
                if V18_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V18_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v18-to-v19 migration")
                _validate_table_shapes(connection, V18_SCHEMA_STATEMENTS, V18_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 19):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v18_to_v19(connection)
                version = SCHEMA_VERSION_V19
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V19:
                if V19_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V19_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v19-to-v20 migration")
                _validate_table_shapes(connection, V19_SCHEMA_STATEMENTS, V19_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 20):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v19_to_v20(connection)
                version = SCHEMA_VERSION_V20
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V20:
                if V20_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V20_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v20-to-v21 migration")
                _validate_table_shapes(connection, V20_SCHEMA_STATEMENTS, V20_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 21):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v20_to_v21(connection)
                version = SCHEMA_VERSION_V21
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V21:
                if V21_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V21_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v21-to-v22 migration")
                _validate_table_shapes(connection, V21_SCHEMA_STATEMENTS, V21_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 22):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v21_to_v22(connection)
                version = SCHEMA_VERSION_V22
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V22:
                if V22_REQUIRED_TABLES.difference(existing_tables) or (
                    V23_REQUIRED_TABLES.difference(V22_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v22-to-v23 migration")
                _validate_table_shapes(connection, V22_SCHEMA_STATEMENTS, V22_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 23):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v22_to_v23(connection)
                version = SCHEMA_VERSION_V23
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V23:
                if V23_REQUIRED_TABLES.difference(existing_tables) or (
                    V24_REQUIRED_TABLES.difference(V23_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v23-to-v24 migration")
                _validate_table_shapes(connection, V23_SCHEMA_STATEMENTS, V23_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 24):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v23_to_v24(connection)
                version = SCHEMA_VERSION_V24
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V24:
                if V24_REQUIRED_TABLES.difference(existing_tables) or (
                    V25_REQUIRED_TABLES.difference(V24_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v24-to-v25 migration")
                _validate_table_shapes(connection, V24_SCHEMA_STATEMENTS, V24_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 25):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v24_to_v25(connection)
                version = SCHEMA_VERSION_V25
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V25:
                if V25_REQUIRED_TABLES.difference(existing_tables) or (
                    V26_REQUIRED_TABLES.difference(V25_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v25-to-v26 migration")
                _validate_table_shapes(connection, V25_SCHEMA_STATEMENTS, V25_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 26):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v25_to_v26(connection)
                version = SCHEMA_VERSION_V26
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V26:
                if V26_REQUIRED_TABLES.difference(existing_tables) or (
                    V27_REQUIRED_TABLES.difference(V26_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v26-to-v27 migration")
                _validate_table_shapes(connection, V26_SCHEMA_STATEMENTS, V26_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 27):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v26_to_v27(connection)
                version = SCHEMA_VERSION_V27
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V27:
                if V27_REQUIRED_TABLES.difference(existing_tables) or (
                    V28_REQUIRED_TABLES.difference(V27_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v27-to-v28 migration")
                _validate_table_shapes(connection, V27_SCHEMA_STATEMENTS, V27_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 28):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v27_to_v28(connection)
                version = SCHEMA_VERSION_V28
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V28:
                if V28_REQUIRED_TABLES.difference(existing_tables) or (
                    V29_REQUIRED_TABLES.difference(V28_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v28-to-v29 migration")
                _validate_table_shapes(connection, V28_SCHEMA_STATEMENTS, V28_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 29):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v28_to_v29(connection)
                version = SCHEMA_VERSION_V29
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V29:
                if V29_REQUIRED_TABLES.difference(existing_tables) or (
                    V30_REQUIRED_TABLES.difference(V29_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v29-to-v30 migration")
                _validate_table_shapes(connection, V29_SCHEMA_STATEMENTS, V29_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 30):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v29_to_v30(connection)
                version = SCHEMA_VERSION_V30
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V30:
                if V30_REQUIRED_TABLES.difference(existing_tables) or (
                    V31_REQUIRED_TABLES.difference(V30_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v30-to-v31 migration")
                _validate_table_shapes(connection, V30_SCHEMA_STATEMENTS, V30_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 31):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v30_to_v31(connection)
                version = SCHEMA_VERSION_V31
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V31:
                if V31_REQUIRED_TABLES.difference(existing_tables) or (
                    V32_REQUIRED_TABLES.difference(V31_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v31-to-v32 migration")
                _validate_table_shapes(connection, V31_SCHEMA_STATEMENTS, V31_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 32):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v31_to_v32(connection)
                version = SCHEMA_VERSION_V32
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V32:
                if V32_REQUIRED_TABLES.difference(existing_tables) or (
                    V33_REQUIRED_TABLES.difference(V32_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v32-to-v33 migration")
                _validate_table_shapes(connection, V32_SCHEMA_STATEMENTS, V32_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 33):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v32_to_v33(connection)
                version = SCHEMA_VERSION_V33
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V33:
                if V33_REQUIRED_TABLES.difference(existing_tables) or (
                    V34_REQUIRED_TABLES.difference(V33_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v33-to-v34 migration")
                _validate_table_shapes(connection, V33_SCHEMA_STATEMENTS, V33_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 34):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v33_to_v34(connection)
                version = SCHEMA_VERSION_V34
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V34:
                if V34_REQUIRED_TABLES.difference(existing_tables) or (
                    V35_REQUIRED_TABLES.difference(V34_REQUIRED_TABLES).intersection(existing_tables)
                ):
                    raise StorageOpenError("V2 data root has an incomplete v34-to-v35 migration")
                _validate_table_shapes(connection, V34_SCHEMA_STATEMENTS, V34_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 35):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v34_to_v35(connection)
                version = SCHEMA_VERSION_V35
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V35:
                if V35_REQUIRED_TABLES.difference(existing_tables) or "model_call_failures_v36" in existing_tables:
                    raise StorageOpenError("V2 data root has an incomplete v35-to-v36 migration")
                _validate_table_shapes(connection, V35_SCHEMA_STATEMENTS, V35_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 36):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v35_to_v36(connection)
                version = SCHEMA_VERSION_V36
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V36:
                if V36_REQUIRED_TABLES.difference(existing_tables) or "proactive_balance_polls" in existing_tables:
                    raise StorageOpenError("V2 data root has an incomplete v36-to-v37 migration")
                _validate_table_shapes(connection, V36_SCHEMA_STATEMENTS, V36_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 37):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v36_to_v37(connection)
                version = SCHEMA_VERSION_V37
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V37:
                if V37_REQUIRED_TABLES.difference(existing_tables) or "proactive_news_polls" in existing_tables:
                    raise StorageOpenError("V2 data root has an incomplete v37-to-v38 migration")
                _validate_table_shapes(connection, V37_SCHEMA_STATEMENTS, V37_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 38):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v37_to_v38(connection)
                version = SCHEMA_VERSION_V38
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V38:
                if V38_REQUIRED_TABLES.difference(existing_tables) or "role_self_review_runs" in existing_tables:
                    raise StorageOpenError("V2 data root has an incomplete v38-to-v39 migration")
                _validate_table_shapes(connection, V38_SCHEMA_STATEMENTS, V38_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 39):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v38_to_v39(connection)
                version = SCHEMA_VERSION_V39
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V39:
                if V39_REQUIRED_TABLES.difference(existing_tables) or "expression_style_runs" in existing_tables:
                    raise StorageOpenError("V2 data root has an incomplete v39-to-v40 migration")
                _validate_table_shapes(connection, V39_SCHEMA_STATEMENTS, V39_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 40):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v39_to_v40(connection)
                version = SCHEMA_VERSION_V40
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V40:
                # Group-member safety evidence is additive, so a data root that still
                # carries it is a downgraded copy rather than a half-applied upgrade.
                if V40_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v40-to-v41 migration")
                _validate_table_shapes(connection, V40_SCHEMA_STATEMENTS, V40_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 41):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v40_to_v41(connection)
                version = SCHEMA_VERSION_V41
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V41:
                # The reading archive is additive, so a data root that still carries it
                # is a downgraded copy rather than a half-applied upgrade.
                if V41_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v41-to-v42 migration")
                _validate_table_shapes(connection, V41_SCHEMA_STATEMENTS, V41_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 42):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v41_to_v42(connection)
                version = SCHEMA_VERSION_V42
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V42:
                # Reference assets are additive, so a data root that still carries
                # them is a downgraded copy rather than a half-applied upgrade.
                if V42_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v42-to-v43 migration")
                _validate_table_shapes(connection, V42_SCHEMA_STATEMENTS, V42_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 43):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v42_to_v43(connection)
                version = SCHEMA_VERSION_V43
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V43:
                # The trigger cursors are additive, so a data root that still carries
                # them is a downgraded copy rather than a half-applied upgrade.
                if V43_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v43-to-v44 migration")
                _validate_table_shapes(connection, V43_SCHEMA_STATEMENTS, V43_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 44):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v43_to_v44(connection)
                version = SCHEMA_VERSION_V44
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V44:
                # Persona draft versions are additive, so a data root that still
                # carries them is a downgraded copy rather than a half-applied
                # upgrade.
                if V44_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v44-to-v45 migration")
                _validate_table_shapes(connection, V44_SCHEMA_STATEMENTS, V44_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 45):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v44_to_v45(connection)
                version = SCHEMA_VERSION_V45
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V45:
                _validate_table_shapes(connection, V45_SCHEMA_STATEMENTS, V45_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 46):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v45_to_v46(connection)
                version = SCHEMA_VERSION_V46
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V46:
                if V46_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v46-to-v47 migration")
                _validate_table_shapes(connection, V46_SCHEMA_STATEMENTS, V46_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 47):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v46_to_v47(connection)
                version = SCHEMA_VERSION_V47
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V47:
                if V47_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v47-to-v48 migration")
                _validate_table_shapes(connection, V47_SCHEMA_STATEMENTS, V47_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 48):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v47_to_v48(connection)
                version = SCHEMA_VERSION_V48
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V48:
                if V48_REQUIRED_TABLES.difference(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v48-to-v49 migration")
                _validate_table_shapes(connection, V48_SCHEMA_STATEMENTS, V48_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 49):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v48_to_v49(connection)
                version = SCHEMA_VERSION_V49
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V49:
                _validate_table_shapes(connection, V49_SCHEMA_STATEMENTS, V49_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 50):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v49_to_v50(connection)
                version = SCHEMA_VERSION_V50
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V50:
                _validate_table_shapes(connection, V50_SCHEMA_STATEMENTS, V50_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 51):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v50_to_v51(connection)
                version = SCHEMA_VERSION_V51
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V51:
                _validate_table_shapes(connection, V51_SCHEMA_STATEMENTS, V51_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 52):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v51_to_v52(connection)
                version = SCHEMA_VERSION_V52
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V52:
                if V53_REQUIRED_TABLES.difference(V52_REQUIRED_TABLES).intersection(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v52-to-v53 migration")
                _validate_table_shapes(connection, V52_SCHEMA_STATEMENTS, V52_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 53):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v52_to_v53(connection)
                version = SCHEMA_VERSION_V53
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V53:
                if V54_REQUIRED_TABLES.difference(V53_REQUIRED_TABLES).intersection(existing_tables):
                    raise StorageOpenError("V2 data root has an incomplete v53-to-v54 migration")
                _validate_table_shapes(connection, V53_SCHEMA_STATEMENTS, V53_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 54):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v53_to_v54(connection)
                version = SCHEMA_VERSION_V54
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V54:
                _validate_table_shapes(connection, V54_SCHEMA_STATEMENTS, V54_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 55):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v54_to_v55(connection)
                version = SCHEMA_VERSION_V55
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V55:
                _validate_table_shapes(connection, V55_SCHEMA_STATEMENTS, V55_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 56):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v55_to_v56(connection)
                version = SCHEMA_VERSION_V56
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V56:
                _validate_table_shapes(connection, V56_SCHEMA_STATEMENTS, V56_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 57):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v56_to_v57(connection)
                version = SCHEMA_VERSION_V57
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V57:
                _validate_table_shapes(connection, V57_SCHEMA_STATEMENTS, V57_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 58):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v57_to_v58(connection)
                version = SCHEMA_VERSION_V58
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V58:
                _validate_table_shapes(connection, V58_SCHEMA_STATEMENTS, V58_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 59):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v58_to_v59(connection)
                version = SCHEMA_VERSION_V59
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V59:
                _validate_table_shapes(connection, V59_SCHEMA_STATEMENTS, V59_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 60):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v59_to_v60(connection)
                version = SCHEMA_VERSION_V60
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V60:
                _validate_table_shapes(connection, V60_SCHEMA_STATEMENTS, V60_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 61):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v60_to_v61(connection)
                version = SCHEMA_VERSION_V61
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V61:
                _validate_table_shapes(connection, V61_SCHEMA_STATEMENTS, V61_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 62):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v61_to_v62(connection)
                version = SCHEMA_VERSION_V62
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V62:
                _validate_table_shapes(connection, V62_SCHEMA_STATEMENTS, V62_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 63):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v62_to_v63(connection)
                version = SCHEMA_VERSION_V63
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V63:
                _validate_table_shapes(connection, V63_SCHEMA_STATEMENTS, V63_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 64):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v63_to_v64(connection)
                version = SCHEMA_VERSION_V64
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V64:
                _validate_table_shapes(connection, V64_SCHEMA_STATEMENTS, V64_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 65):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v64_to_v65(connection)
                version = SCHEMA_VERSION_V65
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V65:
                _validate_table_shapes(connection, V65_SCHEMA_STATEMENTS, V65_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 66):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v65_to_v66(connection)
                version = SCHEMA_VERSION_V66
                existing_tables = _existing_tables(connection)
            if version == SCHEMA_VERSION_V66:
                _validate_table_shapes(connection, V66_SCHEMA_STATEMENTS, V66_REQUIRED_TABLES)
                _validate_integrity(connection)
                for previous in range(2, 67):
                    getattr(self, f"_validate_v{previous}_migration_record")(connection)
                self._migrate_v66_to_v67(connection)
                version = SCHEMA_VERSION_V67
                existing_tables = _existing_tables(connection)
            if version != SCHEMA_VERSION:
                raise StorageOpenError("V2 data root has an incompatible schema version")
            missing = V67_REQUIRED_TABLES.difference(existing_tables)
            if missing:
                raise StorageOpenError("V2 data root is missing required schema objects")
            _validate_table_shapes(connection, V67_SCHEMA_STATEMENTS, V67_REQUIRED_TABLES)
            # Each schema migration runs a full integrity check at its boundary.
            # Rechecking every page and foreign key on every normal startup makes
            # open time grow with the data store, even when the schema is current.
            if not current_schema_on_open:
                _validate_integrity(connection)
            self._validate_v2_migration_record(connection)
            self._validate_v3_migration_record(connection)
            self._validate_v4_migration_record(connection)
            self._validate_v5_migration_record(connection)
            self._validate_v6_migration_record(connection)
            self._validate_v7_migration_record(connection)
            self._validate_v8_migration_record(connection)
            self._validate_v9_migration_record(connection)
            self._validate_v10_migration_record(connection)
            self._validate_v11_migration_record(connection)
            self._validate_v12_migration_record(connection)
            self._validate_v13_migration_record(connection)
            self._validate_v14_migration_record(connection)
            self._validate_v15_migration_record(connection)
            self._validate_v16_migration_record(connection)
            self._validate_v17_migration_record(connection)
            self._validate_v18_migration_record(connection)
            self._validate_v19_migration_record(connection)
            self._validate_v20_migration_record(connection)
            self._validate_v21_migration_record(connection)
            self._validate_v63_migration_record(connection)
            self._validate_v22_migration_record(connection)
            self._validate_v23_migration_record(connection)
            self._validate_v24_migration_record(connection)
            self._validate_v25_migration_record(connection)
            self._validate_v26_migration_record(connection)
            self._validate_v27_migration_record(connection)
            self._validate_v28_migration_record(connection)
            self._validate_v29_migration_record(connection)
            self._validate_v30_migration_record(connection)
            self._validate_v31_migration_record(connection)
            self._validate_v32_migration_record(connection)
            self._validate_v33_migration_record(connection)
            self._validate_v34_migration_record(connection)
            self._validate_v35_migration_record(connection)
            self._validate_v36_migration_record(connection)
            self._validate_v37_migration_record(connection)
            self._validate_v38_migration_record(connection)
            self._validate_v39_migration_record(connection)
            self._validate_v40_migration_record(connection)
            self._validate_v41_migration_record(connection)
            self._validate_v42_migration_record(connection)
            self._validate_v43_migration_record(connection)
            self._validate_v44_migration_record(connection)
            self._validate_v45_migration_record(connection)
            self._validate_v46_migration_record(connection)
            self._validate_v47_migration_record(connection)
            self._validate_v48_migration_record(connection)
            self._validate_v49_migration_record(connection)
            self._validate_v50_migration_record(connection)
            self._validate_v51_migration_record(connection)
            self._validate_v52_migration_record(connection)
            self._validate_v53_migration_record(connection)
            self._validate_v54_migration_record(connection)
            self._validate_v55_migration_record(connection)
            self._validate_v56_migration_record(connection)
            self._validate_v57_migration_record(connection)
            self._validate_v58_migration_record(connection)
            self._validate_v59_migration_record(connection)
            self._validate_v60_migration_record(connection)
            self._validate_v61_migration_record(connection)
            self._validate_v62_migration_record(connection)
            self._validate_v64_migration_record(connection)
            self._validate_v65_migration_record(connection)
            self._validate_v66_migration_record(connection)
            self._validate_v67_migration_record(connection)

    def _migrate_v53_to_v54(self, connection):
        for statement in V54_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V54_SCHEMA_STATEMENTS, V54_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V53_TO_V54_ID, SCHEMA_VERSION_V53, SCHEMA_VERSION_V54,
            MIGRATION_V53_TO_V54_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V54, "schema_version", SCHEMA_VERSION_V53))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v54 migration lost its schema boundary")

    @staticmethod
    def _validate_v54_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V53_TO_V54_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V53 or row["to_version"] != SCHEMA_VERSION_V54
                           or row["fingerprint"] != MIGRATION_V53_TO_V54_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v54 migration record")

    def _migrate_v54_to_v55(self, connection):
        for statement in V55_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V55_SCHEMA_STATEMENTS, V55_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V54_TO_V55_ID, SCHEMA_VERSION_V54, SCHEMA_VERSION_V55,
            MIGRATION_V54_TO_V55_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V55, "schema_version", SCHEMA_VERSION_V54))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v55 migration lost its schema boundary")

    @staticmethod
    def _validate_v55_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V54_TO_V55_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V54 or row["to_version"] != SCHEMA_VERSION_V55
                           or row["fingerprint"] != MIGRATION_V54_TO_V55_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v55 migration record")

    def _migrate_v55_to_v56(self, connection):
        for statement in V56_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V56_SCHEMA_STATEMENTS, V56_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V55_TO_V56_ID, SCHEMA_VERSION_V55, SCHEMA_VERSION_V56,
            MIGRATION_V55_TO_V56_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V56, "schema_version", SCHEMA_VERSION_V55))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v56 migration lost its schema boundary")

    @staticmethod
    def _validate_v56_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V55_TO_V56_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V55 or row["to_version"] != SCHEMA_VERSION_V56
                           or row["fingerprint"] != MIGRATION_V55_TO_V56_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v56 migration record")

    def _migrate_v56_to_v57(self, connection):
        for statement in V57_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V57_SCHEMA_STATEMENTS, V57_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V56_TO_V57_ID, SCHEMA_VERSION_V56, SCHEMA_VERSION_V57,
            MIGRATION_V56_TO_V57_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V57, "schema_version", SCHEMA_VERSION_V56))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v57 migration lost its schema boundary")

    @staticmethod
    def _validate_v57_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V56_TO_V57_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V56 or row["to_version"] != SCHEMA_VERSION_V57
                           or row["fingerprint"] != MIGRATION_V56_TO_V57_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v57 migration record")

    def _migrate_v57_to_v58(self, connection):
        for statement in V58_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V58_SCHEMA_STATEMENTS, V58_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V57_TO_V58_ID, SCHEMA_VERSION_V57, SCHEMA_VERSION_V58,
            MIGRATION_V57_TO_V58_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V58, "schema_version", SCHEMA_VERSION_V57))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v58 migration lost its schema boundary")

    @staticmethod
    def _validate_v58_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V57_TO_V58_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V57 or row["to_version"] != SCHEMA_VERSION_V58
                           or row["fingerprint"] != MIGRATION_V57_TO_V58_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v58 migration record")

    def _migrate_v58_to_v59(self, connection):
        for statement in V59_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V59_SCHEMA_STATEMENTS, V59_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V58_TO_V59_ID, SCHEMA_VERSION_V58, SCHEMA_VERSION_V59,
            MIGRATION_V58_TO_V59_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V59, "schema_version", SCHEMA_VERSION_V58))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v59 migration lost its schema boundary")

    @staticmethod
    def _validate_v59_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V58_TO_V59_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V58 or row["to_version"] != SCHEMA_VERSION_V59
                           or row["fingerprint"] != MIGRATION_V58_TO_V59_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v59 migration record")

    def _migrate_v59_to_v60(self, connection):
        for statement in V60_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V60_SCHEMA_STATEMENTS, V60_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V59_TO_V60_ID, SCHEMA_VERSION_V59, SCHEMA_VERSION_V60,
            MIGRATION_V59_TO_V60_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V60, "schema_version", SCHEMA_VERSION_V59))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v60 migration lost its schema boundary")

    @staticmethod
    def _validate_v60_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V59_TO_V60_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V59 or row["to_version"] != SCHEMA_VERSION_V60
                           or row["fingerprint"] != MIGRATION_V59_TO_V60_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v60 migration record")

    def _migrate_v60_to_v61(self, connection):
        for statement in V61_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V61_SCHEMA_STATEMENTS, V61_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V60_TO_V61_ID, SCHEMA_VERSION_V60, SCHEMA_VERSION_V61,
            MIGRATION_V60_TO_V61_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V61, "schema_version", SCHEMA_VERSION_V60))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v61 migration lost its schema boundary")

    @staticmethod
    def _validate_v61_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V60_TO_V61_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V60 or row["to_version"] != SCHEMA_VERSION_V61
                           or row["fingerprint"] != MIGRATION_V60_TO_V61_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v61 migration record")

    def _migrate_v61_to_v62(self, connection):
        for statement in V62_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V62_SCHEMA_STATEMENTS, V62_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V61_TO_V62_ID, SCHEMA_VERSION_V61, SCHEMA_VERSION_V62,
            MIGRATION_V61_TO_V62_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V62, "schema_version", SCHEMA_VERSION_V61))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v62 migration lost its schema boundary")

    @staticmethod
    def _validate_v62_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V61_TO_V62_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V61 or row["to_version"] != SCHEMA_VERSION_V62
                           or row["fingerprint"] != MIGRATION_V61_TO_V62_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v62 migration record")

    def _migrate_v62_to_v63(self, connection):
        for statement in V63_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V63_SCHEMA_STATEMENTS, V63_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V62_TO_V63_ID, SCHEMA_VERSION_V62, SCHEMA_VERSION_V63,
            MIGRATION_V62_TO_V63_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V63, "schema_version", SCHEMA_VERSION_V62))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v63 migration lost its schema boundary")

    @staticmethod
    def _validate_v63_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V62_TO_V63_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V62 or row["to_version"] != SCHEMA_VERSION_V63
                           or row["fingerprint"] != MIGRATION_V62_TO_V63_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v63 migration record")

    def _migrate_v63_to_v64(self, connection):
        for statement in V64_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V64_SCHEMA_STATEMENTS, V64_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V63_TO_V64_ID, SCHEMA_VERSION_V63, SCHEMA_VERSION_V64,
            MIGRATION_V63_TO_V64_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V64, "schema_version", SCHEMA_VERSION_V63))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v64 migration lost its schema boundary")

    @staticmethod
    def _validate_v64_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V63_TO_V64_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V63 or row["to_version"] != SCHEMA_VERSION_V64
                           or row["fingerprint"] != MIGRATION_V63_TO_V64_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v64 migration record")

    def _migrate_v64_to_v65(self, connection):
        for statement in V65_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V65_SCHEMA_STATEMENTS, V65_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V64_TO_V65_ID, SCHEMA_VERSION_V64, SCHEMA_VERSION_V65,
            MIGRATION_V64_TO_V65_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V65, "schema_version", SCHEMA_VERSION_V64))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v65 migration lost its schema boundary")

    @staticmethod
    def _validate_v65_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V64_TO_V65_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V64 or row["to_version"] != SCHEMA_VERSION_V65
                           or row["fingerprint"] != MIGRATION_V64_TO_V65_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v65 migration record")

    def _migrate_v65_to_v66(self, connection):
        for statement in V66_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V66_SCHEMA_STATEMENTS, V66_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V65_TO_V66_ID, SCHEMA_VERSION_V65, SCHEMA_VERSION_V66,
            MIGRATION_V65_TO_V66_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V66, "schema_version", SCHEMA_VERSION_V65))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v66 migration lost its schema boundary")

    @staticmethod
    def _validate_v66_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V65_TO_V66_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V65 or row["to_version"] != SCHEMA_VERSION_V66
                           or row["fingerprint"] != MIGRATION_V65_TO_V66_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v66 migration record")

    def _migrate_v66_to_v67(self, connection):
        for statement in V67_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V67_SCHEMA_STATEMENTS, V67_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V66_TO_V67_ID, SCHEMA_VERSION_V66, SCHEMA_VERSION_V67,
            MIGRATION_V66_TO_V67_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V67, "schema_version", SCHEMA_VERSION_V66))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v67 migration lost its schema boundary")

    @staticmethod
    def _validate_v67_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V66_TO_V67_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V66 or row["to_version"] != SCHEMA_VERSION_V67
                           or row["fingerprint"] != MIGRATION_V66_TO_V67_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v67 migration record")

    def _migrate_v52_to_v53(self, connection):
        for statement in V53_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V53_SCHEMA_STATEMENTS, V53_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V52_TO_V53_ID, SCHEMA_VERSION_V52, SCHEMA_VERSION_V53,
            MIGRATION_V52_TO_V53_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V53, "schema_version", SCHEMA_VERSION_V52))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v53 migration lost its schema boundary")

    @staticmethod
    def _validate_v53_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V52_TO_V53_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V52 or row["to_version"] != SCHEMA_VERSION_V53
                           or row["fingerprint"] != MIGRATION_V52_TO_V53_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v53 migration record")

    def _migrate_v49_to_v50(self, connection):
        for statement in V50_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V50_SCHEMA_STATEMENTS, V50_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V49_TO_V50_ID, SCHEMA_VERSION_V49, SCHEMA_VERSION_V50,
            MIGRATION_V49_TO_V50_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V50, "schema_version", SCHEMA_VERSION_V49))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v50 migration lost its schema boundary")

    @staticmethod
    def _validate_v50_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V49_TO_V50_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V49 or row["to_version"] != SCHEMA_VERSION_V50
                           or row["fingerprint"] != MIGRATION_V49_TO_V50_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v50 migration record")

    def _migrate_v50_to_v51(self, connection):
        for statement in V51_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V51_SCHEMA_STATEMENTS, V51_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V50_TO_V51_ID, SCHEMA_VERSION_V50, SCHEMA_VERSION_V51,
            MIGRATION_V50_TO_V51_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V51, "schema_version", SCHEMA_VERSION_V50))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v51 migration lost its schema boundary")

    @staticmethod
    def _validate_v51_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V50_TO_V51_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V50 or row["to_version"] != SCHEMA_VERSION_V51
                           or row["fingerprint"] != MIGRATION_V50_TO_V51_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v51 migration record")

    def _migrate_v51_to_v52(self, connection):
        for statement in V52_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V52_SCHEMA_STATEMENTS, V52_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V51_TO_V52_ID, SCHEMA_VERSION_V51, SCHEMA_VERSION_V52,
            MIGRATION_V51_TO_V52_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V52, "schema_version", SCHEMA_VERSION_V51))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v52 migration lost its schema boundary")

    @staticmethod
    def _validate_v52_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V51_TO_V52_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V51 or row["to_version"] != SCHEMA_VERSION_V52
                           or row["fingerprint"] != MIGRATION_V51_TO_V52_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v52 migration record")

    def _migrate_v48_to_v49(self, connection):
        for statement in V49_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V49_SCHEMA_STATEMENTS, V49_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V48_TO_V49_ID, SCHEMA_VERSION_V48, SCHEMA_VERSION_V49,
            MIGRATION_V48_TO_V49_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V49, "schema_version", SCHEMA_VERSION_V48))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v49 migration lost its schema boundary")

    @staticmethod
    def _validate_v49_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V48_TO_V49_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V48 or row["to_version"] != SCHEMA_VERSION_V49
                           or row["fingerprint"] != MIGRATION_V48_TO_V49_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v49 migration record")

    def _migrate_v47_to_v48(self, connection):
        for statement in V48_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V48_SCHEMA_STATEMENTS, V48_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V47_TO_V48_ID, SCHEMA_VERSION_V47, SCHEMA_VERSION_V48,
            MIGRATION_V47_TO_V48_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V48, "schema_version", SCHEMA_VERSION_V47))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v48 migration lost its schema boundary")

    @staticmethod
    def _validate_v48_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V47_TO_V48_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V47 or row["to_version"] != SCHEMA_VERSION_V48
                           or row["fingerprint"] != MIGRATION_V47_TO_V48_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v48 migration record")

    def _migrate_v41_to_v42(self, connection):
        for statement in V42_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V42_SCHEMA_STATEMENTS, V42_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V41_TO_V42_ID, SCHEMA_VERSION_V41, SCHEMA_VERSION_V42,
            MIGRATION_V41_TO_V42_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V42, "schema_version", SCHEMA_VERSION_V41))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v42 migration lost its schema boundary")

    @staticmethod
    def _validate_v42_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V41_TO_V42_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V41 or row["to_version"] != SCHEMA_VERSION_V42
                           or row["fingerprint"] != MIGRATION_V41_TO_V42_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v42 migration record")

    def _migrate_v42_to_v43(self, connection):
        for statement in V43_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V43_SCHEMA_STATEMENTS, V43_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V42_TO_V43_ID, SCHEMA_VERSION_V42, SCHEMA_VERSION_V43,
            MIGRATION_V42_TO_V43_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V43, "schema_version", SCHEMA_VERSION_V42))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v43 migration lost its schema boundary")

    @staticmethod
    def _validate_v43_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V42_TO_V43_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V42 or row["to_version"] != SCHEMA_VERSION_V43
                           or row["fingerprint"] != MIGRATION_V42_TO_V43_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v43 migration record")

    def _migrate_v43_to_v44(self, connection):
        for statement in V44_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V44_SCHEMA_STATEMENTS, V44_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V43_TO_V44_ID, SCHEMA_VERSION_V43, SCHEMA_VERSION_V44,
            MIGRATION_V43_TO_V44_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V44, "schema_version", SCHEMA_VERSION_V43))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v44 migration lost its schema boundary")

    @staticmethod
    def _validate_v44_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V43_TO_V44_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V43 or row["to_version"] != SCHEMA_VERSION_V44
                           or row["fingerprint"] != MIGRATION_V43_TO_V44_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v44 migration record")

    def _migrate_v44_to_v45(self, connection):
        for statement in V45_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V45_SCHEMA_STATEMENTS, V45_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V44_TO_V45_ID, SCHEMA_VERSION_V44, SCHEMA_VERSION_V45,
            MIGRATION_V44_TO_V45_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V45, "schema_version", SCHEMA_VERSION_V44))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v45 migration lost its schema boundary")

    @staticmethod
    def _validate_v45_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V44_TO_V45_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V44 or row["to_version"] != SCHEMA_VERSION_V45
                           or row["fingerprint"] != MIGRATION_V44_TO_V45_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v45 migration record")
        # The append-only guarantee *is* these triggers, so a root without them is
        # not a complete v45 root -- same reasoning as the recall triggers.
        for name in PERSONA_CRAFT_TRIGGERS:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                                         (name,)).fetchone()
            if trigger is None:
                raise StorageOpenError("V2 persona draft history is not append-only")

    def _migrate_v45_to_v46(self, connection):
        for statement in V46_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V46_SCHEMA_STATEMENTS, V46_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V45_TO_V46_ID, SCHEMA_VERSION_V45, SCHEMA_VERSION_V46,
            MIGRATION_V45_TO_V46_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V46, "schema_version", SCHEMA_VERSION_V45))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v46 migration lost its schema boundary")

    @staticmethod
    def _validate_v46_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V45_TO_V46_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V45 or row["to_version"] != SCHEMA_VERSION_V46
                           or row["fingerprint"] != MIGRATION_V45_TO_V46_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v46 migration record")

    def _migrate_v46_to_v47(self, connection):
        for statement in V47_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V47_SCHEMA_STATEMENTS, V47_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V46_TO_V47_ID, SCHEMA_VERSION_V46, SCHEMA_VERSION_V47,
            MIGRATION_V46_TO_V47_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V47, "schema_version", SCHEMA_VERSION_V46))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v47 migration lost its schema boundary")

    @staticmethod
    def _validate_v47_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                 (MIGRATION_V46_TO_V47_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V46 or row["to_version"] != SCHEMA_VERSION_V47
                           or row["fingerprint"] != MIGRATION_V46_TO_V47_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v47 migration record")

    def _migrate_v40_to_v41(self, connection):
        for statement in V41_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V41_SCHEMA_STATEMENTS, V41_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V40_TO_V41_ID, SCHEMA_VERSION_V40, SCHEMA_VERSION_V41,
            MIGRATION_V40_TO_V41_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V41, "schema_version", SCHEMA_VERSION_V40))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v41 migration lost its schema boundary")

    @staticmethod
    def _validate_v41_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V40_TO_V41_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V40 or row["to_version"] != SCHEMA_VERSION_V41
                           or row["fingerprint"] != MIGRATION_V40_TO_V41_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v41 migration record")

    def _migrate_v39_to_v40(self, connection):
        for statement in V40_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V40_SCHEMA_STATEMENTS, V40_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V39_TO_V40_ID, SCHEMA_VERSION_V39, SCHEMA_VERSION_V40,
            MIGRATION_V39_TO_V40_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V40, "schema_version", SCHEMA_VERSION_V39))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v40 migration lost its schema boundary")

    @staticmethod
    def _validate_v40_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V39_TO_V40_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V39 or row["to_version"] != SCHEMA_VERSION_V40
                           or row["fingerprint"] != MIGRATION_V39_TO_V40_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v40 migration record")

    def _migrate_v38_to_v39(self, connection):
        for statement in V39_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V39_SCHEMA_STATEMENTS, V39_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V38_TO_V39_ID, SCHEMA_VERSION_V38, SCHEMA_VERSION_V39,
            MIGRATION_V38_TO_V39_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V39, "schema_version", SCHEMA_VERSION_V38))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v39 migration lost its schema boundary")

    @staticmethod
    def _validate_v39_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V38_TO_V39_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V38 or row["to_version"] != SCHEMA_VERSION_V39
                           or row["fingerprint"] != MIGRATION_V38_TO_V39_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v39 migration record")

    def _migrate_v37_to_v38(self, connection):
        for statement in V38_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V38_SCHEMA_STATEMENTS, V38_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V37_TO_V38_ID, SCHEMA_VERSION_V37, SCHEMA_VERSION_V38,
            MIGRATION_V37_TO_V38_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V38, "schema_version", SCHEMA_VERSION_V37))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v38 migration lost its schema boundary")

    @staticmethod
    def _validate_v38_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V37_TO_V38_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V37 or row["to_version"] != SCHEMA_VERSION_V38
                           or row["fingerprint"] != MIGRATION_V37_TO_V38_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v38 migration record")

    def _migrate_v36_to_v37(self, connection):
        for statement in V37_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V37_SCHEMA_STATEMENTS, V37_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V36_TO_V37_ID, SCHEMA_VERSION_V36, SCHEMA_VERSION_V37,
            MIGRATION_V36_TO_V37_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V37, "schema_version", SCHEMA_VERSION_V36))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v37 migration lost its schema boundary")

    @staticmethod
    def _validate_v37_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V36_TO_V37_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V36 or row["to_version"] != SCHEMA_VERSION_V37
                           or row["fingerprint"] != MIGRATION_V36_TO_V37_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v37 migration record")

    def _migrate_v35_to_v36(self, connection):
        for statement in V36_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V36_SCHEMA_STATEMENTS, V36_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V35_TO_V36_ID, SCHEMA_VERSION_V35, SCHEMA_VERSION_V36,
            MIGRATION_V35_TO_V36_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V36, "schema_version", SCHEMA_VERSION_V35))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v36 migration lost its schema boundary")

    @staticmethod
    def _validate_v36_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V35_TO_V36_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V35 or row["to_version"] != SCHEMA_VERSION_V36
                           or row["fingerprint"] != MIGRATION_V35_TO_V36_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v36 migration record")

    def _migrate_v34_to_v35(self, connection):
        for statement in V35_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V35_SCHEMA_STATEMENTS, V35_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V34_TO_V35_ID, SCHEMA_VERSION_V34, SCHEMA_VERSION_V35,
            MIGRATION_V34_TO_V35_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V35, "schema_version", SCHEMA_VERSION_V34))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v35 migration lost its schema boundary")

    @staticmethod
    def _validate_v35_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V34_TO_V35_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V34 or row["to_version"] != SCHEMA_VERSION_V35
                           or row["fingerprint"] != MIGRATION_V34_TO_V35_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v35 migration record")

    def _migrate_v33_to_v34(self, connection):
        for statement in V34_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V34_SCHEMA_STATEMENTS, V34_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V33_TO_V34_ID, SCHEMA_VERSION_V33, SCHEMA_VERSION_V34,
            MIGRATION_V33_TO_V34_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V34, "schema_version", SCHEMA_VERSION_V33))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v34 migration lost its schema boundary")

    @staticmethod
    def _validate_v34_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V33_TO_V34_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V33 or row["to_version"] != SCHEMA_VERSION_V34
                           or row["fingerprint"] != MIGRATION_V33_TO_V34_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v34 migration record")

    def _migrate_v32_to_v33(self, connection):
        for statement in V33_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V33_SCHEMA_STATEMENTS, V33_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V32_TO_V33_ID, SCHEMA_VERSION_V32, SCHEMA_VERSION_V33,
            MIGRATION_V32_TO_V33_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V33, "schema_version", SCHEMA_VERSION_V32))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v33 migration lost its schema boundary")

    @staticmethod
    def _validate_v33_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V32_TO_V33_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V32 or row["to_version"] != SCHEMA_VERSION_V33
                           or row["fingerprint"] != MIGRATION_V32_TO_V33_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v33 migration record")

    def _migrate_v31_to_v32(self, connection):
        for statement in V32_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V32_SCHEMA_STATEMENTS, V32_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V31_TO_V32_ID, SCHEMA_VERSION_V31, SCHEMA_VERSION_V32,
            MIGRATION_V31_TO_V32_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V32, "schema_version", SCHEMA_VERSION_V31))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v32 migration lost its schema boundary")

    @staticmethod
    def _validate_v32_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V31_TO_V32_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V31 or row["to_version"] != SCHEMA_VERSION_V32
                           or row["fingerprint"] != MIGRATION_V31_TO_V32_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v32 migration record")

    def _migrate_v30_to_v31(self, connection):
        for statement in V31_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V31_SCHEMA_STATEMENTS, V31_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V30_TO_V31_ID, SCHEMA_VERSION_V30, SCHEMA_VERSION_V31,
            MIGRATION_V30_TO_V31_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V31, "schema_version", SCHEMA_VERSION_V30))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v31 migration lost its schema boundary")

    @staticmethod
    def _validate_v31_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V30_TO_V31_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V30 or row["to_version"] != SCHEMA_VERSION_V31
                           or row["fingerprint"] != MIGRATION_V30_TO_V31_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v31 migration record")

    def _migrate_v29_to_v30(self, connection):
        for statement in V30_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V30_SCHEMA_STATEMENTS, V30_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V29_TO_V30_ID, SCHEMA_VERSION_V29, SCHEMA_VERSION_V30,
            MIGRATION_V29_TO_V30_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V30, "schema_version", SCHEMA_VERSION_V29))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v30 migration lost its schema boundary")

    @staticmethod
    def _validate_v30_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V29_TO_V30_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V29 or row["to_version"] != SCHEMA_VERSION_V30
                           or row["fingerprint"] != MIGRATION_V29_TO_V30_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v30 migration record")

    def _migrate_v28_to_v29(self, connection):
        for statement in V29_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V29_SCHEMA_STATEMENTS, V29_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V28_TO_V29_ID, SCHEMA_VERSION_V28, SCHEMA_VERSION_V29,
            MIGRATION_V28_TO_V29_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V29, "schema_version", SCHEMA_VERSION_V28))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v29 migration lost its schema boundary")

    @staticmethod
    def _validate_v29_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V28_TO_V29_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V28 or row["to_version"] != SCHEMA_VERSION_V29
                           or row["fingerprint"] != MIGRATION_V28_TO_V29_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v29 migration record")

    def _migrate_v27_to_v28(self, connection):
        for statement in V28_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V28_SCHEMA_STATEMENTS, V28_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V27_TO_V28_ID, SCHEMA_VERSION_V27, SCHEMA_VERSION_V28,
            MIGRATION_V27_TO_V28_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V28, "schema_version", SCHEMA_VERSION_V27))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v28 migration lost its schema boundary")

    @staticmethod
    def _validate_v28_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V27_TO_V28_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V27 or row["to_version"] != SCHEMA_VERSION_V28
                           or row["fingerprint"] != MIGRATION_V27_TO_V28_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v28 migration record")

    def _migrate_v26_to_v27(self, connection):
        for statement in V27_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V27_SCHEMA_STATEMENTS, V27_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V26_TO_V27_ID, SCHEMA_VERSION_V26, SCHEMA_VERSION_V27,
            MIGRATION_V26_TO_V27_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V27, "schema_version", SCHEMA_VERSION_V26))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v27 migration lost its schema boundary")

    @staticmethod
    def _validate_v27_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V26_TO_V27_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V26 or row["to_version"] != SCHEMA_VERSION_V27
                           or row["fingerprint"] != MIGRATION_V26_TO_V27_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v27 migration record")
        for statement in V27_MIGRATION_STATEMENTS:
            if statement.startswith("CREATE TRIGGER "):
                trigger = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                                             (statement.split()[2],)).fetchone()
                if trigger is None or trigger[0] != statement:
                    raise StorageOpenError("V2 reflection input reservations are incomplete")

    def _migrate_v25_to_v26(self, connection):
        for statement in V26_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V26_SCHEMA_STATEMENTS, V26_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V25_TO_V26_ID, SCHEMA_VERSION_V25, SCHEMA_VERSION_V26,
            MIGRATION_V25_TO_V26_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V26, "schema_version", SCHEMA_VERSION_V25))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 v26 migration lost its schema boundary")

    @staticmethod
    def _validate_v26_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V25_TO_V26_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V25 or row["to_version"] != SCHEMA_VERSION_V26
                           or row["fingerprint"] != MIGRATION_V25_TO_V26_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v26 migration record")
        # Unlike optional cache tables, these triggers certify completeness.
        for statement in V26_MIGRATION_STATEMENTS:
            if statement.startswith("CREATE TRIGGER "):
                name = statement.split()[2]
                trigger = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)).fetchone()
                if trigger is None or trigger[0] != statement:
                    raise StorageOpenError("V2 memory recall change tracking is incomplete")

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        for statement in V2_MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id, from_version, to_version, fingerprint, applied_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                MIGRATION_V1_TO_V2_ID,
                SCHEMA_VERSION_V1,
                SCHEMA_VERSION_V2,
                MIGRATION_V1_TO_V2_FINGERPRINT,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        updated = connection.execute(
            "UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
            (SCHEMA_VERSION_V2, "schema_version", SCHEMA_VERSION_V1),
        )
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v2_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM schema_migrations WHERE migration_id = ?",
            (MIGRATION_V1_TO_V2_ID,),
        ).fetchone()
        if row is None:
            raise StorageOpenError("V2 data root has no trusted schema migration record")
        if (
            row["from_version"] != SCHEMA_VERSION_V1
            or row["to_version"] != SCHEMA_VERSION_V2
            or row["fingerprint"] != MIGRATION_V1_TO_V2_FINGERPRINT
        ):
            raise StorageOpenError("V2 data root has an untrusted schema migration record")

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        for statement in V3_MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)",
            (MIGRATION_V2_TO_V3_ID, SCHEMA_VERSION_V2, SCHEMA_VERSION_V3,
             MIGRATION_V2_TO_V3_FINGERPRINT, datetime.now(timezone.utc).isoformat()),
        )
        updated = connection.execute(
            "UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
            (SCHEMA_VERSION_V3, "schema_version", SCHEMA_VERSION_V2),
        )
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v3_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V2_TO_V3_ID,),
        ).fetchone()
        if row is None or (
            row["from_version"] != SCHEMA_VERSION_V2
            or row["to_version"] != SCHEMA_VERSION_V3
            or row["fingerprint"] != MIGRATION_V2_TO_V3_FINGERPRINT
        ):
            raise StorageOpenError("V2 data root has an untrusted v3 migration record")
        metadata = connection.execute("SELECT revision FROM identity_policy_metadata WHERE singleton = 1").fetchone()
        if metadata is None or metadata["revision"] < 1:
            raise StorageOpenError("V2 identity policy metadata is missing")

    def _migrate_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        for statement in V4_MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)",
            (MIGRATION_V3_TO_V4_ID, SCHEMA_VERSION_V3, SCHEMA_VERSION_V4,
             MIGRATION_V3_TO_V4_FINGERPRINT, datetime.now(timezone.utc).isoformat()),
        )
        updated = connection.execute(
            "UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
            (SCHEMA_VERSION_V4, "schema_version", SCHEMA_VERSION_V3),
        )
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v4_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V3_TO_V4_ID,),
        ).fetchone()
        if row is None or (
            row["from_version"] != SCHEMA_VERSION_V3 or row["to_version"] != SCHEMA_VERSION_V4
            or row["fingerprint"] != MIGRATION_V3_TO_V4_FINGERPRINT
        ):
            raise StorageOpenError("V2 data root has an untrusted v4 migration record")

    def _migrate_v4_to_v5(self, connection: sqlite3.Connection) -> None:
        for statement in V5_MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)",
            (MIGRATION_V4_TO_V5_ID, SCHEMA_VERSION_V4, SCHEMA_VERSION_V5,
             MIGRATION_V4_TO_V5_FINGERPRINT, datetime.now(timezone.utc).isoformat()),
        )
        updated = connection.execute(
            "UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
            (SCHEMA_VERSION_V5, "schema_version", SCHEMA_VERSION_V4),
        )
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v5_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V4_TO_V5_ID,),
        ).fetchone()
        if row is None or (
            row["from_version"] != SCHEMA_VERSION_V4 or row["to_version"] != SCHEMA_VERSION_V5
            or row["fingerprint"] != MIGRATION_V4_TO_V5_FINGERPRINT
        ):
            raise StorageOpenError("V2 data root has an untrusted v5 migration record")
        metadata = connection.execute("SELECT revision FROM object_policy_metadata WHERE singleton = 1").fetchone()
        if metadata is None or metadata["revision"] < 1:
            raise StorageOpenError("V2 object policy metadata is missing")

    def _migrate_v5_to_v6(self, connection: sqlite3.Connection) -> None:
        for table, statement in V6_TABLE_STATEMENTS.items():
            temporary = "scope_v6_" + table
            if temporary in _existing_tables(connection):
                raise StorageOpenError("V2 scope migration has an unexpected temporary table")
            connection.execute(statement.replace(table + " (", temporary + " (", 1))
            columns = ", ".join('"' + row["name"] + '"' for row in connection.execute(f'PRAGMA table_info("{table}")'))
            connection.execute(f'INSERT INTO "{temporary}" ({columns}) SELECT {columns} FROM "{table}"')
            connection.execute(f'DROP TABLE "{table}"')
            connection.execute(f'ALTER TABLE "{temporary}" RENAME TO "{table}"')
        for statement in V6_INDEX_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V6_SCHEMA_STATEMENTS, V6_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V5_TO_V6_ID, SCHEMA_VERSION_V5, SCHEMA_VERSION_V6,
            MIGRATION_V5_TO_V6_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V6, "schema_version", SCHEMA_VERSION_V5))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v6_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V5_TO_V6_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V5 or row["to_version"] != SCHEMA_VERSION_V6
                           or row["fingerprint"] != MIGRATION_V5_TO_V6_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v6 migration record")

    def _migrate_v6_to_v7(self, connection: sqlite3.Connection) -> None:
        for statement in V7_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V7_SCHEMA_STATEMENTS, V7_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V6_TO_V7_ID, SCHEMA_VERSION_V6, SCHEMA_VERSION_V7,
            MIGRATION_V6_TO_V7_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V7, "schema_version", SCHEMA_VERSION_V6))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v7_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V6_TO_V7_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V6 or row["to_version"] != SCHEMA_VERSION_V7
                           or row["fingerprint"] != MIGRATION_V6_TO_V7_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v7 migration record")

    def _migrate_v7_to_v8(self, connection: sqlite3.Connection) -> None:
        for statement in V8_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V8_SCHEMA_STATEMENTS, V8_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V7_TO_V8_ID, SCHEMA_VERSION_V7, SCHEMA_VERSION_V8,
            MIGRATION_V7_TO_V8_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V8, "schema_version", SCHEMA_VERSION_V7))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v8_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V7_TO_V8_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V7 or row["to_version"] != SCHEMA_VERSION_V8
                           or row["fingerprint"] != MIGRATION_V7_TO_V8_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v8 migration record")

    def _migrate_v8_to_v9(self, connection: sqlite3.Connection) -> None:
        for statement in V9_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V9_SCHEMA_STATEMENTS, V9_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V8_TO_V9_ID, SCHEMA_VERSION_V8, SCHEMA_VERSION_V9,
            MIGRATION_V8_TO_V9_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V9, "schema_version", SCHEMA_VERSION_V8))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v9_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V8_TO_V9_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V8 or row["to_version"] != SCHEMA_VERSION_V9
                           or row["fingerprint"] != MIGRATION_V8_TO_V9_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v9 migration record")

    def _migrate_v9_to_v10(self, connection: sqlite3.Connection) -> None:
        for statement in V10_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V10_SCHEMA_STATEMENTS, V10_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V9_TO_V10_ID, SCHEMA_VERSION_V9, SCHEMA_VERSION_V10,
            MIGRATION_V9_TO_V10_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V10, "schema_version", SCHEMA_VERSION_V9))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v10_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V9_TO_V10_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V9 or row["to_version"] != SCHEMA_VERSION_V10
                           or row["fingerprint"] != MIGRATION_V9_TO_V10_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v10 migration record")

    def _migrate_v10_to_v11(self, connection: sqlite3.Connection) -> None:
        for statement in V11_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V11_SCHEMA_STATEMENTS, V11_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V10_TO_V11_ID, SCHEMA_VERSION_V10, SCHEMA_VERSION_V11,
            MIGRATION_V10_TO_V11_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V11, "schema_version", SCHEMA_VERSION_V10))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v11_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V10_TO_V11_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V10 or row["to_version"] != SCHEMA_VERSION_V11
                           or row["fingerprint"] != MIGRATION_V10_TO_V11_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v11 migration record")

    def _migrate_v11_to_v12(self, connection: sqlite3.Connection) -> None:
        for statement in V12_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V12_SCHEMA_STATEMENTS, V12_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V11_TO_V12_ID, SCHEMA_VERSION_V11, SCHEMA_VERSION_V12,
            MIGRATION_V11_TO_V12_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V12, "schema_version", SCHEMA_VERSION_V11))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v12_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V11_TO_V12_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V11 or row["to_version"] != SCHEMA_VERSION_V12
                           or row["fingerprint"] != MIGRATION_V11_TO_V12_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v12 migration record")

    def _migrate_v12_to_v13(self, connection: sqlite3.Connection) -> None:
        for statement in V13_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V13_SCHEMA_STATEMENTS, V13_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V12_TO_V13_ID, SCHEMA_VERSION_V12, SCHEMA_VERSION_V13,
            MIGRATION_V12_TO_V13_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V13, "schema_version", SCHEMA_VERSION_V12))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v13_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V12_TO_V13_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V12 or row["to_version"] != SCHEMA_VERSION_V13
                           or row["fingerprint"] != MIGRATION_V12_TO_V13_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v13 migration record")

    def _migrate_v13_to_v14(self, connection: sqlite3.Connection) -> None:
        for statement in V14_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V14_SCHEMA_STATEMENTS, V14_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V13_TO_V14_ID, SCHEMA_VERSION_V13, SCHEMA_VERSION_V14,
            MIGRATION_V13_TO_V14_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V14, "schema_version", SCHEMA_VERSION_V13))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v14_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V13_TO_V14_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V13 or row["to_version"] != SCHEMA_VERSION_V14
                           or row["fingerprint"] != MIGRATION_V13_TO_V14_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v14 migration record")

    def _migrate_v14_to_v15(self, connection: sqlite3.Connection) -> None:
        for statement in V15_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V15_SCHEMA_STATEMENTS, V15_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V14_TO_V15_ID, SCHEMA_VERSION_V14, SCHEMA_VERSION_V15,
            MIGRATION_V14_TO_V15_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V15, "schema_version", SCHEMA_VERSION_V14))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v15_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V14_TO_V15_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V14 or row["to_version"] != SCHEMA_VERSION_V15
                           or row["fingerprint"] != MIGRATION_V14_TO_V15_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v15 migration record")

    def _migrate_v15_to_v16(self, connection: sqlite3.Connection) -> None:
        for statement in V16_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V16_SCHEMA_STATEMENTS, V16_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V15_TO_V16_ID, SCHEMA_VERSION_V15, SCHEMA_VERSION_V16,
            MIGRATION_V15_TO_V16_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V16, "schema_version", SCHEMA_VERSION_V15))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v16_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V15_TO_V16_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V15 or row["to_version"] != SCHEMA_VERSION_V16
                           or row["fingerprint"] != MIGRATION_V15_TO_V16_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v16 migration record")

    def _migrate_v16_to_v17(self, connection: sqlite3.Connection) -> None:
        for statement in V17_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V17_SCHEMA_STATEMENTS, V17_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V16_TO_V17_ID, SCHEMA_VERSION_V16, SCHEMA_VERSION_V17,
            MIGRATION_V16_TO_V17_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V17, "schema_version", SCHEMA_VERSION_V16))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v17_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V16_TO_V17_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V16 or row["to_version"] != SCHEMA_VERSION_V17
                           or row["fingerprint"] != MIGRATION_V16_TO_V17_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v17 migration record")

    def _migrate_v17_to_v18(self, connection: sqlite3.Connection) -> None:
        for statement in V18_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V18_SCHEMA_STATEMENTS, V18_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V17_TO_V18_ID, SCHEMA_VERSION_V17, SCHEMA_VERSION_V18,
            MIGRATION_V17_TO_V18_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V18, "schema_version", SCHEMA_VERSION_V17))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v18_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V17_TO_V18_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V17 or row["to_version"] != SCHEMA_VERSION_V18
                           or row["fingerprint"] != MIGRATION_V17_TO_V18_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v18 migration record")

    def _migrate_v18_to_v19(self, connection: sqlite3.Connection) -> None:
        for statement in V19_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V19_SCHEMA_STATEMENTS, V19_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V18_TO_V19_ID, SCHEMA_VERSION_V18, SCHEMA_VERSION_V19,
            MIGRATION_V18_TO_V19_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V19, "schema_version", SCHEMA_VERSION_V18))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v19_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V18_TO_V19_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V18 or row["to_version"] != SCHEMA_VERSION_V19
                           or row["fingerprint"] != MIGRATION_V18_TO_V19_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v19 migration record")

    def _migrate_v19_to_v20(self, connection: sqlite3.Connection) -> None:
        for statement in V20_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V20_SCHEMA_STATEMENTS, V20_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V19_TO_V20_ID, SCHEMA_VERSION_V19, SCHEMA_VERSION_V20,
            MIGRATION_V19_TO_V20_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V20, "schema_version", SCHEMA_VERSION_V19))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v20_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V19_TO_V20_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V19 or row["to_version"] != SCHEMA_VERSION_V20
                           or row["fingerprint"] != MIGRATION_V19_TO_V20_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v20 migration record")

    def _migrate_v20_to_v21(self, connection: sqlite3.Connection) -> None:
        for statement in V21_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V21_SCHEMA_STATEMENTS, V21_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V20_TO_V21_ID, SCHEMA_VERSION_V20, SCHEMA_VERSION_V21,
            MIGRATION_V20_TO_V21_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V21, "schema_version", SCHEMA_VERSION_V20))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 data root has an incompatible schema version")

    @staticmethod
    def _validate_v21_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V20_TO_V21_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V20 or row["to_version"] != SCHEMA_VERSION_V21
                           or row["fingerprint"] != MIGRATION_V20_TO_V21_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v21 migration record")

    def _migrate_v21_to_v22(self, connection: sqlite3.Connection) -> None:
        for statement in V22_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V22_SCHEMA_STATEMENTS, V22_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V21_TO_V22_ID, SCHEMA_VERSION_V21, SCHEMA_VERSION_V22,
            MIGRATION_V21_TO_V22_FINGERPRINT, datetime.now(timezone.utc).isoformat(),
        ))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V22, "schema_version", SCHEMA_VERSION_V21))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v22_migration_record(connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V21_TO_V22_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V21 or row["to_version"] != SCHEMA_VERSION_V22
                           or row["fingerprint"] != MIGRATION_V21_TO_V22_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v22 migration record")

    def _migrate_v22_to_v23(self, connection):
        from .activity_history import backfill
        for statement in V23_MIGRATION_STATEMENTS:
            connection.execute(statement)
        backfill(connection)
        _validate_table_shapes(connection, V23_SCHEMA_STATEMENTS, V23_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V22_TO_V23_ID, SCHEMA_VERSION_V22, SCHEMA_VERSION_V23,
            MIGRATION_V22_TO_V23_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V23, "schema_version", SCHEMA_VERSION_V22))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v23_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V22_TO_V23_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V22 or row["to_version"] != SCHEMA_VERSION_V23
                           or row["fingerprint"] != MIGRATION_V22_TO_V23_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v23 migration record")

    def _migrate_v23_to_v24(self, connection):
        for statement in V24_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V24_SCHEMA_STATEMENTS, V24_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V23_TO_V24_ID, SCHEMA_VERSION_V23, SCHEMA_VERSION_V24,
            MIGRATION_V23_TO_V24_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        updated = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V24, "schema_version", SCHEMA_VERSION_V23))
        if updated.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during migration")

    @staticmethod
    def _validate_v24_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V23_TO_V24_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V23 or row["to_version"] != SCHEMA_VERSION_V24
                           or row["fingerprint"] != MIGRATION_V23_TO_V24_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v24 migration record")

    def _migrate_v24_to_v25(self, connection):
        for statement in V25_MIGRATION_STATEMENTS:
            connection.execute(statement)
        _validate_table_shapes(connection, V25_SCHEMA_STATEMENTS, V25_REQUIRED_TABLES)
        _validate_integrity(connection)
        connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
            MIGRATION_V24_TO_V25_ID, SCHEMA_VERSION_V24, SCHEMA_VERSION_V25,
            MIGRATION_V24_TO_V25_FINGERPRINT, datetime.now(timezone.utc).isoformat()))
        changed = connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ? AND value = ?",
                                     (SCHEMA_VERSION_V25, "schema_version", SCHEMA_VERSION_V24))
        if changed.rowcount != 1:
            raise StorageOpenError("V2 schema version changed during v25 migration")

    @staticmethod
    def _validate_v25_migration_record(connection):
        row = connection.execute("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V24_TO_V25_ID,)).fetchone()
        if row is None or (row["from_version"] != SCHEMA_VERSION_V24 or row["to_version"] != SCHEMA_VERSION_V25
                           or row["fingerprint"] != MIGRATION_V24_TO_V25_FINGERPRINT):
            raise StorageOpenError("V2 data root has an untrusted v25 migration record")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageOpenError("V2 storage runtime is closed")
        return self._connection


def _existing_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {row["name"] for row in rows}


def _validate_table_shapes(
    connection: sqlite3.Connection,
    statements: tuple[str, ...],
    tables: frozenset[str],
) -> None:
    # Let SQLite interpret its DDL so column checks cannot drift from the schema.
    expected = sqlite3.connect(":memory:")
    try:
        for statement in statements:
            expected.execute(statement)
        for table in sorted(tables):
            if table in ("model_call_failures", "inbound_no_replies"):
                actual_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                                                (table,)).fetchone()
                expected_sql = expected.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                                                (table,)).fetchone()
                if actual_sql is None or actual_sql[0] != expected_sql[0]:
                    raise StorageOpenError(f"V2 table constraint does not match: {table}")
            for pragma in ("table_info", "foreign_key_list"):
                query = f'PRAGMA {pragma}("{table}")'
                actual_rows = [tuple(row) for row in connection.execute(query)]
                if actual_rows != expected.execute(query).fetchall():
                    raise StorageOpenError(f"V2 table structure does not match: {table}")
            if _index_shapes(connection, table) != _index_shapes(expected, table):
                raise StorageOpenError(f"V2 table indexes do not match: {table}")
    finally:
        expected.close()


def _index_shapes(connection: sqlite3.Connection, table: str) -> list[tuple]:
    shapes = []
    for row in connection.execute(f'PRAGMA index_list("{table}")'):
        # The index name comes from SQLite metadata and is quoted as an identifier.
        quoted_name = row[1].replace('"', '""')
        columns = tuple(tuple(info) for info in connection.execute(f'PRAGMA index_xinfo("{quoted_name}")'))
        shapes.append((row[2], row[3], row[4], columns))
    return sorted(shapes)


def _validate_integrity(connection: sqlite3.Connection) -> None:
    if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
        raise StorageOpenError("V2 SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageOpenError("V2 SQLite contains broken references")


def _validate_data_root(value: Path) -> Path:
    root = value.resolve()
    if not root.name:
        raise StoragePathError("data_root must name a dedicated V2 directory")
    for ancestor in (root, *root.parents):
        if ancestor.name.casefold() not in LEGACY_PLUGIN_DIRECTORY_NAMES:
            continue
        relative = root.relative_to(ancestor).parts
        # The public plugin name is shared with 6.x. Its isolated upgrade child
        # and a fresh installation's profiles/runtime/installations must remain
        # usable after takeover. Old data itself, other legacy plugins and
        # arbitrary children stay read-only.
        if (ancestor.name.casefold() != "astrbot_plugin_private_companion" or not relative
                or relative[0].casefold() not in ("companion-7", "profiles", "runtime", "installations")):
            raise StoragePathError("V2 must not use a V1 plugin directory as its data root")
        if any((ancestor / marker).is_file() for marker in ("main.py", "pyproject.toml")):
            raise StoragePathError("V2 runtime must not be inside the plugin code directory")
        boundary = ancestor / relative[0]
        checked = (root, *(parent for parent in root.parents if parent.is_relative_to(boundary)))
        owned = any((ancestor / marker).is_file() for marker in (
            ".companion-v2-storage-owner.json", ".companion-v2-storage-location.json"))
        if relative[0].casefold() in ("profiles", "runtime", "installations") and not owned:
            checked = (*checked, ancestor)
        if any((folder / marker).exists() for folder in checked
               for marker in ("companions.json", "companions.db", ".storage-backend-state.json")):
            raise StoragePathError("V2 runtime must stay outside legacy user data")
    return root
