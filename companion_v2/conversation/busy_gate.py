"""Busy-hour reply gate: one pure decision plus one bounded calendar read.

V1 delayed a passive reply with ``asyncio.sleep`` inside the event task and then
cancelled that reply when a newer private message arrived. V2 owns its timers in
``conversation_flows``, so the same intent is expressed as a *deferral* the host
adds to ``due_at`` while accepting the input. Nothing here sleeps, queues, seals
or dispatches anything, and nothing here reorders an already dispatched part.

The only input is the character's own accepted ``role_plan`` calendar window
(``calendar_records``). ``role_work/state``'s ``busy`` field and any platform
online status are deliberately not consulted: they are reported material for a
reply, never an automatic reason to withhold one.

The judgement (``busy_context``/``defer_seconds``/``bypass_reason``/
``proactive_defer_until``) is a pure function of the values passed in. The one
side effect is the bounded, read-only query in ``calendar_items``, which uses the
same record ownership key as ``CalendarService`` and grants nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
import random
import re

from companion_v2.agenda.models import AgendaError, Schedule
from companion_v2.agenda.records import CalendarRecords
from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import normalize_utc
from companion_v2.identity_policy.repository import PersistentIdentityDirectory


GROUP = "conversation.busy_gate"
OWNER = "conversation.busy_gate"
LAYERS = (ConfigLayer.ROLE, ConfigLayer.WINDOW)
BUSY_DELAY_INSTRUCTION = (
    "本轮按忙碌日程规则经过了短暂延迟；规则命中只说明调度原因，不证明角色刚忙完、正在忙或用户在忙。"
    "若已注入的角色状态或日程材料体现了手头事务，可在回应用户时顺手自然带出一点刚腾出手、边做事边看手机的"
    "轻量呼吸感，但不要每次生硬套用固定抱歉模板，"
    "不要编造材料中未记录的具体进度或结果，也不要据此敷衍或拒绝回应。"
)
# V1 hard-coded ``min(12, ...)`` for group chats and capped both delays at 900.
# V2 keeps the 900 bound for private passive replies and records the group cap as
# a deliberate design difference (docs/BUSY_REPLY_GATE_SLICE_20260927.md).
MAX_DELAY_SECONDS = 900
MAX_KEYWORDS = 64
MAX_KEYWORD_CHARS = 24
# A busy window with no readable end still pauses proactive contact for this long.
FALLBACK_BUSY_WINDOW_SECONDS = 15 * 60
# One bounded page per decision; the calendar owner reads at most 200 records.
MAX_CALENDAR_RECORDS = 64
# Turn-deadline extension ceiling for one accepted batch; see ``defer_extension``.
MAX_TURN_DEADLINE_EXTENSION_SECONDS = 900
# A batch must keep room for one further quiet window before its own deadline.
DEADLINE_MARGIN_SECONDS = 1.0

# V1 ``BusyReplyGateMixin._BUSY_ACTIVITY_PATTERN`` as literal keywords.
DEFAULT_ACTIVITY_KEYWORDS = (
    "上课", "课堂", "听课", "自习", "学习", "复习", "预习", "写作业", "做作业", "赶作业", "做题",
    "考试", "测验", "开会", "会议", "工作", "办公", "值班", "实习", "训练", "排练", "实验", "赶稿",
    "写稿", "编程", "写代码", "处理事情", "处理任务", "处理工作", "专注", "集中精神", "忙着", "忙碌",
    "忙工作", "忙学习", "通勤", "赶路",
)
# V1 ``BusyReplyGateMixin._BUSY_ACTIVITY_EXCLUSION_PATTERN`` as literal keywords.
DEFAULT_EXCLUDE_KEYWORDS = (
    "睡觉", "睡眠", "午睡", "午休", "补觉", "休息", "发呆", "摸鱼", "放松", "吃饭", "用餐", "散步",
    "刷视频", "看番", "打游戏", "玩游戏", "聊天", "自由时间", "准备睡", "洗漱", "刚醒", "起床",
)
# V1 ``BusyReplyGateMixin._BUSY_URGENT_PATTERN`` as literal keywords.
DEFAULT_URGENT_KEYWORDS = (
    "救命", "出事", "紧急", "急事", "报警", "医院", "受伤", "流血", "疼得", "不舒服", "呼吸困难",
    "害怕", "崩溃", "自杀", "不想活", "马上回", "立刻回", "快回", "现在就回", "十万火急",
)
# V1 bypassed the platform command shape and the "陪伴…" management shape.
COMMAND_PREFIX_PATTERN = re.compile(r"^[!！/／]")
MANAGEMENT_PATTERN = re.compile(
    r"^(?:陪伴|私聊陪伴)[^。！？!?\n]{0,32}"
    r"(?:设置|配置|开启|关闭|启用|停用|检查|修复|诊断|状态|帮助)"
)

DEFAULT_ACTIVITY_TEXT = json.dumps(list(DEFAULT_ACTIVITY_KEYWORDS), ensure_ascii=False)
DEFAULT_EXCLUDE_TEXT = json.dumps(list(DEFAULT_EXCLUDE_KEYWORDS), ensure_ascii=False)
DEFAULT_URGENT_TEXT = json.dumps(list(DEFAULT_URGENT_KEYWORDS), ensure_ascii=False)

DEFAULTS = {
    "busy_reply_gate_enabled": False,
    "busy_reply_min_delay_seconds": 60,
    "busy_reply_max_delay_seconds": 300,
    "busy_reply_proactive_resume_buffer_minutes": 10,
    "busy_activity_keywords": DEFAULT_ACTIVITY_TEXT,
    "busy_exclude_keywords": DEFAULT_EXCLUDE_TEXT,
    "busy_urgent_keywords": DEFAULT_URGENT_TEXT,
}
LIMITS = {
    "busy_reply_min_delay_seconds": (0, MAX_DELAY_SECONDS),
    "busy_reply_max_delay_seconds": (0, MAX_DELAY_SECONDS),
    "busy_reply_proactive_resume_buffer_minutes": (0, 120),
}
BOOLEAN_FIELDS = ("busy_reply_gate_enabled",)
INTEGER_FIELDS = tuple(LIMITS)
TEXT_FIELDS = ("busy_activity_keywords", "busy_exclude_keywords", "busy_urgent_keywords")
KEYWORD_FIELDS = tuple(GROUP + "." + name for name in TEXT_FIELDS)


def keywords(value, *, field):
    """Accept the declared JSON list or the equivalent separated string."""
    if type(value) is not str:
        raise ConfigurationError("busy_gate_settings_invalid", f"{field} requires text")
    if len(value.encode("utf-8")) > 8192:
        raise ConfigurationError("busy_gate_settings_invalid", f"{field} is too long")
    text = value.strip()
    if text.startswith("["):
        try:
            rows = json.loads(text)
        except (ValueError, RecursionError) as error:
            raise ConfigurationError("busy_gate_settings_invalid", f"{field} is not a valid JSON list") from error
        if type(rows) is not list:
            raise ConfigurationError("busy_gate_settings_invalid", f"{field} requires a list")
    elif text.startswith("{"):
        raise ConfigurationError("busy_gate_settings_invalid", f"{field} requires a list, not an object")
    else:
        rows = [part for part in re.split(r"[,，、;；|\s]+", text) if part]
    if len(rows) > MAX_KEYWORDS:
        raise ConfigurationError("busy_gate_settings_invalid", f"{field} has too many entries")
    seen, result = set(), []
    for row in rows:
        if (type(row) is not str or not row or row != row.strip() or len(row) > MAX_KEYWORD_CHARS
                or any(ord(char) < 32 for char in row)):
            raise ConfigurationError("busy_gate_settings_invalid", f"{field} needs short literal entries")
        if row.casefold() in seen:
            continue
        seen.add(row.casefold())
        result.append(row)
    return tuple(result)


class BusyGateField(FieldDefinition):
    """Parse a keyword list at the write boundary as well as at read time."""

    def validate(self, value):
        value = super().validate(value)
        if self.field_id in KEYWORD_FIELDS:
            keywords(value, field=self.field_id)
        return value


def configuration_fields():
    definitions = [BusyGateField(GROUP + "." + name, GROUP, GROUP, ValueKind.BOOLEAN, DEFAULTS[name],
        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER) for name in BOOLEAN_FIELDS]
    for name in INTEGER_FIELDS:
        minimum, maximum = LIMITS[name]
        definitions.append(BusyGateField(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, DEFAULTS[name],
            minimum=minimum, maximum=maximum, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    for name in TEXT_FIELDS:
        definitions.append(BusyGateField(GROUP + "." + name, GROUP, GROUP, ValueKind.TEXT, DEFAULTS[name],
            layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER))
    return tuple(definitions)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)


def _single_line(value, limit=220):
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True)
class Settings:
    busy_reply_gate_enabled: bool
    busy_reply_min_delay_seconds: int
    busy_reply_max_delay_seconds: int
    busy_reply_proactive_resume_buffer_minutes: int
    busy_activity_keywords: tuple
    busy_exclude_keywords: tuple
    busy_urgent_keywords: tuple

    def __post_init__(self):
        if type(self.busy_reply_gate_enabled) is not bool:
            raise ConfigurationError("busy_gate_settings_invalid", "the busy reply switch must be boolean")
        for name, (minimum, maximum) in LIMITS.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigurationError("busy_gate_settings_invalid", f"{name} is outside its registered range")
        for name in TEXT_FIELDS:
            value = getattr(self, name)
            if type(value) is not tuple or any(type(word) is not str for word in value):
                raise ConfigurationError("busy_gate_settings_invalid", f"{name} must be a parsed keyword list")

    @classmethod
    def from_values(cls, values=None):
        values = values or {}
        return cls(
            busy_reply_gate_enabled=values.get("busy_reply_gate_enabled", DEFAULTS["busy_reply_gate_enabled"]),
            busy_reply_min_delay_seconds=values.get(
                "busy_reply_min_delay_seconds", DEFAULTS["busy_reply_min_delay_seconds"]),
            busy_reply_max_delay_seconds=values.get(
                "busy_reply_max_delay_seconds", DEFAULTS["busy_reply_max_delay_seconds"]),
            busy_reply_proactive_resume_buffer_minutes=values.get(
                "busy_reply_proactive_resume_buffer_minutes",
                DEFAULTS["busy_reply_proactive_resume_buffer_minutes"]),
            busy_activity_keywords=keywords(values.get("busy_activity_keywords", DEFAULT_ACTIVITY_TEXT),
                                            field=GROUP + ".busy_activity_keywords"),
            busy_exclude_keywords=keywords(values.get("busy_exclude_keywords", DEFAULT_EXCLUDE_TEXT),
                                           field=GROUP + ".busy_exclude_keywords"),
            busy_urgent_keywords=keywords(values.get("busy_urgent_keywords", DEFAULT_URGENT_TEXT),
                                          field=GROUP + ".busy_urgent_keywords"),
        )

    def delay_bounds(self):
        """One configured interval; V1 swapped reversed bounds instead of rejecting them."""
        minimum = min(MAX_DELAY_SECONDS, self.busy_reply_min_delay_seconds)
        maximum = min(MAX_DELAY_SECONDS, self.busy_reply_max_delay_seconds)
        return (maximum, minimum) if maximum < minimum else (minimum, maximum)


class SettingsReader:
    """Read role defaults plus any explicit window override for one accept."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.owner = SparseConfigurationOwner(runtime)

    def read(self, context, *, override=None):
        """Malformed stored values never block a turn and never look like a delay."""
        snapshot = self.owner.read(target_for(context), configuration_fields())
        values = {key.removeprefix(GROUP + "."): field.value for key, field in snapshot.fields.items()}
        if override is not None:
            values.update(override)
        try:
            return Settings.from_values(values)
        except ConfigurationError:
            return Settings.from_values(DEFAULTS)


