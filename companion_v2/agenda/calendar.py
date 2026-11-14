"""Versioned plans, reported plans and explicitly accepted commitments."""

from datetime import datetime, timedelta
import json

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.identity_policy.private_domain import source_ref
from .calendar_access import CalendarAccess
from .models import AgendaTarget, Schedule
from .records import CalendarRecords


INSTRUCTION = (
    "日历里的 role_plan 是角色计划（由角色身份持有），user_report 是用户自述，commitment/proposed 是未接受的约定；仅使用当前角色与当前私聊用户可验证的来源。accepted 只表示接受安排，不代表到点已发生或已完成，缺席和过时不能推断完成。条件讨论先保留候选，不代替对方确认；改期后的约定重新确认。presence 是公开显示的角色短状态，不包含用户姓名、地点、约定正文或健康隐私。根据当前话题选择工具；日历冲突是供协商的材料，不强行拒绝人物互动。"
)


def text(value, limit, *, blank=True):
    if type(value) is not str or len(value) > limit or (not blank and not value.strip()):
        raise TaskError("calendar_text_invalid", "invalid calendar text")
    return value


def event_data(data):
    if type(data) is not dict or set(data) != {"kind", "title", "detail", "schedule", "duration_minutes", "presence"}:
        raise TaskError("calendar_event_invalid", "provide the declared event fields")
    if data["kind"] not in ("role_plan", "user_report", "commitment"):
        raise TaskError("calendar_kind_invalid", "choose plan, reported plan or commitment")
    schedule = Schedule.restore(data["schedule"])
    if schedule is None or type(data["duration_minutes"]) is not int or not 1 <= data["duration_minutes"] <= 1440:
        raise TaskError("calendar_duration_invalid", "provide a schedule and duration of at most one day")
    return {"kind": data["kind"], "title": text(data["title"], 256, blank=False),
            "detail": text(data["detail"], 4096), "schedule": json.loads(encode(schedule)),
            "duration_minutes": data["duration_minutes"], "presence": text(data["presence"], 8)}


