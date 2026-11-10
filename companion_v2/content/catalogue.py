"""Read-only projection of Content-owned works for the management catalogue.

The Content extension keeps its own database and lifecycle.  This projection
opens that file read-only and selects only catalogue columns, never manuscript
versions or source payloads.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

LIMIT = 200


def content_database(data_root) -> Path:
    return Path(data_root) / "extensions" / "content" / "content_v2" / "works.sqlite3"


def read_works(path, *, installation_id: str, bot_id: str, role_id: str, limit: int = LIMIT):
    """Return the active works of one role; never reads manuscript versions."""
    database = Path(path)
    if not database.is_file():
        return {"available": False, "count": 0, "works": []}
    limit = max(1, min(int(limit), LIMIT))
    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    except (sqlite3.Error, ValueError):
        return {"available": False, "count": 0, "works": []}
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT work_id, title, work_type, revision, created_at FROM content_v2_works
               WHERE state = 'active'
                 AND json_extract(owner_json, '$.installation_id') = ?
                 AND json_extract(owner_json, '$.bot_id') = ?
                 AND json_extract(owner_json, '$.role_id') = ?
               ORDER BY created_at DESC, work_id DESC LIMIT ?""",
            (installation_id, bot_id, role_id, limit)).fetchall()
    except sqlite3.Error:
        return {"available": False, "count": 0, "works": []}
    finally:
        connection.close()
    works = [{"work_id": str(row["work_id"]), "title": str(row["title"]),
              "work_type": str(row["work_type"]), "revision": int(row["revision"]),
              "created_at": str(row["created_at"])} for row in rows]
    return {"available": True, "count": len(works), "works": works}
