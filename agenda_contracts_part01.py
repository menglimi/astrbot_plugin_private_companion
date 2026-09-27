# -*- coding: utf-8 -*-
"""agenda_contracts_part01：从 agenda_contracts.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（36 个函数 / 362 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 agenda_contracts.py re-export，接口不变。
"""
from __future__ import annotations

try:  # package import
    from .agenda_contracts_shared import (
        ACTOR_TYPES,
        AGENDA_STATUSES,
        AUTHORITY_KINDS,
        AgendaContractError,
        Any,
        COMMITMENT_LEVELS,
        CONTENT_GRANULARITIES,
        EPISTEMIC_STATUSES,
        EVIDENCE_KINDS,
        EVIDENCE_LEVELS,
        FACT_ELIGIBILITIES,
        MATERIALIZATION_STATES,
        Real,
        SCHEDULE_WINDOWS,
        SOURCE_KINDS,
        STATUS_ALIASES,
        TEMPORAL_PHASES,
        WINDOW_SLUGS,
        _contract_window_for_minutes,
        date,
        datetime,
        deepcopy,
        time,
        timedelta,
        timezone_or_default,
    )
except ImportError:  # direct test/import from the plugin directory
    from agenda_contracts_shared import (
        ACTOR_TYPES,
        AGENDA_STATUSES,
        AUTHORITY_KINDS,
        AgendaContractError,
        Any,
        COMMITMENT_LEVELS,
        CONTENT_GRANULARITIES,
        EPISTEMIC_STATUSES,
        EVIDENCE_KINDS,
        EVIDENCE_LEVELS,
        FACT_ELIGIBILITIES,
        MATERIALIZATION_STATES,
        Real,
        SCHEDULE_WINDOWS,
        SOURCE_KINDS,
        STATUS_ALIASES,
        TEMPORAL_PHASES,
        WINDOW_SLUGS,
        _contract_window_for_minutes,
        date,
        datetime,
        deepcopy,
        time,
        timedelta,
        timezone_or_default,
    )


def _text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]

def _list(value: Any, limit: int = 30) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, 200)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result

def _items(value: Any, limit: int = 40) -> list[Any]:
    if not isinstance(value, list):
        return []
    return deepcopy(value[:limit])

def _trace(code: str, message: str = "", **details: Any) -> dict[str, Any]:
    """Create a compact, JSON-safe normalizer decision record."""

    result: dict[str, Any] = {"code": _text(code, 96)}
    if message:
        result["message"] = _text(message, 240)
    for key, value in details.items():
        if value is not None:
            result[key] = deepcopy(value)
    return result

def _trace_list(value: Any) -> list[dict[str, Any]]:
    """Keep old trace values readable while normalizing new trace entries."""

    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:50]:
        if isinstance(item, dict):
            result.append(deepcopy(item))
        elif _text(item, 240):
            result.append(_trace("legacy_trace", _text(item, 240)))
    return result

def _enum(value: Any, choices: set[str], default: str) -> str:
    candidate = _text(value, 64).lower()
    return candidate if candidate in choices else default

def _float_confidence(value: Any, default: float = 0.5) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(0.0, min(1.0, number))

def _certainty(value: Any, default: str = "medium") -> Any:
    """Preserve legacy numeric certainty values exactly as numbers."""

    if isinstance(value, Real) and not isinstance(value, bool):
        return value
    return _text(value, 24) or default

def _normalize_participant_roles(value: Any, participants: Any = None) -> list[Any]:
    """Normalize role entries without collapsing actor IDs into one user field."""

    value = value if value is not None else participants
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            role = deepcopy(item)
            if role.get("actor_id") is not None:
                role["actor_id"] = _text(role.get("actor_id"), 120)
            if role.get("actor_type") is not None:
                role["actor_type"] = _enum(role.get("actor_type"), ACTOR_TYPES, "external_party")
            if role.get("role") is not None:
                role["role"] = _text(role.get("role"), 64)
            result.append(role)
        else:
            item_text = _text(item, 120)
            if item_text and item_text not in result:
                result.append(item_text)
        if len(result) >= 30:
            break
    return result

def _actor_fields(raw: dict[str, Any], *, default_source_actor: str = "system") -> dict[str, Any]:
    actor_type = _enum(raw.get("actor_type"), ACTOR_TYPES, "")
    subject_actor_id = _text(raw.get("subject_actor_id") or raw.get("subject_id"), 120)
    bot_id = _text(raw.get("bot_id"), 120)
    if not actor_type and bot_id:
        actor_type = "bot"
    if actor_type == "bot" and not subject_actor_id:
        subject_actor_id = bot_id
    source_actor_id = _text(raw.get("source_actor_id"), 120) or default_source_actor
    return {
        "actor_type": actor_type,
        "subject_actor_id": subject_actor_id,
        "object_actor_id": _text(raw.get("object_actor_id"), 120),
        "source_actor_id": source_actor_id,
        "target_user_id": _text(raw.get("target_user_id"), 120),
        "participant_roles": _normalize_participant_roles(raw.get("participant_roles"), raw.get("participants")),
    }

