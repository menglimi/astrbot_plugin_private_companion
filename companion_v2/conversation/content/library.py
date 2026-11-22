"""Relationship-scoped catalogue and immutable manuscript previews."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from companion_v2.content.models import WorkOwner, digest as manuscript_hash, identifier
from companion_v2.execution.models import TaskError
from companion_v2.storage.sqlite_path import readonly_uri

from .access import document, owner_for


def check_audience(stored, current):
    names = (
        "installation_id",
        "platform",
        "kind",
        "native_scope_id",
        "bot_id",
        "account_id",
    )
    if stored.get("recipients") != current["recipients"] or any(
        stored.get("scope", {}).get(name) != current["scope"][name] for name in names
    ):
        raise TaskError(
            "content_work_audience", "this work belongs to a different audience"
        )


def _visible(access, context, row, sources):
    check_audience(json.loads(row["audience_json"]), document(context.audience))
    for source in json.loads(sources):
        access.reference(source, context)


def _base_work_status(origin, *, head_schema, finished_at_head):
    legacy = origin.get("legacy_status")
    status = (
        "writing"
        if (
            legacy in ("draft", "drafting", "writing", "in_progress")
            or origin.get("scheduled_serial") is True
            or head_schema == "companion.calendar-authoring.v1"
            and finished_at_head == 0
        )
        else "finished"
    )
    if finished_at_head == 1:
        status = "finished"
    return status


def _parse_iso_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def summarize_role_works(
    path,
    *,
    installation_id: str,
    bot_id: str,
    role_id: str,
    now: datetime | None = None,
    limit: int = 200,
) -> dict[str, object]:
    """Return bounded, metadata-only work counts for role-level planning."""
    empty = {
        "available": False,
        "works_total": 0,
        "drafting_works": 0,
        "days_since_last_work": None,
    }
    database = Path(path)
    if not database.is_file():
        return empty
    limit = max(1, min(int(limit), 500))
    try:
        with closing(sqlite3.connect(readonly_uri(database), uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT w.origin_json, w.created_at, v.created_at AS head_created_at,
                    json_extract(v.origin_json, '$.request.finished') AS finished_at_head,
                    json_extract(v.origin_json, '$.request.schema') AS head_schema,
                    (SELECT MAX(v2.created_at)
                     FROM content_v2_versions v2
                     WHERE v2.owner_key = w.owner_key AND v2.work_id = w.work_id) AS latest_version_at,
                    (SELECT json_extract(c.origin_json, '$.request.finished')
                     FROM content_v2_versions c
                     WHERE c.owner_key = w.owner_key
                       AND c.work_id = w.work_id
                       AND c.base_version = w.head_version
                       AND c.base_revision = w.revision
                     ORDER BY c.created_at DESC, c.rowid DESC
                     LIMIT 1) AS latest_candidate_finished,
                    EXISTS(
                     SELECT 1
                     FROM content_v2_versions c
                     WHERE c.owner_key = w.owner_key
                       AND c.work_id = w.work_id
                       AND c.base_version = w.head_version
                       AND c.base_revision = w.revision
                    ) AS has_candidate
                FROM content_v2_works w
                LEFT JOIN content_v2_versions v
                    ON v.owner_key = w.owner_key
                   AND v.work_id = w.work_id
                   AND v.version_id = w.head_version
                WHERE w.state = 'active'
                  AND json_extract(w.owner_json, '$.installation_id') = ?
                  AND json_extract(w.owner_json, '$.bot_id') = ?
                  AND json_extract(w.owner_json, '$.role_id') = ?
                ORDER BY w.created_at DESC, w.work_id DESC
                LIMIT ?""",
                (installation_id, bot_id, role_id, limit),
            ).fetchall()
    except (sqlite3.Error, ValueError, OSError):
        return empty
    works_total = len(rows)
    drafting_works = 0
    latest_dt = None
    for row in rows:
        try:
            origin = json.loads(row["origin_json"]).get("request") or {}
        except (TypeError, ValueError, AttributeError):
            origin = {}
        status = _base_work_status(
            origin,
            head_schema=row["head_schema"],
            finished_at_head=row["finished_at_head"],
        )
        has_candidate = bool(row["has_candidate"])
        candidate_finished = row["latest_candidate_finished"] == 1 if has_candidate else False
        if has_candidate:
            status = "writing"
        if status == "writing" and not candidate_finished:
            drafting_works += 1
        for stamp_value in (row["latest_version_at"], row["head_created_at"], row["created_at"]):
            parsed = _parse_iso_timestamp(stamp_value)
            if parsed is not None and (latest_dt is None or parsed > latest_dt):
                latest_dt = parsed
    days_since_last_work = None
    if latest_dt is not None:
        reference_now = now or datetime.now(timezone.utc)
        if reference_now.tzinfo is None:
            reference_now = reference_now.replace(tzinfo=timezone.utc)
        days_since_last_work = max(0, (reference_now - latest_dt).days)
    return {
        "available": True,
        "works_total": works_total,
        "drafting_works": drafting_works,
        "days_since_last_work": days_since_last_work,
    }


