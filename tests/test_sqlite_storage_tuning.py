from __future__ import annotations

import sqlite3
from pathlib import Path

from astrbot_plugin_private_companion.storage.sqlite_backend import SqliteStoreBackend


def _new_store() -> dict:
    return {"users": {}}


def _ensure_defaults(value: dict) -> dict:
    result = dict(value)
    result.setdefault("users", {})
    return result


def _backend(path: Path) -> SqliteStoreBackend:
    return SqliteStoreBackend(path, _ensure_defaults, _new_store)


def _pragma(connection: sqlite3.Connection, name: str) -> int | str:
    return connection.execute(f"PRAGMA {name}").fetchone()[0]


def test_new_store_uses_32k_pages_bounded_cache_mmap_wal_and_normal_sync(tmp_path: Path) -> None:
    backend = _backend(tmp_path / "new.db")
    connection = backend._connect()
    try:
        assert _pragma(connection, "page_size") == 32768
        assert _pragma(connection, "cache_size") == -32768
        assert _pragma(connection, "mmap_size") == 256 * 1024 * 1024
        assert str(_pragma(connection, "journal_mode")).lower() == "wal"
        assert _pragma(connection, "synchronous") == 1  # NORMAL
        assert _pragma(connection, "wal_autocheckpoint") == 8192
    finally:
        connection.close()


def test_existing_store_keeps_its_page_size_without_vacuum_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "existing.db"
    backend = _backend(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA page_size=4096")
        backend._create_empty_v2_schema(connection)
        assert _pragma(connection, "page_size") == 4096
    finally:
        connection.close()

    connection = backend._connect()
    try:
        assert _pragma(connection, "page_size") == 4096
        assert _pragma(connection, "cache_size") == -32768
        assert _pragma(connection, "mmap_size") == 256 * 1024 * 1024
        assert str(_pragma(connection, "journal_mode")).lower() == "wal"
        assert _pragma(connection, "synchronous") == 1  # NORMAL
        assert _pragma(connection, "wal_autocheckpoint") == 65536
    finally:
        connection.close()
