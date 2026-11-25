"""Rebuildable, partitioned Chinese/Latin lexical and optional vector recall."""

from collections import Counter
from contextlib import closing, contextmanager
import json
import math
import re
import sqlite3
import struct
import unicodedata
from uuid import uuid4

from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.storage.sqlite_path import file_uri
from companion_v2.storage.sqlite_tuning import active_sqlite_tuning_profile, apply_sidecar_pragmas


def sqlite_code(error):
    return getattr(error, "sqlite_errorcode", 0) & 0xff


def index_failure(error):
    if sqlite_code(error) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return TaskError("memory_index_busy", "memory search is temporarily busy; retry the same query later")
    return TaskError("memory_index_unavailable", "memory search needs its derived index checked or rebuilt")


def terms(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    result = []
    for word in re.findall(r"[^\W_]+", normalized, re.UNICODE):
        cjk = any("\u3400" <= c <= "\u9fff" for c in word)
        parts = [word] if not cjk else [*word, *(word[i:i + 2] for i in range(len(word) - 1))]
        result.extend("t" + p.encode("utf-8").hex() for p in parts)
    return tuple(result)


class SearchIndex:
    """Nothing in this file is authoritative; every result is read from its owner.

    Vectors are explicit float32 values with a model/version identifier. Supplying
    no embedding uses lexical recall and reports that mode, without hidden calls.
    """

    FORMAT = 3  # Disposable cache layout; unrelated to the authoritative schema.

    def __init__(self, data_root, *, profile=None):
        self.path = data_root / "memory-search.sqlite3"
        self._explicit_profile = profile
        self._ready = False
        try:
            self._ensure_ready()
        except sqlite3.DatabaseError as error:
            if sqlite_code(error) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            # A busy cache cannot prevent its owning host from starting. The
            # next actual query retries initialization without a blocking wait.

    @property
    def _profile(self):
        return self._explicit_profile or active_sqlite_tuning_profile()

    def _ensure_ready(self):
        if self._ready:
            return
        try:
            self._initialize()
        except sqlite3.DatabaseError as error:
            if sqlite_code(error) not in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                raise
            # Only confirmed damage permits replacing this derived file. Busy,
            # readonly, disk-full and I/O errors must never rename a healthy cache.
            self.path.replace(self.path.with_name(self.path.name + ".corrupt-" + uuid4().hex))
            self._initialize()
        self._ready = True

    def _initialize(self):
        with closing(self._connect()) as db, db:
            mode = db.execute("PRAGMA journal_mode").fetchone()
            in_wal = mode is not None and str(mode[0]).lower() == "wal"
            if db.execute("PRAGMA user_version").fetchone()[0] == self.FORMAT:
                if not in_wal:
                    db.execute("PRAGMA journal_mode = WAL")
                return
            if not in_wal:
                db.execute("PRAGMA journal_mode = WAL")
            # Normal construction reads the format marker, not every cache page.
            # Legacy initialization and its marker commit atomically.
            db.execute("BEGIN IMMEDIATE")
            has_partition_owners = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'partition_owners'"
            ).fetchone() is not None
            db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT NOT NULL, owner TEXT NOT NULL, hash TEXT NOT NULL, document_json TEXT NOT NULL, PRIMARY KEY(id, owner))")
            db.execute("CREATE INDEX IF NOT EXISTS document_owner ON documents(owner, id)")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS lexical USING fts5(id UNINDEXED, owner UNINDEXED, tokens)")
            db.execute("CREATE TABLE IF NOT EXISTS vectors (id TEXT NOT NULL, owner TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(id, owner, model))")
            if not has_partition_owners:
                # Old projections stored only an opaque namespace/kind partition.
                # Drop them once rather than risk retaining ungovernable text terms.
                for table in ("lexical", "vectors", "documents"):
                    db.execute("DELETE FROM " + table)
                db.execute("CREATE TABLE partition_owners (partition TEXT PRIMARY KEY, owner TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS fact_documents (
                id TEXT NOT NULL, owner TEXT NOT NULL, revision INTEGER NOT NULL,
                hash TEXT NOT NULL, metadata_json TEXT NOT NULL, PRIMARY KEY(owner, id))""")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fact_lexical USING fts5(id UNINDEXED, owner UNINDEXED, tokens)")
            db.execute("CREATE TABLE IF NOT EXISTS fact_sync (partition TEXT PRIMARY KEY, state_json TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS fact_epoch (singleton INTEGER PRIMARY KEY, revision INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO fact_epoch VALUES (1, 0)")
            from .recall_index import CACHE_STATEMENTS
            for statement in CACHE_STATEMENTS:
                db.execute(statement)
            db.execute(f"PRAGMA user_version = {self.FORMAT}")

    def _connect(self):
        # This cache is optional recall. Waiting here would hold the synchronous
        # host loop (and the caller's authoritative transaction) for seconds.
        target = str(self.path)
        if target == ":memory:":
            db = sqlite3.connect(target, timeout=0)
        else:
            db = sqlite3.connect(file_uri(self.path), timeout=0, uri=True)
        db.row_factory = sqlite3.Row
        apply_sidecar_pragmas(
            db,
            profile=self._profile,
            enable_wal=False,
            read_only=False,
            busy_timeout_ms=None,
            foreign_keys=False,
        )
        return db

    def _open(self):
        self._ensure_ready()
        return self._connect()

    @contextmanager
    def reflection_snapshot(self, database_path):
        """Read committed reservation keys for SQL prefiltering only.

        The caller rechecks every candidate on its authoritative connection,
        including reservations in an enclosing transaction not visible here.
        This separate connection closes before the owner's transaction commits.
        """
        with closing(self._open()) as db, db:
            from companion_v2.storage.sqlite_path import readonly_uri
            db.execute("ATTACH DATABASE ? AS reflection_owner", (readonly_uri(database_path),))
            db.execute("BEGIN")
            yield db

    def clear(self):
        with closing(self._open()) as db, db:
            for table in ("recall_lexical", "recall_documents", "recall_vectors", "recall_sync", "recall_vector_scores", "recall_vector_jobs"):
                db.execute("DELETE FROM " + table)
            db.execute("UPDATE recall_epoch SET revision = revision + 1 WHERE singleton = 1")
            for table in ("lexical", "vectors", "documents", "fact_lexical", "fact_documents", "fact_sync", "partition_owners"):
                db.execute("DELETE FROM " + table)
            db.execute("UPDATE fact_epoch SET revision = revision + 1 WHERE singleton = 1")

    def remove_owner(self, owner):
        """Remove one relationship's derived text and vectors after forgetting."""
        with closing(self._open()) as db, db:
            db.execute("""DELETE FROM recall_lexical WHERE rowid IN (SELECT rowid FROM recall_documents
                WHERE owner IN (SELECT partition FROM partition_owners WHERE owner = ?))""", (owner,))
            db.execute("""DELETE FROM recall_vector_scores WHERE job IN (SELECT job FROM recall_vector_jobs
                WHERE owner IN (SELECT partition FROM partition_owners WHERE owner = ?))""", (owner,))
            for table in ("recall_documents", "recall_vectors", "recall_vector_jobs"):
                db.execute(f"DELETE FROM {table} WHERE owner IN (SELECT partition FROM partition_owners WHERE owner = ?)", (owner,))
            db.execute("DELETE FROM recall_sync WHERE partition IN (SELECT partition FROM partition_owners WHERE owner = ?)", (owner,))
            db.execute("UPDATE recall_epoch SET revision = revision + 1 WHERE singleton = 1")
            for table in ("lexical", "vectors", "documents", "fact_lexical", "fact_documents"):
                db.execute(
                    f"DELETE FROM {table} WHERE owner IN (SELECT partition FROM partition_owners WHERE owner = ?)",
                    (owner,),
                )
            db.execute("DELETE FROM fact_sync WHERE partition IN (SELECT partition FROM partition_owners WHERE owner = ?)", (owner,))
            db.execute("DELETE FROM partition_owners WHERE owner = ?", (owner,))
            db.execute("UPDATE fact_epoch SET revision = revision + 1 WHERE singleton = 1")

    def sync(self, owner, documents, *, relationship_owner=None):
        relationship_owner = owner if relationship_owner is None else relationship_owner
        wanted = {d["id"]: d for d in documents}
        with closing(self._open()) as db, db:
            db.execute(
                "INSERT INTO partition_owners(partition, owner) VALUES (?, ?) "
                "ON CONFLICT(partition) DO UPDATE SET owner = excluded.owner",
                (owner, relationship_owner),
            )
            current = {r["id"]: r["hash"] for r in db.execute("SELECT id, hash FROM documents WHERE owner = ?", (owner,))}
            for identifier in current.keys() - wanted.keys():
                db.execute("DELETE FROM lexical WHERE id = ? AND owner = ?", (identifier, owner))
                db.execute("DELETE FROM documents WHERE id = ? AND owner = ?", (identifier, owner))
                db.execute("DELETE FROM vectors WHERE id = ? AND owner = ?", (identifier, owner))
            for identifier, document in wanted.items():
                fingerprint = digest(document)
                if current.get(identifier) == fingerprint:
                    continue
                db.execute("DELETE FROM lexical WHERE id = ? AND owner = ?", (identifier, owner))
                db.execute("DELETE FROM vectors WHERE id = ? AND owner = ?", (identifier, owner))
                db.execute("INSERT INTO documents VALUES (?, ?, ?, ?) ON CONFLICT(id, owner) DO UPDATE SET hash = excluded.hash, document_json = excluded.document_json",
                           (identifier, owner, fingerprint, encode(document)))
                db.execute("INSERT INTO lexical(id, owner, tokens) VALUES (?, ?, ?)", (identifier, owner, " ".join(terms(document["text"]))))

    def put_vector(self, owner, identifier, *, model, values, expected_hash):
        if not isinstance(model, str) or not model or type(values) is not tuple or not 1 <= len(values) <= 4096:
            raise ValueError("an embedding needs a model version and bounded vector")
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e30 for v in values):
            raise ValueError("embedding values must be finite float32 values")
        with closing(self._open()) as db, db:
            row = db.execute("SELECT hash FROM documents WHERE id = ? AND owner = ?", (identifier, owner)).fetchone()
            table = "vectors"
            if row is None:
                row = db.execute("SELECT hash FROM recall_documents WHERE id = ? AND owner = ?", (identifier, owner)).fetchone()
                table = "recall_vectors"
            if row is None or row["hash"] != expected_hash:
                raise ValueError("embedding refers to a replaced document")
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?, ?, ?)",
                       (identifier, owner, model, expected_hash, len(values), struct.pack('<' + 'f' * len(values), *values)))
            if table == "recall_vectors":
                db.execute("UPDATE recall_epoch SET revision = revision + 1 WHERE singleton = 1")

    def rank(self, owner, query, *, allowed, vector=None, model=None):
        query_terms = tuple(dict.fromkeys(terms(query)))
        if len(query_terms) > 512:
            raise TaskError("memory_query_terms_exceeded", "use a more focused query; no search was performed")
        ranks, mode, coverage = {}, "lexical", None
        with closing(self._open()) as db:
            if query_terms:
                expression = " OR ".join('"' + term + '"' for term in query_terms)
                lexical = db.execute("SELECT id, bm25(lexical) AS score FROM lexical WHERE lexical MATCH ? AND owner = ? ORDER BY score, id",
                                     (expression, owner))
                position = 0
                for row in lexical:
                    if row["id"] in allowed:
                        position += 1
                        ranks[row["id"]] = 1 / (60 + position)
            else:
                ranks = {identifier: 0.0 for identifier in sorted(allowed)}
            if vector is not None:
                if (not model or not isinstance(vector, tuple) or not 1 <= len(vector) <= 4096
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
                    raise ValueError("query embedding needs a model/version and finite dimensions")
                norm = math.hypot(*vector)
                if not norm or not math.isfinite(norm):
                    raise ValueError("query embedding cannot be a zero vector")
                unit_query = tuple(v / norm for v in vector)
                row_ids, blobs = [], []
                for row in db.execute("""SELECT v.id, v.vector FROM vectors v JOIN documents d ON d.id = v.id AND d.owner = v.owner
                        WHERE d.owner = ? AND v.model = ? AND v.hash = d.hash AND v.dimensions = ?""", (owner, model, len(vector))):
                    if row["id"] in allowed:
                        row_ids.append(row["id"])
                        blobs.append(row["vector"])
                from .recall_vectors import batch_cosines_f32
                candidates = [(cosine, identifier) for identifier, cosine in batch_cosines_f32(
                    unit_query, row_ids, blobs, len(vector))]
                for position, (_, identifier) in enumerate(sorted(candidates, key=lambda item: (-item[0], item[1])), 1):
                    ranks[identifier] = ranks.get(identifier, 0) + 1 / (60 + position)
                mode, coverage = "hybrid", {"embedded": len(candidates), "eligible": len(allowed), "model": model}
        return sorted(ranks, key=lambda identifier: (-ranks[identifier], identifier)), mode, coverage
