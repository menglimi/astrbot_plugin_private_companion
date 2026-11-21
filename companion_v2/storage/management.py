"""Inventory V2-owned files and apply a bounded snapshot retention policy.

A snapshot is a *pair* that is only ever moved or deleted as a whole: the sidecar
manifest (``companion-v2-<id>.json``) and either a bundle directory
(``companion-v2-<id>.bundle``, the whole-installation format) or a single database
file (``companion-v2-<id>.sqlite3``, the legacy single-database format). Retention,
copy-before-switch and deletion all verify completeness first, so an interrupted
backup is never treated as a restorable snapshot and never takes the last usable
copy with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from uuid import uuid4


DEFAULT_RETENTION_COUNT = 10
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_COUNT, MAX_RETENTION_COUNT = 1, 200
MIN_RETENTION_DAYS, MAX_RETENTION_DAYS = 1, 3650
PENDING_FILENAME = "restore-pending.json"
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{32}$")


def _is_link(path: Path) -> bool:
    """True for symlinks and Windows reparse points (junctions, mount points)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_walk(root: Path):
    """Yield ``(directory, files, links)`` without following links or reparse points.

    ``os.walk(followlinks=False)`` still descends into Windows junctions (they are
    directories, not symlinks), which would let an inventory or a cleanup read and
    delete files outside the plugin data root.
    """
    stack = [Path(root)]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        files, links = [], []
        for entry in entries:
            if _is_link(entry):
                links.append(entry)
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                files.append(entry)
        yield current, files, links


def _remove_tree(path: Path) -> None:
    """Delete one bundle directory without following links or read-only entries."""
    target = Path(path)
    if _is_link(target) or target.is_file():
        for _attempt in range(2):
            try:
                target.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                try:
                    os.chmod(target, stat.S_IWRITE)
                except OSError:
                    return
        return
    if not target.is_dir():
        return
    try:
        children = list(target.iterdir())
    except OSError:
        return
    for child in children:
        _remove_tree(child)
    try:
        target.rmdir()
    except OSError:
        try:
            os.chmod(target, stat.S_IWRITE)
            target.rmdir()
        except OSError:
            pass


def retention_policy(config: object) -> dict[str, int]:
    storage = config.get("storage", {}) if isinstance(config, dict) else {}
    if not isinstance(storage, dict):
        storage = {}
    count, days = storage.get("retention_count", DEFAULT_RETENTION_COUNT), storage.get(
        "retention_days", DEFAULT_RETENTION_DAYS)
    if type(count) is not int or not MIN_RETENTION_COUNT <= count <= MAX_RETENTION_COUNT:
        count = DEFAULT_RETENTION_COUNT
    if type(days) is not int or not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
        days = DEFAULT_RETENTION_DAYS
    return {"retention_count": count, "retention_days": days}


DEFAULT_AUTO_BACKUP_INTERVAL_MINUTES = 1440
MAX_AUTO_BACKUP_INTERVAL_MINUTES = 43200


