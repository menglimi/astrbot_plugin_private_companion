# -*- coding: utf-8 -*-
"""In-process bounded log buffer for the companion page.

The host logger normally only writes to the AstrBot console/file.  When a user
reports "刚才为什么没回", the interesting lines have already scrolled away.  This
handler keeps the most recent records in memory (no file, no database) and the
page API can page through them.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
from typing import Any

from astrbot.api import logger

from .plugin_identity import PLUGIN_ID

MAX_RECORDS = 2000
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_PLUGIN_ROOT = Path(__file__).absolute().parent
_PLUGIN_LOGGER = f"astrbot.plugin.{PLUGIN_ID}"
_HANDLER_MARKER = "_private_companion_v1_log_buffer"


def _belongs_to_plugin(record: logging.LogRecord) -> bool:
    return record.name == _PLUGIN_LOGGER or Path(record.pathname).absolute().is_relative_to(_PLUGIN_ROOT)


def _record_payload(record: logging.LogRecord) -> dict[str, Any]:
    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)
    return {
        "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": message,
        "module": record.module,
        "line": record.lineno,
    }


class LogRingBuffer(logging.Handler):
    def __init__(self, capacity: int = MAX_RECORDS) -> None:
        super().__init__(logging.DEBUG)
        self._records: deque[dict[str, Any]] = deque(maxlen=max(100, int(capacity)))
        self._lock = threading.Lock()
        self._sequence = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = _record_payload(record)
            with self._lock:
                self._sequence += 1
                payload["seq"] = self._sequence
                self._records.append(payload)
        except Exception:
            self.handleError(record)

    def snapshot(self, *, level: str = "", query: str = "", limit: int = 200,
                 before: int | None = None) -> tuple[list[dict[str, Any]], int | None]:
        minimum = _LEVELS.get(str(level or "").upper(), 0)
        needle = str(query or "").strip().casefold()
        limit = max(1, min(1000, int(limit or 200)))
        with self._lock:
            rows = list(self._records)
        selected = []
        for row in reversed(rows):
            if before is not None and row["seq"] >= before:
                continue
            if _LEVELS.get(row["level"], 0) < minimum:
                continue
            if needle and needle not in row["message"].casefold():
                continue
            selected.append(row)
            if len(selected) >= limit:
                break
        selected.reverse()
        next_before = selected[0]["seq"] - 1 if len(selected) >= limit and selected else None
        return selected, next_before


_BUFFER: LogRingBuffer | None = None
_BUFFER_LOCK = threading.Lock()


def install_log_buffer() -> LogRingBuffer:
    """Attach one buffer to the plugin logger; repeated calls are harmless."""
    global _BUFFER
    with _BUFFER_LOCK:
        if _BUFFER is not None:
            return _BUFFER
        buffer = LogRingBuffer()
        buffer.addFilter(_belongs_to_plugin)
        setattr(buffer, _HANDLER_MARKER, True)
        for existing in tuple(logger.handlers):
            if getattr(existing, _HANDLER_MARKER, False):
                logger.removeHandler(existing)
                existing.close()
        logger.addHandler(buffer)
        _BUFFER = buffer
        return buffer


def snapshot(*, level: str = "", query: str = "", limit: int = 200,
             before: int | None = None) -> tuple[list[dict[str, Any]], int | None]:
    buffer = _BUFFER
    if buffer is None:
        return [], None
    return buffer.snapshot(level=level, query=query, limit=limit, before=before)
