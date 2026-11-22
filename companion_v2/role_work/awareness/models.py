"""Bounded role-level cross-window presence documents.

The record answers one question: *what did this role recently see the same
person do in another window?*  It deliberately stores metadata only.  Group
turns keep the current speaker's own public text as a short topic; private
turns keep no text at all.  Nothing here copies another window's transcript,
another member's words, or a private message into a role-wide document.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

from companion_v2.execution.models import digest, encode, stamp
from companion_v2.foundation.refs import ScopeKind


VERSION = "role.awareness.v1"
PREFIX = VERSION + ":"

MAX_EVENTS = 64
RETENTION_HOURS = 24
WINDOW_HOURS = 12
MAX_PROMPT_ITEMS = 4
MAX_ALLOWED_PROMPT_ITEMS = 8
MAX_TOPIC_CHARS = 96
MAX_EVENT_KEY_CHARS = 256
MAX_DOCUMENT_CHARS = 65536
ATTENTION_MINUTES = 30

WINDOWS = frozenset({"private", "group"})
EVENT_KINDS = frozenset({"spoke", "reply", "peer_reply", "together_room"})
_TOPIC_PLACEHOLDER = re.compile(r"\[[^\]]{0,24}\]")
_TOPIC_SPACE = re.compile(r"\s+")


def window_of(context) -> str:
    return "group" if context.scope.kind is ScopeKind.GROUP else "private"


def role_ref(context) -> dict:
    scope, role = context.scope, context.role_binding.role
    return {"installation_id": scope.installation_id, "bot_id": scope.bot_id, "role_id": role.subject_id}


def owner_key(context) -> str:
    role = role_ref(context)
    return digest([VERSION, role["installation_id"], role["bot_id"], role["role_id"]])


def empty_document(context, at: datetime) -> dict:
    return {"version": VERSION, "role": role_ref(context), "events": [],
            "attention": {"last_inbound_window": "", "last_inbound_at": "",
                          "last_reply_window": "", "last_reply_at": ""},
            "updated_at": stamp(at)}


def clean_topic(text, maximum: int = MAX_TOPIC_CHARS) -> str:
    """Keep a short, display-safe slice of the speaker's own wording.

    Long messages keep both their opening and closing words so a question at
    the end is not lost when continuity looks back at the recent exchange.
    """
    value = _TOPIC_PLACEHOLDER.sub(" ", str(text or ""))
    value = " ".join(_TOPIC_SPACE.split(value)).strip()
    if maximum <= 1:
        return value[:max(0, maximum)]
    if len(value) <= maximum:
        return value
    head = max(1, maximum * 2 // 3)
    tail = maximum - head - 1
    return value[:head] + "…" + (value[-tail:] if tail else "")


def event_record(context, at: datetime, text: str, event_key: str = "", source=None, peer_count: int = 0) -> dict:
    window = window_of(context)
    peers = peer_count if type(peer_count) is int and 0 <= peer_count <= 64 else 0
    return {"at": stamp(at), "window": window,
            "group_id": context.scope.native_scope_id if window == "group" else "",
            "platform": context.scope.platform, "account_id": context.scope.account_id or "",
            "subject": context.actor.subject.subject_id,
            "speaker_account": context.actor.principal.native_account_id,
            "kind": "spoke",
            "topic": clean_topic(text) if window == "group" else "",
            "event_key": str(event_key or "")[:MAX_EVENT_KEY_CHARS],
            "source": json.loads(encode(source)) if source is not None else None,
            "peer_count": peers if window == "group" else 0}


def reply_record(context, at: datetime, text: str, event_key: str = "") -> dict:
    """One bounded reply authored by this same role in a group window."""
    if window_of(context) != "group":
        raise ValueError("reply continuity records are group-only")
    speaker_account = str(context.scope.account_id or context.role_binding.role.subject_id or "")
    if not speaker_account:
        raise ValueError("reply continuity requires a stable speaker account")
    return {"at": stamp(at), "window": "group",
            "group_id": context.scope.native_scope_id,
            "platform": context.scope.platform, "account_id": context.scope.account_id or "",
            "subject": context.role_binding.role.subject_id,
            "speaker_account": speaker_account,
            "kind": "reply", "topic": clean_topic(text),
            "event_key": str(event_key or "")[:MAX_EVENT_KEY_CHARS],
            "source": None, "peer_count": 0}


def peer_reply_record(context, at: datetime, subject: str, event_key: str = "", source=None) -> dict:
    """One other member quoted/replied to a prior group speaker; no text is kept."""
    return {"at": stamp(at), "window": "group",
            "group_id": context.scope.native_scope_id,
            "platform": context.scope.platform, "account_id": context.scope.account_id or "",
            "subject": subject,
            "speaker_account": context.actor.principal.native_account_id,
            "kind": "peer_reply", "topic": "",
            "event_key": str(event_key or "")[:MAX_EVENT_KEY_CHARS],
            "source": json.loads(encode(source)) if source is not None else None,
            "peer_count": 0}


def together_record(context, at: datetime, text: str, event_key: str = "") -> dict:
    """One desensitized, private-only summary from a single-user Together room."""
    if window_of(context) != "private":
        raise ValueError("together room awareness records are private-only")
    topic = clean_topic(text)
    if not topic:
        raise ValueError("together room awareness record requires non-empty topic")
    return {"at": stamp(at), "window": "private",
            "group_id": "",
            "platform": context.scope.platform, "account_id": context.scope.account_id or "",
            "subject": context.actor.subject.subject_id,
            "speaker_account": context.actor.principal.native_account_id,
            "kind": "together_room", "topic": topic,
            "event_key": str(event_key or "")[:MAX_EVENT_KEY_CHARS],
            "source": None, "peer_count": 0}


def _when(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if type(value) is str else None
    except ValueError:
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _valid_event(item) -> bool:
    if type(item) is not dict or set(item) != {
            "at", "window", "group_id", "platform", "account_id", "subject", "speaker_account",
            "kind", "topic", "event_key", "source", "peer_count"}:
        return False
    if _when(item["at"]) is None or len(item["at"]) > 64:
        return False
    if item["window"] not in WINDOWS or item["kind"] not in EVENT_KINDS:
        return False
    if type(item["group_id"]) is not str or len(item["group_id"]) > 256:
        return False
    if item["window"] == "private" and item["group_id"]:
        return False
    if any(type(item[name]) is not str or len(item[name]) > 512 for name in ("platform", "account_id", "speaker_account")):
        return False
    if not item["speaker_account"]:
        return False
    if type(item["subject"]) is not str or not item["subject"] or len(item["subject"]) > 512:
        return False
    if type(item["topic"]) is not str or len(item["topic"]) > MAX_TOPIC_CHARS:
        return False
    if item["kind"] == "reply":
        if item["window"] != "group" or not item["topic"] or item["peer_count"] != 0:
            return False
    elif item["kind"] == "peer_reply":
        if item["window"] != "group" or item["topic"] or item["peer_count"] != 0:
            return False
    elif item["kind"] == "together_room":
        if item["window"] != "private" or not item["topic"] or item["peer_count"] != 0:
            return False
    elif item["window"] == "private" and item["topic"]:
        return False
    if type(item["peer_count"]) is not int or not 0 <= item["peer_count"] <= 64:
        return False
    if item["window"] == "private" and item["peer_count"]:
        return False
    if item["source"] is not None and type(item["source"]) is not dict:
        return False
    return type(item["event_key"]) is str and len(item["event_key"]) <= MAX_EVENT_KEY_CHARS


def _valid_attention(item) -> bool:
    if type(item) is not dict or set(item) != {
            "last_inbound_window", "last_inbound_at", "last_reply_window", "last_reply_at"}:
        return False
    for window, at in (("last_inbound_window", "last_inbound_at"),
                       ("last_reply_window", "last_reply_at")):
        if item[window] not in ("", *WINDOWS) or type(item[at]) is not str or len(item[at]) > 64:
            return False
        if item[at] and _when(item[at]) is None:
            return False
    return True


def valid_document(value) -> bool:
    if type(value) is not dict or set(value) != {"version", "role", "events", "attention", "updated_at"}:
        return False
    if len(encode(value)) > MAX_DOCUMENT_CHARS:
        return False
    role = value["role"]
    if value["version"] != VERSION or type(role) is not dict or set(role) != {
            "installation_id", "bot_id", "role_id"}:
        return False
    if any(type(role[name]) is not str or not role[name] or len(role[name]) > 512
           for name in ("installation_id", "bot_id", "role_id")):
        return False
    if _when(value["updated_at"]) is None or len(value["updated_at"]) > 64:
        return False
    if type(value["events"]) is not list or len(value["events"]) > MAX_EVENTS:
        return False
    if any(not _valid_event(item) for item in value["events"]):
        return False
    return _valid_attention(value["attention"])


def prune(events, at: datetime) -> list:
    cutoff = at - timedelta(hours=RETENTION_HOURS)
    live = [item for item in events
            if (when := _when(item["at"])) is not None and when >= cutoff]
    live.sort(key=lambda item: item["at"])
    return live[-MAX_EVENTS:]


__all__ = [
    "VERSION", "PREFIX", "MAX_EVENTS", "RETENTION_HOURS", "WINDOW_HOURS", "MAX_PROMPT_ITEMS",
    "MAX_ALLOWED_PROMPT_ITEMS", "MAX_TOPIC_CHARS", "MAX_DOCUMENT_CHARS", "ATTENTION_MINUTES",
    "WINDOWS", "window_of", "role_ref", "owner_key", "empty_document", "clean_topic",
    "event_record", "reply_record", "peer_reply_record", "together_record", "valid_document",
    "prune",
]
