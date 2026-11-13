"""Rest reply gate and wake-after backlog over the role's own sleep receipts.

V1 (``astrbot_plugin_private_companion/user_rest_gate.py`` plus the rest-gate
block in ``main.py``) stopped a passive reply inside the event task while the
character was asleep and decided, by probability or by a small model judgement,
whether to wake up for that message; a message that was not answered went into a
per-user ``rest_reply_backlog`` that the next reply injected once.

V2 owns its timers in ``conversation_flows``, so the same intent is expressed as
a *deferral* the host adds to ``due_at`` while accepting the input (the T52
busy-gate wording). Nothing here sleeps, queues, seals, dispatches or sends, and
nothing here reorders a part that was already dispatched. The deferral is
bounded by ``MAX_DEFER_SECONDS``, which is also the ceiling
``ConversationFlow.accept`` and ``deadline_for_deferral`` accept, so a rest
deferral can never make a batch reach ``deadline_elapsed``.

What V1 stopped and V2 only postpones is recorded here as an honest design
difference: V2 never silently swallows an accepted message, so "kept sleeping"
means "answered as late as the durable timer allows", and the admitted inputs
stay in their own batch.

The sleepy signal is the role's *actual* sleep receipt -- the active
``role_activities`` row written by ``role_work/life/runtime.py`` and completed by
``life/body_clock.py`` -- parsed with the shared ``role_work.codec``. The planned
wake comes from that same receipt's ``work.due_at``. ``role_work/state``'s
``busy`` field and any platform online status are deliberately not consulted:
they are reported material for a reply, never an automatic reason to withhold
one (``state/maintenance.py`` and ``conversation/life_context.py``). No model is
called here at all: the judgement is injected by the caller, and an unavailable
or unknown judgement always keeps the role asleep.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import random
import re
import zoneinfo

from companion_v2.configuration.models import ConfigurationError
from companion_v2.execution.models import digest, encode, stamp
from companion_v2.foundation.refs import normalize_utc
from companion_v2.role_work.codec import activity_from_json
from companion_v2.role_work.models import RoleActivityState as ActivityState
from companion_v2.role_work.state.ownership import role_owner


GROUP = "role.life"
OWNER = "role.life.settings"
# One durable wait per accepted batch. The value is the same ceiling
# ``ConversationFlow.accept`` validates and ``deadline_for_deferral`` extends by,
# so a rest deferral is always expressible and never outlives its own deadline.
MAX_DEFER_SECONDS = 900
# V1 read ``rest_backlog_max_messages`` with only a lower bound; the registered
# upper bound also caps the stored document.
MAX_BACKLOG_ENTRIES = 32
MAX_BACKLOG_TEXT_CHARS = 180
MAX_SLEEP_RECEIPTS = 8
MODES = ("probability", "llm")
# V1 ``plugin_bootstrap`` accepted these aliases for the model-judgement mode.
MODE_ALIASES = {"model": "llm", "模型": "llm", "llm_judge": "llm", "llm-judge": "llm"}
DEFAULT_TIMEZONE = "Asia/Shanghai"
WINDOW_PATTERN = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")
WINDOW_TRANSLATION = str.maketrans({"：": ":", "—": "-", "–": "-", "－": "-", "~": "-", "～": "-", "至": "-", "到": "-"})
WINDOW_SEPARATORS = re.compile(r"[,;；，、]+")
PREFIX = "role.life.rest_gate.v1:"

# The eight V1 rest-gate keys, with the V1 defaults
# (``_conf_schema.json`` + ``plugin_bootstrap.py``).
DEFAULTS = {
    "enable_rest_reply_simulation": False,
    "rest_reply_mode": "probability",
    "rest_reply_probability": 18,
    "rest_reply_llm_threshold": 65,
    "rest_reply_active_windows": "23:00-08:30,12:20-13:40",
    "rest_reply_awake_grace_minutes": 30,
    "enable_rest_backlog_reply": True,
    "rest_backlog_max_messages": 4,
}
BOOLEAN_FIELDS = ("enable_rest_reply_simulation", "enable_rest_backlog_reply")
INTEGER_FIELDS = ("rest_reply_probability", "rest_reply_llm_threshold",
                  "rest_reply_awake_grace_minutes", "rest_backlog_max_messages")
TEXT_FIELDS = ("rest_reply_mode", "rest_reply_active_windows")
LIMITS = {
    "rest_reply_probability": (0, 100),
    "rest_reply_llm_threshold": (0, 100),
    "rest_reply_awake_grace_minutes": (0, 240),
    "rest_backlog_max_messages": (1, MAX_BACKLOG_ENTRIES),
}
REST_FIELDS = frozenset(DEFAULTS)
# V1 ``_rest_reply_boundary_score``: a negative score means "do not reply", a
# score at or above the wake threshold means "this is worth waking up for", and
# everything else falls through to the probability or model decision.
NO_REPLY_BOUNDARY = (r"(?:了|啦|吧|我|这(?:个|条|句|段)(?:消息|话|话题|内容|问题)?|这(?:条)?消息|本条消息|消息|哈|噢|哦|$|[，。！？,.!?])")
NO_REPLY_PATTERN = re.compile(
    r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
    + NO_REPLY_BOUNDARY)
PROACTIVE_ONLY_PATTERN = re.compile(
    r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}主动.{0,8}(?:打扰|吵|发消息|找我|回(?:复)?|理我|搭理我|接话|说话)")
CORRECTION_PATTERN = re.compile(
    r"你.{0,6}(没睡|没在睡|不是在睡|不是睡|不在睡|还没睡|醒着|清醒|没休息|没在休息|不是在休息|不是休息|不在休息)"
    r"|别装睡|别装休息|装睡|装睡觉|明明醒着|明明没睡|明明没休息|又没睡|又没休息")
WAKEUP_PATTERN = re.compile(
    r"快醒|醒醒|醒一醒|醒来|醒过来|别睡了?|别睡啦|别睡嘛|先别睡|别睡别睡|起床|起来|快起|回我一下|快回|马上回")
QUIET_PATTERN = re.compile(
    r"(?:不用|不必|无需|别|不要|先别|暂时别|今晚别|今天别)(?:再)?(?:回(?:复)?|理我|搭理我|接话|说话|出声)"
    + NO_REPLY_BOUNDARY
    + r"|(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|吵我|叫我|主动|发消息|找我)"
    r"|(?:安静点|闭嘴|别说话|不要说话|别醒|继续睡)")
GOODNIGHT_PATTERN = re.compile(r"(?:晚安|好梦|早点睡|早点休息|睡个好觉)")
URGENT_PATTERN = re.compile(r"救命|出事|急|紧急|重要|不舒服|难受|害怕|崩溃|报警|医院|摔|痛")
SOFT_WAKEUP_PATTERN = re.compile(r"醒了吗|睡了吗|在吗|能不能回|可以回吗|想你|陪我|听我说|还睡吗|还在睡吗")
BACKLOG_INSTRUCTION = (
    "下面是角色休息期间到达、当时没有回复的私聊，按到达顺序列出，只作为醒来后的补看材料：不声称当时已经回过，不把整段休息说成没有收到消息，也不把它当成新请求重复索取信息。"
)


def normalize_mode(value):
    """V1 coerced unknown mode text to ``probability``; the read path keeps that."""
    mode = " ".join(str(value or "").split()).strip().lower()
    mode = MODE_ALIASES.get(mode, mode)
    return mode if mode in MODES else "probability"


def parse_windows(value):
    """``23:00-08:30,12:20-13:40`` as ``(start, end)`` minute pairs.

    Faithful to V1 ``proactive_engine._parse_window_minutes``: separators are
    normalized, hours are taken modulo 24, and an end that is not after its start
    is read as crossing midnight (``end += 24 * 60``), which is what makes the
    V1 default windows work at all. An empty value means "no restriction".
    """
    compact = re.sub(r"\s+", "", str(value or ""))
    if not compact:
        return ()
    result = []
    for part in WINDOW_SEPARATORS.split(compact.translate(WINDOW_TRANSLATION)):
        if not part:
            continue
        match = WINDOW_PATTERN.fullmatch(part)
        if match is None:
            raise ConfigurationError("rest_gate_settings_invalid", "rest windows must look like 23:00-08:30")
        start_hour, start_minute, end_hour, end_minute = (int(item) for item in match.groups())
        if start_minute > 59 or end_minute > 59:
            raise ConfigurationError("rest_gate_settings_invalid", "rest windows use whole minutes")
        start = (start_hour % 24) * 60 + start_minute
        end = (end_hour % 24) * 60 + end_minute
        if end <= start:
            end += 24 * 60
        result.append((start, end))
    return tuple(result)


def window_active(windows, *, minutes):
    """True when the local minute of day falls inside one configured window."""
    if not windows:
        return True
    return any(start <= candidate < end for start, end in windows
               for candidate in (minutes, minutes + 24 * 60))


def window_wake_at(settings, *, now):
    """End of the configured rest window containing ``now``, or ``None``.

    Only used to keep the rest-reply gate useful for windows that already run
    role sleep; a role that has no sleep receipt can still rest through the
    explicit window without pretending an execution receipt exists.
    """
    windows = settings.windows()
    if not windows:
        return None
    minutes = local_minutes(now, settings.sleep_timezone)
    for start, end in windows:
        for candidate in (minutes, minutes + 24 * 60):
            if start <= candidate < end:
                return now + timedelta(minutes=end - candidate)
    return None


def local_minutes(now, timezone):
    """Local minute of day for the configured sleep timezone; bad zones fall back."""
    try:
        zone = zoneinfo.ZoneInfo(str(timezone or DEFAULT_TIMEZONE))
    except Exception:
        zone = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
    local = normalize_utc(now, field="rest gate time").astimezone(zone)
    return local.hour * 60 + local.minute


def boundary_score(text):
    """V1 ``_rest_reply_boundary_score``: ``(score, reason)`` on compacted text."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return 0, "empty"
    if NO_REPLY_PATTERN.search(compact):
        return -100, "user_asks_no_reply"
    proactive_only_quiet = bool(PROACTIVE_ONLY_PATTERN.search(compact))
    if CORRECTION_PATTERN.search(compact):
        return 100, "user_corrects_not_resting"
    if WAKEUP_PATTERN.search(compact):
        return 100, "explicit_wakeup_request"
    if not proactive_only_quiet and QUIET_PATTERN.search(compact):
        return -100, "user_asks_quiet"
    if GOODNIGHT_PATTERN.search(compact):
        return 100, "goodnight_ack"
    if proactive_only_quiet:
        return 0, "user_asks_no_proactive"
    if URGENT_PATTERN.search(compact):
        return 100, "urgent_or_explicit_wakeup"
    if SOFT_WAKEUP_PATTERN.search(compact):
        return 72, "soft_wakeup_request"
    return 0, "normal"


