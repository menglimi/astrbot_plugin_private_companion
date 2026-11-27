"""Morning rhythm: learn the user's routine and run the morning sequence.

The morning chain has three separate decisions that must not collapse into one
fixed alarm:

1. the role wakes up and may say a natural good morning;
2. the user's own wake window is predicted from typed routine samples;
3. a gentle wake-up nudge may follow only while the user has not taken the
   conversation, inside a bounded window, with a hard attempt cap.

Routine samples are derived from typed evidence only: wake observations,
private input timestamps, wake-session receipts and morning-contact receipts.
No message text, no keyword list and no silence-based guess is used.  Every
learned value is a bounded window plus a confidence, and every plan step is a
bounded action with a stable reason code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


DEFAULT_WAKE_MINUTES = 7 * 60
DEFAULT_SLEEP_MINUTES = 23 * 60
DEFAULT_SPREAD_MINUTES = 60
MIN_SAMPLES = 5
WINDOW_DAYS = 60
MAX_WAKE_SPREAD = 90
MAX_SLEEP_SPREAD = 120
RHYTHM_FLOOR = 0.05
RHYTHM_CEILING = 2.0
ACTIVE_WINDOW_FACTOR = 1.15
EDGE_WINDOW_FACTOR = 0.8
RESTING_WINDOW_FACTOR = 0.45


@dataclass(frozen=True)
class RoutineSample:
    """One local day of typed routine evidence; missing parts stay ``None``."""

    local_date: str
    wake_minutes: int | None = None
    sleep_minutes: int | None = None
    morning_contact: bool = False
    replied_after_contact: bool = False
    response_delay_minutes: int | None = None

    def __post_init__(self):
        try:
            day = date.fromisoformat(str(self.local_date))
        except ValueError as error:
            raise ValueError("routine sample requires an ISO local date") from error
        object.__setattr__(self, "local_date", day.isoformat())
        for name in ("wake_minutes", "sleep_minutes"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 1439):
                raise ValueError(f"{name} must be a local minute of day")
        if self.response_delay_minutes is not None and (
                type(self.response_delay_minutes) is not int or self.response_delay_minutes < 0):
            raise ValueError("response delay must be a nonnegative integer")
        if type(self.morning_contact) is not bool or type(self.replied_after_contact) is not bool:
            raise ValueError("morning contact flags must be boolean")

    @property
    def weekday(self):
        return date.fromisoformat(self.local_date).weekday()

    @property
    def is_weekend(self):
        return self.weekday >= 5


@dataclass(frozen=True)
class WindowEstimate:
    """A learned local-time window with its sample count and confidence."""

    center_minutes: int
    spread_minutes: int
    samples: int
    confidence: float
    source: str

    def document(self):
        return {"center_minutes": self.center_minutes, "spread_minutes": self.spread_minutes,
                "samples": self.samples, "confidence": self.confidence, "source": self.source}


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _confidence(samples, spread, *, minimum=MIN_SAMPLES, ceiling):
    volume = min(1.0, samples / float(max(1, minimum)))
    stability = max(0.0, 1.0 - min(1.0, spread / float(max(1, ceiling))))
    return round(volume * (0.6 + 0.4 * stability), 3)


def _wake_estimate(values, *, source):
    center = _median(values)
    if center is None:
        return WindowEstimate(DEFAULT_WAKE_MINUTES, DEFAULT_SPREAD_MINUTES, 0, 0.0, "default")
    spread = _median([abs(value - center) for value in values]) or 0
    spread = min(MAX_WAKE_SPREAD, int(spread))
    return WindowEstimate(int(center), spread, len(values),
                          _confidence(len(values), spread, ceiling=MAX_WAKE_SPREAD), source)


def _night_center(values):
    shifted = [(value - 720) % 1440 for value in values]
    center = _median(shifted)
    return None if center is None else (center + 720) % 1440


def _night_spread(values, center):
    return _median([min((value - center) % 1440, (center - value) % 1440)
                    for value in values]) or 0


def _sleep_estimate(values, *, source):
    center = _night_center(values)
    if center is None:
        return WindowEstimate(DEFAULT_SLEEP_MINUTES, DEFAULT_SPREAD_MINUTES, 0, 0.0, "default")
    spread = min(MAX_SLEEP_SPREAD, int(_night_spread(values, center)))
    return WindowEstimate(int(center), spread, len(values),
                          _confidence(len(values), spread, ceiling=MAX_SLEEP_SPREAD), source)

@dataclass(frozen=True)
class RoutineProfile:
    weekday_wake: WindowEstimate
    weekend_wake: WindowEstimate
    weekday_sleep: WindowEstimate
    weekend_sleep: WindowEstimate
    response_rate: float
    median_response_minutes: int | None
    sample_days: int
    observed_from: str | None
    observed_to: str | None
    zone: str

    def document(self):
        return {"weekday_wake": self.weekday_wake.document(),
                "weekend_wake": self.weekend_wake.document(),
                "weekday_sleep": self.weekday_sleep.document(),
                "weekend_sleep": self.weekend_sleep.document(),
                "response_rate": self.response_rate,
                "median_response_minutes": self.median_response_minutes,
                "sample_days": self.sample_days,
                "observed_from": self.observed_from,
                "observed_to": self.observed_to,
                "zone": self.zone}



def estimate(samples, *, now, zone="Asia/Shanghai", window_days=WINDOW_DAYS):
    """Estimate the user's local wake/sleep windows from typed routine samples."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("routine estimate requires a timezone-aware time")
    try:
        tz = ZoneInfo(str(zone or "Asia/Shanghai"))
    except Exception as error:
        raise ValueError("routine estimate requires a valid timezone") from error
    today = now.astimezone(tz).date()
    kept = []
    for item in samples or ():
        if not isinstance(item, RoutineSample):
            raise ValueError("routine estimate accepts RoutineSample only")
        age = (today - date.fromisoformat(item.local_date)).days
        if 0 <= age <= max(1, int(window_days)):
            kept.append(item)
    wake_weekday = [item.wake_minutes for item in kept
                    if not item.is_weekend and item.wake_minutes is not None]
    wake_weekend = [item.wake_minutes for item in kept
                    if item.is_weekend and item.wake_minutes is not None]
    sleep_weekday = [item.sleep_minutes for item in kept
                     if not item.is_weekend and item.sleep_minutes is not None]
    sleep_weekend = [item.sleep_minutes for item in kept
                     if item.is_weekend and item.sleep_minutes is not None]
    all_wake = [item.wake_minutes for item in kept if item.wake_minutes is not None]
    all_sleep = [item.sleep_minutes for item in kept if item.sleep_minutes is not None]

    weekday_wake = (_wake_estimate(wake_weekday, source="learned_weekday")
                    if len(wake_weekday) >= 2 else _wake_estimate(all_wake, source="learned_overall"))
    weekend_wake = (_wake_estimate(wake_weekend, source="learned_weekend")
                    if len(wake_weekend) >= 2 else _wake_estimate(all_wake, source="learned_overall"))
    weekday_sleep = (_sleep_estimate(sleep_weekday, source="learned_weekday")
                     if len(sleep_weekday) >= 2 else _sleep_estimate(all_sleep, source="learned_overall"))
    weekend_sleep = (_sleep_estimate(sleep_weekend, source="learned_weekend")
                     if len(sleep_weekend) >= 2 else _sleep_estimate(all_sleep, source="learned_overall"))

    contacted = [item for item in kept if item.morning_contact]
    replied = [item for item in contacted if item.replied_after_contact]
    delays = [item.response_delay_minutes for item in contacted
              if item.response_delay_minutes is not None]
    observed = sorted(item.local_date for item in kept)
    return RoutineProfile(
        weekday_wake=weekday_wake, weekend_wake=weekend_wake,
        weekday_sleep=weekday_sleep, weekend_sleep=weekend_sleep,
        response_rate=round(len(replied) / len(contacted), 3) if contacted else 0.0,
        median_response_minutes=_median(delays),
        sample_days=len(kept),
        observed_from=observed[0] if observed else None,
        observed_to=observed[-1] if observed else None,
        zone=tz.key)


