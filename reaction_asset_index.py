# -*- coding: utf-8 -*-
"""Lookup index, TTL and external-file revision tracking for reaction assets."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable


class ReactionAssetLookupIndex:
    """Own the runtime lookup generation independently of catalog persistence."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.source_stamp: tuple[int, int, int, int] | None = None
        self.revision = ""
        self.has_enabled_assets = False
        self.checked_at = 0.0

    def invalidate(self) -> None:
        self.source_stamp = None
        self.revision = ""
        self.has_enabled_assets = False
        self.checked_at = 0.0

    def hot(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return bool(self.revision and current - self.checked_at < self.ttl_seconds)

    @staticmethod
    def _file_revision(path: Path | None) -> tuple[int, int] | None:
        try:
            stat = path.stat() if path is not None else None
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size)) if stat is not None else None

    def rebuild(
        self,
        items: Iterable[dict[str, Any]],
        *,
        path_for: Callable[[dict[str, Any]], Path | None],
        source_stamp: tuple[int, int, int, int],
        now: float | None = None,
    ) -> tuple[str, bool]:
        """Re-stat every indexed asset after TTL; directory mtimes are hints only."""
        rows: list[dict[str, Any]] = []
        enabled = False
        for item in items:
            file_revision = self._file_revision(path_for(item))
            if item["enabled"] and file_revision is not None:
                enabled = True
            rows.append({
                "id": item["id"],
                "enabled": item["enabled"],
                "scopes": item["scopes"],
                "name": item["name"],
                "description": item["description"],
                "visible_text": item["visible_text"],
                "tags": item["tags"],
                "emotions": item["emotions"],
                "intents": item["intents"],
                "file": file_revision,
            })
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.revision = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        self.has_enabled_assets = enabled
        self.source_stamp = source_stamp
        self.checked_at = time.monotonic() if now is None else now
        return self.revision, enabled