@dataclass(frozen=True)
class Settings:
    """The eight V1 fields plus the existing sleep timezone the windows are read in.

    ``sleep_timezone`` is the role-wide sleep field registered by
    ``life/configuration.py``; it is consumed here because the same windows are
    local time and is not re-registered as a rest-gate field.
    """

    enable_rest_reply_simulation: bool
    rest_reply_mode: str
    rest_reply_probability: int
    rest_reply_llm_threshold: int
    rest_reply_active_windows: str
    rest_reply_awake_grace_minutes: int
    enable_rest_backlog_reply: bool
    rest_backlog_max_messages: int
    sleep_timezone: str = DEFAULT_TIMEZONE
    sleep_enabled: bool = False

    def __post_init__(self):
        for name in BOOLEAN_FIELDS:
            if type(getattr(self, name)) is not bool:
                raise ConfigurationError("rest_gate_settings_invalid", f"{name} must be a boolean")
        if type(self.sleep_enabled) is not bool:
            raise ConfigurationError("rest_gate_settings_invalid", "sleep_enabled must be a boolean")
        for name, (minimum, maximum) in LIMITS.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigurationError("rest_gate_settings_invalid",
                                         f"{name} must be an integer from {minimum} to {maximum}")
        if type(self.rest_reply_mode) is not str or self.rest_reply_mode not in MODES:
            raise ConfigurationError("rest_gate_settings_invalid", "rest_reply_mode must be probability or llm")
        if type(self.rest_reply_active_windows) is not str:
            raise ConfigurationError("rest_gate_settings_invalid", "rest_reply_active_windows requires text")
        parse_windows(self.rest_reply_active_windows)
        if type(self.sleep_timezone) is not str or not self.sleep_timezone.strip():
            raise ConfigurationError("rest_gate_settings_invalid", "the sleep timezone requires an IANA name")

    @classmethod
    def from_values(cls, values=None):
        values = values or {}
        return cls(
            enable_rest_reply_simulation=values.get(
                "enable_rest_reply_simulation", DEFAULTS["enable_rest_reply_simulation"]),
            rest_reply_mode=normalize_mode(values.get("rest_reply_mode", DEFAULTS["rest_reply_mode"])),
            rest_reply_probability=values.get("rest_reply_probability", DEFAULTS["rest_reply_probability"]),
            rest_reply_llm_threshold=values.get("rest_reply_llm_threshold", DEFAULTS["rest_reply_llm_threshold"]),
            rest_reply_active_windows=values.get(
                "rest_reply_active_windows", DEFAULTS["rest_reply_active_windows"]),
            rest_reply_awake_grace_minutes=values.get(
                "rest_reply_awake_grace_minutes", DEFAULTS["rest_reply_awake_grace_minutes"]),
            enable_rest_backlog_reply=values.get(
                "enable_rest_backlog_reply", DEFAULTS["enable_rest_backlog_reply"]),
            rest_backlog_max_messages=values.get(
                "rest_backlog_max_messages", DEFAULTS["rest_backlog_max_messages"]),
            sleep_timezone=str(values.get("sleep_timezone") or DEFAULT_TIMEZONE),
            sleep_enabled=values.get("sleep_enabled", False),
        )

    def windows(self):
        return parse_windows(self.rest_reply_active_windows)

    def max_backlog(self):
        return max(1, min(MAX_BACKLOG_ENTRIES, self.rest_backlog_max_messages))


