"""Measure V2 SQLite growth, payload size, startup validation, and lock shape.

Usage:
    python tools/audit_storage.py --profiles-root <V2 plugin data>/profiles
    python tools/audit_storage.py --database <profile>/runtime/companion-v2.sqlite3

Startup timing runs StorageRuntime.open() against SQLite backup snapshots in a
temporary directory. The selected databases are only read; only snapshots may
be migrated while measuring older schema versions.
"""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion_v2.storage.runtime import StorageRuntime  # noqa: E402
from companion_v2.storage.schema import SCHEMA_VERSION  # noqa: E402


DEFAULT_MAX_DATABASE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_HISTORY_ROWS = 100_000
DEFAULT_MAX_DOCUMENT_AVG_BYTES = 64 * 1024
DEFAULT_MAX_DOCUMENT_BYTES = 1024 * 1024
DEFAULT_MAX_OPEN_MS = 1_000


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _snapshot(source_path: Path, destination_path: Path) -> None:
    source_uri = source_path.resolve().as_uri() + "?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=5.0)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _metadata(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        metadata = {row["key"]: row["value"] for row in connection.execute(
            "SELECT key, value FROM runtime_metadata WHERE key IN ('installation_id', 'schema_version')"
        )}
        histories = {
            table: connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
            for table in tables if table.endswith("_history")
        }
        if "memory_views" in tables:
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(AVG(length(CAST(document_json AS BLOB))), 0), "
                "COALESCE(MAX(length(CAST(document_json AS BLOB))), 0) FROM memory_views"
            ).fetchone()
            document_bytes = {
                "rows": row[0],
                "average": round(row[1], 2),
                "maximum": row[2],
            }
        else:
            document_bytes = {"rows": 0, "average": 0, "maximum": 0}
        query_plans = _memory_view_query_plans(connection) if "memory_views" in tables else {}
        index_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        return {
            "schema_version": metadata.get("schema_version"),
            "installation_id": metadata.get("installation_id"),
            "table_count": len(tables),
            "index_count": index_count,
            "history_rows": histories,
            "memory_view_documents_bytes": document_bytes,
            "memory_view_query_plans": query_plans,
        }
    finally:
        connection.close()


def _memory_view_query_plans(connection: sqlite3.Connection) -> dict[str, list[str]]:
    statements = {
        "group_directory_keyset": (
            "SELECT * FROM memory_views WHERE owner_key = ? AND kind = ? AND state = ? "
            "AND view_id > ? ORDER BY view_id LIMIT ?",
            ("owner", "group", "active", "", 21),
        ),
        "experience_directory_keyset": (
            "SELECT view_id FROM memory_views WHERE owner_key = ? AND kind = ? AND state = ? "
            "AND view_id > ? ORDER BY view_id LIMIT ?",
            ("owner", "experience", "active", "", 21),
        ),
        "owner_active_views": (
            "SELECT view_id FROM memory_views WHERE owner_key = ? AND state = 'active' "
            "ORDER BY view_id",
            ("owner",),
        ),
        "person_directory_recent": (
            "SELECT * FROM memory_views WHERE kind = ? AND state = 'active' "
            "ORDER BY updated_at DESC, view_id DESC LIMIT ?",
            ("person", 200),
        ),
        "read_one_view": (
            "SELECT * FROM memory_views WHERE view_id = ? AND owner_key = ? AND kind = ?",
            ("view", "owner", "experience"),
        ),
    }
    return {
        name: [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + statement, parameters)]
        for name, (statement, parameters) in statements.items()
    }


def _database_sizes(database: Path) -> dict[str, int]:
    paths = {
        "database": database,
        "wal": Path(str(database) + "-wal"),
        "shm": Path(str(database) + "-shm"),
    }
    return {name: path.stat().st_size if path.exists() else 0 for name, path in paths.items()}


def _open_samples(database: Path, installation_id: str, samples: int) -> list[float]:
    durations = []
    for _ in range(samples):
        with tempfile.TemporaryDirectory(prefix="v2-storage-audit-") as temporary:
            data_root = Path(temporary)
            snapshot = data_root / "companion-v2.sqlite3"
            _snapshot(database, snapshot)
            started = time.perf_counter()
            runtime = StorageRuntime.open(data_root=data_root, installation_id=installation_id)
            durations.append((time.perf_counter() - started) * 1000)
            runtime.close()
    return [round(item, 2) for item in durations]


def _lock_facts() -> dict[str, Any]:
    source = (ROOT / "companion_v2" / "storage" / "runtime.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    runtime_class = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "StorageRuntime")
    methods = {node.name: node for node in runtime_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def uses_runtime_lock(name: str) -> bool:
        method = methods.get(name)
        if method is None:
            return False
        return any(
            isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Attribute)
                and item.context_expr.attr == "_lock"
                and isinstance(item.context_expr.value, ast.Name)
                and item.context_expr.value.id == "self"
                for item in node.items
            )
            for node in ast.walk(method)
        )

    read_methods = ("read_one", "read_all", "read_snapshot", "read_only_snapshot")
    reads_locked = all(uses_runtime_lock(name) for name in read_methods)
    return {
        "read_methods_share_runtime_rlock": reads_locked,
        "read_methods": {name: {"uses_runtime_rlock": uses_runtime_lock(name)} for name in read_methods},
        "write_transaction_uses_runtime_rlock": uses_runtime_lock("transaction"),
        "write_transaction_begins_immediate": "BEGIN IMMEDIATE" in source,
        "synchronous_full": "PRAGMA synchronous = FULL" in source,
        "synchronous_normal": "PRAGMA synchronous = NORMAL" in source,
        "busy_timeout_ms": 5000 if "PRAGMA busy_timeout = 5000" in source else None,
    }


