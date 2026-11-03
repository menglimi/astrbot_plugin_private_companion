"""Explicit draft snapshots in a separate SQLite file, never the host runtime DB."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from companion_v2.execution.models import TaskError
from .protocol import document


IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")


class DraftStore:
    def __init__(self, root):
        self.path = Path(root) / "persona_designer" / "drafts.sqlite3"

    def _read(self):
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)

    def list(self, owner, before=0):
        if not self.path.exists():
            return {"items": [], "next_before": None}
        with closing(self._read()) as db:
            rows = db.execute("SELECT sequence, draft_id, title, created_at FROM drafts WHERE owner = ? "
                              "AND (? = 0 OR sequence < ?) ORDER BY sequence DESC LIMIT 51",
                              (owner, before, before)).fetchall()
        return {"items": [{"id": row[1], "title": row[2], "created_at": row[3]} for row in rows[:50]],
                "next_before": rows[49][0] if len(rows) > 50 else None}

    def load(self, owner, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise TaskError("persona_designer_invalid", "请选择一份已保存的草稿。")
        row = None
        if self.path.exists():
            with closing(self._read()) as db:
                row = db.execute("SELECT title, snapshot, created_at FROM drafts WHERE owner = ? AND draft_id = ?",
                                 (owner, identifier)).fetchone()
        if row is None:
            raise TaskError("persona_designer_draft_missing", "这份草稿不存在或不属于当前账号。")
        return {"id": identifier, "title": row[0], "state": document(json.loads(row[1])), "created_at": row[2]}

    def save(self, owner, identifier, title, state):
        if (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
                or type(title) is not str or not title.strip() or len(title) > 120):
            raise TaskError("persona_designer_invalid", "请填写简短的草稿名称。")
        snapshot = json.dumps(document(state), ensure_ascii=False, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS drafts (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "owner TEXT NOT NULL, draft_id TEXT NOT NULL, title TEXT NOT NULL, "
                       "snapshot TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(owner, draft_id))")
            previous = db.execute("SELECT title, snapshot, created_at FROM drafts WHERE owner = ? AND draft_id = ?",
                                  (owner, identifier)).fetchone()
            if previous:
                if previous[:2] != (title.strip(), snapshot):
                    raise TaskError("persona_designer_save_conflict", "草稿内容已变化，请作为新版本保存。")
                created = previous[2]
            else:
                db.execute("INSERT INTO drafts(owner, draft_id, title, snapshot, created_at) VALUES (?, ?, ?, ?, ?)",
                           (owner, identifier, title.strip(), snapshot, created))
        return {"id": identifier, "title": title.strip(), "created_at": created}