class CalendarService:
    def __init__(self, tasks, agenda, role_state):
        self.tasks, self.runtime, self.agenda = tasks, tasks.runtime, agenda
        self.access = CalendarAccess(tasks, role_state)
        self.records = CalendarRecords(self.runtime, tasks.identity)

    def _evidence(self, context, sources, *, at=None):
        self.access.evidence(context, sources, at=at)

    def read(self, context, identifier, *, operator=None):
        self.access.check(context, "calendar.read", operator=operator)
        record = self.records.get(context, identifier)
        if record is None:
            raise TaskError("calendar_unavailable", "event is unavailable")
        try:
            self._evidence(context, record["sources"])
        except TaskError as error:
            if error.code.startswith(("calendar_source_", "domain_source_", "source_")):
                raise TaskError("calendar_unavailable", "event is unavailable") from error
            raise
        return record

    def command(self, context, *, identifier, action, expected_revision, operation_key,
                data=None, sources=(), operator=None, planning=None):
        with self.runtime.transaction():
            self.access.check(context, "calendar.write", operator=operator)
            self._evidence(context, sources)
            actor = operator.subject if operator else context.role_binding.role
            current_record = self.records.get(context, identifier)
            write_kind = (event_data(data)["kind"] if action == "create" else
                          current_record["kind"] if current_record else "user_report")
            storage_key = self.records.storage_key_for_change(context, identifier, write_kind)

            def transform(current):
                if action == "create":
                    if current or self.records.get(context, identifier):
                        raise TaskError("calendar_exists", "event already exists")
                    fields = event_data(data)
                    return {**fields, **({"planning": planning} if planning is not None else {}),
                        "state": "accepted" if fields["kind"] == "role_plan" else "proposed",
                        "sources": json.loads(encode(sources)), "exceptions": {}, "accepted_by": [],
                        "changed_by": json.loads(encode(actor)), "reminder_id": None,
                        "reminder_context_key": None}
                if current is None or current["state"] == "cancelled":
                    raise TaskError("calendar_closed", "event is missing or cancelled")
                self._evidence(context, current["sources"])
                result = {**current, "changed_by": json.loads(encode(actor))}
                if action == "revise":
                    fields = event_data(data)
                    if fields["kind"] != current["kind"]:
                        raise TaskError("calendar_kind_changed", "create a separately attributed event to change its kind")
                    result.update(fields, state="accepted" if fields["kind"] == "role_plan" else "proposed",
                                  accepted_by=[], exceptions={}, details={}, sources=json.loads(encode(sources)))
                elif action == "refine":
                    if current["kind"] != "role_plan" or current["state"] != "accepted":
                        raise TaskError("calendar_refine_target_invalid", "only accepted role plans can be refined")
                    if type(data) is not dict or set(data) != {"sequence", "detail"}:
                        raise TaskError("calendar_refine_invalid", "select a period and its intended next steps")
                    if Schedule.restore(current["schedule"]).at(data["sequence"]) is None or (str(data["sequence"]) in current["exceptions"] and current["exceptions"][str(data["sequence"])] is None):
                        raise TaskError("calendar_occurrence_missing", "original occurrence is unavailable")
                    dependencies = tuple(dict.fromkeys((*map(source_ref, current["sources"]),
                        *(source_ref(s) if isinstance(s, dict) else s for s in sources))))
                    self.access.evidence(context, dependencies)
                    details = {**current.get("details", {}), str(data["sequence"]): text(data["detail"], 4096, blank=False)}
                    if len(details) > 128:
                        raise TaskError("calendar_exception_limit", "split this series before retaining more refinements")
                    result.update(details=details, sources=json.loads(encode(dependencies)))
                elif action == "presence":
                    if current["kind"] != "role_plan":
                        raise TaskError("calendar_presence_target_invalid", "only role plans supply a public status")
                    if type(data) is not dict or set(data) != {"presence"}:
                        raise TaskError("calendar_presence_invalid", "provide only the public wording")
                    result["presence"] = text(data["presence"], 8).strip()
                elif action == "accept":
                    if operator is None:
                        raise TaskError("calendar_confirmation_required", "acceptance needs an explicit authenticated human command")
                    result.update(state="accepted", accepted_by=[json.loads(encode(operator.subject))])
                elif action == "cancel":
                    result["state"] = "cancelled"
                elif action == "exception":
                    if type(data) is not dict or set(data) != {"sequence", "start"}:
                        raise TaskError("calendar_exception_invalid", "select an original occurrence and optional new start")
                    schedule = Schedule.restore(current["schedule"])
                    if schedule.at(data["sequence"]) is None:
                        raise TaskError("calendar_occurrence_missing", "original occurrence does not exist")
                    moved = stamp(datetime.fromisoformat(data["start"])) if data["start"] else None
                    exceptions = {**current["exceptions"], str(data["sequence"]): moved}
                    if len(exceptions) > 128:
                        raise TaskError("calendar_exception_limit", "split this long series before adding more exceptions")
                    result.update(exceptions=exceptions)
                    if current["kind"] == "commitment" and moved:
                        result.update(state="proposed", accepted_by=[])
                else:
                    raise TaskError("calendar_action_invalid", "unknown calendar command")
                if planning is not None:
                    result["planning"] = planning
                if current.get("reminder_id") and action not in ("presence", "refine"):
                    self._cancel_reminder(context, current, operation_key)
                    result.update(reminder_id=None, reminder_context_key=None)
                return result

            return self.records.change(context, identifier, operation_key=operation_key,
                expected_revision=expected_revision, parameters=[action, data, sources, actor] + ([planning] if planning is not None else []),
                transform=transform, at=self.tasks.now(), storage_key=storage_key)

    def inventory(self, context, *, after_id="", limit=30, operator=None, kinds=None, active_since=None):
        self.access.check(context, "calendar.read", operator=operator)
        rows = self.records.list(context, after_id=after_id, limit=limit, kinds=kinds, active_since=active_since)
        items, skipped, skipped_reasons = [], 0, {"role_mismatch": 0, "source_unavailable": 0}
        for row in rows:
            try:
                self._evidence(context, row["sources"])
                items.append(row)
            except TaskError as error:
                skipped += 1
                reason = "role_mismatch" if error.code == "calendar_source_role_mismatch" else "source_unavailable"
                skipped_reasons[reason] += 1
        return {"items": items, "skipped": skipped,
                "skipped_reasons": {key: count for key, count in skipped_reasons.items() if count},
                "next_cursor": rows[-1]["id"] if len(rows) == limit else None}

    def occurrences(self, context, *, start, end, operator=None, kinds=None, diagnostics=False):
        if not start < end or end - start > timedelta(days=31):
            raise TaskError("calendar_window_invalid", "query a positive window no longer than 31 days")
        self.access.check(context, "calendar.read", operator=operator)
        items, cursor, skipped, scanned, has_more = [], "", 0, 0, False
        skipped_reasons = {"role_mismatch": 0, "source_unavailable": 0}
        while scanned < 200:
            page = self.inventory(context, after_id=cursor, limit=50, operator=operator, kinds=kinds,
                                  active_since=start - timedelta(days=1))
            skipped += page["skipped"]
            for reason, count in page.get("skipped_reasons", {}).items():
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + count
            for row in page["items"]:
                if row["state"] == "cancelled":
                    continue
                schedule = Schedule.restore(row["schedule"])
                duration = timedelta(minutes=row["duration_minutes"])
                seq = schedule.first_at_or_after(start - duration)
                sequences = set()
                while seq is not None and schedule.at(seq) is not None and schedule.at(seq) < end:
                    sequences.add(seq)
                    seq += 1
                    if len(sequences) > 64:
                        break
                sequences.update(int(n) for n, moved in row["exceptions"].items()
                                 if moved and start - duration <= datetime.fromisoformat(moved) < end)
                for sequence in sorted(sequences):
                    due = schedule.at(sequence)
                    if str(sequence) in row["exceptions"]:
                        value = row["exceptions"][str(sequence)]
                        due = datetime.fromisoformat(value) if value else None
                    if due and due < end and due + duration > start:
                        items.append({"id": row["id"], "revision": row["revision"], "sequence": sequence,
                            "kind": row["kind"], "state": row["state"], "title": row["title"],
                            "detail": row.get("details", {}).get(str(sequence), row["detail"]),
                            "start": stamp(due), "end": stamp(due + duration), "presence": row["presence"],
                            "sources": row["sources"], "planning": row.get("planning")})
            scanned += 50
            if not page["next_cursor"]:
                break
            cursor = page["next_cursor"]
            if scanned >= 200:
                probe = self.inventory(context, after_id=cursor, limit=1, operator=operator, kinds=kinds,
                                       active_since=start - timedelta(days=1))
                skipped += probe["skipped"]
                for reason, count in probe.get("skipped_reasons", {}).items():
                    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + count
                has_more = bool(probe["items"] or probe["skipped"])
        items.sort(key=lambda item: (item["start"], item["id"], item["sequence"]))
        occurrence_overflow = max(0, len(items) - 64)
        result = {"items": items[:64], "partial": has_more or occurrence_overflow > 0 or bool(skipped),
                  "skipped": skipped}
        if diagnostics:
            result.update(skipped_reasons={key: count for key, count in skipped_reasons.items() if count},
                          scan_truncated=has_more, occurrences_truncated=occurrence_overflow)
        return result

    def _target(self, context):
        return AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)

    @staticmethod
    def _operation(context, key):
        identifier = "calendar-reminder-" + digest([context.key, key])
        return OperationRef(identifier, "agenda", context.scope, VersionRef(identifier, 1), identifier)

    def _cancel_reminder(self, context, record, key):
        owner_context = record.get("reminder_context_key")
        storage_key = self.records.storage_key_for(context, record["id"])
        if ((owner_context is not None and owner_context != context.key)
                or (owner_context is None and storage_key == self.records.role_key(context))):
            raise TaskError("calendar_reminder_context_mismatch",
                            "change this reminder from the conversation that created it")
        identifier = record["reminder_id"]
        reminder = self.agenda.repository.reminder(identifier)
        if reminder and reminder.state not in ("cancelled", "completed"):
            require(self.agenda.change_reminder(context.actor, target=self._target(context), reminder_id=identifier, action="cancel",
                expected_revision=reminder.revision, operation=self._operation(context, key + ":cancel"),
                expected_role_revision=context.role_binding.revision, observed_at=self.tasks.now()))

    def remind(self, context, *, identifier, expected_revision, operation_key, sequence=0, operator=None):
        with self.runtime.transaction():
            self.access.check(context, "calendar.write", operator=operator)
            current_record = self.records.get(context, identifier)
            write_kind = current_record["kind"] if current_record else "user_report"
            storage_key = self.records.storage_key_for_change(context, identifier, write_kind)
            def transform(current):
                if current is None or current["state"] != "accepted":
                    raise TaskError("calendar_not_accepted", "only accepted events can schedule a reminder")
                self._evidence(context, current["sources"])
                due = Schedule.restore(current["schedule"]).at(sequence)
                if str(sequence) in current["exceptions"]:
                    value = current["exceptions"][str(sequence)]
                    due = datetime.fromisoformat(value) if value else None
                if due is None or due <= self.tasks.now():
                    raise TaskError("calendar_reminder_past", "choose an existing future occurrence")
                if current.get("reminder_id"):
                    self._cancel_reminder(context, current, operation_key)
                rid = "calendar-reminder-" + digest([context.key, operation_key])
                require(self.agenda.create_reminder(context.actor, target=self._target(context), reminder_id=rid,
                    message="日历提醒：" + current["title"], schedule=Schedule(due, current["schedule"]["timezone_name"]),
                    operation=self._operation(context, operation_key), expected_role_revision=context.role_binding.revision,
                    observed_at=self.tasks.now()))
                return {**current, "reminder_id": rid, "reminder_context_key": context.key}
            return self.records.change(context, identifier, operation_key=operation_key,
                expected_revision=expected_revision, parameters=["remind", sequence, operator.subject if operator else None],
                transform=transform, at=self.tasks.now(), storage_key=storage_key)

    def validate_reminder(self, context, reminder_id, at):
        if not reminder_id.startswith("calendar-reminder-"):
            return
        rows = self.runtime.read_all("""SELECT record_json FROM calendar_records
            WHERE owner_key IN (?, ?) AND json_extract(record_json, '$.reminder_id') = ?""",
            (self.records.role_key(context), self.records.conversation_key(context), reminder_id))
        if len(rows) != 1:
            raise TaskError("calendar_reminder_changed", "original event no longer owns this reminder")
        record = json.loads(rows[0]["record_json"])
        if record.get("reminder_context_key", context.key) != context.key:
            raise TaskError("calendar_reminder_changed", "original reminder belongs to another conversation")
        self.access.check(context, "calendar.read", at=at)
        self._evidence(context, record["sources"], at=at)
        if record["state"] != "accepted":
            raise TaskError("calendar_reminder_changed", "original event is no longer accepted")