def _evidence_level_mapping(level: str) -> dict[str, Any]:
    canonical = normalize_evidence_level(level, "L0")
    archive = canonical if canonical in {"L0", "L1", "L2", "L3"} else "L3"
    return {
        "canonical_evidence_level": canonical,
        "archive_evidence_level": archive,
        "lossy": canonical != archive,
    }

def _version(value: Any, default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default

def _now_iso(now: datetime | None = None) -> str:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone_or_default("Asia/Shanghai"))
    return current.isoformat(timespec="seconds")

def normalize_window(value: Any) -> str:
    candidate = _text(value, 48).lower()
    return candidate if candidate in WINDOW_SLUGS else ""

def window_for_minutes(minutes: Any) -> str:
    """Delegate minute classification to the shared chat-side contract."""

    try:
        return _contract_window_for_minutes(int(minutes))
    except (TypeError, ValueError):
        return ""

def _window_spec(window: Any) -> tuple[str, str, int, int]:
    slug = normalize_window(window)
    for item in SCHEDULE_WINDOWS:
        if item[0] == slug:
            return item
    raise AgendaContractError(f"unknown window: {window!r}")

def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value, 32))
    except ValueError as exc:
        raise AgendaContractError(f"invalid date: {value!r}") from exc

def parse_datetime(value: Any, *, timezone_name: str = "Asia/Shanghai", default: datetime | None = None) -> datetime:
    """Parse ISO/date/time values and attach the requested local timezone."""

    tz = timezone_or_default(timezone_name)
    if isinstance(value, datetime):
        current = value
    elif isinstance(value, date):
        current = datetime.combine(value, time.min)
    else:
        text = _text(value, 96)
        if not text:
            if default is None:
                raise AgendaContractError("datetime is required")
            current = default
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                current = datetime.fromisoformat(text)
            except ValueError:
                try:
                    current = datetime.strptime(text, "%H:%M")
                except ValueError as exc:
                    raise AgendaContractError(f"invalid datetime: {value!r}") from exc
    if current.tzinfo is None:
        return current.replace(tzinfo=tz)
    return current.astimezone(tz)