def valid_auto_backup_interval(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_AUTO_BACKUP_INTERVAL_MINUTES


def auto_backup_policy(config: object) -> dict:
    storage = config.get("storage", {}) if isinstance(config, dict) else {}
    if not isinstance(storage, dict):
        storage = {}
    interval = storage.get("auto_backup_interval_minutes")
    return {"auto_backup_enabled": storage.get("auto_backup_enabled") is True,
            "auto_backup_interval_minutes": (interval if valid_auto_backup_interval(interval)
                                             else DEFAULT_AUTO_BACKUP_INTERVAL_MINUTES)}


def latest_installation_snapshot(data_root: Path, *, backup_directory: Path,
                                 installation_id: str, now: datetime) -> tuple[str, datetime] | None:
    """Return the newest eligible snapshot ID and time, ignoring legacy and future records."""
    records = _snapshot_records(data_root, backup_directory=backup_directory,
                                installation_id=installation_id)
    return next(((item["snapshot_id"], item["created_at"]) for item in records
                 if item["kind"] == "bundle" and item["created_at"] <= now), None)


# -- snapshot discovery ------------------------------------------------------


def _bundle_contents(directory: Path, document: dict) -> tuple[Path, int, str | None]:
    """Return ``(bundle, payload bytes, problem)`` for one v2 manifest."""
    from .backup import BUNDLE_SUFFIX, PAYLOAD_DIRECTORY, _validated_artifacts

    snapshot_id = document.get("snapshot_id")
    bundle = Path(directory) / f"companion-v2-{snapshot_id}{BUNDLE_SUFFIX}"
    payload = bundle / PAYLOAD_DIRECTORY
    if _is_link(bundle) or not bundle.is_dir() or _is_link(payload) or not payload.is_dir():
        return bundle, 0, "bundle_missing"
    try:
        artifacts = _validated_artifacts(document)
    except (ValueError, TypeError, KeyError):
        return bundle, 0, "manifest_invalid"
    total = 0
    for item in artifacts:
        target = payload.joinpath(*PurePosixPath(item["path"]).parts)
        if _is_link(target) or not target.is_file():
            return bundle, 0, "artifact_missing"
        try:
            size = target.stat().st_size
        except OSError:
            return bundle, 0, "artifact_unreadable"
        if size != item["bytes"]:
            return bundle, 0, "artifact_size_mismatch"
        total += size
    return bundle, total, None


def _scan_directory(directory: Path, *, installation_id: str | None = None) -> tuple[list[dict], list[dict]]:
    """List complete snapshot pairs in one directory, plus any active-format problem.

    A sidecar that cannot be parsed at all is not this version's business and is
    skipped, exactly as before. A parseable whole-installation sidecar whose bundle
    is missing or incomplete is a *problem*: copying or deleting around it would
    hide a broken backup, so the caller has to fail closed instead.
    """
    from .backup import BACKUP_FORMAT, LEGACY_BACKUP_FORMAT

    records: list[dict] = []
    problems: list[dict] = []
    if not directory.is_dir() or _is_link(directory):
        return records, problems
    for sidecar in sorted(directory.glob("companion-v2-*.json")):
        if _is_link(sidecar):
            continue
        try:
            document = json.loads(sidecar.read_text(encoding="utf-8"))
            snapshot_id = document.get("snapshot_id")
            if (type(document) is not dict or type(snapshot_id) is not str
                    or not _SNAPSHOT_ID.fullmatch(snapshot_id)
                    or (installation_id is not None and document.get("installation_id") != installation_id)):
                continue
            created_at = datetime.fromisoformat(str(document.get("created_at", "")).replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if created_at.tzinfo is None:
            continue
        if document.get("format") == BACKUP_FORMAT:
            bundle, payload_bytes, problem = _bundle_contents(directory, document)
            if problem is not None:
                problems.append({"snapshot_id": snapshot_id, "reason": problem, "sidecar": sidecar})
                continue
            records.append({"snapshot_id": snapshot_id, "format": BACKUP_FORMAT, "kind": "bundle",
                            "created_at": created_at.astimezone(timezone.utc),
                            "primary": bundle, "sidecar": sidecar,
                            "byte_size": payload_bytes + sidecar.stat().st_size})
        elif document.get("format") == LEGACY_BACKUP_FORMAT:
            database = sidecar.with_name(f"companion-v2-{snapshot_id}.sqlite3")
            if _is_link(database) or not database.is_file():
                # A sidecar without its database is not a snapshot; it also must not
                # vanish from a directory move without a word (review P2-7).
                problems.append({"snapshot_id": snapshot_id, "reason": "snapshot_database_missing",
                                 "sidecar": sidecar})
                continue
            records.append({"snapshot_id": snapshot_id, "format": LEGACY_BACKUP_FORMAT, "kind": "database",
                            "created_at": created_at.astimezone(timezone.utc),
                            "primary": database, "sidecar": sidecar,
                            "byte_size": database.stat().st_size + sidecar.stat().st_size})
    records.sort(key=lambda item: item["created_at"], reverse=True)
    return records, problems


def _snapshot_records(data_root: Path, *, backup_directory: Path | None = None,
                      installation_id: str | None = None) -> list[dict]:
    records: list[dict] = []
    root = Path(data_root)
    directories = (Path(backup_directory),) if backup_directory is not None else backup_directories(root)
    for directory in directories:
        found, _problems = _scan_directory(directory, installation_id=installation_id)
        records.extend(found)
    records.sort(key=lambda item: item["created_at"], reverse=True)
    return records


def _snapshot_scan(data_root: Path, *, backup_directory: Path | None = None,
                   installation_id: str | None = None) -> tuple[list[dict], list[dict]]:
    """Complete records and the broken pairs beside them, in one pass."""
    records: list[dict] = []
    problems: list[dict] = []
    root = Path(data_root)
    directories = (Path(backup_directory),) if backup_directory is not None else backup_directories(root)
    for directory in directories:
        found, broken = _scan_directory(directory, installation_id=installation_id)
        records.extend(found)
        problems.extend({**item, "directory": directory} for item in broken)
    records.sort(key=lambda item: item["created_at"], reverse=True)
    return records, problems


def _pending_protected_ids(data_root: Path) -> frozenset[str]:
    """Snapshots a staged restore still depends on (its source and its safety copy)."""
    pending = Path(data_root) / PENDING_FILENAME
    try:
        document = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if type(document) is not dict:
        return frozenset()
    values = (document.get("snapshot_id"), document.get("safety_snapshot_id"))
    return frozenset(value for value in values if type(value) is str and _SNAPSHOT_ID.fullmatch(value))


# -- retention ---------------------------------------------------------------


def cleanup_plan(data_root: Path, *, retention_count: int, retention_days: int,
                 now: datetime | None = None, backup_directory: Path | None = None,
                 installation_id: str | None = None,
                 preserve_snapshot_ids: tuple[str, ...] | list[str] = (),
                 created_snapshot_id: str | None = None) -> dict:
    """Return complete snapshots outside policy, always keeping the newest eligible one.

    ``incomplete_ids`` names pairs that cannot be counted (missing bundle, missing
    payload) and are therefore never deleted here. They are reported so the
    operator — and a backup-directory move — can see what is blocking instead of
    guessing (review P2-4).
    """
    plan, _ = _cleanup_selection(data_root, retention_count=retention_count, retention_days=retention_days,
                                 now=now, backup_directory=backup_directory, installation_id=installation_id,
                                 preserve_snapshot_ids=preserve_snapshot_ids, created_snapshot_id=created_snapshot_id)
    return plan


def _cleanup_selection(data_root: Path, *, retention_count: int, retention_days: int,
                       now: datetime | None, backup_directory: Path | None, installation_id: str | None,
                       preserve_snapshot_ids, created_snapshot_id: str | None) -> tuple[dict, list[dict]]:
    records, problems = _snapshot_scan(Path(data_root), backup_directory=backup_directory,
                                       installation_id=installation_id)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("cleanup time must include a timezone")
    cutoff = current.astimezone(timezone.utc) - timedelta(days=retention_days)
    eligible = [item for item in records if item["created_at"] <= current]
    # A clock correction must not let a future-dated snapshot consume today's
    # quota. Keep those records untouched until their timestamps become eligible.
    # On equal timestamps prefer the snapshot that this operation just published.
    if created_snapshot_id is not None:
        eligible.sort(key=lambda item: (item["created_at"], item["snapshot_id"] == created_snapshot_id),
                      reverse=True)
    protected = set(preserve_snapshot_ids) | set(_pending_protected_ids(Path(data_root)))
    if created_snapshot_id is not None:
        protected.add(created_snapshot_id)
    removed = [item for index, item in enumerate(eligible)
               if index > 0 and item["snapshot_id"] not in protected
               and (index >= retention_count or item["created_at"] < cutoff)]
    plan = {"snapshot_count": len(records), "future_count": len(records) - len(eligible),
            "remove_count": len(removed),
            "remove_bytes": sum(item["byte_size"] for item in removed),
            "snapshot_ids": [item["snapshot_id"] for item in removed],
            "incomplete_count": len(problems),
            "incomplete_ids": [item["snapshot_id"] for item in problems],
            "incomplete_reasons": [item["reason"] for item in problems]}
    return plan, removed


def cleanup_backups(data_root: Path, *, retention_count: int, retention_days: int,
                    now: datetime | None = None, backup_directory: Path | None = None,
                    installation_id: str | None = None,
                    preserve_snapshot_ids: tuple[str, ...] | list[str] = (),
                    created_snapshot_id: str | None = None) -> dict:
    """Delete only complete, verified-by-manifest snapshot pairs outside policy."""
    plan, candidates = _cleanup_selection(data_root, retention_count=retention_count,
                                          retention_days=retention_days, now=now,
                                          backup_directory=backup_directory, installation_id=installation_id,
                                          preserve_snapshot_ids=preserve_snapshot_ids,
                                          created_snapshot_id=created_snapshot_id)
    removed_count = removed_bytes = 0
    for item in candidates:
        try:
            if item["kind"] == "bundle":
                _remove_tree(item["primary"])
                if item["primary"].exists() or _is_link(item["primary"]):
                    # Keep the manifest when filesystem removal is incomplete.
                    continue
            else:
                item["primary"].unlink()
            item["sidecar"].unlink()
        except OSError:
            # Preserve progress on other pairs, but report a partial cleanup.
            continue
        removed_count += 1
        removed_bytes += item["byte_size"]
    failed_count = plan["remove_count"] - removed_count
    return {**plan, "removed_count": removed_count, "removed_bytes": removed_bytes,
            "failed": failed_count > 0, "failed_count": failed_count}


QUARANTINE_DIRECTORY = ".incomplete"


def quarantine_incomplete_snapshots(directory: Path, *, snapshot_id: str,
                                    data_root: Path | None = None) -> dict:
    """Move one incomplete pair out of the active directory, keeping every byte.

    An interrupted bundle can never be restored, but it also blocks the only
    recovery path a user has: switching the backup directory (``copy_backup_snapshots``
    refuses to move a set that contains one). Quarantining is the smallest safe
    operation: the pair is moved to ``.incomplete/`` under the same directory, so
    nothing is deleted, the history stays auditable, and the move/cleanup paths stop
    seeing it. A snapshot a staged restore still points at is refused.
    """
    directory = Path(directory)
    if type(snapshot_id) is not str or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("a snapshot id is a lowercase hex digest")
    if data_root is not None and snapshot_id in _pending_protected_ids(Path(data_root)):
        raise ValueError("a staged restore still depends on this snapshot")
    records, problems = _scan_directory(directory)
    if any(item["snapshot_id"] == snapshot_id for item in records):
        raise ValueError("this snapshot is complete and must not be quarantined")
    problem = next((item for item in problems if item["snapshot_id"] == snapshot_id), None)
    if problem is None:
        raise ValueError("no incomplete snapshot with that id in this directory")
    sidecar = directory / f"companion-v2-{snapshot_id}.json"
    bundle = directory / f"companion-v2-{snapshot_id}.bundle"
    database = directory / f"companion-v2-{snapshot_id}.sqlite3"
    target = directory / QUARANTINE_DIRECTORY
    target.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for source in (bundle, database, sidecar):
        if not (source.exists() or _is_link(source)):
            continue
        destination = target / source.name
        if destination.exists():
            _remove_tree(destination)
        os.replace(source, destination)
        moved.append(source.name)
    return {"snapshot_id": snapshot_id, "reason": problem["reason"], "moved": moved,
            "quarantine_directory": str(target)}


# -- copy before a directory switch ------------------------------------------


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(root: Path) -> str:
    """A stable digest over a bundle tree: names, sizes and bytes; links fail closed."""
    root = Path(root)
    entries = []
    for _current, files, links in _safe_walk(root):
        if links:
            raise OSError("backup snapshot bundle contains a link")
        for path in files:
            entries.append((path.relative_to(root).as_posix(), path.stat().st_size, _file_digest(path)))
    entries.sort()
    digest = hashlib.sha256()
    for relative, size, content in entries:
        digest.update(f"{relative}\0{size}\0{content}\n".encode("utf-8"))
    return digest.hexdigest()


def _copy_file_verified(source: Path, destination: Path) -> None:
    source, destination = Path(source), Path(destination)
    source_digest = _file_digest(source)
    if destination.exists():
        if (not destination.is_file() or _is_link(destination)
                or destination.stat().st_size != source.stat().st_size
                or _file_digest(destination) != source_digest):
            raise FileExistsError(f"backup snapshot conflicts at {destination.name}")
        return
    temporary = destination.with_name("." + destination.name + "." + uuid4().hex + ".tmp")
    try:
        shutil.copy2(source, temporary)
        if (source.stat().st_size != temporary.stat().st_size
                or _file_digest(temporary) != source_digest):
            raise OSError("backup snapshot copy verification failed")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_bundle_verified(source: Path, destination: Path) -> None:
    source, destination = Path(source), Path(destination)
    source_digest = _tree_digest(source)
    if destination.exists():
        if _is_link(destination) or not destination.is_dir() or _tree_digest(destination) != source_digest:
            raise FileExistsError(f"backup snapshot conflicts at {destination.name}")
        return
    temporary = destination.with_name("." + destination.name + "." + uuid4().hex + ".tmp")
    try:
        shutil.copytree(source, temporary, symlinks=True)
        if _tree_digest(temporary) != source_digest:
            raise OSError("backup snapshot copy verification failed")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            _remove_tree(temporary)


def copy_backup_snapshots(source: Path, destination: Path, *,
                          installation_id: str | None = None,
                          skip_incomplete: bool = False,
                          data_root: Path | None = None,
                          report: dict | None = None) -> tuple[tuple[Path, Path], ...]:
    """Copy complete snapshot pairs, verifying bytes before a config switch.

    Every source pair is validated before the first byte is written, so a partial
    bundle aborts the relocation with the destination untouched instead of leaving
    the new location short of one snapshot. That is still the default.

    ``skip_incomplete=True`` is the way out of the trap the review found (P2-4): one
    interrupted bundle used to make every later move fail forever. Skipped pairs are
    left exactly where they are and named in ``report["skipped"]``; a pair a staged
    restore still points at is never skipped, because moving the installation's
    rollback material would be worse than refusing the move.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return ()
    if destination.exists() and not destination.is_dir():
        raise OSError("backup destination is not a directory")
    if not source.is_dir() or _is_link(source):
        return ()
    records, problems = _scan_directory(source, installation_id=installation_id)
    skipped: list[dict] = []
    if problems:
        if not skip_incomplete:
            detail = ", ".join(f"{item['snapshot_id']}({item['reason']})" for item in problems[:3])
            raise OSError(f"backup snapshots are incomplete and were not copied: {detail}")
        if data_root is not None:
            protected = _pending_protected_ids(Path(data_root))
            blocking = [item for item in problems if item["snapshot_id"] in protected]
            if blocking:
                detail = ", ".join(f"{item['snapshot_id']}({item['reason']})" for item in blocking[:3])
                raise OSError("a staged restore still depends on an incomplete backup snapshot: " + detail)
        skipped = [{"snapshot_id": item["snapshot_id"], "reason": item["reason"],
                    "sidecar": item["sidecar"].name} for item in problems]
    destination.mkdir(parents=True, exist_ok=True)

    transferred = []
    for record in sorted(records, key=lambda item: item["snapshot_id"]):
        if record["kind"] == "bundle":
            _copy_bundle_verified(record["primary"], destination / record["primary"].name)
        else:
            _copy_file_verified(record["primary"], destination / record["primary"].name)
        _copy_file_verified(record["sidecar"], destination / record["sidecar"].name)
        transferred.append((record["primary"], record["sidecar"]))
    if report is not None:
        report.clear()
        report.update({"copied": len(transferred), "skipped": skipped,
                       "skipped_count": len(skipped)})
    return tuple(transferred)


def remove_transferred_backup_snapshots(pairs: tuple[tuple[Path, Path], ...], destination: Path) -> int:
    """Remove source pairs only when the verified destination still matches."""
    destination = Path(destination).resolve()
    removed = 0
    for primary, sidecar in pairs:
        primary, sidecar = Path(primary), Path(sidecar)
        target_primary = destination / primary.name
        target_sidecar = destination / sidecar.name
        if primary.is_dir():
            if not (target_primary.is_dir() and not _is_link(target_primary)
                    and _tree_digest(primary) == _tree_digest(target_primary)):
                continue
            if not (target_sidecar.is_file() and _file_digest(sidecar) == _file_digest(target_sidecar)):
                continue
            _remove_tree(primary)
            sidecar.unlink()
            removed += 1
            continue
        if not (target_primary.is_file() and not _is_link(target_primary)
                and _file_digest(primary) == _file_digest(target_primary)):
            continue
        if not (target_sidecar.is_file() and _file_digest(sidecar) == _file_digest(target_sidecar)):
            continue
        primary.unlink()
        sidecar.unlink()
        removed += 1
    return removed


# -- inventory ---------------------------------------------------------------


def backup_directories(data_root: Path) -> tuple[Path, ...]:
    root = Path(data_root)
    if not root.is_dir():
        return ()
    found = []
    for current, _files, _links in _safe_walk(root):
        if current != root and current.name == "backups" and current.is_dir():
            found.append(current)
    return tuple(sorted(found, key=lambda item: item.as_posix()))


def cleanup_all_backups(data_root: Path, *, retention_count: int, retention_days: int,
                        now: datetime | None = None, dry_run: bool = True) -> dict:
    """Apply the retention policy to every backup directory under one data root.

    ``data_root`` is the root the staged-restore request lives in; passing
    ``directory.parent`` (as this used to) made ``_pending_protected_ids`` look in
    the wrong place, so a cleanup could delete the snapshot a restore was about to
    apply (review P3-3).
    """
    root = Path(data_root)
    summaries = []
    for directory in backup_directories(root):
        if dry_run:
            summary = cleanup_plan(root, retention_count=retention_count,
                                   retention_days=retention_days, now=now,
                                   backup_directory=directory)
        else:
            summary = cleanup_backups(root, retention_count=retention_count,
                                      retention_days=retention_days, now=now,
                                      backup_directory=directory)
        summaries.append(summary)
    return {"directories": len(summaries),
            "snapshot_count": sum(item["snapshot_count"] for item in summaries),
            "remove_count": sum(item["remove_count"] for item in summaries),
            "remove_bytes": sum(item["remove_bytes"] for item in summaries),
            "removed_count": sum(item.get("removed_count", 0) for item in summaries),
            "removed_bytes": sum(item.get("removed_bytes", 0) for item in summaries),
            "failed": any(item.get("failed", False) for item in summaries),
            "failed_count": sum(item.get("failed_count", 0) for item in summaries),
            "future_count": sum(item["future_count"] for item in summaries),
            "incomplete_count": sum(item.get("incomplete_count", 0) for item in summaries),
            "incomplete_ids": [identifier for item in summaries
                               for identifier in item.get("incomplete_ids", [])],
            "snapshot_ids": [snapshot_id for item in summaries for snapshot_id in item["snapshot_ids"]]}



def inventory(data_root: Path, *, backup_directory: Path | None = None) -> dict:
    """Measure V2 plugin data without following links or opening user databases."""
    root = Path(data_root)
    totals = {"database": {"files": 0, "bytes": 0},
              "backups": {"files": 0, "bytes": 0},
              "other": {"files": 0, "bytes": 0}}
    if root.is_dir():
        for _current, files, _links in _safe_walk(root):
            for path in files:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                # Whole-installation bundles keep their payload below
                # ``backups/<id>.bundle``, so the category is decided by any
                # ``backups`` segment, not by the immediate parent.
                relative_parts = path.relative_to(root).parts[:-1]
                if "backups" in relative_parts:
                    group = "backups"
                elif path.name == "companion-v2.sqlite3" or path.name.startswith("companion-v2.sqlite3-"):
                    group = "database"
                else:
                    group = "other"
                totals[group]["files"] += 1
                totals[group]["bytes"] += size
    extra_backup = Path(backup_directory).resolve() if backup_directory is not None else None
    if extra_backup is not None and not extra_backup.is_relative_to(root.resolve()) and extra_backup.is_dir():
        for _current, files, _links in _safe_walk(extra_backup):
            for path in files:
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                totals["backups"]["files"] += 1
                totals["backups"]["bytes"] += size
    total_bytes = sum(value["bytes"] for value in totals.values())
    anchor = root
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    try:
        free_bytes = shutil.disk_usage(anchor).free
    except OSError:
        free_bytes = None
    backup_anchor = extra_backup if extra_backup is not None else root / "backups"
    while not backup_anchor.exists() and backup_anchor != backup_anchor.parent:
        backup_anchor = backup_anchor.parent
    try:
        backup_free_bytes = shutil.disk_usage(backup_anchor).free
    except OSError:
        backup_free_bytes = None
    return {"root": str(root), "total_bytes": total_bytes,
            "categories": totals, "free_bytes": free_bytes, "backup_free_bytes": backup_free_bytes}
