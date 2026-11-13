"""Bounded reminder planning and original-source validation for shared delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from companion_v2.delivery.service import DeliveryRequest
from companion_v2.delivery.history import HistoricalText
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import AudienceRef, ScopeKind
from companion_v2.identity_policy.models import RoleBinding

from .models import AgendaError


OWNER = "agenda.reminder"
CURSOR_KEY = "agenda.background.cursor.v1"


@dataclass(frozen=True)
class AgendaDispatchPolicy:
    # A recent reminder may still be useful. Older periods are recorded as skipped.
    lateness_seconds: int = 300
    scan_size: int = 16

    def __post_init__(self):
        if type(self.lateness_seconds) is not int or not 1 <= self.lateness_seconds <= 3600:
            raise ValueError("reminder lateness must be between one second and one hour")
        if type(self.scan_size) is not int or not 1 <= self.scan_size <= 32:
            raise ValueError("reminder scans must be bounded")


class AgendaBackground:
    def __init__(self, agenda, tasks, delivery, *, policy=AgendaDispatchPolicy(), calendar=None):
        if agenda.runtime is not tasks.runtime or delivery.tasks is not tasks:
            raise ValueError("reminder planning must share the original runtime and task supervisor")
        self.agenda, self.tasks, self.delivery = agenda, tasks, delivery
        self.runtime, self.policy = agenda.runtime, policy
        self.calendar = calendar
        self._connection_available = None

    def bind_connection_gate(self, available):
        """Keep unstarted reminders pending while their account is offline."""
        self._connection_available = available

    @staticmethod
    def _context(reminder):
        target = reminder.target
        if target.scope.kind is not ScopeKind.PRIVATE or not target.scope.is_resolved or reminder.requester.subject != target.owner:
            raise TaskError("reminder_private_route_required", "reminder needs its original recipient's complete private route")
        return TaskContext(reminder.requester, RoleBinding(target.scope, target.role, reminder.role_binding_revision),
            AudienceRef(target.scope, (target.owner,)))

    @outcome
    def delivery_text(self, request, at):
        with self.runtime.read_snapshot():
            preparation = require(self.agenda.validate_preparation(request.logical_key, observed_at=at))
            row = self.agenda.repository.occurrence(request.logical_key)
            reminder = self.agenda.repository.reminder(preparation.reminder_id)
            if self.calendar is not None:
                self.calendar.validate_reminder(request.context, reminder.reminder_id, at)
            if (request.owner != OWNER or request.context != self._context(reminder)
                    or request.source_ref != encode([row["occurrence_id"], row["revision"], digest(preparation)])
                    or request.payload_hash != digest(preparation.message)):
                raise TaskError("reminder_preparation_changed", "delivery must retain its exact original reminder preparation")
            return preparation.message

    @outcome
    def history_text(self, request, at):
        return HistoricalText(require(self.delivery_text(request, at)))

    def _record(self, row, state, reason, *, delivery_id=None, next_sequence=None):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO agenda_dispatches
                (occurrence_id, occurrence_revision, state, delivery_id, reason, next_sequence, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", (row["occurrence_id"], row["revision"], state,
                delivery_id, reason, next_sequence, stamp(self.tasks.now())))

    def _skip(self, row, reminder, memo, cutoff):
        next_sequence = None
        reason = "missed_deadline"
        if memo is not None:
            reason = "missed_awaiting_memo_completion"
        else:
            try:
                next_sequence = reminder.schedule.first_at_or_after(cutoff, minimum=row["sequence"] + 1)
                if next_sequence is not None:
                    self.agenda.repository.schedule(reminder, sequence=next_sequence, now=self.tasks.now())
            except AgendaError as error:
                if error.code not in ("schedule_needs_resolution", "schedule_out_of_range"):
                    raise
                reason = error.code
        self._record(row, "skipped", reason, next_sequence=next_sequence)

    def _advance_one(self, identifier):
        now = self.tasks.now()
        try:
            row, reminder, memo = self.agenda._current_occurrence(identifier, now)
        except AgendaError as error:
            raise TaskError(error.code, str(error)) from error
        context = self._context(reminder)
        require(self.tasks.authorize_context(context, action="reminder.deliver", observed_at=now))
        cutoff = now - timedelta(seconds=self.policy.lateness_seconds)
        if datetime.fromisoformat(row["due_at"]) <= cutoff:
            # Strictly after the deadline boundary, so a skipped period cannot be re-enqueued.
            self._skip(row, reminder, memo, cutoff + timedelta(microseconds=1))
            return
        if self._connection_available is not None and not self._connection_available(context):
            return
        claim = require(self.agenda.accept_due(identifier, observed_at=now))
        prepared = require(self.agenda.prepare_local(claim, observed_at=now))
        row = self.agenda.repository.occurrence(identifier)
        request = DeliveryRequest(OWNER, identifier, context,
            encode([identifier, row["revision"], digest(prepared)]), digest(prepared.message), now,
            prepared.due_at + timedelta(seconds=self.policy.lateness_seconds))
        require(self.delivery.queue(request))
        self._record(row, "queued", "task_registered", delivery_id=request.delivery_id)

    @outcome
    def advance(self):
        """One persisted scan page. The cursor advances even past denied or full-queue rows."""
        with self.runtime.transaction() as connection:
            require(self.tasks.check_generation())
            saved = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (CURSOR_KEY,)).fetchone()
            cursor = tuple(json.loads(saved["value"])) if saved else ("", "")
            rows = connection.execute("""SELECT o.* FROM agenda_occurrences o
                WHERE o.state IN ('scheduled', 'accepted', 'prepared') AND o.due_at <= ?
                AND (o.due_at, o.occurrence_id) > (?, ?)
                AND NOT EXISTS (SELECT 1 FROM agenda_dispatches d WHERE d.occurrence_id = o.occurrence_id)
                ORDER BY o.due_at, o.occurrence_id LIMIT ?""",
                (stamp(self.tasks.now()), *cursor, self.policy.scan_size + 1)).fetchall()
            page, more = rows[:self.policy.scan_size], len(rows) > self.policy.scan_size
            for row in page:
                try:
                    # Failed queue admission must also roll back preparation and recurrence advancement.
                    with self.runtime.transaction():
                        self._advance_one(row["occurrence_id"])
                except TaskError as error:
                    if error.code in ("supervisor_generation_conflict", "supervisor_fenced"):
                        raise
                    if error.code != "task_queue_full":
                        self._record(row, "blocked", error.code)
            next_cursor = (page[-1]["due_at"], page[-1]["occurrence_id"]) if more else ("", "")
            connection.execute("""INSERT INTO runtime_metadata VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (CURSOR_KEY, encode(next_cursor)))
            # A skip/preparation can create an eligible successor earlier in this same page.
            # It is considered on the next bounded poll, without a history-sized loop here.
            return more

    @outcome
    def observe(self, actor, *, target, occurrence_id, observed_at):
        """Authorized scheduling/dispatch accounting; no reminder body is included."""
        with self.runtime.read_snapshot():
            row = self.agenda.repository.occurrence(occurrence_id)
            if row is None:
                raise TaskError("agenda_object_not_found", "occurrence is unavailable")
            require(self.agenda.read_reminder(actor, target=target, reminder_id=row["reminder_id"], observed_at=observed_at))
            dispatch = self.runtime.read_one("SELECT * FROM agenda_dispatches WHERE occurrence_id = ?", (occurrence_id,))
            if dispatch is None:
                return {"occurrence_id": occurrence_id, "scheduling": row["state"], "delivery": "not_started"}
            delivery = self.delivery._row(dispatch["delivery_id"]) if dispatch["delivery_id"] else None
            return {"occurrence_id": occurrence_id, "scheduling": dispatch["state"],
                "reason": delivery["reason"] or dispatch["reason"] if delivery else dispatch["reason"],
                "delivery": delivery["state"] if delivery else "not_started",
                "delivery_id": dispatch["delivery_id"], "next_sequence": dispatch["next_sequence"],
                "receipt_ref": delivery["receipt_ref"] if delivery else None}
