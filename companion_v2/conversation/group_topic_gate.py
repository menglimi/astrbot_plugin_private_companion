"""Pressure-aware topic admission for group replies.

High-pressure groups do not queue one question; several topics arrive while the
role is still answering the previous one.  This gate keeps a bounded topic
backlog per group, spends a reply only on topics that are cheap to close, and
lets the rest expire silently — the unanswered backlog is the debounce.

It is opt-in through the existing high-intensity closeout switch so ordinary
groups keep their current reply rate.  Selection is heuristic, no extra model
call: explicit addresses, short/clear questions score high; long or ambiguous
questions score low.  The gate also caps how many topics may be answered inside
the air-guard window, gives repeatedly skipped topics a forced turn, and applies
a small close-out feedback penalty when the same sender immediately continues
the same kind of topic after an answer.
"""

from __future__ import annotations

import json
from datetime import datetime

from companion_v2.execution.models import digest

PREFIX = "group.topic-gate.v1:"
MAX_PENDING = 8
TTL_SECONDS = 90
FEEDBACK_SECONDS = 45
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
    return {"topics": {}, "replies": [], "answered": []}


def _finished(topics, replies, answered, at, reason, sender):
    history = [item for item in answered
               if isinstance(item, dict) and _parse(item.get("at")) is not None]
    history.append({"at": at.isoformat(), "reason": reason, "sender": sender})
    return {"topics": topics,
            "replies": [when.isoformat() for when in replies],
            "answered": history[-8:]}


def evaluate(state, *, event_id, reason, text, at, enabled, window_seconds, max_replies,
             ttl_seconds=TTL_SECONDS, max_pending=MAX_PENDING, max_skip=3, sender=""):
    """Return ``(allowed, new_state, detail)`` for one candidate event."""
    if not enabled or not isinstance(at, datetime) or at.tzinfo is None:
        return True, state, {"gate": "disabled"}
    topics = {key: dict(value) for key, value in (state.get("topics") or {}).items()
              if isinstance(value, dict)}
    replies = [when for when in (_parse(value) for value in (state.get("replies") or []))
               if when is not None and (at - when).total_seconds() <= window_seconds]
    answered = [item for item in (state.get("answered") or [])
                if isinstance(item, dict) and _parse(item.get("at")) is not None]
    ttl = max(1, int(ttl_seconds))
    for key in list(topics):
        created = _parse(topics[key].get("created"))
        if created is None or (at - created).total_seconds() > ttl:
            topics.pop(key, None)
    key = event_id or digest([reason, text, at.isoformat()])[:24]
    topic = topics.get(key) or {"created": at.isoformat(), "skipped": 0}
    penalty = 0
    if sender:
        for item in answered:
            when = _parse(item.get("at"))
            if (when is not None and item.get("sender") == sender
                    and item.get("reason") == reason
                    and (at - when).total_seconds() <= FEEDBACK_SECONDS):
                penalty = -2
                break
    topic["reason"] = reason
    topic["score"] = score(reason, text) + penalty
    topic["sender"] = sender or ""
    topic["last_at"] = at.isoformat()
    topics[key] = topic
    pending = len(topics)
    recent = len(replies)
    detail = {"gate": "pressure", "score": topic["score"], "pending": pending,
              "replies": recent, "window_seconds": window_seconds,
              "max_replies": max_replies, "feedback": penalty,
              "skipped": topic["skipped"]}
    if recent >= max(1, int(max_replies)):
        topic["skipped"] += 1
        detail["decision"] = "budget"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies],
                       "answered": answered[-8:]}, detail
    if topic["skipped"] >= max(1, int(max_skip)):
        # 公平性兜底：一直被跳过的话题在预算内强制回答一次，避免永远被更容易的话题挤掉。
        detail["decision"] = "starvation"
        topics.pop(key, None)
        replies.append(at)
        return True, _finished(topics, replies, answered, at, reason, sender), detail
    others = [value for key_, value in topics.items() if key_ != key]
    best_other = max((int(value.get("score", 0)) for value in others), default=None)
    detail["best_pending"] = best_other
    if pending > max_pending and topic["score"] < 4:
        topic["skipped"] += 1
        detail["decision"] = "backlog"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies],
                       "answered": answered[-8:]}, detail
    if pending >= 2 and topic["score"] <= 0:
        topic["skipped"] += 1
        detail["decision"] = "hard_topic"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies],
                       "answered": answered[-8:]}, detail
    if best_other is not None and best_other > topic["score"] + 1:
        # 已经有一个更收得尾的话题在排队：本轮先不答当前这条，等它下次再来或过期。
        topic["skipped"] += 1
        detail["decision"] = "easy_first"
        return False, {"topics": topics, "replies": [when.isoformat() for when in replies],
                       "answered": answered[-8:]}, detail
    topics.pop(key, None)
    replies.append(at)
    detail["decision"] = "answer"
    return True, _finished(topics, replies, answered, at, reason, sender), detail


def _key(context) -> str:
    return PREFIX + digest(context.scope.key)


def decide(runtime, context, *, event_id, reason, text, at, enabled, window_seconds,
           max_replies, ttl_seconds=TTL_SECONDS):
    """Persist the gate state for one group and return ``(allowed, detail)``."""
    if not enabled:
        return True, {"gate": "disabled"}
    sender = getattr(getattr(context.actor, "principal", None), "native_account_id", "")
    with runtime.transaction() as connection:
        row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
                                 (_key(context),)).fetchone()
        state = json.loads(row["value"]) if row else empty_state()
        allowed, state, detail = evaluate(
            state, event_id=event_id, reason=reason, text=text, at=at, enabled=True,
            window_seconds=window_seconds, max_replies=max_replies,
            ttl_seconds=ttl_seconds, sender=sender or "")
        connection.execute(
            "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_key(context), json.dumps(state, ensure_ascii=False)))
    return allowed, detail
