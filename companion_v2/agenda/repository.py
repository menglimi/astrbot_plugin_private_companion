"""Agenda rows share the V2 transaction and operation authority."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3

from companion_v2.storage.runtime import StorageRuntime

from .models import (AgendaError, AgendaTarget, Memo, Reminder, Schedule, encode, fingerprint,
                     open_item_expiry, restore_identity, stamp)


class AgendaRepository:
    def __init__(self, runtime: StorageRuntime) -> None:
        self.runtime = runtime

    def memo(self, memo_id: str) -> Memo | None:
        row = self.runtime.read_one("SELECT * FROM agenda_memos WHERE memo_id = ?", (memo_id,))
        if row is None:
            return None
        data = json.loads(row["record_json"])
        created_at = datetime.fromisoformat(row["created_at"])
        purpose = data.get("purpose", "note")
        schedule = Schedule.restore(data.get("schedule"))
        expires_at = data.get("expires_at")
        if expires_at is not None:
            expires_at = datetime.fromisoformat(expires_at)
        elif purpose == "open_loop":
            expires_at = open_item_expiry(created_at, schedule, data.get("sequence", 0))
        activity = self.runtime.read_one("SELECT * FROM agenda_open_loop_activity WHERE memo_id = ?", (memo_id,))
        return Memo(memo_id, AgendaTarget.restore(json.loads(row["target_json"])), row["revision"], row["state"],
                    data["title"], data["body"], bool(row["pinned"]), schedule,
                    data["sequence"], data["completion_count"], restore_identity(data["creator"]),
                    data.get("content_revision", 1), purpose, expires_at, created_at,
                    datetime.fromisoformat(activity["last_context_at"]) if activity and activity["last_context_at"] else None,
                    datetime.fromisoformat(activity["last_followup_at"]) if activity and activity["last_followup_at"] else None,
                    activity["context_count"] if activity else 0,
                    activity["followup_count"] if activity else 0)

    def reminder(self, reminder_id: str) -> Reminder | None:
        row = self.runtime.read_one("SELECT * FROM agenda_reminders WHERE reminder_id = ?", (reminder_id,))
        if row is None:
            return None
        data = json.loads(row["record_json"])
        return Reminder(reminder_id, AgendaTarget.restore(json.loads(row["target_json"])), row["revision"], row["state"],
                        row["memo_id"], data["message"], Schedule.restore(data["schedule"]),
                        restore_identity(data["requester"]), data["role_binding_revision"],
                        data["authorization_action"], data["sequence"])

    def linked_reminder(self, memo_id: str) -> Reminder | None:
        row = self.runtime.read_one("SELECT reminder_id FROM agenda_reminders WHERE memo_id = ?", (memo_id,))
        return self.reminder(row["reminder_id"]) if row else None

    def claim_open_loop_context(
        self, memo_id: str, *, at: datetime, cooldown: timedelta, max_count: int, force: bool = False,
    ) -> bool:
        cutoff = stamp(at - cooldown)
        timestamp = stamp(at)
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT last_context_at, context_count FROM agenda_open_loop_activity WHERE memo_id = ?",
                                     (memo_id,)).fetchone()
            if row is not None and row["last_context_at"] == timestamp:
                return True
            if row is not None and row["context_count"] >= max_count:
                return False
            if (not force and row is not None and row["last_context_at"] is not None
                    and row["last_context_at"] > cutoff):
                return False
            connection.execute("""INSERT INTO agenda_open_loop_activity
                (memo_id, last_context_at, context_count, last_followup_at, followup_count, updated_at)
                VALUES (?, ?, 1, NULL, 0, ?)
                ON CONFLICT(memo_id) DO UPDATE SET last_context_at = excluded.last_context_at,
                    context_count = agenda_open_loop_activity.context_count + 1,
                    updated_at = excluded.updated_at""", (memo_id, timestamp, timestamp))
            return True

    def claim_open_loop_followup(self, memo_id: str, *, at: datetime) -> bool:
        timestamp = stamp(at)
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT followup_count FROM agenda_open_loop_activity WHERE memo_id = ?",
                                     (memo_id,)).fetchone()
            if row is not None and row["followup_count"] > 0:
                return False
            connection.execute("""INSERT INTO agenda_open_loop_activity
                (memo_id, last_context_at, context_count, last_followup_at, followup_count, updated_at)
                VALUES (?, NULL, 0, ?, 1, ?)
                ON CONFLICT(memo_id) DO UPDATE SET last_followup_at = excluded.last_followup_at,
                    followup_count = agenda_open_loop_activity.followup_count + 1,
                    updated_at = excluded.updated_at""", (memo_id, timestamp, timestamp))
            return True

    def record_open_loop_followup_delivery(self, memo_id: str, *, at: datetime) -> bool:
        """Bind the claim to the message that actually reached the user.

        The scheduler claims a follow-up while selecting a candidate.  That
        claim prevents duplicates, but its timestamp is not evidence that the
        message was sent.  Only a confirmed delivery may open the reply-review
        window.  The receipt carries its original ``delivered_at``, so a replay
        writes the same value; a segmented message may advance to its latest
        delivered part.
        """
        timestamp = stamp(at)
        with self.runtime.transaction() as connection:
            result = connection.execute(
                """UPDATE agenda_open_loop_activity
                   SET last_followup_at = ?, updated_at = ?
                   WHERE memo_id = ? AND followup_count > 0""",
                (timestamp, timestamp, memo_id))
            return result.rowcount == 1

    def claim_open_loop_review(self, memo_id: str, *, at: datetime) -> bool:
        """Record one model-facing review after a delivered follow-up.

        A review is not an ordinary open-item surfacing: it must not consume
        the three-context lifetime cap, but it still records ``last_context_at``
        so the same delivered follow-up is not injected into every later turn.
        """
        timestamp = stamp(at)
        with self.runtime.transaction() as connection:
            row = connection.execute(
                """SELECT last_context_at, last_followup_at, followup_count
                   FROM agenda_open_loop_activity WHERE memo_id = ?""", (memo_id,)).fetchone()
            if row is None or not row["last_followup_at"] or row["followup_count"] <= 0:
                return False
            if row["last_context_at"] == timestamp:
                return True
            if row["last_context_at"] is not None and row["last_context_at"] >= row["last_followup_at"]:
                return False
            connection.execute(
                """UPDATE agenda_open_loop_activity
                   SET last_context_at = ?, updated_at = ?
                   WHERE memo_id = ?""", (timestamp, timestamp, memo_id))
            return True
    def save_memo(self, memo: Memo, *, expected_revision: int, now: datetime) -> None:
        with self.runtime.transaction() as connection:
            if expected_revision == 0:
                connection.execute("INSERT INTO agenda_memos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    memo.memo_id, memo.target.key, encode(memo.target), memo.revision, memo.state,
                    int(memo.pinned), encode(memo), stamp(now), stamp(now),
                ))
            else:
                result = connection.execute(
                    "UPDATE agenda_memos SET revision = ?, state = ?, pinned = ?, record_json = ?, updated_at = ? WHERE memo_id = ? AND revision = ?",
                    (memo.revision, memo.state, int(memo.pinned), encode(memo), stamp(now), memo.memo_id, expected_revision),
                )
                if result.rowcount != 1:
                    raise AgendaError("agenda_revision_conflict", "memo changed before commit")

    def save_reminder(self, reminder: Reminder, *, expected_revision: int, now: datetime) -> None:
        with self.runtime.transaction() as connection:
            if expected_revision == 0:
                connection.execute("INSERT INTO agenda_reminders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                    reminder.reminder_id, reminder.target.key, encode(reminder.target), reminder.memo_id,
                    reminder.revision, reminder.state, encode(reminder), stamp(now), stamp(now),
                ))
            else:
                result = connection.execute(
                    "UPDATE agenda_reminders SET revision = ?, state = ?, record_json = ?, updated_at = ? WHERE reminder_id = ? AND revision = ?",
                    (reminder.revision, reminder.state, encode(reminder), stamp(now), reminder.reminder_id, expected_revision),
                )
                if result.rowcount != 1:
                    raise AgendaError("agenda_revision_conflict", "reminder changed before commit")

    def schedule(self, reminder: Reminder, *, sequence: int, now: datetime) -> str | None:
        due = reminder.schedule.at(sequence)
        if due is None:
            return None
        occurrence_id = fingerprint([reminder.reminder_id, reminder.revision, sequence])
        with self.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO agenda_occurrences VALUES (?, ?, ?, ?, ?, 'scheduled', 1, NULL, NULL, ?)
                   ON CONFLICT(occurrence_id) DO NOTHING""",
                (occurrence_id, reminder.reminder_id, reminder.revision, sequence, stamp(due), stamp(now)),
            )
        return occurrence_id

    def replace_schedule(self, reminder: Reminder, *, previous_revision: int, now: datetime) -> None:
        with self.runtime.transaction() as connection:
            self.save_reminder(reminder, expected_revision=previous_revision, now=now)
            # Prepared data remains as historical evidence but no longer authorizes handoff.
            connection.execute(
                """UPDATE agenda_occurrences SET state = 'cancelled', revision = revision + 1, updated_at = ?
                   WHERE reminder_id = ? AND state != 'cancelled'""", (stamp(now), reminder.reminder_id),
            )
            if reminder.state == "active":
                self.schedule(reminder, sequence=reminder.sequence, now=now)

    def sync_memo(self, memo: Memo, *, now: datetime) -> Reminder | None:
        reminder = self.linked_reminder(memo.memo_id)
        if reminder is None or reminder.state != "active":
            return reminder
        updated = replace(reminder, revision=reminder.revision + 1, sequence=memo.sequence,
                          schedule=memo.schedule or reminder.schedule,
                          state="active" if memo.state == "active" and memo.schedule is not None else "cancelled")
        self.replace_schedule(updated, previous_revision=reminder.revision, now=now)
        return updated

    def invalidate_memo_content(self, memo_id: str, *, now: datetime) -> None:
        """Rebuild the same unsubmitted preparation; editing prose does not create another appointment."""
        with self.runtime.transaction() as connection:
            connection.execute(
                """UPDATE agenda_occurrences SET state = 'scheduled', revision = revision + 1,
                   memo_revision = NULL, prepared_json = NULL, updated_at = ?
                   WHERE reminder_id IN (SELECT reminder_id FROM agenda_reminders WHERE memo_id = ? AND state = 'active')
                   AND state IN ('accepted', 'prepared')""", (stamp(now), memo_id),
            )

    def erase_memo_preparations(self, memo_id: str) -> None:
        with self.runtime.transaction() as connection:
            connection.execute(
                """UPDATE agenda_occurrences SET prepared_json = NULL
                   WHERE reminder_id IN (SELECT reminder_id FROM agenda_reminders WHERE memo_id = ?)""", (memo_id,),
            )

    def occurrence(self, occurrence_id: str) -> sqlite3.Row | None:
        return self.runtime.read_one("SELECT * FROM agenda_occurrences WHERE occurrence_id = ?", (occurrence_id,))