class BusyGateConfigurationOwner(SparseConfigurationOwner):
    """Reject keyword combinations the reader would otherwise have to default."""

    owner_id = OWNER

    @staticmethod
    def _validate_combination(fields, values):
        current = {field.field_id.removeprefix(GROUP + "."):
                   (values[field.field_id].value if field.field_id in values else field.default)
                   for field in fields}
        try:
            Settings.from_values(current)
        except ConfigurationError as error:
            raise ConfigurationError("busy_gate_settings_invalid", str(error)) from error

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        self._validate_combination(fields, result)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        self._validate_combination(fields, self.preview_set(target, fields, changes=changes))
        return super().write(target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at)


def calendar_items(runtime, context, *, limit=MAX_CALENDAR_RECORDS):
    """Read the bounded role-wide plan page used by calendar planning."""
    records = CalendarRecords(runtime, PersistentIdentityDirectory(runtime))
    items, _ = records.shared_role_plans(context, limit=limit)
    return items


def readable_items(runtime, context, *, items=None, limit=MAX_CALENDAR_RECORDS):
    """A calendar that stopped answering must never hold or reorder a reply."""
    if items is not None:
        return items
    try:
        return calendar_items(runtime, context, limit=limit)
    except Exception:
        return ()


def _busy_interval(item, now, settings):
    """The occurrence of one accepted ``role_plan`` covering ``now``, if it is busy."""
    if type(item) is not dict or item.get("kind") != "role_plan" or item.get("state") != "accepted":
        return None
    try:
        schedule = Schedule.restore(item.get("schedule"))
        duration = timedelta(minutes=int(item.get("duration_minutes")))
    except (AgendaError, TypeError, ValueError, OverflowError):
        return None
    if schedule is None or duration <= timedelta(0):
        return None
    exceptions = item.get("exceptions") if type(item.get("exceptions")) is dict else {}
    sequences = set()
    sequence = schedule.first_at_or_after(now - duration)
    if sequence is not None:
        sequences.add(sequence)
    for raw_seq, moved in exceptions.items():
        if not moved:
            continue
        try:
            seq_num = int(raw_seq)
            moved_at = datetime.fromisoformat(moved)
        except (TypeError, ValueError):
            continue
        if moved_at.tzinfo is None:
            continue
        if moved_at <= now < moved_at + duration:
            sequences.add(seq_num)
    matched_sequence = None
    matched_start = None
    for candidate in sorted(sequences):
        try:
            if str(candidate) in exceptions:
                moved = exceptions[str(candidate)]
                start = datetime.fromisoformat(moved) if moved else None
            else:
                start = schedule.at(candidate)
        except (AgendaError, TypeError, ValueError):
            continue
        if start is not None and start.tzinfo is not None and start <= now < start + duration:
            matched_sequence = candidate
            matched_start = start
            break
    if matched_start is None:
        return None
    title = _single_line(item.get("title"), 256)
    details = item.get("details") if type(item.get("details")) is dict else {}
    raw_detail = details.get(str(matched_sequence), item.get("detail"))
    detail = _single_line(raw_detail, 1024)
    presence = _single_line(item.get("presence"), 8)
    if not is_busy_text(f"{title} {detail} {presence}", settings):
        return None
    label = " ".join(part for part in (title, presence) if part) or detail
    return matched_start + duration, _single_line(label, 220) or "role_plan"


