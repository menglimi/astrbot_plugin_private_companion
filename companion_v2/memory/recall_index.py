"""Paged object-change projection shared by source, experience and mixed recall."""

from contextlib import closing
import json
from uuid import uuid4

from companion_v2.execution.models import TaskError, digest, encode, stamp

from .models import owner_key
from .search import terms


CACHE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS recall_documents (
        owner TEXT NOT NULL, id TEXT NOT NULL, producer TEXT NOT NULL, object_id TEXT NOT NULL,
        revision INTEGER NOT NULL, hash TEXT NOT NULL, document_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL, UNIQUE(owner, id))""",
    "CREATE INDEX IF NOT EXISTS recall_object_idx ON recall_documents(owner, producer, object_id)",
    "CREATE INDEX IF NOT EXISTS recall_reflection_time_idx ON recall_documents(owner, json_extract(metadata_json, '$.message_time'), json_extract(metadata_json, '$.source_id'), json_extract(metadata_json, '$.start'), id)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS recall_lexical USING fts5(tokens)",
    "CREATE TABLE IF NOT EXISTS recall_sync (partition TEXT PRIMARY KEY, state_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS recall_epoch (singleton INTEGER PRIMARY KEY, revision INTEGER NOT NULL)",
    "INSERT OR IGNORE INTO recall_epoch VALUES (1, 0)",
    """CREATE TABLE IF NOT EXISTS recall_vectors (
        id TEXT NOT NULL, owner TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL,
        dimensions INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(owner, model, id))""",
    """CREATE TABLE IF NOT EXISTS recall_vector_jobs (
        job TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL, state_json TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS recall_vector_expiry_idx ON recall_vector_jobs(expires_at)",
    """CREATE TABLE IF NOT EXISTS recall_vector_scores (
        job TEXT NOT NULL, id TEXT NOT NULL, score REAL NOT NULL, PRIMARY KEY(job, id))""",
    "CREATE INDEX IF NOT EXISTS recall_vector_rank_idx ON recall_vector_scores(job, score DESC, id)",
)


