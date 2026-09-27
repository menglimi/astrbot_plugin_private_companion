"""Deterministic joke-boundary guard for the Companion persona.

Group teasing is fun until it is not.  This contract tracks per-member joke
sensitivity from lightweight signals — message recalls (撤回), serious
objections (严肃反对 like "别拿我开玩笑"), and cold-silence reactions — and
offers a mood-correction plus an explicit "do not joke" flag the runtime
adapter can persist.  The contract owns no persistence, clock, network, or
platform access.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

from .group_mood import project_group_mood

JOKE_BOUNDARY_VERSION = "joke_boundary.v1"

# Sensitivity 0-100.  Soft floor/ceiling so one signal never locks a member
# in, and repeated objections accumulate.
_MIN_SENSITIVITY = 0.0
_MAX_SENSITIVITY = 100.0
_OBJECTION_STEP = 28.0
_RECALL_STEP = 12.0
_SILENCE_STEP = 6.0
_CALM_DECAY_HALF_LIFE = 14 * 24 * 3600.0  # two weeks
_BLOCK_THRESHOLD = 60.0

# Serious-objection phrasing markers (inbound text).
_OBJECTION_PATTERNS = (
    r"别拿我开玩笑",
    r"不要开我玩笑",
    r"别开这种玩笑",
    r"这不是玩笑",
    r"我不是在开玩笑",
    r"一点都不好笑",
    r"不好笑",
    r"过分了",
    r"玩笑适可而止",
    r"别再这样了",
    r"我真的生气了",
    r"生气了",
    r"别闹了",
)


def _finite(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _clamp(value: float) -> float:
    return max(_MIN_SENSITIVITY, min(_MAX_SENSITIVITY, value))


def _member_key(message: Any) -> str:
    if isinstance(message, Mapping):
        sender_id = message.get("sender_id") or message.get("user_id") or ""
        name = message.get("name") or message.get("sender_name") or ""
        return str(sender_id or name or "")
    return ""


def _message_key(message: Any, index: int) -> str:
    message_id = ""
    if isinstance(message, Mapping):
        message_id = str(message.get("message_id") or message.get("id") or message.get("signal_id") or "").strip()
        if message_id:
            return f"id:{message_id}"
        ts = str(message.get("ts") or message.get("timestamp") or "").strip()
        sender = _member_key(message)
        kind = str(message.get("kind") or message.get("signal") or "").strip()
        text = str(message.get("text") or message.get("content") or message.get("message") or "").strip()
        if ts:
            return f"msg:{ts}|{sender}|{kind}|{text}"
        if sender or kind or text:
            return f"msg:{index}|{sender}|{kind}|{text}"
    return f"idx:{index}"


def is_serious_objection(text: Any) -> bool:
    lowered = str(text or "").lower()
    return any(re.search(pattern, lowered) for pattern in _OBJECTION_PATTERNS)


def project_joke_boundary(
    value: Any,
    *,
    now: Any = None,
) -> dict[str, Any]:
    """Project a persisted per-member sensitivity snapshot with decay."""
    raw = dict(value) if isinstance(value, Mapping) else {}
    if raw.get("version") != JOKE_BOUNDARY_VERSION:
        return {}
    current_ts = max(0.0, _finite(now)) if now is not None else 0.0
    members: dict[str, Any] = {}
    stored = raw.get("members")
    if isinstance(stored, Mapping):
        for member_id, entry in stored.items():
            entry = dict(entry) if isinstance(entry, Mapping) else {}
            sensitivity = _finite(entry.get("sensitivity"))
            updated = _finite(entry.get("updated_at"))
            if sensitivity <= 0:
                continue
            elapsed = max(0.0, current_ts - updated) if current_ts and updated else 0.0
            if _CALM_DECAY_HALF_LIFE > 0:
                sensitivity = sensitivity * (0.5 ** (elapsed / _CALM_DECAY_HALF_LIFE))
            if sensitivity < 4.0:
                continue
            members[str(member_id)] = {
                "sensitivity": round(_clamp(sensitivity), 2),
                "objection_count": max(0, int(_finite(entry.get("objection_count")))),
                "recall_count": max(0, int(_finite(entry.get("recall_count")))),
                "updated_at": updated,
            }
    return {
        "version": JOKE_BOUNDARY_VERSION,
        "members": members,
        "updated_at": _finite(raw.get("updated_at")),
        "processed_message_keys": [
            str(key) for key in (raw.get("processed_message_keys") or []) if str(key)
        ][-128:],
    }


def settle_joke_boundary(
    existing: Any,
    *,
    messages: Iterable[Any],
    now: Any = None,
) -> dict[str, Any]:
    """Fold inbound signals into per-member joke sensitivity.

    Signal detection is intentionally cheap and local: objection phrases,
    recall events (``kind == "recall"`` or a message whose text starts with
    the recall marker), and a same-window silence note are combined.
    """
    current_ts = max(0.0, _finite(now)) if now is not None else 0.0
    prior = project_joke_boundary(existing, now=current_ts)
    processed = {
        str(key)
        for key in (prior.get("processed_message_keys") or [])
        if str(key)
    }
    processed_order = list(processed)
    members: dict[str, dict[str, Any]] = {
        str(key): dict(entry) for key, entry in (prior.get("members") or {}).items()
    }
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            continue
        key = _message_key(message, index)
        if key in processed:
            continue
        processed.add(key)
        processed_order.append(key)
        key = _member_key(message)
        if not key:
            continue
        entry = members.get(key)
        if entry is None:
            entry = {"sensitivity": 0.0, "objection_count": 0, "recall_count": 0, "updated_at": current_ts}
            members[key] = entry
        kind = str(message.get("kind") or message.get("signal") or "")
        text = str(message.get("text") or message.get("content") or message.get("message") or "")
        step = 0.0
        counter_key: str | None = None
        if kind in {"recall", "withdraw", "withdrawal"} or text.startswith("<!-- private_companion_recall"):
            step = _RECALL_STEP
            counter_key = "recall_count"
        if is_serious_objection(text):
            step = max(step, _OBJECTION_STEP)
            counter_key = "objection_count"
        if kind in {"dead_silence", "cold", "awkward_silence"}:
            step = max(step, _SILENCE_STEP)
        if step > 0:
            entry["sensitivity"] = _clamp(_finite(entry.get("sensitivity")) + step)
            if counter_key:
                entry[counter_key] = int(_finite(entry.get(counter_key))) + 1
            entry["updated_at"] = current_ts
    return {
        "version": JOKE_BOUNDARY_VERSION,
        "members": members,
        "updated_at": current_ts,
        "processed_message_keys": processed_order[-128:],
    }


def correct_mood_for_member(
    mood: Any,
    boundary: Any,
    *,
    member_id: Any = None,
    now: Any = None,
) -> dict[str, Any]:
    """Reduce playful mood energy when the targeted member dislikes jokes.

    Returns the original mood projection with an added ``joke_guard`` and
    dampened ``tease``/``banter`` scores.  Being conservative, the correction
    scales with sensitivity once it passes the block threshold.
    """
    projected = project_group_mood(mood, now=now)
    if not projected:
        return projected
    boundary = project_joke_boundary(boundary, now=now)
    members = boundary.get("members") or {}
    key = str(member_id or "")
    member = members.get(key) if key else {}
    sensitivity = _finite(member.get("sensitivity") if isinstance(member, Mapping) else 0.0)
    guard = "normal"
    dampen = 0.0
    if sensitivity >= _BLOCK_THRESHOLD:
        guard = "blocked"
        dampen = 0.85
    elif sensitivity >= _BLOCK_THRESHOLD * 0.55:
        guard = "caution"
        dampen = 0.5
    if dampen > 0:
        scores = dict(projected.get("mood_scores") or {})
        scores["tease"] = round(max(0.0, _finite(scores.get("tease")) * (1.0 - dampen)), 2)
        scores["banter"] = round(max(0.0, _finite(scores.get("banter")) * (1.0 - dampen)), 2)
        projected = {
            **projected,
            "mood_scores": scores,
            "social_tension": round(max(0.0, _finite(projected.get("social_tension")) * (1.0 - dampen * 0.5)), 2),
        }
    projected["joke_guard"] = guard
    projected["member_sensitivity"] = round(sensitivity, 2)
    return projected


def joke_guard_suggestion(boundary: Any, *, member_id: Any = None) -> dict[str, Any]:
    """Return a stable guard decision without prompt-facing prose."""
    boundary = project_joke_boundary(boundary, now=0)
    members = boundary.get("members") or {}
    key = str(member_id or "")
    member = members.get(key) if key else {}
    sensitivity = _finite(member.get("sensitivity") if isinstance(member, Mapping) else 0.0)
    if sensitivity >= _BLOCK_THRESHOLD:
        return {
            "blocked": True,
            "reason_code": "repeated_serious_objection_or_recall",
            "sensitivity": round(sensitivity, 2),
        }
    if sensitivity >= _BLOCK_THRESHOLD * 0.55:
        return {
            "blocked": False,
            "reason_code": "low_joke_acceptance",
            "sensitivity": round(sensitivity, 2),
        }
    return {
        "blocked": False,
        "reason_code": "",
        "sensitivity": round(sensitivity, 2),
    }


__all__ = [
    "JOKE_BOUNDARY_VERSION",
    "is_serious_objection",
    "project_joke_boundary",
    "settle_joke_boundary",
    "correct_mood_for_member",
    "joke_guard_suggestion",
    "_BLOCK_THRESHOLD",
]