def catalogue(path, access, context, *, limit=100):
    owner = WorkOwner(**owner_for(context))
    with closing(sqlite3.connect(readonly_uri(path), uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        rows = db.execute(
            """SELECT w.*, v.sources_json AS head_sources,
                json_extract(v.origin_json, '$.request.finished') AS finished_at_head,
                json_extract(v.origin_json, '$.request.schema') AS head_schema
            FROM content_v2_works w JOIN content_v2_versions v
                ON v.owner_key=w.owner_key AND v.work_id=w.work_id AND v.version_id=w.head_version
            WHERE w.owner_key=? AND w.state='active'
            ORDER BY w.created_at DESC, w.work_id DESC LIMIT ?""",
            (owner.key, limit),
        ).fetchall()
        result = []
        for row in rows:
            try:
                _visible(access, context, row, row["head_sources"])
            except TaskError:
                continue
            origin = json.loads(row["origin_json"]).get("request") or {}
            status = _base_work_status(
                origin,
                head_schema=row["head_schema"],
                finished_at_head=row["finished_at_head"],
            )
            candidate, candidate_at, candidate_finished, candidate_append_only = (
                None,
                None,
                False,
                False,
            )
            drafts = db.execute(
                """SELECT version_id, sources_json, created_at,
                      json_extract(origin_json, '$.request.finished') AS finished,
                      json_extract(manuscript_json, '$[0].chapter_id') AS first_chapter,
                      (SELECT json_extract(manuscript_json, '$[0].chapter_id')
                       FROM content_v2_versions AS h WHERE h.owner_key=v.owner_key AND h.work_id=v.work_id
                         AND h.version_id=v.base_version) AS base_first_chapter
                FROM content_v2_versions v WHERE owner_key=? AND work_id=? AND base_version=?
                    AND base_revision=? ORDER BY created_at DESC, rowid DESC""",
                (owner.key, row["work_id"], row["head_version"], row["revision"]),
            )
            for draft in drafts:
                try:
                    _visible(access, context, row, draft["sources_json"])
                except TaskError:
                    continue
                candidate, candidate_at = draft["version_id"], draft["created_at"]
                candidate_finished = draft["finished"] == 1
                candidate_append_only = (
                    draft["first_chapter"] != draft["base_first_chapter"]
                )
                status = "writing"
                break
            result.append(
                {
                    "work_id": row["work_id"],
                    "title": row["title"],
                    "work_type": row["work_type"],
                    "revision": row["revision"],
                    "head_version": row["head_version"],
                    "created_at": row["created_at"],
                    "state": status,
                    "candidate_version": candidate,
                    "candidate_at": candidate_at,
                    "candidate_finished": candidate_finished,
                    "candidate_append_only": candidate_append_only,
                    "needs_writing": status == "writing" and not candidate_finished,
                    "target_chars": origin.get("legacy_target_chars"),
                }
            )
    return result


def manuscript(path, access, context, work_id, version_id):
    owner = WorkOwner(**owner_for(context))
    with closing(sqlite3.connect(readonly_uri(path), uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """SELECT w.audience_json, v.manuscript_json, v.content_hash, v.sources_json
            FROM content_v2_works w JOIN content_v2_versions v USING(owner_key, work_id)
            WHERE w.owner_key=? AND w.work_id=? AND w.state='active' AND v.version_id=?""",
            (owner.key, identifier(work_id), identifier(version_id)),
        ).fetchone()
        if row is None:
            raise TaskError(
                "content_work_unavailable", "this work version is unavailable"
            )
        _visible(access, context, row, row["sources_json"])
        if manuscript_hash(row["manuscript_json"]) != row["content_hash"]:
            raise TaskError(
                "content_version_changed", "the original manuscript hash changed"
            )
        return {
            "chapters": json.loads(row["manuscript_json"]),
            "content_hash": row["content_hash"],
            "sources": json.loads(row["sources_json"]),
        }
