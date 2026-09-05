# -*- coding: utf-8 -*-
"""Long-lived calendar contracts used by the companion agenda.

This module is deliberately independent from the daily-plan generator.  Calendar
records are durable facts/constraints (periods, events, recurring rules and
explicit exceptions); a snapshot expands them for one date and reports
conflicts without mutating the source records.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from ._agenda_primitives import AgendaContractError, stable_id, timezone_or_default


CALENDAR_VERSION = 1
CALENDAR_RECORD_KINDS = {"period", "event", "recurrence", "exception"}
CALENDAR_EVENT_KINDS = {"period", "event"}
CALENDAR_STATUSES = {"confirmed", "active", "tentative", "cancelled", "expired"}
# ``status`` is retained as the projection consumed by the existing calendar
# expander.  ``lifecycle_state`` carries the richer observation lifecycle so a
# candidate can become confirmed, active, completed, cancelled, or expired
# without making the old status contract ambiguous.
CALENDAR_LIFECYCLE_STATES = {
    "candidate",
    "tentative",
    "confirmed",
    "active",
    "completed",
    "cancelled",
    "expired",
}
CALENDAR_EVIDENCE_KINDS = {
    "message",
    "manual",
    "observation",
    "inference",
    "system",
    "user",
    "bot",
}
RECURRENCE_FREQUENCIES = {"daily", "weekly", "monthly", "yearly"}
EXCEPTION_ACTIONS = {"cancel", "replace", "reschedule", "add"}

# Explicit exceptions and user-confirmed one-off events should win over a
# broad period/rule when they overlap.  A caller may provide ``priority`` to
# refine this ordering without changing the kind defaults.
DEFAULT_PRIORITIES = {
    "exception": 1000,
    "event": 700,
    "period": 600,
    "recurrence": 500,
}


def _text(value: Any, limit: int = 240) -> str:
    return str(value).strip()[:limit] if value is not None else ""


def _list(value: Any, limit: int = 32) -> list[Any]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[Any] = []
    for item in value:
        if item not in result:
            result.append(deepcopy(item))
        if len(result) >= limit:
            break
    return result


def _as_date(value: Any, *, field: str = "date") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value, 32)
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise AgendaContractError(f"invalid {field}: {value!r}") from exc


def _date_text(value: Any, *, field: str = "date") -> str:
    return _as_date(value, field=field).isoformat()


def _time_text(value: Any) -> str:
    text = _text(value, 48)
    if not text:
        return ""
    if "T" in text:
        text = text.split("T", 1)[1]
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(f"2000-01-01T{text}")
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%H:%M")
        except ValueError as exc:
            raise AgendaContractError(f"invalid time: {value!r}") from exc
    return parsed.time().isoformat(timespec="seconds")


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = _text(value, 16).lower()
    if text in {"1", "true", "yes", "y", "on", "是", "启用"}:
        return True
    if text in {"0", "false", "no", "n", "off", "否", "停用"}:
        return False
    return default


def _confidence(value: Any, default: float = 1.0) -> float:
    """Return a bounded confidence value without rejecting loose model output."""

    if value is None or value == "":
        return max(0.0, min(1.0, float(default)))
    text = _text(value, 32).rstrip("%")
    try:
        number = float(text)
    except (TypeError, ValueError):
        number = float(default)
    # Accept the common 0-100 percentage form as well as 0-1 confidence.
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _normal_lifecycle_state(value: Any, *, status: str = "confirmed") -> str:
    """Normalize lifecycle aliases while preserving the legacy status field."""

    aliases = {
        "proposed": "candidate",
        "suggested": "candidate",
        "inferred": "candidate",
        "pending": "tentative",
        "planned": "tentative",
        "accepted": "confirmed",
        "scheduled": "confirmed",
        "in_progress": "active",
        "in-progress": "active",
        "started": "active",
        "done": "completed",
        "complete": "completed",
        "finished": "completed",
        "finish": "completed",
        "canceled": "cancelled",
        "deleted": "cancelled",
    }
    candidate = _text(value, 32).lower()
    if candidate:
        candidate = aliases.get(candidate, candidate)
        if candidate in CALENDAR_LIFECYCLE_STATES:
            return candidate
    status_value = aliases.get(_text(status, 32).lower(), _text(status, 32).lower())
    if status_value == "tentative":
        # Existing tentative records are semantically pending observations.
        return "tentative"
    if status_value in CALENDAR_LIFECYCLE_STATES:
        return status_value
    return "confirmed"


def _evidence_datetime(value: Any, *, timezone_name: str) -> str:
    if not value:
        return ""
    try:
        return _datetime_text(value, timezone_name=timezone_name, field="evidence observed_at")
    except AgendaContractError:
        return ""


def normalize_calendar_evidence(
    raw: Any,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any] | None:
    """Normalize one source assertion attached to a calendar candidate.

    Evidence is intentionally small and JSON-safe.  Invalid optional fields
    are ignored so a malformed extraction cannot prevent a calendar record
    from being saved.
    """

    if isinstance(raw, str):
        raw = {"quote": raw}
    if not isinstance(raw, dict):
        return None
    source_type = _text(
        raw.get("source_type") or raw.get("kind") or raw.get("type") or "message",
        32,
    ).lower()
    if source_type not in CALENDAR_EVIDENCE_KINDS:
        source_type = "message"
    source_id = _text(
        raw.get("source_id")
        or raw.get("message_id")
        or raw.get("event_id")
        or raw.get("ref")
        or raw.get("source_ref"),
        200,
    )
    quote = _text(
        raw.get("quote")
        or raw.get("excerpt")
        or raw.get("text")
        or raw.get("content")
        or raw.get("assertion"),
        500,
    )
    observed_at = _evidence_datetime(
        raw.get("observed_at") or raw.get("created_at") or raw.get("timestamp") or raw.get("at"),
        timezone_name=timezone_name,
    )
    if not observed_at and now:
        observed_at = now.astimezone(timezone_or_default(timezone_name)).isoformat(timespec="seconds")
    if not source_id and not quote and not observed_at:
        return None
    evidence_id = _text(raw.get("evidence_id") or raw.get("id"), 200)
    if not evidence_id:
        evidence_id = stable_id("calendar_evidence", source_type, source_id, quote, observed_at)
    result: dict[str, Any] = {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "confidence": _confidence(raw.get("confidence"), default=0.7),
    }
    if source_id:
        result["source_id"] = source_id
    if quote:
        result["quote"] = quote
    if observed_at:
        result["observed_at"] = observed_at
    actor = _text(raw.get("actor") or raw.get("asserted_by") or raw.get("author"), 80)
    if actor:
        result["actor"] = actor
    return result


def normalize_calendar_evidence_chain(
    value: Any,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
    limit: int = 32,
) -> list[dict[str, Any]]:
    """Normalize and de-duplicate a record's evidence chain."""

    if isinstance(value, (dict, str)):
        values: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        max_items = max(1, min(128, int(limit)))
    except (TypeError, ValueError):
        max_items = 32
    for item in values:
        evidence = normalize_calendar_evidence(item, now=now, timezone_name=timezone_name)
        if not evidence:
            continue
        key = str(evidence.get("evidence_id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(evidence)
        if len(result) >= max_items:
            break
    return result


def merge_calendar_evidence(
    record: dict[str, Any],
    evidence: Any,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
    limit: int = 32,
) -> dict[str, Any]:
    """Return a copied record with new evidence appended idempotently."""

    if not isinstance(record, dict):
        raise AgendaContractError("calendar record must be an object")
    result = deepcopy(record)
    existing = normalize_calendar_evidence_chain(
        result.get("evidence") or result.get("evidence_chain"),
        now=now,
        timezone_name=timezone_name,
        limit=limit,
    )
    additions = normalize_calendar_evidence_chain(
        evidence,
        now=now,
        timezone_name=timezone_name,
        limit=limit,
    )
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *additions]:
        key = str(item.get("evidence_id") or stable_id("calendar_evidence", item))
        if key in seen:
            continue
        seen.add(key)
        combined.append(item)
    result["evidence"] = combined[: max(1, min(128, int(limit) if str(limit).lstrip("-").isdigit() else 32))]
    source_refs = [str(item)[:200] for item in _list(result.get("source_refs"), 20) if str(item).strip()]
    for item in result["evidence"]:
        source_id = str(item.get("source_id") or "")
        if source_id and source_id not in source_refs:
            source_refs.append(source_id)
    result["source_refs"] = source_refs[:20]
    return result


def _datetime_text(value: Any, *, timezone_name: str, field: str) -> str:
    """Normalize an optional instant while retaining the configured local TZ."""

    text = _text(value, 96)
    if not text:
        return ""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AgendaContractError(f"invalid {field}: {value!r}") from exc
    tz = timezone_or_default(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.isoformat(timespec="seconds")


def _normal_kind(value: Any) -> str:
    aliases = {
        "range": "period",
        "interval": "period",
        "one_off": "event",
        "one-off": "event",
        "single": "event",
        "rule": "recurrence",
        "recurring": "recurrence",
        "override": "exception",
        "adjustment": "exception",
    }
    kind = _text(value, 32).lower()
    return aliases.get(kind, kind)


def _normal_frequency(value: Any) -> str:
    aliases = {"day": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}
    frequency = _text(value, 16).lower()
    frequency = aliases.get(frequency, frequency)
    if frequency not in RECURRENCE_FREQUENCIES:
        raise AgendaContractError(f"unsupported recurrence frequency: {value!r}")
    return frequency


def _normal_weekdays(value: Any, *, fallback: int | None = None) -> list[int]:
    names = {
        "mon": 0, "monday": 0, "周一": 0, "星期一": 0,
        "tue": 1, "tuesday": 1, "周二": 1, "星期二": 1,
        "wed": 2, "wednesday": 2, "周三": 2, "星期三": 2,
        "thu": 3, "thursday": 3, "周四": 3, "星期四": 3,
        "fri": 4, "friday": 4, "周五": 4, "星期五": 4,
        "sat": 5, "saturday": 5, "周六": 5, "星期六": 5,
        "sun": 6, "sunday": 6, "周日": 6, "星期日": 6,
    }
    values = value if isinstance(value, (list, tuple, set)) else ([value] if value not in (None, "") else [])
    result: list[int] = []
    for item in values:
        if isinstance(item, bool):
            continue
        try:
            number = int(item)
        except (TypeError, ValueError):
            number = names.get(_text(item, 16).lower(), -1)
        if 0 <= number <= 6 and number not in result:
            result.append(number)
    if not result and fallback is not None:
        result = [fallback]
    return sorted(result)


def _priority(value: Any, kind: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_PRIORITIES.get(kind, 0)
    return max(-10000, min(10000, number))


def _status(value: Any) -> str:
    status = _text(value, 24).lower()
    aliases = {"confirmed": "confirmed", "planned": "tentative", "cancelled": "cancelled", "canceled": "cancelled", "deleted": "cancelled", "done": "expired"}
    return aliases.get(status, status if status in CALENDAR_STATUSES else "confirmed")


def normalize_calendar_record(
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Normalize one calendar record into a JSON-compatible durable object."""

    if not isinstance(raw, dict):
        raise AgendaContractError("calendar record must be an object")
    kind = _normal_kind(raw.get("kind") or raw.get("type") or raw.get("record_type"))
    if kind not in CALENDAR_RECORD_KINDS:
        raise AgendaContractError(f"unsupported calendar record kind: {kind!r}")
    result = deepcopy(raw)
    title = _text(raw.get("title") or raw.get("summary") or raw.get("name"), 240)
    if kind != "exception" and not title:
        raise AgendaContractError("calendar record requires a title")

    record_id = _text(raw.get("calendar_id") or raw.get("event_id") or raw.get("id"), 160)
    if not record_id:
        record_id = stable_id("calendar", kind, title, raw.get("date") or raw.get("start_date") or raw.get("start_at"), raw.get("target_id"))
    status = _status(raw.get("status") or raw.get("state"))
    lifecycle_state = _normal_lifecycle_state(
        raw.get("lifecycle_state") or raw.get("lifecycle") or raw.get("lifecycle_status"),
        status=status,
    )
    # An explicit lifecycle is the richer source of truth.  Keep the legacy
    # status projection synchronized so old expanders do not accidentally treat
    # a candidate as a confirmed fact (or a completed item as upcoming).
    if raw.get("lifecycle_state") or raw.get("lifecycle") or raw.get("lifecycle_status"):
        if lifecycle_state in {"candidate", "tentative"}:
            status = "tentative"
        elif lifecycle_state == "active":
            status = "active"
        elif lifecycle_state == "completed":
            status = "expired"
        elif lifecycle_state == "cancelled":
            status = "cancelled"
        elif lifecycle_state == "expired":
            status = "expired"
    record_timezone = _text(raw.get("timezone"), 64) or timezone_name
    has_clock = bool(raw.get("start_time") or raw.get("end_time") or raw.get("time") or raw.get("until_time") or raw.get("start_at") or raw.get("end_at"))
    all_day = _bool_value(raw.get("all_day", raw.get("is_all_day")), default=not has_clock and kind in {"period", "event", "recurrence"})
    evidence = normalize_calendar_evidence_chain(
        raw.get("evidence") or raw.get("evidence_chain"),
        now=now,
        timezone_name=record_timezone,
    )
    result.update(
        {
            "calendar_id": record_id,
            "kind": kind,
            "type": kind,
            "title": title,
            "status": status,
            "lifecycle_state": lifecycle_state,
            "lifecycle": lifecycle_state,
            "all_day": all_day,
            "priority": _priority(raw.get("priority"), kind),
            "authority_kind": _text(raw.get("authority_kind"), 48) or "calendar",
            "commitment_level": _text(raw.get("commitment_level"), 24) or ("confirmed" if status == "confirmed" else "tentative"),
            "source_refs": [str(item)[:200] for item in _list(raw.get("source_refs"), 20) if str(item).strip()],
            "subject_actor_id": _text(raw.get("subject_actor_id") or raw.get("actor_id"), 120) or "bot_self",
            "visibility": _text(raw.get("visibility"), 24) or "private",
            "timezone": record_timezone,
            "version": max(1, int(raw.get("version") or 1)) if str(raw.get("version") or "").strip().lstrip("-").isdigit() else 1,
            "updated_at": _datetime_text(raw.get("updated_at"), timezone_name=timezone_name, field="updated_at") if raw.get("updated_at") else (now.astimezone(timezone_or_default(timezone_name)).isoformat(timespec="seconds") if now else ""),
        }
    )
    if evidence:
        result["evidence"] = evidence
    if raw.get("confidence") is not None or lifecycle_state in {"candidate", "tentative"}:
        result["confidence"] = _confidence(raw.get("confidence"), default=0.7 if lifecycle_state in {"candidate", "tentative"} else 1.0)
    history = raw.get("lifecycle_history")
    if isinstance(history, (list, tuple)):
        result["lifecycle_history"] = deepcopy(list(history)[-32:])

    if kind in {"period", "event", "recurrence"}:
        normalized_start_at = _datetime_text(raw.get("start_at"), timezone_name=record_timezone, field="start_at") if raw.get("start_at") else ""
        normalized_end_at = _datetime_text(raw.get("end_at"), timezone_name=record_timezone, field="end_at") if raw.get("end_at") else ""
        # An explicit instant is authoritative when it is supplied together
        # with a convenience ``date`` field.  This matters when an UTC
        # timestamp crosses midnight in the configured calendar timezone.
        start_date_value = normalized_start_at or raw.get("start_date") or raw.get("date") or raw.get("day")
        if not start_date_value:
            raise AgendaContractError(f"{kind} requires date or start_date")
        start_date = _date_text(start_date_value, field="start_date")
        result["start_date"] = start_date
        # Keep the legacy ``date`` alias aligned with the normalized instant;
        # otherwise a UTC timestamp that crosses midnight would expose two
        # contradictory dates to the page and prompt layers.
        result["date"] = start_date
        end_date_value = normalized_end_at.split("T", 1)[0] if normalized_end_at else (raw.get("end_date") or raw.get("until_date") or raw.get("date_end"))
        if kind == "period":
            end_date_value = end_date_value or raw.get("end") or start_date
        if end_date_value:
            end_date = _date_text(end_date_value, field="end_date")
            if end_date < start_date:
                raise AgendaContractError("end_date must not precede start_date")
            result["end_date"] = end_date
        elif kind != "recurrence":
            result["end_date"] = start_date

        start_time = raw.get("start_time") or raw.get("time")
        end_time = raw.get("end_time") or raw.get("until_time")
        if normalized_start_at and "T" in normalized_start_at:
            start_time = normalized_start_at.split("T", 1)[1]
        if normalized_end_at and "T" in normalized_end_at:
            end_time = normalized_end_at.split("T", 1)[1]
        if normalized_start_at:
            result["start_at"] = normalized_start_at
        if normalized_end_at:
            result["end_at"] = normalized_end_at
        if start_time:
            result["start_time"] = _time_text(start_time)
        if end_time:
            result["end_time"] = _time_text(end_time)
        if kind == "recurrence":
            result["frequency"] = _normal_frequency(raw.get("frequency") or raw.get("freq") or "weekly")
            interval = raw.get("interval", 1)
            try:
                interval = max(1, min(365, int(interval)))
            except (TypeError, ValueError):
                interval = 1
            result["interval"] = interval
            result["by_weekday"] = _normal_weekdays(raw.get("by_weekday") or raw.get("weekdays"), fallback=_as_date(start_date).weekday() if result["frequency"] == "weekly" else None)
            if raw.get("by_monthday") is not None:
                monthdays = []
                for item in _list(raw.get("by_monthday"), 12):
                    try:
                        number = int(item)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= number <= 31 and number not in monthdays:
                        monthdays.append(number)
                result["by_monthday"] = sorted(monthdays)
            until = raw.get("until") or raw.get("recurrence_until") or raw.get("end_date")
            if until:
                result["until"] = _date_text(until, field="until")
            try:
                count = int(raw.get("count")) if raw.get("count") is not None else 0
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                result["count"] = min(count, 10000)
    elif kind == "exception":
        target_id = _text(raw.get("target_id") or raw.get("target_event_id") or raw.get("target"), 160)
        action = _text(raw.get("action") or raw.get("operation") or "replace", 24).lower()
        if action not in EXCEPTION_ACTIONS:
            action = "replace"
        if action != "add" and not target_id:
            raise AgendaContractError("calendar exception requires target_id")
        result["target_id"] = target_id
        result["action"] = action
        target_date = raw.get("date") or raw.get("start_date") or raw.get("occurrence_date")
        if not target_date:
            raise AgendaContractError("calendar exception requires date")
        result["date"] = _date_text(target_date, field="exception date")
        if raw.get("end_date") or raw.get("until_date"):
            result["end_date"] = _date_text(raw.get("end_date") or raw.get("until_date"), field="exception end_date")
            if result["end_date"] < result["date"]:
                raise AgendaContractError("exception end_date must not precede date")
        if title:
            result["title"] = title
        elif action == "add":
            result["title"] = "临时安排"
        if raw.get("start_time") or raw.get("time"):
            result["start_time"] = _time_text(raw.get("start_time") or raw.get("time"))
        if raw.get("end_time") or raw.get("until_time"):
            result["end_time"] = _time_text(raw.get("end_time") or raw.get("until_time"))
        new_date = raw.get("new_date") or raw.get("rescheduled_date") or raw.get("move_to")
        if new_date:
            result["new_date"] = _date_text(new_date, field="exception new_date")
    return result


def normalize_calendar_records(
    records: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in records or []:
        if not isinstance(raw, dict):
            continue
        try:
            result.append(normalize_calendar_record(raw, now=now, timezone_name=timezone_name))
        except AgendaContractError:
            continue
    return result


def calendar_candidate_from_record(
    record: dict[str, Any],
    *,
    evidence: Any = None,
    confidence: Any = 0.7,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Create a durable candidate without treating an inference as a fact.

    The returned object remains compatible with the normal calendar record
    contract, but has a tentative status/commitment and an explicit lifecycle
    state.  Callers should persist it in a separate candidate section and only
    materialize it into the formal calendar after confirmation.
    """

    normalized = normalize_calendar_record(record, now=now, timezone_name=timezone_name)
    normalized["status"] = "tentative"
    normalized["commitment_level"] = "tentative"
    normalized["lifecycle_state"] = "candidate"
    normalized["lifecycle"] = "candidate"
    normalized["confidence"] = _confidence(confidence, default=0.7)
    if now:
        observed_at = now.astimezone(timezone_or_default(timezone_name)).isoformat(timespec="seconds")
        normalized["candidate_created_at"] = observed_at
        normalized["updated_at"] = observed_at
    normalized = merge_calendar_evidence(
        normalized,
        evidence,
        now=now,
        timezone_name=timezone_name,
    )
    history = normalized.get("lifecycle_history") if isinstance(normalized.get("lifecycle_history"), list) else []
    if not history:
        history = [
            {
                "from_state": "",
                "to_state": "candidate",
                "action": "candidate_created",
                "at": normalized.get("candidate_created_at") or normalized.get("updated_at") or "",
                "evidence_ids": [str(item.get("evidence_id") or "") for item in normalized.get("evidence", []) if item.get("evidence_id")],
            }
        ]
    normalized["lifecycle_history"] = history[-32:]
    return normalized


# Descriptive alias for callers that prefer the normalization vocabulary.
normalize_calendar_candidate = calendar_candidate_from_record


_LIFECYCLE_ACTIONS = {
    "candidate": "candidate",
    "propose": "candidate",
    "suggest": "candidate",
    "observe": "candidate",
    "tentative": "tentative",
    "pending": "tentative",
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "accept": "confirmed",
    "activate": "active",
    "active": "active",
    "start": "active",
    "started": "active",
    "in_progress": "active",
    "in-progress": "active",
    "complete": "completed",
    "completed": "completed",
    "finish": "completed",
    "finished": "completed",
    "done": "completed",
    "cancel": "cancelled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "reject": "cancelled",
    "rejected": "cancelled",
    "expire": "expired",
    "expired": "expired",
}


def advance_calendar_lifecycle(
    record: dict[str, Any],
    transition: str | dict[str, Any],
    *,
    evidence: Any = None,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Advance one record's lifecycle and append an auditable history entry.

    This function is deliberately pure: it copies the input and never writes
    storage.  Unknown transitions fail explicitly, while valid transitions may
    be repeated (for example a second confirmation) without duplicating a
    state-change history row.
    """

    if isinstance(transition, dict):
        action = _text(transition.get("action") or transition.get("event") or transition.get("to") or transition.get("state"), 40).lower()
        transition_evidence = transition.get("evidence")
    else:
        action = _text(transition, 40).lower()
        transition_evidence = None
    target_state = _LIFECYCLE_ACTIONS.get(action)
    if not target_state:
        # Also accept a lifecycle state spelled with spaces for model output.
        target_state = _normal_lifecycle_state(action, status="confirmed") if action.replace(" ", "_") in CALENDAR_LIFECYCLE_STATES else ""
    if target_state not in CALENDAR_LIFECYCLE_STATES:
        raise AgendaContractError(f"unsupported calendar lifecycle transition: {transition!r}")

    normalized = normalize_calendar_record(record, now=now, timezone_name=timezone_name)
    current_state = _normal_lifecycle_state(
        normalized.get("lifecycle_state") or normalized.get("lifecycle"),
        status=str(normalized.get("status") or "confirmed"),
    )
    normalized = merge_calendar_evidence(
        normalized,
        transition_evidence if transition_evidence is not None else evidence,
        now=now,
        timezone_name=timezone_name,
    )
    if target_state in {"candidate", "tentative"}:
        normalized["status"] = "tentative"
        normalized["commitment_level"] = "tentative"
    elif target_state == "confirmed":
        normalized["status"] = "confirmed"
        normalized["commitment_level"] = "confirmed"
    elif target_state == "active":
        normalized["status"] = "active"
        normalized["commitment_level"] = "confirmed"
    elif target_state == "completed":
        # ``expired`` keeps completed rows out of future expansion while the
        # lifecycle state retains the user-facing distinction from expiry.
        normalized["status"] = "expired"
        normalized["commitment_level"] = "confirmed"
    elif target_state == "cancelled":
        normalized["status"] = "cancelled"
    elif target_state == "expired":
        normalized["status"] = "expired"
    normalized["lifecycle_state"] = target_state
    normalized["lifecycle"] = target_state
    if now:
        normalized["updated_at"] = now.astimezone(timezone_or_default(timezone_name)).isoformat(timespec="seconds")
    try:
        normalized["version"] = max(1, int(normalized.get("version") or 1)) + (0 if current_state == target_state else 1)
    except (TypeError, ValueError):
        normalized["version"] = 1
    history = normalized.get("lifecycle_history") if isinstance(normalized.get("lifecycle_history"), list) else []
    evidence_ids = [
        str(item.get("evidence_id") or "")
        for item in normalized.get("evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    if current_state != target_state:
        history.append(
            {
                "from_state": current_state,
                "to_state": target_state,
                "action": action,
                "at": normalized.get("updated_at") or "",
                "evidence_ids": evidence_ids[-8:],
            }
        )
    normalized["lifecycle_history"] = history[-32:]
    return normalized


def calendar_lifecycle_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Return a prompt/page-safe lifecycle view for one calendar record."""

    if not isinstance(record, dict):
        return {}
    state = _normal_lifecycle_state(record.get("lifecycle_state") or record.get("lifecycle"), status=str(record.get("status") or "confirmed"))
    evidence = record.get("evidence") if isinstance(record.get("evidence"), list) else []
    history = record.get("lifecycle_history") if isinstance(record.get("lifecycle_history"), list) else []
    return {
        "calendar_id": str(record.get("calendar_id") or record.get("id") or ""),
        "title": str(record.get("title") or ""),
        "lifecycle_state": state,
        "status": str(record.get("status") or "confirmed"),
        "commitment_level": str(record.get("commitment_level") or ""),
        "confidence": _confidence(record.get("confidence"), default=1.0 if state in {"confirmed", "active"} else 0.7),
        "evidence_count": len(evidence),
        "last_evidence_at": str(evidence[-1].get("observed_at") or "") if evidence and isinstance(evidence[-1], dict) else "",
        "last_transition": deepcopy(history[-1]) if history and isinstance(history[-1], dict) else None,
    }


def _instance_bounds(record: dict[str, Any], occurrence_date: date, *, timezone_name: str) -> tuple[datetime, datetime]:
    tz = timezone_or_default(record.get("timezone") or timezone_name)
    start_time = "00:00:00" if record.get("all_day") else (record.get("start_time") or "00:00:00")
    end_time = record.get("end_time")
    start = datetime.combine(occurrence_date, time.fromisoformat(str(start_time)), tzinfo=tz)
    if record.get("all_day"):
        end = start + timedelta(days=1)
    elif end_time:
        end = datetime.combine(occurrence_date, time.fromisoformat(str(end_time)), tzinfo=tz)
        if end <= start:
            end += timedelta(days=1)
    else:
        end = start + timedelta(days=1 if record.get("all_day") else 1)
    return start, end


def _continuous_timed_record(record: dict[str, Any]) -> bool:
    """Whether a timed event/range represents one continuous interval.

    All-day periods intentionally remain daily instances so a day snapshot can
    describe the date it covers.  A timed record with an explicit end date is
    instead one interval; emitting one segment per day would duplicate it and
    make cross-midnight/month views misleading.
    """

    if str(record.get("kind") or "") not in {"period", "event"} or bool(record.get("all_day")):
        return False
    start_date = str(record.get("start_date") or record.get("date") or "")[:10]
    end_date = str(record.get("end_date") or start_date)[:10]
    return bool(record.get("start_time") or record.get("start_at")) and (
        bool(record.get("end_time") or record.get("end_at")) and end_date >= start_date
    )


def _continuous_instance(record: dict[str, Any], *, timezone_name: str) -> dict[str, Any]:
    start_date = _as_date(record.get("start_date") or record.get("date"), field="start_date")
    end_date = _as_date(record.get("end_date") or start_date, field="end_date")
    start, default_end = _instance_bounds(record, start_date, timezone_name=timezone_name)
    if record.get("end_at"):
        try:
            end = datetime.fromisoformat(str(record["end_at"]))
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone_or_default(record.get("timezone") or timezone_name))
        except (TypeError, ValueError):
            end = default_end
    elif record.get("end_time"):
        end = datetime.combine(
            end_date,
            time.fromisoformat(str(record["end_time"])),
            tzinfo=timezone_or_default(record.get("timezone") or timezone_name),
        )
        if end <= start:
            end += timedelta(days=1)
    else:
        end = default_end
    if end <= start:
        end = default_end
    return _record_instance(record, start_date, timezone_name=timezone_name, occurrence_key=stable_id("calendar_instance", record.get("calendar_id"), start_date.isoformat())) | {
        "start_at": start.isoformat(timespec="seconds"),
        "end_at": end.isoformat(timespec="seconds"),
    }


def _date_intersects(record: dict[str, Any], target: date) -> bool:
    start = _as_date(record.get("start_date") or record.get("date"))
    end = _as_date(record.get("end_date") or record.get("date") or start)
    return start <= target <= end


def _recurrence_matches_unbounded(record: dict[str, Any], target: date) -> bool:
    start = _as_date(record["start_date"])
    if target < start:
        return False
    until = record.get("until")
    if until and target > _as_date(until, field="until"):
        return False
    delta = (target - start).days
    frequency = record.get("frequency", "weekly")
    interval = int(record.get("interval") or 1)
    if frequency == "daily":
        return delta % interval == 0
    if frequency == "weekly":
        weekdays = record.get("by_weekday") or [start.weekday()]
        if target.weekday() not in weekdays:
            return False
        return (delta // 7) % interval == 0
    if frequency == "monthly":
        months = (target.year - start.year) * 12 + target.month - start.month
        if months < 0 or months % interval:
            return False
        monthdays = record.get("by_monthday") or [start.day]
        return target.day in monthdays
    if frequency == "yearly":
        years = target.year - start.year
        if years < 0 or years % interval:
            return False
        return target.month == start.month and target.day == start.day
    return False


def _recurrence_matches(record: dict[str, Any], target: date) -> bool:
    if not _recurrence_matches_unbounded(record, target):
        return False
    try:
        count = int(record.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return True
    start = _as_date(record["start_date"])
    delta = (target - start).days
    frequency = record.get("frequency", "weekly")
    interval = int(record.get("interval") or 1)
    if frequency == "daily":
        return (delta // interval) + 1 <= count
    if frequency == "weekly":
        window_index, offset = divmod(delta, 7)
        valid_offsets = sorted({(weekday - start.weekday()) % 7 for weekday in (record.get("by_weekday") or [start.weekday()])})
        if not valid_offsets or window_index % interval:
            return False
        ordinal = (window_index // interval) * len(valid_offsets) + sum(1 for item in valid_offsets if item <= offset)
        return ordinal <= count
    if frequency == "monthly":
        months = (target.year - start.year) * 12 + target.month - start.month
        monthdays = sorted({int(item) for item in (record.get("by_monthday") or [start.day]) if 1 <= int(item) <= 31})
        occurrences = 0
        for month_offset in range(0, months + 1, interval):
            year, month_zero = divmod(start.month - 1 + month_offset, 12)
            month_year = start.year + year
            month = month_zero + 1
            for monthday in monthdays:
                try:
                    candidate = date(month_year, month, monthday)
                except ValueError:
                    continue
                if start <= candidate <= target:
                    occurrences += 1
                    if candidate == target:
                        return occurrences <= count
        return False
    if frequency == "yearly":
        years = target.year - start.year
        occurrences = 0
        for year_offset in range(0, years + 1, interval):
            try:
                candidate = date(start.year + year_offset, start.month, start.day)
            except ValueError:
                continue
            if start <= candidate <= target:
                occurrences += 1
                if candidate == target:
                    return occurrences <= count
        return False
    return False


def _record_instance(record: dict[str, Any], occurrence_date: date, *, timezone_name: str, occurrence_key: str = "") -> dict[str, Any]:
    start, end = _instance_bounds(record, occurrence_date, timezone_name=timezone_name)
    instance = deepcopy(record)
    instance.update(
        {
            "instance_id": occurrence_key or stable_id("calendar_instance", record.get("calendar_id"), occurrence_date.isoformat()),
            "occurrence_date": occurrence_date.isoformat(),
            "start_at": start.isoformat(timespec="seconds"),
            "end_at": end.isoformat(timespec="seconds"),
            "source_calendar_id": record.get("calendar_id"),
        }
    )
    return instance


def expand_calendar_records(
    records: Iterable[dict[str, Any]],
    start_date: str | date,
    end_date: str | date | None = None,
    *,
    timezone_name: str = "Asia/Shanghai",
    include_exceptions: bool = False,
) -> list[dict[str, Any]]:
    """Expand calendar records into concrete occurrences in an inclusive range."""

    start = _as_date(start_date, field="range start")
    end = _as_date(end_date or start, field="range end")
    if end < start:
        raise AgendaContractError("range end must not precede range start")
    normalized = normalize_calendar_records(records, timezone_name=timezone_name)
    output: list[dict[str, Any]] = []
    # Materialize timed multi-day events/ranges once, provided their interval
    # intersects the requested date window.  Daily all-day periods continue to
    # be expanded below for intuitive per-day snapshots.
    for record in normalized:
        if record.get("status") in {"cancelled", "expired"} or record.get("kind") == "exception":
            continue
        if not _continuous_timed_record(record):
            continue
        try:
            instance = _continuous_instance(record, timezone_name=timezone_name)
            instance_start = datetime.fromisoformat(str(instance.get("start_at")))
            instance_end = datetime.fromisoformat(str(instance.get("end_at")))
            range_start = datetime.combine(start, time.min, tzinfo=instance_start.tzinfo)
            range_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=instance_start.tzinfo)
            if instance_start < range_end and instance_end > range_start:
                output.append(instance)
        except (AgendaContractError, TypeError, ValueError):
            continue
    day = start
    while day <= end:
        for record in normalized:
            if record.get("status") in {"cancelled", "expired"}:
                continue
            kind = record.get("kind")
            if kind == "exception":
                if include_exceptions and _date_intersects(record, day):
                    output.append(_record_instance(record, day, timezone_name=timezone_name))
                continue
            if kind == "recurrence":
                if _recurrence_matches(record, day):
                    output.append(_record_instance(record, day, timezone_name=timezone_name))
                continue
            if _continuous_timed_record(record):
                continue
            if _date_intersects(record, day):
                output.append(_record_instance(record, day, timezone_name=timezone_name))
        day += timedelta(days=1)
    return sorted(output, key=lambda item: (str(item.get("start_at") or ""), -int(item.get("priority") or 0), str(item.get("calendar_id") or "")))


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> tuple[datetime, datetime] | None:
    try:
        start_left = datetime.fromisoformat(str(left["start_at"]))
        end_left = datetime.fromisoformat(str(left["end_at"]))
        start_right = datetime.fromisoformat(str(right["start_at"]))
        end_right = datetime.fromisoformat(str(right["end_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    if start_left.tzinfo is None:
        start_left = start_left.replace(tzinfo=timezone_or_default("Asia/Shanghai"))
    if end_left.tzinfo is None:
        end_left = end_left.replace(tzinfo=timezone_or_default("Asia/Shanghai"))
    if start_right.tzinfo is None:
        start_right = start_right.replace(tzinfo=timezone_or_default("Asia/Shanghai"))
    if end_right.tzinfo is None:
        end_right = end_right.replace(tzinfo=timezone_or_default("Asia/Shanghai"))
    start, end = max(start_left, start_right), min(end_left, end_right)
    return (start, end) if start < end else None


def detect_calendar_conflicts(instances: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic pairwise conflicts and their precedence winner."""

    rows = [item for item in instances if isinstance(item, dict) and item.get("status") not in {"cancelled", "expired"}]
    conflicts: list[dict[str, Any]] = []
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if str(left.get("source_calendar_id") or left.get("calendar_id")) == str(right.get("source_calendar_id") or right.get("calendar_id")):
                continue
            overlap = _overlap(left, right)
            if overlap is None:
                continue
            left_priority = int(left.get("priority") or 0)
            right_priority = int(right.get("priority") or 0)
            if left_priority > right_priority:
                winner, loser = left, right
            elif right_priority > left_priority:
                winner, loser = right, left
            else:
                left_id = str(left.get("instance_id") or left.get("calendar_id") or "")
                right_id = str(right.get("instance_id") or right.get("calendar_id") or "")
                winner, loser = (left, right) if left_id <= right_id else (right, left)
            conflicts.append(
                {
                    "conflict_id": stable_id("calendar_conflict", left.get("instance_id"), right.get("instance_id")),
                    "event_ids": [str(left.get("source_calendar_id") or left.get("calendar_id") or ""), str(right.get("source_calendar_id") or right.get("calendar_id") or "")],
                    "instance_ids": [str(left.get("instance_id") or ""), str(right.get("instance_id") or "")],
                    "winner_id": str(winner.get("source_calendar_id") or winner.get("calendar_id") or ""),
                    "loser_id": str(loser.get("source_calendar_id") or loser.get("calendar_id") or ""),
                    "winner_priority": int(winner.get("priority") or 0),
                    "reason": "overlapping_calendar_entries",
                    "overlap_start": overlap[0].isoformat(timespec="seconds"),
                    "overlap_end": overlap[1].isoformat(timespec="seconds"),
                    "unresolved": left_priority == right_priority,
                }
            )
    return conflicts


def _exception_matches(exception: dict[str, Any], instance: dict[str, Any]) -> bool:
    target_id = str(exception.get("target_id") or "")
    source_id = str(instance.get("source_calendar_id") or instance.get("calendar_id") or "")
    if target_id and target_id != source_id:
        return False
    target_date = str(exception.get("date") or "")[:10]
    occurrence = str(instance.get("occurrence_date") or "")[:10]
    if not target_date:
        return True
    end_date = str(exception.get("end_date") or target_date)[:10]
    return target_date <= occurrence <= end_date


def resolve_calendar_snapshot(
    records: Iterable[dict[str, Any]],
    target_date: str | date,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Build one date's effective calendar view, applying explicit exceptions."""

    target = _as_date(target_date)
    normalized = normalize_calendar_records(records, timezone_name=timezone_name)
    # Tentative adjustments remain visible as records but cannot silently
    # override a confirmed schedule. Only confirmed/active exceptions are
    # enforcement inputs.
    exceptions = [item for item in normalized if item.get("kind") == "exception" and item.get("status") in {"confirmed", "active"}]
    previous = target - timedelta(days=1)
    instances = expand_calendar_records(normalized, previous, target, timezone_name=timezone_name)
    target_start = datetime.combine(target, time.min, tzinfo=timezone_or_default(timezone_name))
    target_end = target_start + timedelta(days=1)
    instances = [
        item for item in instances
        if _overlap(
            item,
            {"start_at": target_start.isoformat(), "end_at": target_end.isoformat()},
        ) is not None
    ]
    applied: list[str] = []
    effective: list[dict[str, Any]] = []
    for instance in instances:
        matching = [item for item in exceptions if _exception_matches(item, instance)]
        current = instance
        cancelled = False
        # One occurrence receives at most its highest-priority exception. This
        # prevents a lower-priority replacement from undoing a confirmed
        # higher-priority cancellation/replacement later in the same loop.
        for exception in sorted(matching, key=lambda item: (-int(item.get("priority") or 0), str(item.get("calendar_id") or "")))[:1]:
            applied.append(str(exception.get("calendar_id") or ""))
            action = exception.get("action")
            if action == "cancel":
                cancelled = True
                break
            if action in {"replace", "reschedule"}:
                new_date = _as_date(exception.get("new_date"), field="exception new_date") if exception.get("new_date") else target
                if action == "reschedule" and new_date != target:
                    cancelled = True
                    applied.append(str(exception.get("calendar_id") or ""))
                    break
                current = deepcopy(current)
                for key in ("title", "start_time", "end_time", "all_day", "priority"):
                    if exception.get(key) not in (None, ""):
                        current[key] = deepcopy(exception[key])
                current["exception_id"] = exception.get("calendar_id")
                current = _record_instance(current, new_date, timezone_name=timezone_name, occurrence_key=str(current.get("instance_id") or ""))
            current["effective_priority"] = max(int(current.get("priority") or 0), int(exception.get("priority") or 0))
        if not cancelled:
            effective.append(current)
    # A moved recurrence/event has no source occurrence on its destination
    # date. Materialize that destination explicitly when resolving the new
    # day, while leaving the original date cancelled above.
    source_records = [item for item in normalized if item.get("kind") != "exception"]
    for exception in exceptions:
        if exception.get("action") != "reschedule" or not exception.get("new_date"):
            continue
        destination_date = _as_date(exception.get("new_date"), field="exception new_date")
        if destination_date != target or destination_date == _as_date(exception.get("date"), field="exception date"):
            continue
        original_date = _as_date(exception.get("date"), field="exception date")
        moved_sources = expand_calendar_records(source_records, original_date, original_date, timezone_name=timezone_name)
        for source in moved_sources:
            if not _exception_matches(exception, source):
                continue
            current = deepcopy(source)
            for key in ("title", "start_time", "end_time", "all_day", "priority"):
                if exception.get(key) not in (None, ""):
                    current[key] = deepcopy(exception[key])
            current["exception_id"] = exception.get("calendar_id")
            current = _record_instance(current, target, timezone_name=timezone_name, occurrence_key=str(source.get("instance_id") or ""))
            current["effective_priority"] = max(int(current.get("priority") or 0), int(exception.get("priority") or 0))
            effective.append(current)
    for exception in exceptions:
        if exception.get("action") != "add" or exception.get("date") != target.isoformat():
            continue
        applied.append(str(exception.get("calendar_id") or ""))
        synthetic = deepcopy(exception)
        synthetic["kind"] = "event"
        synthetic["type"] = "event"
        synthetic["calendar_id"] = str(exception.get("calendar_id") or stable_id("calendar_add", target.isoformat())) + ":added"
        synthetic["source_calendar_id"] = synthetic["calendar_id"]
        synthetic["effective_priority"] = int(exception.get("priority") or 0)
        effective.append(_record_instance(synthetic, target, timezone_name=timezone_name))
    effective.sort(key=lambda item: (str(item.get("start_at") or ""), -int(item.get("effective_priority", item.get("priority", 0)) or 0), str(item.get("calendar_id") or "")))
    conflicts = detect_calendar_conflicts(effective)
    # Overlap is information for the timeline, not an implicit deletion rule.
    # A period and a rhythm may both be true (for example, a vacation with one
    # explicitly retained weekly lesson).  Suppression is reserved for an
    # explicit ``overrides_calendar_ids`` declaration or a concrete exception.
    by_source_id = {
        str(item.get("source_calendar_id") or item.get("calendar_id") or ""): item
        for item in effective
        if isinstance(item, dict)
    }
    for conflict in conflicts:
        winner = by_source_id.get(str(conflict.get("winner_id") or ""))
        loser = by_source_id.get(str(conflict.get("loser_id") or ""))
        if not isinstance(winner, dict) or not isinstance(loser, dict):
            continue
        overrides = winner.get("overrides_calendar_ids")
        if isinstance(overrides, str):
            overrides = [overrides]
        if not isinstance(overrides, (list, tuple, set)):
            overrides = []
        loser_source = str(loser.get("source_calendar_id") or loser.get("calendar_id") or "")
        if loser_source in {str(item) for item in overrides}:
            loser["calendar_effective"] = False
            loser["overridden_by"] = str(winner.get("source_calendar_id") or winner.get("calendar_id") or "")
    for item in effective:
        # User-message observations are intentionally visible in the calendar
        # but remain a soft candidate until explicit confirmation or repeated
        # consistent evidence promotes them.
        if str(item.get("lifecycle") or "") == "candidate":
            item["calendar_effective"] = False
        else:
            item.setdefault("calendar_effective", True)
    effective_events = [item for item in effective if item.get("calendar_effective", True)]
    return {
        "calendar_version": CALENDAR_VERSION,
        "date": target.isoformat(),
        "timezone": timezone_name,
        "events": effective,
        "effective_events": effective_events,
        "conflicts": conflicts,
        "has_conflicts": bool(conflicts),
        "applied_exceptions": sorted(set(item for item in applied if item)),
        "generated_at": datetime.now(timezone_or_default(timezone_name)).isoformat(timespec="seconds"),
    }


def _timeline_projection(
    item: dict[str, Any],
    *,
    role: str = "",
    occurrence_date: str = "",
) -> dict[str, Any]:
    """Project a calendar row into the small, prompt/page-safe timeline shape."""

    fields = (
        "calendar_id", "source_calendar_id", "instance_id", "kind", "type", "title",
        "status", "commitment_level", "priority", "timezone", "start_date", "end_date",
        "date", "start_at", "end_at", "start_time", "end_time", "all_day", "frequency",
        "interval", "by_weekday", "by_monthday", "count", "until", "target_id", "action",
        "new_date", "exception_id", "calendar_effective", "overridden_by", "overrides_calendar_ids", "category",
        "phase_type", "note", "description", "confidence", "source_refs",
    )
    result = {key: deepcopy(item[key]) for key in fields if key in item}
    if occurrence_date:
        result["occurrence_date"] = occurrence_date[:10]
    elif item.get("occurrence_date"):
        result["occurrence_date"] = str(item.get("occurrence_date"))[:10]
    if role:
        result["timeline_role"] = role
    return result


def _timeline_confirmed(item: dict[str, Any]) -> bool:
    return str(item.get("status") or "confirmed").lower() in {"confirmed", "active"} and str(item.get("commitment_level") or "confirmed").lower() != "tentative"


def resolve_calendar_timeline(
    records: Iterable[dict[str, Any]],
    target_date: str | date,
    *,
    timezone_name: str = "Asia/Shanghai",
    history_days: int = 3,
    horizon_days: int = 14,
) -> dict[str, Any]:
    """Build the companion's longitudinal calendar context.

    A snapshot answers ``what is on this date``.  A timeline answers ``what
    phase are we in, what rhythm surrounds it, what just changed, and what is
    about to change``.  It deliberately keeps overlapping rows visible.  A
    broad period is context, not an implicit deletion of every recurring rule;
    only explicit exceptions or an explicit ``overrides_calendar_ids`` field
    may suppress another record.
    """

    target = _as_date(target_date, field="timeline date")
    try:
        history = max(0, min(31, int(history_days)))
    except (TypeError, ValueError):
        history = 3
    try:
        horizon = max(1, min(62, int(horizon_days)))
    except (TypeError, ValueError):
        horizon = 14
    normalized = normalize_calendar_records(records, timezone_name=timezone_name)
    current_snapshot = resolve_calendar_snapshot(normalized, target, timezone_name=timezone_name)

    active_periods: list[dict[str, Any]] = []
    rhythms: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for record in normalized:
        status = str(record.get("status") or "confirmed").lower()
        if status in {"cancelled", "expired"}:
            continue
        if status == "tentative" or str(record.get("commitment_level") or "").lower() == "tentative":
            uncertain.append(_timeline_projection(record, role="uncertain"))
        kind = str(record.get("kind") or "")
        if kind == "period":
            start = _as_date(record.get("start_date") or record.get("date"))
            end = _as_date(record.get("end_date") or start)
            if start <= target <= end:
                active_periods.append(_timeline_projection(record, role="current_phase"))
            if target < start <= target + timedelta(days=horizon):
                transitions.append({
                    "date": start.isoformat(),
                    "kind": "phase_start",
                    "calendar_id": str(record.get("calendar_id") or ""),
                    "title": str(record.get("title") or ""),
                    "status": status,
                })
            if target <= end < target + timedelta(days=horizon + 1):
                transitions.append({
                    "date": end.isoformat(),
                    "kind": "phase_end",
                    "calendar_id": str(record.get("calendar_id") or ""),
                    "title": str(record.get("title") or ""),
                    "status": status,
                })
        elif kind == "recurrence":
            rhythms.append(_timeline_projection(record, role="rhythm"))
        elif kind == "exception":
            exception_date = _as_date(record.get("date"), field="exception date")
            new_date = _as_date(record.get("new_date"), field="exception new_date") if record.get("new_date") else None
            for date_value, transition_kind in ((exception_date, "exception"), (new_date, "rescheduled_to")):
                if date_value and target < date_value <= target + timedelta(days=horizon):
                    transitions.append({
                        "date": date_value.isoformat(),
                        "kind": transition_kind,
                        "calendar_id": str(record.get("calendar_id") or ""),
                        "title": str(record.get("title") or "例外调整"),
                        "action": str(record.get("action") or ""),
                        "status": status,
                    })

    def collect_instances(start: date, end: date, *, role: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            snapshot = resolve_calendar_snapshot(normalized, cursor, timezone_name=timezone_name)
            rows = snapshot.get("events") if isinstance(snapshot.get("events"), list) else []
            for row in rows:
                if not isinstance(row, dict) or str(row.get("kind") or "") == "period":
                    continue
                if str(row.get("status") or "confirmed") in {"cancelled", "expired"}:
                    continue
                output.append(_timeline_projection(row, role=role, occurrence_date=cursor.isoformat()))
            cursor += timedelta(days=1)
        return output

    recent = collect_instances(target - timedelta(days=history), target - timedelta(days=1), role="recent") if history else []
    upcoming = collect_instances(target + timedelta(days=1), target + timedelta(days=horizon), role="upcoming")

    def dedupe(rows: Iterable[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            key = str(row.get("instance_id") or "") or stable_id(
                "timeline", row.get("calendar_id"), row.get("occurrence_date"), row.get("start_at"), row.get("title")
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
            if len(result) >= limit:
                break
        return result

    upcoming = dedupe(sorted(upcoming, key=lambda row: (str(row.get("start_at") or row.get("occurrence_date") or ""), str(row.get("calendar_id") or ""))), limit=32)
    recent = dedupe(sorted(recent, key=lambda row: (str(row.get("start_at") or row.get("occurrence_date") or ""), str(row.get("calendar_id") or ""))), limit=16)
    today_events = [
        _timeline_projection(row, role="today", occurrence_date=str(row.get("occurrence_date") or target.isoformat()))
        for row in (current_snapshot.get("events") if isinstance(current_snapshot.get("events"), list) else [])
        if isinstance(row, dict)
    ]
    today_events = dedupe(today_events, limit=24)
    for rhythm in rhythms:
        source_id = str(rhythm.get("calendar_id") or "")
        next_row = next((row for row in upcoming if str(row.get("source_calendar_id") or row.get("calendar_id") or "") == source_id), None)
        if next_row:
            rhythm["next_occurrence"] = str(next_row.get("occurrence_date") or "")
    transitions = sorted(transitions, key=lambda row: (str(row.get("date") or ""), str(row.get("calendar_id") or "")))[:16]
    unresolved = [
        {
            "kind": "conflict",
            "conflict_id": str(item.get("conflict_id") or ""),
            "event_ids": list(item.get("event_ids") or []),
            "unresolved": bool(item.get("unresolved")),
            "winner_id": str(item.get("winner_id") or ""),
            "loser_id": str(item.get("loser_id") or ""),
        }
        for item in (current_snapshot.get("conflicts") if isinstance(current_snapshot.get("conflicts"), list) else [])
        if isinstance(item, dict) and item.get("unresolved")
    ]
    uncertain.extend(unresolved)
    active_periods.sort(key=lambda row: (-int(row.get("priority") or 0), str(row.get("calendar_id") or "")))
    rhythms.sort(key=lambda row: (str(row.get("next_occurrence") or "9999-12-31"), str(row.get("calendar_id") or "")))
    next_transition = transitions[0] if transitions else None
    confirmed_phase = [row for row in active_periods if _timeline_confirmed(row)]
    known_until = max((str(row.get("end_date") or target.isoformat())[:10] for row in confirmed_phase), default=target.isoformat())
    certainty = "confirmed" if confirmed_phase and not uncertain else "mixed" if confirmed_phase or uncertain else "open"
    return {
        "calendar_version": CALENDAR_VERSION,
        "date": target.isoformat(),
        "timezone": timezone_name,
        "current_phase": active_periods[:8],
        "rhythms": rhythms[:12],
        "today": today_events,
        "recent_changes": recent,
        "upcoming": upcoming,
        "transitions": transitions,
        "next_transition": deepcopy(next_transition) if next_transition else None,
        "uncertainties": uncertain[:16],
        "conflicts": current_snapshot.get("conflicts", []),
        "applied_exceptions": current_snapshot.get("applied_exceptions", []),
        "continuity": {
            "known_until": known_until,
            "next_transition": deepcopy(next_transition) if next_transition else None,
            "certainty": certainty,
            "phase_ids": [str(row.get("calendar_id") or "") for row in confirmed_phase[:8]],
        },
        "generated_at": datetime.now(timezone_or_default(timezone_name)).isoformat(timespec="seconds"),
    }


def calendar_records_from_store(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the additive durable sections without changing legacy fields."""

    if not isinstance(data, dict):
        return []
    records: list[dict[str, Any]] = []
    for key in ("calendar_events", "calendar_rules", "calendar_exceptions"):
        value = data.get(key)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    # A short-lived development format used a unified list; read it for a
    # painless migration while keeping the canonical three sections.
    if isinstance(data.get("calendar_records"), list):
        records.extend(item for item in data["calendar_records"] if isinstance(item, dict))
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        key = str(item.get("calendar_id") or item.get("id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(item)
    return unique


def migrate_calendar_store(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Add calendar sections additively and preserve malformed legacy values."""

    if not isinstance(data, dict):
        raise AgendaContractError("store must be an object")
    changed = False
    defaults: dict[str, Any] = {
        "calendar_version": CALENDAR_VERSION,
        "calendar_events": [],
        "calendar_rules": [],
        "calendar_exceptions": [],
        "calendar_candidates": [],
        "calendar_observations": [],
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = deepcopy(default)
            changed = True
        elif not isinstance(data.get(key), type(default)):
            data[f"legacy_{key}"] = deepcopy(data[key])
            data[key] = deepcopy(default)
            changed = True
    # ``calendar_observations`` was the early name for pending observations.
    # Keep it readable and make the new candidate lane durable without
    # duplicating rows on every startup.
    observations = data.get("calendar_observations")
    candidates = data.get("calendar_candidates")
    if isinstance(observations, list) and isinstance(candidates, list) and observations:
        candidate_ids = {str(item.get("candidate_id") or item.get("calendar_id") or "") for item in candidates if isinstance(item, dict)}
        for item in observations:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("candidate_id") or item.get("calendar_id") or "")
            if item_id and item_id not in candidate_ids:
                candidates.append(deepcopy(item))
                candidate_ids.add(item_id)
                changed = True
    try:
        version = max(0, int(data.get("calendar_version") or 0))
    except (TypeError, ValueError):
        version = 0
    if version < CALENDAR_VERSION:
        data["calendar_version"] = CALENDAR_VERSION
        changed = True
    # Normalize valid rows on load.  Invalid rows remain in a legacy bucket so
    # a bad entry cannot prevent the rest of the plugin from starting.
    for key, kind in (("calendar_events", "event"), ("calendar_rules", "recurrence"), ("calendar_exceptions", "exception")):
        values = data.get(key) if isinstance(data.get(key), list) else []
        normalized: list[dict[str, Any]] = []
        invalid: list[Any] = []
        for raw in values:
            try:
                item = normalize_calendar_record(raw, timezone_name=str(raw.get("timezone") or "Asia/Shanghai")) if isinstance(raw, dict) else None
                if item is None or (kind != item.get("kind") and not (kind == "event" and item.get("kind") == "period")):
                    raise AgendaContractError("calendar record kind mismatch")
                normalized.append(item)
            except AgendaContractError:
                invalid.append(deepcopy(raw))
        if invalid:
            legacy_key = f"legacy_{key}_invalid"
            prior = data.get(legacy_key) if isinstance(data.get(legacy_key), list) else []
            merged = prior + invalid
            if data.get(legacy_key) != merged:
                data[legacy_key] = merged[-100:]
                changed = True
        if normalized != values:
            data[key] = normalized
            changed = True
    legacy_records = data.get("calendar_records")
    if isinstance(legacy_records, list) and legacy_records:
        # Earlier development builds stored one mixed list.  Split it once so
        # incremental section saves persist future edits without duplicating
        # the compatibility list on every read.
        legacy_copy = deepcopy(legacy_records)
        for raw in legacy_records:
            if not isinstance(raw, dict):
                continue
            try:
                normalized = normalize_calendar_record(raw, timezone_name=str(raw.get("timezone") or "Asia/Shanghai"))
            except AgendaContractError:
                continue
            kind = str(normalized.get("kind") or "event")
            section = "calendar_rules" if kind == "recurrence" else "calendar_exceptions" if kind == "exception" else "calendar_events"
            existing_ids = {str(item.get("calendar_id") or "") for item in data.get(section, []) if isinstance(item, dict)}
            if str(normalized.get("calendar_id") or "") not in existing_ids:
                data.setdefault(section, []).append(normalized)
                changed = True
        if data.get("legacy_calendar_records") != legacy_copy:
            data["legacy_calendar_records"] = legacy_copy
            changed = True
        data["calendar_records"] = []
        changed = True
    return data, changed


__all__ = [
    "CALENDAR_VERSION",
    "CALENDAR_RECORD_KINDS",
    "CALENDAR_EVENT_KINDS",
    "CALENDAR_STATUSES",
    "CALENDAR_LIFECYCLE_STATES",
    "CALENDAR_EVIDENCE_KINDS",
    "RECURRENCE_FREQUENCIES",
    "EXCEPTION_ACTIONS",
    "normalize_calendar_evidence",
    "normalize_calendar_evidence_chain",
    "merge_calendar_evidence",
    "normalize_calendar_record",
    "normalize_calendar_records",
    "calendar_candidate_from_record",
    "normalize_calendar_candidate",
    "advance_calendar_lifecycle",
    "calendar_lifecycle_summary",
    "expand_calendar_records",
    "detect_calendar_conflicts",
    "resolve_calendar_snapshot",
    "resolve_calendar_timeline",
    "calendar_records_from_store",
    "migrate_calendar_store",
]
