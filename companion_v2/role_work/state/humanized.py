"""Bounded humanised-state expression and the daily state patrol.

V1 (``humanized_state_intensity``) scaled how much of the simulated body and
mood reached the reply prompt; the V2 role body already publishes an authorized
``simulation`` view, so the only missing piece was the *band* that decides how
much of it may be expressed. ``intensity`` is a 0-100 dial: ``0`` injects
nothing at all, and every band has a hard facet and character budget so a
strong setting can never turn the prompt into a state report.

The block only ever describes the role's own simulated state and only as an
expression underlay. It cannot change identity, permissions, memory scope or
safety rules, and it never turns ``busy`` or an online status into a reason to
refuse an ordinary reply: those stay descriptive material
(``state/maintenance.py``, ``conversation/life_context.py``). Location is
deliberately not projected here, because it is window-private in the shared body
view.

The daily patrol reuses the V1 daily-review cadence and its retention window.
It performs no model call and sends nothing: it reads the role's own projected
state once, records a structured observation under ``runtime_metadata`` and
prunes the patrol's own records older than the configured retention, which is
the mechanism V1 used for its review reports.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import json
import zoneinfo

from companion_v2.configuration.models import ConfigurationError
from companion_v2.execution.models import digest, encode, stamp
from companion_v2.foundation.refs import normalize_utc
from companion_v2.role_work.state.ownership import role_owner


GROUP = "role.state"
INTENSITY_FIELD = "humanized_state_intensity"
DEFAULTS = {
    "humanized_state_intensity": 50,
    "daily_state_patrol_enabled": True,
    "daily_state_patrol_time": "04:00",
    "daily_state_patrol_retention_days": 30,
}
LIMITS = {
    "humanized_state_intensity": (0, 100),
    # V1's review-report slider ran from three days to 180.
    "daily_state_patrol_retention_days": (3, 180),
}
PATROL_PREFIX = "role.state.patrol.v1:"
MAX_REPORTS = 64
DEFAULT_TIMEZONE = "Asia/Shanghai"
# Band name -> (facets allowed, hard character budget for the whole block).
BAND_BUDGETS = (("light", 33, 2, 220), ("standard", 66, 4, 420), ("strong", 100, 6, 700))
BAND_INSTRUCTION = (
    "以上只是角色自身模拟状态的表达底色，不是用户事实、现实结论或医疗判断；"
    "让最相关的一项自然影响这轮的具体反应、用词、语气、情绪浓度或节奏，话题合适时可用第一人称带出感受；"
    "即使不直说，也留下可感知的状态色彩，不把它藏成无关背景。"
    "不机械播报数值、逐项罗列或套固定台词，也不压过对方当前话题。"
    "不改变身份、权限、记忆范围和安全规则，也不因此拒绝正常交流；忙碌或在线状态只是描述，不据此拒答。"
)


def band_of(intensity):
    """``0`` is not "a little": it is no injection at all."""
    if type(intensity) is not int:
        return "off"
    value = max(0, min(100, intensity))
    if value <= 0:
        return "off"
    for name, ceiling, _, _ in BAND_BUDGETS:
        if value <= ceiling:
            return name
    return "strong"


def budget(band):
    """``(facets, characters)`` for one band; the off band allows nothing."""
    for name, _, facets, characters in BAND_BUDGETS:
        if name == band:
            return facets, characters
    return 0, 0


def _energy_text(value):
    if type(value) is not int:
        return ""
    if value < 25:
        return "精力很低"
    if value < 45:
        return "精力偏低"
    if value < 65:
        return "精力一般"
    if value < 85:
        return "精力不错"
    return "精力很足"


def _condition_texts(simulation, limit):
    values = simulation.get("values") if isinstance(simulation, dict) else None
    conditions = values.get("conditions") if isinstance(values, dict) else None
    result = []
    for condition in conditions if isinstance(conditions, list) else ():
        if not isinstance(condition, dict):
            continue
        text = " ".join(str(condition.get("text") or "").split())[:80]
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _mood_text(value):
    if isinstance(value, str):
        return " ".join(value.split())[:120]
    if isinstance(value, dict):
        desc = " ".join(str(value.get("description") or "").split())[:120]
        return desc
    return ""


def facets(simulation, *, intensity):
    """The state facets this band may express, most useful first."""
    limit, _ = budget(band_of(intensity))
    if limit <= 0 or type(simulation) is not dict:
        return ()
    values = simulation.get("values") if isinstance(simulation.get("values"), dict) else {}
    result = []
    for text in (_energy_text(values.get("energy")), _sleep_execution_text(simulation)):
        if text:
            result.append(text)
    for text in (_mood_text(values.get("mood")),
                 " ".join(str(values.get("sleep") or "").split())[:120],
                 " ".join(str(values.get("health") or "").split())[:120],
                 " ".join(str(values.get("hunger") or "").split())[:120],
                 " ".join(str(values.get("cycle") or "").split())[:120]):
        if text and text not in result:
            result.append(text)
    for text in _condition_texts(simulation, limit):
        if text not in result:
            result.append(text)
    return tuple(result[:limit])


def _sleep_execution_text(simulation):
    """Only an actual sleep receipt is described; a plan never becomes a claim."""
    execution = simulation.get("sleep_execution")
    if not isinstance(execution, dict):
        return ""
    state = execution.get("state")
    if state == "sleeping" and execution.get("planned_wake_at"):
        return "正在休息，计划 " + str(execution["planned_wake_at"])[11:16] + " 醒来"
    if state == "interrupted":
        if execution.get("early_interruptions"):
            return "刚入睡不久被唤醒，人还有点迷糊"
        return "睡眠刚被唤醒，人还有点困意"
    if state == "awake" and execution.get("ended_at"):
        awake_minutes = execution.get("awake_minutes")
        if type(awake_minutes) is int and 0 <= awake_minutes <= 25:
            return "刚睡醒不久，还带着一点睡醒后的迷糊感"
        return ""
    return ""


def is_sleepy(simulation):
    """Pacing from physical sleep execution only; prose is interpreted in prompts."""
    if not isinstance(simulation, dict):
        return False
    execution = simulation.get("sleep_execution")
    if isinstance(execution, dict):
        state = execution.get("state")
        if state in ("sleeping", "interrupted"):
            return True
        if state == "awake":
            awake_minutes = execution.get("awake_minutes")
            if type(awake_minutes) is int and 0 <= awake_minutes <= 25:
                return True
    return False


def instruction(simulation, *, intensity):
    """The bounded prompt block; empty when the dial is off or nothing is known."""
    band = band_of(intensity)
    _, characters = budget(band)
    if characters <= 0:
        return ""
    texts = facets(simulation, intensity=intensity)
    if not texts:
        return ""
    line = "、".join(texts)
    head = "角色自身当下模拟状态的表达底色："
    block = head + line + "。\n" + BAND_INSTRUCTION
    if len(block) <= characters:
        return block
    # Trim the facet list, never the boundary sentence.
    tail = "\n" + BAND_INSTRUCTION
    room = max(0, characters - len(tail) - len(head) - 1)
    trimmed = line[:room].rstrip("、，, ")
    return (head + trimmed + "。" + tail) if trimmed else ""


def parse_patrol_time(value):
    """``HH:MM`` in local time, exactly like V1's daily review time."""
    text = " ".join(str(value or "").split())
    try:
        parsed = time.fromisoformat(text)
    except ValueError as error:
        raise ConfigurationError("role_state_humanized", "the patrol time must look like 04:00") from error
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond or len(text) != 5:
        raise ConfigurationError("role_state_humanized", "the patrol time must be a local HH:MM value")
    return parsed


