# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage.sqlite_backend import SqliteStoreBackend, SqliteStoreNotInitializedError
from .storage.store_manager import StoreManager


_MIGRATION_LOCKS_GUARD = threading.Lock()
_MIGRATION_LOCKS: dict[str, threading.RLock] = {}
MAX_PERSONA_READ_ONLY_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_PERSONA_READ_ONLY_SQLITE_BYTES = 256 * 1024 * 1024


def _path_marker(path: str | Path) -> str:
    return str(Path(path).resolve()).casefold()


def _migration_lock(path: str | Path) -> threading.RLock:
    marker = _path_marker(path)
    with _MIGRATION_LOCKS_GUARD:
        lock = _MIGRATION_LOCKS.get(marker)
        if lock is None:
            lock = threading.RLock()
            _MIGRATION_LOCKS[marker] = lock
        return lock


def _callable_identity(callback: Callable[..., Any]) -> tuple[int, int]:
    owner = getattr(callback, "__self__", None)
    function = getattr(callback, "__func__", callback)
    return id(owner), id(function)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is not allowed: {key}")
        result[key] = value
    return result


def _strict_load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_strict_json_object,
    )
    if not isinstance(payload, dict):
        raise TypeError("persona legacy JSON root must be an object")
    return payload


def _strict_load_json_object_read_only(
    path: Path,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    """Read one bounded regular JSON file without following its final symlink."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise RuntimeError("no-follow file reads are unavailable")
    flags = os.O_RDONLY | no_follow
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("persona legacy JSON is not a regular file")
        if before.st_size < 0 or before.st_size > max_bytes:
            raise ValueError("persona legacy JSON exceeds its byte limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise ValueError("persona legacy JSON exceeds its byte limit")
        after = os.fstat(descriptor)
        if (
            (
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
            or len(raw) != after.st_size
        ):
            raise ValueError("persona legacy JSON changed during read-only snapshot")
    finally:
        os.close(descriptor)
    payload = json.loads(
        raw.decode("utf-8", errors="strict"),
        parse_constant=_reject_json_constant,
        object_pairs_hook=_strict_json_object,
    )
    if type(payload) is not dict:
        raise TypeError("persona legacy JSON root must be an object")
    return payload


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _normalized_payload(
    payload: dict[str, Any],
    ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("persona store payload must be an object")
    normalized = ensure_defaults(deepcopy(payload))
    if not isinstance(normalized, dict):
        raise TypeError("ensure_defaults must return an object")
    # Canonicalization validates all values before any SQLite write begins.
    _canonical_json(normalized)
    return normalized


def _sqlite_quick_check(sqlite_path: Path) -> None:
    uri = f"{sqlite_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=15.0)
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    finally:
        connection.close()
    if rows != [("ok",)]:
        details = "; ".join(str(row[0]) for row in rows[:5] if row)
        raise RuntimeError(f"SQLite quick_check failed: {details or 'unknown error'}")


class PersonaSqliteStoreError(RuntimeError):
    def __init__(
        self,
        *,
        persona_id: str,
        phase: str,
        cause: BaseException,
    ) -> None:
        self.persona_id = str(persona_id or "").strip()
        self.phase = str(phase or "unknown")
        self.cause = cause
        super().__init__(
            f"secondary persona SQLite {self.phase} failed for "
            f"{self.persona_id or '<empty>'}: {cause}"
        )


@dataclass(frozen=True)
class PersonaSqliteStoreHandle:
    persona_id: str
    manager: StoreManager
    data: dict[str, Any]
    source: str


@dataclass(frozen=True)
class _RegistryEntry:
    manager: StoreManager
    legacy_marker: str
    callbacks: tuple[tuple[int, int], tuple[int, int]]


class PersonaSqliteStoreRegistry:
    """Cache one SQLite manager per caller-resolved secondary-persona path."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, _RegistryEntry] = {}

    def manager_for(
        self,
        *,
        legacy_json_path: str | Path,
        sqlite_path: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
    ) -> StoreManager:
        legacy_path = Path(legacy_json_path)
        database_path = Path(sqlite_path)
        legacy_marker = _path_marker(legacy_path)
        database_marker = _path_marker(database_path)
        if legacy_marker == database_marker:
            raise ValueError("legacy JSON and persona SQLite paths must be different")
        callbacks = (
            _callable_identity(ensure_defaults),
            _callable_identity(new_store),
        )
        with self._lock:
            entry = self._entries.get(database_marker)
            if entry is not None:
                if entry.legacy_marker != legacy_marker or entry.callbacks != callbacks:
                    raise ValueError(
                        "persona SQLite manager path is already registered with different inputs"
                    )
                return entry.manager
            manager = StoreManager(
                backend_name="sqlite",
                data_file=legacy_path,
                sqlite_path=database_path,
                ensure_defaults=ensure_defaults,
                new_store=new_store,
            )
            self._entries[database_marker] = _RegistryEntry(
                manager=manager,
                legacy_marker=legacy_marker,
                callbacks=callbacks,
            )
            return manager

    def discard(self, sqlite_path: str | Path) -> None:
        with self._lock:
            self._entries.pop(_path_marker(sqlite_path), None)

    def load(
        self,
        *,
        persona_id: str,
        legacy_json_path: str | Path,
        sqlite_path: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
        prepare_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> PersonaSqliteStoreHandle:
        manager = self.manager_for(
            legacy_json_path=legacy_json_path,
            sqlite_path=sqlite_path,
            ensure_defaults=ensure_defaults,
            new_store=new_store,
        )
        return _load_persona_sqlite_store(
            persona_id=persona_id,
            legacy_json_path=Path(legacy_json_path),
            sqlite_path=Path(sqlite_path),
            ensure_defaults=ensure_defaults,
            new_store=new_store,
            manager=manager,
            prepare_payload=prepare_payload,
        )


def _verified_sqlite_payload(
    *,
    manager: StoreManager,
    sqlite_path: Path,
    expected: dict[str, Any] | None,
    ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    loaded = manager.backend.load_store()
    normalized = _normalized_payload(loaded, ensure_defaults)
    if expected is not None and _canonical_json(normalized) != _canonical_json(expected):
        raise ValueError("SQLite read-back content does not match normalized source")
    health = manager.health_check(raise_on_error=True)
    if not isinstance(health, dict) or not bool(health.get("ok")):
        raise RuntimeError(f"SQLite health check failed: {health!r}")
    _sqlite_quick_check(sqlite_path)
    return normalized


def _load_persona_sqlite_store(
    *,
    persona_id: str,
    legacy_json_path: Path,
    sqlite_path: Path,
    ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
    new_store: Callable[[], dict[str, Any]],
    manager: StoreManager,
    prepare_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> PersonaSqliteStoreHandle:
    pid = str(persona_id or "").strip()
    if not pid:
        raise ValueError("persona_id is required")

    with _migration_lock(sqlite_path):
        if legacy_json_path.exists():
            try:
                source = _strict_load_json_object(legacy_json_path)
                normalized_source = _normalized_payload(source, ensure_defaults)
                if prepare_payload is not None:
                    normalized_source = _normalized_payload(
                        prepare_payload(deepcopy(normalized_source)),
                        ensure_defaults,
                    )
            except Exception as exc:
                raise PersonaSqliteStoreError(
                    persona_id=pid,
                    phase="parse_legacy_json",
                    cause=exc,
                ) from exc
            try:
                # SqliteStoreBackend.save_store performs one BEGIN IMMEDIATE transaction.
                manager.backend.save_store(deepcopy(normalized_source))
            except Exception as exc:
                raise PersonaSqliteStoreError(
                    persona_id=pid,
                    phase="write_sqlite",
                    cause=exc,
                ) from exc
            try:
                loaded = _verified_sqlite_payload(
                    manager=manager,
                    sqlite_path=sqlite_path,
                    expected=normalized_source,
                    ensure_defaults=ensure_defaults,
                )
            except Exception as exc:
                raise PersonaSqliteStoreError(
                    persona_id=pid,
                    phase="verify_sqlite",
                    cause=exc,
                ) from exc
            try:
                legacy_json_path.unlink()
            except Exception as exc:
                raise PersonaSqliteStoreError(
                    persona_id=pid,
                    phase="remove_legacy_json",
                    cause=exc,
                ) from exc
            return PersonaSqliteStoreHandle(
                persona_id=pid,
                manager=manager,
                data=loaded,
                source="legacy_json",
            )

        try:
            if manager.backend.exists():
                try:
                    loaded = _verified_sqlite_payload(
                        manager=manager,
                        sqlite_path=sqlite_path,
                        expected=None,
                        ensure_defaults=ensure_defaults,
                    )
                    source = "sqlite"
                except SqliteStoreNotInitializedError:
                    initial = _normalized_payload(new_store(), ensure_defaults)
                    manager.backend.save_store(deepcopy(initial))
                    loaded = _verified_sqlite_payload(
                        manager=manager,
                        sqlite_path=sqlite_path,
                        expected=initial,
                        ensure_defaults=ensure_defaults,
                    )
                    source = "initialized"
            else:
                initial = _normalized_payload(new_store(), ensure_defaults)
                manager.backend.save_store(deepcopy(initial))
                loaded = _verified_sqlite_payload(
                    manager=manager,
                    sqlite_path=sqlite_path,
                    expected=initial,
                    ensure_defaults=ensure_defaults,
                )
                source = "initialized"
        except Exception as exc:
            raise PersonaSqliteStoreError(
                persona_id=pid,
                phase="load_sqlite",
                cause=exc,
            ) from exc
        return PersonaSqliteStoreHandle(
            persona_id=pid,
            manager=manager,
            data=loaded,
            source=source,
        )


def load_persona_sqlite_store(
    *,
    persona_id: str,
    legacy_json_path: str | Path,
    sqlite_path: str | Path,
    ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
    new_store: Callable[[], dict[str, Any]],
    registry: PersonaSqliteStoreRegistry | None = None,
    prepare_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> PersonaSqliteStoreHandle:
    active_registry = registry or PersonaSqliteStoreRegistry()
    return active_registry.load(
        persona_id=persona_id,
        legacy_json_path=legacy_json_path,
        sqlite_path=sqlite_path,
        ensure_defaults=ensure_defaults,
        new_store=new_store,
        prepare_payload=prepare_payload,
    )


def read_persona_store_snapshot_read_only(
    *,
    persona_id: str,
    legacy_json_path: str | Path,
    sqlite_path: str | Path,
    max_bytes: int = MAX_PERSONA_READ_ONLY_SNAPSHOT_BYTES,
) -> dict[str, Any] | None:
    """Read one persisted persona without migration, initialization, or repair."""
    pid = str(persona_id or "").strip()
    if not pid:
        raise ValueError("secondary persona_id is required")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        raise ValueError("persona read-only snapshot limit must be positive")
    legacy_path = Path(legacy_json_path)
    database_path = Path(sqlite_path)

    def regular_file_state(path: Path) -> bool:
        try:
            details = path.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("persona persisted source is not a regular file")
        return True

    try:
        legacy_exists = regular_file_state(legacy_path)
        sqlite_exists = regular_file_state(database_path)
        if legacy_exists and sqlite_exists:
            raise ValueError("persona persisted sources are ambiguous")
        if legacy_exists:
            return _strict_load_json_object_read_only(
                legacy_path,
                max_bytes=max_bytes,
            )
        if not sqlite_exists:
            return None
        backend = SqliteStoreBackend(
            database_path,
            ensure_defaults=lambda payload: payload,
            new_store=dict,
        )
        snapshot = backend.load_store_read_only(
            max_payload_bytes=max_bytes,
            max_database_bytes=MAX_PERSONA_READ_ONLY_SQLITE_BYTES,
        )
        if type(snapshot) is not dict:
            raise TypeError("persona SQLite snapshot root must be an object")
        return snapshot
    except Exception as exc:
        raise PersonaSqliteStoreError(
            persona_id=pid,
            phase="read_only_snapshot",
            cause=exc,
        ) from exc


__all__ = [
    "PersonaSqliteStoreError",
    "PersonaSqliteStoreHandle",
    "PersonaSqliteStoreRegistry",
    "MAX_PERSONA_READ_ONLY_SNAPSHOT_BYTES",
    "MAX_PERSONA_READ_ONLY_SQLITE_BYTES",
    "load_persona_sqlite_store",
    "read_persona_store_snapshot_read_only",
]