def window_bounds(
    window_date: str | date,
    window: str,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> tuple[datetime, datetime]:
    """Return inclusive-start/exclusive-end aware bounds for a window date."""

    _slug, _name, start_minute, end_minute = _window_spec(window)
    target = _as_date(window_date)
    tz = timezone_or_default(timezone_name)
    start = datetime.combine(target, time.min, tzinfo=tz) + timedelta(minutes=start_minute)
    end_date = target + timedelta(days=1) if end_minute <= start_minute else target
    end = datetime.combine(end_date, time.min, tzinfo=tz) + timedelta(minutes=end_minute)
    return start, end

def window_for_datetime(
    value: datetime | date | str,
    timezone_name: str = "Asia/Shanghai",
) -> tuple[str, str, datetime, datetime]:
    """Resolve a moment to ``(slug, window_date, start, end)``.

    The early-morning part of ``late_night`` belongs to the preceding
    ``window_date``.  Minute classification always goes through the shared
    ``bot_personal_contract.window_for_minutes`` implementation.
    """

    current = parse_datetime(value, timezone_name=timezone_name)
    minute = current.hour * 60 + current.minute
    slug = window_for_minutes(minute)
    if not slug:
        raise AgendaContractError(f"no window for minute: {minute}")
    _slug, _name, start_minute, end_minute = _window_spec(slug)
    belongs_to_previous_date = end_minute <= start_minute and minute < end_minute
    target_date = current.date() - timedelta(days=1) if belongs_to_previous_date else current.date()
    start, end = window_bounds(target_date, slug, timezone_name=timezone_name)
    return slug, target_date.isoformat(), start, end

def window_for_plan_minutes(
    plan_date: str | date,
    minutes: Any,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> tuple[str, str]:
    """Resolve a plan date plus possibly out-of-range minute offset."""

    try:
        base = _as_date(plan_date)
        value = int(minutes)
    except (TypeError, ValueError, AgendaContractError):
        return "", ""
    day_offset, raw_minute = divmod(value, 24 * 60)
    moment = datetime.combine(base + timedelta(days=day_offset), time.min)
    moment += timedelta(minutes=raw_minute)
    slug, target_date, _start, _end = window_for_datetime(moment, timezone_name=timezone_name)
    return slug, target_date

def interval_overlaps_window(
    item: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> bool:
    """Return whether an item interval intersects ``[start, end)``."""

    if not isinstance(item, dict):
        return False
    # Legacy daily-plan rows carry ``date`` plus bare ``time``/``end`` clocks.
    # Resolve them through the same helper used by temporal-phase derivation so
    # a 00:30 item remains attached to the preceding late-night window.
    item_start = _item_datetime(
        item,
        ("start_at", "start", "starts_at", "time"),
        timezone_name=timezone_name,
    )
    if item_start is None:
        return False
    item_end = _item_datetime(
        item,
        ("end_at", "end", "ends_at", "end_time"),
        timezone_name=timezone_name,
        default=item_start,
    )
    if item_end is None:
        item_end = item_start
    if item_end <= item_start:
        raw_start = _text(item.get("start_at") or item.get("start") or item.get("time"), 96)
        raw_end = _text(item.get("end_at") or item.get("end") or item.get("end_time"), 96)
        start_clock = raw_start.rsplit("T", 1)[-1][:5] if ":" in raw_start else ""
        end_clock = raw_end.rsplit("T", 1)[-1][:5] if ":" in raw_end else ""
        if start_clock and end_clock and end_clock <= start_clock:
            item_end = item_end + timedelta(days=1)
        else:
            item_end = item_start + timedelta(seconds=1)
    start_local = parse_datetime(start, timezone_name=timezone_name)
    end_local = parse_datetime(end, timezone_name=timezone_name)
    return item_start < end_local and item_end > start_local

def _item_datetime(
    item: dict[str, Any],
    keys: tuple[str, ...],
    *,
    timezone_name: str = "Asia/Shanghai",
    default: datetime | None = None,
) -> datetime | None:
    """Resolve an item time, combining legacy ``date`` plus clock fields."""

    value: Any = None
    for key in keys:
        if item.get(key) not in (None, ""):
            value = item.get(key)
            break
    if value in (None, ""):
        return default
    text = _text(value, 96)
    # A bare clock must use the plan's date when one is available.  Parsing it
    # against today's date would make old daily-plan payloads drift silently.
    if len(text) <= 8 and ":" in text and "T" not in text and "-" not in text:
        date_text = _text(item.get("date") or item.get("window_date"), 32)
        if date_text:
            text = f"{date_text}T{text}"
    try:
        return parse_datetime(text, timezone_name=timezone_name, default=default)
    except AgendaContractError:
        return default

def derive_temporal_phase(
    item: dict[str, Any],
    now: datetime | None = None,
    *,
    timezone_name: str = "Asia/Shanghai",
) -> str:
    """Derive ``future/current/past`` from time bounds only.

    Missing time is conservatively treated as future for a plan (it can still
    be shown by a future-schedule view) and does not create execution evidence.
    """

    if not isinstance(item, dict):
        return "future"
    current = parse_datetime(now or datetime.now().astimezone(), timezone_name=timezone_name)
    start = _item_datetime(item, ("start_at", "start", "starts_at", "time"), timezone_name=timezone_name)
    if start is None:
        existing = _enum(item.get("temporal_phase"), TEMPORAL_PHASES, "")
        return existing or "future"
    end = _item_datetime(item, ("end_at", "end", "ends_at", "end_time"), timezone_name=timezone_name, default=start)
    if end is None or end <= start:
        raw_start = _text(item.get("start_at") or item.get("start") or item.get("time"), 96)
        raw_end = _text(item.get("end_at") or item.get("end") or item.get("end_time"), 96)
        start_clock = raw_start.rsplit("T", 1)[-1][:5] if ":" in raw_start else ""
        end_clock = raw_end.rsplit("T", 1)[-1][:5] if ":" in raw_end else ""
        if start_clock and end_clock and end_clock <= start_clock:
            end = end + timedelta(days=1) if end is not None else start + timedelta(days=1)
        else:
            end = start + timedelta(seconds=1)
    current = current.astimezone(start.tzinfo)
    if current < start:
        return "future"
    if current >= end:
        return "past"
    return "current"

def normalize_temporal_phase(value: Any, default: str = "future") -> str:
    return _enum(value, TEMPORAL_PHASES, default)

def normalize_source_kind(value: Any, default: str) -> str:
    candidate = _text(value, 32).lower()
    return candidate if candidate in SOURCE_KINDS else default

def normalize_evidence_level(value: Any, default: str) -> str:
    candidate = _text(value, 8).upper()
    return candidate if candidate in EVIDENCE_LEVELS else default

def _normalize_status(value: Any, default: str) -> str:
    candidate = _text(value, 32).lower()
    candidate = STATUS_ALIASES.get(candidate, candidate)
    return candidate if candidate in AGENDA_STATUSES else default

def normalize_status(value: Any, default: str = "unknown") -> str:
    """Public status adapter shared by disclosure and compatibility readers."""

    return _normalize_status(value, default)

def normalize_evidence_kind(value: Any, default: str = "none") -> str:
    return _enum(value, EVIDENCE_KINDS, default)

def normalize_authority_kind(value: Any, default: str = "llm") -> str:
    return _enum(value, AUTHORITY_KINDS, default)

def normalize_commitment_level(value: Any, default: str = "tentative") -> str:
    return _enum(value, COMMITMENT_LEVELS, default)

def normalize_epistemic_status(value: Any, default: str = "inferred") -> str:
    return _enum(value, EPISTEMIC_STATUSES, default)

def normalize_content_granularity(value: Any, default: str = "intent") -> str:
    return _enum(value, CONTENT_GRANULARITIES, default)

def normalize_materialization_state(value: Any, default: str = "none") -> str:
    return _enum(value, MATERIALIZATION_STATES, default)

def normalize_fact_eligibility(value: Any, default: str = "none") -> str:
    return _enum(value, FACT_ELIGIBILITIES, default)
