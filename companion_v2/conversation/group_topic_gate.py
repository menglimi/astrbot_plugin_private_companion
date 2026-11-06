"""Pressure-aware topic admission for group replies (MVP).

High-pressure groups do not queue one question; several topics arrive while the
role is still answering the previous one.  This gate keeps a bounded topic
backlog per group, spends a reply only on topics that are cheap to close, and
lets the rest expire silently — the unanswered backlog is the debounce.

It is opt-in through the existing high-intensity closeout switch so ordinary
groups keep their current reply rate.  Selection is heuristic, no extra model
call: explicit addresses, short/clear questions score high; long or ambiguous
questions score low.  During the reply window the gate also caps how many
topics may be answered, using the air-guard window/threshold.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from companion_v2.execution.models import digest

PREFIX = "group.topic-gate.v1:"
MAX_PENDING = 8
TTL_SECONDS = 90
ADDRESSED = frozenset((
    "mention", "native_wakeup", "quoted_reply", "native_quote", "direct_word",
    "active_follow_up", "reply_follow_up",
))


def score(reason: str, text: str) -> int:
    """How cheap this topic is to close, from the admission evidence alone."""
    value = 0
    if reason in ADDRESSED:
        value += 3
    cleaned = (text or "").strip()
    if cleaned.endswith(("?", "？")):
        value += 2
    length = len(cleaned)
    if 0 < length <= 30:
        value += 2
    elif length <= 60:
        value += 1
    elif length > 120:
        value -= 2
    if cleaned.count("\n") > 3:
        value -= 1
    return value


def _parse(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def empty_state():
    return {"topics": {}, "replies": []}


def evaluate(state, *, event_id, reason, text, at, enabled, window_seconds, max_replies,
             ttl_seconds=TTL_SECONDS, max_pending=MAX_PENDING):
    """Return ``(allowed, new_state, detail)`` for one candidate event."""
    if not enabled or not isinstance(at, datetime) or at.tzinfo is None:
        return True, state, {"gate": "disabled"}
    topics = {key: dict(value) for key, value in (state.get("topics") or {}).items()
              if isinstance(value, dict)}
    replies = [when for when in (_parse(value) for value in (state.get("replies") or []))
               if when is not None and (at - when).total_seconds() <= window_seconds]
    ttl = max(1, int(ttl_seconds))
    for key in list(topics):
        created = _parse(topics[key].get("created"))
        if created is None or (at - created).total_seconds() > ttl:
            topics.pop(key, None)
    key = event_id or digest([reason, text, at.isoformat()])[:24]
    topic = topics.get(key) or {"created": at.isoformat(), "skipped": 0}
    topic["reason"] = reason
    topic["score"] = score(reason, text)
    topic["last_at"] = at.isoformat()
    topics[key] = topic
    pending = len(topics)
    recent = len(replies)
    detail = {"gate": "pressure", "score": topic["score"], "pending": pending,
              "replies": recent, "window_seconds": window_seconds,
              "max_replies": max_replies}
    if recent >= max(1, int(max_replies)):
        topic["skipped"] += 1
        detail["decision"] = "budget"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies]}, detail
    if pending > max_pending and topic["score"] < 4:
        topic["skipped"] += 1
        detail["decision"] = "backlog"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies]}, detail
    if pending >= 2 and topic["score"] <= 0:
        topic["skipped"] += 1
        detail["decision"] = "hard_topic"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies]}, detail
    topics.pop(key, None)
    replies.append(at)
    detail["decision"] = "answer"
    return True, {"topics": topics, "replies": [when.isoformat() for when in replies]}, detail


def _key(context) -> str:
    return PREFIX + digest(context.scope.key)


def decide(runtime, context, *, event_id, reason, text, at, enabled, window_seconds,
           max_replies, ttl_seconds=TTL_SECONDS):
    """Persist the gate state for one group and return ``(allowed, detail)``."""
    if not enabled:
        return True, {"gate": "disabled"}
    with runtime.transaction() as connection:
        row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
                                 (_key(context),)).fetchone()
        state = json.loads(row["value"]) if row else empty_state()
        allowed, state, detail = evaluate(
            state, event_id=event_id, reason=reason, text=text, at=at, enabled=True,
            window_seconds=window_seconds, max_replies=max_replies, ttl_seconds=ttl_seconds)
        connection.execute(
            "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_key(context), json.dumps(state, ensure_ascii=False)))
    return allowed, detail
