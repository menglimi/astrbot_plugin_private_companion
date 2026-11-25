"""Consistent, checksummed snapshots for the independent V1 memory database."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import shutil
import stat
import tempfile

from .archive import build_v1_archive_plan, copy_v1_archive, verify_v1_archive
from .sqlite_snapshot import open_readonly_sqlite_snapshot


REQUIRED_TABLES = frozenset(("memories", "timeline"))
MAX_DATABASE_BYTES = 2 * 1024 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class MemoryArchiveAsset:
    relative_path: str
    source_path: Path
    kind: str
    size: int
    modified_ns: int
    device: int
    inode: int
    wal_signature: tuple | None = None
    table_counts: dict | None = None
    schema_fingerprint: str = ""
    sha256: str = ""

    @property
    def wal_bytes(self):
        return self.wal_signature[0] if self.wal_signature else 0

    def fingerprint_item(self):
        return [self.relative_path, self.kind,
                [self.size, self.modified_ns, self.device, self.inode],
                self.wal_signature, self.table_counts or {}, self.schema_fingerprint, self.sha256]

    def document(self):
        counts = self.table_counts or {}
        return {
            "path": self.relative_path,
            "kind": self.kind,
            "size": self.size,
            "wal_bytes": self.wal_bytes,
            "table_count": len(counts),
            "record_count": sum(counts.values()),
            "table_counts": dict(counts),
            "schema_fingerprint": self.schema_fingerprint,
            "sha256": self.sha256,
        }


class _MemoryPackageTooLarge(Exception):
    def __init__(self, total_bytes):
        self.total_bytes = total_bytes


@dataclass(frozen=True)
class MemoryDatabasePlan:
    path: Path
    state: str
    fingerprint: str = ""
    database_bytes: int = 0
    wal_bytes: int = 0
    table_counts: dict | None = None
    schema_fingerprint: str = ""
    detail: str = ""
    assets: tuple[MemoryArchiveAsset, ...] = ()
    planned_total_bytes: int | None = None
    issues: tuple[tuple[str, str], ...] = ()

    @property
    def total_bytes(self):
        if self.planned_total_bytes is not None:
            return self.planned_total_bytes
        if self.assets:
            return sum(asset.size + asset.wal_bytes for asset in self.assets)
        return self.database_bytes + self.wal_bytes

    def document(self):
        counts = self.table_counts or {}
        return {
            "state": self.state,
            "database_bytes": self.database_bytes,
            "wal_bytes": self.wal_bytes,
            "total_bytes": self.total_bytes,
            "table_count": len(counts),
            "record_count": sum(counts.values()),
            "table_counts": dict(counts),
            "fingerprint": self.fingerprint,
            "detail": self.detail,
            "assets": [asset.document() for asset in self.assets],
            "complete": self.state == "ready" and not self.issues,
            "excluded_count": len(self.issues),
            "issues": [{"path": path, "reason": reason} for path, reason in self.issues],
        }


def _file_signature(path: Path, *, optional=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise ValueError("memory_archive_source_missing")
    if (not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
        raise ValueError("memory_archive_source_unsafe")
    return (int(info.st_size), int(info.st_mtime_ns), int(info.st_dev), int(info.st_ino))


def _database_signatures(path: Path):
    database_signature = _file_signature(path)
    wal_path = Path(str(path) + "-wal")
    wal_signature = _file_signature(wal_path, optional=True)
    if wal_signature is not None and wal_signature[0] == 0:
        wal_signature = None
    _file_signature(Path(str(path) + "-shm"), optional=True)
    return database_signature, wal_signature


def _connect_readonly(path: Path):
    return open_readonly_sqlite_snapshot(path, timeout=5)


def _inventory(connection, *, required_tables=REQUIRED_TABLES):
    schema = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    tables = sorted(str(row[1]) for row in schema if row[0] == "table")
    missing = required_tables - set(tables)
    if missing:
        raise ValueError("memory_archive_schema_unsupported")
    counts = {}
    for name in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
    schema_fingerprint = hashlib.sha256(json.dumps(schema, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return counts, schema_fingerprint


def _sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_sha256(path: Path, wal_signature):
    digest = hashlib.sha256()
    digest.update(b"memory-sqlite-pair.v1\0")
    digest.update(bytes.fromhex(_sha256_file(path)))
    if wal_signature is None:
        digest.update(b"\0wal-absent")
    else:
        digest.update(b"\0wal-present\0")
        digest.update(bytes.fromhex(_sha256_file(Path(str(path) + "-wal"))))
    return digest.hexdigest()


def _sqlite_asset(path: Path, relative_path: str, *, required_tables=frozenset()):
    database_signature, wal_signature = _database_signatures(path)
    wal_path = Path(str(path) + "-wal")
    connection = _connect_readonly(path)
    try:
        counts, schema_fingerprint = _inventory(connection, required_tables=required_tables)
    finally:
        connection.close()
    content_sha256 = _database_sha256(path, wal_signature)
    current_wal_signature = _file_signature(wal_path, optional=True)
    if current_wal_signature is not None and current_wal_signature[0] == 0:
        current_wal_signature = None
    if _file_signature(path) != database_signature or current_wal_signature != wal_signature:
        raise ValueError("memory_archive_source_changed")
    return MemoryArchiveAsset(relative_path, path, "sqlite", database_signature[0],
        database_signature[1], database_signature[2], database_signature[3],
        wal_signature, counts, schema_fingerprint, content_sha256)


def _historical_inventory(root: Path, *, excluded):
    files, issues = [], []
    pending = [(root, "")]
    while pending:
        current, prefix = pending.pop()
        try:
            with os.scandir(current) as entries:
                children = sorted(entries, key=lambda item: item.name)
        except OSError:
            issues.append((prefix or ".", "directory_unreadable"))
            continue
        for entry in children:
            path = Path(entry.path)
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                issues.append((relative, "entry_unreadable"))
                continue
            if (stat.S_ISLNK(info.st_mode)
                    or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
                issues.append((relative.replace("\\", "/"), "reparse_point"))
                continue
            if relative in excluded:
                if not stat.S_ISREG(info.st_mode):
                    issues.append((relative.replace("\\", "/"), "unsupported_file_type"))
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append((path, relative))
                continue
            if not stat.S_ISREG(info.st_mode):
                issues.append((relative.replace("\\", "/"), "unsupported_file_type"))
                continue
            try:
                signature = _file_signature(path)
                if _file_signature(path) != signature:
                    issues.append((relative.replace("\\", "/"), "entry_changed"))
                    continue
            except OSError:
                issues.append((relative.replace("\\", "/"), "entry_unreadable"))
                continue
            except ValueError:
                issues.append((relative.replace("\\", "/"), "entry_unsafe"))
                continue
            files.append((relative.replace("\\", "/"), path, signature))
    return tuple(sorted(files, key=lambda item: item[0])), tuple(sorted(set(issues)))


def _hash_historical_files(files):
    assets, issues = [], []
    for relative, path, signature in files:
        try:
            digest = _sha256_file(path)
            if _file_signature(path) != signature:
                issues.append((relative, "entry_changed"))
                continue
        except OSError:
            issues.append((relative, "entry_unreadable"))
            continue
        assets.append(MemoryArchiveAsset(relative, path, "historical_file",
            signature[0], signature[1], signature[2], signature[3], sha256=digest))
    return tuple(assets), tuple(sorted(set(issues)))


def _issue_for_database(path: Path, relative_path: str, root: Path):
    issues = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            issues.append((candidate.relative_to(root).as_posix(), "entry_unreadable"))
            continue
        if (stat.S_ISLNK(info.st_mode)
                or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
            issues.append((candidate.relative_to(root).as_posix(), "reparse_point"))
    if not issues:
        issues.append((relative_path, "database_unreadable"))
    return issues


def build_memory_database_plan(path: str | Path) -> MemoryDatabasePlan:
    candidate = Path(path).expanduser()
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except FileNotFoundError:
        return MemoryDatabasePlan(candidate, "absent", detail="未发现独立记忆库。")
    except OSError:
        return MemoryDatabasePlan(candidate, "unreadable", detail="独立记忆资料目录无法读取。",
            issues=((candidate.name, "entry_unreadable"),))
    # Allow the top-level parent mount/directory itself to traverse OS symlinks
    # (e.g. Android/Termux /sdcard or container bind mounts) while keeping every
    # file and subdirectory inside ``root`` strictly non-symlinked.
    root = resolved_parent
    candidate = root / candidate.name
    try:
        candidate.lstat()
    except FileNotFoundError:
        return MemoryDatabasePlan(candidate, "absent", detail="未发现独立记忆库。")
    except OSError:
        return MemoryDatabasePlan(candidate, "unreadable", detail="独立记忆资料目录无法读取。",
            issues=((candidate.name, "entry_unreadable"),))
    try:
        parent_info = root.stat()
        if not stat.S_ISDIR(parent_info.st_mode):
            raise ValueError("memory_archive_source_unsafe")

        issues = []
        assets = []
        database_bytes = wal_bytes = 0
        main_signature = main_wal = scoped_signature = scoped_wal = None
        try:
            main_signature, main_wal = _database_signatures(candidate)
            database_bytes, wal_bytes = main_signature[0], main_wal[0] if main_wal else 0
        except (OSError, sqlite3.Error, ValueError, OverflowError):
            issues.extend(_issue_for_database(candidate, "memory_companion.db", root))

        scoped = root / "req041_scoped.db"
        try:
            scoped.lstat()
        except FileNotFoundError:
            scoped_present = False
        except OSError:
            scoped_present = False
            issues.append(("req041_scoped.db", "entry_unreadable"))
        else:
            scoped_present = True
        if scoped_present:
            try:
                scoped_signature, scoped_wal = _database_signatures(scoped)
            except (OSError, sqlite3.Error, ValueError, OverflowError):
                issues.extend(_issue_for_database(scoped, "req041_scoped.db", root))

        excluded = {"memory_companion.db", "memory_companion.db-wal", "memory_companion.db-shm",
                    "req041_scoped.db", "req041_scoped.db-wal", "req041_scoped.db-shm"}
        historical_files, historical_issues = _historical_inventory(root, excluded=excluded)
        total_bytes = sum(signature[0] + (wal[0] if wal else 0)
                          for signature, wal in ((main_signature, main_wal),
                                                 (scoped_signature, scoped_wal)) if signature)
        total_bytes += sum(signature[0] for _, _, signature in historical_files)
        if total_bytes > MAX_DATABASE_BYTES:
            raise _MemoryPackageTooLarge(total_bytes)

        if main_signature is not None:
            try:
                main_asset = _sqlite_asset(candidate, "memory_companion.db",
                    required_tables=REQUIRED_TABLES)
                if (main_asset.size, main_asset.modified_ns, main_asset.device, main_asset.inode,
                        main_asset.wal_signature) != (*main_signature, main_wal):
                    issues.append(("memory_companion.db", "entry_changed"))
                else:
                    assets.append(main_asset)
            except (OSError, sqlite3.Error, ValueError, OverflowError):
                issues.extend(_issue_for_database(candidate, "memory_companion.db", root))
        if scoped_signature is not None:
            try:
                scoped_asset = _sqlite_asset(scoped, "req041_scoped.db")
                if (scoped_asset.size, scoped_asset.modified_ns, scoped_asset.device, scoped_asset.inode,
                        scoped_asset.wal_signature) != (*scoped_signature, scoped_wal):
                    issues.append(("req041_scoped.db", "entry_changed"))
                else:
                    assets.append(scoped_asset)
            except (OSError, sqlite3.Error, ValueError, OverflowError):
                issues.extend(_issue_for_database(scoped, "req041_scoped.db", root))
        historical, hash_issues = _hash_historical_files(historical_files)
        assets.extend(historical)
        issues.extend(historical_issues)
        issues.extend(hash_issues)
        issues = tuple(sorted(set(issues)))
        material = {
            "source_path_hash": hashlib.sha256(str(candidate.resolve()).casefold().encode("utf-8")).hexdigest(),
            "assets": [asset.fingerprint_item() for asset in assets],
            "issues": [list(item) for item in issues],
        }
        fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        main_asset = next((asset for asset in assets
                           if asset.relative_path == "memory_companion.db"), None)
        state = "incomplete" if issues else "ready"
        return MemoryDatabasePlan(candidate, state, fingerprint, database_bytes,
            wal_bytes, main_asset.table_counts if main_asset else None,
            main_asset.schema_fingerprint if main_asset else "",
            detail=("部分资料因无法安全读取而未纳入归档。" if issues else ""),
            assets=tuple(assets), planned_total_bytes=total_bytes, issues=issues)
    except _MemoryPackageTooLarge as error:
        return MemoryDatabasePlan(candidate, "too_large", database_bytes=database_bytes,
            wal_bytes=wal_bytes, detail="独立记忆资料包超过单次安全快照上限。",
            planned_total_bytes=error.total_bytes)
    except (OSError, sqlite3.Error, ValueError, OverflowError) as error:
        reason = "source_unsafe" if str(error) == "memory_archive_source_unsafe" else "source_unreadable"
        return MemoryDatabasePlan(candidate, "unreadable",
            detail="独立记忆资料目录无法安全核验。", issues=((".", reason),))


def copy_memory_database_archive(plan: MemoryDatabasePlan, archive_root: str | Path):
    if (plan.state not in {"ready", "incomplete"} or not plan.fingerprint
            or (not plan.assets and not plan.issues)):
        raise ValueError("memory_archive_source_unavailable")
    current = build_memory_database_plan(plan.path)
    if (current.state not in {"ready", "incomplete"}
            or current.fingerprint != plan.fingerprint):
        raise ValueError("memory_archive_source_changed")

    root = Path(archive_root).expanduser().resolve(strict=False)
    source_parent = plan.path.parent.resolve(strict=True)
    if root == source_parent or source_parent in root.parents:
        raise ValueError("memory_archive_destination_inside_source")
    root.mkdir(parents=True, exist_ok=True)
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
        raise ValueError("memory_archive_destination_invalid")

    archive_id = "memory-" + plan.fingerprint
    metadata = {"source_id": "memory.timeline_and_governance",
                "snapshot_kind": "sqlite_backup_api_and_raw_historical_files",
                "source_complete": not plan.issues,
                "source_issues": [[path, reason] for path, reason in plan.issues],
                "table_counts": plan.table_counts or {},
                "schema_fingerprint": plan.schema_fingerprint,
                "assets": [asset.document() for asset in plan.assets],
                "integrity_check": "ok"}
    target = root / archive_id
    if target.exists():
        try:
            verified = verify_v1_archive(target)
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("memory_archive_target_conflict") from error
        if (manifest.get("source_fingerprint") != plan.fingerprint
                or manifest.get("metadata") != metadata):
            raise ValueError("memory_archive_target_conflict")
        return {"archive_id": archive_id, "file_count": verified["file_count"],
                "total_bytes": verified["total_bytes"], "already_present": True,
                "manifest_sha256": hashlib.sha256((target / "manifest.json").read_bytes()).hexdigest(),
                "table_counts": plan.table_counts or {},
                "record_count": sum((plan.table_counts or {}).values()),
                "assets": [asset.document() for asset in plan.assets],
                "source_complete": not plan.issues,
                "source_issues": metadata["source_issues"],
                "integrity_check": "ok", "source_fingerprint": plan.fingerprint}
    if shutil.disk_usage(root).free < plan.total_bytes * 2 + 2 * 1024 * 1024:
        raise OSError("memory_archive_space_insufficient")

    with tempfile.TemporaryDirectory(prefix=".memory-snapshot-", dir=root) as temporary:
        source_root = plan.path.parent.resolve(strict=True)
        for asset in plan.assets:
            destination_path = Path(temporary) / Path(*asset.relative_path.split("/"))
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            source_path = _safe_source_path(source_root, asset.relative_path)
            if asset.kind == "sqlite":
                source = _connect_readonly(source_path)
                destination = sqlite3.connect(destination_path)
                try:
                    source.backup(destination, pages=4096, sleep=0.01)
                    result = destination.execute("PRAGMA integrity_check").fetchall()
                    if result != [("ok",)]:
                        raise ValueError("memory_archive_integrity_failed")
                    required = REQUIRED_TABLES if asset.relative_path == "memory_companion.db" else frozenset()
                    counts, schema_fingerprint = _inventory(destination, required_tables=required)
                finally:
                    destination.close()
                    source.close()
                if (counts != (asset.table_counts or {})
                        or schema_fingerprint != asset.schema_fingerprint):
                    raise ValueError("memory_archive_source_changed")
            else:
                before = _file_signature(source_path)
                if before != (asset.size, asset.modified_ns, asset.device, asset.inode):
                    raise ValueError("memory_archive_source_changed")
                digest = hashlib.sha256()
                with source_path.open("rb") as reader, destination_path.open("xb") as writer:
                    while True:
                        chunk = reader.read(COPY_CHUNK_BYTES)
                        if not chunk:
                            break
                        writer.write(chunk)
                        digest.update(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if (digest.hexdigest() != asset.sha256
                        or _file_signature(source_path) != before):
                    raise ValueError("memory_archive_source_changed")
        current = build_memory_database_plan(plan.path)
        if (current.state not in {"ready", "incomplete"}
                or current.fingerprint != plan.fingerprint):
            raise ValueError("memory_archive_source_changed")

        snapshot_plan = build_v1_archive_plan(temporary)
        archived = copy_v1_archive(snapshot_plan, root, archive_id=archive_id,
            source_fingerprint=plan.fingerprint,
            metadata=metadata)
        return {**archived, "table_counts": plan.table_counts or {},
                "record_count": sum((plan.table_counts or {}).values()),
                "assets": [asset.document() for asset in plan.assets],
                "source_complete": not plan.issues,
                "source_issues": metadata["source_issues"],
                "integrity_check": "ok", "source_fingerprint": plan.fingerprint}


def _safe_source_path(root: Path, relative_path: str):
    relative = PurePosixPath(relative_path)
    if (relative.is_absolute() or not relative.parts
            or any(part in ("", ".", "..") for part in relative.parts)):
        raise ValueError("memory_archive_source_changed")
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as error:
            raise ValueError("memory_archive_source_changed") from error
        if (stat.S_ISLNK(info.st_mode)
                or int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT):
            raise ValueError("memory_archive_source_changed")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise ValueError("memory_archive_source_changed")
    return current