def validate_values(values):
    """Read-side contract: the whole ``role.life`` value set must stay usable."""
    Settings.from_values(values)
    return values


def validate_changes(changes, reset=()):
    """Write-side contract: reject a rest field the reader would have to coerce."""
    normalized = {}
    for key, value in dict(changes or {}).items():
        name = str(key).rsplit(".", 1)[-1]
        if name in REST_FIELDS:
            normalized[name] = value
    if not normalized:
        return changes
    mode = normalized.get("rest_reply_mode")
    if mode is not None:
        text = " ".join(str(mode).split()).strip().lower()
        if MODE_ALIASES.get(text, text) not in MODES:
            raise ConfigurationError("rest_gate_settings_invalid", "rest_reply_mode must be probability or llm")
    Settings.from_values({**DEFAULTS, **normalized})
    return changes


def read_settings(runtime, context):
    """One window read of this group; unusable stored values fall back to defaults."""
    from .configuration import LifeConfigurationOwner, configuration_fields, target_for, values
    try:
        snapshot = LifeConfigurationOwner(runtime.configuration_store).read(
            target_for(context), configuration_fields())
        return Settings.from_values(values(snapshot))
    except (ValueError, TypeError, KeyError):
        return Settings.from_values(DEFAULTS)


def sleep_receipts(runtime, context, *, limit=MAX_SLEEP_RECEIPTS):
    """Bounded read of this role's sleeping or recently interrupted receipts; grants nothing.

    The owner key and the record shape are the ones ``RoleBodyClock.active``
    uses, so the gate can only ever see the same execution receipts the shared
    body already reads.
    """
    rows = runtime.read_all("""SELECT record_json FROM role_activities WHERE owner_key = ? AND state IN ('active','paused')
          AND json_extract(record_json, '$.scope.kind') = 'private'
          AND json_extract(record_json, '$.work.kind') = 'sleep'
        ORDER BY updated_at, activity_id LIMIT ?""", (digest(role_owner(context)), limit))
    activities = []
    for row in rows:
        try:
            activities.append(activity_from_json(row["record_json"]))
        except (ValueError, TypeError, KeyError):
            continue  # A receipt that cannot be read is never treated as sleep.
    return tuple(activities)


