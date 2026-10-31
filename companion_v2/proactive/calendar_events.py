"""Upcoming accepted commitments as revision-bound private contact candidates."""

from collections import OrderedDict
from datetime import datetime, timedelta
import json

from companion_v2.agenda.appointments import AppointmentSilence, occurrence_at, upcoming_occurrences
from companion_v2.agenda.models import AgendaError, Schedule
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import SourceRef, SubjectKind, VersionRef
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.sources.dependencies import link
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


SOURCE_OWNER = "proactive.calendar"
PAGE_SIZE = 32
POLL_INTERVAL = timedelta(seconds=30)
POST_COMMITMENT_DELAY = timedelta(minutes=5)
POST_COMMITMENT_WINDOW = timedelta(minutes=60)
ROLE_PLAN_FINISH_WINDOW = timedelta(minutes=45)
ROLE_PLAN_MIN_DURATION = 15


class CalendarContactEvents:
    def __init__(self, contacts, calendar):
        if contacts.runtime is not calendar.runtime:
            raise ValueError("calendar contacts must share the original calendar ledger")
        self.contacts, self.calendar, self.runtime = contacts, calendar, calendar.runtime
        self._scans = OrderedDict()
        self.appointments = AppointmentSilence(calendar)
        self.guard = bind(self.runtime, SOURCE_OWNER, self._validate_source)

    def close(self):
        self.guard.close()
        self._scans.clear()
        self.appointments.close()

    def _record(self, context, identifier, at, *, phase="upcoming"):
        self.calendar.access.check(context, "calendar.read", at=at)
        record = self.calendar.records.get(context, identifier)
        if record is None:
            raise TaskError("contact_calendar_unavailable", "original commitment is unavailable")
        self.calendar.access.evidence(context, record["sources"], at=at)
        if phase == "role_plan_finished":
            if (record["kind"] != "role_plan" or record["state"] != "accepted"
                    or record.get("reminder_id")
                    or type(record.get("duration_minutes")) is not int
                    or record["duration_minutes"] < ROLE_PLAN_MIN_DURATION):
                raise TaskError("contact_calendar_unconfirmed", "only an accepted role plan without a separate reminder qualifies")
            return record
        if (record["kind"] != "commitment" or record["state"] != "accepted"
                or record.get("reminder_id") or not any(
                    type(item) is dict and item.get("kind") == SubjectKind.HUMAN.value
                    for item in record.get("accepted_by", ()))):
            raise TaskError("contact_calendar_unconfirmed", "only a human-accepted commitment without a separate reminder qualifies")
        return record

    @staticmethod
    def _window(preferences):
        """The appointment lead and lag this relationship configured.

        ``lead`` may be 0, which means the contact opens exactly at the
        appointment instant ("到点开口"); an opportunity always stays open for at
        least one minute so the send is still possible. ``lag`` bounds how long
        after the appointment the contact may still be generated; a longer
        outage drops the slot instead of replaying a stale appointment.
        """
        lead = timedelta(minutes=preferences.values["appointment_lead_minutes"])
        lag = timedelta(minutes=preferences.values["appointment_lag_minutes"])
        return lead, lag

    @classmethod
    def _upcoming(cls, record, start, end):
        return upcoming_occurrences(record, start, end)

    @staticmethod
    def _opportunity(context, record, sequence, due, lead, lag, *, phase="upcoming"):
        duration = timedelta(minutes=record["duration_minutes"])
        if phase == "post_commitment":
            ends = due + duration
            opened = ends + POST_COMMITMENT_DELAY
            expires = opened + POST_COMMITMENT_WINDOW
            return Opportunity("calendar-afterglow-" + digest([context.key, record["id"], sequence, stamp(due)]),
                context.scope, opened, expires)
        if phase == "role_plan_finished":
            ends = due + duration
            opened = ends
            expires = ends + ROLE_PLAN_FINISH_WINDOW
            return Opportunity("calendar-plan-done-" + digest([context.key, record["id"], sequence, stamp(due)]),
                context.scope, opened, expires)
        opened = due - lead
        expires = max(due + lag, opened + timedelta(minutes=1))
        return Opportunity("calendar-" + digest([context.key, record["id"], sequence, stamp(due)]),
            context.scope, opened, expires)

    @staticmethod
    def _material(record, sequence, due, *, phase="upcoming"):
        schedule = Schedule.restore(record["schedule"])
        duration = timedelta(minutes=record["duration_minutes"])
        ends = due + duration
        if phase == "post_commitment":
            return {"event": "accepted_commitment_concluded", "title": record["title"],
                "detail": record["detail"], "sequence": sequence,
                "starts_at": stamp(due), "ends_at": stamp(ends),
                "local_start": due.astimezone(schedule.zone()).isoformat(),
                "local_end": ends.astimezone(schedule.zone()).isoformat(),
                "timezone": schedule.timezone_name, "duration_minutes": record["duration_minutes"],
                "meaning": "这是双方此前已接受、且预定时间段已经结束的约定；这只证明原定时间已过，不证明双方线下见过面或完成了约定事项，结合期间实际聊天情况自然回味或轻声问候。"}
        if phase == "role_plan_finished":
            return {"event": "role_plan_window_concluded", "title": record["title"],
                "detail": record["detail"], "sequence": sequence,
                "starts_at": stamp(due), "ends_at": stamp(ends),
                "local_start": due.astimezone(schedule.zone()).isoformat(),
                "local_end": ends.astimezone(schedule.zone()).isoformat(),
                "timezone": schedule.timezone_name, "duration_minutes": record["duration_minutes"],
                "presence": record.get("presence", ""),
                "meaning": "这是角色自己日程表上已过预定时间段的一项安排；不证明实际执行、刚忙完或取得成果。日程结束只是可选的联系机会，结合近期对话判断有没有值得分享的内容；同一件事没有真实新变化、话题已经收尾时，通常留白，不从计划标题续编进度。"}
        return {"event": "accepted_commitment_upcoming", "title": record["title"],
            "detail": record["detail"], "sequence": sequence,
            "starts_at": stamp(due), "local_start": due.astimezone(schedule.zone()).isoformat(),
            "timezone": schedule.timezone_name, "duration_minutes": record["duration_minutes"],
            "meaning": "这是人类明确接受、尚未开始的约定；不能据此声称已经见面、出席或完成。"}

    @staticmethod
    def _source_ref(context, record, sequence, due, *, phase="upcoming"):
        tag = [context.key, record["id"], record["revision"], sequence, stamp(due)]
        if phase != "upcoming":
            tag.append(phase)
        identifier = "contact-calendar-source-" + digest(tag)
        return SourceRef(identifier, SOURCE_OWNER, context.scope, VersionRef(identifier, 1),
            datetime.fromisoformat(record["updated_at"]))

    def _project(self, context, record, sequence, due, at, *, phase="upcoming"):
        reference = self._source_ref(context, record, sequence, due, phase=phase)
        receipt = {"context": json.loads(encode(context)), "id": record["id"],
            "revision": record["revision"], "sequence": sequence, "starts_at": stamp(due),
            "record_hash": digest(record)}
        if phase != "upcoming":
            receipt["phase"] = phase
        payload = json.dumps({**self._material(record, sequence, due, phase=phase), "_receipt": receipt},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.runtime.transaction():
            require(self.contacts.sources.publish(
                SourceRecord(reference, context.audience, digest(payload), payload), observed_at=at))
            link(self.runtime, reference, tuple(source_ref(item) for item in record["sources"]), at)
        require(self.contacts.sources.validate_reference_for_audience(
            reference, recipient=context.actor.subject, observed_at=at))
        return reference

    def _validate_source(self, source, at):
        try:
            payload = json.loads(source.payload)
            receipt = payload.pop("_receipt")
            if type(payload) is not dict or type(receipt) is not dict:
                raise ValueError("invalid calendar source receipt")
            keys = set(receipt)
            if keys == {"context", "id", "revision", "sequence", "starts_at", "record_hash"}:
                phase = "upcoming"
            elif keys == {"context", "id", "revision", "sequence", "starts_at", "record_hash", "phase"}:
                phase = receipt["phase"]
                if phase not in ("post_commitment", "role_plan_finished"):
                    raise ValueError("invalid calendar source phase")
            else:
                raise ValueError("invalid calendar source receipt")
            context = TaskContext.restore(receipt["context"])
            record = self._record(context, receipt["id"], at, phase=phase)
            due = occurrence_at(record, receipt["sequence"])
            if (due is None or source.reference != self._source_ref(context, record, receipt["sequence"], due, phase=phase)
                    or source.audience != context.audience or record["revision"] != receipt["revision"]
                    or digest(record) != receipt["record_hash"] or stamp(due) != receipt["starts_at"]
                    or payload != self._material(record, receipt["sequence"], due, phase=phase)
                    or source.content_hash != digest(source.payload)):
                raise ValueError("original calendar commitment changed")
        except TaskError:
            raise
        except (AgendaError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_calendar_source_changed", "original commitment cannot attest this source") from error

    def _known(self, context, opportunity):
        identifier = self.contacts.task_id(context, opportunity)
        return self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (identifier,)) or self.runtime.read_one(
            "SELECT 1 FROM proactive_contacts WHERE task_id = ?", (identifier,))

    def select(self, context, preferences, at):
        """Scan one calendar page for approaching commitments; durable task IDs prevent repeats."""
        token = preferences.snapshot.resolution_token
        state = self._scans.get(context.key)
        if state is None or state[0] != token:
            state = (token, "", at)
        self._scans[context.key] = state
        self._scans.move_to_end(context.key)
        while len(self._scans) > 1024:
            self._scans.popitem(last=False)
        _, cursor, ready_at = state
        if ready_at > at:
            return None, False
        if preferences.reason("reminder"):
            self._scans[context.key] = (token, "", at + POLL_INTERVAL)
            return None, False
        try:
            self.calendar.access.check(context, "calendar.read", at=at)
        except TaskError as error:
            if error.code in ("supervisor_fenced", "supervisor_generation_conflict"):
                raise
            self._scans[context.key] = (token, "", at + POLL_INTERVAL)
            return None, False
        page_cursor = cursor
        page = self.calendar.inventory(context, after_id=cursor, limit=PAGE_SIZE)
        next_cursor = page["next_cursor"] or ""
        lead, lag = self._window(preferences)
        # The scan only has to reach occurrences that could be open right now; the
        # opportunity itself decides, so "到点" (lead 0) opens exactly at the instant.
        scan_start, scan_end = at - lag - timedelta(seconds=1), at + max(lead, timedelta(minutes=1)) + lag
        for record in page["items"]:
            cursor = record["id"]
            if record["kind"] != "commitment" or record["state"] != "accepted" or record.get("reminder_id"):
                continue
            try:
                self._record(context, record["id"], at)
                occurrences = self._upcoming(record, scan_start, scan_end)
            except (TaskError, AgendaError, KeyError, TypeError, ValueError):
                continue
            for sequence, due in occurrences:
                opportunity = self._opportunity(context, record, sequence, due, lead, lag)
                if not opportunity.opened_at <= at < opportunity.expires_at:
                    continue
                if self._known(context, opportunity):
                    continue
                try:
                    source = self._project(context, record, sequence, due, at)
                except (TaskError, AgendaError, KeyError, TypeError, ValueError):
                    continue
                marker = {"id": record["id"], "revision": record["revision"],
                    "sequence": sequence, "starts_at": stamp(due), "record_hash": digest(record),
                    "purpose": "reminder"}
                thought = Thought("calendar-event-" + digest(marker), opportunity, ThoughtKind.CONVERSATION,
                    "一项已被人类明确接受的约定即将开始，可以自然地简短提醒；不合适就跳过，不说成已经发生。",
                    EvidenceSnapshot(context.scope, at, (source,)), 1.0)
                self._scans[context.key] = (token, page_cursor, at)
                return (opportunity, (ContactIntent("reminder", thought),), marker), True
        more = next_cursor != ""
        self._scans[context.key] = (token, next_cursor, at if more else at + POLL_INTERVAL)
        return None, more

    def select_transitions(self, context, preferences, at, *, purpose=None):
        """Scan one calendar page for post-commitment afterglow or concluded role plans."""
        scan_key = context.key + ":transitions"
        token = preferences.snapshot.resolution_token
        state = self._scans.get(scan_key)
        if state is None or state[0] != token:
            state = (token, "", at)
        self._scans[scan_key] = state
        self._scans.move_to_end(scan_key)
        while len(self._scans) > 1024:
            self._scans.popitem(last=False)
        _, cursor, ready_at = state
        if ready_at > at:
            return None, False
        followup_ok = (purpose in (None, "followup")) and not preferences.reason("followup")
        life_ok = (purpose in (None, "life")) and not preferences.reason("life")
        if not followup_ok and not life_ok:
            self._scans[scan_key] = (token, "", at + POLL_INTERVAL)
            return None, False
        try:
            self.calendar.access.check(context, "calendar.read", at=at)
        except TaskError as error:
            if error.code in ("supervisor_fenced", "supervisor_generation_conflict"):
                raise
            self._scans[scan_key] = (token, "", at + POLL_INTERVAL)
            return None, False
        page_cursor = cursor
        page = self.calendar.inventory(context, after_id=cursor, limit=PAGE_SIZE)
        next_cursor = page["next_cursor"] or ""
        lead, lag = self._window(preferences)
        for record in page["items"]:
            cursor = record["id"]
            if record.get("state") != "accepted" or record.get("reminder_id"):
                continue
            kind = record.get("kind")
            if kind == "commitment" and followup_ok:
                phase, candidate_purpose = "post_commitment", "followup"
                max_window = POST_COMMITMENT_DELAY + POST_COMMITMENT_WINDOW
            elif kind == "role_plan" and life_ok:
                phase, candidate_purpose = "role_plan_finished", "life"
                max_window = ROLE_PLAN_FINISH_WINDOW
            else:
                continue
            duration = timedelta(minutes=record.get("duration_minutes") or 0)
            scan_start = at - duration - max_window - timedelta(seconds=1)
            scan_end = at + timedelta(seconds=1)
            try:
                self._record(context, record["id"], at, phase=phase)
                occurrences = self._upcoming(record, scan_start, scan_end)
            except (TaskError, AgendaError, KeyError, TypeError, ValueError):
                continue
            for sequence, due in occurrences:
                opportunity = self._opportunity(context, record, sequence, due, lead, lag, phase=phase)
                if not opportunity.opened_at <= at < opportunity.expires_at:
                    continue
                if self._known(context, opportunity):
                    continue
                try:
                    source = self._project(context, record, sequence, due, at, phase=phase)
                except (TaskError, AgendaError, KeyError, TypeError, ValueError):
                    continue
                marker = {"id": record["id"], "revision": record["revision"],
                    "sequence": sequence, "starts_at": stamp(due), "record_hash": digest(record),
                    "purpose": candidate_purpose, "phase": phase}
                if phase == "post_commitment":
                    summary = ("双方此前约定的时间段刚刚走完，可以顺着刚才的约定自然回味一句或轻轻关心对方现在怎么样；"
                               "时间过去不等于确认完成了约定内容，不合适就跳过。")
                else:
                    summary = ("角色日程表上的一段安排已过预定时间，这本身不证明活动执行或产生新进展。"
                               "结合近期对话与真实记录，有自然的新内容才考虑分享；同一话题已经收尾或只是状态未变，可以留白。")
                thought = Thought("calendar-transition-" + digest(marker), opportunity, ThoughtKind.CONVERSATION,
                    summary, EvidenceSnapshot(context.scope, at, (source,)), 1.0)
                self._scans[scan_key] = (token, page_cursor, at)
                return (opportunity, (ContactIntent(candidate_purpose, thought),), marker), True
        more = next_cursor != ""
        self._scans[scan_key] = (token, next_cursor, at if more else at + POLL_INTERVAL)
        return None, more

    def validate(self, context, marker, at):
        if not isinstance(marker, dict):
            raise TaskError("contact_calendar_invalid", "automatic reminder requires its exact original occurrence")
        keys = set(marker)
        if keys == {"id", "revision", "sequence", "starts_at", "record_hash", "purpose"}:
            phase, expected_purpose = "upcoming", "reminder"
        elif keys == {"id", "revision", "sequence", "starts_at", "record_hash", "purpose", "phase"}:
            phase = marker["phase"]
            if phase == "post_commitment":
                expected_purpose = "followup"
            elif phase == "role_plan_finished":
                expected_purpose = "life"
            else:
                raise TaskError("contact_calendar_invalid", "calendar transition phase is invalid")
        else:
            raise TaskError("contact_calendar_invalid", "automatic reminder requires its exact original occurrence")
        record = self._record(context, marker["id"], at, phase=phase)
        try:
            due = occurrence_at(record, marker["sequence"])
        except (AgendaError, KeyError, TypeError, ValueError) as error:
            raise TaskError("contact_calendar_changed", "original occurrence changed") from error
        if (due is None or record["revision"] != marker["revision"] or digest(record) != marker["record_hash"]
                or stamp(due) != marker["starts_at"] or marker["purpose"] != expected_purpose):
            raise TaskError("contact_calendar_changed", "original commitment changed after selection")
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        lead, lag = self._window(preferences)
        opportunity = self._opportunity(context, record, marker["sequence"], due, lead, lag, phase=phase)
        if not opportunity.opened_at <= at < opportunity.expires_at:
            raise TaskError("contact_calendar_expired", "commitment window is no longer active")
        preferences.require_contact(expected_purpose)