def _thresholds(args: argparse.Namespace) -> dict[str, int]:
    return {
        "database_bytes": args.max_database_bytes,
        "history_rows": args.max_history_rows,
        "document_average_bytes": args.max_document_avg_bytes,
        "document_max_bytes": args.max_document_bytes,
        "open_ms": args.max_open_ms,
    }


def audit_database(database: Path, args: argparse.Namespace) -> dict[str, Any]:
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    sizes = _database_sizes(database)
    metadata = _metadata_snapshot(database)
    open_samples = _open_samples(database, metadata["installation_id"], args.open_samples)
    open_median = round(statistics.median(open_samples), 2)
    thresholds = _thresholds(args)
    schema_current = metadata["schema_version"] == SCHEMA_VERSION
    findings = []
    total_size = sum(sizes.values())
    if total_size > thresholds["database_bytes"]:
        findings.append(f"database files exceed {thresholds['database_bytes']} bytes")
    for table, count in metadata["history_rows"].items():
        if count > thresholds["history_rows"]:
            findings.append(f"{table} exceeds {thresholds['history_rows']} rows")
    documents = metadata["memory_view_documents_bytes"]
    if documents["average"] > thresholds["document_average_bytes"]:
        findings.append(f"memory_views average document exceeds {thresholds['document_average_bytes']} bytes")
    if documents["maximum"] > thresholds["document_max_bytes"]:
        findings.append(f"memory_views maximum document exceeds {thresholds['document_max_bytes']} bytes")
    if schema_current and open_median > thresholds["open_ms"]:
        findings.append(f"StorageRuntime.open median exceeds {thresholds['open_ms']} ms")
    return {
        "database": str(database),
        "profile": database.parent.parent.name,
        "file_sizes_bytes": sizes,
        "total_file_bytes": total_size,
        **metadata,
        "schema_current": schema_current,
        "startup_validation_open_ms": {
            "samples": open_samples,
            "median": open_median,
            "includes_migration": not schema_current,
        },
        "thresholds": thresholds,
        "status": "RED" if findings else "OK",
        "findings": findings,
    }


def _metadata_snapshot(database: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v2-storage-metrics-") as temporary:
        snapshot = Path(temporary) / "snapshot.sqlite3"
        _snapshot(database, snapshot)
        return _metadata(snapshot)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="append", type=Path, default=[], help="SQLite path; may be repeated")
    parser.add_argument("--profiles-root", type=Path, help="Recursively audit companion-v2.sqlite3 under this V2 profiles directory")
    parser.add_argument("--open-samples", type=int, default=3, help="Isolated snapshot opens per database (default: 3)")
    parser.add_argument("--max-database-bytes", type=int, default=DEFAULT_MAX_DATABASE_BYTES)
    parser.add_argument("--max-history-rows", type=int, default=DEFAULT_MAX_HISTORY_ROWS)
    parser.add_argument("--max-document-avg-bytes", type=int, default=DEFAULT_MAX_DOCUMENT_AVG_BYTES)
    parser.add_argument("--max-document-bytes", type=int, default=DEFAULT_MAX_DOCUMENT_BYTES)
    parser.add_argument("--max-open-ms", type=int, default=DEFAULT_MAX_OPEN_MS)
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report")
    args = parser.parse_args()
    if args.open_samples < 1:
        parser.error("--open-samples must be at least 1")
    if not args.database and args.profiles_root is None:
        parser.error("provide --database or --profiles-root")
    paths = list(args.database)
    if args.profiles_root is not None:
        paths.extend(args.profiles_root.rglob("companion-v2.sqlite3"))
    args.databases = sorted({path.resolve() for path in paths})
    if not args.databases:
        parser.error("no companion-v2.sqlite3 files found")
    return args


def main() -> int:
    args = _arguments()
    reports = []
    errors = []
    for database in args.databases:
        try:
            reports.append(audit_database(database, args))
        except Exception as error:
            errors.append({"database": str(database), "error": f"{type(error).__name__}: {error}"})
    report = {"lock_and_runtime_facts": _lock_facts(), "databases": reports, "errors": errors}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        facts = report["lock_and_runtime_facts"]
        print("RUNTIME\t" + json.dumps(facts, ensure_ascii=False, sort_keys=True))
        for item in reports:
            print(f"{item['status']}\t{item['profile']}\t{item['database']}")
            print(f"  files_bytes={item['file_sizes_bytes']} total={item['total_file_bytes']}")
            print(f"  schema={item['schema_version']} tables={item['table_count']} indexes={item['index_count']}")
            print(f"  history_rows={item['history_rows']}")
            print(f"  memory_views={item['memory_view_documents_bytes']}")
            print(f"  memory_view_query_plans={item['memory_view_query_plans']}")
            print(f"  open_ms={item['startup_validation_open_ms']}")
            for finding in item["findings"]:
                print(f"  RED: {finding}")
        for error in errors:
            print(f"ERROR\t{error['database']}\t{error['error']}")
    return 1 if errors or any(item["status"] == "RED" for item in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
