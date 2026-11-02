"""Inbound private-message anti-harassment guard.

The decision page owns the settings and the blacklist directory; this module is
the enforcement point used by the intake handler before a turn reaches the model.
Counters are per (scope, user) and live only in memory: a restart just starts the
window over, while the blacklist itself is durable.
"""

from __future__ import annotations

from time import time

from companion_v2.relationships import harassment

MAX_TRACKED = 4096


class HarassmentGuard:
    def __init__(self):
        self._hits = {}

    def _recent(self, key, now, window):
        hits = [moment for moment in self._hits.get(key, ()) if now - moment <= window]
        if not hits and key in self._hits:
            self._hits.pop(key, None)
        return hits

    def check(self, runtime, *, scope, user_id, text, now=None):
        """Return {"blocked": bool, ...} for one private inbound message."""
        now = time() if now is None else float(now)
        scope = str(scope or "")
        user_id = str(user_id or "")
        if not scope or not user_id:
            return {"blocked": False}
        existing = harassment.entry_for(runtime, scope, user_id)
        if existing is not None:
            return {"blocked": True, "entry": existing, "triggered": ""}
        settings = harassment.read_settings(runtime)
        if not settings.get("enabled"):
            return {"blocked": False}
        key = (scope, user_id)
        hits = self._recent(key, now, settings["window_seconds"])
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > MAX_TRACKED:
            self._hits.pop(next(iter(self._hits)), None)
        reason = harassment.reason_for(settings, len(hits), text)
        if not reason:
            return {"blocked": False}
        entry = harassment.add_entry(runtime, scope=scope, user_id=user_id, reason=reason,
                                     count=len(hits), at=now)
        self._hits.pop(key, None)
        return {"blocked": True, "entry": entry, "triggered": reason}

    def reset(self, scope, user_id):
        self._hits.pop((str(scope or ""), str(user_id or "")), None)
