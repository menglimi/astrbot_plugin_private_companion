# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Collection, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from .backend_base import StoreBackendBase
from ..logging_util import get_module_logger

logger = get_module_logger(__name__)

_SCHEMA_VERSION = 2
_SQLITE_INTEGER_MAX = (1 << 63) - 1
_MAX_SECTION_REVISION = _SQLITE_INTEGER_MAX - 1
_SECTION_COLUMNS = (
    "section_name",
    "payload_json",
    "updated_at",
    "checksum",
    "schema_version",
    "revision",
    "is_deleted",
)
_V1_SECTION_COLUMNS = _SECTION_COLUMNS[:5]
_SCHEMA_LOCKS_GUARD = threading.Lock()
_SCHEMA_LOCKS: dict[str, threading.RLock] = {}


class SqliteStoreNotInitializedError(RuntimeError):
    """The database exists, but no store snapshot has been committed yet."""


class SqliteSchemaError(RuntimeError):
    """The database schema or persisted metadata is inconsistent."""


class SqliteUnsupportedSchemaError(SqliteSchemaError):
    """The database was created by a newer unsupported storage implementation."""


class SqliteRevisionConflictError(RuntimeError):
    """A partial write conflicts with the persisted section revision."""


def _shared_schema_lock(path: Path) -> threading.RLock:
    try:
        marker = str(path.resolve()).casefold()
    except (OSError, RuntimeError):
        marker = str(path).casefold()
    with _SCHEMA_LOCKS_GUARD:
        lock = _SCHEMA_LOCKS.get(marker)
        if lock is None:
            lock = threading.RLock()
            _SCHEMA_LOCKS[marker] = lock
        return lock


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _payload_checksum(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _strict_json_value(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is not allowed: {key}")
        result[key] = value
    return result


def _section_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise ValueError(
            "SQLite section name must be a non-empty string of at most 256 characters"
        )
    return value


def _revision(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_SECTION_REVISION
    ):
        raise ValueError(
            "SQLite section revision must be a positive integer within SQLite range"
        )
    return value


class SqliteStoreBackend(StoreBackendBase):
    def __init__(
        self,
        db_path: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
    ) -> None:
        self.db_path = Path(db_path)
        self.ensure_defaults = ensure_defaults
        self.new_store = new_store
        self._schema_lock = _shared_schema_lock(self.db_path)

    def backend_name(self) -> str:
        return "sqlite"

    def exists(self) -> bool:
        return self.db_path.exists()

    @staticmethod
    def _create_sections_table(connection: sqlite3.Connection, table_name: str) -> None:
        if table_name not in {"store_sections", "store_sections_v2_migration"}:
            raise ValueError("Unexpected SQLite migration table name")
        connection.execute(
            f"CREATE TABLE {table_name} ("
            "section_name TEXT NOT NULL PRIMARY KEY,"
            "payload_json TEXT NOT NULL,"
            "updated_at REAL NOT NULL,"
            "checksum TEXT NOT NULL DEFAULT '',"
            "schema_version INTEGER NOT NULL DEFAULT 2,"
            "revision INTEGER NOT NULL DEFAULT 0,"
            "is_deleted INTEGER NOT NULL DEFAULT 0,"
            "CONSTRAINT store_sections_schema_v2 "
            "CHECK(typeof(schema_version)='integer' AND schema_version=2),"
            "CONSTRAINT store_sections_positive_revision "
            f"CHECK(typeof(revision)='integer' AND revision>0 AND revision<={_MAX_SECTION_REVISION}),"
            "CONSTRAINT store_sections_deleted_flag "
            "CHECK(typeof(is_deleted)='integer' AND is_deleted IN (0,1))"
            ")"
        )

    @staticmethod
    def _create_meta_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE store_meta ("
            "meta_key TEXT NOT NULL PRIMARY KEY,"
            "meta_value TEXT NOT NULL)"
        )

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    @staticmethod
    def _table_info(
        connection: sqlite3.Connection, table_name: str
    ) -> list[tuple[Any, ...]]:
        if table_name not in {
            "store_sections",
            "store_sections_v2_migration",
            "store_meta",
        }:
            raise ValueError("Unexpected SQLite table name")
        return list(connection.execute(f"PRAGMA table_info({table_name})"))

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in connection.execute(
                "SELECT meta_key,meta_value FROM store_meta"
            )
        }

    @staticmethod
    def _metadata_integer(metadata: Mapping[str, str], key: str) -> int:
        raw = metadata.get(key)
        try:
            value = int(raw) if raw is not None else 0
        except (TypeError, ValueError, OverflowError) as exc:
            raise SqliteSchemaError(f"SQLite store meta {key} is invalid") from exc
        if value <= 0 or value > _SQLITE_INTEGER_MAX:
            raise SqliteSchemaError(f"SQLite store meta {key} is invalid")
        return value

    def _validate_v1_schema(self, connection: sqlite3.Connection) -> None:
        tables = self._table_names(connection)
        if tables != {"store_sections"}:
            raise SqliteSchemaError("SQLite v1 store has unexpected tables")
        info = self._table_info(connection, "store_sections")
        names = tuple(str(row[1]) for row in info)
        if names != _V1_SECTION_COLUMNS:
            raise SqliteSchemaError("SQLite v1 store_sections columns are invalid")
        expected_types = ("TEXT", "TEXT", "REAL", "TEXT", "INTEGER")
        if tuple(str(row[2]).upper() for row in info) != expected_types:
            raise SqliteSchemaError("SQLite v1 store_sections column types are invalid")
        if any(int(info[index][3] or 0) != 1 for index in (1, 2)):
            raise SqliteSchemaError("SQLite v1 store_sections nullability is invalid")
        primary_keys = [str(row[1]) for row in info if int(row[5] or 0) > 0]
        if primary_keys != ["section_name"]:
            raise SqliteSchemaError("SQLite v1 store_sections primary key is invalid")

    def _validate_v2_schema(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != _SCHEMA_VERSION:
            raise SqliteSchemaError("SQLite v2 schema version marker is invalid")
        tables = self._table_names(connection)
        if tables != {"store_sections", "store_meta"}:
            raise SqliteSchemaError(
                "SQLite v2 schema is incomplete or has unexpected tables"
            )

        section_info = self._table_info(connection, "store_sections")
        section_names = tuple(str(row[1]) for row in section_info)
        if section_names != _SECTION_COLUMNS:
            raise SqliteSchemaError(
                "SQLite v2 schema store_sections columns are invalid"
            )
        section_primary_keys = [
            str(row[1]) for row in section_info if int(row[5] or 0) > 0
        ]
        if section_primary_keys != ["section_name"]:
            raise SqliteSchemaError(
                "SQLite v2 schema store_sections primary key is invalid"
            )
        if any(int(row[3] or 0) != 1 for row in section_info):
            raise SqliteSchemaError(
                "SQLite v2 schema store_sections nullability is invalid"
            )
        expected_section_types = (
            "TEXT",
            "TEXT",
            "REAL",
            "TEXT",
            "INTEGER",
            "INTEGER",
            "INTEGER",
        )
        if tuple(str(row[2]).upper() for row in section_info) != expected_section_types:
            raise SqliteSchemaError("SQLite v2 schema store_sections types are invalid")

        meta_info = self._table_info(connection, "store_meta")
        if tuple(str(row[1]) for row in meta_info) != ("meta_key", "meta_value"):
            raise SqliteSchemaError("SQLite v2 schema store_meta columns are invalid")
        if [str(row[1]) for row in meta_info if int(row[5] or 0) > 0] != ["meta_key"]:
            raise SqliteSchemaError(
                "SQLite v2 schema store_meta primary key is invalid"
            )
        if any(int(row[3] or 0) != 1 for row in meta_info):
            raise SqliteSchemaError(
                "SQLite v2 schema store_meta nullability is invalid"
            )
        if tuple(str(row[2]).upper() for row in meta_info) != ("TEXT", "TEXT"):
            raise SqliteSchemaError("SQLite v2 schema store_meta types are invalid")

        schema_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='store_sections'"
        ).fetchone()
        schema_sql = (
            str(schema_row[0] if schema_row else "").casefold().replace(" ", "")
        )
        for constraint_name in (
            "store_sections_schema_v2",
            "store_sections_positive_revision",
            "store_sections_deleted_flag",
        ):
            if constraint_name not in schema_sql:
                raise SqliteSchemaError("SQLite v2 schema write guards are missing")

        metadata = self._metadata(connection)
        if set(metadata) != {"initialized", "next_revision"}:
            raise SqliteSchemaError("SQLite store meta keys are invalid")
        if metadata.get("initialized") not in {"0", "1"}:
            raise SqliteSchemaError("SQLite store meta initialized is invalid")
        next_revision = self._metadata_integer(metadata, "next_revision")
        invalid_row = connection.execute(
            "SELECT section_name FROM store_sections "
            "WHERE typeof(schema_version)<>'integer' OR schema_version<>2 "
            "OR typeof(revision)<>'integer' OR revision<=0 OR revision>? "
            "OR typeof(is_deleted)<>'integer' OR is_deleted NOT IN (0,1) "
            "OR (length(checksum) NOT IN (0,64)) "
            "OR (is_deleted=1 AND payload_json<>'null') LIMIT 1",
            (_MAX_SECTION_REVISION,),
        ).fetchone()
        if invalid_row is not None:
            raise SqliteSchemaError(
                f"SQLite v2 section metadata is invalid: {invalid_row[0]}"
            )
        row_count, maximum_revision = connection.execute(
            "SELECT COUNT(*),COALESCE(MAX(revision),0) FROM store_sections"
        ).fetchone()
        if next_revision <= int(maximum_revision or 0):
            raise SqliteSchemaError("SQLite store meta next_revision is stale")
        if metadata["initialized"] == "0" and (
            int(row_count or 0) != 0 or next_revision != 1
        ):
            raise SqliteSchemaError(
                "SQLite uninitialized store metadata is inconsistent"
            )
        if (
            metadata["initialized"] == "1"
            and int(row_count or 0) == 0
            and next_revision < 2
        ):
            raise SqliteSchemaError(
                "SQLite initialized empty store metadata is inconsistent"
            )

    def _create_empty_v2_schema(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._create_sections_table(connection, "store_sections")
            self._create_meta_table(connection)
            connection.executemany(
                "INSERT INTO store_meta(meta_key,meta_value) VALUES(?,?)",
                (("initialized", "0"), ("next_revision", "1")),
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            self._validate_v2_schema(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _backup_v1_database(self, source: sqlite3.Connection) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.db_path.with_name(
            f"{self.db_path.name}.pre-v2-{timestamp}-{uuid.uuid4().hex[:12]}.bak"
        )
        destination: sqlite3.Connection | None = None
        try:
            destination = sqlite3.connect(str(backup_path), timeout=15.0)
            source.backup(destination)
            check = destination.execute("PRAGMA quick_check").fetchone()
            if check is None or str(check[0]).casefold() != "ok":
                raise SqliteSchemaError(
                    "SQLite pre-migration backup integrity check failed"
                )
            destination.commit()
            return backup_path
        except Exception:
            if destination is not None:
                destination.close()
                destination = None
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if destination is not None:
                destination.close()

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        self._validate_v1_schema(connection)
        backup_path = self._backup_v1_database(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT section_name,payload_json,updated_at,checksum,schema_version "
                "FROM store_sections ORDER BY section_name"
            ).fetchall()
            prepared: list[tuple[str, str, float, str, int, int, int]] = []
            for raw_name, raw_payload, raw_updated_at, raw_checksum, raw_schema in rows:
                name = _section_name(raw_name)
                if (
                    isinstance(raw_schema, bool)
                    or not isinstance(raw_schema, int)
                    or raw_schema != 1
                ):
                    raise SqliteSchemaError(
                        f"SQLite v1 section schema version is invalid: {name}"
                    )
                if not isinstance(raw_payload, str):
                    raise SqliteSchemaError(
                        f"SQLite v1 section payload type is invalid: {name}"
                    )
                try:
                    parsed = json.loads(raw_payload)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"SQLite v1 section payload contains invalid JSON: {name}"
                    ) from exc
                payload_json = _canonical_json(parsed)
                checksum = _payload_checksum(payload_json)
                existing_checksum = str(raw_checksum or "")
                raw_payload_checksum = _payload_checksum(raw_payload)
                if existing_checksum and existing_checksum not in {
                    raw_payload_checksum,
                    checksum,
                }:
                    raise SqliteSchemaError(
                        f"SQLite v1 section checksum mismatch: {name}"
                    )
                try:
                    updated_at = float(raw_updated_at)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise SqliteSchemaError(
                        f"SQLite v1 section timestamp is invalid: {name}"
                    ) from exc
                prepared.append(
                    (name, payload_json, updated_at, checksum, _SCHEMA_VERSION, 1, 0)
                )

            self._create_sections_table(connection, "store_sections_v2_migration")
            connection.executemany(
                "INSERT INTO store_sections_v2_migration("
                "section_name,payload_json,updated_at,checksum,schema_version,revision,is_deleted"
                ") VALUES(?,?,?,?,?,?,?)",
                prepared,
            )
            verification_rows = connection.execute(
                "SELECT section_name,payload_json,checksum FROM store_sections_v2_migration "
                "ORDER BY section_name"
            ).fetchall()
            expected_rows = [(row[0], row[1], row[3]) for row in prepared]
            if verification_rows != expected_rows:
                raise SqliteSchemaError("SQLite v1 to v2 copy verification failed")

            connection.execute("DROP TABLE store_sections")
            connection.execute(
                "ALTER TABLE store_sections_v2_migration RENAME TO store_sections"
            )
            self._create_meta_table(connection)
            initialized = "1" if prepared else "0"
            next_revision = "2" if prepared else "1"
            connection.executemany(
                "INSERT INTO store_meta(meta_key,meta_value) VALUES(?,?)",
                (("initialized", initialized), ("next_revision", next_revision)),
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            self._validate_v2_schema(connection)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise SqliteSchemaError("SQLite v1 to v2 integrity check failed")
            connection.commit()
            logger.info(
                "Migrated SQLite store schema to v2 after creating backup: %s",
                backup_path,
            )
        except Exception:
            connection.rollback()
            raise

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise SqliteUnsupportedSchemaError(
                f"Unsupported SQLite store schema version {version}"
            )
        if version not in {0, 1, _SCHEMA_VERSION}:
            raise SqliteSchemaError(
                f"SQLite schema version marker is invalid: {version}"
            )
        if version == _SCHEMA_VERSION:
            self._validate_v2_schema(connection)
            return

        tables = self._table_names(connection)
        if "store_sections" in tables:
            self._migrate_v1_to_v2(connection)
            return
        if version != 0 or tables:
            raise SqliteSchemaError("SQLite schema marker does not match its tables")
        self._create_empty_v2_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._schema_lock:
            connection = sqlite3.connect(str(self.db_path), timeout=15.0)
            try:
                connection.execute("PRAGMA busy_timeout=15000")
                self._ensure_schema(connection)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                return connection
            except Exception:
                connection.close()
                raise

    @staticmethod
    def _initialized(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT meta_value FROM store_meta WHERE meta_key='initialized'"
        ).fetchone()
        return row is not None and str(row[0]) == "1"

    def load_store(self) -> dict[str, Any]:
        if not self.exists():
            return self.new_store()
        try:
            with self._schema_lock:
                connection = self._connect()
                try:
                    if not self._initialized(connection):
                        raise SqliteStoreNotInitializedError(
                            f"SQLite store is not initialized: {self.db_path}"
                        )
                    rows = connection.execute(
                        "SELECT section_name,payload_json,checksum,schema_version,revision,is_deleted "
                        "FROM store_sections ORDER BY section_name"
                    ).fetchall()
                finally:
                    connection.close()

            data: dict[str, Any] = {}
            tombstoned_sections: set[str] = set()
            for (
                section_name,
                payload_json,
                checksum,
                schema_version,
                revision,
                is_deleted,
            ) in rows:
                name = str(section_name)
                try:
                    parsed = json.loads(payload_json)
                except Exception as exc:
                    raise ValueError(
                        f"SQLite section payload is invalid: {name}"
                    ) from exc
                canonical = _canonical_json(parsed)
                if (
                    int(schema_version) != _SCHEMA_VERSION
                    or int(revision) <= 0
                    or (str(checksum) and str(checksum) != _payload_checksum(canonical))
                ):
                    raise ValueError(f"SQLite section checksum is invalid: {name}")
                if int(is_deleted):
                    if parsed is not None or str(payload_json) != "null":
                        raise ValueError(f"SQLite section tombstone is invalid: {name}")
                    tombstoned_sections.add(name)
                    continue
                data[name] = parsed
            data = self.ensure_defaults(data)
            for name in tombstoned_sections:
                data.pop(name, None)
            return data
        except SqliteStoreNotInitializedError:
            raise
        except Exception as exc:
            logger.warning(
                "读取 SQLite 数据失败,已保留原数据库并中止加载: %s",
                exc,
            )
            raise

    def load_store_read_only(
        self,
        *,
        max_payload_bytes: int,
        max_database_bytes: int,
    ) -> dict[str, Any]:
        """Load one current, legacy-v1, or empty snapshot without writes."""
        if (
            isinstance(max_payload_bytes, bool)
            or not isinstance(max_payload_bytes, int)
            or max_payload_bytes <= 0
            or isinstance(max_database_bytes, bool)
            or not isinstance(max_database_bytes, int)
            or max_database_bytes <= 0
        ):
            raise ValueError("read-only SQLite byte limits must be positive")
        before = self.db_path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise SqliteSchemaError("read-only SQLite path is not a regular file")
        if before.st_size < 0 or before.st_size > max_database_bytes:
            raise SqliteSchemaError("read-only SQLite database exceeds its byte limit")

        sidecars = tuple(
            Path(f"{self.db_path}{suffix}")
            for suffix in ("-wal", "-shm", "-journal")
        )

        def assert_sidecars_regular() -> None:
            for sidecar in sidecars:
                try:
                    details = sidecar.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(details.st_mode):
                    raise SqliteSchemaError(
                        "read-only SQLite sidecar path is not a regular file"
                    )

        # WAL and SHM files are normal for this backend: _connect() explicitly
        # enables WAL. Opening the database as immutable would ignore committed
        # frames that have not been checkpointed yet, while rejecting sidecars
        # would make a healthy active database impossible to preflight.
        assert_sidecars_regular()

        uri = f"{self.db_path.absolute().as_uri()}?mode=ro"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=15.0,
                isolation_level=None,
            )
            try:
                connection.execute("PRAGMA query_only=ON")
                query_only = connection.execute("PRAGMA query_only").fetchone()
                if query_only != (1,):
                    raise SqliteSchemaError("SQLite query-only mode is unavailable")
                connection.execute("BEGIN")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                tables = self._table_names(connection)
                legacy = version in {0, 1} and tables == {"store_sections"}
                if version == 0 and not tables:
                    connection.rollback()
                    return {}
                if legacy:
                    self._validate_v1_schema(connection)
                    quick_check = connection.execute("PRAGMA quick_check").fetchall()
                    if quick_check != [("ok",)]:
                        raise SqliteSchemaError("SQLite read-only quick_check failed")
                    encoded_size = connection.execute(
                        "SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) "
                        "FROM store_sections"
                    ).fetchone()
                    if (
                        encoded_size is None
                        or isinstance(encoded_size[0], bool)
                        or not isinstance(encoded_size[0], int)
                        or encoded_size[0] < 0
                        or encoded_size[0] > max_payload_bytes
                    ):
                        raise ValueError(
                            "SQLite read-only snapshot exceeds its byte limit"
                        )
                    rows = connection.execute(
                        "SELECT section_name,payload_json,updated_at,checksum,schema_version "
                        "FROM store_sections ORDER BY section_name"
                    )
                    data: dict[str, Any] = {}
                    for (
                        raw_name,
                        raw_payload,
                        raw_updated_at,
                        raw_checksum,
                        raw_schema,
                    ) in rows:
                        name = _section_name(raw_name)
                        if (
                            not isinstance(raw_payload, str)
                            or isinstance(raw_schema, bool)
                            or not isinstance(raw_schema, int)
                            or raw_schema != 1
                        ):
                            raise SqliteSchemaError(
                                f"SQLite v1 section identity is invalid: {name}"
                            )
                        try:
                            parsed = json.loads(raw_payload)
                            float(raw_updated_at)
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise SqliteSchemaError(
                                f"SQLite v1 section payload is invalid: {name}"
                            ) from exc
                        canonical = _canonical_json(parsed)
                        checksum = str(raw_checksum or "")
                        if checksum not in {
                            "",
                            _payload_checksum(raw_payload),
                            _payload_checksum(canonical),
                        }:
                            raise SqliteSchemaError(
                                f"SQLite v1 section checksum mismatch: {name}"
                            )
                        data[name] = parsed
                    connection.rollback()
                    return data

                self._validate_v2_schema(connection)
                if not self._initialized(connection):
                    raise SqliteStoreNotInitializedError(
                        f"SQLite store is not initialized: {self.db_path}"
                    )
                quick_check = connection.execute("PRAGMA quick_check").fetchall()
                if quick_check != [("ok",)]:
                    raise SqliteSchemaError("SQLite read-only quick_check failed")
                encoded_size = connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) "
                    "FROM store_sections"
                ).fetchone()
                if (
                    encoded_size is None
                    or isinstance(encoded_size[0], bool)
                    or not isinstance(encoded_size[0], int)
                    or encoded_size[0] < 0
                    or encoded_size[0] > max_payload_bytes
                ):
                    raise ValueError(
                        "SQLite read-only snapshot exceeds its byte limit"
                    )
                rows = connection.execute(
                    "SELECT section_name,payload_json,checksum,schema_version,revision,is_deleted "
                    "FROM store_sections ORDER BY section_name"
                )
                data: dict[str, Any] = {}
                for (
                    raw_name,
                    raw_payload,
                    raw_checksum,
                    schema_version,
                    revision,
                    is_deleted,
                ) in rows:
                    name = _section_name(raw_name)
                    if not isinstance(raw_payload, str):
                        raise SqliteSchemaError(
                            f"SQLite section payload type is invalid: {name}"
                        )
                    parsed = json.loads(
                        raw_payload,
                        parse_constant=_reject_json_constant,
                        object_pairs_hook=_strict_json_value,
                    )
                    canonical = _canonical_json(parsed)
                    checksum = str(raw_checksum or "")
                    if (
                        schema_version != _SCHEMA_VERSION
                        or isinstance(revision, bool)
                        or not isinstance(revision, int)
                        or revision <= 0
                        or checksum not in {"", _payload_checksum(canonical)}
                    ):
                        raise SqliteSchemaError(
                            f"SQLite section identity is invalid: {name}"
                        )
                    if is_deleted == 1:
                        if parsed is not None or raw_payload != "null":
                            raise SqliteSchemaError(
                                f"SQLite section tombstone is invalid: {name}"
                            )
                        continue
                    if is_deleted != 0:
                        raise SqliteSchemaError(
                            f"SQLite section deletion flag is invalid: {name}"
                        )
                    data[name] = parsed
                connection.rollback()
            finally:
                connection.close()
        finally:
            assert_sidecars_regular()
            try:
                after = self.db_path.lstat()
            except OSError as exc:
                raise SqliteSchemaError(
                    "SQLite file changed during read-only snapshot"
                ) from exc
            if (
                not stat.S_ISREG(after.st_mode)
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
            ):
                raise SqliteSchemaError(
                    "SQLite file changed during read-only snapshot"
                )
        return data

    def load_sections(self, section_names: tuple[str, ...] | list[str]) -> dict[str, Any]:
        """Load a small subset without rebuilding the complete live store."""
        names = [str(name) for name in section_names if str(name)]
        if not names or not self.exists():
            return {}
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in names)
            rows = conn.execute(
                f"SELECT section_name, payload_json FROM store_sections "
                f"WHERE section_name IN ({placeholders})",
                names,
            ).fetchall()
        finally:
            conn.close()
        result: dict[str, Any] = {}
        for section_name, payload_json in rows:
            result[str(section_name)] = json.loads(payload_json)
        return result
    @staticmethod
    def _upsert_sql() -> str:
        return (
            "INSERT INTO store_sections("
            "section_name,payload_json,updated_at,checksum,schema_version,revision,is_deleted"
            ") VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(section_name) DO UPDATE SET "
            "payload_json=CASE WHEN (store_sections.checksum=excluded.checksum "
            "OR (store_sections.checksum='' "
            "AND store_sections.payload_json=excluded.payload_json)) "
            "AND store_sections.is_deleted=excluded.is_deleted "
            "THEN store_sections.payload_json ELSE excluded.payload_json END,"
            "updated_at=CASE WHEN (store_sections.checksum=excluded.checksum "
            "OR (store_sections.checksum='' "
            "AND store_sections.payload_json=excluded.payload_json)) "
            "AND store_sections.is_deleted=excluded.is_deleted "
            "THEN store_sections.updated_at ELSE excluded.updated_at END,"
            "checksum=excluded.checksum,"
            "schema_version=excluded.schema_version,"
            "revision=excluded.revision,"
            "is_deleted=excluded.is_deleted"
        )

    def _replace_store(
        self,
        data: dict[str, Any],
        *,
        minimum_revision: int | None = None,
        deleted_sections: Mapping[str, int] | None = None,
        preserve_tombstones: bool = False,
    ) -> int:
        if not isinstance(data, dict):
            raise TypeError("SQLite full store payload must be a dictionary")
        if minimum_revision is not None:
            minimum_revision = _revision(minimum_revision)
        if deleted_sections is None:
            deleted_sections = {}
        if not isinstance(deleted_sections, Mapping):
            raise TypeError("SQLite full snapshot deleted sections require a mapping")
        prepared: list[tuple[str, str, str]] = []
        prepared_names: set[str] = set()
        for raw_name, payload in data.items():
            name = _section_name(raw_name)
            if name in prepared_names:
                raise ValueError(f"Duplicate SQLite section name: {name}")
            prepared_names.add(name)
            payload_json = _canonical_json(payload)
            prepared.append((name, payload_json, _payload_checksum(payload_json)))
        deleted: dict[str, int] = {}
        for raw_name, raw_revision in deleted_sections.items():
            name = _section_name(raw_name)
            if name in prepared_names:
                raise ValueError(
                    f"SQLite full snapshot section is both changed and deleted: {name}"
                )
            deleted[name] = _revision(raw_revision)

        with self._schema_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                metadata = self._metadata(connection)
                baseline_revision = self._metadata_integer(metadata, "next_revision")
                if minimum_revision is not None:
                    baseline_revision = max(baseline_revision, minimum_revision)
                if deleted:
                    baseline_revision = max(baseline_revision, max(deleted.values()))
                if preserve_tombstones:
                    preserved = {
                        str(name): int(revision)
                        for name, revision in connection.execute(
                            "SELECT section_name,revision FROM store_sections "
                            "WHERE is_deleted=1"
                        )
                        if str(name) not in prepared_names and str(name) not in deleted
                    }
                    deleted.update(preserved)
                    if preserved:
                        baseline_revision = max(baseline_revision, max(preserved.values()))
                _revision(baseline_revision)
                now = time.time()
                connection.execute("DELETE FROM store_sections")
                connection.executemany(
                    "INSERT INTO store_sections("
                    "section_name,payload_json,updated_at,checksum,schema_version,revision,is_deleted"
                    ") VALUES(?,?,?,?,?,?,0)",
                    [
                        (
                            name,
                            payload_json,
                            now,
                            checksum,
                            _SCHEMA_VERSION,
                            baseline_revision,
                        )
                        for name, payload_json, checksum in prepared
                    ],
                )
                connection.executemany(
                    "INSERT INTO store_sections("
                    "section_name,payload_json,updated_at,checksum,schema_version,revision,is_deleted"
                    ") VALUES(?,?,?,?,?,?,1)",
                    [
                        (
                            name,
                            "null",
                            now,
                            _payload_checksum("null"),
                            _SCHEMA_VERSION,
                            baseline_revision,
                        )
                        for name in deleted
                    ],
                )
                connection.execute(
                    "UPDATE store_meta SET meta_value='1' WHERE meta_key='initialized'"
                )
                connection.execute(
                    "UPDATE store_meta SET meta_value=? WHERE meta_key='next_revision'",
                    (str(baseline_revision + 1),),
                )
                connection.commit()
                return baseline_revision
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def save_store(self, data: dict[str, Any]) -> None:
        self._replace_store(data)

    def save_sections(
        self,
        changed_sections: Mapping[str, tuple[int, Any]],
        deleted_sections: Mapping[str, int],
    ) -> dict[str, int]:
        if not isinstance(changed_sections, Mapping) or not isinstance(
            deleted_sections, Mapping
        ):
            raise TypeError("SQLite partial writes require mapping inputs")
        overlap = set(changed_sections).intersection(deleted_sections)
        if overlap:
            raise ValueError(
                f"SQLite changed and deleted sections overlap: {sorted(map(str, overlap))}"
            )

        prepared: dict[str, tuple[int, str, str, int]] = {}
        for raw_name, value in changed_sections.items():
            name = _section_name(raw_name)
            if not isinstance(value, tuple) or len(value) != 2:
                raise ValueError(
                    "SQLite changed section value must be a (revision, payload) tuple"
                )
            revision = _revision(value[0])
            payload_json = _canonical_json(value[1])
            prepared[name] = (
                revision,
                payload_json,
                _payload_checksum(payload_json),
                0,
            )
        for raw_name, raw_revision in deleted_sections.items():
            name = _section_name(raw_name)
            revision = _revision(raw_revision)
            payload_json = "null"
            prepared[name] = (
                revision,
                payload_json,
                _payload_checksum(payload_json),
                1,
            )
        if not prepared:
            return {}

        confirmed = {name: values[0] for name, values in prepared.items()}
        with self._schema_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                metadata = self._metadata(connection)
                next_revision = self._metadata_integer(metadata, "next_revision")
                persisted: dict[str, tuple[str, int, int] | None] = {}
                for name in sorted(prepared):
                    row = connection.execute(
                        "SELECT payload_json,checksum,revision,is_deleted "
                        "FROM store_sections WHERE section_name=?",
                        (name,),
                    ).fetchone()
                    if row is None:
                        persisted[name] = None
                        continue
                    stored_payload, stored_checksum, stored_revision, stored_deleted = (
                        row
                    )
                    persisted[name] = (
                        str(stored_checksum),
                        int(stored_revision),
                        int(stored_deleted),
                    )
                    requested_revision, payload_json, checksum, is_deleted = prepared[
                        name
                    ]
                    stored_checksum = str(stored_checksum)
                    stored_payload = str(stored_payload)
                    stored_deleted = int(stored_deleted)
                    same_payload = stored_payload == payload_json
                    same_delete_state = stored_deleted == is_deleted
                    if requested_revision < int(stored_revision):
                        raise SqliteRevisionConflictError(
                            f"SQLite section revision moved backwards: {name}"
                        )
                    if requested_revision == int(stored_revision):
                        checksum_matches = stored_checksum == checksum
                        legacy_checksum_upgrade = not stored_checksum and same_payload
                        if (
                            not same_delete_state
                            or not same_payload
                            or (not checksum_matches and not legacy_checksum_upgrade)
                        ):
                            raise SqliteRevisionConflictError(
                                f"SQLite section content conflicts at equal revision: {name}"
                            )
                now = time.time()
                upsert_sql = self._upsert_sql()
                for name in sorted(prepared):
                    requested_revision, payload_json, checksum, is_deleted = prepared[
                        name
                    ]
                    stored = persisted[name]
                    if (
                        stored is not None
                        and requested_revision == stored[1]
                        and stored[0] == checksum
                    ):
                        continue
                    connection.execute(
                        upsert_sql,
                        (
                            name,
                            payload_json,
                            now,
                            checksum,
                            _SCHEMA_VERSION,
                            requested_revision,
                            is_deleted,
                        ),
                    )
                connection.execute(
                    "UPDATE store_meta SET meta_value='1' WHERE meta_key='initialized'"
                )
                connection.execute(
                    "UPDATE store_meta SET meta_value=? WHERE meta_key='next_revision'",
                    (str(max(next_revision, max(confirmed.values()) + 1)),),
                )
                connection.commit()
                return confirmed
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def save_snapshot(
        self,
        data: dict[str, Any],
        *,
        minimum_revision: int | None = None,
        deleted_sections: Mapping[str, int] | None = None,
        preserve_tombstones: bool = False,
    ) -> int:
        return self._replace_store(
            data,
            minimum_revision=minimum_revision,
            deleted_sections=deleted_sections,
            preserve_tombstones=preserve_tombstones,
        )

    def next_revision(self) -> int:
        if not self.exists():
            return 1
        with self._schema_lock:
            connection = self._connect()
            try:
                return self._metadata_integer(
                    self._metadata(connection),
                    "next_revision",
                )
            finally:
                connection.close()

    def deleted_section_revisions(
        self,
        section_names: Collection[str],
    ) -> dict[str, int]:
        names = sorted({_section_name(name) for name in section_names})
        if not names or not self.exists():
            return {}
        result: dict[str, int] = {}
        with self._schema_lock:
            connection = self._connect()
            try:
                for name in names:
                    row = connection.execute(
                        "SELECT revision FROM store_sections "
                        "WHERE section_name=? AND is_deleted=1",
                        (name,),
                    ).fetchone()
                    if row is not None:
                        result[name] = int(row[0])
            finally:
                connection.close()
        return result

    def health_check(self, *, raise_on_error: bool = False) -> dict[str, Any]:
        ok = True
        error = ""
        try:
            if self.exists():
                with self._schema_lock:
                    connection = self._connect()
                    try:
                        check = connection.execute("PRAGMA integrity_check").fetchone()
                        if check is None or str(check[0]).casefold() != "ok":
                            raise SqliteSchemaError("SQLite integrity_check failed")
                    finally:
                        connection.close()
        except Exception as exc:
            if raise_on_error:
                raise
            ok = False
            error = str(exc)
        return {
            "backend": self.backend_name(),
            "path": str(self.db_path),
            "exists": self.exists(),
            "ok": ok,
            "error": error,
        }
