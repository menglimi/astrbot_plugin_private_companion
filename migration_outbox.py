"""REQ-041 durable outbox, revision, epoch and tombstone primitives."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator

try:
    from .identity_namespace import NamespaceContext, validate_namespace_context
except ImportError:  # pragma: no cover - direct-module test compatibility
    from identity_namespace import NamespaceContext, validate_namespace_context


MIGRATION_STATES = frozenset({"active", "degraded", "paused", "replaying", "verified"})
OUTBOX_STATES = frozenset({"pending", "applied", "failed", "discarded"})
MAX_PAYLOAD_BYTES = 32768
FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "chat_text", "content", "conversation", "evidence_body", "message_text", "messages", "prompt", "raw_text",
})


class OutboxError(RuntimeError):
    pass


class OutboxConflict(OutboxError):
    pass


class RevisionGap(OutboxError):
    pass


class StaleMigrationEpoch(OutboxError):
    pass


@dataclass(frozen=True, slots=True)
class OutboxItem:
    event_id: str
    migration_epoch: str
    source_revision: int
    namespace: dict[str, str]
    policy_version: str
    payload: dict[str, Any]
    payload_hash: str
    state: str
    retry_count: int
    error_code: str
    target_revision: int
    stream_key: str = ""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _contains_forbidden(value: Any, depth: int = 0) -> bool:
    if depth > 5:
        return True
    if isinstance(value, dict):
        return any(
            not isinstance(key, str)
            or key.strip().lower() in FORBIDDEN_PAYLOAD_KEYS
            or _contains_forbidden(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item, depth + 1) for item in value)
    return not (value is None or isinstance(value, (str, int, float, bool)))


def _payload(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict) or _contains_forbidden(value):
        raise OutboxError("outbox_payload_invalid")
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OutboxError("outbox_payload_invalid") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise OutboxError("outbox_payload_too_large")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _token(value: Any, *, limit: int = 128) -> str:
    if not isinstance(value, str):
        return ""
    result = value.strip()
    if not result or len(result) > limit or any(ord(ch) < 32 for ch in result):
        return ""
    return result


# 单个事件的最大重放次数；超过后不再参与重放，避免它一直卡在队首阻塞其余事件。
_OUTBOX_MAX_RETRIES = 5
OUTBOX_MAX_RETRIES = _OUTBOX_MAX_RETRIES


def _replayable_clause(alias: str = "") -> tuple[str, tuple[Any, ...]]:
    """本模块「可重放集合（replayable）」的**唯一**定义。

    可重放 := state ∈ {pending, failed} 且 retry_count < OUTBOX_MAX_RETRIES
    被放弃 := state ∈ {pending, failed} 且 retry_count >= OUTBOX_MAX_RETRIES

    pending() / backlog_for_stream() / safe_admin_summary()["backlog"] 都必须用本函数构造
    谓词，不得再各自手写条件。被放弃集合定义见 _abandoned_clause()，二者是本集合的严格
    补集（都限定在 pending/failed 内），合起来等于 _unresolved_clause()。
    """
    column = f"{alias}." if alias else ""
    return (
        f"{column}state IN ('pending','failed') AND {column}retry_count < ?",
        (int(_OUTBOX_MAX_RETRIES),),
    )


def _abandoned_clause(alias: str = "") -> tuple[str, tuple[Any, ...]]:
    """「被放弃集合（abandoned）」的**唯一**定义：重放上限之外、重放器不再尝试的事件。

    它是 _replayable_clause() 在 pending/failed 内的严格补集，用于可见性与人工重排，
    绝不代表事件已应用。详见 MigrationOutbox.count_abandoned / list_abandoned。
    """
    column = f"{alias}." if alias else ""
    return (
        f"{column}state = 'dead_letter' OR ({column}state IN ('pending','failed') AND {column}retry_count >= ?)",
        (int(_OUTBOX_MAX_RETRIES),),
    )


def _unresolved_clause(alias: str = "") -> tuple[str, tuple[Any, ...]]:
    """「未了结集合（unresolved）」的**唯一**定义：所有尚未成功应用的 pending/failed 事件。

    等于可重放集合 ⊎ 被放弃集合，**刻意忽略重放上限**。仅用于不可逆的破坏性门禁
    （retire_streams）：只要还有任何未应用 revision，无论是否已被上限放弃，都不能被当作
    已归档而静默擦除。日常重放进度/积压口径请用 _replayable_clause()。
    """
    column = f"{alias}." if alias else ""
    return (f"{column}state IN ('pending','failed','dead_letter')", ())


class MigrationOutbox:
    """SQLite-backed single-process outbox with transactional idempotency."""

    def __init__(self, path: str | Path, *, clock: Any = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock if callable(clock) else time.time
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")
        return conn

    def _truncate_wal(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                if conn.in_transaction:
                    conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS migration_epochs (
                    migration_epoch TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    checkpoint TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    namespace_json TEXT NOT NULL,
                    namespace_scope TEXT NOT NULL,
                    stream_key TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    target_revision INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (event_id, migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(migration_epoch, state, source_revision, created_at);
                CREATE TABLE IF NOT EXISTS revisions (
                    stream_key TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (stream_key, migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                CREATE TABLE IF NOT EXISTS tombstones (
                    object_key TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (object_key, migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                CREATE TABLE IF NOT EXISTS stream_retirement_operations (
                    operation_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(operation_id,migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                CREATE TABLE IF NOT EXISTS retired_streams (
                    stream_key TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    retired_revision INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(stream_key,migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                CREATE TABLE IF NOT EXISTS stream_purge_operations (
                    operation_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(operation_id,migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                CREATE TABLE IF NOT EXISTS purged_streams (
                    stream_key TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    purged_at REAL NOT NULL,
                    PRIMARY KEY(stream_key,migration_epoch),
                    FOREIGN KEY (migration_epoch) REFERENCES migration_epochs(migration_epoch)
                );
                """
            )
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(outbox)").fetchall()}
            if "stream_key" not in columns:
                conn.execute("ALTER TABLE outbox ADD COLUMN stream_key TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_outbox_stream
                   ON outbox(migration_epoch,stream_key,state,source_revision)"""
            )

    def begin_epoch(self, migration_epoch: str, *, policy_version: str, state: str = "active") -> dict[str, Any]:
        epoch = _token(migration_epoch)
        policy = _token(policy_version, limit=64)
        if not epoch or not policy or state not in MIGRATION_STATES:
            raise OutboxError("migration_epoch_invalid")
        now = float(self._clock())
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT state, checkpoint, policy_version FROM migration_epochs WHERE migration_epoch=?", (epoch,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO migration_epochs(migration_epoch,state,checkpoint,policy_version,updated_at) VALUES(?,?,?,?,?)",
                    (epoch, state, "", policy, now),
                )
            elif row["policy_version"] != policy:
                raise OutboxConflict("migration_epoch_policy_conflict")
        return self.epoch_status(epoch)

    def epoch_status(self, migration_epoch: str) -> dict[str, Any]:
        epoch = _token(migration_epoch)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT migration_epoch,state,checkpoint,policy_version,updated_at FROM migration_epochs WHERE migration_epoch=?",
                (epoch,),
            ).fetchone()
        return dict(row) if row is not None else {}

    def safe_admin_summary(self, migration_epoch: str) -> dict[str, Any]:
        """Return aggregate queue health; payloads and stream keys stay private.

        backlog 只统计**可重放集合**（_replayable_clause）：被上限放弃的事件不再把 backlog
        永久钉住（migration_stability 依赖 backlog 归零），而是单独计入 abandoned，绝不静默
        吞掉。states[state] 内的 replayable / abandoned 字段给出每个状态的同口径拆分。
        """
        epoch = _token(migration_epoch)
        now = float(self._clock())
        replayable_sql, replayable_params = _replayable_clause()
        abandoned_sql, abandoned_params = _abandoned_clause()
        with self._connection() as conn:
            rows = conn.execute(
                f"""SELECT state,COUNT(*) AS count,MIN(created_at) AS oldest_created_at,
                          MAX(retry_count) AS max_retry_count,
                          SUM(CASE WHEN {replayable_sql} THEN 1 ELSE 0 END) AS replayable_count,
                          SUM(CASE WHEN {abandoned_sql} THEN 1 ELSE 0 END) AS abandoned_count
                   FROM outbox WHERE migration_epoch=? GROUP BY state ORDER BY state""",
                (*replayable_params, *abandoned_params, epoch),
            ).fetchall()
        states = {
            str(row["state"]): {
                "count": int(row["count"] or 0),
                "oldest_age_ms": round(max(0.0, now - float(row["oldest_created_at"] or now)) * 1000.0, 3),
                "max_retry_count": int(row["max_retry_count"] or 0),
                "replayable": int(row["replayable_count"] or 0),
                "abandoned": int(row["abandoned_count"] or 0),
            }
            for row in rows
        }
        backlog = sum(
            int(item.get("replayable") or 0)
            for state, item in states.items() if state in {"pending", "failed"}
        )
        abandoned = sum(
            int(item.get("abandoned") or 0)
            for state, item in states.items() if state in {"pending", "failed", "dead_letter"}
        )
        return {"backlog": backlog, "abandoned": abandoned, "states": states}

    def set_epoch_state(self, migration_epoch: str, state: str, *, checkpoint: str | None = None) -> dict[str, Any]:
        epoch = _token(migration_epoch)
        if state not in MIGRATION_STATES:
            raise OutboxError("migration_state_invalid")
        with self._transaction() as conn:
            row = conn.execute("SELECT state,checkpoint FROM migration_epochs WHERE migration_epoch=?", (epoch,)).fetchone()
            if row is None:
                raise StaleMigrationEpoch("migration_epoch_missing")
            if row["state"] == "verified" and state != "verified":
                raise StaleMigrationEpoch("migration_epoch_closed")
            value = row["checkpoint"] if checkpoint is None else _token(checkpoint, limit=256)
            conn.execute(
                "UPDATE migration_epochs SET state=?,checkpoint=?,updated_at=? WHERE migration_epoch=?",
                (state, value, float(self._clock()), epoch),
            )
        return self.epoch_status(epoch)

    def _require_epoch(self, conn: sqlite3.Connection, epoch: str, policy: str) -> None:
        row = conn.execute(
            "SELECT state,policy_version FROM migration_epochs WHERE migration_epoch=?", (epoch,)
        ).fetchone()
        if row is None:
            raise StaleMigrationEpoch("migration_epoch_missing")
        if row["policy_version"] != policy:
            raise StaleMigrationEpoch("migration_policy_stale")
        if row["state"] == "verified":
            raise StaleMigrationEpoch("migration_epoch_closed")

    @staticmethod
    def _ensure_stream_active(conn: sqlite3.Connection, stream: str, epoch: str) -> None:
        retired = conn.execute(
            "SELECT 1 FROM retired_streams WHERE stream_key=? AND migration_epoch=?",
            (stream, epoch),
        ).fetchone()
        if retired is not None:
            raise OutboxConflict("outbox_stream_retired")

    def enqueue(
        self,
        *,
        event_id: str,
        source_revision: int,
        namespace: NamespaceContext,
        migration_epoch: str,
        policy_version: str,
        payload: dict[str, Any],
    ) -> str:
        event = _token(event_id)
        epoch = _token(migration_epoch)
        policy = _token(policy_version, limit=64)
        if not event or source_revision < 1 or not epoch or not policy:
            raise OutboxError("outbox_envelope_invalid")
        namespace_payload = namespace.to_dict() if isinstance(namespace, NamespaceContext) else {}
        if validate_namespace_context(namespace_payload):
            raise OutboxError("outbox_namespace_invalid")
        if namespace.migration_epoch != epoch or namespace.policy_version != policy:
            raise StaleMigrationEpoch("outbox_namespace_epoch_mismatch")
        encoded, digest = _payload(payload)
        namespace_json = _canonical(namespace_payload)
        now = float(self._clock())
        with self._transaction() as conn:
            self._require_epoch(conn, epoch, policy)
            existing = conn.execute(
                "SELECT source_revision,namespace_json,policy_version,payload_hash FROM outbox WHERE event_id=? AND migration_epoch=?",
                (event, epoch),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["source_revision"] == source_revision
                    and existing["namespace_json"] == namespace_json
                    and existing["policy_version"] == policy
                    and existing["payload_hash"] == digest
                )
                if same:
                    return "duplicate"
                raise OutboxConflict("outbox_event_conflict")
            conn.execute(
                """INSERT INTO outbox(
                    event_id,migration_epoch,source_revision,namespace_json,namespace_scope,stream_key,policy_version,
                    payload_json,payload_hash,state,retry_count,error_code,target_revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event, epoch, source_revision, namespace_json, namespace.cache_scope(), "", policy,
                    encoded, digest, "pending", 0, "", 0, now, now,
                ),
            )
        return "enqueued"

    def enqueue_next(
        self,
        *,
        stream_key: str,
        event_id: str,
        namespace: NamespaceContext,
        migration_epoch: str,
        policy_version: str,
        payload: dict[str, Any],
        expected_source_revision: int | None = None,
    ) -> dict[str, Any]:
        """Atomically allocate the next stream revision and persist its event."""
        stream = _token(stream_key)
        event = _token(event_id)
        epoch = _token(migration_epoch)
        policy = _token(policy_version, limit=64)
        namespace_payload = namespace.to_dict() if isinstance(namespace, NamespaceContext) else {}
        if not stream or not event or not epoch or not policy:
            raise OutboxError("outbox_envelope_invalid")
        if expected_source_revision is not None and (
            isinstance(expected_source_revision, bool)
            or not isinstance(expected_source_revision, int)
            or expected_source_revision < 1
        ):
            raise OutboxError("outbox_expected_revision_invalid")
        if validate_namespace_context(namespace_payload):
            raise OutboxError("outbox_namespace_invalid")
        if namespace.migration_epoch != epoch or namespace.policy_version != policy:
            raise StaleMigrationEpoch("outbox_namespace_epoch_mismatch")
        encoded, digest = _payload(payload)
        namespace_json = _canonical(namespace_payload)
        now = float(self._clock())
        with self._transaction() as conn:
            self._require_epoch(conn, epoch, policy)
            existing = conn.execute(
                """SELECT source_revision,namespace_json,policy_version,payload_hash
                   FROM outbox WHERE event_id=? AND migration_epoch=?""",
                (event, epoch),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["namespace_json"] == namespace_json
                    and existing["policy_version"] == policy
                    and existing["payload_hash"] == digest
                )
                if not same:
                    raise OutboxConflict("outbox_event_conflict")
                if expected_source_revision is not None and int(existing["source_revision"]) != expected_source_revision:
                    raise RevisionGap(
                        f"revision_gap:{existing['source_revision']}:{expected_source_revision}:{existing['source_revision']}"
                    )
                return {"status": "duplicate", "source_revision": int(existing["source_revision"])}
            self._ensure_stream_active(conn, stream, epoch)
            revision_row = conn.execute(
                "SELECT revision FROM revisions WHERE stream_key=? AND migration_epoch=?",
                (stream, epoch),
            ).fetchone()
            current = int(revision_row["revision"]) if revision_row is not None else 0
            next_revision = current + 1
            if expected_source_revision is not None and expected_source_revision != next_revision:
                raise RevisionGap(f"revision_gap:{current}:{expected_source_revision}:{next_revision}")
            conn.execute(
                """INSERT INTO outbox(
                    event_id,migration_epoch,source_revision,namespace_json,namespace_scope,stream_key,policy_version,
                    payload_json,payload_hash,state,retry_count,error_code,target_revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event, epoch, next_revision, namespace_json, namespace.cache_scope(), stream, policy,
                    encoded, digest, "pending", 0, "", 0, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO revisions(stream_key,migration_epoch,revision,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(stream_key,migration_epoch) DO UPDATE SET
                       revision=excluded.revision,updated_at=excluded.updated_at""",
                (stream, epoch, next_revision, now),
            )
        return {"status": "enqueued", "source_revision": next_revision}

    def stream_revision(self, stream_key: str, migration_epoch: str) -> int:
        stream, epoch = _token(stream_key), _token(migration_epoch)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT revision FROM revisions WHERE stream_key=? AND migration_epoch=?",
                (stream, epoch),
            ).fetchone()
        return int(row["revision"]) if row is not None else 0

    def enqueue_next_with_tombstone(
        self,
        *,
        stream_key: str,
        event_id: str,
        namespace: NamespaceContext,
        migration_epoch: str,
        policy_version: str,
        payload: dict[str, Any],
        tombstone_key: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Atomically append a stream event and advance its latest tombstone."""
        stream, event = _token(stream_key), _token(event_id)
        epoch, policy = _token(migration_epoch), _token(policy_version, limit=64)
        object_key, reason = _token(tombstone_key), _token(reason_code, limit=80)
        namespace_payload = namespace.to_dict() if isinstance(namespace, NamespaceContext) else {}
        if not stream or not event or not epoch or not policy or not object_key or not reason:
            raise OutboxError("outbox_tombstone_envelope_invalid")
        if validate_namespace_context(namespace_payload):
            raise OutboxError("outbox_namespace_invalid")
        if namespace.migration_epoch != epoch or namespace.policy_version != policy:
            raise StaleMigrationEpoch("outbox_namespace_epoch_mismatch")
        encoded, digest = _payload(payload)
        namespace_json = _canonical(namespace_payload)
        now = float(self._clock())
        with self._transaction() as conn:
            self._require_epoch(conn, epoch, policy)
            existing = conn.execute(
                """SELECT source_revision,namespace_json,policy_version,payload_hash
                   FROM outbox WHERE event_id=? AND migration_epoch=?""",
                (event, epoch),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["namespace_json"] == namespace_json
                    and existing["policy_version"] == policy
                    and existing["payload_hash"] == digest
                )
                tombstone = conn.execute(
                    "SELECT revision,reason_code FROM tombstones WHERE object_key=? AND migration_epoch=?",
                    (object_key, epoch),
                ).fetchone()
                if (
                    not same
                    or tombstone is None
                    or int(tombstone["revision"]) < int(existing["source_revision"])
                    or tombstone["reason_code"] != reason
                ):
                    raise OutboxConflict("outbox_tombstone_event_conflict")
                return {"status": "duplicate", "source_revision": int(existing["source_revision"])}
            self._ensure_stream_active(conn, stream, epoch)
            revision_row = conn.execute(
                "SELECT revision FROM revisions WHERE stream_key=? AND migration_epoch=?",
                (stream, epoch),
            ).fetchone()
            next_revision = (int(revision_row["revision"]) if revision_row is not None else 0) + 1
            prior_tombstone = conn.execute(
                "SELECT revision FROM tombstones WHERE object_key=? AND migration_epoch=?",
                (object_key, epoch),
            ).fetchone()
            if prior_tombstone is not None and int(prior_tombstone["revision"]) >= next_revision:
                raise OutboxConflict("tombstone_revision_conflict")
            conn.execute(
                """INSERT INTO outbox(
                    event_id,migration_epoch,source_revision,namespace_json,namespace_scope,stream_key,policy_version,
                    payload_json,payload_hash,state,retry_count,error_code,target_revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event, epoch, next_revision, namespace_json, namespace.cache_scope(), stream, policy,
                    encoded, digest, "pending", 0, "", 0, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO revisions(stream_key,migration_epoch,revision,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(stream_key,migration_epoch) DO UPDATE SET
                       revision=excluded.revision,updated_at=excluded.updated_at""",
                (stream, epoch, next_revision, now),
            )
            conn.execute(
                """INSERT INTO tombstones(object_key,migration_epoch,revision,reason_code,created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(object_key,migration_epoch) DO UPDATE SET
                       revision=excluded.revision,reason_code=excluded.reason_code,created_at=excluded.created_at""",
                (object_key, epoch, next_revision, reason, now),
            )
        return {"status": "enqueued", "source_revision": next_revision}

    def retire_streams(
        self,
        stream_keys: list[str] | tuple[str, ...],
        migration_epoch: str,
        *,
        operation_id: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Atomically close reconciled streams before destructive lifecycle work.

        门禁只认**未了结集合**（_unresolved_clause）：只要流内还存在任何尚未成功应用的
        pending/failed 事件，就抛 outbox_stream_backlog —— 包括已被重放上限放弃的事件，
        因为它们同样是**未应用 revision**，而 retirement 之后是不可逆的 purge。
        这是刻意的保守口径，与可重放集合不同；被放弃事件可用 count_abandoned / list_abandoned
        查看，用 requeue_abandoned 重新排入重放，或由管理员显式丢弃后放行。
        """
        epoch = _token(migration_epoch)
        operation = _token(operation_id)
        reason = _token(reason_code, limit=80)
        streams = sorted({_token(item) for item in stream_keys if _token(item)})
        if not epoch or not operation or not reason or not 1 <= len(streams) <= 16:
            raise OutboxError("outbox_stream_retirement_invalid")
        request_hash = hashlib.sha256(_canonical({
            "streams": streams, "reason_code": reason,
        }).encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM migration_epochs WHERE migration_epoch=?", (epoch,)
            ).fetchone() is None:
                raise StaleMigrationEpoch("migration_epoch_missing")
            prior_operation = conn.execute(
                "SELECT request_hash,result_json FROM stream_retirement_operations WHERE operation_id=? AND migration_epoch=?",
                (operation, epoch),
            ).fetchone()
            if prior_operation is not None:
                if prior_operation["request_hash"] != request_hash:
                    raise OutboxConflict("outbox_stream_retirement_conflict")
                return json.loads(prior_operation["result_json"])
            for stream in streams:
                prior_stream = conn.execute(
                    "SELECT operation_id FROM retired_streams WHERE stream_key=? AND migration_epoch=?",
                    (stream, epoch),
                ).fetchone()
                if prior_stream is not None:
                    raise OutboxConflict("outbox_stream_retired")
                unresolved_sql, unresolved_params = _unresolved_clause()
                backlog = conn.execute(
                    f"""SELECT COUNT(*) AS count FROM outbox
                       WHERE migration_epoch=? AND stream_key=? AND {unresolved_sql}""",
                    (epoch, stream, *unresolved_params),
                ).fetchone()
                if backlog is not None and int(backlog["count"]) != 0:
                    raise OutboxConflict("outbox_stream_backlog")
            revisions: dict[str, int] = {}
            for stream in streams:
                row = conn.execute(
                    "SELECT revision FROM revisions WHERE stream_key=? AND migration_epoch=?",
                    (stream, epoch),
                ).fetchone()
                revision = int(row["revision"]) if row is not None else 0
                revisions[stream] = revision
                conn.execute(
                    """INSERT INTO retired_streams(
                           stream_key,migration_epoch,operation_id,retired_revision,reason_code,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    (stream, epoch, operation, revision, reason, now),
                )
            result = {
                "code": "outbox_streams_retired",
                "stream_count": len(streams),
                "revisions": revisions,
                "reason_code": reason,
            }
            conn.execute(
                """INSERT INTO stream_retirement_operations(
                       operation_id,migration_epoch,request_hash,result_json,created_at
                   ) VALUES(?,?,?,?,?)""",
                (operation, epoch, request_hash, _canonical(result), now),
            )
            return result

    def retired_stream(self, stream_key: str, migration_epoch: str) -> dict[str, Any]:
        stream, epoch = _token(stream_key), _token(migration_epoch)
        with self._connection() as conn:
            row = conn.execute(
                """SELECT stream_key,migration_epoch,operation_id,retired_revision,reason_code,created_at
                   FROM retired_streams WHERE stream_key=? AND migration_epoch=?""",
                (stream, epoch),
            ).fetchone()
        return dict(row) if row is not None else {}

    def purge_retired_streams(
        self,
        stream_keys: list[str] | tuple[str, ...],
        migration_epoch: str,
        *,
        operation_id: str,
        reason_code: str = "person_delete",
    ) -> dict[str, Any]:
        """Physically erase payload-bearing rows for already retired streams."""
        epoch = _token(migration_epoch)
        operation = _token(operation_id)
        reason = _token(reason_code, limit=80)
        streams = sorted({_token(item) for item in stream_keys if _token(item)})
        if not epoch or not operation or not reason or not 1 <= len(streams) <= 16:
            raise OutboxError("outbox_stream_purge_invalid")
        request_hash = hashlib.sha256(_canonical({
            "streams": streams, "reason_code": reason,
        }).encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._transaction() as conn:
            prior_operation = conn.execute(
                "SELECT request_hash,result_json FROM stream_purge_operations WHERE operation_id=? AND migration_epoch=?",
                (operation, epoch),
            ).fetchone()
            if prior_operation is not None:
                if prior_operation["request_hash"] != request_hash:
                    raise OutboxConflict("outbox_stream_purge_conflict")
                return json.loads(prior_operation["result_json"])
            identity_tombstone_keys: set[str] = set()
            event_count = 0
            for stream in streams:
                retired = conn.execute(
                    "SELECT 1 FROM retired_streams WHERE stream_key=? AND migration_epoch=?",
                    (stream, epoch),
                ).fetchone()
                if retired is None:
                    raise OutboxConflict("outbox_stream_not_retired")
                purged = conn.execute(
                    "SELECT operation_id FROM purged_streams WHERE stream_key=? AND migration_epoch=?",
                    (stream, epoch),
                ).fetchone()
                if purged is not None:
                    raise OutboxConflict("outbox_stream_already_purged")
                rows = conn.execute(
                    "SELECT payload_json FROM outbox WHERE stream_key=? AND migration_epoch=?",
                    (stream, epoch),
                ).fetchall()
                event_count += len(rows)
                if stream.startswith("identity:"):
                    for row in rows:
                        try:
                            payload = json.loads(row["payload_json"])
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        identity_key = _token(payload.get("identity_key_ref"), limit=160) if isinstance(payload, dict) else ""
                        if identity_key:
                            identity_tombstone_keys.add(f"identity-link:{identity_key}")
            placeholders = ",".join("?" for _ in streams)
            conn.execute(
                f"DELETE FROM outbox WHERE migration_epoch=? AND stream_key IN ({placeholders})",
                (epoch, *streams),
            )
            conn.execute(
                f"DELETE FROM revisions WHERE migration_epoch=? AND stream_key IN ({placeholders})",
                (epoch, *streams),
            )
            tombstone_count = 0
            for key in identity_tombstone_keys:
                tombstone_count += conn.execute(
                    "DELETE FROM tombstones WHERE object_key=? AND migration_epoch=?", (key, epoch)
                ).rowcount
            for stream in streams:
                conn.execute(
                    "INSERT INTO purged_streams(stream_key,migration_epoch,operation_id,purged_at) VALUES(?,?,?,?)",
                    (stream, epoch, operation, now),
                )
            result = {
                "code": "outbox_retired_streams_purged",
                "stream_count": len(streams),
                "event_count": event_count,
                "tombstone_count": tombstone_count,
                "reason_code": reason,
            }
            conn.execute(
                """INSERT INTO stream_purge_operations(
                       operation_id,migration_epoch,request_hash,result_json,created_at
                   ) VALUES(?,?,?,?,?)""",
                (operation, epoch, request_hash, _canonical(result), now),
            )
        self._truncate_wal()
        return result

    @staticmethod
    def _item(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            event_id=row["event_id"], migration_epoch=row["migration_epoch"], source_revision=row["source_revision"],
            namespace=json.loads(row["namespace_json"]), policy_version=row["policy_version"],
            payload=json.loads(row["payload_json"]), payload_hash=row["payload_hash"], state=row["state"],
            retry_count=row["retry_count"], error_code=row["error_code"], target_revision=row["target_revision"],
            stream_key=str(row["stream_key"] or ""),
        )

    def pending(self, migration_epoch: str, *, limit: int = 100) -> list[OutboxItem]:
        """取本 epoch 可重放的事件（判据见 _replayable_clause，本模块唯一口径）。

        注意 retry_count 上限：失败事件会被反复取回重放。如果不加上限，一个始终无法应用
        的事件会一直排在队首（它的 created_at 最早），同批其余事件便永远得不到处理。
        超过上限后进入 abandoned：仍保留在表内（state='failed'、error_code 可查），不再被
        pending() 取回，可用 count_abandoned / list_abandoned 查看，用 requeue_abandoned 重排。
        """
        epoch = _token(migration_epoch)
        safe_limit = max(1, min(1000, int(limit)))
        replayable_sql, replayable_params = _replayable_clause()
        with self._connection() as conn:
            rows = conn.execute(
                f"""SELECT * FROM outbox WHERE migration_epoch=? AND {replayable_sql}
                    ORDER BY stream_key,source_revision,created_at LIMIT ?""",
                (epoch, *replayable_params, safe_limit),
            ).fetchall()
        return [self._item(row) for row in rows]

    def applied_revision(self, stream_key: str, migration_epoch: str) -> int:
        stream, epoch = _token(stream_key), _token(migration_epoch)
        with self._connection() as conn:
            row = conn.execute(
                """SELECT COALESCE(MAX(source_revision),0) AS revision FROM outbox
                   WHERE migration_epoch=? AND stream_key=? AND state='applied'""",
                (epoch, stream),
            ).fetchone()
        return int(row["revision"]) if row is not None else 0

    def backlog_for_stream(self, stream_key: str, migration_epoch: str) -> int:
        """该流仍会被重放器尝试处理的事件数（可重放集合，见 _replayable_clause）。"""
        stream, epoch = _token(stream_key), _token(migration_epoch)
        replayable_sql, replayable_params = _replayable_clause()
        with self._connection() as conn:
            row = conn.execute(
                f"""SELECT COUNT(*) AS count FROM outbox
                    WHERE migration_epoch=? AND stream_key=? AND {replayable_sql}""",
                (epoch, stream, *replayable_params),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def count_abandoned(self, migration_epoch: str) -> int:
        """被 _OUTBOX_MAX_RETRIES 永久排除出重放（abandoned）的事件数量。

        这些事件 state 仍是 'pending'/'failed' 且从未成功应用，只是 retry_count 已达/超上限，
        pending() 与 backlog_for_stream() 都不再返回它们。它们**没有**被删除，也**不代表已应用**。

        如何重新排入重放（二选一，均由管理员显式操作）：

        1) 重新排队（推荐，等价于 requeue_abandoned()，把重试计数清零后交给重放器再试）::

               UPDATE outbox
                  SET state='pending', retry_count=0, error_code='', updated_at=:now
                WHERE migration_epoch=:epoch
                  AND state IN ('pending','failed') AND retry_count >= :max_retries;
               -- 只救单条事件时追加: AND event_id=:event_id

        2) 确认不可救、显式丢弃（此后不再属于 pending/failed，retire_streams 门禁才会放行）::

               UPDATE outbox SET state='discarded', updated_at=:now
                WHERE migration_epoch=:epoch AND event_id=:event_id;

        第二种是唯一的"放弃数据"出口，必须人工确认；本模块不会自动丢弃。
        """
        epoch = _token(migration_epoch)
        abandoned_sql, abandoned_params = _abandoned_clause()
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM outbox WHERE migration_epoch=? AND {abandoned_sql}",
                (epoch, *abandoned_params),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def list_abandoned(self, migration_epoch: str, *, limit: int = 100) -> list[OutboxItem]:
        """列出被重放上限永久排除的事件明细，供人工核查与重排。

        与 count_abandoned() 同一判据（_abandoned_clause）。返回项含 event_id / stream_key /
        source_revision / retry_count / error_code，据此决定重排或显式丢弃，SQL 见
        count_abandoned() 的 docstring。
        """
        epoch = _token(migration_epoch)
        safe_limit = max(1, min(1000, int(limit)))
        abandoned_sql, abandoned_params = _abandoned_clause()
        with self._connection() as conn:
            rows = conn.execute(
                f"""SELECT * FROM outbox WHERE migration_epoch=? AND {abandoned_sql}
                    ORDER BY stream_key,source_revision,created_at LIMIT ?""",
                (epoch, *abandoned_params, safe_limit),
            ).fetchall()
        return [self._item(row) for row in rows]

    def requeue_abandoned(
        self,
        migration_epoch: str,
        *,
        event_ids: list[str] | tuple[str, ...] | None = None,
        stream_keys: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """人工重排 abandoned 事件：retry_count 归零、state 置回 'pending'。

        不传 event_ids / stream_keys 时重排本 epoch 全部 abandoned；传了则只处理其并集。
        返回 {"requeued": n, "event_ids": [...]}。重排后事件重新进入可重放集合，会被
        pending() 取走重放；这是让被上限放弃的事件"可救"的正式接口。
        """
        epoch = _token(migration_epoch)
        if not epoch:
            raise OutboxError("migration_epoch_invalid")
        targets = sorted({_token(item) for item in (event_ids or ()) if _token(item)})
        streams = sorted({_token(item) for item in (stream_keys or ()) if _token(item)})
        abandoned_sql, abandoned_params = _abandoned_clause()
        conditions = [f"migration_epoch=? AND {abandoned_sql}"]
        params: list[Any] = [epoch, *abandoned_params]
        if targets or streams:
            scopes = []
            if targets:
                scopes.append("event_id IN (" + ",".join("?" for _ in targets) + ")")
                params.extend(targets)
            if streams:
                scopes.append("stream_key IN (" + ",".join("?" for _ in streams) + ")")
                params.extend(streams)
            conditions.append("(" + " OR ".join(scopes) + ")")
        with self._transaction() as conn:
            rows = conn.execute(
                f"SELECT event_id FROM outbox WHERE {' AND '.join(conditions)} "
                "ORDER BY stream_key,source_revision,created_at",
                tuple(params),
            ).fetchall()
            ids = [str(row["event_id"]) for row in rows]
            if ids:
                conn.execute(
                    f"""UPDATE outbox SET state='pending',retry_count=0,error_code='',updated_at=?
                        WHERE migration_epoch=? AND event_id IN ({",".join("?" for _ in ids)})""",
                    (float(self._clock()), epoch, *ids),
                )
        return {"requeued": len(ids), "event_ids": ids}

    def stream_keys(self, migration_epoch: str) -> list[str]:
        epoch = _token(migration_epoch)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT stream_key FROM outbox WHERE migration_epoch=? AND stream_key<>'' ORDER BY stream_key",
                (epoch,),
            ).fetchall()
        return [str(row["stream_key"]) for row in rows]

    def latest_for_stream(self, stream_key: str, migration_epoch: str) -> OutboxItem | None:
        stream, epoch = _token(stream_key), _token(migration_epoch)
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM outbox WHERE migration_epoch=? AND stream_key=?
                   ORDER BY source_revision DESC,created_at DESC LIMIT 1""",
                (epoch, stream),
            ).fetchone()
        return self._item(row) if row is not None else None

    def mark_applied(self, event_id: str, migration_epoch: str, *, target_revision: int) -> None:
        if target_revision < 1:
            raise OutboxError("target_revision_invalid")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT state,target_revision FROM outbox WHERE event_id=? AND migration_epoch=?",
                (_token(event_id), _token(migration_epoch)),
            ).fetchone()
            if row is not None and row["state"] == "applied":
                if int(row["target_revision"]) != target_revision:
                    raise OutboxConflict("outbox_target_revision_conflict")
                return
            changed = conn.execute(
                """UPDATE outbox SET state='applied',target_revision=?,error_code='',updated_at=?
                   WHERE event_id=? AND migration_epoch=? AND state IN ('pending','failed')""",
                (target_revision, float(self._clock()), _token(event_id), _token(migration_epoch)),
            ).rowcount
            if changed != 1:
                raise OutboxError("outbox_event_missing")

    def mark_failed(self, event_id: str, migration_epoch: str, *, error_code: str) -> None:
        error = _token(error_code, limit=80) or "target_write_failed"
        with self._transaction() as conn:
            changed = conn.execute(
                """UPDATE outbox SET
                       state=CASE WHEN retry_count+1 >= ? THEN 'dead_letter' ELSE 'failed' END,
                       retry_count=retry_count+1,error_code=?,updated_at=?
                   WHERE event_id=? AND migration_epoch=? AND state IN ('pending','failed')""",
                (int(_OUTBOX_MAX_RETRIES), error, float(self._clock()), _token(event_id), _token(migration_epoch)),
            ).rowcount
            if changed != 1:
                raise OutboxError("outbox_event_missing")

    def advance_revision(self, stream_key: str, migration_epoch: str, *, expected: int, target: int) -> str:
        stream = _token(stream_key)
        epoch = _token(migration_epoch)
        if not stream or expected < 0 or target != expected + 1:
            raise OutboxError("revision_request_invalid")
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM migration_epochs WHERE migration_epoch=?", (epoch,)).fetchone() is None:
                raise StaleMigrationEpoch("migration_epoch_missing")
            row = conn.execute(
                "SELECT revision FROM revisions WHERE stream_key=? AND migration_epoch=?", (stream, epoch)
            ).fetchone()
            current = int(row["revision"]) if row is not None else 0
            if current == target:
                return "duplicate"
            if current != expected:
                raise RevisionGap(f"revision_gap:{current}:{expected}:{target}")
            conn.execute(
                """INSERT INTO revisions(stream_key,migration_epoch,revision,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(stream_key,migration_epoch) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at""",
                (stream, epoch, target, float(self._clock())),
            )
        return "advanced"

    def add_tombstone(self, object_key: str, migration_epoch: str, *, revision: int, reason_code: str) -> str:
        key = _token(object_key)
        epoch = _token(migration_epoch)
        reason = _token(reason_code, limit=80)
        if not key or revision < 1 or not reason:
            raise OutboxError("tombstone_invalid")
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM migration_epochs WHERE migration_epoch=?", (epoch,)).fetchone() is None:
                raise StaleMigrationEpoch("migration_epoch_missing")
            row = conn.execute(
                "SELECT revision,reason_code FROM tombstones WHERE object_key=? AND migration_epoch=?", (key, epoch)
            ).fetchone()
            if row is not None:
                if row["revision"] == revision and row["reason_code"] == reason:
                    return "duplicate"
                if int(row["revision"]) >= revision:
                    raise OutboxConflict("tombstone_conflict")
                conn.execute(
                    """UPDATE tombstones SET revision=?,reason_code=?,created_at=?
                       WHERE object_key=? AND migration_epoch=?""",
                    (revision, reason, float(self._clock()), key, epoch),
                )
                return "advanced"
            conn.execute(
                "INSERT INTO tombstones(object_key,migration_epoch,revision,reason_code,created_at) VALUES(?,?,?,?,?)",
                (key, epoch, revision, reason, float(self._clock())),
            )
        return "created"

    def tombstone(self, object_key: str, migration_epoch: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT object_key,migration_epoch,revision,reason_code,created_at FROM tombstones WHERE object_key=? AND migration_epoch=?",
                (_token(object_key), _token(migration_epoch)),
            ).fetchone()
        return dict(row) if row is not None else {}


__all__ = [
    "MAX_PAYLOAD_BYTES", "MIGRATION_STATES", "OUTBOX_MAX_RETRIES", "OUTBOX_STATES", "MigrationOutbox",
    "OutboxConflict", "OutboxError", "OutboxItem", "RevisionGap", "StaleMigrationEpoch",
]
