"""One authorized local entry for personal memo operations and reminder preparation.

The due methods are trusted internal ports. They do no model, host, or platform I/O;
an accepted claim can therefore be recovered without assuming an external attempt failed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from typing import Callable, Mapping

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef, ReferenceValidationError, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthorizationTier, ResolvedIdentity
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.models import OperationState
from companion_v2.storage.codec import scope_from_row
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime

from .models import (
    AgendaError, AgendaReceipt, AgendaTarget, DueClaim, DueCursor, DuePage, LocalPreparation,
    Memo, MemoCursor, Reminder, Schedule, encode, fingerprint, open_item_expiry, opaque, stamp,
)
from .repository import AgendaRepository


def require(result: Outcome):
    if result.failure is not None:
        raise AgendaError(result.failure.code, result.failure.reason)
    return result.unwrap()


def expect_revision(actual: int, expected: int) -> None:
    if type(expected) is not int or expected != actual:
        raise AgendaError("agenda_revision_conflict", "object changed; reload its current version")


def text_field(value: object, *, nonblank: bool = False) -> str:
    if not isinstance(value, str) or (nonblank and not value.strip()):
        raise AgendaError("invalid_agenda_text", "text must be a string and required titles/messages cannot be blank")
    return value


def check_schedule(value: object) -> None:
    if value is not None and not isinstance(value, Schedule):
        raise AgendaError("invalid_schedule", "use a validated schedule or explicitly clear it")


def check_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AgendaError("invalid_page_size", "page size must be between 1 and 100")


class AgendaService:
    OWNER = "agenda"

    def __init__(self, runtime: StorageRuntime) -> None:
        self.runtime = runtime
        self.identity = PersistentIdentityDirectory(runtime)
        self.policy = ObjectAccessPolicy(runtime)
        self.repository = AgendaRepository(runtime)
        self.operations = OperationRepository(runtime)
        self._register_existing_objects()

    @staticmethod
    def object_key(domain: str, identifier: str) -> str:
        return f"agenda.{domain}:{opaque(identifier)}"

    def _register_existing_objects(self) -> None:
        """Idempotent owner registration from domain authority, including existing v4 data.

        This startup backfill grants no sharing or tier. It never assigns old records
        to the currently calling person or the window's newly bound role.
        """
        marker = "agenda.access_objects.v1"
        if self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (marker,)) is not None:
            return
        with self.runtime.transaction() as connection:
            if connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (marker,)).fetchone() is not None:
                return
            for table, id_column, domain in (("agenda_memos", "memo_id", "memo"),
                                            ("agenda_reminders", "reminder_id", "reminder")):
                for row in self.runtime.read_all(f"SELECT {id_column}, target_json FROM {table}", ()):
                    target = AgendaTarget.restore(json.loads(row["target_json"]))
                    self._register(domain, row[id_column], target)
            for row in self.runtime.read_all(
                """SELECT a.operation_id, a.subject_id, o.installation_id, o.platform, o.scope_kind, o.native_scope_id,
                          o.bot_id, o.account_id, o.conversation_id, o.session_id
                   FROM agenda_results a JOIN operations o ON o.operation_id = a.operation_id""", (),
            ):
                self.policy.register_object(self.object_key("operation", row["operation_id"]),
                                            owner=SubjectRef(self.runtime.installation_id, SubjectKind.HUMAN, row["subject_id"]),
                                            scope=scope_from_row(row), domain="operation")
            connection.execute("INSERT INTO runtime_metadata VALUES (?, ?)", (marker, "complete"))

    def _register(self, domain: str, identifier: str, target: AgendaTarget) -> None:
        self.policy.register_object(self.object_key(domain, identifier), owner=target.owner, scope=target.scope, domain=domain)

    def _role_revision(self, target: AgendaTarget, tier: AuthorizationTier, now: datetime) -> int:
        result = self.identity.resolve_role(target.scope, observed_at=now)
        binding = result.value
        if result.failure is not None and result.failure.code != "role_binding_missing":
            require(result)
        if tier is not AuthorizationTier.CORE and (binding is None or binding.role != target.role):
            raise AgendaError("role_binding_changed", "scope is no longer bound to this role")
        return binding.revision if binding else 0

    def _authorize(
        self, actor: ResolvedIdentity, target: AgendaTarget, action: str, now: datetime, object_key: str | None = None,
    ) -> int:
        access = self.policy.authorize(actor, scope=target.scope, action=action, owner=target.owner,
                                       object_key=object_key, observed_at=now,
                                       allow_private_owner=action == "memo.read")
        if access.failure is not None and access.failure.code.startswith("object_"):
            raise AgendaError("agenda_object_not_found", "object is unavailable in the authorized view")
        decision = require(access)
        return self._role_revision(target, decision.tier, now)

    def _memo(self, memo_id: str, target: AgendaTarget, *, include_deleted: bool = False) -> Memo:
        memo = self.repository.memo(opaque(memo_id))
        if memo is None or memo.target != target or (memo.state == "deleted" and not include_deleted):
            raise AgendaError("agenda_object_not_found", "object is unavailable in the authorized view")
        return memo

    def _reminder(self, reminder_id: str, target: AgendaTarget) -> Reminder:
        reminder = self.repository.reminder(opaque(reminder_id))
        if reminder is None or reminder.target != target:
            raise AgendaError("agenda_object_not_found", "object is unavailable in the authorized view")
        return reminder

    def _command(
        self, actor: ResolvedIdentity, target: AgendaTarget, action: str, operation: OperationRef,
        expected_role_revision: int, parameters: object, now: datetime,
        mutate: Callable[[], AgendaReceipt], object_key: str | None = None,
    ) -> Outcome[AgendaReceipt]:
        try:
            with self.runtime.transaction() as connection:
                role_revision = self._authorize(actor, target, action, now, object_key)
                if operation.owner != self.OWNER or operation.scope != target.scope:
                    raise AgendaError("operation_scope_mismatch", "operation must belong to this agenda scope")
                if type(expected_role_revision) is not int or expected_role_revision != role_revision:
                    raise AgendaError("role_binding_changed", "role binding changed after this command was prepared")
                parameters_hash = fingerprint({
                    "target": target, "action": action, "parameters": parameters,
                    "actor": [actor.principal, actor.subject, actor.binding_id, actor.binding_revision],
                    "role_revision": expected_role_revision,
                })
                reservation = require(self.operations.reserve(operation, parameter_hash=parameters_hash, observed_at=now))
                if reservation.replayed:
                    row = connection.execute("SELECT result_json FROM agenda_results WHERE operation_id = ?",
                                             (reservation.operation.operation_id,)).fetchone()
                    if row is None:
                        raise AgendaError("agenda_operation_incomplete", "original operation has no local result")
                    return Outcome.success(replace(AgendaReceipt(**json.loads(row["result_json"])), replayed=True))
                result = replace(mutate(), operation_id=reservation.operation.operation_id, action=action)
                connection.execute("INSERT INTO agenda_results VALUES (?, ?, ?, ?, ?)", (
                    result.operation_id, target.key, actor.subject.subject_id, action, encode(result),
                ))
                self.policy.register_object(self.object_key("operation", result.operation_id), owner=actor.subject,
                                            scope=target.scope, domain="operation")
                require(self.operations.transition(result.operation_id, expected_state=OperationState.ACCEPTED,
                                                   next_state=OperationState.COMPLETED, observed_at=now))
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))
        except (ReferenceValidationError, TypeError) as error:
            return Outcome.failed("invalid_agenda_request", str(error))

    def create_memo(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, memo_id: str, title: str, body: str,
        operation: OperationRef, expected_role_revision: int, observed_at: datetime,
        schedule: Schedule | None = None, purpose: str = "note",
    ) -> Outcome[AgendaReceipt]:
        def mutate() -> AgendaReceipt:
            opaque(memo_id)
            text_field(title, nonblank=True)
            text_field(body)
            check_schedule(schedule)
            if purpose not in ("note", "open_loop"):
                raise AgendaError("invalid_memo_purpose", "memo purpose must be note or open_loop")
            if schedule is not None:
                self._authorize(actor, target, "memo.reminder.set", observed_at)
            if self.repository.memo(memo_id) is not None:
                raise AgendaError("agenda_id_conflict", "memo identifier is already in use")
            expiry = open_item_expiry(observed_at, schedule) if purpose == "open_loop" else None
            memo = Memo(memo_id, target, 1, "active", title, body, False, schedule, 0, 0, actor,
                        purpose=purpose, expires_at=expiry, created_at=observed_at)
            self.repository.save_memo(memo, expected_revision=0, now=observed_at)
            self._register("memo", memo_id, target)
            return AgendaReceipt("", "", memo_id, 1, "active")
        return self._command(actor, target, "memo.create", operation, expected_role_revision,
                             [memo_id, title, body, schedule, purpose], observed_at, mutate)

    def change_memo(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, memo_id: str, action: str,
        expected_revision: int, expected_role_revision: int, operation: OperationRef, observed_at: datetime,
        changes: Mapping[str, object] | None = None,
    ) -> Outcome[AgendaReceipt]:
        actions = {"edit": "memo.edit", "complete": "memo.complete", "reopen": "memo.reopen",
                   "pin": "memo.pin", "unpin": "memo.unpin", "delete": "memo.delete"}
        if action not in actions:
            return Outcome.failed("unknown_agenda_action", "memo action is unsupported")
        patch = dict(changes or {})

        def mutate() -> AgendaReceipt:
            memo = self._memo(memo_id, target)
            expect_revision(memo.revision, expected_revision)
            if memo.expired(observed_at) and action not in ("reopen", "delete"):
                raise AgendaError("agenda_memo_expired", "expired open items must be explicitly reopened before editing")
            updated = memo
            schedule_changed = "schedule" in patch and patch["schedule"] != memo.schedule
            if action != "edit" and patch:
                raise AgendaError("invalid_agenda_patch", "only edit accepts fields")
            if action == "edit":
                if set(patch).difference({"title", "body", "schedule"}):
                    raise AgendaError("invalid_agenda_patch", "unknown memo field")
                if "title" in patch:
                    text_field(patch["title"], nonblank=True)
                if "body" in patch:
                    text_field(patch["body"])
                if "schedule" in patch:
                    check_schedule(patch["schedule"])
                if schedule_changed:
                    self._authorize(actor, target, "memo.reminder.set", observed_at, self.object_key("memo", memo_id))
                    if memo.state != "active":
                        raise AgendaError("agenda_state_conflict", "reopen the memo before changing its schedule")
                    updated = replace(updated, sequence=0)
                updated = replace(updated, **patch)
                if memo.purpose == "open_loop" and schedule_changed:
                    created_at = memo.created_at or observed_at
                    updated = replace(updated, expires_at=open_item_expiry(created_at, updated.schedule, updated.sequence))
            elif action == "complete":
                if memo.state != "active":
                    raise AgendaError("agenda_state_conflict", "memo is not active")
                next_due = memo.schedule.at(memo.sequence + 1) if memo.schedule else None
                updated = replace(memo, state="active" if next_due is not None else "completed",
                                  sequence=memo.sequence + 1 if next_due is not None else memo.sequence,
                                  completion_count=memo.completion_count + 1)
            elif action == "reopen":
                if memo.state == "completed":
                    updated = replace(memo, state="active",
                        expires_at=open_item_expiry(observed_at, memo.schedule, memo.sequence)
                        if memo.purpose == "open_loop" else memo.expires_at)
                elif memo.state == "active" and memo.expired(observed_at):
                    updated = replace(memo, expires_at=open_item_expiry(observed_at, memo.schedule, memo.sequence))
                else:
                    raise AgendaError("agenda_state_conflict", "only a completed or expired open item can reopen")
            elif action in ("pin", "unpin"):
                updated = replace(memo, pinned=action == "pin")
            elif action == "delete":
                # The caller supplies an already expressed intent for this ID/version.
                updated = replace(memo, state="deleted", title="", body="", schedule=None, pinned=False)
            if updated != memo:
                updated = replace(updated, revision=memo.revision + 1,
                                  content_revision=memo.content_revision + (0 if action in ("pin", "unpin") else 1))
                self.repository.save_memo(updated, expected_revision=memo.revision, now=observed_at)
                if action == "edit" and not schedule_changed:
                    self.repository.invalidate_memo_content(memo_id, now=observed_at)
                elif action not in ("pin", "unpin"):
                    self.repository.sync_memo(updated, now=observed_at)
                if action == "delete":
                    self.repository.erase_memo_preparations(memo_id)
            linked = self.repository.linked_reminder(memo_id)
            scheduling = "not_requested" if linked is None else ("local_registered" if linked.state == "active" else "local_cancelled")
            return AgendaReceipt("", "", memo_id, updated.revision, updated.state, scheduling=scheduling)
        return self._command(actor, target, actions[action], operation, expected_role_revision,
                             [memo_id, expected_revision, patch], observed_at, mutate, self.object_key("memo", memo_id))

    def create_reminder(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, reminder_id: str,
        operation: OperationRef, expected_role_revision: int, observed_at: datetime,
        schedule: Schedule | None = None, message: str = "", memo_id: str | None = None,
        expected_memo_revision: int | None = None,
    ) -> Outcome[AgendaReceipt]:
        def mutate() -> AgendaReceipt:
            opaque(reminder_id)
            check_schedule(schedule)
            text_field(message, nonblank=memo_id is None)
            self._authorize(actor, target, "reminder.deliver", observed_at)
            selected_schedule = schedule
            sequence = 0
            if memo_id is not None:
                self._authorize(actor, target, "memo.reminder.set", observed_at, self.object_key("memo", memo_id))
                memo = self._memo(memo_id, target)
                expect_revision(memo.revision, expected_memo_revision)
                if memo.state != "active" or memo.schedule is None or schedule is not None or message != "":
                    raise AgendaError("invalid_schedule", "linked reminder must use the active memo's existing schedule")
                if self.repository.linked_reminder(memo_id) is not None:
                    raise AgendaError("reminder_already_linked", "use the existing reminder to change or re-enable its schedule")
                selected_schedule, sequence = memo.schedule, memo.sequence
            if selected_schedule is None:
                raise AgendaError("invalid_schedule", "reminder requires an explicit due time")
            if self.repository.reminder(reminder_id) is not None:
                raise AgendaError("agenda_id_conflict", "reminder identifier is already in use")
            reminder = Reminder(reminder_id, target, 1, "active", memo_id, message, selected_schedule,
                                actor, expected_role_revision, "reminder.create", sequence)
            self.repository.save_reminder(reminder, expected_revision=0, now=observed_at)
            self._register("reminder", reminder_id, target)
            self.repository.schedule(reminder, sequence=sequence, now=observed_at)
            return AgendaReceipt("", "", reminder_id, 1, "active", scheduling="local_registered")
        return self._command(actor, target, "reminder.create", operation, expected_role_revision,
                             [reminder_id, schedule, message, memo_id, expected_memo_revision], observed_at, mutate)

    def change_reminder(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, reminder_id: str, action: str,
        expected_revision: int, expected_role_revision: int, operation: OperationRef,
        observed_at: datetime, schedule: Schedule | None = None, expected_memo_revision: int | None = None,
    ) -> Outcome[AgendaReceipt]:
        if action not in ("cancel", "reschedule"):
            return Outcome.failed("unknown_agenda_action", "reminder action is unsupported")
        def mutate() -> AgendaReceipt:
            reminder = self._reminder(reminder_id, target)
            expect_revision(reminder.revision, expected_revision)
            check_schedule(schedule)
            if action == "cancel":
                if schedule is not None or expected_memo_revision is not None:
                    raise AgendaError("invalid_agenda_patch", "cancellation does not edit time or memo")
                updated = replace(reminder, state="cancelled")
            else:
                if schedule is None:
                    raise AgendaError("invalid_schedule", "reschedule requires an explicit schedule")
                self._authorize(actor, target, "reminder.deliver", observed_at, self.object_key("reminder", reminder_id))
                if reminder.memo_id is not None:
                    self._authorize(actor, target, "memo.reminder.set", observed_at, self.object_key("memo", reminder.memo_id))
                    memo = self._memo(reminder.memo_id, target)
                    expect_revision(memo.revision, expected_memo_revision)
                    if memo.state != "active":
                        raise AgendaError("agenda_state_conflict", "linked memo is not active")
                    self.repository.save_memo(replace(memo, revision=memo.revision + 1, content_revision=memo.content_revision + 1,
                                                      schedule=schedule, sequence=0),
                                              expected_revision=memo.revision, now=observed_at)
                updated = replace(reminder, state="active", schedule=schedule, sequence=0, requester=actor,
                                  role_binding_revision=expected_role_revision, authorization_action="reminder.reschedule")
            if updated != reminder or action == "reschedule":
                updated = replace(updated, revision=reminder.revision + 1)
                self.repository.replace_schedule(updated, previous_revision=reminder.revision, now=observed_at)
            return AgendaReceipt("", "", reminder_id, updated.revision, updated.state,
                                 scheduling="local_cancelled" if action == "cancel" else "local_registered")
        return self._command(actor, target, f"reminder.{action}", operation, expected_role_revision,
                             [reminder_id, expected_revision, schedule, expected_memo_revision], observed_at, mutate,
                             self.object_key("reminder", reminder_id))

    def read_memo(self, actor: ResolvedIdentity, *, target: AgendaTarget, memo_id: str, observed_at: datetime,
                  include_expired: bool = False) -> Outcome[Memo]:
        try:
            if type(include_expired) is not bool:
                raise AgendaError("invalid_agenda_filter", "include_expired must be a boolean")
            with self.runtime.read_snapshot():
                self._authorize(actor, target, "memo.read", observed_at, self.object_key("memo", memo_id))
                memo = self._memo(memo_id, target)
                if memo.expired(observed_at) and not include_expired:
                    raise AgendaError("agenda_object_not_found", "open item is unavailable")
            return Outcome.success(memo)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def list_memos(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, observed_at: datetime,
        limit: int = 20, after: MemoCursor | None = None, search: str = "", state: str = "all",
        purpose: str | None = None, include_expired: bool = False,
    ) -> Outcome[tuple[Memo, ...]]:
        try:
            check_limit(limit)
            text_field(search)
            if type(include_expired) is not bool:
                raise AgendaError("invalid_agenda_filter", "include_expired must be a boolean")
            if after is not None and not isinstance(after, MemoCursor):
                raise AgendaError("invalid_memo_cursor", "use the last row's pinned state and stable ID")
            cursor = (int(after.pinned), after.memo_id) if after else (2, "")
            if state not in ("active", "completed", "all"):
                raise AgendaError("invalid_agenda_filter", "unsupported memo state")
            if purpose not in (None, "note", "open_loop"):
                raise AgendaError("invalid_memo_purpose", "unsupported memo purpose")
            with self.runtime.read_snapshot():
                decision = require(self.policy.feature_access(actor, scope=target.scope, action="memo.list",
                    observed_at=observed_at, allow_private_owner=True))
                self._role_revision(target, decision.tier, observed_at)
                visible = require(self.policy.permitted_object_keys(actor, scope=target.scope, action="memo.read",
                    owner=target.owner, observed_at=observed_at, allow_private_owner=True))
                ids = tuple(key[len("agenda.memo:"):] for key in visible if key.startswith("agenda.memo:"))
                rows = []
                result = []
                scan_cursor = cursor
                while len(result) < limit:
                    rows = self.runtime.read_all(
                    """SELECT memo_id, pinned FROM agenda_memos WHERE target_key = ? AND state != 'deleted'
                       AND memo_id IN (SELECT value FROM json_each(?))
                       AND (pinned < ? OR (pinned = ? AND memo_id > ?))
                       AND (? IS NULL OR COALESCE(json_extract(record_json, '$.purpose'), 'note') = ?)
                       AND (? = 'all' OR state = ?) AND (instr(json_extract(record_json, '$.title'), ?) > 0
                       OR instr(json_extract(record_json, '$.body'), ?) > 0) ORDER BY pinned DESC, memo_id LIMIT ?""",
                    (target.key, encode(ids), scan_cursor[0], *scan_cursor, purpose, purpose,
                     state, state, search, search, limit),
                    )
                    if not rows:
                        break
                    exhausted = len(rows) < limit
                    for row in rows:
                        memo = self.repository.memo(row["memo_id"])
                        scan_cursor = (int(row["pinned"]), row["memo_id"])
                        if memo is None or (not include_expired and memo.expired(observed_at)):
                            continue
                        result.append(memo)
                        if len(result) >= limit:
                            break
                    if len(result) >= limit or exhausted:
                        break
            return Outcome.success(tuple(result))
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def read_reminder(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, reminder_id: str, observed_at: datetime,
    ) -> Outcome[Reminder]:
        try:
            with self.runtime.read_snapshot():
                self._authorize(actor, target, "reminder.read", observed_at, self.object_key("reminder", reminder_id))
                result = self._reminder(reminder_id, target)
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def list_reminders(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, observed_at: datetime,
        limit: int = 20, after_id: str = "",
    ) -> Outcome[tuple[Reminder, ...]]:
        try:
            check_limit(limit)
            text_field(after_id)
            with self.runtime.read_snapshot():
                decision = require(self.policy.feature_access(actor, scope=target.scope, action="reminder.list", observed_at=observed_at))
                self._role_revision(target, decision.tier, observed_at)
                visible = require(self.policy.permitted_object_keys(actor, scope=target.scope, action="reminder.read",
                                                                    owner=target.owner, observed_at=observed_at))
                ids = tuple(key[len("agenda.reminder:"):] for key in visible if key.startswith("agenda.reminder:"))
                rows = self.runtime.read_all(
                    """SELECT reminder_id FROM agenda_reminders WHERE target_key = ? AND reminder_id > ?
                       AND reminder_id IN (SELECT value FROM json_each(?)) ORDER BY reminder_id LIMIT ?""",
                    (target.key, after_id, encode(ids), limit),
                )
                result = tuple(self.repository.reminder(row["reminder_id"]) for row in rows)
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def receipt(
        self, actor: ResolvedIdentity, *, target: AgendaTarget, operation_id: str, observed_at: datetime,
    ) -> Outcome[AgendaReceipt]:
        try:
            with self.runtime.read_snapshot():
                row = self.runtime.read_one(
                    "SELECT result_json, subject_id FROM agenda_results WHERE operation_id = ? AND target_key = ?",
                    (opaque(operation_id), target.key),
                )
                if row is None:
                    raise AgendaError("agenda_object_not_found", "original result is unavailable in this view")
                requester = SubjectRef(self.runtime.installation_id, SubjectKind.HUMAN, row["subject_id"])
                access = self.policy.authorize(actor, scope=target.scope, action="operation.lookup", owner=requester,
                                               object_key=self.object_key("operation", operation_id), observed_at=observed_at)
                if access.failure is not None and access.failure.code.startswith("object_"):
                    raise AgendaError("agenda_object_not_found", "original result is unavailable in this view")
                decision = require(access)
                self._role_revision(target, decision.tier, observed_at)
                result = replace(AgendaReceipt(**json.loads(row["result_json"])), replayed=True)
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def due(
        self, *, observed_at: datetime, limit: int = 20, after: DueCursor | None = None,
    ) -> DuePage:
        """Trusted scheduler scan: bounded IDs only; execution rechecks every item."""
        check_limit(limit)
        cursor = (stamp(after.due_at), opaque(after.occurrence_id)) if after else ("", "")
        rows = self.runtime.read_all(
            """SELECT occurrence_id, due_at FROM agenda_occurrences WHERE state IN ('scheduled', 'accepted')
               AND due_at <= ? AND (due_at, occurrence_id) > (?, ?) ORDER BY due_at, occurrence_id LIMIT ?""",
            (stamp(observed_at), *cursor, limit + 1),
        )
        page = rows[:limit]
        next_cursor = DueCursor(datetime.fromisoformat(page[-1]["due_at"]), page[-1]["occurrence_id"]) if len(rows) > limit else None
        return DuePage(tuple(row["occurrence_id"] for row in page), next_cursor)

    def _current_occurrence(self, occurrence_id: str, now: datetime):
        row = self.repository.occurrence(opaque(occurrence_id))
        if row is None:
            raise AgendaError("agenda_object_not_found", "occurrence does not exist")
        reminder = self.repository.reminder(row["reminder_id"])
        if reminder.state != "active" or row["state"] == "cancelled" or reminder.revision != row["reminder_revision"]:
            raise AgendaError("occurrence_superseded", "occurrence was cancelled or replaced")
        binding_revision = self._authorize(reminder.requester, reminder.target, reminder.authorization_action, now,
                                           self.object_key("reminder", reminder.reminder_id))
        self._authorize(reminder.requester, reminder.target, "reminder.deliver", now, self.object_key("reminder", reminder.reminder_id))
        binding = require(self.identity.resolve_role(reminder.target.scope, observed_at=now))
        if binding is None or binding.role != reminder.target.role:
            raise AgendaError("role_binding_changed", "original reminder role is no longer bound")
        if binding_revision != reminder.role_binding_revision:
            raise AgendaError("role_binding_changed", "original reminder role binding is no longer current")
        memo = self._memo(reminder.memo_id, reminder.target) if reminder.memo_id else None
        if memo is not None:
            self._authorize(reminder.requester, reminder.target, "memo.reminder.set", now,
                            self.object_key("memo", reminder.memo_id))
            if memo.state != "active" or memo.sequence != row["sequence"]:
                raise AgendaError("occurrence_superseded", "linked memo is no longer in this occurrence")
        if row["due_at"] > stamp(now):
            raise AgendaError("reminder_not_due", "occurrence is not due")
        return row, reminder, memo

    def accept_due(self, occurrence_id: str, *, observed_at: datetime) -> Outcome[DueClaim]:
        try:
            with self.runtime.transaction() as connection:
                row, reminder, memo = self._current_occurrence(occurrence_id, observed_at)
                memo_revision = memo.content_revision if memo else None
                if row["state"] == "scheduled":
                    connection.execute(
                        """UPDATE agenda_occurrences SET state = 'accepted', revision = revision + 1,
                           memo_revision = ?, updated_at = ? WHERE occurrence_id = ?""",
                        (memo_revision, stamp(observed_at), occurrence_id),
                    )
                    revision = row["revision"] + 1
                elif row["memo_revision"] != memo_revision:
                    raise AgendaError("agenda_revision_conflict", "memo changed after acceptance")
                else:
                    revision = row["revision"] - (1 if row["state"] == "prepared" else 0)
                result = DueClaim(occurrence_id, revision, reminder.reminder_id, reminder.revision, memo_revision)
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    @staticmethod
    def _preparation(payload: str) -> LocalPreparation:
        data = json.loads(payload)
        target = AgendaTarget.restore(data["target"])
        return LocalPreparation(data["occurrence_id"], data["reminder_id"], data["reminder_revision"],
                                data["memo_revision"], datetime.fromisoformat(data["due_at"]), target, target.owner,
                                data["message"], datetime.fromisoformat(data["prepared_at"]),
                                continuation=data["continuation"])

    def prepare_local(self, claim: DueClaim, *, observed_at: datetime) -> Outcome[LocalPreparation]:
        try:
            with self.runtime.transaction() as connection:
                row, reminder, memo = self._current_occurrence(claim.occurrence_id, observed_at)
                expected = DueClaim(row["occurrence_id"], row["revision"] - (1 if row["state"] == "prepared" else 0),
                                    reminder.reminder_id, reminder.revision, memo.content_revision if memo else None)
                if claim != expected or row["state"] not in ("accepted", "prepared"):
                    raise AgendaError("agenda_revision_conflict", "claim is no longer current")
                if row["state"] == "prepared":
                    return Outcome.success(self._preparation(row["prepared_json"]))
                message = reminder.message if memo is None else memo.title + ("\n" + memo.body if memo.body else "")
                continuation = "awaiting_memo_completion" if memo else "finished"
                if memo is None:
                    try:
                        next_id = self.repository.schedule(reminder, sequence=row["sequence"] + 1, now=observed_at)
                        if next_id:
                            continuation = "next_registered"
                    except AgendaError as error:
                        if error.code not in ("schedule_needs_resolution", "schedule_out_of_range"):
                            raise
                        continuation = error.code
                result = LocalPreparation(claim.occurrence_id, reminder.reminder_id, reminder.revision,
                                          claim.memo_revision, datetime.fromisoformat(row["due_at"]), reminder.target,
                                          reminder.target.owner, message, observed_at, continuation=continuation)
                connection.execute(
                    """UPDATE agenda_occurrences SET state = 'prepared', revision = revision + 1,
                       prepared_json = ?, updated_at = ? WHERE occurrence_id = ?""",
                    (encode(result), stamp(observed_at), claim.occurrence_id),
                )
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))

    def validate_preparation(self, occurrence_id: str, *, observed_at: datetime) -> Outcome[LocalPreparation]:
        """Trusted local handoff read. Real Delivery must revalidate again at its own effect boundary."""
        try:
            with self.runtime.read_snapshot():
                row, reminder, memo = self._current_occurrence(occurrence_id, observed_at)
                if row["state"] != "prepared" or row["memo_revision"] != (memo.content_revision if memo else None):
                    raise AgendaError("preparation_unavailable", "preparation is missing or no longer current")
                result = self._preparation(row["prepared_json"])
            return Outcome.success(result)
        except AgendaError as error:
            return Outcome.failed(error.code, str(error))
