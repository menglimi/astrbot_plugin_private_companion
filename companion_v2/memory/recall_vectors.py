"""Exact hybrid recall with resumable embedding reads and SQL result ranking."""

from contextlib import closing
import json
import math
import struct

try:
    import numpy as _NP
except Exception:  # pragma: no cover - optional acceleration
    _NP = None

from companion_v2.execution.models import digest, encode, stamp


def validate(vector, model):
    if (not model or not isinstance(vector, tuple) or not 1 <= len(vector) <= 4096
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
        raise ValueError("query embedding needs a model/version and finite dimensions")
    norm = math.hypot(*vector)
    if not norm or not math.isfinite(norm):
        raise ValueError("query embedding needs a finite nonzero norm")
    return tuple(v / norm for v in vector)


def batch_cosines_f32(unit_query, row_ids, blobs, dimensions):
    """Compute cosine similarities between *unit_query* and float32 vector BLOBs.

    Uses NumPy ``float32`` matrix multiplication when available and falls back to
    pure Python ``struct.unpack`` otherwise. Malformed, non-finite, or zero-norm
    vectors are skipped identically in both paths.
    """
    if not row_ids or dimensions <= 0:
        return []
    expected_bytes = dimensions * 4
    valid_ids = []
    valid_blobs = []
    for row_id, raw in zip(row_ids, blobs):
        if raw is None:
            continue
        blob = bytes(raw) if isinstance(raw, memoryview) else raw
        if isinstance(blob, (bytes, bytearray)) and len(blob) == expected_bytes:
            valid_ids.append(row_id)
            valid_blobs.append(blob)
    if not valid_ids:
        return []

    np = _NP
    if np is not None:
        matrix = np.frombuffer(b"".join(valid_blobs), dtype="<f4").reshape(len(valid_ids), dimensions)
        finite_rows = np.all(np.isfinite(matrix), axis=1)
        finite_indices = np.flatnonzero(finite_rows)
        if finite_indices.size == 0:
            return []
        sub = matrix[finite_indices]
        norms = np.linalg.norm(sub, axis=1)
        nonzero = np.isfinite(norms) & (norms > 0.0)
        if not np.any(nonzero):
            return []
        active_indices = finite_indices[nonzero]
        q_vec = np.asarray(unit_query, dtype=np.float32)
        dots = sub[nonzero] @ q_vec
        scores = dots / norms[nonzero]
        result = []
        for idx, score in zip(active_indices.tolist(), scores.tolist()):
            if math.isfinite(score):
                result.append((valid_ids[idx], float(score)))
        return result

    fmt = "<" + "f" * dimensions
    result = []
    for row_id, blob in zip(valid_ids, valid_blobs):
        values = struct.unpack(fmt, blob)
        norm = math.hypot(*values)
        if norm and math.isfinite(norm):
            cosine = sum(a * (b / norm) for a, b in zip(unit_query, values))
            if math.isfinite(cosine):
                result.append((row_id, cosine))
    return result


def _progress(db, job, state, filters):
    where, parameters = filters
    embedded = db.execute("SELECT COUNT(*) FROM recall_vector_scores s JOIN recall_documents d ON d.id = s.id "
        "WHERE s.job = ? AND " + where, (job, *parameters)).fetchone()[0]
    eligible = (db.execute("SELECT COUNT(*) FROM recall_documents d WHERE " + where, parameters).fetchone()[0]
                if state["ready"] else None)
    return {"ready": state["ready"], "embedded": embedded, "eligible": eligible}


def advance(index, snapshot, *, session, filters, vector, model):
    unit = validate(vector, model)
    # Cosines depend only on the current projection and embedding, not on a
    # turn's cursor, grants or time filters. Reuse calculation progress across
    # turns so a corpus larger than one turn's budget can eventually finish.
    # Filtering, ranks and original-object authority are still query-local.
    job = digest(["recall-cosines.v1", snapshot["partition"], snapshot["fingerprint"], model, vector])
    now = stamp(index.query.tasks.now())
    with closing(index.index._open()) as db, db:
        db.execute("BEGIN")
        index.check(db, snapshot)
        row = db.execute("SELECT state_json FROM recall_vector_jobs WHERE job = ? AND expires_at > ?", (job, now)).fetchone()
        state = json.loads(row[0]) if row else None
        if state and state["ready"]:
            return job, _progress(db, job, state, filters)
        db.rollback()
        db.execute("BEGIN IMMEDIATE")
        index.check(db, snapshot)
        db.execute("DELETE FROM recall_vector_scores WHERE job IN (SELECT job FROM recall_vector_jobs WHERE expires_at <= ?)",
                   (now,))
        db.execute("DELETE FROM recall_vector_jobs WHERE expires_at <= ?", (now,))
        row = db.execute("SELECT state_json FROM recall_vector_jobs WHERE job = ?", (job,)).fetchone()
        state = json.loads(row[0]) if row else {"after": "", "ready": False}
        if state["ready"]:
            return job, _progress(db, job, state, filters)
        rows = db.execute("""SELECT v.id, v.dimensions, v.vector FROM recall_vectors v
            JOIN recall_documents d ON d.owner = v.owner AND d.id = v.id AND d.hash = v.hash
            WHERE v.owner = ? AND v.model = ? AND v.dimensions = ? AND v.id > ? ORDER BY v.id LIMIT ?""",
            (snapshot["partition"], model, len(vector), state["after"], index.vector_items + 1)).fetchall()
        batch_rows = rows[:index.vector_items]
        if batch_rows:
            scored = batch_cosines_f32(
                unit,
                [row["id"] for row in batch_rows],
                [row["vector"] for row in batch_rows],
                len(vector),
            )
            if scored:
                db.executemany(
                    "INSERT OR REPLACE INTO recall_vector_scores VALUES (?, ?, ?)",
                    [(job, row_id, cosine) for row_id, cosine in scored],
                )
            state["after"] = batch_rows[-1]["id"]
        state["ready"] = len(rows) <= index.vector_items
        db.execute("INSERT OR REPLACE INTO recall_vector_jobs VALUES (?, ?, ?, ?)",
                   (job, snapshot["partition"], session["expires_at"], encode(state)))
        return job, _progress(db, job, state, filters)


def candidates(db, expression, where, parameters, job, offset, limit):
    if expression:
        lexical = """lexical_scores AS MATERIALIZED (
            SELECT d.id, bm25(recall_lexical) score FROM recall_lexical
            JOIN recall_documents d ON d.rowid = recall_lexical.rowid
            WHERE recall_lexical MATCH ? AND """ + where + """),
            lexical_rank AS (SELECT id, 1.0 / (60 + row_number() OVER (ORDER BY score, id)) score FROM lexical_scores)"""
        arguments = (expression, *parameters)
    else:
        lexical = "lexical_rank AS (SELECT d.id, 0.0 score FROM recall_documents d WHERE " + where + ")"
        arguments = tuple(parameters)
    statement = "WITH " + lexical + """,
        vector_rank AS (SELECT s.id, 1.0 / (60 + row_number() OVER (ORDER BY s.score DESC, s.id)) score
                        FROM recall_vector_scores s JOIN recall_documents d ON d.id = s.id
                        WHERE s.job = ? AND """ + where + """),
        scores AS (SELECT id, score FROM lexical_rank UNION ALL SELECT id, score FROM vector_rank),
        ranked AS (SELECT id, SUM(score) score FROM scores GROUP BY id)
        SELECT d.*, ranked.score AS recall_score FROM ranked JOIN recall_documents d ON d.id = ranked.id
        WHERE """ + where + " ORDER BY ranked.score DESC, d.id LIMIT ? OFFSET ?"
    return db.execute(statement, (*arguments, job, *parameters, *parameters, limit, offset)).fetchall()


def document_vectors(index, snapshot, candidates, *, model, dimensions):
    """Read matching derived vectors for an already-ranked candidate page.

    Vector rows are optional cache data. A missing, stale, malformed or
    differently-sized row becomes ``None`` so it cannot block an otherwise
    valid recall page or grant access to a document.
    """
    if not candidates:
        return ()
    identifiers = tuple(dict.fromkeys(row["id"] for row in candidates))
    marks = ",".join("?" for _ in identifiers)
    with closing(index.index._open()) as db, db:
        db.execute("BEGIN")
        index.check(db, snapshot)
        rows = db.execute(
            """SELECT v.id, v.dimensions, v.vector FROM recall_vectors v
            JOIN recall_documents d ON d.owner = v.owner AND d.id = v.id AND d.hash = v.hash
            WHERE v.owner = ? AND v.model = ? AND v.dimensions = ? AND v.id IN ("""
            + marks + ")",
            (snapshot["partition"], model, dimensions, *identifiers),
        ).fetchall()
    values = {}
    for row in rows:
        try:
            vector = struct.unpack("<" + "f" * row["dimensions"], row["vector"])
        except (struct.error, TypeError, ValueError):
            continue
        if any(not math.isfinite(value) for value in vector):
            continue
        values[row["id"]] = vector
    return tuple(values.get(row["id"]) for row in candidates)
