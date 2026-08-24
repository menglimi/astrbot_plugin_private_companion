# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage.sqlite_backend import SqliteStoreNotInitializedError
from .storage.store_manager import StoreManager


_MIGRATION_LOCKS_GUARD = threading.Lock()
_MIGRATION_LOCKS: dict[str, threading.RLock] = {}


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
        raise ValueError("secondary persona_id is required")

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


__all__ = [
    "PersonaSqliteStoreError",
    "PersonaSqliteStoreHandle",
    "PersonaSqliteStoreRegistry",
    "load_persona_sqlite_store",
]
