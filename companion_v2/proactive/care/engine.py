"""Appraisal for care concerns: cue in, impulse out, usually no action.

The engine is intentionally deterministic and side-effect free.  It answers one
question: given the current device picture and the character's standing
concerns, what would it *notice* right now?  Execution belongs to a later phase
and a different layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .models import Concern, DeviceObservation, Impulse

CONCERNS: tuple[Concern, ...] = (
    Concern(
        id="sleep_light",
        label="你睡觉不关灯",
        note="灯一直亮着的时候，它会惦记你睡不好。",
        weight="high",
        supported=True,
        cue="夜里灯还亮着",
    ),
    Concern(
        id="ac_temperature",
        label="空调开得太低",
        note="夜里温度太低时，它会担心你着凉。",
        weight="medium",
        supported=True,
        cue="夜里空调低于 24°C",
    ),
    Concern(
        id="humidity_low",
        label="房间有点干",
        note="湿度低的时候，它会记着，但还没有能替你做的事。",
        weight="low",
        supported=True,
        cue="湿度低于 35%",
    ),
    Concern(
        id="sedentary",
        label="你坐得太久",
        note="还没有可靠的久坐线索，所以它暂时只会想想。",
        weight="low",
        supported=False,
        cue="",
    ),
)

_WEIGHTS = ("low", "medium", "high")


ACTIONS: dict[str, dict[str, Any]] = {
    "sleep_light": {"risk": "low", "label": "关灯", "default_enabled": True,
                    "patience_minutes": 30,
                    "description": "只在你安静下来、灯还亮着时关掉。"},
    "ac_temperature": {"risk": "medium", "label": "调整空调温度",
                       "default_enabled": False, "patience_minutes": 45,
                       "description": "只会往舒适方向调 1–2°C，不会调得更冷。"},
}


def normalize_settings(raw: Any) -> dict[str, Any]:
    """Return a bounded, serialisable care configuration."""
    raw = raw if isinstance(raw, Mapping) else {}
    stored = raw.get("concerns") if isinstance(raw.get("concerns"), Mapping) else {}
    concerns: dict[str, dict[str, Any]] = {}
    for concern in CONCERNS:
        item = stored.get(concern.id) if isinstance(stored.get(concern.id), Mapping) else {}
        weight = item.get("weight") if item.get("weight") in _WEIGHTS else concern.weight
        concerns[concern.id] = {
            "enabled": item.get("enabled", concern.supported) is True,
            "weight": weight,
            "note": str(item.get("note") or "")[:80],
        }
    impulses: list[dict[str, Any]] = []
    for item in raw.get("impulses") or []:
        if not isinstance(item, Mapping) or not item.get("at"):
            continue
        impulses.append({
            "concern_id": str(item.get("concern_id") or "")[:40],
            "alias": str(item.get("alias") or "")[:64],
            "title": str(item.get("title") or "")[:80],
            "reason": str(item.get("reason") or "")[:160],
            "verdict": str(item.get("verdict") or "held")[:16],
            "action": str(item.get("action") or "")[:40],
            "at": str(item.get("at"))[:32],
            "marker": str(item.get("marker") or "")[:120],
        })
    actions_raw = raw.get("actions") if isinstance(raw.get("actions"), Mapping) else {}
    legacy_counter = isinstance(actions_raw.get("date"), str) or "count" in actions_raw
    counters_raw = actions_raw if legacy_counter else (
        raw.get("counters") if isinstance(raw.get("counters"), Mapping) else {})
    if legacy_counter:
        actions_raw = {}
    legacy_enabled = raw.get("action_enabled")
    actions: dict[str, dict[str, Any]] = {}
    for action_id, meta in ACTIONS.items():
        item = actions_raw.get(action_id) if isinstance(actions_raw.get(action_id), Mapping) else {}
        default_enabled = meta["default_enabled"] is True
        if action_id == "sleep_light" and legacy_enabled is not None and not item:
            default_enabled = legacy_enabled is True
        actions[action_id] = {
            "enabled": item.get("enabled", default_enabled) is True,
            "daily_cap": _bounded_int(item.get("daily_cap"), 1, 5, 1),
            "patience_minutes": _bounded_int(
                item.get("patience_minutes"),
                15,
                120,
                meta["patience_minutes"],
            ),
        }
    counts_raw = counters_raw.get("by_action") if isinstance(counters_raw.get("by_action"), Mapping) else {}
    counts = {action_id: _bounded_int(counts_raw.get(action_id), 0, 20, 0)
              for action_id in ACTIONS}
    return {"enable": raw.get("enable") is True, "concerns": concerns,
            "actions": actions,
            "counters": {"date": str(counters_raw.get("date") or "")[:10], "by_action": counts},
            "feedback": _feedback(raw.get("feedback")),
            "impulses": impulses[-40:]}


def _feedback(raw: Any) -> list[dict[str, Any]]:
    result = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or item.get("verdict") not in ("praise", "pause", "allow"):
            continue
        result.append({"concern_id": str(item.get("concern_id") or "")[:40],
                       "verdict": str(item.get("verdict")),
                       "at": str(item.get("at") or "")[:32]})
    return result[-30:]


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(maximum, max(minimum, number))


def _light_impulse(observation: DeviceObservation, now: datetime) -> Impulse | None:
    if not observation.is_light or not observation.is_on() or observation.online is False:
        return None
    if not (now.hour >= 22 or now.hour < 3):
        return None
    return Impulse(
        concern_id="sleep_light",
        alias=observation.alias,
        title=f"{observation.alias}还亮着",
        reason="夜里的灯一直亮着。它先记着，还没有替你关。",
        verdict="held",
        action="power_off",
        at=now.isoformat(timespec="seconds"),
        marker=f"sleep_light:{observation.alias}:{now.date().isoformat()}",
    )


def _temperature_impulse(observation: DeviceObservation, now: datetime) -> Impulse | None:
    if not observation.is_air_conditioner or not observation.is_on():
        return None
    if not (now.hour >= 23 or now.hour < 7):
        return None
    try:
        target = int(observation.state.get("target_temperature"))
    except (TypeError, ValueError):
        return None
    if target >= 24:
        return None
    return Impulse(
        concern_id="ac_temperature",
        alias=observation.alias,
        title=f"{observation.alias}设到 {target}°C",
        reason="夜里这个温度偏低，它有点担心；但怕吵醒你，先没有动。",
        verdict="held",
        action="target_temperature",
        at=now.isoformat(timespec="seconds"),
        marker=f"ac_temperature:{observation.alias}:{now.date().isoformat()}",
    )


def _humidity_impulse(observation: DeviceObservation, now: datetime) -> Impulse | None:
    try:
        humidity = int(observation.state.get("humidity"))
    except (TypeError, ValueError):
        return None
    if humidity >= 35:
        return None
    return Impulse(
        concern_id="humidity_low",
        alias=observation.alias,
        title=f"{observation.alias}湿度 {humidity}%",
        reason="房间有点干，它先记着；还没有想好要替你做什么。",
        verdict="held",
        action="",
        at=now.isoformat(timespec="seconds"),
        marker=f"humidity_low:{observation.alias}:{now.date().isoformat()}",
    )


def appraise(settings: Mapping[str, Any], observations: Sequence[DeviceObservation],
             now: datetime) -> list[dict[str, Any]]:
    """Return the impulses this moment produces; never performs an action."""
    if settings.get("enable") is not True:
        return []
    concerns = settings.get("concerns") or {}
    result: list[dict[str, Any]] = []
    for concern_id, factory in (("sleep_light", _light_impulse),
                                 ("ac_temperature", _temperature_impulse),
                                 ("humidity_low", _humidity_impulse)):
        config = concerns.get(concern_id) or {}
        if config.get("enabled") is not True:
            continue
        for observation in observations:
            impulse = factory(observation, now)
            if impulse is not None:
                result.append(impulse.as_dict())
    return result