def busy_items(items, *, now, settings):
    """Every accepted ``role_plan`` occurrence that covers ``now`` and reads as busy."""
    now = normalize_utc(now, field="busy gate time")
    result = []
    for item in items:
        found = _busy_interval(item, now, settings)
        if found is not None:
            result.append(found)
    return tuple(result)


def is_busy_text(text, settings=None):
    """V1 excluded leisure first, then required one activity keyword (substring)."""
    settings = settings or Settings.from_values(DEFAULTS)
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(word in compact for word in settings.busy_exclude_keywords):
        return False
    return any(word in compact for word in settings.busy_activity_keywords)


def busy_context(items, *, now, settings=None):
    """The whole judgement, as a plain mapping, from already-read calendar items."""
    settings = settings or Settings.from_values(DEFAULTS)
    if not settings.busy_reply_gate_enabled:
        return {"busy": False, "reason": "disabled", "until": None, "schedule": ""}
    now = normalize_utc(now, field="busy gate time")
    matches = busy_items(items, now=now, settings=settings)
    if not matches:
        return {"busy": False, "reason": "schedule_not_busy", "until": None, "schedule": ""}
    until, schedule = min(matches)
    return {"busy": True, "reason": "schedule_keyword", "until": until, "schedule": schedule}


def scope_busy_context(runtime, context, *, now, settings=None, items=None):
    """One authorized window's decision; the host calls this on the accept path."""
    settings = settings or SettingsReader(runtime).read(context)
    return busy_context(readable_items(runtime, context, items=items), now=now, settings=settings)


