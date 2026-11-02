"""Coalesce private follow-ups that arrive after debounce but before model request.

AstrBot's own debounce ends before the core model request is built. A second
private message can therefore arrive after the quiet window yet still belong to
the same turn. The core runner captures such messages only for tool results, so
a plain conversational reply would otherwise be generated twice.

This owner keeps the pre-request turn open just long enough for the first model
request to read messages that arrived in that gap. Once the request starts, the
window closes: late messages keep the ordinary separate-turn path rather than
being silently folded into a generation that can no longer see them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass
class _ActiveReply:
    key: str
    event: object
    event_ref: str
    entries: list[tuple[object, str, object]]
    applied: int
    created: float
    session_key: str = ""
    request_started: bool = False


class PrivateReplyFollowUps:
    """Small in-memory guard for one plugin process.

    It stores no conversation content and never sends. Entries are retained
    only until the first provider request or a bounded idle timeout.
    """

    def __init__(self, *, clock=time.monotonic, idle_seconds=120.0):
        if type(idle_seconds) not in (int, float) or idle_seconds <= 0:
            raise ValueError("private follow-up idle timeout must be positive")
        self._clock = clock
        self._idle_seconds = float(idle_seconds)
        self._active: dict[str, _ActiveReply] = {}

    def _prune(self):
        now = self._clock()
        for key, active in tuple(self._active.items()):
            if now - active.created > self._idle_seconds:
                self._active.pop(key, None)

    @staticmethod
    def _event_session(event):
        """A stable session identity even when AstrBot hands us a new wrapper."""
        if event is None:
            return ""
        origin = getattr(event, "unified_msg_origin", None)
        if isinstance(origin, str) and origin:
            return origin
        getter = getattr(event, "get_session_id", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = None
            if isinstance(value, str) and value:
                return value
        value = getattr(event, "session_id", None)
        return value if isinstance(value, str) and value else ""

    def start(self, key, event, event_ref, entries):
        """Register the admitted turn immediately before it enters core."""
        self._prune()
        if not isinstance(key, str) or not key or not entries:
            return None
        normalized = tuple((entry[0], str(entry[1]), entry[2] if len(entry) > 2 else None)
                           for entry in entries if len(entry) >= 2 and str(entry[1]).strip())
        if not normalized:
            return None
        active = _ActiveReply(key=key, event=event, event_ref=str(event_ref or ""),
                              entries=list(normalized), applied=len(normalized),
                              created=self._clock(), session_key=self._event_session(event))
        self._active[key] = active
        return active

    def fold(self, key, event, text):
        """Append a follow-up only while the first provider request is pending."""
        self._prune()
        active = self._active.get(key)
        if active is None:
            return None
        if self._clock() - active.created > self._idle_seconds:
            self._active.pop(key, None)
            return None
        if active.event is event or active.request_started:
            return None
        body = str(text or "").strip()
        if not body:
            return None
        active.entries.append((event, body, getattr(event, "created_at", None)))
        return active

    def for_request(self, event):
        """Close the merge window and return entries not yet present in the request."""
        self._prune()
        session_key = self._event_session(event)
        active = next((item for item in self._active.values() if item.event is event), None)
        if active is None and session_key:
            active = next((item for item in self._active.values()
                           if item.session_key == session_key), None)
        if active is None:
            return None
        active.request_started = True
        pending = tuple(active.entries[active.applied:])
        active.applied = len(active.entries)
        return pending, tuple(active.entries)

    def finish(self, event):
        """Release a turn after its provider run or delivery is over.

        Lifecycle hooks may receive a new event wrapper for the same session;
        object identity alone would then leave the old merge window open, and
        every later private message would be folded into a finished turn.
        """
        session_key = self._event_session(event)
        removed = False
        for key, active in tuple(self._active.items()):
            if active.event is event or (session_key and active.session_key == session_key):
                self._active.pop(key, None)
                removed = True
        return removed

    def active_for(self, key):
        self._prune()
        return self._active.get(key)

    def close(self):
        self._active.clear()
