"""Inbound private-message anti-harassment guard.

The decision page owns the settings, the action ladder and the blacklist
directory; this module is the enforcement point used by the intake handler
before a turn reaches the model.

Counters are per (scope, user) and live only in memory: a restart starts the
windows over, while entries, strikes and audit are durable. Detectors only
produce reasons and evidence; the action comes from ``harassment.action_for``.
"""

from __future__ import annotations

from time import time

from companion_v2.relationships import harassment

MAX_TRACKED = 4096
MAX_TEXTS = 20
MAX_STRIKES = 10


class _State:
    __slots__ = ("stamps", "texts", "ments", "links", "media", "short", "strikes", "last_strike", "notice_at")

    def __init__(self):
        self.stamps = []
        self.texts = []
        self.ments = []
        self.links = []
        self.media = []
        self.short = []
        self.strikes = 0
        self.last_strike = 0.0
        self.notice_at = 0.0


def _normalized(text):
    return "".join(char for char in (text or "").casefold() if char.isalnum() or "\u4e00" <= char <= "\u9fff")


class HarassmentGuard:
    def __init__(self):
        self._states = {}

    def _state(self, key):
        state = self._states.get(key)
        if state is None:
            state = self._states[key] = _State()
            if len(self._states) > MAX_TRACKED:
                self._states.pop(next(iter(self._states)), None)
        return state

    @staticmethod
    def _window(stamps, now, window):
        return [moment for moment in stamps if now - moment <= window]

    def check(self, runtime, *, scope, user_id, text, mentions=0, media=False, now=None):
        """Return {"blocked": bool, "action": str, "reasons": [...], ...} for one message."""
        now = time() if now is None else float(now)
        scope, user_id = str(scope or ""), str(user_id or "")
        if not scope or not user_id or runtime is None:
            # Before the host runtime exists (early message or a host that never
            # initialized) there is no blacklist to read; the guard must not turn
            # a startup path into an exception.
            return {"blocked": False}
        active = harassment.entry_for(runtime, scope, user_id, at=now)
        if active is not None:
            return {"blocked": True, "entry": active, "action": active.get("kind", "block"),
                    "reasons": tuple(active.get("reasons") or ())}
        settings = harassment.read_settings(runtime)
        if not settings.get("enabled"):
            return {"blocked": False}
        if settings.get("exempt_admins") and str(user_id) in harassment._items(settings.get("exempt_ids")):
            return {"blocked": False}
        state = self._state((scope, user_id))
        reasons, details = self._detect(settings, state, text, mentions, media, now)
        if not reasons:
            return {"blocked": False}
        strike, decayed = harassment.strike_for(settings, {"strikes": state.strikes,
                                                           "last_strike": state.last_strike}, now)
        if decayed:
            state.strikes = 0
        state.strikes = min(strike + 1, MAX_STRIKES)
        state.last_strike = now
        strong = "keyword" in reasons
        action, hours = harassment.action_for(settings, state.strikes, strong=strong)
        evidence = harassment.evidence_text(reasons, details)
        dry = bool(settings.get("dry_run"))
        harassment.record_audit(runtime, scope=scope, user_id=user_id,
                                action=("would_" + action) if dry else action,
                                reasons=reasons, evidence=evidence, at=now)
        if dry:
            return {"blocked": False, "dry_run": True, "action": action, "reasons": tuple(reasons)}
        entry = harassment.add_entry(
            runtime, scope=scope, user_id=user_id, kind=action, reasons=reasons,
            evidence=evidence, strike=state.strikes, hours=hours,
            seconds=settings["cooldown_seconds"] if action == "cooldown" else 0, at=now)
        state.notice_at = now
        return {"blocked": True, "entry": entry, "action": action, "reasons": tuple(reasons),
                "notice": settings.get("notice_text") if settings.get("notice_mode") == "text" else ""}

    def _detect(self, settings, state, text, mentions, media, now):
        reasons, details = [], []
        lowered = (text or "").casefold()
        strong = [word for word in harassment.keywords_strong(settings) if word.casefold() in lowered]
        if strong:
            reasons.append("keyword")
            details.append("强词：%s" % "、".join(strong[:3]))
        weak = [word for word in harassment.keywords_weak(settings) if word.casefold() in lowered]
        if weak:
            reasons.append("keyword")
            details.append("弱词：%s" % "、".join(weak[:3]))
        regex = harassment.regex_hit(settings, text)
        if regex:
            reasons.append("regex")
            details.append("正则：%s" % regex)
        links = harassment.urls(text)
        if links:
            state.links.append((now, len(links)))
        state.links = [(moment, count) for moment, count in state.links
                       if now - moment <= settings["link_window_seconds"]]
        if settings.get("link_enabled") and sum(count for _, count in state.links) > settings["link_max"]:
            reasons.append("link")
            details.append("%d 秒内 %d 条链接" % (settings["link_window_seconds"],
                                                 sum(count for _, count in state.links)))
        state.stamps = self._window(state.stamps, now, max(settings["rate_window_seconds"],
                                                           settings["burst_window_seconds"],
                                                           settings["duplicate_window_seconds"],
                                                           settings["split_window_seconds"]))
        state.stamps.append(now)
        if settings.get("rate_enabled"):
            recent = self._window(state.stamps, now, settings["rate_window_seconds"])
            if len(recent) > settings["rate_max_messages"]:
                reasons.append("rate")
                details.append("%d 秒内 %d 条" % (settings["rate_window_seconds"], len(recent)))
        if settings.get("burst_enabled"):
            recent = self._window(state.stamps, now, settings["burst_window_seconds"])
            if len(recent) > settings["burst_max_messages"]:
                reasons.append("burst")
                details.append("%d 秒内 %d 条" % (settings["burst_window_seconds"], len(recent)))
        normalized = _normalized(text)
        if normalized:
            state.texts.append((now, normalized))
        state.texts = [(moment, value) for moment, value in state.texts
                       if now - moment <= settings["duplicate_window_seconds"]][-MAX_TEXTS:]
        if settings.get("duplicate_enabled") and normalized:
            repeats = sum(1 for _, value in state.texts if value == normalized)
            if repeats >= settings["duplicate_max_repeats"]:
                reasons.append("duplicate")
                details.append("同一句重复 %d 次" % repeats)
        if mentions:
            state.ments.append((now, int(mentions)))
        state.ments = [(moment, count) for moment, count in state.ments
                       if now - moment <= settings["mention_window_seconds"]]
        if settings.get("mention_enabled") and sum(count for _, count in state.ments) > settings["mention_max"]:
            reasons.append("mention")
            details.append("%d 秒内 @ %d 次" % (settings["mention_window_seconds"],
                                                sum(count for _, count in state.ments)))
        if media:
            state.media.append(now)
        state.media = self._window(state.media, now, settings["media_window_seconds"])
        if settings.get("media_enabled") and len(state.media) > settings["media_max"]:
            reasons.append("media")
            details.append("%d 秒内 %d 条媒体" % (settings["media_window_seconds"], len(state.media)))
        short = len(str(text or "").strip()) <= 6
        if short:
            state.short.append(now)
        state.short = self._window(state.short, now, settings["split_window_seconds"])
        if settings.get("split_enabled") and len(state.short) > settings["split_max"]:
            reasons.append("split")
            details.append("%d 秒内 %d 条短消息" % (settings["split_window_seconds"], len(state.short)))
        # 去重并保持稳定顺序，便于页面与证据展示。
        unique = []
        for reason in reasons:
            if reason not in unique:
                unique.append(reason)
        return unique, "；".join(details)

    def reset(self, scope, user_id):
        self._states.pop((str(scope or ""), str(user_id or "")), None)