def defer_seconds(settings, *, now, busy=None, text="", items=None):
    """A delay inside the configured interval, or 0.0 when the gate does not apply.

    ``next_wake`` drives the delay from ``conversation_flows.due_at``; the value
    only ever feeds that column, so no caller may sleep on it. Bypass runs before
    the interval, so an urgent message is never held for a random draw.
    """
    if not isinstance(settings, Settings) or not settings.busy_reply_gate_enabled:
        return 0.0
    if bypass_reason(text, settings=settings):
        return 0.0
    context = busy if busy is not None else busy_context(items or (), now=now, settings=settings)
    if not (isinstance(context, dict) and context.get("busy")):
        return 0.0
    minimum, maximum = settings.delay_bounds()
    if maximum <= 0:
        return 0.0
    return float(random.uniform(minimum, maximum) if maximum > minimum else minimum)


def deferral(runtime, context, *, text="", now, settings=None, items=None):
    """The one accept-path entry: read this window once, then answer with a delay.

    The host calls this immediately before ``flow.accept`` so the delay it returns
    is persisted into ``due_at`` by that same call. Urgent and command text is
    never held, the gate switch defaults to off, and a calendar that stopped
    answering yields no delay instead of a stalled reply.
    """
    settings = settings or SettingsReader(runtime).read(context)
    busy = busy_context(readable_items(runtime, context, items=items), now=now, settings=settings)
    return defer_seconds(settings, now=now, busy=busy, text=text)


