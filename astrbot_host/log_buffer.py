"""Small in-process ring buffer for this plugin's recent log records.

The buffer is intentionally process-local: it exists to answer "what did the
plugin just do?" from the governance page without reading AstrBot's shared log
file or retaining anything across a restart. Only loggers owned by this plugin
and its V2 package are captured; the dashboard API still requires an
authenticated administrator.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import threading
import traceback

MAX_RECORDS = 2000
MAX_MESSAGE_CHARS = 4000
_MAX_QUERY_CHARS = 120
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_PREFIXES = ("astrbot_plugin_private_companion", "companion_v2", "astrbot_host")
_records: deque[dict] = deque(maxlen=MAX_RECORDS)
_lock = threading.Lock()
_next_id = 0
_handler: logging.Handler | None = None


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _next_id
        name = record.name or ""
        if not name.startswith(_PREFIXES):
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if record.exc_info:
            message += "\n" + "".join(traceback.format_exception(*record.exc_info))
        message = message[:MAX_MESSAGE_CHARS]
        with _lock:
            _next_id += 1
            _records.append({
                "id": _next_id,
                "at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": name[:120],
                "message": message,
            })


def install_log_buffer() -> None:
    """Attach the ring buffer once, to the root logger, with a name filter."""
    global _handler
    if _handler is not None:
        return
    handler = _BufferHandler()
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    _handler = handler


def snapshot(*, level: str = "", query: str = "", limit: int = 200, before: int = 0) -> dict:
    level = str(level or "").strip().upper()
    if level not in LEVELS:
        level = ""
    query = str(query or "").strip().lower()[:_MAX_QUERY_CHARS]
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(500, limit))
    try:
        before = int(before)
    except (TypeError, ValueError):
        before = 0
    with _lock:
        rows = list(_records)
    if before > 0:
        rows = [row for row in rows if row["id"] < before]
    if level:
        rank = logging.getLevelName(level)
        rows = [row for row in rows if logging.getLevelName(row["level"]) >= rank]
    if query:
        rows = [row for row in rows if query in row["message"].lower()
                or query in row["logger"].lower()]
    rows = rows[-limit:]
    return {
        "records": rows,
        "levels": list(LEVELS),
        "total": len(_records),
        "next_before": rows[0]["id"] if rows else 0,
    }