def sleep_context(runtime, context, *, now, settings=None):
    """Is the role actually asleep inside one configured window right now?"""
    settings = settings or Settings.from_values(DEFAULTS)
    now = normalize_utc(now, field="rest gate time")
    try:
        receipts = sleep_receipts(runtime, context)
    except Exception:
        receipts = ()
    if not receipts:
        # Sleep may be disabled while the operator still expects the explicit
        # rest windows to hold replies. That is not a fake sleep receipt: the
        # window itself is the durable rest bound, and it only applies when the
        # operator enabled the rest-reply simulation and configured windows.
        if settings.enable_rest_reply_simulation:
            window_wake = window_wake_at(settings, now=now)
            if window_wake is not None:
                return {"resting": True, "reason": "rest_window", "sleep": None,
                        "wake_at": window_wake,
                        "until_seconds": max(0.0, (window_wake - now).total_seconds())}
        return {"resting": False, "reason": "not_sleeping", "sleep": None, "wake_at": None, "until_seconds": None}
    active = [receipt for receipt in receipts if receipt.state is ActivityState.ACTIVE]
    if not active and not settings.sleep_enabled and settings.enable_rest_reply_simulation:
        # Older interrupted receipts must not make a disabled sleep schedule
        # permanently immune to the operator's explicit rest windows.
        window_wake = window_wake_at(settings, now=now)
        if window_wake is not None:
            return {"resting": True, "reason": "rest_window", "sleep": None,
                    "wake_at": window_wake,
                    "until_seconds": max(0.0, (window_wake - now).total_seconds())}
    activity = receipts[-1]
    wake_at = activity.work.due_at if activity.work else None
    if activity.state is ActivityState.PAUSED:
        return {"resting": False, "reason": "sleep_interrupted", "sleep": activity.activity_id,
                "wake_at": wake_at, "until_seconds": 0.0}
    if activity.state is not ActivityState.ACTIVE:
        return {"resting": False, "reason": "not_sleeping", "sleep": None, "wake_at": None, "until_seconds": None}
    if wake_at is not None and wake_at <= now:
        # The durable timer has already expired; the waking owner completes it.
        return {"resting": False, "reason": "sleep_timer_due", "sleep": activity.activity_id,
                "wake_at": wake_at, "until_seconds": 0.0}
    if not window_active(settings.windows(), minutes=local_minutes(now, settings.sleep_timezone)):
        return {"resting": False, "reason": "outside_active_window", "sleep": activity.activity_id,
                "wake_at": wake_at, "until_seconds": None}
    until = None if wake_at is None else max(0.0, (wake_at - now).total_seconds())
    return {"resting": True, "reason": "sleep_window", "sleep": activity.activity_id,
            "wake_at": wake_at, "until_seconds": until}


