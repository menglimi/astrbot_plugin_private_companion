"""V2-only semantic catalog. Originals are versioned; vectors are disposable."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
import time

try:
    import numpy as _NP
except Exception:  # pragma: no cover - optional acceleration
    _NP = None

from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.storage.image_payloads import image_extension
from companion_v2.storage.sqlite_tuning import active_sqlite_tuning_profile, apply_sidecar_pragmas


VECTOR_BLOB_MAGIC = b"F16\x00"
REACTION_FILE_MARKER = b"\x00PCREACT1/"
_REACTION_MARKER_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}\.(?:png|jpg|webp|gif|bin)$")


def encode_vector_blob(vector) -> bytes:
    """Pack a finite embedding vector as a compact little-endian float16 BLOB."""
    if _NP is not None:
        arr = _NP.asarray(vector, dtype="<f2")
        return VECTOR_BLOB_MAGIC + arr.tobytes()
    return VECTOR_BLOB_MAGIC + struct.pack("<" + "e" * len(vector), *vector)


def _coerce_f32_sequence(values):
    if not isinstance(values, (list, tuple)) or not values:
        return None
    if _NP is not None:
        try:
            arr = _NP.asarray(values, dtype=_NP.float32)
        except (TypeError, ValueError):
            return None
        if arr.ndim != 1 or not _NP.all(_NP.isfinite(arr)):
            return None
        return arr
    try:
        converted = tuple(float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(v) for v in converted):
        return None
    return converted


def decode_vector_f32(raw):
    """Decode either a float16 BLOB or legacy JSON vector into a float32 array or tuple."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        mv = memoryview(raw)
        if len(mv) >= 4 and mv[:4].tobytes() == VECTOR_BLOB_MAGIC:
            payload = mv[4:]
            if len(payload) == 0 or len(payload) % 2 != 0:
                return None
            if _NP is not None:
                arr = _NP.frombuffer(payload, dtype="<f2").astype(_NP.float32)
                return arr if _NP.all(_NP.isfinite(arr)) else None
            count = len(payload) // 2
            values = struct.unpack("<" + "e" * count, payload)
            return values if all(math.isfinite(v) for v in values) else None
        try:
            values = json.loads(bytes(mv).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        return _coerce_f32_sequence(values)
    if isinstance(raw, str):
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return _coerce_f32_sequence(values)
    if _NP is not None and isinstance(raw, _NP.ndarray):
        try:
            arr = raw.astype(_NP.float32, copy=False)
        except (TypeError, ValueError):
            return None
        if arr.ndim != 1 or arr.size == 0 or not _NP.all(_NP.isfinite(arr)):
            return None
        return arr
    if isinstance(raw, (list, tuple)):
        return _coerce_f32_sequence(raw)
    return None


def decode_vector_tuple(raw) -> tuple[float, ...] | None:
    """Decode a stored semantic vector (float16 BLOB or legacy JSON) into a Python tuple."""
    decoded = decode_vector_f32(raw)
    if decoded is None:
        return None
    if _NP is not None and isinstance(decoded, _NP.ndarray):
        return tuple(decoded.tolist())
    return tuple(float(v) for v in decoded)


def batch_semantic_dots(query_vector, candidate_vectors):
    """Compute dot products between *query_vector* and decoded candidate vectors.

    *candidate_vectors* is a sequence of ``(index, raw_or_vector)`` pairs.
    Returns ``{index: float_cosine}`` using NumPy ``float32`` matrix multiply
    when available, with pure-Python fallback.
    """
    if not candidate_vectors or not query_vector:
        return {}
    dims = len(query_vector)
    np = _NP
    if np is not None:
        valid_indices = []
        f16_blobs = []
        fallback_rows = []
        for idx, raw in candidate_vectors:
            if isinstance(raw, (bytes, bytearray, memoryview)):
                mv = memoryview(raw)
                if len(mv) == 4 + dims * 2 and mv[:4].tobytes() == VECTOR_BLOB_MAGIC:
                    valid_indices.append(idx)
                    f16_blobs.append(bytes(mv[4:]))
                    continue
            arr = decode_vector_f32(raw)
            if arr is not None and arr.ndim == 1 and arr.shape[0] == dims:
                fallback_rows.append((idx, arr))
        q_vec = np.asarray(query_vector, dtype=np.float32)
        results = {}
        if f16_blobs:
            mat = np.frombuffer(b"".join(f16_blobs), dtype="<f2").reshape(len(f16_blobs), dims).astype(np.float32)
            scores = mat @ q_vec
            for idx, score in zip(valid_indices, scores.tolist()):
                if math.isfinite(score):
                    results[idx] = float(score)
        if fallback_rows:
            mat = np.vstack([arr for _, arr in fallback_rows]).astype(np.float32, copy=False)
            scores = mat @ q_vec
            for (idx, _), score in zip(fallback_rows, scores.tolist()):
                if math.isfinite(score):
                    results[idx] = float(score)
        return results

    results = {}
    for idx, raw in candidate_vectors:
        vec = decode_vector_tuple(raw)
        if vec is not None and len(vec) == dims:
            score = sum(a * b for a, b in zip(query_vector, vec))
            if math.isfinite(score):
                results[idx] = float(score)
    return results


DEFAULTS = {"enabled": True, "embedding_provider": "", "rerank_provider": "", "analysis_provider": "",
            "memory_enabled": True, "reactions_enabled": True,
            "reactions_private_enabled": True, "reactions_group_enabled": False,
            "reactions_collection_private_enabled": False, "reactions_collection_group_enabled": False,
            "reactions_collection_private_probability": 0.1, "reactions_collection_group_probability": 0.1,
            "reactions_collection_mode": "automatic",
            "reactions_proactive_enabled": False,
            "reactions_trigger_probability": 0.2, "reactions_cooldown_seconds": 180,
            "reactions_delivery_mode": "separate_after", "reactions_image_format": "image",
            "slang_enabled": True,
            "slang_learning": True, "slang_web_search_enabled": False,
            "slang_web_search_candidates": 4, "slang_web_search_results": 2,
            "vision_review": True, "timeout_seconds": 8,
            "batch_size": 24, "learning_interval_seconds": 21600}
LEGACY_LEARNING_INTERVAL_DEFAULT = 900
INTEGER_LIMITS = {"timeout_seconds": (1, 30), "batch_size": (1, 64),
                  "learning_interval_seconds": (60, 86400), "slang_web_search_candidates": (1, 12),
                  "slang_web_search_results": (1, 5), "reactions_cooldown_seconds": (0, 3600)}
PROBABILITY_FIELDS = frozenset(("reactions_trigger_probability", "reactions_collection_private_probability",
                               "reactions_collection_group_probability"))
SETTING_OPTIONS = {
    "reactions_collection_mode": ("automatic", "confirm"),
    "reactions_delivery_mode": ("separate_after", "same_message", "separate_before"),
    "reactions_image_format": ("image", "qq_emoji"),
}


class SemanticStore:
    BUSY_TIMEOUT_MS = 5000

    def __init__(self, root, *, profile=None):
        self.root = Path(root)
        self.path = self.root / "semantic-catalog.sqlite3"
        self._explicit_profile = profile
        is_new = not self.path.exists()
        with self.db(enable_wal=True, set_incremental_vacuum=is_new) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8):
                raise ValueError("unsupported semantic catalog version")
            db.execute("""CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                explicit TEXT NOT NULL DEFAULT '[]')""")
            if version < 5:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(settings)")}
                if "explicit" not in columns:
                    db.execute("ALTER TABLE settings ADD COLUMN explicit TEXT NOT NULL DEFAULT '[]'")
                    row = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
                    if row is not None:
                        configured = json.loads(row["payload"])
                        db.execute("UPDATE settings SET explicit=? WHERE id=1",
                                   (encode(sorted(configured)),))
            db.execute("INSERT OR IGNORE INTO settings(id, revision, payload, explicit) VALUES (1, 0, '{}', '[]')")
            db.execute("""CREATE TABLE IF NOT EXISTS items (
                owner TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, content BLOB, enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(owner, kind, id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS vectors (
                owner TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                model TEXT NOT NULL, hash TEXT NOT NULL, vector TEXT NOT NULL,
                PRIMARY KEY(owner, kind, id, model))""")
            db.execute("""CREATE TABLE IF NOT EXISTS offers (
                batch TEXT NOT NULL, call TEXT NOT NULL, owner TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(batch, call))""")
            db.execute("CREATE TABLE IF NOT EXISTS learning (owner TEXT PRIMARY KEY, checked_at REAL NOT NULL, retry_after REAL NOT NULL DEFAULT 0)")
            if version < 7:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(learning)")}
                if "retry_after" not in columns:
                    db.execute("ALTER TABLE learning ADD COLUMN retry_after REAL NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS slang_candidate_events (
                owner TEXT NOT NULL, event_key TEXT NOT NULL, observed_at REAL NOT NULL,
                PRIMARY KEY(owner, event_key))""")
            db.execute("""CREATE TABLE IF NOT EXISTS slang_candidates (
                owner TEXT NOT NULL, term TEXT NOT NULL, occurrences INTEGER NOT NULL,
                last_seen REAL NOT NULL, PRIMARY KEY(owner, term))""")
            db.execute("CREATE TABLE IF NOT EXISTS uses (owner TEXT, id TEXT, batch TEXT, used_at REAL, PRIMARY KEY(owner,id,batch))")
            db.execute("CREATE TABLE IF NOT EXISTS reaction_frames (owner TEXT, id TEXT, position INTEGER, content BLOB NOT NULL, PRIMARY KEY(owner,id,position))")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_preferences (
                owner TEXT NOT NULL, actor TEXT NOT NULL, auto_disabled INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(owner,actor))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_affinity (
                owner TEXT NOT NULL, actor TEXT NOT NULL, id TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0,
                positive_count INTEGER NOT NULL DEFAULT 0, negative_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(owner,actor,id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_deliveries (
                owner TEXT NOT NULL, actor TEXT NOT NULL, batch TEXT NOT NULL, id TEXT NOT NULL,
                delivered_at REAL NOT NULL, feedback_event TEXT, feedback_signal TEXT,
                PRIMARY KEY(owner,id,batch))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_inputs (
                owner TEXT NOT NULL, event_key TEXT NOT NULL, actor TEXT NOT NULL,
                received_at REAL, signal TEXT,
                PRIMARY KEY(owner,event_key))""")
            if version < 3:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(reaction_inputs)")}
                for column, kind in (("received_at", "REAL"), ("signal", "TEXT")):
                    if column not in columns:
                        db.execute(f"ALTER TABLE reaction_inputs ADD COLUMN {column} {kind}")
            db.execute("CREATE INDEX IF NOT EXISTS reaction_inputs_feedback ON reaction_inputs(owner,actor,received_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_turns (
                batch TEXT PRIMARY KEY, owner TEXT NOT NULL, actor TEXT NOT NULL,
                explicit_request INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_collection_runs (
                owner TEXT NOT NULL, run_key TEXT NOT NULL, scope TEXT NOT NULL,
                status TEXT NOT NULL, code TEXT, item_id TEXT, updated_at REAL NOT NULL,
                PRIMARY KEY(owner,run_key))""")
            db.execute("CREATE INDEX IF NOT EXISTS reaction_collection_recent ON reaction_collection_runs(owner,updated_at DESC)")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_collection_totals (
                owner TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(owner,scope,status))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reaction_owner_migrations (
                source_owner TEXT NOT NULL, target_owner TEXT NOT NULL,
                PRIMARY KEY(source_owner,target_owner))""")
            db.execute("PRAGMA user_version=8")
            row = db.execute("SELECT revision, payload, explicit FROM settings WHERE id=1").fetchone()
            configured = json.loads(row["payload"])
            explicit = set(json.loads(row["explicit"]))
            if ("learning_interval_seconds" not in explicit
                    and configured.get("learning_interval_seconds") == LEGACY_LEARNING_INTERVAL_DEFAULT):
                configured["learning_interval_seconds"] = DEFAULTS["learning_interval_seconds"]
                db.execute("UPDATE settings SET revision=revision+1,payload=? WHERE id=1 AND revision=?",
                           (encode(configured), row["revision"]))

    @property
    def _profile(self):
        return self._explicit_profile or active_sqlite_tuning_profile()

    @property
    def reaction_media_root(self) -> Path:
        return self.root / "media" / "reactions"

    @staticmethod
    def is_reaction_file_marker(value) -> bool:
        return isinstance(value, (bytes, bytearray, memoryview)) and bytes(value).startswith(REACTION_FILE_MARKER)

    def _store_reaction_bytes(self, content: bytes, *, mime_type: str = "") -> bytes:
        raw = bytes(content)
        if not raw or self.is_reaction_file_marker(raw):
            return raw
        digest_hex = hashlib.sha256(raw).hexdigest()
        ext = image_extension(raw, mime_type)
        rel = f"{digest_hex[:2]}/{digest_hex}{ext}"
        target_dir = self.reaction_media_root / digest_hex[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest_hex}{ext}"
        if not target.is_file() or target.stat().st_size != len(raw):
            fd, tmp_name = tempfile.mkstemp(prefix=".react-", suffix=".tmp", dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, target)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        return REACTION_FILE_MARKER + rel.encode("ascii")

    def _load_reaction_bytes(self, value):
        if value is None:
            return None
        if isinstance(value, (bytearray, memoryview)):
            value = bytes(value)
        if not isinstance(value, bytes) or not self.is_reaction_file_marker(value):
            return value
        try:
            rel = value[len(REACTION_FILE_MARKER):].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise TaskError("reaction_media_corrupt", "reaction media marker is invalid") from error
        if not _REACTION_MARKER_RE.match(rel):
            raise TaskError("reaction_media_corrupt", "reaction media marker is invalid")
        path = self.reaction_media_root / rel
        try:
            return path.read_bytes()
        except OSError as error:
            raise TaskError("reaction_media_missing", "reaction media file is unavailable") from error

    def _decode_item_row(self, row) -> dict:
        record = dict(row)
        record.pop("_rowid", None)
        record["payload"] = json.loads(record["payload"])
        if record.get("content") is not None and self.is_reaction_file_marker(record["content"]):
            record["content"] = self._load_reaction_bytes(record["content"])
        return record

    @contextmanager
    def db(self, *, enable_wal=False, read_only=False, set_incremental_vacuum=False):
        db = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT_MS / 1000.0)
        db.row_factory = sqlite3.Row
        try:
            if set_incremental_vacuum and not read_only:
                try:
                    db.execute("PRAGMA auto_vacuum = INCREMENTAL")
                except sqlite3.Error:
                    pass
            apply_sidecar_pragmas(
                db,
                profile=self._profile,
                enable_wal=enable_wal,
                read_only=read_only,
                busy_timeout_ms=self.BUSY_TIMEOUT_MS,
                foreign_keys=False,
            )
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        snapshot = self.configuration_snapshot()
        return {"revision": snapshot["revision"], **snapshot["values"]}

    def configuration_snapshot(self):
        """Return semantic settings and which values were explicitly configured."""
        with self.db(read_only=True) as db:
            row = db.execute("SELECT revision, payload, explicit FROM settings WHERE id=1").fetchone()
        configured = json.loads(row["payload"])
        return {"revision": row["revision"], "values": {**DEFAULTS, **configured},
                "explicit": frozenset(json.loads(row["explicit"]))}

    @staticmethod
    def validate_changes(changes):
        """Validate and normalize a semantic-settings patch without writing it."""
        if type(changes) is not dict or set(changes) - set(DEFAULTS):
            raise ValueError("unknown semantic settings")
        normalized = dict(changes)
        for name in PROBABILITY_FIELDS:
            probability = normalized.get(name)
            if type(probability) in (int, float):
                normalized[name] = float(probability)
        for name, value in normalized.items():
            if type(value) is not type(DEFAULTS[name]):
                raise ValueError("wrong semantic setting type")
            if type(value) is str and (len(value) > 200 or value != value.strip()):
                raise ValueError("invalid provider identifier")
            if name in INTEGER_LIMITS:
                low, high = INTEGER_LIMITS[name]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError("semantic setting outside supported range")
            if name in PROBABILITY_FIELDS and not math.isfinite(value):
                raise ValueError("semantic setting outside supported range")
            if name in SETTING_OPTIONS and value not in SETTING_OPTIONS[name]:
                raise ValueError("unsupported semantic setting")
        return normalized

    def configure(self, changes, revision):
        changes = self.validate_changes(changes)
        snapshot = self.configuration_snapshot()
        settings = {"revision": snapshot["revision"], **snapshot["values"], **changes}
        if not (1 <= settings["timeout_seconds"] <= 30 and 1 <= settings["batch_size"] <= 64
                and 60 <= settings["learning_interval_seconds"] <= 86400
                and 1 <= settings["slang_web_search_candidates"] <= 12
                and 1 <= settings["slang_web_search_results"] <= 5
                and all(math.isfinite(settings[name]) and 0 <= settings[name] <= 1
                        for name in PROBABILITY_FIELDS)
                and 0 <= settings["reactions_cooldown_seconds"] <= 3600):
            raise ValueError("semantic settings outside supported range")
        settings.pop("revision")
        explicit = sorted(set(snapshot["explicit"]) | set(changes))
        with self.db() as db:
            if not db.execute("UPDATE settings SET revision=revision+1,payload=?,explicit=? WHERE id=1 AND revision=?",
                              (encode(settings), encode(explicit), revision)).rowcount:
                raise TaskError("semantic_conflict", "settings changed; refresh before saving")
        return self.settings()

    def get(self, owner, kind, identifier):
        with self.db(read_only=True) as db:
            row = db.execute("SELECT * FROM items WHERE owner=? AND kind=? AND id=?", (owner, kind, identifier)).fetchone()
            if row is None:
                raise TaskError("semantic_item_missing", "catalog item is unavailable")
            return self._decode_item_row(row)

    def list(self, owner, kind, *, after="", limit=64):
        with self.db(read_only=True) as db:
            rows = db.execute("SELECT * FROM items WHERE owner=? AND kind=? AND id>? ORDER BY id LIMIT ?",
                              (owner, kind, after, limit)).fetchall()
            return [self._decode_item_row(r) for r in rows]

    def list_recent_page(self, owner, kind, *, after="", limit=24):
        """Return up to ``limit`` items newest-first plus the next opaque cursor."""
        if type(after) is not str or type(limit) is not int or limit < 1:
            raise ValueError("invalid catalog pagination")
        with self.db(read_only=True) as db:
            if after:
                if not after.startswith("r:") or not after[2:].isascii() or not after[2:].isdigit():
                    raise ValueError("invalid catalog cursor")
                cursor = int(after[2:])
                if cursor < 1:
                    raise ValueError("invalid catalog cursor")
                rows = db.execute(
                    "SELECT rowid AS _rowid, * FROM items WHERE owner=? AND kind=? AND rowid<? "
                    "ORDER BY rowid DESC LIMIT ?",
                    (owner, kind, cursor, limit + 1),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT rowid AS _rowid, * FROM items WHERE owner=? AND kind=? "
                    "ORDER BY rowid DESC LIMIT ?",
                    (owner, kind, limit + 1),
                ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_after = f"r:{page[-1]['_rowid']}" if has_more else None
        items = [self._decode_item_row(row) for row in page]
        return items, next_after

    def reaction_frames(self, owner, identifier) -> tuple[bytes, ...]:
        """Load animation frame bytes for one reaction, resolving file markers transparently."""
        with self.db(read_only=True) as db:
            rows = db.execute(
                "SELECT content FROM reaction_frames WHERE owner=? AND id=? ORDER BY position",
                (owner, identifier),
            ).fetchall()
        return tuple(self._load_reaction_bytes(row[0]) for row in rows)

    def record_slang_candidates(self, owner, event_key, terms, *, observed_at):
        """Accumulate bounded vocabulary counts once per authorized input source."""
        if (type(owner) is not str or not owner or type(event_key) is not str or not event_key
                or type(observed_at) not in (int, float) or not math.isfinite(observed_at)):
            raise ValueError("invalid group vocabulary observation")
        normalized = tuple(dict.fromkeys(term for term in terms
            if type(term) is str and term.strip() and len(term) <= 64))
        if len(normalized) > 8:
            raise ValueError("too many group vocabulary candidates")
        with self.db() as db:
            inserted = db.execute("INSERT OR IGNORE INTO slang_candidate_events VALUES (?,?,?)",
                                  (owner, event_key, float(observed_at))).rowcount
            if inserted:
                for term in normalized:
                    db.execute("""INSERT INTO slang_candidates VALUES (?,?,1,?)
                        ON CONFLICT(owner,term) DO UPDATE SET
                        occurrences=MIN(999, slang_candidates.occurrences+1),
                        last_seen=MAX(slang_candidates.last_seen, excluded.last_seen)""",
                        (owner, term, float(observed_at)))
                db.execute("""DELETE FROM slang_candidate_events WHERE owner=? AND event_key NOT IN
                    (SELECT event_key FROM slang_candidate_events WHERE owner=?
                     ORDER BY observed_at DESC, event_key DESC LIMIT 512)""", (owner, owner))
                db.execute("""DELETE FROM slang_candidates WHERE owner=? AND term NOT IN
                    (SELECT term FROM slang_candidates WHERE owner=?
                     ORDER BY occurrences DESC, last_seen DESC, term LIMIT 80)""", (owner, owner))
            rows = db.execute("SELECT term,occurrences FROM slang_candidates WHERE owner=?",
                              (owner,)).fetchall()
        return {row["term"]: row["occurrences"] for row in rows}

    def slang_candidate_counts(self, owner):
        with self.db(read_only=True) as db:
            rows = db.execute("SELECT term,occurrences FROM slang_candidates WHERE owner=?",
                              (owner,)).fetchall()
        return {row["term"]: row["occurrences"] for row in rows}

    def put(self, owner, kind, identifier, payload, *, expected=0, content=None, enabled=True, frames=None):
        stored_content = content
        if kind == "reaction" and isinstance(content, (bytes, bytearray, memoryview)) and len(content) > 0:
            mime_type = ""
            if isinstance(payload, dict):
                desc = payload.get("descriptor")
                if isinstance(desc, dict):
                    mime_type = str(desc.get("mime_type") or "")
            stored_content = self._store_reaction_bytes(bytes(content), mime_type=mime_type)
        stored_frames = None
        if frames is not None:
            stored_frames = [
                (position, self._store_reaction_bytes(bytes(image)) if isinstance(image, (bytes, bytearray, memoryview)) and len(image) > 0 else image)
                for position, image in frames
            ]
        with self.db() as db:
            old = db.execute("SELECT * FROM items WHERE owner=? AND kind=? AND id=?", (owner, kind, identifier)).fetchone()
            if (old["revision"] if old else 0) != expected:
                raise TaskError("semantic_conflict", "item changed; refresh before saving")
            db.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?)", (owner, kind, identifier,
                       expected + 1, encode(payload), stored_content if stored_content is not None else old["content"] if old else None, int(enabled)))
            if stored_frames is not None:
                db.execute("DELETE FROM reaction_frames WHERE owner=? AND id=?", (owner, identifier))
                db.executemany("INSERT INTO reaction_frames VALUES (?,?,?,?)",
                               ((owner, identifier, position, image) for position, image in stored_frames))
            db.execute("DELETE FROM vectors WHERE owner=? AND kind=? AND id=?", (owner, kind, identifier))
        return self.get(owner, kind, identifier)

    def migrate_reaction_blobs_to_files(self, *, batch_size: int = 64) -> dict[str, int]:
        """Losslessly offload legacy inline reaction & frame BLOBs to content-addressed files."""
        chunk = max(8, int(batch_size))
        items_migrated = 0
        frames_migrated = 0
        already_file_backed = 0
        bytes_offloaded = 0

        last_rowid = 0
        while True:
            with self.db(read_only=True) as db:
                rows = db.execute(
                    "SELECT rowid, owner, kind, id, payload, content FROM items "
                    "WHERE rowid > ? AND kind = 'reaction' AND content IS NOT NULL "
                    "ORDER BY rowid ASC LIMIT ?",
                    (last_rowid, chunk),
                ).fetchall()
            if not rows:
                break
            updates: list[tuple[bytes, str, str, str]] = []
            for row in rows:
                last_rowid = int(row["rowid"])
                raw = bytes(row["content"])
                if not raw or self.is_reaction_file_marker(raw):
                    already_file_backed += 1
                    continue
                mime_type = ""
                try:
                    desc = json.loads(row["payload"]).get("descriptor")
                    if isinstance(desc, dict):
                        mime_type = str(desc.get("mime_type") or "")
                except (TypeError, ValueError, AttributeError):
                    pass
                marker = self._store_reaction_bytes(raw, mime_type=mime_type)
                updates.append((marker, row["owner"], row["kind"], row["id"]))
                items_migrated += 1
                bytes_offloaded += max(0, len(raw) - len(marker))
            if updates:
                with self.db() as db:
                    db.executemany(
                        "UPDATE items SET content = ? WHERE owner = ? AND kind = ? AND id = ?",
                        updates,
                    )

        last_rowid = 0
        while True:
            with self.db(read_only=True) as db:
                rows = db.execute(
                    "SELECT rowid, owner, id, position, content FROM reaction_frames "
                    "WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                    (last_rowid, chunk),
                ).fetchall()
            if not rows:
                break
            frame_updates: list[tuple[bytes, str, str, int]] = []
            for row in rows:
                last_rowid = int(row["rowid"])
                raw = bytes(row["content"])
                if not raw or self.is_reaction_file_marker(raw):
                    already_file_backed += 1
                    continue
                marker = self._store_reaction_bytes(raw)
                frame_updates.append((marker, row["owner"], row["id"], row["position"]))
                frames_migrated += 1
                bytes_offloaded += max(0, len(raw) - len(marker))
            if frame_updates:
                with self.db() as db:
                    db.executemany(
                        "UPDATE reaction_frames SET content = ? WHERE owner = ? AND id = ? AND position = ?",
                        frame_updates,
                    )

        return {
            "items_migrated": items_migrated,
            "frames_migrated": frames_migrated,
            "already_file_backed": already_file_backed,
            "bytes_offloaded": bytes_offloaded,
        }

    def gc_unreferenced_reaction_files(self, *, min_age_seconds: float = 300.0) -> dict[str, int]:
        """Remove unreferenced files under ``media/reactions`` older than *min_age_seconds*.

        The cutoff timestamp is captured *before* reading committed SQLite
        references so concurrent writers that flush a new file to disk before
        committing their metadata row are protected by the grace window.
        """
        if not self.reaction_media_root.exists():
            return {"removed_files": 0, "reclaimed_bytes": 0}
        grace = max(0.0, float(min_age_seconds))
        cutoff = time.time() - grace
        referenced: set[str] = set()
        with self.db(read_only=True) as db:
            for query in (
                "SELECT content FROM items WHERE content IS NOT NULL",
                "SELECT content FROM reaction_frames WHERE content IS NOT NULL",
            ):
                for row in db.execute(query):
                    raw = bytes(row[0])
                    if self.is_reaction_file_marker(raw):
                        try:
                            rel = raw[len(REACTION_FILE_MARKER):].decode("ascii", errors="strict")
                        except UnicodeDecodeError:
                            continue
                        if _REACTION_MARKER_RE.match(rel):
                            referenced.add(rel)
        removed = 0
        reclaimed = 0
        for file_path in self.reaction_media_root.rglob("*"):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            rel = file_path.relative_to(self.reaction_media_root).as_posix()
            if rel not in referenced:
                try:
                    st = file_path.stat()
                    if grace > 0.0 and st.st_mtime >= cutoff:
                        continue
                    file_path.unlink()
                    removed += 1
                    reclaimed += st.st_size
                except OSError:
                    continue
        return {"removed_files": removed, "reclaimed_bytes": reclaimed}

    def delete(self, owner, kind, identifier, *, expected):
        with self.db() as db:
            row = db.execute("SELECT revision FROM items WHERE owner=? AND kind=? AND id=?",
                             (owner, kind, identifier)).fetchone()
            if row is None:
                raise TaskError("semantic_item_missing", "catalog item is unavailable")
            if row["revision"] != expected:
                raise TaskError("semantic_conflict", "item changed; refresh before deleting")
            db.execute("DELETE FROM vectors WHERE owner=? AND kind=? AND id=?", (owner, kind, identifier))
            if kind == "reaction":
                db.execute("DELETE FROM reaction_frames WHERE owner=? AND id=?", (owner, identifier))
                db.execute("DELETE FROM uses WHERE owner=? AND id=?", (owner, identifier))
                db.execute("DELETE FROM reaction_deliveries WHERE owner=? AND id=?", (owner, identifier))
                db.execute("DELETE FROM reaction_affinity WHERE owner=? AND id=?", (owner, identifier))
            db.execute("DELETE FROM items WHERE owner=? AND kind=? AND id=? AND revision=?",
                       (owner, kind, identifier, expected))
        return {"deleted": True}

    def record_reaction_collection(self, owner, run_key, scope, status, *, at, code=None, item_id=None):
        terminal = {"added", "pending_confirmation", "duplicate", "rejected", "failed", "cancelled"}
        if (scope not in ("private_capture", "group_capture") or status not in terminal | {"checking"}
                or type(at) not in (int, float)):
            raise ValueError("invalid reaction collection record")
        with self.db() as db:
            previous = db.execute("SELECT status FROM reaction_collection_runs WHERE owner=? AND run_key=?",
                                  (owner, run_key)).fetchone()
            was_terminal = previous is not None and previous["status"] in terminal
            db.execute("""INSERT INTO reaction_collection_runs VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(owner,run_key) DO UPDATE SET scope=excluded.scope,status=excluded.status,
                code=excluded.code,item_id=excluded.item_id,updated_at=excluded.updated_at""",
                (owner, run_key, scope, status, code, item_id, float(at)))
            if status in terminal and not was_terminal:
                db.execute("""INSERT INTO reaction_collection_totals VALUES (?,?,?,1)
                    ON CONFLICT(owner,scope,status) DO UPDATE SET count=count+1""",
                    (owner, scope, status))
            db.execute("""DELETE FROM reaction_collection_runs WHERE owner=? AND run_key NOT IN
                (SELECT run_key FROM reaction_collection_runs WHERE owner=? ORDER BY updated_at DESC LIMIT 100)""",
                (owner, owner))

    def reaction_collection_summary(self, owner):
        scopes = ("private_capture", "group_capture")
        statuses = ("added", "pending_confirmation", "duplicate", "rejected", "failed", "cancelled")
        summary = {scope: {status: 0 for status in statuses} for scope in scopes}
        with self.db(read_only=True) as db:
            for row in db.execute("SELECT scope,status,count FROM reaction_collection_totals WHERE owner=?", (owner,)):
                if row["scope"] in summary and row["status"] in statuses:
                    summary[row["scope"]][row["status"]] = row["count"]
            recent = [dict(row) for row in db.execute("""SELECT scope,status,code,updated_at
                FROM reaction_collection_runs WHERE owner=? ORDER BY updated_at DESC,run_key DESC LIMIT 8""",
                (owner,))]
        return {"totals": summary, "recent": recent}

    def promote_reaction_owner(self, source_owner, target_owner):
        """Copy a legacy window-owned reaction catalog to its stable role owner."""
        if source_owner == target_owner:
            return False
        with self.db() as db:
            if db.execute("SELECT 1 FROM reaction_owner_migrations WHERE source_owner=? AND target_owner=?",
                          (source_owner, target_owner)).fetchone():
                return False
            rows = db.execute("SELECT * FROM items WHERE owner=? AND kind='reaction' ORDER BY rowid, id",
                              (source_owner,)).fetchall()
            identifiers = tuple(row["id"] for row in rows)
            for row in rows:
                db.execute("INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?)",
                    (target_owner, row["kind"], row["id"], row["revision"], row["payload"],
                     row["content"], row["enabled"]))
                db.execute("INSERT OR IGNORE INTO vectors SELECT ?,kind,id,revision,model,hash,vector "
                           "FROM vectors WHERE owner=? AND kind='reaction' AND id=?",
                           (target_owner, source_owner, row["id"]))
                db.execute("INSERT OR IGNORE INTO reaction_frames SELECT ?,id,position,content "
                           "FROM reaction_frames WHERE owner=? AND id=?",
                           (target_owner, source_owner, row["id"]))
                db.execute("INSERT OR IGNORE INTO uses SELECT ?,id,batch,used_at FROM uses "
                           "WHERE owner=? AND id=?", (target_owner, source_owner, row["id"]))
            for row in db.execute("SELECT batch,call,payload FROM offers WHERE owner=?", (source_owner,)).fetchall():
                try:
                    payload = json.loads(row["payload"])
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, dict) and payload.get("kind") == "reaction":
                    db.execute("UPDATE offers SET owner=? WHERE batch=? AND call=? AND owner=?",
                               (target_owner, row["batch"], row["call"], source_owner))
            db.execute("""INSERT OR IGNORE INTO reaction_collection_runs
                SELECT ?,run_key,scope,status,code,item_id,updated_at FROM reaction_collection_runs
                WHERE owner=?""", (target_owner, source_owner))
            totals = db.execute("SELECT scope,status,count FROM reaction_collection_totals WHERE owner=?",
                                (source_owner,)).fetchall()
            for row in totals:
                db.execute("""INSERT INTO reaction_collection_totals VALUES (?,?,?,?)
                    ON CONFLICT(owner,scope,status) DO UPDATE SET count=count+excluded.count""",
                    (target_owner, row["scope"], row["status"], row["count"]))
            db.execute("INSERT INTO reaction_owner_migrations VALUES (?,?)", (source_owner, target_owner))
        return bool(identifiers)

    def unclaimed_legacy_reaction_owners(self, target_owner):
        """Return unclaimed catalogs made entirely from explicit V1 imports."""
        with self.db(read_only=True) as db:
            rows = db.execute("""SELECT i.owner FROM items i
                WHERE i.kind='reaction' AND i.owner<>?
                GROUP BY i.owner
                HAVING SUM(CASE WHEN json_extract(i.payload,'$.collection_source')='legacy_migration'
                                THEN 1 ELSE 0 END)=COUNT(*)
                AND NOT EXISTS (SELECT 1 FROM reaction_owner_migrations m
                                WHERE m.source_owner=i.owner)
                ORDER BY i.owner""", (target_owner,)).fetchall()
        return tuple(row["owner"] for row in rows)

    def vector(self, item, model):
        with self.db(read_only=True) as db:
            row = db.execute("SELECT * FROM vectors WHERE owner=? AND kind=? AND id=? AND model=?",
                             (item["owner"], item["kind"], item["id"], model)).fetchone()
            if row and row["revision"] == item["revision"] and row["hash"] == digest(item["payload"]):
                return decode_vector_tuple(row["vector"])
            return None

    def vectors_for_items(self, owner, kind, model, items):
        """Batch-fetch fresh raw vector payloads (float16 BLOB or legacy JSON) for *items*."""
        if not items or not model:
            return {}
        expected = {
            item["id"]: (item["revision"], digest(item["payload"]))
            for item in items
        }
        identifiers = tuple(expected)
        matched = {}
        chunk_size = 500
        with self.db(read_only=True) as db:
            for start in range(0, len(identifiers), chunk_size):
                chunk = identifiers[start:start + chunk_size]
                marks = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"SELECT id, revision, hash, vector FROM vectors "
                    f"WHERE owner=? AND kind=? AND model=? AND id IN ({marks})",
                    (owner, kind, model, *chunk),
                ).fetchall()
                for row in rows:
                    item_id = row["id"]
                    rev_hash = expected.get(item_id)
                    if rev_hash is not None and row["revision"] == rev_hash[0] and row["hash"] == rev_hash[1]:
                        matched[item_id] = row["vector"]
        return matched

    def put_vector(self, item, model, vector):
        current = self.get(item["owner"], item["kind"], item["id"])
        if current["revision"] != item["revision"] or current["payload"] != item["payload"] or not current["enabled"]:
            return False
        encoded_blob = encode_vector_blob(vector)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?,?,?)", (item["owner"], item["kind"],
                       item["id"], item["revision"], model, digest(item["payload"]), encoded_blob))
        return True

    def offer(self, batch, call, owner, payload):
        with self.db() as db:
            old = db.execute("SELECT owner,payload FROM offers WHERE batch=? AND call=?", (batch, call)).fetchone()
            if old and tuple(old) != (owner, encode(payload)):
                raise TaskError("semantic_offer_conflict", "original semantic result cannot be replaced")
            db.execute("INSERT OR IGNORE INTO offers VALUES (?,?,?,?)", (batch, call, owner, encode(payload)))

    def offers(self, batch, owner):
        with self.db(read_only=True) as db:
            return [(r["call"], json.loads(r["payload"])) for r in db.execute(
                "SELECT * FROM offers WHERE batch=? AND owner=? ORDER BY call", (batch, owner))]
