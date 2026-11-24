"""One transaction publishes a generation receipt and its immutable originals."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from companion_v2.storage.image_payloads import image_extension
from companion_v2.storage.sqlite_tuning import active_sqlite_tuning_profile, apply_sidecar_pragmas

from .types import NativeImageError, document, fingerprint, inspect_image

NATIVE_IMAGE_FILE_MARKER = b"\x00PCNIMG1/"
_MARKER_REL_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}\.(?:png|jpg|webp|gif|bin)$")


_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS runtime (singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS provider_identity ("
    "singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity_key TEXT NOT NULL, generation TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS operations ("
    "id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT NOT NULL, "
    "fingerprint TEXT NOT NULL, generation TEXT NOT NULL, request TEXT NOT NULL, "
    "state TEXT NOT NULL, locator TEXT NOT NULL DEFAULT '{}', "
    "receipt TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', "
    "created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(scope,request_id))",
    "CREATE TABLE IF NOT EXISTS artifacts ("
    "id TEXT PRIMARY KEY, scope TEXT NOT NULL, operation_id TEXT NOT NULL, "
    "ordinal INTEGER NOT NULL, metadata TEXT NOT NULL, data BLOB NOT NULL, "
    "UNIQUE(operation_id,ordinal), FOREIGN KEY(operation_id) REFERENCES operations(id))",
)


class ImageStore:
    BUSY_TIMEOUT_MS = 5000

    def __init__(self, directory: Path, generation: str | None, *, identity_key: str, profile=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "native_image.sqlite3"
        self.runtime_id = uuid4().hex
        self._explicit_profile = profile
        is_new = not self.path.exists()
        with self.db(enable_wal=True, set_incremental_vacuum=is_new) as db:
            for statement in _SCHEMA_STATEMENTS:
                db.execute(statement)
            identity = db.execute("SELECT * FROM provider_identity WHERE singleton=1").fetchone()
            self.generation = generation or (identity["generation"] if identity and identity["identity_key"] == identity_key else uuid4().hex)
            db.execute("INSERT INTO provider_identity(singleton,identity_key,generation) VALUES(1,?,?) "
                       "ON CONFLICT(singleton) DO UPDATE SET identity_key=excluded.identity_key,generation=excluded.generation", (identity_key, self.generation))
            db.execute("INSERT INTO runtime(singleton,generation) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation", (self.runtime_id,))
            db.execute("UPDATE operations SET state=CASE WHEN state='prepared' THEN 'stopped' ELSE 'unknown' END, "
                       "error='runtime_restarted',updated=? WHERE state IN ('prepared','dispatching')", (time.time(),))

    @property
    def _profile(self):
        return self._explicit_profile or active_sqlite_tuning_profile()

    @property
    def media_root(self) -> Path:
        return self.directory / "media" / "images"

    @staticmethod
    def is_file_marker(value) -> bool:
        return isinstance(value, (bytes, bytearray, memoryview)) and bytes(value).startswith(NATIVE_IMAGE_FILE_MARKER)

    def _store_media_bytes(self, content: bytes, *, media_type: str = "") -> bytes:
        if self.is_file_marker(content):
            return bytes(content)
        digest_hex = hashlib.sha256(content).hexdigest()
        ext = image_extension(content, media_type)
        rel = f"{digest_hex[:2]}/{digest_hex}{ext}"
        target_dir = self.media_root / digest_hex[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest_hex}{ext}"
        if not target.is_file() or target.stat().st_size != len(content):
            fd, tmp_name = tempfile.mkstemp(prefix=".nimg-", suffix=".tmp", dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, target)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        return NATIVE_IMAGE_FILE_MARKER + rel.encode("ascii")

    def _load_media_bytes(self, raw: bytes) -> bytes:
        if not self.is_file_marker(raw):
            return raw
        try:
            rel = raw[len(NATIVE_IMAGE_FILE_MARKER):].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise NativeImageError("image_artifact_corrupt") from error
        if not _MARKER_REL_RE.match(rel):
            raise NativeImageError("image_artifact_corrupt")
        path = self.media_root / rel
        try:
            return path.read_bytes()
        except OSError as error:
            raise NativeImageError("image_artifact_corrupt") from error

    @contextmanager
    def db(self, *, enable_wal: bool = False, set_incremental_vacuum: bool = False):
        """Open a WAL-mode write transaction (BEGIN IMMEDIATE)."""
        db = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if set_incremental_vacuum:
                try:
                    db.execute("PRAGMA auto_vacuum = INCREMENTAL")
                except sqlite3.Error:
                    pass
            apply_sidecar_pragmas(
                db,
                profile=self._profile,
                enable_wal=enable_wal,
                read_only=False,
                busy_timeout_ms=self.BUSY_TIMEOUT_MS,
                foreign_keys=True,
            )
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @contextmanager
    def read_db(self):
        """Open a read-only WAL snapshot without acquiring a write lock."""
        db = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            apply_sidecar_pragmas(
                db,
                profile=self._profile,
                enable_wal=False,
                read_only=True,
                busy_timeout_ms=self.BUSY_TIMEOUT_MS,
                foreign_keys=True,
            )
            db.execute("BEGIN")
            yield db
            if db.in_transaction:
                db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _current(self, db):
        if db.execute("SELECT generation FROM runtime WHERE singleton=1").fetchone()[0] != self.runtime_id:
            raise NativeImageError("provider_generation_changed")

    @staticmethod
    def _decode(row):
        if row is None:
            raise NativeImageError("image_operation_not_found")
        result = dict(row)
        for name in ("request", "locator", "receipt"):
            result[name] = json.loads(result[name])
        return result

    def begin(self, request: dict) -> tuple[dict, bool]:
        encoded, digest = document(request), fingerprint(request)
        with self.db() as db:
            self._current(db)
            previous = db.execute("SELECT * FROM operations WHERE scope=? AND request_id=?", (request["scope_key"], request["request_id"])).fetchone()
            if previous:
                if previous["fingerprint"] != digest:
                    raise NativeImageError("image_request_conflict")
                return self._decode(previous), False
            operation_id, now = "imgop_" + uuid4().hex, time.time()
            db.execute("INSERT INTO operations(id,scope,request_id,fingerprint,generation,request,state,created,updated) VALUES(?,?,?,?,?,?,'prepared',?,?)",
                       (operation_id, request["scope_key"], request["request_id"], digest, self.generation, encoded, now, now))
            return self._decode(db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()), True

    def get(self, scope: str, operation_id: str) -> dict:
        with self.read_db() as db:
            self._current(db)
            return self._decode(db.execute("SELECT * FROM operations WHERE scope=? AND id=?", (scope, operation_id)).fetchone())

    def by_request(self, scope: str, request_id: str) -> dict:
        with self.read_db() as db:
            self._current(db)
            return self._decode(db.execute("SELECT * FROM operations WHERE scope=? AND request_id=?", (scope, request_id)).fetchone())

    def state(self, scope: str, operation_id: str, state: str, *, error: str = "", locator=None, receipt=None) -> dict:
        """Move one operation between the reopenable states.

        ``prepared``, ``dispatching`` and ``pending`` are the only reopenable
        states.  ``dispatching -> failed`` is deliberate: the dispatch fence is
        crossed before the POST, so ``dispatching`` proves that a request *may*
        have been sent, and a definitive provider refusal (4xx) proves that it
        was not executed.  Rows already stored as ``unknown`` are never
        rewritten here; replaying their ``(scope, request_id)`` returns them
        unchanged and never dispatches again.
        """
        with self.db() as db:
            self._current(db)
            current = self._decode(db.execute("SELECT * FROM operations WHERE scope=? AND id=?", (scope, operation_id)).fetchone())
            if current["state"] not in {"prepared", "dispatching", "pending"}:
                raise NativeImageError("image_operation_final")
            db.execute("UPDATE operations SET state=?,error=?,locator=?,receipt=?,updated=? WHERE id=?",
                       (state, error, document(current["locator"] if locator is None else locator),
                        document(current["receipt"] if receipt is None else receipt), time.time(), operation_id))
            return self._decode(db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone())

    def complete(self, scope: str, operation_id: str, images: tuple[bytes, ...], backend_receipt: dict) -> dict:
        # Verify and offload every image to content-addressed disk storage before
        # starting the short SQLite publication transaction.
        checked = [(raw, inspect_image(raw)) for raw in images]
        if not checked:
            raise NativeImageError("image_outputs_missing")
        markers = [self._store_media_bytes(raw, media_type=info.get("media_type", "")) for raw, info in checked]
        with self.db() as db:
            self._current(db)
            operation = self._decode(db.execute("SELECT * FROM operations WHERE scope=? AND id=?", (scope, operation_id)).fetchone())
            if operation["state"] not in {"dispatching", "pending"}:
                raise NativeImageError("image_operation_final")
            request = operation["request"]
            artifacts = []
            for index, (raw, info) in enumerate(checked):
                item = {"artifact_id": "imgart_" + uuid4().hex, "scope_key": scope,
                        "operation_id": operation_id, "origin_key": request["origin_key"],
                        "provider_generation": operation["generation"], "invocation_id": request["invocation_id"],
                        "request_id": request["request_id"], "operation": request["operation"], "ordinal": index, **info}
                artifacts.append(item)
            receipt = {"receipt_id": "imgreceipt_" + uuid4().hex, "operation_id": operation_id,
                       "invocation_id": request["invocation_id"], "request_id": request["request_id"],
                       "origin_key": request["origin_key"], "provider_generation": operation["generation"],
                       "backend": backend_receipt, "artifacts": artifacts, "state": "succeeded"}
            receipt["fingerprint"] = fingerprint(receipt)
            for item, marker in zip(artifacts, markers):
                metadata = {**item, "operation_receipt": receipt}
                db.execute("INSERT INTO artifacts(id,scope,operation_id,ordinal,metadata,data) VALUES(?,?,?,?,?,?)",
                           (item["artifact_id"], scope, operation_id, item["ordinal"], document(metadata), marker))
            db.execute("UPDATE operations SET state='succeeded',receipt=?,error='',updated=? WHERE id=?", (document(receipt), time.time(), operation_id))
            return self._decode(db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone())

    def artifact(self, scope: str, artifact_id: str) -> tuple[dict, bytes]:
        with self.read_db() as db:
            self._current(db)
            row = db.execute("SELECT metadata,data FROM artifacts WHERE scope=? AND id=?", (scope, artifact_id)).fetchone()
            if row is None:
                raise NativeImageError("image_artifact_not_found")
            metadata, raw = json.loads(row["metadata"]), self._load_media_bytes(bytes(row["data"]))
            if inspect_image(raw) != {key: metadata[key] for key in ("sha256", "size_bytes", "media_type", "width", "height", "frame_count")}:
                raise NativeImageError("image_artifact_corrupt")
            receipt = metadata["operation_receipt"]
            descriptor = {key: value for key, value in metadata.items() if key != "operation_receipt"}
            if (descriptor not in receipt.get("artifacts", ()) or metadata["artifact_id"] != artifact_id
                    or metadata["scope_key"] != scope):
                raise NativeImageError("image_artifact_metadata_corrupt")
            if fingerprint({key: value for key, value in receipt.items() if key != "fingerprint"}) != receipt["fingerprint"]:
                raise NativeImageError("image_receipt_corrupt")
            operation = self._decode(db.execute("SELECT * FROM operations WHERE id=? AND scope=?", (metadata["operation_id"], scope)).fetchone())
            if operation["state"] != "succeeded" or operation["receipt"] != receipt:
                raise NativeImageError("image_receipt_corrupt")
            return metadata, raw

    def migrate_artifacts_to_files(self, *, batch_size: int = 64) -> dict[str, int]:
        """Losslessly offload legacy inline ``artifacts.data`` BLOBs to content-addressed files."""
        chunk = max(8, int(batch_size))
        migrated = 0
        already_file_backed = 0
        bytes_offloaded = 0
        last_rowid = 0
        while True:
            with self.read_db() as db:
                rows = db.execute(
                    "SELECT rowid, id, metadata, data FROM artifacts WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                    (last_rowid, chunk),
                ).fetchall()
            if not rows:
                break
            updates: list[tuple[bytes, str]] = []
            for row in rows:
                last_rowid = int(row["rowid"])
                raw = bytes(row["data"])
                if self.is_file_marker(raw):
                    already_file_backed += 1
                    continue
                media_type = ""
                try:
                    media_type = str(json.loads(row["metadata"]).get("media_type") or "")
                except (TypeError, ValueError):
                    pass
                marker = self._store_media_bytes(raw, media_type=media_type)
                updates.append((marker, row["id"]))
                migrated += 1
                bytes_offloaded += max(0, len(raw) - len(marker))
            if updates:
                with self.db() as db:
                    db.executemany("UPDATE artifacts SET data = ? WHERE id = ?", updates)
        return {
            "migrated": migrated,
            "already_file_backed": already_file_backed,
            "bytes_offloaded": bytes_offloaded,
        }

    def gc_unreferenced_media(self, *, min_age_seconds: float = 300.0) -> dict[str, int]:
        """Remove unreferenced files under ``media/images`` older than *min_age_seconds*.

        The cutoff timestamp is captured *before* reading committed SQLite
        references so concurrent writes that persist a file before committing
        their artifact row cannot be unlinked prematurely.
        """
        if not self.media_root.exists():
            return {"removed_files": 0, "reclaimed_bytes": 0}
        grace = max(0.0, float(min_age_seconds))
        cutoff = time.time() - grace
        referenced: set[str] = set()
        with self.read_db() as db:
            for row in db.execute("SELECT data FROM artifacts"):
                raw = bytes(row["data"])
                if self.is_file_marker(raw):
                    try:
                        rel = raw[len(NATIVE_IMAGE_FILE_MARKER):].decode("ascii", errors="strict")
                    except UnicodeDecodeError:
                        continue
                    if _MARKER_REL_RE.match(rel):
                        referenced.add(rel)
        removed = 0
        reclaimed = 0
        for file_path in self.media_root.rglob("*"):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            rel = file_path.relative_to(self.media_root).as_posix()
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


def migrate_native_image_store_at(directory: Path | str, *, batch_size: int = 64) -> dict[str, int]:
    """Safely offload inline artifact BLOBs in an existing ``native_image.sqlite3``.

    Unlike instantiating ``ImageStore``, this helper does not rotate runtime
    generation or mutate in-flight operations, making it safe to invoke from
    online maintenance/compaction jobs.
    """
    base_dir = Path(directory)
    db_path = base_dir / "native_image.sqlite3"
    if not db_path.is_file():
        return {"migrated": 0, "already_file_backed": 0, "bytes_offloaded": 0}
    profile = active_sqlite_tuning_profile()
    media_root = base_dir / "media" / "images"
    chunk = max(8, int(batch_size))
    migrated = 0
    already_file_backed = 0
    bytes_offloaded = 0
    last_rowid = 0
    while True:
        conn = sqlite3.connect(db_path, timeout=ImageStore.BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            apply_sidecar_pragmas(
                conn,
                profile=profile,
                enable_wal=False,
                read_only=True,
                busy_timeout_ms=ImageStore.BUSY_TIMEOUT_MS,
                foreign_keys=True,
            )
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'artifacts'"
            ).fetchone()
            if has_table is None:
                return {"migrated": 0, "already_file_backed": 0, "bytes_offloaded": 0}
            rows = conn.execute(
                "SELECT rowid, id, metadata, data FROM artifacts WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                (last_rowid, chunk),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            break
        updates: list[tuple[bytes, str]] = []
        for row in rows:
            last_rowid = int(row["rowid"])
            raw = bytes(row["data"])
            if ImageStore.is_file_marker(raw):
                already_file_backed += 1
                continue
            media_type = ""
            try:
                media_type = str(json.loads(row["metadata"]).get("media_type") or "")
            except (TypeError, ValueError):
                pass
            digest_hex = hashlib.sha256(raw).hexdigest()
            ext = image_extension(raw, media_type)
            rel = f"{digest_hex[:2]}/{digest_hex}{ext}"
            target_dir = media_root / digest_hex[:2]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{digest_hex}{ext}"
            if not target.is_file() or target.stat().st_size != len(raw):
                fd, tmp_name = tempfile.mkstemp(prefix=".nimg-", suffix=".tmp", dir=target_dir)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp_name, target)
                finally:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
            marker = NATIVE_IMAGE_FILE_MARKER + rel.encode("ascii")
            updates.append((marker, row["id"]))
            migrated += 1
            bytes_offloaded += max(0, len(raw) - len(marker))
        if updates:
            conn = sqlite3.connect(db_path, timeout=ImageStore.BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
            try:
                apply_sidecar_pragmas(
                    conn,
                    profile=profile,
                    enable_wal=False,
                    read_only=False,
                    busy_timeout_ms=ImageStore.BUSY_TIMEOUT_MS,
                    foreign_keys=True,
                )
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany("UPDATE artifacts SET data = ? WHERE id = ?", updates)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
    return {
        "migrated": migrated,
        "already_file_backed": already_file_backed,
        "bytes_offloaded": bytes_offloaded,
    }
