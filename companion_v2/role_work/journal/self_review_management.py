"""Dashboard review of model-suggested self-review guidance.

Accepting an item is the only way guidance becomes active, and an accepted item
is only ever injected while it is unexpired and its original evidence still
resolves. Rejecting is always available, including when the evidence has since
been withdrawn, so an operator is never stuck with an undecidable row.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.execution.service import outcome

from ..growth.management import RoleGrowthManagement


# A page cursor names the ``(created_at, item_id)`` of the last row of the page.
# A bare item id is the cursor older builds emitted when this list was ordered and
# paged by the hashed item id; that order cannot be resumed under the time order.
CURSOR_SEPARATOR = "|"
# 自动采纳的指导原本只活一个模型给的短期限。操作者确认某条真的有用时，可以把它
# 留成长期指导；注入前仍会重新核对原始证据，证据失效时一样停止注入。
RETAIN_WINDOW = timedelta(days=365)
RETAINED_AT_LEAST = timedelta(days=300)


def cursor_text(row) -> str:
    return row["created_at"] + CURSOR_SEPARATOR + row["item_id"]


class SelfReviewManagement:
    def __init__(self, app):
        self.app = app
        self.runtime = app.runtime
        self.evidence = app.role_self_review_evidence
        self.management = RoleGrowthManagement(app.role_growth)

    @staticmethod
    def _token(operator, context, row, policy_revision):
        return digest(["self-review-decision.v1", operator.subject, operator.binding_id,
            operator.binding_revision, context.key, row["item_id"], row["run_id"],
            row["expires_at"], policy_revision])

    def _row(self, item_id, owner, context):
        if (type(item_id) is not str or not item_id.strip() or len(item_id) > 512
                or item_id != item_id.strip()):
            raise TaskError("self_review_item_invalid", "select an original self-review item")
        row = self.runtime.read_one("""SELECT * FROM role_self_review_items
            WHERE item_id = ? AND owner_key = ? AND scope_key = ?""",
            (item_id, digest(owner), digest(context.scope)))
        if row is None:
            raise TaskError("self_review_item_unavailable", "self-review item is unavailable in this role")
        return row

    def _run(self, row, owner, context):
        run = self.runtime.read_one("""SELECT * FROM role_self_review_runs
            WHERE run_id = ? AND owner_key = ? AND scope_key = ?""",
            (row["run_id"], digest(owner), digest(context.scope)))
        if run is None:
            raise TaskError("self_review_item_unavailable", "self-review run is unavailable in this role")
        try:
            window = json.loads(run["window_json"])
            handles = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            raise TaskError("self_review_evidence_changed", "stored self-review evidence is invalid") from None
        if (type(window) is not dict or set(window) != {"opened_at", "closed_at", "handles", "kinds"}
                or type(window["handles"]) is not list
                or any(type(value) is not str for value in window["handles"])
                or len(set(window["handles"])) != len(window["handles"])
                or type(handles) is not list or not handles
                or any(type(value) is not str or value not in window["handles"] for value in handles)
                or len(set(handles)) != len(handles)):
            raise TaskError("self_review_evidence_changed", "stored self-review evidence is invalid")
        return run, window, handles

    def _basis(self, row, owner, context, at):
        """Recheck the cited handles; unrelated withdrawn evidence does not block this item."""
        run, window, handles = self._run(row, owner, context)
        try:
            opened_at = datetime.fromisoformat(window["opened_at"])
            closed_at = datetime.fromisoformat(window["closed_at"])
        except (TypeError, ValueError):
            raise TaskError("self_review_evidence_changed", "stored self-review window is invalid") from None
        if (opened_at.tzinfo is None or opened_at.utcoffset() is None
                or closed_at.tzinfo is None or closed_at.utcoffset() is None):
            raise TaskError("self_review_evidence_changed", "stored self-review window is invalid")
        if opened_at > closed_at or closed_at > at:
            raise TaskError("self_review_evidence_changed", "self-review window is not yet complete")
        try:
            descriptions = self.evidence.describe_handles(handles, opened_at, closed_at, context, at)
        except TaskError as error:
            raise TaskError("self_review_evidence_changed", error.reason) from None
        return run, handles, descriptions

    def _cursor(self, value):
        """Decode a page cursor into the ``(created_at, item_id)`` bound it names.

        A bare item id was emitted by builds that paged this bucket by the hashed
        item id in ascending order.  Resuming it under the time order cannot be
        both complete and duplicate free (rows sharing one ``created_at`` would be
        replayed or skipped), so it is rejected and the page simply re-reads from
        the first page.
        """
        if value is None:
            return None
        created_at, separator, item_id = value.partition(CURSOR_SEPARATOR)
        if not separator:
            raise TaskError("self_review_query_invalid", "invalid self-review cursor")
        try:
            named = datetime.fromisoformat(created_at)
        except ValueError:
            raise TaskError("self_review_query_invalid", "invalid self-review cursor") from None
        if named.tzinfo is None or named.utcoffset() is None or not item_id:
            raise TaskError("self_review_query_invalid", "invalid self-review cursor")
        return stamp(named), item_id

    @outcome
    def list(self, *, operator, context, state="pending", limit=20, after_id=None):
        if state not in ("pending", "active", "decided") or type(limit) is not int or not 1 <= limit <= 50 or (after_id is not None and
                (type(after_id) is not str or not after_id.strip() or len(after_id) > 512)):
            raise TaskError("self_review_query_invalid", "invalid self-review page")
        at = self.management.ledger.now()
        if state == "pending":
            state_clause, state_values = "(state = 'pending')", ()
        elif state == "active":
            # Keep accepted rows visible even after their evidence expires or is
            # withdrawn.  The dashboard needs to explain why injection stopped;
            # filtering them out here made the stopped section impossible to
            # render and made the effective count lie.
            state_clause, state_values = "(state = 'accepted')", ()
        else:
            state_clause, state_values = "(state = 'rejected' OR (state = 'accepted' AND expires_at <= ?))", (stamp(at),)
        with self.runtime.read_snapshot():
            owner, policy_revision = self.management._authorize(operator, context, at)
            owner_key, scope_key = digest(owner), digest(context.scope)
            cursor = self._cursor(after_id)
            if cursor is None:
                cursor_clause, cursor_values = "", ()
            else:
                cursor_clause = "AND (created_at < ? OR (created_at = ? AND item_id < ?))"
                cursor_values = (cursor[0], cursor[0], cursor[1])
            # Newest first: the dashboard reads this bucket one page at a time, so a
            # page must be the newest rows rather than an arbitrary hash-ordered
            # subset of it.  The item id only breaks ties inside one review run.
            rows = self.runtime.read_all(f"""SELECT * FROM role_self_review_items
                WHERE owner_key = ? AND scope_key = ? AND {state_clause}
                  {cursor_clause} ORDER BY created_at DESC, item_id DESC LIMIT ?""",
                (owner_key, scope_key, *state_values, *cursor_values, limit + 1))
            items, expired = [], 0
            for row in rows[:limit]:
                unavailable, expires_at, run_summary, evidence = None, row["expires_at"], None, []
                try:
                    run, _, evidence = self._basis(row, owner, context, at)
                    run_summary = run["summary"]
                except TaskError as error:
                    unavailable = error.code
                if expires_at is not None and expires_at <= stamp(at):
                    expired += 1
                if row["state"] != "pending":
                    actions = []
                    if (row["state"] == "accepted" and unavailable is None and expires_at is not None
                            and expires_at > stamp(at)
                            and datetime.fromisoformat(expires_at) < at + RETAINED_AT_LEAST):
                        actions = ["retain"]
                elif (unavailable is not None or expires_at is None or expires_at <= stamp(at)
                      or row["kind"] != "ask_user"):
                    actions = ["reject"]
                else:
                    actions = ["accept", "reject"]
                items.append({"item_id": row["item_id"], "run_id": row["run_id"],
                    "kind": row["kind"], "observation": row["observation"] if unavailable is None else None,
                    "next_action": row["next_action"] if unavailable is None else None,
                    "confidence": row["confidence"], "expires_at": expires_at,
                    "created_at": row["created_at"], "state": row["state"],
                    "summary": run_summary, "evidence": evidence if unavailable is None else [],
                    "automatic": row["decided_by"] == "system:self-review",
                    "unavailable_reason": unavailable, "actions": actions,
                    "view_token": self._token(operator, context, row, policy_revision)})
            reviewer = getattr(self.app, "role_self_review", None)
            policy = {
                "window_hours": getattr(reviewer, "window_hours", 24),
                "scan_seconds": getattr(reviewer, "scan_seconds", 86400),
                "empty_retry_seconds": getattr(reviewer, "empty_retry_seconds", 3600),
                "minimum_evidence": getattr(reviewer, "minimum_evidence", 1),
            }
            return {"items": items, "expired_in_page": expired, "policy": policy,
                "next_cursor": cursor_text(rows[limit - 1]) if len(rows) > limit else None,
                "context_key": context.key, "role_id": owner.persona_id}

    @outcome
    def command(self, *, operator, context, item_id, action, view_token, operation_key):
        if action not in ("accept", "reject", "retain"):
            raise TaskError("self_review_command_invalid", "select accept, reject or retain")
        self.management._validate_operation_key(operation_key)
        at = self.management.ledger.now()
        with self.runtime.transaction() as connection:
            owner, policy_revision = self.management._authorize(operator, context, at)
            row = self._row(item_id, owner, context)
            if type(view_token) is not str or view_token != self._token(operator, context, row, policy_revision):
                raise TaskError("self_review_view_stale", "refresh the self-review item before deciding")
            decision_key = digest([operator.subject, context.key, operation_key, item_id, action])
            if action == "retain":
                return self._retain(connection, operator, context, row, at, decision_key,
                                    owner=owner)
            if row["state"] != "pending":
                if row["state"] == ("accepted" if action == "accept" else "rejected") and row["decision_key"] == decision_key:
                    return {"item_id": item_id, "state": row["state"], "replayed": True}
                raise TaskError("self_review_decided", "self-review item has already been decided")
            if action == "accept":
                self._basis(row, owner, context, at)
                if row["expires_at"] <= stamp(at):
                    raise TaskError("self_review_expired", "this guidance has already expired")
                if row["kind"] != "ask_user":
                    raise TaskError("self_review_command_invalid",
                                    "only guidance that asks the user can be accepted manually")
            changed = connection.execute("""UPDATE role_self_review_items
                SET state = ?, decided_at = ?, decided_by = ?, decision_key = ?
                WHERE item_id = ? AND owner_key = ? AND scope_key = ? AND state = 'pending'""",
                ("accepted" if action == "accept" else "rejected", stamp(at),
                 operator.subject.subject_id, decision_key,
                 item_id, digest(owner), digest(context.scope)))
            if changed.rowcount != 1:
                raise TaskError("self_review_decided", "self-review item changed during decision")
            connection.execute("""UPDATE role_self_review_runs SET state = 'decided', decided_at = ?
                WHERE run_id = ? AND state = 'pending' AND NOT EXISTS (
                    SELECT 1 FROM role_self_review_items WHERE run_id = ? AND state = 'pending')""",
                (stamp(at), row["run_id"], row["run_id"]))
            self.app.notify()
            return {"item_id": item_id, "state": "accepted" if action == "accept" else "rejected",
                "replayed": False}

    def _retain(self, connection, operator, context, row, at, decision_key, *, owner):
        """Extend one already-accepted guidance to a long-term window.

        The original evidence is re-resolved first; a long retention never makes
        guidance injectable once its window is gone.
        """
        if row["state"] != "accepted":
            raise TaskError("self_review_command_invalid", "only accepted guidance can be kept long-term")
        if row["decision_key"] == decision_key:
            return {"item_id": row["item_id"], "state": "accepted", "expires_at": row["expires_at"],
                    "replayed": True}
        try:
            current = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            raise TaskError("self_review_item_unavailable", "guidance has no usable expiry") from None
        if current <= at:
            raise TaskError("self_review_expired", "this guidance has already expired")
        if current >= at + RETAINED_AT_LEAST:
            return {"item_id": row["item_id"], "state": "accepted", "expires_at": row["expires_at"],
                    "replayed": False}
        self._basis(row, owner, context, at)
        retained = stamp(at + RETAIN_WINDOW)
        changed = connection.execute("""UPDATE role_self_review_items
            SET expires_at = ?, decided_by = ?, decision_key = ?
            WHERE item_id = ? AND owner_key = ? AND scope_key = ? AND state = 'accepted'""",
            (retained, operator.subject.subject_id, decision_key,
             row["item_id"], digest(owner), digest(context.scope)))
        if changed.rowcount != 1:
            raise TaskError("self_review_decided", "self-review item changed during decision")
        return {"item_id": row["item_id"], "state": "accepted", "expires_at": retained, "replayed": False}