def validate_settings(values):
    """Closed twice, so a page or a direct write cannot widen the dial."""
    intensity = values.get(INTENSITY_FIELD, DEFAULTS[INTENSITY_FIELD])
    low, high = LIMITS[INTENSITY_FIELD]
    if type(intensity) is not int or not low <= intensity <= high:
        raise ConfigurationError("role_state_humanized", f"{INTENSITY_FIELD} must be an integer from {low} to {high}")
    retention = values.get("daily_state_patrol_retention_days", DEFAULTS["daily_state_patrol_retention_days"])
    low, high = LIMITS["daily_state_patrol_retention_days"]
    if type(retention) is not int or not low <= retention <= high:
        raise ConfigurationError("role_state_humanized",
                                 f"daily_state_patrol_retention_days must be an integer from {low} to {high}")
    if type(values.get("daily_state_patrol_enabled", DEFAULTS["daily_state_patrol_enabled"])) is not bool:
        raise ConfigurationError("role_state_humanized", "daily_state_patrol_enabled must be a boolean")
    parse_patrol_time(values.get("daily_state_patrol_time", DEFAULTS["daily_state_patrol_time"]))
    return values


def _zone(timezone):
    try:
        return zoneinfo.ZoneInfo(str(timezone or DEFAULT_TIMEZONE))
    except Exception:
        return zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)


