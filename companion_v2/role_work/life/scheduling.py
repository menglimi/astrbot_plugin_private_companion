"""Smooth per-role bedtime variation and sleep-rhythm projection."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from companion_v2.execution.models import digest

_SMOOTHING_DAYS = 32
_SMOOTHING_RATE = 0.28
SLEEPY_MINUTES = 60
WIND_DOWN_MINUTES = 20
WAKE_RECOVERY_MINUTES = 30


def _daily_target(owner, local_day: date, maximum_minutes: int) -> int:
    if maximum_minutes <= 0:
        return 0
    seed = digest([owner.installation_id, owner.bot_id, owner.persona_id, local_day.isoformat()])
    return int(seed[:12], 16) % (maximum_minutes * 2 + 1) - maximum_minutes


def variation_for(owner, local_day: date, maximum_minutes: int) -> int:
    """Return a stable offset with enough day-to-day inertia to feel like a rhythm."""
    if maximum_minutes <= 0:
        return 0
    day = local_day - timedelta(days=_SMOOTHING_DAYS)
    offset = _daily_target(owner, day, maximum_minutes)
    for step in range(_SMOOTHING_DAYS - 1, -1, -1):
        target = _daily_target(owner, day + timedelta(days=_SMOOTHING_DAYS - step), maximum_minutes)
        offset = round(offset + _SMOOTHING_RATE * (target - offset))
    return max(-maximum_minutes, min(maximum_minutes, offset))


def bedtime_at(owner, local_day: date, timezone_id: str, local_time: time, maximum_minutes: int) -> datetime:
    offset = variation_for(owner, local_day, maximum_minutes)
    local = datetime.combine(local_day, local_time) + timedelta(minutes=offset)
    return local.replace(tzinfo=ZoneInfo(timezone_id)).astimezone(timezone.utc)


def next_bedtime(now: datetime, owner, timezone_id: str, local_time: time, maximum_minutes: int) -> datetime:
    zone = ZoneInfo(timezone_id)
    # A late bedtime from the previous date can fall after midnight.
    day = (now.astimezone(zone) - timedelta(minutes=maximum_minutes)).date()
    while True:
        candidate = bedtime_at(owner, day, timezone_id, local_time, maximum_minutes)
        if candidate >= now:
            return candidate
        day += timedelta(days=1)


def sleep_due_window_expired(now: datetime, next_sleep: datetime, *, window_minutes: int, sleep_minutes: int) -> bool:
    """Return True when a missed bedtime is far enough in the past that starting night sleep now would be stale."""
    grace_minutes = max(int(window_minutes), 60) + max(int(sleep_minutes) // 2, 120)
    return now >= next_sleep + timedelta(minutes=grace_minutes)


def sleep_schedule_summary(settings: dict | None) -> dict | None:
    """Project role.life sleep settings into a compact prompt-ready schedule summary."""
    if not isinstance(settings, dict):
        return None
    sleep_time = settings.get("sleep_time")
    sleep_timezone = settings.get("sleep_timezone")
    sleep_minutes = settings.get("sleep_minutes")
    if not isinstance(sleep_time, str) or not isinstance(sleep_timezone, str) or type(sleep_minutes) is not int:
        return None
    try:
        parsed = time.fromisoformat(sleep_time)
    except ValueError:
        return None
    start_minutes = parsed.hour * 60 + parsed.minute
    wake_minutes = (start_minutes + max(1, min(1440, sleep_minutes))) % (24 * 60)
    expected_wake = f"{wake_minutes // 60:02d}:{wake_minutes % 60:02d}"
    return {
        "enabled": bool(settings.get("sleep_enabled", False)),
        "sleep_time": sleep_time,
        "timezone": sleep_timezone,
        "duration_minutes": sleep_minutes,
        "expected_wake_time": expected_wake,
    }


def sleep_rhythm(now: datetime, next_sleep_at: datetime | None, body_sleep: dict | None) -> dict:
    """Describe a gentle phase without treating a projected time as a sleep receipt."""
    if body_sleep and body_sleep.get("state") == "sleeping":
        return {"phase": "sleeping", "basis": "execution_receipt",
                "since": body_sleep.get("started_at"), "until": body_sleep.get("planned_wake_at"),
                "sleep_pressure": body_sleep.get("sleep_pressure"),
                "sleep_debt_minutes": body_sleep.get("sleep_debt_minutes", 0),
                "sleep_quality_range": body_sleep.get("sleep_quality_range"),
                "interruptions": body_sleep.get("interruptions", 0)}

    if body_sleep and body_sleep.get("state") == "interrupted":
        return {"phase": "interrupted", "basis": "execution_receipt",
                "since": body_sleep.get("started_at"), "until": body_sleep.get("wake_until"),
                "sleep_pressure": body_sleep.get("sleep_pressure"),
                "sleep_debt_minutes": body_sleep.get("sleep_debt_minutes", 0),
                "sleep_quality_range": body_sleep.get("sleep_quality_range"),
                "interruptions": body_sleep.get("interruptions", 0)}

    sleep_debt = (body_sleep.get("sleep_debt_minutes", 0) if body_sleep and "sleep_debt_minutes" in body_sleep
                  else _sleep_debt_minutes(body_sleep))
    ended_at = _parse_stamp(body_sleep.get("ended_at")) if body_sleep else None
    if ended_at is not None:
        elapsed = (now - ended_at).total_seconds() / 60
        if 0 <= elapsed < WAKE_RECOVERY_MINUTES:
            return {"phase": "recovering", "basis": "execution_receipt",
                    "since": body_sleep.get("ended_at"),
                    "until": (ended_at + timedelta(minutes=WAKE_RECOVERY_MINUTES)).isoformat(),
                    "sleep_debt_minutes": sleep_debt}

    if next_sleep_at is not None:
        remaining = (next_sleep_at - now).total_seconds() / 60
        if 0 <= remaining <= WIND_DOWN_MINUTES:
            phase = "winding_down"
        elif WIND_DOWN_MINUTES < remaining <= SLEEPY_MINUTES + min(60, sleep_debt):
            phase = "sleepy"
        else:
            phase = "awake"
        return {"phase": phase, "basis": "schedule_projection",
                "until": next_sleep_at.isoformat() if phase != "awake" else None,
                "sleep_debt_minutes": sleep_debt,
                "sleep_pressure": body_sleep.get("sleep_pressure") if body_sleep else None}
    return {"phase": "awake", "basis": "schedule_projection", "until": None,
            "sleep_debt_minutes": sleep_debt,
            "sleep_pressure": body_sleep.get("sleep_pressure") if body_sleep else None}


def _sleep_debt_minutes(body_sleep):
    if not body_sleep or body_sleep.get("state") != "awake":
        return 0
    started_at = _parse_stamp(body_sleep.get("started_at"))
    ended_at = _parse_stamp(body_sleep.get("ended_at"))
    planned_wake_at = _parse_stamp(body_sleep.get("planned_wake_at"))
    if not started_at or not ended_at or not planned_wake_at or ended_at < started_at:
        return 0
    planned = (planned_wake_at - started_at).total_seconds() / 60
    actual = (ended_at - started_at).total_seconds() / 60
    return max(0, round(planned - actual))


def _parse_stamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
