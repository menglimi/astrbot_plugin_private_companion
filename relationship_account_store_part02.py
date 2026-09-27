# -*- coding: utf-8 -*-
"""RelationshipAccountStorePart02Mixin。

由 tools/split_mixin_domain.py 从 relationship_account_store.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 352 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 RelationshipAccountStore）。
"""
from __future__ import annotations

try:  # package import
    from .relationship_account_store_shared import (
        ACCOUNT_MODES,
        ACCOUNT_ROLES,
        GROUP_DIRECT_REASON,
        PRIVATE_EVENT_REASONS,
        _canonical,
        _integer,
        _source_scope,
        _token,
        _weighted_integer,
    )
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import (
        ACCOUNT_MODES,
        ACCOUNT_ROLES,
        GROUP_DIRECT_REASON,
        PRIVATE_EVENT_REASONS,
        _canonical,
        _integer,
        _source_scope,
        _token,
        _weighted_integer,
    )
try:  # package import
    from .relationship_account_store_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import Any
try:  # package import
    from .relationship_account_store_shared import GroupAffinityAdmissionResult
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import GroupAffinityAdmissionResult
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
    from .relationship_account_store_shared import RelationshipEventResult
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipEventResult
try:  # package import
    from .relationship_account_store_shared import RelationshipStoreError
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipStoreError
try:  # package import
    from .relationship_account_store_shared import hashlib
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import hashlib
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
try:  # package import
    from .relationship_account_store_shared import time
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import time
try:  # package import
    from .relationship_account_store_shared import validate_group_interaction_proof
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import validate_group_interaction_proof



class RelationshipAccountStorePart02Mixin:
    """RelationshipAccountStorePart02Mixin（从 RelationshipAccountStore 拆出）。"""


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