def bypass_reason(text, *, settings=None):
    """V1's urgent/command/management bypass, on the compacted inbound text."""
    settings = settings or Settings.from_values(DEFAULTS)
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return ""
    if any(word in compact for word in settings.busy_urgent_keywords):
        return "urgent_or_safety"
    if COMMAND_PREFIX_PATTERN.match(compact):
        return "command"
    if MANAGEMENT_PATTERN.match(compact):
        return "management_command"
    return ""


def proactive_defer_until(settings, busy, *, now):
    """The instant proactive contact may resume: busy end plus the resume buffer."""
    now = normalize_utc(now, field="busy gate time")
    if not isinstance(settings, Settings) or not settings.busy_reply_gate_enabled:
        return None
    if not isinstance(busy, dict) or not busy.get("busy"):
        return None
    until = busy.get("until")
    if not isinstance(until, datetime) or until <= now:
        until = now + timedelta(seconds=FALLBACK_BUSY_WINDOW_SECONDS)
    return until + timedelta(minutes=settings.busy_reply_proactive_resume_buffer_minutes)


def proactive_hold(runtime, context, *, now, settings=None, items=None):
    """Trusted scheduler read: the resume instant, or None while contact may proceed."""
    settings = settings or SettingsReader(runtime).read(context)
    if not settings.busy_reply_gate_enabled:
        return None
    busy = busy_context(readable_items(runtime, context, items=items), now=now, settings=settings)
    return proactive_defer_until(settings, busy, now=now)


# Event-driven opportunities carry their own verified timing and stay outside the
# gate, exactly as they keep their place ahead of the ordinary check-in slot.
EVENT_MARKERS = frozenset((
    "activity_event", "growth_event", "journal_event", "calendar_event",
    "reminiscence_event", "date_event", "meal_event",
    "weather_event", "weather_change_event", "official_alert_event", "balance_event", "news_event",
    "reality_location_event",
))


def busy_exempt_source(origin):
    """True when this opportunity was produced by a time-critical event owner."""
    if not isinstance(origin, dict):
        return True
    return bool(EVENT_MARKERS.intersection(origin))


def deadline_for_deferral(*, now, due_at, deadline_at, turn_timeout_seconds, quiet_seconds, max_delay_seconds):
    """The deadline one deferred window needs, bounded by the gate's own maximum.

    The deadline has to move with the delay: V2 stops an unstarted flow once
    ``now >= deadline_at`` (``flow._advance_one`` cancels and halts with
    ``deadline_elapsed``), so a deferral that outran the original turn deadline
    would discard the message. The extension is exactly what the delayed first
    window needs to keep one further quiet window inside the promise, and it never
    exceeds ``max_delay_seconds``. With no deferral the deadline is returned
    unchanged, so every existing timer stays byte-identical.
    """
    if max_delay_seconds <= 0:
        return deadline_at
    required = due_at + timedelta(seconds=quiet_seconds + DEADLINE_MARGIN_SECONDS)
    return deadline_at + timedelta(seconds=min(
        MAX_TURN_DEADLINE_EXTENSION_SECONDS, max(0.0, (required - deadline_at).total_seconds(),
                                                 float(max_delay_seconds))))


def validate_defer_seconds(value):
    """A caller may only defer inside the registered interval."""
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not 0 <= float(value) <= MAX_DELAY_SECONDS):
        raise TaskError("busy_gate_defer_invalid", "the busy delay must be a finite count inside its range")
    return float(value)