class RecallIndex:
    # Bound owner objects and chunks separately: even a long message resumes at
    # its next span instead of bypassing the materialization budget.
    sync_items = 256
    candidate_items = 128
    vector_items = 256
    projection_version = 3

    def __init__(self, query, objects):
        self.query, self.runtime, self.index, self.objects = query, query.runtime, query.index, objects

    def _anchor(self, feeds, sequence=None):
        if sequence == 0:
            return {"sequence": 0, "token": "empty"}
        if sequence is not None:
            row = self.runtime.read_one("SELECT * FROM memory_recall_changes WHERE sequence = ?", (sequence,))
            if row is None or (row["owner"], row["kind"]) not in feeds:
                return None
        else:
            rows = [self.runtime.read_one("""SELECT sequence, token FROM memory_recall_changes
                WHERE owner = ? AND kind = ? ORDER BY sequence DESC LIMIT 1""", feed) for feed in feeds]
            row = max((row for row in rows if row is not None), key=lambda row: row["sequence"], default=None)
            if row is None:
                return {"sequence": 0, "token": "empty"}
        return {"sequence": row["sequence"], "token": row["token"]}

    def _changes(self, feeds, after, until):
        # Independent indexed feeds avoid sorting the whole relationship log
        # to merge human/reply/fact/experience streams before applying LIMIT.
        rows = []
        for feed in feeds:
            rows.extend(self.runtime.read_all("""SELECT sequence, kind, object_id FROM memory_recall_changes
                WHERE owner = ? AND kind = ? AND sequence > ? AND sequence <= ?
                ORDER BY sequence LIMIT ?""", (*feed, after, until, self.sync_items + 1)))
        return sorted(rows, key=lambda row: row["sequence"])[:self.sync_items + 1]

    @staticmethod
    def _remove(db, partition, producer=None, identifier=None):
        condition, parameters = "owner = ?", [partition]
        if producer is not None:
            condition += " AND producer = ? AND object_id = ?"
            parameters.extend((producer, identifier))
        db.execute(f"DELETE FROM recall_lexical WHERE rowid IN (SELECT rowid FROM recall_documents WHERE {condition})", parameters)
        db.execute(f"DELETE FROM recall_vectors WHERE owner = ? AND id IN (SELECT id FROM recall_documents WHERE {condition})", (partition, *parameters))
        return bool(db.execute(f"DELETE FROM recall_documents WHERE {condition}", parameters).rowcount)

    @staticmethod
    def _put(db, partition, producer, identifier, document, metadata):
        inserted = db.execute("""INSERT INTO recall_documents
            (owner, id, producer, object_id, revision, hash, document_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (partition, document["id"], producer, identifier,
            document["revision"], digest(document), encode(document), encode(metadata)))
        db.execute("INSERT INTO recall_lexical(rowid, tokens) VALUES (?, ?)",
                   (inserted.lastrowid, " ".join(terms(document["text"]))))

    def _bootstrap_experiences(self, db, partition, context, namespace, state, remaining):
        """Reconcile active experience views missing from the derived recall index.

        Runs once per cold build after change-log replay catches up, bounded per
        page so hot queries never scan ``memory_views``.
        """
        scan_limit = max(self.sync_items, 256)
        view_rows = self.runtime.read_all(
            """SELECT view_id, revision FROM memory_views
            WHERE owner_key = ? AND kind = 'experience' AND state = 'active' AND view_id > ?
            ORDER BY view_id LIMIT ?""",
            (owner_key(context), state["experience_bootstrap"], scan_limit + 1),
        )
        existing = {
            row[0]: row[1]
            for row in db.execute(
                "SELECT object_id, revision FROM recall_documents WHERE owner = ? AND producer = 'experience'",
                (partition,),
            )
        }
        changed = False
        scanned = 0
        exhausted = True
        for view_row in view_rows[:scan_limit]:
            view_id = view_row["view_id"]
            if existing.get(view_id) == view_row["revision"]:
                scanned += 1
                continue
            if not remaining:
                exhausted = False
                break
            changed = self._remove(db, partition, "experience", view_id) or changed
            used = 0
            try:
                for document, metadata in self.objects.project(context, namespace, "experience", view_id, offset=0):
                    self._put(db, partition, "experience", view_id, document, metadata)
                    changed = True
                    used += 1
            except TaskError as error:
                if error.code != "memory_source_unavailable":
                    raise
                state["unavailable"] = True
            remaining -= max(1, used)
            scanned += 1
        if exhausted and len(view_rows) <= scan_limit:
            state["experience_bootstrap"] = None
        elif scanned:
            state["experience_bootstrap"] = view_rows[scanned - 1]["view_id"]
        return changed

    def advance(self, context, namespace, kind):
        feeds = self.objects.feeds(context, kind)
        has_experience_feed = any(feed_kind == "experience" for _, feed_kind in feeds)
        current = self._anchor(feeds)
        partition = digest([owner_key(context), namespace, kind, "incremental-recall.v1"])
        with closing(self.index._open()) as db, db:
            db.execute("BEGIN")
            row = db.execute("SELECT state_json FROM recall_sync WHERE partition = ?", (partition,)).fetchone()
            state = json.loads(row[0]) if row else None
            if (state and state.get("projection_version") == self.projection_version
                    and state.get("experience_bootstrap") is None
                    and state["after"] == current["sequence"] and state["anchor"] == current):
                return self._snapshot(db, partition, state, current)
            db.rollback()
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state_json FROM recall_sync WHERE partition = ?", (partition,)).fetchone()
            state = json.loads(row[0]) if row else None
            if state and (state.get("projection_version") != self.projection_version
                          or self._anchor(feeds, state["anchor"]["sequence"]) != state["anchor"]):
                state = None
            changed = False
            if state is None:
                changed = self._remove(db, partition)
                state = {"generation": uuid4().hex, "after": 0, "pending": 0, "anchor": current,
                         "unavailable": False, "projection_version": self.projection_version,
                         "experience_bootstrap": "" if has_experience_feed else None}
            db.execute("INSERT OR REPLACE INTO partition_owners VALUES (?, ?)", (partition, owner_key(context)))
            rows = self._changes(feeds, state["after"], current["sequence"])
            remaining = self.sync_items
            for row in rows[:self.sync_items]:
                if not remaining:
                    break
                if not state["pending"]:
                    changed = self._remove(db, partition, row["kind"], row["object_id"]) or changed
                documents = iter(self.objects.project(context, namespace, row["kind"], row["object_id"], offset=state["pending"]))
                used = 0
                exhausted = True
                try:
                    for document, metadata in documents:
                        if used == remaining:
                            exhausted = False
                            break
                        self._put(db, partition, row["kind"], row["object_id"], document, metadata)
                        changed = True
                        used += 1
                except TaskError as error:
                    if error.code != "memory_source_unavailable":
                        raise
                    # Retain a conservative gap marker across pages/restarts.
                    # Missing originals are not evidence that no event occurred.
                    state["unavailable"] = True
                if exhausted:
                    state["after"], state["pending"] = row["sequence"], 0
                    remaining -= max(1, used)
                    continue
                state["pending"] += used
                break
            if state["after"] == current["sequence"] and state.get("experience_bootstrap") is not None:
                changed = self._bootstrap_experiences(
                    db, partition, context, namespace, state, remaining
                ) or changed
            state["anchor"] = current
            db.execute("INSERT OR REPLACE INTO recall_sync VALUES (?, ?)", (partition, encode(state)))
            if changed:
                db.execute("UPDATE recall_epoch SET revision = revision + 1 WHERE singleton = 1")
            return self._snapshot(db, partition, state, current)

    @staticmethod
    def _snapshot(db, partition, state, current):
        epoch = db.execute("SELECT revision FROM recall_epoch WHERE singleton = 1").fetchone()[0]
        return {**state, "partition": partition, "epoch": epoch,
                "ready": state["after"] == current["sequence"] and state.get("experience_bootstrap") is None,
                "fingerprint": digest([state["generation"], current, epoch])}

    @staticmethod
    def check(db, snapshot):
        row = db.execute("SELECT state_json FROM recall_sync WHERE partition = ?", (snapshot["partition"],)).fetchone()
        epoch = db.execute("SELECT revision FROM recall_epoch WHERE singleton = 1").fetchone()[0]
        if row is None or json.loads(row[0])["generation"] != snapshot["generation"] or epoch != snapshot["epoch"]:
            raise TaskError("memory_cursor_stale", "memory search changed; start a fresh query")

    @staticmethod
    def filters(snapshot, *, context, start, end, at, subjects, retention, confidence):
        where, parameters = ["d.owner = ?"], [snapshot["partition"]]
        for operator, value in ((">=", start), ("<", end)):
            if value is not None:
                where.append(f"json_extract(d.metadata_json, '$.observed_at') {operator} ?")
                parameters.append(stamp(value))
        if subjects:
            marks = ','.join('?' for _ in subjects)
            where.append(f"(json_extract(d.metadata_json, '$.subject') IN ({marks}) OR EXISTS "
                         f"(SELECT 1 FROM json_each(d.metadata_json, '$.entities') WHERE value IN ({marks})))")
            parameters.extend((*subjects, *subjects))
        if retention:
            where.append(f"(d.producer != 'fact' OR json_extract(d.metadata_json, '$.retention') IN ({','.join('?' for _ in retention)}))")
            parameters.extend(retention)
        if confidence is not None:
            where.append("(d.producer != 'fact' OR json_extract(d.metadata_json, '$.confidence') >= ?)")
            parameters.append(confidence)
        where.append("(d.producer != 'fact' OR json_extract(d.metadata_json, '$.retention') != 'session' OR json_extract(d.metadata_json, '$.scope') = ?)")
        parameters.append(encode(context.scope))
        for field, operator in (("valid_from", "<="), ("valid_to", ">")):
            where.append(f"(d.producer != 'fact' OR json_extract(d.metadata_json, '$.{field}') IS NULL OR json_extract(d.metadata_json, '$.{field}') {operator} ?)")
            parameters.append(stamp(at))
        return " AND ".join(where), parameters

    def candidates(self, snapshot, expression, *, filters, offset, limit, vector_job=None):
        where, parameters = filters
        with closing(self.index._open()) as db, db:
            db.execute("BEGIN")
            self.check(db, snapshot)
            if vector_job is not None:
                from .recall_vectors import candidates
                rows = candidates(db, expression, where, parameters, vector_job, offset, limit + 1)
            elif expression:
                rows = db.execute("SELECT d.* FROM recall_lexical JOIN recall_documents d ON d.rowid = recall_lexical.rowid "
                    "WHERE recall_lexical MATCH ? AND " + where + " ORDER BY bm25(recall_lexical), d.id LIMIT ? OFFSET ?",
                    (expression, *parameters, limit + 1, offset)).fetchall()
            else:
                rows = db.execute("SELECT d.* FROM recall_documents d WHERE " + where + " ORDER BY d.id LIMIT ? OFFSET ?",
                                  (*parameters, limit + 1, offset)).fetchall()
        return rows[:limit], len(rows) > limit

    def candidates_for_ids(self, snapshot, identifiers):
        """Reload a previously ordered page without changing its order."""
        identifiers = tuple(identifiers)
        if not identifiers:
            return []
        if (len(identifiers) > self.candidate_items
                or any(type(identifier) is not str or not identifier for identifier in identifiers)):
            raise TaskError("memory_cursor_stale", "memory cursor contains an invalid candidate page")
        marks = ",".join("?" for _ in identifiers)
        with closing(self.index._open()) as db, db:
            db.execute("BEGIN")
            self.check(db, snapshot)
            rows = db.execute(
                "SELECT * FROM recall_documents WHERE owner = ? AND id IN (" + marks + ")",
                (snapshot["partition"], *identifiers),
            ).fetchall()
        by_id = {row["id"]: row for row in rows}
        if len(by_id) != len(set(identifiers)):
            raise TaskError("memory_cursor_stale", "memory cursor candidates are no longer available")
        return [by_id[identifier] for identifier in identifiers]