def hold_defer(sleep, *, now):
    """How long one held reply waits: up to the planned wake, capped for the flow."""
    del now
    until = sleep.get("until_seconds") if isinstance(sleep, dict) else None
    if until is None:
        return float(MAX_DEFER_SECONDS)
    return float(min(MAX_DEFER_SECONDS, max(1.0, until)))


def awake_grace(state, *, now):
    """The V1 awake-grace record, valid only for the same physical sleep."""
    value = state.get("awake_until") if isinstance(state, dict) else None
    if not value:
        return {"active": False, "sleep": None, "until": None}
    try:
        until = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return {"active": False, "sleep": None, "until": None}
    if until <= normalize_utc(now, field="rest gate time"):
        return {"active": False, "sleep": None, "until": None}
    return {"active": True, "sleep": state.get("awake_sleep"), "until": until}


def judgement(settings, *, now, sleep, text="", grace=None, judge=None):
    """The whole decision as a plain mapping; no storage and no model call.

    ``judge`` is an optional synchronous callable
    ``judge(text=..., sleep=..., settings=...) -> (score, reason)``. It is only
    consulted in ``llm`` mode, that mode is off by default, and a missing,
    failing or unknown judgement always keeps the role asleep.
    """
    now = normalize_utc(now, field="rest gate time")
    result = {"resting": False, "action": "reply", "reason": "not_sleeping", "defer": 0.0,
              "score": 0, "boundary": "not_sleeping"}
    if not isinstance(settings, Settings) or not settings.enable_rest_reply_simulation:
        result["reason"] = "disabled"
        return result
    if not (isinstance(sleep, dict) and sleep.get("resting")):
        result["reason"] = (sleep or {}).get("reason") or "not_sleeping"
        return result
    result["resting"] = True
    if isinstance(grace, dict) and grace.get("active") and grace.get("sleep") == sleep.get("sleep"):
        # V1: a role called awake once is not re-judged for the next messages.
        result["reason"] = "awake_grace"
        return result
    score, reason = boundary_score(text)
    result["score"], result["boundary"] = score, reason
    threshold = settings.rest_reply_llm_threshold
    if score < 0:
        result.update(action="hold", reason=reason, defer=hold_defer(sleep, now=now))
        return result
    # 0 表示关闭“评分捷径”：明确叫醒/在吗这类文本不再直接放行，交给
    # 概率或模型判定。否则 0 会被 max(1, …) 变成“任何正分都放行”，
    # 概率调到 0 也照样一叫就醒（用户实际遇到的语义）。
    if threshold > 0 and score >= threshold:
        result["reason"] = reason
        return result
    if settings.rest_reply_mode == "llm":
        decided = None
        if callable(judge):
            try:
                decided = judge(text=text, sleep=sleep, settings=settings)
            except Exception:
                decided = None
        if not (isinstance(decided, (tuple, list)) and len(decided) == 2
                and type(decided[0]) is int and 0 <= decided[0] <= 100):
            # Unknown judgement is not permission to reply.
            result.update(action="hold", reason="judge_unavailable", defer=hold_defer(sleep, now=now))
            return result
        result["score"] = decided[0]
        if decided[0] >= settings.rest_reply_llm_threshold:
            result["reason"] = "judge_wake"
            return result
        result.update(action="hold", reason="judge_keeps_sleeping", defer=hold_defer(sleep, now=now))
        return result
    probability = max(0, min(100, settings.rest_reply_probability))
    if random.random() * 100 < probability:
        result["reason"] = "probability_hit"
        return result
    result.update(action="hold", reason="probability_miss", defer=hold_defer(sleep, now=now))
    return result


