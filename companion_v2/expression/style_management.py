"""Dashboard review of model-suggested expression-style signals.

Adopting a signal is the only way it can influence later replies, and it is
always reversible: ``retire`` pauses an adopted rule while keeping its history
and ``restore`` can bring it back after the original evidence is checked
again. Rejecting is available even when the sampled writing has since been
withdrawn, so an operator is never left with an undecidable row.
"""

from __future__ import annotations

import json
from datetime import datetime

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind
from companion_v2.identity_policy.object_access import ObjectAccessPolicy

from ..conversation.scope_family import scope_family_keys
from ..role_work.growth.management import RoleGrowthManagement
from . import feedback as style_feedback
from . import style_rotation
from . import rule_details
from . import rule_support
from .configuration import read_settings
from .learning import DIMENSIONS, candidate_signature, form_identity, normalize_applicability
from .sharing import MAX_IMPORT_SIGNALS, build_pack, normalize_pack


SHARE_PACK_SOURCE = "shared_pack"


class ExpressionStyleManagement:
    def __init__(self, app):
        self.app = app
        self.runtime = app.runtime
        self.samples = app.expression_samples
        self.management = RoleGrowthManagement(app.role_growth)

    def list_samples(self, **arguments):
        from .sample_management import ExpressionSampleManagement
        return ExpressionSampleManagement(self).list(**arguments)

    def sample_command(self, **arguments):
        from .sample_management import ExpressionSampleManagement
        return ExpressionSampleManagement(self).command(**arguments)

    def learn(self, **arguments):
        from .sample_management import ExpressionSampleManagement
        return ExpressionSampleManagement(self).learn(**arguments)

    def revise(self, **arguments):
        from .rule_management import revise
        return revise(self, **arguments)

    @staticmethod
    def _token(operator, context, row, policy_revision):
        return digest(["expression-style-decision.v2", operator.subject, operator.binding_id,
            operator.binding_revision, context.key, row["signal_id"], row["run_id"],
            row["state"], row["decision_key"], row["decided_at"], policy_revision])

    def _family(self, context):
        """Current window plus the earlier windows of the same owner.

        A private family is the participant's own windows; a group family is
        every window of that group and role, including the synthetic identity
        scope the V1 group import wrote under. Both the review page and reply
        injection resolve the family here, so a rule can never be listed but
        unusable, or usable but unlisted.
        """
        return scope_family_keys(self.runtime, context)

    def _row(self, signal_id, owner, context, *, keys=None):
        if (type(signal_id) is not str or not signal_id.strip() or len(signal_id) > 512
                or signal_id != signal_id.strip()):
            raise TaskError("expression_signal_invalid", "select an original style signal")
        keys = keys if keys is not None else self._family(context)
        row = self.runtime.read_one(
            "SELECT * FROM expression_style_signals WHERE signal_id = ? AND owner_key = ? "
            "AND scope_key IN (" + ",".join("?" * len(keys)) + ")",
            (signal_id, digest(owner), *keys))
        if row is None:
            raise TaskError("expression_signal_unavailable", "style signal is unavailable in this role")
        return row

    def _run(self, row, owner, context):
        run = self.runtime.read_one("""SELECT * FROM expression_style_runs
            WHERE run_id = ? AND owner_key = ? AND scope_key = ?""",
            (row["run_id"], digest(owner), row["scope_key"]))
        if run is None:
            raise TaskError("expression_signal_unavailable", "style run is unavailable in this role")
        try:
            window = json.loads(run["sample_json"])
            handles = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            raise TaskError("expression_sample_changed", "stored style samples are invalid") from None
        required = {"opened_at", "closed_at", "handles", "characters"}
        if (type(window) is not dict or not required <= set(window)
                or set(window) - required - {"context_key", "source_kind", "legacy"}
                or ("context_key" in window and (type(window["context_key"]) is not str
                    or len(window["context_key"]) != 64))
                or window.get("source_kind", "confirmed_reply") not in
                   ("confirmed_reply", "conversation_input", SHARE_PACK_SOURCE, "legacy_expression")
                or type(window.get("handles")) is not list
                or type(window.get("characters")) is not int or window["characters"] < 0
                or type(handles) is not list
                or (window.get("source_kind") == SHARE_PACK_SOURCE and (handles or window["handles"] or window["characters"] != 0))
                or (window.get("source_kind") not in (SHARE_PACK_SOURCE, "legacy_expression") and not handles)
                or any(type(value) is not str or value not in window["handles"] for value in handles)):
            raise TaskError("expression_sample_changed", "stored style samples are invalid")
        return run, window, handles

    def _authorize(self, operator, context, at):
        if context.scope.kind is ScopeKind.PRIVATE:
            return self.management._authorize(operator, context, at)
        if context.scope.kind is not ScopeKind.GROUP:
            raise TaskError("expression_scope_required", "select a private or group conversation")
        self.samples._authorize(context, at)
        current = require(self.app.tasks.identity.resolve_role(context.scope, observed_at=at))
        if current != context.role_binding:
            raise TaskError("expression_binding_changed", "the original group role is no longer bound")
        access = require(ObjectAccessPolicy(self.runtime).scope_access(operator, scope=context.scope,
            action="configuration.read", observed_at=at, core_only=True))
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        return owner, access.policy_revision

    def _evidence_context(self, window, context):
        return self.samples.evidence_context(window, context)

    def _window(self, window, at):
        try:
            opened_at = datetime.fromisoformat(window["opened_at"])
            closed_at = datetime.fromisoformat(window["closed_at"])
        except (TypeError, ValueError):
            raise TaskError("expression_sample_changed", "stored style window is invalid") from None
        if (opened_at.tzinfo is None or opened_at.utcoffset() is None
                or closed_at.tzinfo is None or closed_at.utcoffset() is None):
            raise TaskError("expression_sample_changed", "stored style window is invalid")
        if opened_at > closed_at or closed_at > at:
            raise TaskError("expression_sample_changed", "style window is not yet complete")
        return opened_at, closed_at

    def _basis(self, row, owner, context, at):
        """Recheck only the samples this signal cites."""
        _, window, handles = self._run(row, owner, context)
        opened_at, closed_at = self._window(window, at)
        if window.get("source_kind") == SHARE_PACK_SOURCE:
            return []
        if window.get("source_kind") == "legacy_expression":
            from .legacy_import import evidence
            return evidence(window, handles)
        if window.get("source_kind") == "conversation_input" and not self.samples.reviews.allows_handles(context, handles):
            raise TaskError("expression_sample_changed", "sample approval was revoked")
        try:
            sample_context = self._evidence_context(window, context)
            return self.samples.describe_handles(handles, opened_at, closed_at, sample_context, at,
                source_kind=window.get("source_kind", "confirmed_reply"))
        except TaskError as error:
            raise TaskError("expression_sample_changed", str(error)) from None

    @outcome
    def list(self, *, operator, context, state="pending", dimension=None, limit=20, after_id=None):
        if state not in ("pending", "adopted", "rejected", "retired"):
            raise TaskError("expression_query_invalid", "unknown style signal state")
        if dimension is not None and dimension not in DIMENSIONS:
            raise TaskError("expression_query_invalid", "unknown style dimension")
        if type(limit) is not int or not 1 <= limit <= 50 or (after_id is not None and
                (type(after_id) is not str or not after_id.strip() or len(after_id) > 512)):
            raise TaskError("expression_query_invalid", "invalid style page")
        at = self.management.ledger.now()
        with self.runtime.read_snapshot():
            owner, policy_revision = self._authorize(operator, context, at)
            owner_key = digest(owner)
            keys = self._family(context)
            placeholders = ",".join("?" * len(keys))
            rows = self.runtime.read_all(f"""SELECT * FROM expression_style_signals
                WHERE owner_key = ? AND scope_key IN ({placeholders}) AND state = ?
                  AND (? IS NULL OR dimension = ?)
                  AND (? IS NULL OR signal_id > ?) ORDER BY signal_id LIMIT ?""",
                (owner_key, *keys, state, dimension, dimension, after_id, after_id, limit + 1))
            items = []
            # Adopted rules rotate by how recently they were injected, so the
            # review view reports that ledger: a rule used on every turn while
            # others wait is exactly the distribution this page has to show.
            usage_map = {}
            if state in ("adopted", "retired"):
                for scope_key in keys:
                    for signal_id, (injections, last) in style_rotation.usage(
                            self.runtime, owner_key, scope_key).items():
                        previous = usage_map.get(signal_id, (0, None))
                        latest = max((item for item in (previous[1], last) if item is not None),
                                     default=None)
                        usage_map[signal_id] = (previous[0] + injections, latest)
            feedback_map = {}
            for scope_key in keys:
                feedback_map.update(style_feedback.usage(self.runtime, owner_key, scope_key))
            capacity = {"active": 0, "limit": read_settings(self.runtime, context).active_signal_limit}
            counts = {}
            for scope_key in keys:
                active_count = self.runtime.read_one("""SELECT COUNT(*) AS count FROM expression_style_signals
                    WHERE owner_key = ? AND scope_key = ? AND state IN ('pending', 'adopted')""",
                    (owner_key, scope_key))
                capacity["active"] += int(active_count["count"]) if active_count is not None else 0
                for item in self.runtime.read_all("""SELECT state, COUNT(*) AS count
                        FROM expression_style_signals WHERE owner_key = ? AND scope_key = ? GROUP BY state""",
                        (owner_key, scope_key)):
                    counts[item["state"]] = counts.get(item["state"], 0) + item["count"]
            for row in rows[:limit]:
                unavailable, evidence = None, []
                applicability = None
                source_kind = "confirmed_reply"
                if state in ("pending", "adopted", "retired", "rejected"):
                    try:
                        applicability = normalize_applicability(json.loads(row["applicability_json"]))
                        run, window, handles = self._run(row, owner, context)
                        source_kind = window.get("source_kind", "confirmed_reply")
                        opened_at, closed_at = self._window(window, at)
                        if source_kind == "legacy_expression":
                            from .legacy_import import evidence as legacy_evidence
                            evidence = legacy_evidence(window, handles)
                        elif source_kind != SHARE_PACK_SOURCE:
                            if source_kind == "conversation_input" and not self.samples.reviews.allows_handles(context, handles):
                                raise TaskError("expression_sample_changed", "sample approval was revoked")
                            sample_context = self._evidence_context(window, context)
                            evidence = self.samples.describe_handles(handles, opened_at, closed_at,
                                sample_context, at, source_kind=source_kind)
                            if source_kind == "conversation_input":
                                for extra in rule_support.observations(self.runtime, row["signal_id"]):
                                    try:
                                        extra_window, extra_handles = extra["window"], extra["handles"]
                                        extra_start, extra_end = self._window(extra_window, at)
                                        extra_context = self._evidence_context(extra_window, context)
                                        if self.samples.reviews.allows_handles(extra_context, extra_handles):
                                            evidence.extend(self.samples.describe_handles(extra_handles,
                                                extra_start, extra_end, extra_context, at,
                                                source_kind="conversation_input"))
                                    except (TaskError, KeyError, TypeError, ValueError):
                                        continue
                    except TaskError as error:
                        unavailable = error.code
                    except (TypeError, ValueError):
                        unavailable = "expression_applicability_invalid"
                if state == "pending":
                    actions = ["reject"] if unavailable is not None else ["adopt", "reject"]
                elif state == "adopted":
                    # A rule can always be paused, even if the source has
                    # since become unavailable. The pause itself does not
                    # depend on re-reading the old evidence.
                    actions = ["retire"]
                elif state == "retired":
                    actions = [] if unavailable is not None else ["restore"]
                else:
                    actions = [] if unavailable is not None else ["reconsider"]
                injections, last = usage_map.get(row["signal_id"], (0, None))
                feedback = feedback_map.get(row["signal_id"], {})
                detail = rule_details.read(self.runtime, row["signal_id"])
                legacy = rule_details.legacy_counts(detail)
                injections += legacy["use_count"]
                items.append({"signal_id": row["signal_id"], "run_id": row["run_id"],
                    "dimension": row["dimension"], "source_kind": source_kind,
                    "applicability": applicability,
                    "expression": detail["form"] if detail and unavailable is None else None,
                    "observation": row["observation"] if unavailable is None else None,
                    "proposal": row["proposal"] if unavailable is None else None,
                    "evidence": evidence if unavailable is None else [],
                    "confidence": row["confidence"], "state": row["state"],
                    "created_at": row["created_at"], "decided_at": row["decided_at"],
                    "injections": injections,
                    "last_injected_at": last.isoformat() if last is not None else None,
                    "positive_feedback": feedback.get("positive_count", 0) + legacy.get("positive_feedback", 0),
                    "negative_feedback": feedback.get("negative_count", 0) + legacy.get("negative_feedback", 0),
                    "last_feedback": feedback.get("last_feedback"),
                    "last_feedback_at": feedback.get("last_feedback_at"),
                    "review_reason": feedback.get("review_reason"),
                    "source_title": run["summary"] if unavailable is None and source_kind in (SHARE_PACK_SOURCE, "legacy_expression") else None,
                    "unavailable_reason": unavailable, "actions": actions,
                    "can_revise": unavailable is None,
                    "view_token": self._token(operator, context, row, policy_revision)})
            return {"items": items, "state": state,
                "next_cursor": rows[limit - 1]["signal_id"] if len(rows) > limit else None,
                "context_key": context.key, "role_id": owner.persona_id, "capacity": capacity, "counts": counts}

    @outcome
    def command(self, *, operator, context, signal_id, action, view_token, operation_key):
        if action not in ("adopt", "reject", "retire", "restore", "reconsider"):
            raise TaskError("expression_command_invalid", "select adopt, reject, retire or restore")
        self.management._validate_operation_key(operation_key)
        at = self.management.ledger.now()
        with self.runtime.transaction() as connection:
            owner, policy_revision = self._authorize(operator, context, at)
            keys = self._family(context)
            row = self._row(signal_id, owner, context, keys=keys)
            decision_key = digest([operator.subject, context.key, operation_key, signal_id, action])
            if row["decision_key"] == decision_key and row["state"] == self._target(action):
                return {"signal_id": signal_id, "state": row["state"], "replayed": True}
            if type(view_token) is not str or view_token != self._token(operator, context, row, policy_revision):
                raise TaskError("expression_view_stale", "refresh the style signal before deciding")
            allowed = {
                "pending": {"adopt", "reject"},
                "adopted": {"retire"},
                "retired": {"restore"},
                "rejected": {"reconsider"},
            }
            if action not in allowed.get(row["state"], set()):
                if row["state"] == "rejected":
                    raise TaskError("expression_decided", "style signal has already been rejected")
                raise TaskError("expression_command_invalid", "this style signal cannot use that action")
            if action in ("adopt", "restore", "reconsider"):
                self._basis(row, owner, context, at)
            if action in ("restore", "reconsider"):
                # The active-rule budget belongs to the family, exactly as the
                # review page counts it: restoring a rule stored in an earlier
                # window of the same group cannot exceed the configured limit.
                placeholders = ",".join("?" * len(keys))
                capacity = self.runtime.read_one(f"""SELECT COUNT(*) AS count FROM expression_style_signals
                    WHERE owner_key = ? AND scope_key IN ({placeholders})
                      AND state IN ('pending', 'adopted')""", (digest(owner), *keys))
                if capacity["count"] >= read_settings(self.runtime, context).active_signal_limit:
                    raise TaskError("expression_capacity_reached", "retire an unused rule before restoring another")
            # Decide the stored row in the window it belongs to; a group rule
            # learned in an earlier window keeps its own scope and evidence.
            changed = connection.execute("""UPDATE expression_style_signals
                SET state = ?, decided_at = ?, decided_by = ?, decision_key = ?
                WHERE signal_id = ? AND owner_key = ? AND scope_key = ? AND state = ?""",
                (self._target(action), stamp(at), operator.subject.subject_id, decision_key,
                 signal_id, digest(owner), row["scope_key"], row["state"]))
            if changed.rowcount != 1:
                raise TaskError("expression_decided", "style signal changed during decision")
            self.app.notify()
            return {"signal_id": signal_id, "state": self._target(action), "replayed": False}

    def _share_preview(self, *, operator, context, owner, policy_revision, pack):
        owner_key, scope_key = digest(owner), digest(context.scope)
        active = self.runtime.read_one("""SELECT COUNT(*) AS count FROM expression_style_signals
            WHERE owner_key = ? AND scope_key = ? AND state IN ('pending', 'adopted')""",
            (owner_key, scope_key))
        active_count = int(active["count"]) if active is not None else 0
        limit = read_settings(self.runtime, context).active_signal_limit
        rows = self.runtime.read_all("""SELECT signal_id, dimension, observation, proposal,
                applicability_json, state FROM expression_style_signals
            WHERE owner_key = ? AND scope_key = ?""",
            (owner_key, scope_key))
        existing = {candidate_signature(row["dimension"], row["observation"], row["proposal"],
                    normalize_applicability(json.loads(row["applicability_json"])),
                    rule_details.form_for(self.runtime, row))
                    for row in rows if row["state"] in ("pending", "adopted")}
        # The same situation and pattern is the same rule here just as it is in
        # learning, so a reworded observation cannot import a second live copy.
        # Rejected and retired rules stay dormant: importing a pack again is the
        # operator's explicit recovery path once the original evidence is gone.
        known_forms = {identity for identity in
            (form_identity(rule_details.form_for(self.runtime, row)) for row in rows
             if row["state"] in ("pending", "adopted"))
            if identity is not None}
        rejected = list(pack["rejected"])
        in_pack_duplicates = sum(item["reason"] == "duplicate_in_pack" for item in rejected)
        candidates, duplicate_count, blocked_count = [], in_pack_duplicates, 0
        remaining = max(0, limit - active_count)
        for signal in pack["signals"]:
            signature = candidate_signature(signal["dimension"], signal["observation"], signal["proposal"],
                                            signal["applicability"], signal.get("expression"))
            identity = form_identity(signal.get("expression"))
            if (identity is not None and identity in known_forms) or signature in existing:
                duplicate_count += 1
                continue
            existing.add(signature)
            if identity is not None:
                known_forms.add(identity)
            if len(candidates) < remaining:
                candidates.append(signal)
            else:
                blocked_count += 1
        invalid_count = len(rejected) - in_pack_duplicates
        token = digest(["expression-share-preview.v1", operator.subject, operator.binding_id,
            operator.binding_revision, context.key, owner, policy_revision, pack, active_count,
            limit, candidates, duplicate_count, blocked_count])
        return {"title": pack["title"], "group_count": pack["group_count"],
            "rule_count": len(pack["signals"]) + in_pack_duplicates + invalid_count,
            "importable_group_count": len(candidates), "importable_rule_count": len(candidates),
            "duplicate_rule_count": duplicate_count, "capacity_blocked_count": blocked_count,
            "rejected_count": invalid_count, "rejected": rejected[:20], "candidates": candidates,
            "capacity": {"active": active_count, "limit": limit}, "preview_token": token}

    @outcome
    def export_pack(self, *, operator, context, signal_ids=None):
        if signal_ids is not None and (type(signal_ids) is not list or not signal_ids
                or len(signal_ids) > MAX_IMPORT_SIGNALS
                or any(type(value) is not str or not value.strip() or len(value) > 512 for value in signal_ids)
                or len(set(signal_ids)) != len(signal_ids)):
            raise TaskError("expression_export_invalid", "select up to 200 unique adopted rules")
        at = self.management.ledger.now()
        with self.runtime.read_snapshot():
            owner, _ = self._authorize(operator, context, at)
            # Export what the review page shows: the adopted rules of this
            # conversation's family, so a group rule learned in an earlier
            # window can still be shared.
            keys = self._family(context)
            placeholders = ",".join("?" * len(keys))
            rows = self.runtime.read_all(f"""SELECT * FROM expression_style_signals
                WHERE owner_key = ? AND scope_key IN ({placeholders})
                  AND state = 'adopted' ORDER BY signal_id""", (digest(owner), *keys))
            if signal_ids is not None:
                selected = set(signal_ids)
                rows = [row for row in rows if row["signal_id"] in selected]
            if signal_ids is not None and len(rows) != len(signal_ids):
                raise TaskError("expression_signal_unavailable", "some selected rules are not adopted in this role")
            if len(rows) > MAX_IMPORT_SIGNALS:
                raise TaskError("expression_export_limit", "select no more than 200 adopted rules to export")
            signals, excluded = [], 0
            for row in rows:
                try:
                    _, window, _ = self._run(row, owner, context)
                    self._window(window, at)
                    if window.get("source_kind") != SHARE_PACK_SOURCE:
                        self._basis(row, owner, context, at)
                except TaskError:
                    excluded += 1
                    continue
                signals.append({"dimension": row["dimension"], "observation": row["observation"],
                                "proposal": row["proposal"],
                                "expression": rule_details.form_for(self.runtime, row),
                                "applicability": normalize_applicability(json.loads(row["applicability_json"])),
                                "confidence": row["confidence"]})
        if not signals:
            raise TaskError("expression_export_empty", "there are no adopted rules with usable sources to export")
        package = build_pack(signals, title="陪伴表达分享", exported_at=stamp(at))
        return {"package": package, "rule_count": len(signals), "excluded_count": excluded}

    @outcome
    def preview_import(self, *, operator, context, raw_pack):
        try:
            pack = normalize_pack(raw_pack)
        except (TypeError, ValueError, OverflowError) as error:
            raise TaskError("expression_pack_invalid", "the expression share pack is invalid or unsupported") from error
        at = self.management.ledger.now()
        with self.runtime.read_snapshot():
            owner, policy_revision = self._authorize(operator, context, at)
            return self._share_preview(operator=operator, context=context, owner=owner,
                                       policy_revision=policy_revision, pack=pack)

    @outcome
    def import_pack(self, *, operator, context, raw_pack, preview_token, operation_key):
        self.management._validate_operation_key(operation_key)
        try:
            pack = normalize_pack(raw_pack)
        except (TypeError, ValueError, OverflowError) as error:
            raise TaskError("expression_pack_invalid", "the expression share pack is invalid or unsupported") from error
        at = self.management.ledger.now()
        material_hash = digest(["expression-share-pack.v1", pack])
        run_id = "expression-share-import:" + digest([operator.subject, context.key, operation_key])
        inserted = 0
        with self.runtime.transaction() as connection:
            owner, policy_revision = self._authorize(operator, context, at)
            existing_run = connection.execute("""SELECT material_hash FROM expression_style_runs
                WHERE run_id = ? AND owner_key = ? AND scope_key = ?""",
                (run_id, digest(owner), digest(context.scope))).fetchone()
            if existing_run is not None:
                if existing_run["material_hash"] != material_hash:
                    raise TaskError("expression_operation_conflict", "the import operation key was used for another pack")
                count = connection.execute("SELECT COUNT(*) AS count FROM expression_style_signals WHERE run_id = ?",
                                          (run_id,)).fetchone()["count"]
                return {"imported_count": int(count), "state": "pending_review", "replayed": True,
                        "capacity": {"active": int(connection.execute("""SELECT COUNT(*) AS count
                            FROM expression_style_signals WHERE owner_key = ? AND scope_key = ?
                              AND state IN ('pending', 'adopted')""",
                            (digest(owner), digest(context.scope))).fetchone()["count"]),
                            "limit": read_settings(self.runtime, context).active_signal_limit}}
            if type(preview_token) is not str or len(preview_token) != 64:
                raise TaskError("expression_import_stale", "preview this share pack again before importing")
            preview = self._share_preview(operator=operator, context=context, owner=owner,
                                          policy_revision=policy_revision, pack=pack)
            if preview["preview_token"] != preview_token:
                raise TaskError("expression_import_stale", "the target or import preview changed; preview again")
            candidates = preview["candidates"]
            if not candidates:
                return {"imported_count": 0, "state": "pending_review", "replayed": False,
                        "capacity": preview["capacity"]}
            window = {"opened_at": stamp(at), "closed_at": stamp(at), "handles": [],
                      "characters": 0, "source_kind": SHARE_PACK_SOURCE}
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state, created_at, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)""",
                (run_id, digest(owner), digest(context.scope), encode(window), material_hash,
                 pack["title"], stamp(at)))
            for ordinal, signal in enumerate(candidates):
                signal_id = "expression-share-signal:" + digest([run_id, ordinal])
                connection.execute("""INSERT INTO expression_style_signals
                    (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, applicability_json,
                     confidence, evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL, NULL, ?)""",
                    (signal_id, run_id, digest(owner), digest(context.scope), signal["dimension"],
                     signal["observation"], signal["proposal"], encode(signal["applicability"]),
                     signal["confidence"], encode([]),
                     stamp(at), ordinal))
                inserted += 1
                if signal.get("expression") is not None:
                    rule_details.write(self.runtime, signal_id, signal["expression"], at=at,
                                       actor=operator.subject.subject_id, operation_key=operation_key)
        self.app.notify()
        return {"imported_count": inserted, "state": "pending_review", "replayed": False,
                "capacity": {"active": preview["capacity"]["active"] + inserted,
                             "limit": preview["capacity"]["limit"]}}

    @staticmethod
    def _target(action):
        return {"adopt": "adopted", "reject": "rejected", "retire": "retired",
                "restore": "adopted", "reconsider": "pending"}[action]
