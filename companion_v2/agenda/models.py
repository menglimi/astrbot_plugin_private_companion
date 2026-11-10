"""Explicit ownership, calendar anchors, and local-only execution results."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef, normalize_utc
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity


OPEN_ITEM_TTL = timedelta(days=30)
OPEN_ITEM_AFTER_DUE_GRACE = timedelta(days=7)
OPEN_ITEM_MAX_LIFETIME = timedelta(days=180)


class AgendaError(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        super().__init__(reason)


def opaque(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        raise AgendaError("invalid_agenda_id", "identifier must be nonblank and at most 512 characters")
    return value


def encode(value: object) -> str:
    def default(item: object):
        if isinstance(item, ScopeRef):
            return item.document()
        if isinstance(item, AuthenticatedHostPrincipal):
            return {field.name: getattr(item, field.name) for field in fields(item) if getattr(item, field.name) is not None}
        if is_dataclass(item):
            return {field.name: getattr(item, field.name) for field in fields(item)}
        if isinstance(item, datetime):
            return normalize_utc(item, field="timestamp").isoformat()
        raise TypeError(f"unsupported agenda value: {type(item).__name__}")
    return json.dumps(value, default=default, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def stamp(value: datetime) -> str:
    return normalize_utc(value, field="timestamp").isoformat(timespec="microseconds")


def open_item_expiry(created_at: datetime, schedule: Schedule | None = None, sequence: int = 0) -> datetime:
    created_at = normalize_utc(created_at, field="created_at")
    due_at = schedule.at(sequence) if schedule is not None else None
    expiry = (due_at + OPEN_ITEM_AFTER_DUE_GRACE
              if due_at is not None and due_at > created_at else created_at + OPEN_ITEM_TTL)
    return min(expiry, created_at + OPEN_ITEM_MAX_LIFETIME)


def restore_identity(data: dict) -> ResolvedIdentity:
    return ResolvedIdentity(
        AuthenticatedHostPrincipal(**data["principal"]),
        SubjectRef(data["subject"]["installation_id"], SubjectKind(data["subject"]["kind"]), data["subject"]["subject_id"]),
        data["binding_id"], data["binding_revision"], data["policy_revision"],
    )


@dataclass(frozen=True)
class AgendaTarget:
    scope: ScopeRef
    role: SubjectRef
    owner: SubjectRef

    def __post_init__(self) -> None:
        if (self.role.kind is not SubjectKind.BOT or self.owner.kind is not SubjectKind.HUMAN
                or self.scope.installation_id != self.role.installation_id
                or self.scope.installation_id != self.owner.installation_id):
            raise AgendaError("invalid_agenda_target", "target requires a role and human owner in the same installation")

    @property
    def key(self) -> str:
        return fingerprint(self)

    @classmethod
    def restore(cls, data: dict) -> AgendaTarget:
        scope = data["scope"]
        return cls(
            ScopeRef.restore(scope),
            SubjectRef(data["role"]["installation_id"], SubjectKind.BOT, data["role"]["subject_id"]),
            SubjectRef(data["owner"]["installation_id"], SubjectKind.HUMAN, data["owner"]["subject_id"]),
        )


class Frequency(str, Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class GapPolicy(str, Enum):
    REJECT = "reject"
    SHIFT_FORWARD = "shift_forward"


@dataclass(frozen=True)
class Schedule:
    anchor: datetime
    timezone_name: str
    frequency: Frequency = Frequency.ONCE
    interval: int = 1
    count: int | None = None
    fold: int = 0
    gap_policy: GapPolicy = GapPolicy.REJECT

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor", normalize_utc(self.anchor, field="schedule anchor"))
        if (not isinstance(self.frequency, Frequency) or not isinstance(self.gap_policy, GapPolicy)
                or type(self.interval) is not int or self.interval < 1
                or (self.count is not None and (type(self.count) is not int or self.count < 1))
                or type(self.fold) is not int or self.fold not in (0, 1)):
            raise AgendaError("invalid_schedule", "recurrence requires positive intervals/counts and an explicit time policy")
        self.zone()

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(opaque(self.timezone_name))
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise AgendaError("timezone_unavailable", "timezone is unknown or its database is unavailable") from error

    def at(self, sequence: int) -> datetime | None:
        if type(sequence) is not int or sequence < 0:
            raise AgendaError("invalid_occurrence", "sequence must be a nonnegative integer")
        if ((self.count is not None and sequence >= self.count)
                or (self.frequency is Frequency.ONCE and sequence > 0)):
            return None
        if sequence == 0:
            return self.anchor
        zone = self.zone()
        local = self.anchor.astimezone(zone).replace(tzinfo=None)
        delta = sequence * self.interval
        try:
            if self.frequency in (Frequency.DAILY, Frequency.WEEKLY):
                wall = local + timedelta(days=delta * (7 if self.frequency is Frequency.WEEKLY else 1))
            else:
                months = delta * (12 if self.frequency is Frequency.YEARLY else 1)
                year, month_zero = divmod(local.year * 12 + local.month - 1 + months, 12)
                month = month_zero + 1
                wall = local.replace(year=year, month=month, day=min(local.day, monthrange(year, month)[1]))
        except (ValueError, OverflowError) as error:
            raise AgendaError("schedule_out_of_range", "next occurrence is outside the supported calendar") from error
        candidate = wall.replace(tzinfo=zone, fold=self.fold).astimezone(timezone.utc)
        roundtrip = candidate.astimezone(zone).replace(tzinfo=None)
        if roundtrip != wall:
            if self.gap_policy is GapPolicy.REJECT:
                raise AgendaError("schedule_needs_resolution", "next local time does not exist under the selected timezone")
            choices = [wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)]
            candidate = min(item for item in choices if item.astimezone(zone).replace(tzinfo=None) > wall)
        return candidate

    def first_at_or_after(self, instant: datetime, *, minimum: int = 0) -> int | None:
        """Jump from the original calendar anchor; never walk every missed occurrence."""
        instant = normalize_utc(instant, field="schedule lower bound")
        if type(minimum) is not int or minimum < 0:
            raise AgendaError("invalid_occurrence", "minimum sequence must be nonnegative")
        anchor, local = self.anchor.astimezone(self.zone()), instant.astimezone(self.zone())
        if self.frequency is Frequency.ONCE:
            estimate = 0
        elif self.frequency in (Frequency.DAILY, Frequency.WEEKLY):
            step = self.interval * (7 if self.frequency is Frequency.WEEKLY else 1)
            estimate = (local.date() - anchor.date()).days // step
        else:
            step = self.interval * (12 if self.frequency is Frequency.YEARLY else 1)
            estimate = ((local.year - anchor.year) * 12 + local.month - anchor.month) // step
        sequence = max(minimum, estimate, 0)
        due = self.at(sequence)
        if due is not None and due < instant:
            sequence += 1
            due = self.at(sequence)
        return sequence if due is not None else None

    @classmethod
    def restore(cls, data: dict | None) -> Schedule | None:
        if data is None:
            return None
        if type(data) is not dict:
            raise AgendaError("invalid_schedule", "schedule must be an object")
        fold = data.get("fold", 0)
        # JSON false/true is a common calendar-tool spelling for fold 0/1; keep
        # the stored contract integer-only but accept the boolean spelling.
        if type(fold) is bool:
            fold = int(fold)
        return cls(datetime.fromisoformat(data["anchor"]), data["timezone_name"],
                   Frequency(data.get("frequency", "once")), data.get("interval", 1),
                   data.get("count"), fold, GapPolicy(data.get("gap_policy", "reject")))


@dataclass(frozen=True)
class Memo:
    memo_id: str
    target: AgendaTarget
    revision: int
    state: str
    title: str
    body: str
    pinned: bool
    schedule: Schedule | None
    sequence: int
    completion_count: int
    creator: ResolvedIdentity
    content_revision: int = 1
    purpose: str = "note"
    expires_at: datetime | None = None
    created_at: datetime | None = None
    last_context_at: datetime | None = None
    last_followup_at: datetime | None = None
    context_count: int = 0
    followup_count: int = 0

    def __post_init__(self) -> None:
        if self.purpose not in ("note", "open_loop"):
            raise AgendaError("invalid_memo_purpose", "memo purpose must be note or open_loop")
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", normalize_utc(self.expires_at, field="expires_at"))
        if self.created_at is not None:
            object.__setattr__(self, "created_at", normalize_utc(self.created_at, field="created_at"))
        if self.last_context_at is not None:
            object.__setattr__(self, "last_context_at", normalize_utc(self.last_context_at, field="last_context_at"))
        if self.last_followup_at is not None:
            object.__setattr__(self, "last_followup_at", normalize_utc(self.last_followup_at, field="last_followup_at"))
        if (type(self.context_count) is not int or self.context_count < 0
                or type(self.followup_count) is not int or self.followup_count < 0):
            raise AgendaError("invalid_activity_count", "open-loop activity counters must be nonnegative integers")

    def expired(self, at: datetime) -> bool:
        return (self.state == "active" and self.purpose == "open_loop" and self.expires_at is not None
                and normalize_utc(at, field="observed_at") >= self.expires_at)


@dataclass(frozen=True)
class Reminder:
    reminder_id: str
    target: AgendaTarget
    revision: int
    state: str
    memo_id: str | None
    message: str
    schedule: Schedule
    requester: ResolvedIdentity
    role_binding_revision: int
    authorization_action: str
    sequence: int = 0


@dataclass(frozen=True)
class AgendaReceipt:
    operation_id: str
    action: str
    object_id: str
    revision: int
    state: str
    replayed: bool = False
    scheduling: str = "not_requested"
    delivery: str = "not_started"


@dataclass(frozen=True)
class DueCursor:
    due_at: datetime
    occurrence_id: str


@dataclass(frozen=True)
class MemoCursor:
    pinned: bool
    memo_id: str

    def __post_init__(self) -> None:
        if type(self.pinned) is not bool:
            raise AgendaError("invalid_memo_cursor", "cursor requires an explicit pinned state")
        opaque(self.memo_id)


@dataclass(frozen=True)
class DuePage:
    occurrence_ids: tuple[str, ...]
    next_cursor: DueCursor | None


@dataclass(frozen=True)
class DueClaim:
    occurrence_id: str
    revision: int
    reminder_id: str
    reminder_revision: int
    memo_revision: int | None


@dataclass(frozen=True)
class LocalPreparation:
    occurrence_id: str
    reminder_id: str
    reminder_revision: int
    memo_revision: int | None
    due_at: datetime
    target: AgendaTarget
    recipient: SubjectRef
    message: str
    prepared_at: datetime
    scheduling: str = "local_prepared"
    delivery: str = "not_started"
    continuation: str = "not_applicable"