def metadata_key(context):
    return PREFIX + digest([context.key])


def empty_document():
    return {"version": 1, "awake_until": None, "awake_sleep": None, "last": None, "backlog": []}


def document(runtime, context):
    """The gate's own durable document; damaged content reads as never slept."""
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (metadata_key(context),))
    if row is None:
        return empty_document()
    try:
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return empty_document()
    if type(value) is not dict or value.get("version") != 1:
        return empty_document()
    backlog = value.get("backlog")
    if type(backlog) is not list or any(type(entry) is not dict for entry in backlog):
        backlog = []
    awake_until = value.get("awake_until") if isinstance(value.get("awake_until"), str) else None
    awake_sleep = value.get("awake_sleep") if isinstance(value.get("awake_sleep"), str) else None
    last = value.get("last") if isinstance(value.get("last"), dict) else None
    return {"version": 1, "awake_until": awake_until, "awake_sleep": awake_sleep, "last": last,
            "backlog": backlog[-MAX_BACKLOG_ENTRIES:]}


def save(runtime, context, value):
    with runtime.transaction() as connection:
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (metadata_key(context), encode(value)))
    return value


def _source_document(source):
    if source is None:
        return None
    try:
        return json.loads(encode(source))
    except (TypeError, ValueError):
        return None


def backlog_entry(*, now, reason, source=None, capability="", sleep=None):
    return {"at": stamp(normalize_utc(now, field="rest gate time")), "reason": " ".join(str(reason or "").split())[:80],
            "source": _source_document(source), "capability": str(capability or "")[:64], "sleep": sleep}


def append_backlog(state, *, now, settings, entry):
    """Newest last, deduplicated by original source, bounded by the configured N."""
    if not settings.enable_rest_backlog_reply:
        return state
    backlog = [item for item in state.get("backlog", ()) if type(item) is dict]
    source = entry.get("source")
    if source is not None:
        backlog = [item for item in backlog if item.get("source") != source]
    elif backlog and backlog[-1].get("at") == entry["at"] and backlog[-1].get("reason") == entry["reason"]:
        return state
    backlog.append(entry)
    state = dict(state)
    state["backlog"] = backlog[-settings.max_backlog():]
    return state


def pending(runtime, context):
    """The recorded wake-after backlog, oldest first; read-only."""
    return tuple(document(runtime, context)["backlog"])


