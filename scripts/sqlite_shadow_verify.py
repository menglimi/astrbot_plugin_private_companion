#!/usr/bin/env python3
"""Read-only production SQLite backup and baseline/current shadow verification.

The source data tree is only opened through SQLite ``mode=ro`` connections.  Every
write is directed below --output.  Reports contain paths, schema/count metadata and
hashes, never SQLite payloads or JSON values.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_NAME = "astrbot_plugin_private_companion"
DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checks(connection: sqlite3.Connection) -> dict[str, Any]:
    quick = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    tables = int(connection.execute(
        "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0])
    return {"quick_check": quick, "integrity_check": integrity, "table_count": tables}


def readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def backup_one(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    source_file_hash = sha256(source)
    with readonly_connection(source) as src, sqlite3.connect(destination) as dst:
        source_checks = checks(src)
        src.backup(dst, pages=256, sleep=0.05)
    with readonly_connection(destination) as copied:
        backup_checks = checks(copied)
        user_version = int(copied.execute("PRAGMA user_version").fetchone()[0])
    after = source.stat()
    return {
        "source_path": str(source.resolve()),
        "source_size": before.st_size,
        "source_mtime_ns": before.st_mtime_ns,
        "source_file_sha256": source_file_hash,
        "source_identity_stable_during_backup": (
            before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
        ),
        "source_checks": source_checks,
        "backup_path": str(destination.resolve()),
        "backup_size": destination.stat().st_size,
        "backup_sha256": sha256(destination),
        "backup_user_version": user_version,
        "backup_checks": backup_checks,
        "backup_api_wal_consistent": True,
    }


def load_backend(module_path: Path, namespace: str):
    root = namespace
    storage = f"{root}.storage"
    package = types.ModuleType(root)
    package.__path__ = [str(module_path.parent.parent)]
    sys.modules[root] = package
    storage_package = types.ModuleType(storage)
    storage_package.__path__ = [str(module_path.parent)]
    sys.modules[storage] = storage_package
    logging_module = types.ModuleType(f"{root}.logging_util")
    logging_module.get_module_logger = lambda _name: __import__("logging").getLogger(_name)
    sys.modules[logging_module.__name__] = logging_module
    for short in ("backend_base", "sqlite_backend"):
        name = f"{storage}.{short}"
        path = module_path.parent / f"{short}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        assert spec and spec.loader
        spec.loader.exec_module(module)
    return sys.modules[f"{storage}.sqlite_backend"]


def normalized(path: Path) -> dict[str, Any]:
    with readonly_connection(path) as connection:
        schema = [
            (str(kind), str(name), hashlib.sha256(str(sql or "").encode()).hexdigest())
            for kind, name, sql in connection.execute(
                "SELECT type,name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        meta = list(connection.execute(
            "SELECT meta_key,meta_value FROM store_meta ORDER BY meta_key"
        ))
        sections = []
        for name, payload, checksum, revision, deleted in connection.execute(
            "SELECT section_name,payload_json,checksum,revision,is_deleted "
            "FROM store_sections ORDER BY section_name"
        ):
            sections.append({
                "section_name": str(name),
                "revision": int(revision),
                "is_deleted": int(deleted),
                "checksum": str(checksum),
                "payload_sha256": hashlib.sha256(str(payload).encode("utf-8")).hexdigest(),
            })
        result = {
            "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "schema": schema,
            "store_meta": meta,
            "sections": sections,
            "checks": checks(connection),
        }
    result["normalized_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def exercise(module: Any, db_path: Path) -> dict[str, Any]:
    backend_class = module.SqliteStoreBackend
    backend = backend_class(db_path, lambda value: value, lambda: {})
    loaded = backend.load_store()  # load
    next_revision = backend.next_revision()
    prefix = "__shadow_validation__"
    changed = {
        f"{prefix}_a": (next_revision, {"kind": "probe", "n": 1}),
        f"{prefix}_b": (next_revision + 1, ["probe", 2]),
    }
    confirmed = dict(backend.save_sections(changed, {}))  # incremental save
    retry = dict(backend.save_sections(changed, {}))  # idempotent retry
    before_retry = normalized(db_path)["normalized_sha256"]
    backend.save_sections(changed, {})
    after_retry = normalized(db_path)["normalized_sha256"]

    conflict_seen = False
    rollback_section = f"{prefix}_must_rollback"
    try:
        backend.save_sections(
            {
                rollback_section: (next_revision + 2, {"must": "rollback"}),
                f"{prefix}_a": (next_revision, {"kind": "different"}),
            },
            {},
        )
    except module.SqliteRevisionConflictError:
        conflict_seen = True

    tombstone_revision = next_revision + 3
    backend.save_sections({}, {f"{prefix}_b": tombstone_revision})
    tombstones = dict(backend.deleted_section_revisions([f"{prefix}_b", "absent"]))

    del backend
    reopened = backend_class(db_path, lambda value: value, lambda: {})
    reopened_data = reopened.load_store()  # close/reopen behavior
    rollback_absent = rollback_section not in reopened_data

    # SQLite itself must roll back an interrupted uncommitted transaction.
    interrupted = f"{prefix}_interrupted"
    connection = sqlite3.connect(db_path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO store_sections(section_name,payload_json,updated_at,checksum,schema_version,revision,is_deleted) "
        "VALUES(?,?,?,?,?,?,?)",
        (interrupted, "null", 0.0, hashlib.sha256(b"null").hexdigest(), 2, next_revision + 4, 0),
    )
    connection.close()
    with readonly_connection(db_path) as check:
        interrupted_absent = check.execute(
            "SELECT count(*) FROM store_sections WHERE section_name=?", (interrupted,)
        ).fetchone()[0] == 0

    return {
        "initial_section_count": len(loaded),
        "confirmed": confirmed,
        "retry": retry,
        "idempotent_physical_state": before_retry == after_retry,
        "revision_conflict_seen": conflict_seen,
        "conflict_transaction_rolled_back": rollback_absent,
        "tombstone_revision": tombstones.get(f"{prefix}_b"),
        "close_reopen_ok": f"{prefix}_a" in reopened_data and f"{prefix}_b" not in reopened_data,
        "interrupted_transaction_rolled_back": interrupted_absent,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default="2ad6f30")
    args = parser.parse_args()
    plugin_root = args.plugin_root.resolve()
    data_root = args.data_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    plugin_data = data_root / "plugin_data" / PLUGIN_NAME
    config = data_root / "config" / f"{PLUGIN_NAME}_config.json"
    marker = plugin_data / ".storage-backend-state.json"
    if not plugin_data.is_dir() or not config.is_file():
        raise SystemExit("AstrBot plugin data/config evidence is incomplete")
    configuration = json.loads(config.read_text(encoding="utf-8-sig"))
    marker_data = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None

    database_paths = sorted(
        path for path in plugin_data.rglob("*")
        if path.is_file() and path.suffix.lower() in DB_SUFFIXES
    )
    backups = []
    for index, source in enumerate(database_paths, 1):
        backups.append(backup_one(source, output / "backups" / f"{index:03d}_{source.name}"))

    baseline_dir = output / "baseline_source" / "storage"
    baseline_dir.mkdir(parents=True)
    for name in ("backend_base.py", "sqlite_backend.py"):
        content = subprocess.check_output(
            ["git", "-C", str(plugin_root), "show", f"{args.baseline}:storage/{name}"]
        )
        (baseline_dir / name).write_bytes(content)

    current_module = load_backend(plugin_root / "storage" / "sqlite_backend.py", "shadow_current")
    baseline_module = load_backend(baseline_dir / "sqlite_backend.py", "shadow_baseline")

    # Production currently may use JSON.  Build a private seed from that configured
    # store, then clone it using Backup API so both implementations start identically.
    seed = output / "seed.db"
    if str(configuration.get("storage_backend", "json")).lower() == "sqlite":
        configured = str(configuration.get("storage_sqlite_path", "") or "companions.db")
        candidate = Path(configured)
        source_store = candidate if candidate.is_absolute() else plugin_data / candidate
        if not source_store.is_file():
            raise SystemExit("configured SQLite store does not exist")
        backup_one(source_store, seed)
        seed_basis = "configured_sqlite_backup"
    else:
        json_store = plugin_data / "companions.json"
        if not json_store.is_file():
            raise SystemExit("configured JSON store does not exist")
        payload = json.loads(json_store.read_text(encoding="utf-8"))
        current_module.SqliteStoreBackend(seed, lambda value: value, lambda: {}).save_store(payload)
        del payload
        seed_basis = "configured_json_converted_inside_output"

    implementation_results = {}
    final_states = {}
    for label, module in (("baseline", baseline_module), ("current", current_module)):
        target = output / f"{label}.db"
        with readonly_connection(seed) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
        implementation_results[label] = exercise(module, target)
        final_states[label] = normalized(target)

    equivalent = final_states["baseline"] == final_states["current"]
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "location_evidence": {
            "astrbot_data_root_argument": str(data_root),
            "plugin_config_path": str(config),
            "plugin_data_path": str(plugin_data),
            "plugin_data_marker_path": str(marker) if marker.exists() else None,
            "configured_backend": configuration.get("storage_backend", "json"),
            "configured_sqlite_path": configuration.get("storage_sqlite_path", ""),
            "marker_backend": marker_data.get("backend") if isinstance(marker_data, dict) else None,
            "marker_sqlite_path": marker_data.get("sqlite_path") if isinstance(marker_data, dict) else None,
        },
        "source_database_count": len(database_paths),
        "backups": backups,
        "baseline_revision": args.baseline,
        "current_revision": subprocess.check_output(
            ["git", "-C", str(plugin_root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "seed_basis": seed_basis,
        "implementation_results": implementation_results,
        "final_states": final_states,
        "normalized_equivalent": equivalent,
        "privacy": "No payload values are emitted; payloads are represented by SHA-256 only.",
    }
    (output / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(output),
        "source_database_count": len(database_paths),
        "normalized_equivalent": equivalent,
        "baseline_checks": final_states["baseline"]["checks"],
        "current_checks": final_states["current"]["checks"],
        "implementation_results": implementation_results,
    }, ensure_ascii=False, indent=2))
    return 0 if equivalent and all(
        state["checks"][key] == ["ok"]
        for state in final_states.values() for key in ("quick_check", "integrity_check")
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
