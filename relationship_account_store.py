"""REQ-041 revisioned Shadow relationship account store.

One account belongs to one verified identity.  Conversation namespaces are
authorization and provenance boundaries; they never become a second account.
The store is deliberately disconnected from the live message path until the
Shadow reconciliation gate is accepted.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator

try:
    from .identity_namespace import AssurancePolicy, NamespaceContext
    from .relationship_ledger import (
        apply_relationship_event,
        normalize_relationship_mode,
        normalize_relationship_positive_stage_cap_key,
    )
    from .relationship_policy import relationship_stage_for_score
    from .relationship_event_policy import validate_group_interaction_proof
except ImportError:  # pragma: no cover - direct-module test compatibility
    from identity_namespace import AssurancePolicy, NamespaceContext
    from relationship_ledger import (
        apply_relationship_event,
        normalize_relationship_mode,
        normalize_relationship_positive_stage_cap_key,
    )
    from relationship_policy import relationship_stage_for_score
    from relationship_event_policy import validate_group_interaction_proof


ACCOUNT_ROLES = frozenset({"friend", "owner"})
ACCOUNT_MODES = frozenset({"normal", "owner_exclusive"})
ADMIN_ACTORS = frozenset({"administrator", "migration"})
EVENT_ACTORS = frozenset({"private_pipeline", "group_pipeline", "administrator", "migration", "system"})
PRIVATE_EVENT_REASONS = frozenset({
    "boundary_violation",
    "care_feedback",
    "food_feedback",
    "friendly_exchange",
    "helpful_reply",
    "inbound",
    "interaction_pressure",
    "interaction_warmth",
    "intimate_interaction",
    "natural_decay",
    "playful_interaction",
    "proactive_reply",
    "relationship_violation",
    "relationship_violation_clawback",
    "relationship_violation_recovery",
    "schedule_adjustment",
    "support",
    "warmth",
})
GROUP_DIRECT_REASON = "direct_group_interaction"
GROUP_ZERO_REASONS = PRIVATE_EVENT_REASONS | frozenset({"group_inbound", GROUP_DIRECT_REASON})


class RelationshipStoreError(RuntimeError):
    pass


class RelationshipAccessDenied(RelationshipStoreError):
    pass


class RelationshipConflict(RelationshipStoreError):
    pass


class RelationshipNotFound(RelationshipStoreError):
    pass


@dataclass(frozen=True, slots=True)
class RelationshipEventResult:
    event_id: str
    identity_id: str
    code: str
    applied: bool
    requested_delta: int
    weighted_delta: int
    applied_delta: int
    score: int
    account_revision: int
    source_kind: str


@dataclass(frozen=True, slots=True)
class GroupAffinityAdmissionResult:
    event_id: str
    identity_id: str
    code: str
    requested_delta: int
    weighted_delta: int
    admitted_delta: int
    source_scope: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _token(value: Any, *, limit: int = 128) -> str:
    if not isinstance(value, str):
        return ""
    result = value.strip()
    if not result or len(result) > limit or any(ord(char) < 32 for char in result):
        return ""
    return result


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number


def _weighted_integer(delta: int, weight: float) -> int:
    value = abs(delta) * weight
    rounded = int(math.floor(value + 0.5))
    return rounded if delta >= 0 else -rounded


def _source_scope(context: NamespaceContext) -> str:
    """Return a stable redacted namespace key, independent of policy/epoch."""
    source = {"kind": context.kind, "identity_id": context.identity_id, "group_id": context.group_id}
    digest = hashlib.sha256(_canonical(source).encode("utf-8")).hexdigest()[:24]
    return f"{context.kind}:{digest}"


class RelationshipAccountStore:
    """SQLite Shadow store with atomic settlement and redacted provenance."""

    def __init__(
        self, path: str | Path, *, active_migration_epoch: str, clock: Any = None,
        observability: Any = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._active_migration_epoch = _token(active_migration_epoch)
        if not self._active_migration_epoch:
            raise RelationshipStoreError("relationship_store_epoch_required")
        self._clock = clock if callable(clock) else time.time
        self._lock = threading.RLock()
        self._observability = observability
        self._account_cache: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
        self._account_cache_limit = 2048
        self._initialize()

    def set_observability(self, observability: Any) -> None:
        self._observability = observability

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                if connection.in_transaction:
                    connection.execute("COMMIT")
                self._account_cache.clear()
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS relationship_store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relationship_accounts (
                    identity_id TEXT PRIMARY KEY,
                    relationship_role TEXT NOT NULL,
                    relationship_mode TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    positive_stage_cap_key TEXT NOT NULL,
                    daily_totals_json TEXT NOT NULL,
                    ledger_json TEXT NOT NULL,
                    last_effective_at REAL NOT NULL DEFAULT 0,
                    stage_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    legacy_snapshot INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relationship_events (
                    event_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    identity_id TEXT NOT NULL,
                    source_scope TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    requested_delta INTEGER NOT NULL,
                    weighted_delta INTEGER NOT NULL,
                    applied_delta INTEGER NOT NULL,
                    score_after INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    result_code TEXT NOT NULL,
                    account_revision INTEGER NOT NULL,
                    day_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    group_scope TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(event_id, migration_epoch),
                    FOREIGN KEY(identity_id) REFERENCES relationship_accounts(identity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_relationship_events_identity
                    ON relationship_events(identity_id, account_revision, created_at);
                CREATE INDEX IF NOT EXISTS idx_relationship_events_group_budget
                    ON relationship_events(identity_id, source_scope, day_key, reason_code);
                CREATE INDEX IF NOT EXISTS idx_relationship_events_window_budget
                    ON relationship_events(identity_id, source_scope, reason_code, created_at);
                CREATE TABLE IF NOT EXISTS relationship_group_admissions (
                    event_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    identity_id TEXT NOT NULL,
                    source_scope TEXT NOT NULL,
                    group_scope TEXT NOT NULL,
                    requested_delta INTEGER NOT NULL,
                    weighted_delta INTEGER NOT NULL,
                    admitted_delta INTEGER NOT NULL,
                    result_code TEXT NOT NULL,
                    day_key TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(event_id,migration_epoch),
                    FOREIGN KEY(identity_id) REFERENCES relationship_accounts(identity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_group_admissions_window
                    ON relationship_group_admissions(identity_id,source_scope,created_at);
                CREATE INDEX IF NOT EXISTS idx_group_admissions_person_day
                    ON relationship_group_admissions(identity_id,day_key);
                CREATE INDEX IF NOT EXISTS idx_group_admissions_scope_day
                    ON relationship_group_admissions(group_scope,day_key);
                CREATE TABLE IF NOT EXISTS relationship_account_changes (
                    operation_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    identity_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    role_before TEXT NOT NULL,
                    role_after TEXT NOT NULL,
                    mode_before TEXT NOT NULL,
                    mode_after TEXT NOT NULL,
                    score_before INTEGER NOT NULL,
                    score_after INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(operation_id, migration_epoch),
                    FOREIGN KEY(identity_id) REFERENCES relationship_accounts(identity_id)
                );
                CREATE TABLE IF NOT EXISTS relationship_account_tombstones (
                    identity_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    last_revision INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(identity_id,migration_epoch),
                    UNIQUE(operation_id,migration_epoch)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(relationship_events)").fetchall()
            }
            if "group_scope" not in columns:
                connection.execute(
                    "ALTER TABLE relationship_events ADD COLUMN group_scope TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_events_group_scope_budget "
                "ON relationship_events(group_scope,day_key,reason_code)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO relationship_store_meta(key,value) VALUES('active_migration_epoch',?)",
                (self._active_migration_epoch,),
            )
            stored = connection.execute(
                "SELECT value FROM relationship_store_meta WHERE key='active_migration_epoch'"
            ).fetchone()
            if stored is None or stored["value"] != self._active_migration_epoch:
                raise RelationshipConflict("relationship_store_epoch_mismatch")

    def _authorize(self, context: NamespaceContext | None, purpose: str) -> NamespaceContext:
        decision = AssurancePolicy.authorize(context, purpose)
        if not decision.allowed:
            raise RelationshipAccessDenied(decision.code)
        assert context is not None
        if context.migration_epoch != self._active_migration_epoch:
            raise RelationshipAccessDenied("relationship_migration_epoch_stale")
        if context.kind not in {"private", "group_member"}:
            raise RelationshipAccessDenied("relationship_namespace_denied")
        return context

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "identity_id": row["identity_id"],
            "relationship_role": row["relationship_role"],
            "relationship_mode": row["relationship_mode"],
            "relationship_score": int(row["score"]),
            "relationship_positive_stage_cap_key": row["positive_stage_cap_key"],
            "relationship_daily_totals": json.loads(row["daily_totals_json"]),
            "relationship_ledger": json.loads(row["ledger_json"]),
            "relationship_last_effective_at": float(row["last_effective_at"]),
            "relationship_stage_key": row["stage_key"],
            "revision": int(row["revision"]),
            "legacy_snapshot": bool(row["legacy_snapshot"]),
        }

    def _load_row(self, connection: sqlite3.Connection, identity_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM relationship_accounts WHERE identity_id=?", (identity_id,)
        ).fetchone()
        if row is None:
            raise RelationshipNotFound("relationship_account_missing")
        return row

    def create_account(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        actor: str,
        relationship_role: str = "friend",
        relationship_mode: str = "normal",
        score: int = 0,
        positive_stage_cap_key: str = "deeply_bonded",
        daily_totals: dict[str, Any] | None = None,
        last_effective_at: float = 0.0,
        legacy_snapshot: bool = False,
    ) -> dict[str, Any]:
        context = self._authorize(context, "relationship_write")
        operation = _token(operation_id)
        clean_actor = _token(actor, limit=40)
        role = _token(relationship_role, limit=20).lower()
        mode = _token(relationship_mode, limit=32).lower()
        numeric_score = _integer(score)
        if not operation or clean_actor not in ADMIN_ACTORS:
            raise RelationshipAccessDenied("relationship_account_admin_required")
        if role not in ACCOUNT_ROLES or mode not in ACCOUNT_MODES:
            raise RelationshipStoreError("relationship_account_mode_invalid")
        normalized_mode = normalize_relationship_mode(mode, role)
        if normalized_mode != mode or (mode == "owner_exclusive" and role != "owner"):
            raise RelationshipStoreError("relationship_account_mode_invalid")
        if numeric_score is None or not -1200 <= numeric_score <= 1200:
            raise RelationshipStoreError("relationship_account_score_invalid")
        cap_key = normalize_relationship_positive_stage_cap_key(positive_stage_cap_key)
        if daily_totals is None:
            totals, effective = {}, 0.0
        else:
            totals, effective = self._validated_legacy_runtime(daily_totals, last_effective_at)
        request = {
            "operation": "create",
            "identity_id": context.identity_id,
            "role": role,
            "mode": mode,
            "score": numeric_score,
            "cap_key": cap_key,
            "legacy_snapshot": bool(legacy_snapshot),
            "daily_totals": totals, "last_effective_at": effective,
            "actor": clean_actor,
            "policy_version": context.policy_version,
        }
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        now = float(self._clock())
        stage = relationship_stage_for_score(numeric_score)["phase"]["key"]
        with self._transaction() as connection:
            tombstone = connection.execute(
                "SELECT operation_id FROM relationship_account_tombstones WHERE identity_id=? AND migration_epoch=?",
                (context.identity_id, context.migration_epoch),
            ).fetchone()
            if tombstone is not None:
                raise RelationshipConflict("relationship_account_tombstoned")
            previous_change = connection.execute(
                "SELECT request_hash FROM relationship_account_changes WHERE operation_id=? AND migration_epoch=?",
                (operation, context.migration_epoch),
            ).fetchone()
            if previous_change is not None:
                if previous_change["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_operation_conflict")
                return self._account_from_row(self._load_row(connection, context.identity_id))
            existing = connection.execute(
                "SELECT * FROM relationship_accounts WHERE identity_id=?", (context.identity_id,)
            ).fetchone()
            if existing is not None:
                raise RelationshipConflict("relationship_account_exists")
            connection.execute(
                """INSERT INTO relationship_accounts(
                       identity_id,relationship_role,relationship_mode,score,positive_stage_cap_key,
                       daily_totals_json,ledger_json,last_effective_at,stage_key,revision,legacy_snapshot,
                       created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    context.identity_id, role, mode, numeric_score, cap_key, _canonical(totals), "[]", effective,
                    stage, 1, int(bool(legacy_snapshot)), now, now,
                ),
            )
            connection.execute(
                """INSERT INTO relationship_account_changes(
                       operation_id,migration_epoch,request_hash,identity_id,actor,role_before,role_after,
                       mode_before,mode_after,score_before,score_after,revision,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    operation, context.migration_epoch, request_hash, context.identity_id, clean_actor,
                    "", role, "", mode, numeric_score, numeric_score, 1, now,
                ),
            )
            return self._account_from_row(self._load_row(connection, context.identity_id))

    def tombstone_account(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        reason_code: str = "person_archive",
        actor: str = "administrator",
    ) -> dict[str, Any]:
        """Purge one unified relationship account and prevent resurrection."""
        context = self._authorize(context, "relationship_write")
        operation = _token(operation_id)
        reason = _token(reason_code, limit=80)
        clean_actor = _token(actor, limit=40)
        if context.kind != "private" or not operation or not reason or clean_actor != "administrator":
            raise RelationshipAccessDenied("relationship_account_archive_denied")
        request_hash = hashlib.sha256(_canonical({
            "operation": "tombstone_account",
            "identity_id": context.identity_id,
            "reason_code": reason,
            "actor": clean_actor,
            "policy_version": context.policy_version,
        }).encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._transaction() as connection:
            by_operation = connection.execute(
                "SELECT identity_id,request_hash,result_json FROM relationship_account_tombstones WHERE operation_id=? AND migration_epoch=?",
                (operation, context.migration_epoch),
            ).fetchone()
            if by_operation is not None:
                if by_operation["identity_id"] != context.identity_id or by_operation["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_operation_conflict")
                return json.loads(by_operation["result_json"])
            prior = connection.execute(
                "SELECT operation_id,request_hash,result_json FROM relationship_account_tombstones WHERE identity_id=? AND migration_epoch=?",
                (context.identity_id, context.migration_epoch),
            ).fetchone()
            if prior is not None:
                if prior["operation_id"] != operation or prior["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_account_tombstoned")
                return json.loads(prior["result_json"])
            row = connection.execute(
                "SELECT revision FROM relationship_accounts WHERE identity_id=?", (context.identity_id,)
            ).fetchone()
            last_revision = int(row["revision"]) if row is not None else 0
            event_count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM relationship_events WHERE identity_id=?",
                (context.identity_id,),
            ).fetchone()["count"])
            admission_count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM relationship_group_admissions WHERE identity_id=?",
                (context.identity_id,),
            ).fetchone()["count"])
            change_count = int(connection.execute(
                "SELECT COUNT(*) AS count FROM relationship_account_changes WHERE identity_id=?",
                (context.identity_id,),
            ).fetchone()["count"])
            connection.execute("DELETE FROM relationship_events WHERE identity_id=?", (context.identity_id,))
            connection.execute(
                "DELETE FROM relationship_group_admissions WHERE identity_id=?",
                (context.identity_id,),
            )
            connection.execute("DELETE FROM relationship_account_changes WHERE identity_id=?", (context.identity_id,))
            connection.execute("DELETE FROM relationship_accounts WHERE identity_id=?", (context.identity_id,))
            result = {
                "code": "relationship_account_tombstoned" if row is not None else "relationship_account_already_empty",
                "event_count": event_count,
                "admission_count": admission_count,
                "change_count": change_count,
                "last_revision": last_revision,
                "reason_code": reason,
            }
            connection.execute(
                """INSERT INTO relationship_account_tombstones(
                       identity_id,migration_epoch,operation_id,request_hash,reason_code,last_revision,result_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    context.identity_id, context.migration_epoch, operation, request_hash,
                    reason, last_revision, _canonical(result), now,
                ),
            )
            return result

    def configure_account(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        actor: str,
        expected_revision: int,
        relationship_role: str | None = None,
        relationship_mode: str | None = None,
        score: int | None = None,
    ) -> dict[str, Any]:
        context = self._authorize(context, "relationship_write")
        operation = _token(operation_id)
        clean_actor = _token(actor, limit=40)
        if not operation or clean_actor != "administrator":
            raise RelationshipAccessDenied("relationship_account_admin_required")
        with self._transaction() as connection:
            row = self._load_row(connection, context.identity_id)
            account = self._account_from_row(row)
            role = account["relationship_role"] if relationship_role is None else _token(relationship_role, limit=20).lower()
            mode = account["relationship_mode"] if relationship_mode is None else _token(relationship_mode, limit=32).lower()
            next_score = account["relationship_score"] if score is None else _integer(score)
            if role not in ACCOUNT_ROLES or mode not in ACCOUNT_MODES or normalize_relationship_mode(mode, role) != mode:
                raise RelationshipStoreError("relationship_account_mode_invalid")
            if next_score is None or not -1200 <= next_score <= 1200:
                raise RelationshipStoreError("relationship_account_score_invalid")
            request = {
                "operation": "configure", "identity_id": context.identity_id, "role": role, "mode": mode,
                "score": next_score, "expected_revision": expected_revision, "actor": clean_actor,
                "policy_version": context.policy_version,
            }
            request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
            previous = connection.execute(
                "SELECT request_hash FROM relationship_account_changes WHERE operation_id=? AND migration_epoch=?",
                (operation, context.migration_epoch),
            ).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_operation_conflict")
                return account
            if account["revision"] != expected_revision:
                raise RelationshipConflict("relationship_revision_conflict")
            revision = expected_revision + 1
            stage = relationship_stage_for_score(next_score, previous_stage_key=account["relationship_stage_key"])["phase"]["key"]
            now = float(self._clock())
            connection.execute(
                """UPDATE relationship_accounts SET relationship_role=?,relationship_mode=?,score=?,
                       stage_key=?,revision=?,updated_at=? WHERE identity_id=?""",
                (role, mode, next_score, stage, revision, now, context.identity_id),
            )
            connection.execute(
                """INSERT INTO relationship_account_changes(
                       operation_id,migration_epoch,request_hash,identity_id,actor,role_before,role_after,
                       mode_before,mode_after,score_before,score_after,revision,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    operation, context.migration_epoch, request_hash, context.identity_id, clean_actor,
                    account["relationship_role"], role, account["relationship_mode"], mode,
                    account["relationship_score"], next_score, revision, now,
                ),
            )
            return self._account_from_row(self._load_row(connection, context.identity_id))

    @staticmethod
    def _validated_legacy_runtime(
        daily_totals: Any,
        last_effective_at: Any,
    ) -> tuple[dict[str, Any], float]:
        if not isinstance(daily_totals, dict) or set(daily_totals) != {"day", "positive", "negative"}:
            raise RelationshipStoreError("relationship_legacy_runtime_invalid")
        day = _token(daily_totals.get("day"), limit=16) if daily_totals.get("day") else ""
        positive = _integer(daily_totals.get("positive"))
        negative = _integer(daily_totals.get("negative"))
        try:
            effective = float(last_effective_at)
        except (TypeError, ValueError, OverflowError):
            effective = -1.0
        if (
            positive is None or negative is None or not 0 <= positive <= 120
            or not -180 <= negative <= 0 or not math.isfinite(effective) or effective < 0
        ):
            raise RelationshipStoreError("relationship_legacy_runtime_invalid")
        return {"day": day, "positive": positive, "negative": negative}, effective

    def replay_legacy_event(
        self,
        context: NamespaceContext,
        *,
        event_id: str,
        reason_code: str,
        requested_delta: int,
        applied_delta: int,
        score_before: int,
        score_after: int,
        relationship_role: str,
        relationship_mode: str,
        positive_stage_cap_key: str,
        daily_totals: dict[str, Any],
        last_effective_at: float,
    ) -> RelationshipEventResult:
        """Replay one proven legacy result with strict before/after preconditions."""
        context = self._authorize(context, "relationship_write")
        event = _token(event_id)
        reason = _token(reason_code, limit=80).lower()
        requested, applied = _integer(requested_delta), _integer(applied_delta)
        before, after = _integer(score_before), _integer(score_after)
        role = _token(relationship_role, limit=20).lower()
        mode = _token(relationship_mode, limit=32).lower()
        cap = normalize_relationship_positive_stage_cap_key(positive_stage_cap_key)
        totals, effective = self._validated_legacy_runtime(daily_totals, last_effective_at)
        if (
            context.kind != "private" or not event
            or reason not in (PRIVATE_EVENT_REASONS | frozenset({GROUP_DIRECT_REASON}))
            or None in {requested, applied, before, after} or applied == 0
            or after - before != applied or not -1200 <= before <= 1200 or not -1200 <= after <= 1200
            or role not in ACCOUNT_ROLES or mode not in ACCOUNT_MODES
            or normalize_relationship_mode(mode, role) != mode
        ):
            raise RelationshipStoreError("relationship_legacy_event_invalid")
        request = {
            "operation": "legacy_event_replay", "identity_id": context.identity_id,
            "reason": reason, "requested": requested, "applied": applied,
            "before": before, "after": after, "role": role, "mode": mode,
            "cap": cap, "daily_totals": totals, "last_effective_at": effective,
            "policy_version": context.policy_version,
        }
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM relationship_events WHERE event_id=? AND migration_epoch=?",
                (event, context.migration_epoch),
            ).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_event_conflict")
                return self._event_result(previous, connection)
            account = self._account_from_row(self._load_row(connection, context.identity_id))
            if (
                account["relationship_role"] != role
                or account["relationship_mode"] != mode
                or account["relationship_score"] != before
            ):
                raise RelationshipConflict("relationship_legacy_event_precondition_failed")
            revision = account["revision"] + 1
            ledger = account.get("relationship_ledger")
            ledger = list(ledger) if isinstance(ledger, list) else []
            ledger.append({
                "event_key": event,
                "reason_code": reason,
                "delta": applied,
                "score_before": before,
                "score_after": after,
                "source": "migration_replay",
            })
            del ledger[:-200]
            stage_key = relationship_stage_for_score(
                after, previous_stage_key=account["relationship_stage_key"]
            )["phase"]["key"]
            connection.execute(
                """UPDATE relationship_accounts SET score=?,positive_stage_cap_key=?,daily_totals_json=?,
                       ledger_json=?,last_effective_at=?,stage_key=?,revision=?,updated_at=? WHERE identity_id=?""",
                (after, cap, _canonical(totals), _canonical(ledger), effective, stage_key,
                 revision, now, context.identity_id),
            )
            connection.execute(
                """INSERT INTO relationship_events(
                       event_id,migration_epoch,request_hash,identity_id,source_scope,source_kind,
                       reason_code,actor,policy_version,requested_delta,weighted_delta,applied_delta,
                       score_after,weight,result_code,account_revision,day_key,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event, context.migration_epoch, request_hash, context.identity_id, _source_scope(context),
                 context.kind, reason, "migration", context.policy_version, requested, applied, applied,
                 after, 1.0, "legacy_result_replayed", revision, str(totals.get("day") or ""), now),
            )
            inserted = connection.execute(
                "SELECT * FROM relationship_events WHERE event_id=? AND migration_epoch=?",
                (event, context.migration_epoch),
            ).fetchone()
            assert inserted is not None
            return self._event_result(inserted, connection)

    def replay_legacy_snapshot(
        self,
        context: NamespaceContext,
        *,
        operation_id: str,
        relationship_role: str,
        relationship_mode: str,
        score: int,
        positive_stage_cap_key: str,
        daily_totals: dict[str, Any],
        last_effective_at: float,
    ) -> dict[str, Any]:
        context = self._authorize(context, "relationship_write")
        operation = _token(operation_id)
        role = _token(relationship_role, limit=20).lower()
        mode = _token(relationship_mode, limit=32).lower()
        numeric_score = _integer(score)
        cap = normalize_relationship_positive_stage_cap_key(positive_stage_cap_key)
        totals, effective = self._validated_legacy_runtime(daily_totals, last_effective_at)
        if (
            context.kind != "private" or not operation or role not in ACCOUNT_ROLES
            or mode not in ACCOUNT_MODES or normalize_relationship_mode(mode, role) != mode
            or numeric_score is None or not -1200 <= numeric_score <= 1200
        ):
            raise RelationshipStoreError("relationship_legacy_snapshot_invalid")
        request = {
            "operation": "legacy_snapshot_replay", "identity_id": context.identity_id,
            "role": role, "mode": mode, "score": numeric_score, "cap": cap,
            "daily_totals": totals, "last_effective_at": effective,
            "policy_version": context.policy_version,
        }
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        now = float(self._clock())
        with self._transaction() as connection:
            previous = connection.execute(
                """SELECT request_hash FROM relationship_account_changes
                   WHERE operation_id=? AND migration_epoch=?""",
                (operation, context.migration_epoch),
            ).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_operation_conflict")
                return self._account_from_row(self._load_row(connection, context.identity_id))
            account = self._account_from_row(self._load_row(connection, context.identity_id))
            revision = account["revision"] + 1
            stage_key = relationship_stage_for_score(
                numeric_score, previous_stage_key=account["relationship_stage_key"]
            )["phase"]["key"]
            connection.execute(
                """UPDATE relationship_accounts SET relationship_role=?,relationship_mode=?,score=?,
                       positive_stage_cap_key=?,daily_totals_json=?,last_effective_at=?,stage_key=?,
                       revision=?,updated_at=? WHERE identity_id=?""",
                (role, mode, numeric_score, cap, _canonical(totals), effective, stage_key,
                 revision, now, context.identity_id),
            )
            connection.execute(
                """INSERT INTO relationship_account_changes(
                       operation_id,migration_epoch,request_hash,identity_id,actor,role_before,role_after,
                       mode_before,mode_after,score_before,score_after,revision,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (operation, context.migration_epoch, request_hash, context.identity_id, "migration",
                 account["relationship_role"], role, account["relationship_mode"], mode,
                 account["relationship_score"], numeric_score, revision, now),
            )
            return self._account_from_row(self._load_row(connection, context.identity_id))

    @staticmethod
    def _group_admission_result(row: sqlite3.Row) -> GroupAffinityAdmissionResult:
        return GroupAffinityAdmissionResult(
            event_id=str(row["event_id"]),
            identity_id=str(row["identity_id"]),
            code=str(row["result_code"]),
            requested_delta=int(row["requested_delta"]),
            weighted_delta=int(row["weighted_delta"]),
            admitted_delta=int(row["admitted_delta"]),
            source_scope=str(row["source_scope"]),
        )

    def _reserve_group_affinity_tx(
        self,
        connection: sqlite3.Connection,
        context: NamespaceContext,
        *,
        event_id: str,
        delta: int,
        weight: float,
        allow_group_affinity: bool,
        group_daily_net_cap: int,
        group_window_seconds: int,
        group_window_absolute_cap: int,
        group_person_daily_absolute_cap: int,
        group_scope_daily_absolute_cap: int,
        group_event_cap: int,
        group_interaction_proof: dict[str, Any] | None,
        now: float,
    ) -> GroupAffinityAdmissionResult:
        source_scope = _source_scope(context)
        group_scope = "group:" + hashlib.sha256(
            _canonical({"kind": context.kind, "group_id": context.group_id}).encode("utf-8")
        ).hexdigest()[:24]
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        request = {
            "identity_id": context.identity_id,
            "source_scope": source_scope,
            "delta": int(delta),
            "weight": float(weight),
            "allow_group_affinity": bool(allow_group_affinity),
            "group_daily_net_cap": int(group_daily_net_cap),
            "group_window_seconds": int(group_window_seconds),
            "group_window_absolute_cap": int(group_window_absolute_cap),
            "group_person_daily_absolute_cap": int(group_person_daily_absolute_cap),
            "group_scope_daily_absolute_cap": int(group_scope_daily_absolute_cap),
            "group_event_cap": int(group_event_cap),
            "group_proof_hash": hashlib.sha256(
                _canonical(group_interaction_proof).encode("utf-8")
            ).hexdigest() if isinstance(group_interaction_proof, dict) else "",
            "policy_version": context.policy_version,
        }
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        previous = connection.execute(
            "SELECT * FROM relationship_group_admissions WHERE event_id=? AND migration_epoch=?",
            (event_id, context.migration_epoch),
        ).fetchone()
        if previous is not None:
            if previous["request_hash"] != request_hash:
                raise RelationshipConflict("group_affinity_admission_conflict")
            return self._group_admission_result(previous)

        proof_ok, proof_code = validate_group_interaction_proof(
            group_interaction_proof, context, event_id=event_id,
        )
        weighted = 0
        admitted = 0
        code = "group_global_settlement_disabled"
        if allow_group_affinity and proof_ok:
            event_cap = max(1, min(20, int(group_event_cap)))
            bounded_delta = max(-event_cap, min(event_cap, int(delta)))
            weighted = _weighted_integer(bounded_delta, min(float(weight), 0.25))
            net_cap = max(0, min(20, int(group_daily_net_cap)))
            current_net = int(connection.execute(
                """SELECT COALESCE(SUM(admitted_delta),0) AS net
                   FROM relationship_group_admissions
                   WHERE identity_id=? AND source_scope=? AND day_key=?""",
                (context.identity_id, source_scope, day_key),
            ).fetchone()["net"])
            admitted = max(-net_cap - current_net, min(net_cap - current_net, weighted))
            window_seconds = max(60, min(86400, int(group_window_seconds)))
            window_cap = max(0, min(20, int(group_window_absolute_cap)))
            person_cap = max(0, min(120, int(group_person_daily_absolute_cap)))
            scope_cap = max(0, min(1000, int(group_scope_daily_absolute_cap)))
            window_used = int(connection.execute(
                """SELECT COALESCE(SUM(ABS(admitted_delta)),0) AS used
                   FROM relationship_group_admissions
                   WHERE identity_id=? AND source_scope=? AND created_at>=?""",
                (context.identity_id, source_scope, now - window_seconds),
            ).fetchone()["used"])
            person_used = int(connection.execute(
                """SELECT COALESCE(SUM(ABS(admitted_delta)),0) AS used
                   FROM relationship_group_admissions WHERE identity_id=? AND day_key=?""",
                (context.identity_id, day_key),
            ).fetchone()["used"])
            scope_used = int(connection.execute(
                """SELECT COALESCE(SUM(ABS(admitted_delta)),0) AS used
                   FROM relationship_group_admissions WHERE group_scope=? AND day_key=?""",
                (group_scope, day_key),
            ).fetchone()["used"])
            absolute_remaining = min(
                max(0, window_cap - window_used),
                max(0, person_cap - person_used),
                max(0, scope_cap - scope_used),
            )
            if admitted:
                admitted = (1 if admitted > 0 else -1) * min(abs(admitted), absolute_remaining)
            if admitted == 0:
                code = "group_affinity_budget_exhausted"
            elif admitted != weighted:
                code = "group_affinity_budget_clamped"
            else:
                code = "group_affinity_admitted"
        elif allow_group_affinity:
            code = proof_code
        connection.execute(
            """INSERT INTO relationship_group_admissions(
                   event_id,migration_epoch,request_hash,identity_id,source_scope,group_scope,
                   requested_delta,weighted_delta,admitted_delta,result_code,day_key,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id, context.migration_epoch, request_hash, context.identity_id,
                source_scope, group_scope, int(delta), weighted, admitted, code, day_key, now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM relationship_group_admissions WHERE event_id=? AND migration_epoch=?",
            (event_id, context.migration_epoch),
        ).fetchone()
        assert row is not None
        return self._group_admission_result(row)

    def admit_group_event(
        self,
        context: NamespaceContext,
        *,
        event_id: str,
        delta: int,
        weight: float = 0.25,
        allow_group_affinity: bool = False,
        group_daily_net_cap: int = 2,
        group_window_seconds: int = 30 * 60,
        group_window_absolute_cap: int = 1,
        group_person_daily_absolute_cap: int = 4,
        group_scope_daily_absolute_cap: int = 20,
        group_event_cap: int = 4,
        group_interaction_proof: dict[str, Any] | None = None,
    ) -> GroupAffinityAdmissionResult:
        context = self._authorize(context, "relationship_write")
        event = _token(event_id)
        numeric_delta = _integer(delta)
        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError, OverflowError):
            numeric_weight = -1.0
        if context.kind != "group_member":
            raise RelationshipAccessDenied("group_affinity_context_denied")
        if not event or numeric_delta is None or numeric_delta == 0:
            raise RelationshipStoreError("group_affinity_admission_invalid")
        if not math.isfinite(numeric_weight) or numeric_weight < 0 or numeric_weight > 1:
            raise RelationshipStoreError("relationship_event_weight_invalid")
        with self._transaction() as connection:
            self._load_row(connection, context.identity_id)
            return self._reserve_group_affinity_tx(
                connection, context, event_id=event, delta=numeric_delta,
                weight=numeric_weight, allow_group_affinity=allow_group_affinity,
                group_daily_net_cap=group_daily_net_cap,
                group_window_seconds=group_window_seconds,
                group_window_absolute_cap=group_window_absolute_cap,
                group_person_daily_absolute_cap=group_person_daily_absolute_cap,
                group_scope_daily_absolute_cap=group_scope_daily_absolute_cap,
                group_event_cap=group_event_cap,
                group_interaction_proof=group_interaction_proof,
                now=float(self._clock()),
            )

    def group_admission(
        self,
        context: NamespaceContext,
        *,
        event_id: str,
    ) -> GroupAffinityAdmissionResult | None:
        context = self._authorize(context, "relationship_read")
        event = _token(event_id)
        if not event:
            raise RelationshipStoreError("group_affinity_admission_invalid")
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM relationship_group_admissions
                   WHERE event_id=? AND migration_epoch=? AND identity_id=?""",
                (event, context.migration_epoch, context.identity_id),
            ).fetchone()
        return self._group_admission_result(row) if row is not None else None

    def apply_event(
        self,
        context: NamespaceContext,
        *,
        event_id: str,
        actor: str,
        reason_code: str,
        delta: int,
        weight: float = 1.0,
        allow_group_affinity: bool = False,
        group_daily_net_cap: int = 2,
        group_window_seconds: int = 30 * 60,
        group_window_absolute_cap: int = 1,
        group_person_daily_absolute_cap: int = 4,
        group_scope_daily_absolute_cap: int = 20,
        group_event_cap: int = 4,
        group_interaction_proof: dict[str, Any] | None = None,
        positive_daily_cap: int = 12,
        positive_event_cap: int = 4,
        negative_event_cap: int = 12,
        positive_stage_cap_key: str | None = None,
    ) -> RelationshipEventResult:
        context = self._authorize(context, "relationship_write")
        event = _token(event_id)
        clean_actor = _token(actor, limit=40)
        reason = _token(reason_code, limit=80).lower()
        numeric_delta = _integer(delta)
        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError, OverflowError):
            numeric_weight = -1.0
        if not event or clean_actor not in EVENT_ACTORS or numeric_delta is None or numeric_delta == 0:
            raise RelationshipStoreError("relationship_event_invalid")
        if not math.isfinite(numeric_weight) or numeric_weight < 0 or numeric_weight > 1:
            raise RelationshipStoreError("relationship_event_weight_invalid")
        allowed_reasons = PRIVATE_EVENT_REASONS if context.kind == "private" else GROUP_ZERO_REASONS
        if reason not in allowed_reasons:
            raise RelationshipAccessDenied("relationship_event_reason_denied")
        if context.kind == "private" and clean_actor == "group_pipeline":
            raise RelationshipAccessDenied("relationship_event_actor_scope_denied")
        if context.kind == "group_member" and clean_actor == "private_pipeline":
            raise RelationshipAccessDenied("relationship_event_actor_scope_denied")
        request = {
            "identity_id": context.identity_id,
            "source_scope": _source_scope(context),
            "source_kind": context.kind,
            "reason": reason,
            "actor": clean_actor,
            "delta": numeric_delta,
            "weight": numeric_weight,
            "allow_group_affinity": bool(allow_group_affinity),
            "group_daily_net_cap": int(group_daily_net_cap),
            "group_window_seconds": int(group_window_seconds),
            "group_window_absolute_cap": int(group_window_absolute_cap),
            "group_person_daily_absolute_cap": int(group_person_daily_absolute_cap),
            "group_scope_daily_absolute_cap": int(group_scope_daily_absolute_cap),
            "group_event_cap": int(group_event_cap),
            "group_proof_hash": hashlib.sha256(
                _canonical(group_interaction_proof).encode("utf-8")
            ).hexdigest() if isinstance(group_interaction_proof, dict) else "",
            "policy_version": context.policy_version,
        }
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        now = float(self._clock())
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        source_scope = _source_scope(context)
        group_scope = (
            "group:" + hashlib.sha256(
                _canonical({"kind": context.kind, "group_id": context.group_id}).encode("utf-8")
            ).hexdigest()[:24]
            if context.kind == "group_member" else ""
        )
        with self._transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM relationship_events WHERE event_id=? AND migration_epoch=?",
                (event, context.migration_epoch),
            ).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise RelationshipConflict("relationship_event_conflict")
                return self._event_result(previous, connection)
            row = self._load_row(connection, context.identity_id)
            account = self._account_from_row(row)
            weighted = numeric_delta
            result_code = ""
            if context.kind == "group_member":
                if not allow_group_affinity or reason != GROUP_DIRECT_REASON:
                    weighted = 0
                    result_code = "group_global_settlement_disabled"
                else:
                    admission = self._reserve_group_affinity_tx(
                        connection, context, event_id=event, delta=numeric_delta,
                        weight=numeric_weight, allow_group_affinity=True,
                        group_daily_net_cap=group_daily_net_cap,
                        group_window_seconds=group_window_seconds,
                        group_window_absolute_cap=group_window_absolute_cap,
                        group_person_daily_absolute_cap=group_person_daily_absolute_cap,
                        group_scope_daily_absolute_cap=group_scope_daily_absolute_cap,
                        group_event_cap=group_event_cap,
                        group_interaction_proof=group_interaction_proof,
                        now=now,
                    )
                    weighted = admission.admitted_delta
                    result_code = admission.code
            if weighted == 0:
                ledger_result = {
                    "changed": False,
                    "code": result_code or "weighted_delta_zero",
                    "score": account["relationship_score"],
                    "delta": 0,
                }
            else:
                ledger_result = apply_relationship_event(
                    account,
                    weighted,
                    reason_code=reason,
                    now=now,
                    event_id=f"{context.migration_epoch}:{event}",
                    positive_daily_cap=positive_daily_cap,
                    positive_event_cap=positive_event_cap,
                    negative_event_cap=negative_event_cap,
                    positive_stage_cap_key=(positive_stage_cap_key or account["relationship_positive_stage_cap_key"]),
                )
            applied_delta = int(ledger_result.get("delta") or 0)
            applied = bool(ledger_result.get("changed")) and applied_delta != 0
            revision = account["revision"] + 1 if applied else account["revision"]
            score_after = int(ledger_result.get("score", account["relationship_score"]))
            stage_key = account["relationship_stage_key"]
            if applied:
                stage_key = relationship_stage_for_score(
                    score_after, previous_stage_key=stage_key
                )["phase"]["key"]
                connection.execute(
                    """UPDATE relationship_accounts SET score=?,relationship_mode=?,daily_totals_json=?,
                           ledger_json=?,last_effective_at=?,stage_key=?,revision=?,updated_at=? WHERE identity_id=?""",
                    (
                        score_after, account["relationship_mode"],
                        _canonical(account.get("relationship_daily_totals") or {}),
                        _canonical(account.get("relationship_ledger") or []),
                        float(account.get("relationship_last_effective_at") or 0.0),
                        stage_key, revision, now, context.identity_id,
                    ),
                )
            stored_result_code = (
                "applied_group_budget_clamped"
                if applied and result_code == "group_affinity_budget_clamped"
                else str(ledger_result.get("code") or "relationship_event_rejected")
            )
            connection.execute(
                """INSERT INTO relationship_events(
                       event_id,migration_epoch,request_hash,identity_id,source_scope,source_kind,
                       reason_code,actor,policy_version,requested_delta,weighted_delta,applied_delta,
                       score_after,weight,result_code,account_revision,day_key,created_at,group_scope)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event, context.migration_epoch, request_hash, context.identity_id,
                    source_scope, context.kind, reason, clean_actor, context.policy_version,
                    numeric_delta, weighted, applied_delta, score_after, numeric_weight,
                    stored_result_code, revision, day_key, now, group_scope,
                ),
            )
            inserted = connection.execute(
                "SELECT * FROM relationship_events WHERE event_id=? AND migration_epoch=?",
                (event, context.migration_epoch),
            ).fetchone()
            assert inserted is not None
            return self._event_result(inserted, connection)

    @staticmethod
    def _event_result(row: sqlite3.Row, connection: sqlite3.Connection) -> RelationshipEventResult:
        return RelationshipEventResult(
            event_id=row["event_id"], identity_id=row["identity_id"], code=row["result_code"],
            applied=int(row["applied_delta"]) != 0, requested_delta=int(row["requested_delta"]),
            weighted_delta=int(row["weighted_delta"]), applied_delta=int(row["applied_delta"]),
            score=int(row["score_after"]), account_revision=int(row["account_revision"]), source_kind=row["source_kind"],
        )

    def account(self, context: NamespaceContext) -> dict[str, Any]:
        context = self._authorize(context, "relationship_read")
        if context.kind != "private":
            raise RelationshipAccessDenied("relationship_detail_private_only")
        return self._cached_account(context.identity_id, namespace_kind=context.kind)

    def summary(self, context: NamespaceContext) -> dict[str, Any]:
        context = self._authorize(context, "relationship_read")
        account = self._cached_account(context.identity_id, namespace_kind=context.kind)
        projection = relationship_stage_for_score(
            account["relationship_score"], previous_stage_key=account["relationship_stage_key"]
        )
        phase = projection["phase"]
        summary = {
            "schema_version": "chat.relationship_account_summary.v1",
            "identity_id_hash": hashlib.sha256(context.identity_id.encode("utf-8")).hexdigest()[:16],
            "relationship_role": account["relationship_role"],
            "relationship_mode": account["relationship_mode"],
            "stage_key": phase["key"],
            "stage_label": phase["label"],
            "proactive_care_limit": int(phase["proactive_care_limit"]),
            "revision": account["revision"],
            "read_only": True,
        }
        if context.kind == "private":
            summary["score"] = account["relationship_score"]
        return summary

    def _cached_account(self, identity_id: str, *, namespace_kind: str) -> dict[str, Any]:
        """Revision-validated cache: external writers cannot leave a stale account readable."""
        started = time.perf_counter()
        outcome = "miss"
        with self._lock:
            with self._connection() as connection:
                revision_row = connection.execute(
                    "SELECT revision FROM relationship_accounts WHERE identity_id=?",
                    (identity_id,),
                ).fetchone()
                if revision_row is None:
                    raise RelationshipNotFound("relationship_account_missing")
                revision = int(revision_row["revision"])
                cached = self._account_cache.get(identity_id)
                if cached is not None and cached[0] == revision:
                    outcome = "hit"
                    self._account_cache.move_to_end(identity_id)
                    account = deepcopy(cached[1])
                else:
                    account = self._account_from_row(self._load_row(connection, identity_id))
                    self._account_cache[identity_id] = (revision, deepcopy(account))
                    self._account_cache.move_to_end(identity_id)
                    if len(self._account_cache) > self._account_cache_limit:
                        self._account_cache.popitem(last=False)
                        if self._observability is not None:
                            self._observability.cache_event(
                                "relationship", "eviction", size=len(self._account_cache),
                            )
            size = len(self._account_cache)
        if self._observability is not None:
            self._observability.cache_event(
                "relationship", outcome, namespace_kind=namespace_kind,
                latency_ms=(time.perf_counter() - started) * 1000.0, size=size,
            )
        return account

    def audit_events(self, context: NamespaceContext, *, limit: int = 100) -> list[dict[str, Any]]:
        context = self._authorize(context, "relationship_read")
        if context.kind != "private":
            raise RelationshipAccessDenied("relationship_audit_private_only")
        count = max(1, min(500, int(limit)))
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_id,source_scope,source_kind,reason_code,actor,policy_version,
                          requested_delta,weighted_delta,applied_delta,result_code,account_revision,created_at
                   FROM relationship_events WHERE identity_id=? ORDER BY created_at DESC,event_id DESC LIMIT ?""",
                (context.identity_id, count),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = [
    "ACCOUNT_MODES", "ACCOUNT_ROLES", "GROUP_DIRECT_REASON", "PRIVATE_EVENT_REASONS",
    "GroupAffinityAdmissionResult",
    "RelationshipAccessDenied", "RelationshipAccountStore", "RelationshipConflict",
    "RelationshipEventResult", "RelationshipNotFound", "RelationshipStoreError",
]
