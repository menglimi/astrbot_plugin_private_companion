# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from astrbot.api import logger

from .backend_base import StoreBackendBase


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

    def backend_name(self) -> str:
        return "sqlite"

    def exists(self) -> bool:
        return self.db_path.exists()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS store_sections ("
            "section_name TEXT PRIMARY KEY, "
            "payload_json TEXT NOT NULL, "
            "updated_at REAL NOT NULL, "
            "checksum TEXT DEFAULT '', "
            "schema_version INTEGER DEFAULT 1)"
        )
        conn.commit()
        return conn

    def load_store(self) -> dict[str, Any]:
        if not self.exists():
            return self.new_store()
        try:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT section_name, payload_json FROM store_sections").fetchall()
            finally:
                conn.close()
            if not rows:
                return self.new_store()
            data: dict[str, Any] = self.new_store()
            for section_name, payload_json in rows:
                try:
                    data[str(section_name)] = json.loads(payload_json)
                except Exception:
                    logger.warning("[PrivateCompanion] SQLite section 读取失败: %s", section_name)
            return self.ensure_defaults(data)
        except Exception as exc:
            logger.warning(f"[PrivateCompanion] 读取 SQLite 数据失败,将使用空数据: {exc}")
            return self.new_store()

    def save_store(self, data: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            now = time.time()
            with conn:
                section_names = [str(section_name) for section_name in data.keys()]
                if section_names:
                    placeholders = ",".join("?" for _ in section_names)
                    conn.execute(
                        f"DELETE FROM store_sections WHERE section_name NOT IN ({placeholders})",
                        section_names,
                    )
                else:
                    conn.execute("DELETE FROM store_sections")
                for section_name, payload in data.items():
                    conn.execute(
                        "REPLACE INTO store_sections(section_name, payload_json, updated_at, checksum, schema_version) "
                        "VALUES (?, ?, ?, '', 1)",
                        (str(section_name), json.dumps(payload, ensure_ascii=False), now),
                    )
        finally:
            conn.close()

    def save_snapshot(self, data: dict[str, Any]) -> None:
        self.save_store(data)

    def health_check(self) -> dict[str, Any]:
        ok = True
        error = ""
        try:
            conn = self._connect()
            conn.execute("SELECT 1")
            conn.close()
        except Exception as exc:
            ok = False
            error = str(exc)
        return {
            "backend": self.backend_name(),
            "path": str(self.db_path),
            "exists": self.exists(),
            "ok": ok,
            "error": error,
        }