def mark_awake(state, *, now, settings, sleep):
    """V1 ``_mark_sleep_woken_by_user``: no re-judgement inside the grace window."""
    state = dict(state)
    if settings.rest_reply_awake_grace_minutes <= 0:
        state["awake_until"] = None
        state["awake_sleep"] = None
        return state
    state["awake_until"] = stamp(normalize_utc(now, field="rest gate time")
                                 + timedelta(minutes=settings.rest_reply_awake_grace_minutes))
    state["awake_sleep"] = sleep
    return state


def deferral(runtime, context, *, text="", now, event_key="", source=None, capability="",
             settings=None, judge=None, body_clock=None):
    """The one accept-path entry: judge once, record it, return a bounded delay.

    The host calls this immediately before ``flow.accept`` so the delay it returns
    is persisted into ``due_at`` by that same call. It never sleeps, and a
    repeated call for the same inbound returns the original answer instead of
    drawing again (V1's "same batch is judged once").
    """
    settings = settings or read_settings(runtime, context)
    sleep = sleep_context(runtime, context, now=now, settings=settings)
    if not settings.enable_rest_reply_simulation:
        if body_clock is not None and sleep.get("resting") and sleep.get("sleep"):
            body_clock.interrupt(context, sleep["sleep"], observed_at=now,
                                 grace_minutes=settings.rest_reply_awake_grace_minutes)
        return 0.0
    state = document(runtime, context)
    last = state.get("last")
    if event_key and isinstance(last, dict) and last.get("event") == event_key:
        value = last.get("defer")
        return float(value) if type(value) in (int, float) else 0.0
    decision = judgement(settings, now=now, sleep=sleep, text=text,
                         grace=awake_grace(state, now=now), judge=judge)
    if decision["action"] == "hold":
        state = append_backlog(state, now=now, settings=settings,
                               entry=backlog_entry(now=now, reason=decision["reason"], source=source,
                                                   capability=capability, sleep=sleep.get("sleep")))
    else:
        if body_clock is not None and sleep.get("resting") and sleep.get("sleep"):
            body_clock.interrupt(context, sleep["sleep"], observed_at=now,
                                 grace_minutes=settings.rest_reply_awake_grace_minutes)
        if decision["reason"] != "awake_grace":
            state = mark_awake(state, now=now, settings=settings, sleep=sleep.get("sleep"))
    if event_key:
        state = dict(state)
        state["last"] = {"event": event_key, "defer": float(decision["defer"]), "reason": decision["reason"]}
    save(runtime, context, state)
    return float(decision["defer"])


def consume(runtime, context, *, now, settings=None):
    """Take the backlog for the first reply after the role actually woke up."""
    settings = settings or read_settings(runtime, context)
    state = document(runtime, context)
    if not state["backlog"]:
        return ()
    if sleep_context(runtime, context, now=now, settings=settings)["resting"]:
        return ()  # Still asleep: keep it for the wake, never surface it early.
    entries = tuple(state["backlog"])
    state = dict(state)
    state["backlog"] = []
    save(runtime, context, state)
    return entries


def backlog_prompt(entries, texts, *, timezone=DEFAULT_TIMEZONE, max_messages=4):
    """V1 ``_take_rest_reply_backlog_prompt`` shape, over re-read originals.

    ``texts`` is positional: one already-authorized payload per entry, or ``None``
    when the original can no longer be read. Nothing is quoted from storage here,
    so a revoked or expired original simply reads as an unreadable message.
    """
    texts = tuple(texts or ())
    limit = max(0, min(MAX_BACKLOG_ENTRIES, int(max_messages)))
    try:
        zone = zoneinfo.ZoneInfo(str(timezone or DEFAULT_TIMEZONE))
    except Exception:
        zone = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
    lines = []
    for index, entry in enumerate(tuple(entries or ())[-limit:] if limit else (), 1):
        moment = "刚才"
        try:
            moment = datetime.fromisoformat(entry["at"]).astimezone(zone).strftime("%H:%M")
        except (KeyError, TypeError, ValueError):
            pass
        raw = texts[index - 1] if index - 1 < len(texts) else None
        body = " ".join(str(raw).split())[:MAX_BACKLOG_TEXT_CHARS] if raw else "发来了一条消息"
        lines.append(f"{index}. {moment}｜{body}")
    if not lines:
        return ""
    return "休息时有几条私聊没来得及回，醒来后补看到：\n" + "\n".join(lines) + "\n" + BACKLOG_INSTRUCTION
