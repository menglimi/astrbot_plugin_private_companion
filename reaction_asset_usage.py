# -*- coding: utf-8 -*-
"""Sidecar persistence for reaction usage, separate from catalog identity."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


class ReactionAssetUsageStore:
    """Persist delivery counters without rewriting the searchable catalog."""

    def __init__(self, root: Path) -> None:
        self.path = root / "usage.json"
        self._cached: dict[str, dict[str, float | int]] | None = None
        self._stamp: tuple[int, int, int, int] | None = None

    def _file_stamp(self) -> tuple[int, int, int, int]:
        try:
            stat = self.path.stat()
        except OSError:
            return (0, 0, 0, 0)
        return (int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_size), int(stat.st_ino))

    def load(self) -> dict[str, dict[str, float | int]]:
        stamp = self._file_stamp()
        if self._cached is not None and stamp == self._stamp:
            return self._cached
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            raw = {}
        records = raw.get("items") if isinstance(raw, dict) else {}
        clean: dict[str, dict[str, float | int]] = {}
        if isinstance(records, dict):
            for item_id, value in records.items():
                if not isinstance(value, dict) or not str(item_id).strip():
                    continue
                try:
                    count = max(0, int(value.get("usage_count") or 0))
                    last_used = max(0.0, float(value.get("last_used_at") or 0.0))
                except (TypeError, ValueError):
                    continue
                clean[str(item_id)] = {"usage_count": count, "last_used_at": last_used}
        self._cached = clean
        self._stamp = stamp
        return clean

    def get(self, item_id: Any) -> dict[str, float | int] | None:
        return self.load().get(str(item_id or ""))

    def mark_used(self, item_id: str, *, baseline_count: int = 0, used_at: float | None = None) -> None:
        records = {key: dict(value) for key, value in self.load().items()}
        current = records.get(item_id, {})
        count = max(int(current.get("usage_count") or 0), max(0, int(baseline_count))) + 1
        records[item_id] = {
            "usage_count": count,
            "last_used_at": float(used_at if used_at is not None else time.time()),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "items": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self._cached = records
        self._stamp = self._file_stamp()

    def delete(self, item_ids: set[str]) -> None:
        records = {key: dict(value) for key, value in self.load().items() if key not in item_ids}
        if self._cached is not None and records == self._cached:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps({"version": 1, "items": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self._cached = records
        self._stamp = self._file_stamp()