def patrol_key(context):
    """One patrol record per role body, shared by every window of that role."""
    return PATROL_PREFIX + digest(role_owner(context))


def empty_document():
    return {"version": 1, "day": None, "reports": []}


def document(runtime, context):
    """The patrol's own records; damaged content reads as never patrolled."""
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (patrol_key(context),))
    if row is None:
        return empty_document()
    try:
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return empty_document()
    if type(value) is not dict or value.get("version") != 1:
        return empty_document()
    reports = value.get("reports")
    if type(reports) is not list or any(type(item) is not dict for item in reports):
        reports = []
    day = value.get("day") if isinstance(value.get("day"), str) else None
    return {"version": 1, "day": day, "reports": reports[-MAX_REPORTS:]}


def save_document(runtime, context, value):
    with runtime.transaction() as connection:
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (patrol_key(context), encode(value)))
    return value


def prune_reports(reports, *, today, retention_days):
    """Keep the retention window and a hard record cap; oldest records go first."""
    kept = []
    for item in reports:
        if type(item) is not dict or not isinstance(item.get("day"), str):
            continue
        try:
            day = date.fromisoformat(item["day"])
        except ValueError:
            continue
        if day >= today - timedelta(days=retention_days):
            kept.append(item)
    kept.sort(key=lambda item: (item["day"], str(item.get("at") or "")))
    return tuple(kept[-MAX_REPORTS:])


def patrol_due(runtime, context, *, settings, now):
    """V1 cadence: once per local day, at the configured time, with catch-up.

    A day whose configured instant has already passed runs as soon as the host is
    alive again, and a day that already produced a record never runs twice.
    """
    if not settings.get("daily_state_patrol_enabled", DEFAULTS["daily_state_patrol_enabled"]):
        return False
    zone = _zone(settings.get("planning_timezone"))
    local_now = normalize_utc(now, field="state patrol time").astimezone(zone)
    if document(runtime, context).get("day") == local_now.date().isoformat():
        return False
    try:
        target = parse_patrol_time(settings.get("daily_state_patrol_time", DEFAULTS["daily_state_patrol_time"]))
    except ConfigurationError:
        return False
    return local_now >= datetime.combine(local_now.date(), target, tzinfo=zone)


def patrol(service, context, *, settings, now):
    """One local-only patrol pass: read the role's own state, record, then prune.

    No model call and no send. The projection already hides expired conditions;
    the patrol records how many there were instead of rewriting the state, so a
    later revision taken by a human operator is never invalidated by a patrol.
    """
    now = normalize_utc(now, field="state patrol time")
    state = service.read(context)  # raises when the caller has no role.state.read
    if state.get("state") == "disabled":
        return {"state": "disabled", "day": None, "kept": 0, "pruned": 0}
    zone = _zone(settings.get("planning_timezone"))
    local_now = now.astimezone(zone)
    retention = settings.get("daily_state_patrol_retention_days",
                             DEFAULTS["daily_state_patrol_retention_days"])
    values = state.get("values") if isinstance(state.get("values"), dict) else {}
    entry = {"day": local_now.date().isoformat(), "at": stamp(now), "revision": int(state.get("revision") or 0),
             "state": str(state.get("state") or "unknown"),
             "live_conditions": len(values.get("conditions") or ()),
             "expired_conditions": int(state.get("expired_conditions") or 0),
             "retention_days": retention, "role_timezone": zone.key}
    current = document(service.runtime, context)
    reports = [item for item in current["reports"] if item.get("day") != entry["day"]]
    reports.append(entry)
    kept = prune_reports(reports, today=local_now.date(), retention_days=retention)
    saved = save_document(service.runtime, context, {"version": 1, "day": entry["day"], "reports": list(kept)})
    return {"state": "recorded", "day": entry["day"], "kept": len(saved["reports"]),
            "pruned": len(reports) - len(kept), "entry": entry}