@dataclass(frozen=True)
class WakeWindow:
    """The predicted local wake window for one date."""

    start_at: datetime
    end_at: datetime
    center_at: datetime
    confidence: float
    source: str
    zone: str

    def contains(self, at):
        moment = at.astimezone(ZoneInfo(self.zone))
        return self.start_at <= moment <= self.end_at

    def document(self):
        return {"start_at": self.start_at.isoformat(), "end_at": self.end_at.isoformat(),
                "center_at": self.center_at.isoformat(), "confidence": self.confidence,
                "source": self.source, "zone": self.zone}


def predict_wake_window(profile, *, at, zone=None, confidence_floor=0.2):
    """Turn the learned wake estimate into one bounded window for ``at``'s day."""
    if not isinstance(profile, RoutineProfile):
        raise ValueError("wake window prediction requires a RoutineProfile")
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise ValueError("wake window prediction requires a timezone-aware time")
    try:
        tz = ZoneInfo(str(zone or profile.zone or "Asia/Shanghai"))
    except Exception as error:
        raise ValueError("wake window prediction requires a valid timezone") from error
    local = at.astimezone(tz)
    estimate_value = profile.weekend_wake if local.weekday() >= 5 else profile.weekday_wake
    if estimate_value.samples < 2 or estimate_value.confidence < float(confidence_floor):
        center, spread, source = DEFAULT_WAKE_MINUTES, DEFAULT_SPREAD_MINUTES, "default"
    else:
        center, spread, source = (estimate_value.center_minutes,
                                  estimate_value.spread_minutes, estimate_value.source)
    pad = max(20, min(60, int(spread) * 2))
    start_minute = max(4 * 60, center - pad)
    end_minute = min(14 * 60, center + max(30, pad))
    if end_minute <= start_minute:
        end_minute = start_minute + 30
    midnight = datetime.combine(local.date(), time.min, tzinfo=tz)
    return WakeWindow(
        start_at=midnight + timedelta(minutes=start_minute),
        end_at=midnight + timedelta(minutes=end_minute),
        center_at=midnight + timedelta(minutes=center),
        confidence=estimate_value.confidence if source != "default" else 0.0,
        source=source, zone=tz.key)

