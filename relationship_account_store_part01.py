# -*- coding: utf-8 -*-
"""RelationshipAccountStorePart01Mixin。

由 tools/split_mixin_domain.py 从 relationship_account_store.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 443 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 RelationshipAccountStore）。
"""
from __future__ import annotations

try:  # package import
    from .relationship_account_store_shared import ACCOUNT_MODES, ACCOUNT_ROLES, ADMIN_ACTORS, _canonical, _integer, _token
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import ACCOUNT_MODES, ACCOUNT_ROLES, ADMIN_ACTORS, _canonical, _integer, _token
try:  # package import
    from .relationship_account_store_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import Any
try:  # package import
    from .relationship_account_store_shared import AssurancePolicy
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import AssurancePolicy
try:  # package import
    from .relationship_account_store_shared import Iterator
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import Iterator
try:  # package import
    from .relationship_account_store_shared import NamespaceContext
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import NamespaceContext
try:  # package import
    from .relationship_account_store_shared import RelationshipAccessDenied
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipAccessDenied
try:  # package import
    from .relationship_account_store_shared import RelationshipConflict
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipConflict
try:  # package import
    from .relationship_account_store_shared import RelationshipNotFound
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipNotFound
try:  # package import
    from .relationship_account_store_shared import RelationshipStoreError
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipStoreError
try:  # package import
    from .relationship_account_store_shared import contextmanager
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import contextmanager
try:  # package import
    from .relationship_account_store_shared import hashlib
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import hashlib
try:  # package import
    from .relationship_account_store_shared import json
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import json
try:  # package import
    from .relationship_account_store_shared import math
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import math
try:  # package import
    from .relationship_account_store_shared import normalize_relationship_mode
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import normalize_relationship_mode
try:  # package import
    from .relationship_account_store_shared import normalize_relationship_positive_stage_cap_key
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import normalize_relationship_positive_stage_cap_key
try:  # package import
    from .relationship_account_store_shared import relationship_stage_for_score
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import relationship_stage_for_score
try:  # package import
    from .relationship_account_store_shared import sqlite3
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import sqlite3



class RelationshipAccountStorePart01Mixin:
    """RelationshipAccountStorePart01Mixin（从 RelationshipAccountStore 拆出）。"""


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
