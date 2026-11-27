"""Durable proposals; explicit host confirmation is separate from model answers."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import json
from typing import Mapping

from companion_v2.configuration.models import ConfigurationError, encode, fingerprint
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.identity_policy.models import _opaque

from .models import DiagnosticContext, DiagnosticError, DiagnosticQuery, RepairProposal, require, restore_target
from .service import DiagnosticService, report_document


class RepairProposalService:
    """One immutable, reviewable explicit-value patch per proposal.

    confirm/cancel belong to authenticated control-plane commands, never to the
    diagnostic model's callable tools. A confirmation names the exact review token.
    All local configuration owners participate in the same SQLite transaction.
    """

    @staticmethod
    def short_code(proposal_id: str) -> str:
        value = str(proposal_id or "").strip()
        return value[-4:] if len(value) >= 4 else value

    def __init__(self, diagnostics: DiagnosticService):
        self.diagnostics = diagnostics
        self.configuration = diagnostics.configuration
        self.runtime = diagnostics.runtime

    def _row(self, context: DiagnosticContext, proposal_id: str):
        _opaque(proposal_id, field="proposal_id")
        row = self.runtime.read_one("SELECT * FROM diagnostic_repair_proposals WHERE proposal_id = ? AND origin_key = ?",
                                    (proposal_id, context.key))
        if row is None:
            raise DiagnosticError("repair_proposal_unavailable", "original proposal is unavailable in this context")
        return row, json.loads(row["record_json"])

    def _authorize(self, context: DiagnosticContext, target, group_id, *, write: bool):
        now = self.diagnostics.current(context)
        snapshot = require(self.configuration.read(context.actor, target=target, group_id=group_id, observed_at=now))
        if write:
            definitions = self.configuration.registry.group(group_id)
            require(self.configuration.policy.scope_access(context.actor, scope=target.authorization_scope,
                        action="configuration.write", core_only=definitions[0].core_only,
                        allow_private_owner=True, observed_at=now))
        return snapshot

    def _view(self, row, data: dict, *, replayed: bool = False, current_edit_token=None, current_basis_token=None) -> RepairProposal:
        state = row["state"]
        if state == "pending" and self.diagnostics.now() >= datetime.fromisoformat(data["review"]["expires_at"]):
            state = "expired"
        if state == "pending" and ((current_edit_token is not None and current_edit_token != data["review"]["edit_token"])
                                   or (current_basis_token is not None and current_basis_token != data["basis_token"])):
            state = "stale"
        return RepairProposal(row["proposal_id"], row["revision"], state, data["review_token"],
                              encode(data["review"]), encode(data["receipt"]) if data.get("receipt") else None, replayed)

    def propose(self, context: DiagnosticContext, *, proposal_id: str, report_id: str,
                changes: Mapping[str, object], rationale: str, lifetime_seconds: int = 900) -> Outcome[RepairProposal]:
        try:
            _opaque(proposal_id, field="proposal_id")
            _opaque(report_id, field="report_id")
            if (not isinstance(changes, Mapping) or not 1 <= len(changes) <= 32
                    or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4000
                    or type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 1800):
                raise DiagnosticError("invalid_repair_proposal", "supply 1..32 fields, a reason and a lifetime of at most 30 minutes")
            detached = json.loads(encode(dict(changes)))
            request_hash = fingerprint([context.key, report_id, detached, rationale, lifetime_seconds])
            with self.runtime.transaction() as connection:
                now = self.diagnostics.current(context)
                existing = connection.execute("SELECT * FROM diagnostic_repair_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if existing is not None:
                    if existing["origin_key"] != context.key:
                        raise DiagnosticError("repair_proposal_unavailable", "proposal ID is unavailable")
                    data = json.loads(existing["record_json"])
                    snapshot = self._authorize(context, restore_target(data["target"]), data["group_id"], write=True)
                    _, basis = self.diagnostics.collect(context, DiagnosticQuery.restore(data["basis_query"]))
                    if data["request_hash"] != request_hash:
                        raise DiagnosticError("repair_proposal_conflict", "proposal ID belongs to a different immutable plan")
                    return Outcome.success(self._view(existing, data, replayed=True,
                                                       current_edit_token=snapshot.edit_token, current_basis_token=basis))
                report = require(self.diagnostics.validated(context, report_id))
                query = report.query
                if query is None or query.target is None:
                    raise DiagnosticError("repair_configuration_evidence_required", "proposal needs a current configuration check")
                if any(r.code == "local_check_budget_exceeded" for r in report.results):
                    raise DiagnosticError("repair_evidence_incomplete", "timed-out checks cannot support a repair")
                snapshot = self._authorize(context, query.target, query.group_id, write=True)
                definitions = {field.field_id: field for field in self.configuration.registry.group(query.group_id)}
                preview = require(self.configuration.preview_changes(context.actor, target=query.target, group_id=query.group_id,
                                                                     changes=detached, observed_at=now))
                normalized = dict(preview.changes)
                changed_keys = [key for key, after in preview.after.items()
                                if (after.value, after.explicit, after.source) !=
                                (snapshot.fields[key].value, snapshot.fields[key].explicit, snapshot.fields[key].source)]
                if not changed_keys:
                    raise DiagnosticError("repair_has_no_changes", "proposal does not change any explicit setting")
                delta = {key: {"before": {"value": snapshot.fields[key].value,
                                         "explicit": snapshot.fields[key].explicit,
                                         "source": snapshot.fields[key].source.document() if snapshot.fields[key].source else None,
                                         "source_revision": snapshot.fields[key].source_revision},
                               "after": {"value": preview.after[key].value, "explicit": preview.after[key].explicit},
                               "unit": "fraction_0_to_1" if definitions[key].kind.value == "probability" else definitions[key].kind.value}
                         for key in changed_keys}
                review = {"mode": "repair_proposal", "proposal_id": proposal_id, "proposal_revision": 1,
                          "target": query.target.document(), "group_id": query.group_id,
                          "original_role": asdict(context.role_binding), "schema_version": snapshot.schema_version,
                          "configuration_revision": snapshot.revision, "edit_token": snapshot.edit_token,
                          "delta": delta, "rationale": rationale, "basis": report_document(report),
                          "created_at": now.isoformat(), "expires_at": (now + timedelta(seconds=lifetime_seconds)).isoformat(),
                          "impact": {"apply_mode": snapshot.apply_mode.value, "consumer": snapshot.consumer_id,
                                     "inheriting_children_may_change": query.target.layer.value in ("installation", "role"),
                                     "effect_on_confirm": "local_configuration_saved"},
                          "rollback": {"set": {key: item["before"]["value"] for key, item in delta.items()
                                               if item["before"]["explicit"] or not preview.supports_reset},
                                       "reset_to_inherit": [key for key, item in delta.items()
                                                            if not item["before"]["explicit"] and preview.supports_reset],
                                       "exact_prior_state_restorable": preview.supports_reset or all(item["before"]["explicit"] for item in delta.values()),
                                       "requires_fresh_review": True}}
                operation_id = "repair-" + fingerprint([context.key, proposal_id])
                data = {"target": query.target.document(), "group_id": query.group_id, "request_hash": request_hash,
                        "review": review, "review_token": fingerprint(review), "changes": normalized,
                        "owner": definitions[next(iter(normalized))].owner_id, "operation_id": operation_id,
                        "basis_query": query.document(), "basis_token": report.basis_token}
                connection.execute("INSERT INTO diagnostic_repair_proposals VALUES (?, ?, ?, ?, 1, 'pending', ?)",
                                   (proposal_id, context.key, query.target.key, query.group_id, encode(data)))
                row, data = self._row(context, proposal_id)
                return Outcome.success(self._view(row, data))
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))
        except (ValueError, TypeError) as error:
            return Outcome.failed("invalid_repair_proposal", "proposal contains an invalid value")

    def read(self, context: DiagnosticContext, *, proposal_id: str) -> Outcome[RepairProposal]:
        try:
            with self.runtime.read_snapshot():
                self.diagnostics.current(context)
                row, data = self._row(context, proposal_id)
                snapshot = self._authorize(context, restore_target(data["target"]), data["group_id"], write=False)
                # The retained basis may include core-only operations or another
                # resource. Current config access alone cannot release that evidence.
                _, basis = self.diagnostics.collect(context, DiagnosticQuery.restore(data["basis_query"]))
                return Outcome.success(self._view(row, data, current_edit_token=snapshot.edit_token, current_basis_token=basis))
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))

    def list_pending(self, context: DiagnosticContext, *, limit: int = 10) -> Outcome[tuple[RepairProposal, ...]]:
        """List currently actionable pending proposals for this caller context (newest first)."""
        try:
            bounded = max(1, min(int(limit), 20))
            with self.runtime.read_snapshot():
                self.diagnostics.current(context)
                rows = self.runtime.read_all(
                    """SELECT * FROM diagnostic_repair_proposals
                       WHERE origin_key = ? AND state = 'pending'
                       ORDER BY rowid DESC LIMIT ?""",
                    (context.key, bounded * 3),
                )
                pending = []
                for row in rows:
                    try:
                        data = json.loads(row["record_json"])
                        snapshot = self._authorize(context, restore_target(data["target"]), data["group_id"], write=False)
                        _, basis = self.diagnostics.collect(context, DiagnosticQuery.restore(data["basis_query"]))
                        view = self._view(row, data, current_edit_token=snapshot.edit_token, current_basis_token=basis)
                    except (DiagnosticError, ConfigurationError, ValueError, TypeError, KeyError):
                        continue
                    if view.state == "pending":
                        pending.append(view)
                        if len(pending) >= bounded:
                            break
                return Outcome.success(tuple(pending))
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))

    def resolve_proposal_id(self, context: DiagnosticContext, identifier: str) -> Outcome[str]:
        """Resolve an exact proposal ID or an unambiguous >=4-char suffix for this context."""
        try:
            if not isinstance(identifier, str) or not identifier.strip() or len(identifier.strip()) > 512:
                raise DiagnosticError("repair_proposal_unavailable", "original proposal is unavailable in this context")
            needle = identifier.strip()
            with self.runtime.read_snapshot():
                self.diagnostics.current(context)
                exact = self.runtime.read_one(
                    "SELECT proposal_id FROM diagnostic_repair_proposals WHERE proposal_id = ? AND origin_key = ?",
                    (needle, context.key),
                )
                if exact is not None:
                    return Outcome.success(exact["proposal_id"])
                lower = needle.lower()
                if len(lower) >= 4 and all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in lower):
                    rows = self.runtime.read_all(
                        """SELECT proposal_id, state FROM diagnostic_repair_proposals
                           WHERE origin_key = ? ORDER BY rowid DESC LIMIT 64""",
                        (context.key,),
                    )
                    pending_matches = [row["proposal_id"] for row in rows
                                       if row["state"] == "pending" and row["proposal_id"].lower().endswith(lower)]
                    if len(pending_matches) == 1:
                        return Outcome.success(pending_matches[0])
                    if len(pending_matches) > 1:
                        raise DiagnosticError("repair_proposal_ambiguous",
                            "multiple pending proposals share that short suffix; provide more characters or the full ID")
                    other_matches = [row["proposal_id"] for row in rows
                                     if row["state"] != "pending" and row["proposal_id"].lower().endswith(lower)]
                    if len(other_matches) == 1:
                        return Outcome.success(other_matches[0])
                    if len(other_matches) > 1:
                        raise DiagnosticError("repair_proposal_ambiguous",
                            "multiple proposals share that short suffix; provide more characters or the full ID")
                raise DiagnosticError("repair_proposal_unavailable", "original proposal is unavailable in this context")
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))

    def confirm(self, context: DiagnosticContext, *, proposal_id: str, expected_revision: int,
                review_token: str) -> Outcome[RepairProposal]:
        return self._finish(context, proposal_id, expected_revision, review_token, confirm=True)

    def cancel(self, context: DiagnosticContext, *, proposal_id: str, expected_revision: int,
               review_token: str) -> Outcome[RepairProposal]:
        return self._finish(context, proposal_id, expected_revision, review_token, confirm=False)

    def _finish(self, context, proposal_id, expected_revision, review_token, *, confirm):
        try:
            with self.runtime.transaction() as connection:
                now = self.diagnostics.current(context)
                row, data = self._row(context, proposal_id)
                target = restore_target(data["target"])
                snapshot = self._authorize(context, target, data["group_id"], write=confirm)
                _, basis_token = self.diagnostics.collect(context, DiagnosticQuery.restore(data["basis_query"]))
                if review_token != data["review_token"] or type(expected_revision) is not int or expected_revision != 1:
                    raise DiagnosticError("repair_review_conflict", "confirmation must name the exact original review and revision")
                terminal = "confirmed" if confirm else "cancelled"
                if row["state"] == terminal:
                    # Historical receipt replay is allowed after expiry and later config
                    # edits, but never after loss of present identity or authority.
                    if confirm:
                        require(self.configuration.receipt(context.actor, target=target, operation_id=data["operation_id"], observed_at=now))
                    return Outcome.success(self._view(row, data, replayed=True))
                if row["state"] != "pending":
                    raise DiagnosticError("repair_proposal_closed", "the original proposal already has a different terminal outcome")
                if confirm:
                    if not datetime.fromisoformat(data["review"]["created_at"]) <= now < datetime.fromisoformat(data["review"]["expires_at"]):
                        raise DiagnosticError("repair_proposal_expired", "review a new proposal instead of applying an expired one")
                    if snapshot.edit_token != data["review"]["edit_token"]:
                        raise DiagnosticError("configuration_revision_conflict", "configuration, inheritance, binding or policy changed")
                    if basis_token != data["basis_token"]:
                        raise DiagnosticError("diagnostic_evidence_changed", "repair evidence changed; review the current state")
                    identifier = data["operation_id"]
                    operation = OperationRef(identifier, data["owner"], target.authorization_scope, VersionRef(identifier, 1), identifier)
                    receipt = require(self.configuration.patch(context.actor, target=target, group_id=data["group_id"],
                                      operation=operation, expected_edit_token=data["review"]["edit_token"],
                                      changes=data["changes"], observed_at=now))
                    data["receipt"] = {**asdict(receipt), "effect": "local_configuration_saved", "runtime_application": "not_attested_by_confirmation"}
                else:
                    data["receipt"] = {"proposal_id": proposal_id, "effect": "proposal_cancelled",
                                       "finished_at": now.isoformat()}
                data["finished_at"] = now.isoformat()
                updated = connection.execute("UPDATE diagnostic_repair_proposals SET state = ?, revision = revision + 1, record_json = ? "
                                             "WHERE proposal_id = ? AND origin_key = ? AND revision = 1 AND state = 'pending'",
                                             (terminal, encode(data), proposal_id, context.key))
                if updated.rowcount != 1:
                    raise DiagnosticError("repair_review_conflict", "proposal changed before final local commit")
                row, data = self._row(context, proposal_id)
                return Outcome.success(self._view(row, data))
        except (DiagnosticError, ConfigurationError) as error:
            return Outcome.failed(error.code, str(error))
