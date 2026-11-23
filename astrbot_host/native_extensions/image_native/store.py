"""One transaction publishes a generation receipt and its immutable originals."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from companion_v2.storage.sqlite_tuning import apply_sidecar_pragmas, tuning_profile

from .types import NativeImageError, document, fingerprint, inspect_image


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

    def __init__(self, directory: Path, generation: str | None, *, identity_key: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "native_image.sqlite3"
        self.runtime_id = uuid4().hex
        self._profile = tuning_profile()
        with self.db(enable_wal=True) as db:
            for statement in _SCHEMA_STATEMENTS:
                db.execute(statement)
            identity = db.execute("SELECT * FROM provider_identity WHERE singleton=1").fetchone()
            self.generation = generation or (identity["generation"] if identity and identity["identity_key"] == identity_key else uuid4().hex)
            db.execute("INSERT INTO provider_identity(singleton,identity_key,generation) VALUES(1,?,?) "
                       "ON CONFLICT(singleton) DO UPDATE SET identity_key=excluded.identity_key,generation=excluded.generation", (identity_key, self.generation))
            db.execute("INSERT INTO runtime(singleton,generation) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation", (self.runtime_id,))
            db.execute("UPDATE operations SET state=CASE WHEN state='prepared' THEN 'stopped' ELSE 'unknown' END, "
                       "error='runtime_restarted',updated=? WHERE state IN ('prepared','dispatching')", (time.time(),))

    @contextmanager
    def db(self, *, enable_wal: bool = False):
        """Open a WAL-mode write transaction (BEGIN IMMEDIATE)."""
        db = sqlite3.connect(self.path, timeout=self.BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
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
        # Verify every image before starting the publication transaction.
        checked = [(raw, inspect_image(raw)) for raw in images]
        if not checked:
            raise NativeImageError("image_outputs_missing")
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
            for item, (raw, _) in zip(artifacts, checked):
                metadata = {**item, "operation_receipt": receipt}
                db.execute("INSERT INTO artifacts(id,scope,operation_id,ordinal,metadata,data) VALUES(?,?,?,?,?,?)",
                           (item["artifact_id"], scope, operation_id, item["ordinal"], document(metadata), raw))
            db.execute("UPDATE operations SET state='succeeded',receipt=?,error='',updated=? WHERE id=?", (document(receipt), time.time(), operation_id))
            return self._decode(db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone())

    def artifact(self, scope: str, artifact_id: str) -> tuple[dict, bytes]:
        with self.read_db() as db:
            self._current(db)
            row = db.execute("SELECT metadata,data FROM artifacts WHERE scope=? AND id=?", (scope, artifact_id)).fetchone()
            if row is None:
                raise NativeImageError("image_artifact_not_found")
            metadata, raw = json.loads(row["metadata"]), bytes(row["data"])
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
