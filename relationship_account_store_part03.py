# -*- coding: utf-8 -*-
"""RelationshipAccountStorePart03Mixin。

由 tools/split_mixin_domain.py 从 relationship_account_store.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 249 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 RelationshipAccountStore）。
"""
from __future__ import annotations

try:  # package import
    from .relationship_account_store_shared import (
        EVENT_ACTORS,
        GROUP_DIRECT_REASON,
        GROUP_ZERO_REASONS,
        PRIVATE_EVENT_REASONS,
        _canonical,
        _integer,
        _source_scope,
        _token,
    )
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import (
        EVENT_ACTORS,
        GROUP_DIRECT_REASON,
        GROUP_ZERO_REASONS,
        PRIVATE_EVENT_REASONS,
        _canonical,
        _integer,
        _source_scope,
        _token,
    )
try:  # package import
    from .relationship_account_store_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import Any
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
    from .relationship_account_store_shared import RelationshipNotFound
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipNotFound
try:  # package import
    from .relationship_account_store_shared import RelationshipStoreError
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipStoreError
try:  # package import
    from .relationship_account_store_shared import apply_relationship_event
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import apply_relationship_event
try:  # package import
    from .relationship_account_store_shared import deepcopy
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import deepcopy
try:  # package import
    from .relationship_account_store_shared import hashlib
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import hashlib
try:  # package import
    from .relationship_account_store_shared import math
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import math
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



class RelationshipAccountStorePart03Mixin:
    """RelationshipAccountStorePart03Mixin（从 RelationshipAccountStore 拆出）。"""


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