def _as_utc(value, *, field="time"):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MorningContact:
    """Receipt of the morning opportunity already opened for this episode."""

    sent_at: datetime | None = None
    user_replied_at: datetime | None = None
    nudge_count: int = 0
    last_nudge_at: datetime | None = None

    def __post_init__(self):
        for name in ("sent_at", "user_replied_at", "last_nudge_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _as_utc(value, field=name))
        if type(self.nudge_count) is not int or self.nudge_count < 0:
            raise ValueError("nudge count must be a nonnegative integer")


@dataclass(frozen=True)
class MorningStep:
    action: str
    reason: str
    window: WakeWindow
    due_at: datetime | None = None

    def document(self):
        return {"action": self.action, "reason": self.reason,
                "due_at": self.due_at.isoformat() if self.due_at else None,
                "window": self.window.document()}


def manual_wake_window(*, at, zone=None, morning_start_minutes=5 * 60, morning_end_minutes=12 * 60):
    """Build a direct WakeWindow from manual morning start/end minutes when learning is off."""
    zone_name = zone or "Asia/Shanghai"
    tz = ZoneInfo(zone_name)
    local = _as_utc(at, field="wake window time").astimezone(tz)
    start_m = max(0, min(1439, int(morning_start_minutes)))
    end_m = max(0, min(1440, int(morning_end_minutes)))
    if end_m <= start_m:
        end_m = min(1440, start_m + 60)
    day_start = datetime.combine(local.date(), time.min, tzinfo=tz)
    start_at = day_start + timedelta(minutes=start_m)
    end_at = day_start + timedelta(minutes=end_m)
    center_at = start_at + (end_at - start_at) / 2
    return WakeWindow(
        start_at=start_at,
        center_at=center_at,
        end_at=end_at,
        confidence=1.0,
        source="manual",
        zone=zone_name,
    )


def morning_plan(profile, *, at, zone=None, role_woke_at=None, contact=None,
                 user_last_input_at=None, negative_evidence_at=None,
                 nudge_delay_minutes=25, max_nudges=1, role_morning_grace_minutes=180,
                 morning_start_minutes=5 * 60, morning_end_minutes=12 * 60,
                 wake_confidence_floor=0.2, window=None):
    """Plan the next morning step; this function never sends anything."""
    at = _as_utc(at, field="morning plan time")
    if window is None:
        window = predict_wake_window(profile, at=at, zone=zone,
                                     confidence_floor=wake_confidence_floor)
    local = at.astimezone(ZoneInfo(window.zone))
    local_minute = local.hour * 60 + local.minute
    if not int(morning_start_minutes) <= local_minute < int(morning_end_minutes):
        return MorningStep("skip", "outside_wake_window", window)
    if negative_evidence_at is not None:
        negative = _as_utc(negative_evidence_at, field="negative evidence time")
        if at - negative <= timedelta(minutes=45):
            return MorningStep("hold", "recent_sleep_evidence", window)
    if contact is None:
        contact = MorningContact()
    replied = contact.user_replied_at is not None
    if not replied and user_last_input_at is not None and contact.sent_at is not None:
        replied = _as_utc(user_last_input_at, field="user input time") > contact.sent_at
    if replied:
        return MorningStep("skip", "user_engaged", window)
    if contact.sent_at is None:
        earliest = window.start_at if window.source == "manual" else window.start_at - timedelta(minutes=30)
        if at < earliest:
            return MorningStep("hold", "before_wake_window", window, due_at=earliest)
        latest = window.end_at if window.source == "manual" else window.end_at + timedelta(minutes=60)
        if at > latest:
            return MorningStep("skip", "wake_window_closed", window)
        if role_woke_at is None:
            return MorningStep("hold", "role_not_awake", window)
        role_woke = _as_utc(role_woke_at, field="role wake time")
        if at < role_woke:
            return MorningStep("hold", "role_not_awake", window, due_at=role_woke)
        if at - role_woke > timedelta(minutes=max(30, int(role_morning_grace_minutes))):
            return MorningStep("skip", "role_wake_window_elapsed", window)
        return MorningStep("role_morning", "role_woke_in_wake_window", window)
    if at < window.start_at:
        return MorningStep("hold", "before_wake_window", window, due_at=window.start_at)
    if at > window.end_at:
        return MorningStep("skip", "wake_window_closed", window)
    due_at = contact.sent_at + timedelta(minutes=max(1, int(nudge_delay_minutes)))
    if at < due_at:
        return MorningStep("hold", "nudge_delay_not_elapsed", window, due_at=due_at)
    if contact.last_nudge_at is not None and at - contact.last_nudge_at < timedelta(
            minutes=max(1, int(nudge_delay_minutes))):
        return MorningStep("hold", "nudge_cooldown", window,
                           due_at=contact.last_nudge_at + timedelta(
                               minutes=max(1, int(nudge_delay_minutes))))
    if contact.nudge_count >= max(0, int(max_nudges)):
        return MorningStep("skip", "nudge_budget_exhausted", window)
    return MorningStep("wake_nudge", "no_user_reply_yet", window)


def role_wake_at(source):
    """Read the role's own wake receipt from ``sleep_execution`` or legacy ``body_sleep``.

    Accepts either a full ``role_state.read()`` mapping or a direct sleep
    receipt mapping, preferring ``sleep_execution.ended_at`` and falling back
    to legacy ``body_sleep.last_completed.completed_at``. Never used as
    evidence about the user.
    """
    if not isinstance(source, Mapping):
        return None
    sleep = source
    if "sleep_execution" in source or "body_sleep" in source:
        sleep = source.get("sleep_execution")
        if not isinstance(sleep, Mapping):
            sleep = source.get("body_sleep")
    if not isinstance(sleep, Mapping) or sleep.get("state") != "awake":
        return None
    candidates = [sleep.get("ended_at")]
    last = sleep.get("last_completed")
    if isinstance(last, Mapping):
        candidates.append(last.get("completed_at"))
    for value in candidates:
        if not isinstance(value, str) or not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            continue
        return parsed.astimezone(timezone.utc)
    return None

def _in_window(minute, start, end):
    minute, start, end = minute % 1440, start % 1440, end % 1440
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _edge_distance(minute, start, end, width=60):
    minute, start, end = minute % 1440, start % 1440, end % 1440
    forward = (minute - start) % 1440
    backward = (end - minute) % 1440
    return min(forward, backward) < width


@dataclass(frozen=True)
class RhythmAdjustment:
    """Bounded scaling of the existing hour curve from the learned routine."""

    active_start_minutes: int
    active_end_minutes: int
    hour_factors: tuple
    confidence: float
    source: str

    def apply(self, curve):
        if not isinstance(curve, (tuple, list)) or len(curve) != 24:
            raise ValueError("rhythm adjustment requires the existing 24-hour curve")
        return tuple(round(max(RHYTHM_FLOOR, min(RHYTHM_CEILING, float(value) * self.hour_factors[index])), 3)
                     for index, value in enumerate(curve))

    def document(self):
        return {"active_start_minutes": self.active_start_minutes,
                "active_end_minutes": self.active_end_minutes,
                "hour_factors": list(self.hour_factors),
                "confidence": self.confidence, "source": self.source}


def rhythm_adjustment(profile):
    """Shift weight toward the learned awake window, with hard bounds."""
    if not isinstance(profile, RoutineProfile):
        raise ValueError("rhythm adjustment requires a RoutineProfile")
    wake = profile.weekday_wake
    sleep = profile.weekday_sleep
    if wake.samples < 2 or wake.confidence < 0.2:
        wake_center, source = DEFAULT_WAKE_MINUTES, "default"
    else:
        wake_center, source = wake.center_minutes, wake.source
    if sleep.samples < 2 or sleep.confidence < 0.2:
        sleep_center = DEFAULT_SLEEP_MINUTES
    else:
        sleep_center = sleep.center_minutes
    active_start = (wake_center - 30) % 1440
    active_end = (sleep_center - 60) % 1440
    factors = []
    for hour in range(24):
        minute = hour * 60
        if _in_window(minute, active_start, active_end):
            factor = ACTIVE_WINDOW_FACTOR
        elif _edge_distance(minute, active_start, active_end):
            factor = EDGE_WINDOW_FACTOR
        else:
            factor = RESTING_WINDOW_FACTOR
        factors.append(round(factor, 3))
    return RhythmAdjustment(active_start, active_end, tuple(factors),
                            min(wake.confidence, sleep.confidence), source)


def morning_anchor_recommendation(profile):
    """A bounded ``HH:MM-HH:MM`` suggestion for the existing morning anchor."""
    if not isinstance(profile, RoutineProfile):
        return ""
    wake = profile.weekday_wake
    if wake.samples < 2 or wake.confidence < 0.35:
        return ""
    start = max(4 * 60, wake.center_minutes - 45)
    end = min(12 * 60, wake.center_minutes + 75)
    if end <= start:
        return ""
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


MORNING_INSTRUCTION = (
    "这是角色自己醒来后的可选早安机会。先用角色自己的状态、当天语境或一个自然的话头开口，"
    "不要求对方已经醒着，也不索取回应。不要从时间推断对方睡眠、不要报时、不要把它写成打卡，"
    "不要提设备、观察、叫醒记录或任何内部判断。"
)
NUDGE_INSTRUCTION = (
    "这是一次轻量的起床确认机会，不是催促打卡。只在对方还没有接过话时轻轻问一句是否已经起来，"
    "不重复叫醒、不施压、不制造内疚、不连续追问；对方没回就不再发；对方回了就自然转入聊天。"
    "不要提设备、观察、叫醒记录或任何内部判断，也不要说自己一直在看着对方。"
)
