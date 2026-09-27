# -*- coding: utf-8 -*-
"""天气域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 103 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

from .helpers import _safe_float, _safe_int, _single_line, _today_key
from typing import Any



class ProactiveEngineWeatherMixin:
    """天气域（从 ProactiveEngineMixin 拆出）。"""


    def _weather_proactive_block_reason(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float,
    ) -> str:
        """Keep ordinary weather nudges from crowding out other proactive sources."""
        source = _single_line(candidate.get("source"), 40).lower()
        weather_linked = bool(candidate.get("weather_linked"))
        if source not in {"weather_alert", "environment_change", "weather_context"} and not weather_linked:
            return ""
        if candidate.get("_mobile_location_transition_key") and weather_linked:
            # A confirmed departure is a location event with a weather-aware
            # wording, not a free-standing weather nudge.
            return ""
        score = _safe_int(candidate.get("score"), 0, 0, 100)
        # Official high-severity alerts remain available; they are safety events,
        # not ordinary weather chatter.
        if source in {"weather_alert", "environment_change"} and score >= 90:
            return ""
        origin_event_id = self._proactive_origin_event_id(candidate, source=source)
        blocked_events = user.get("weather_proactive_blocked_events")
        if isinstance(blocked_events, dict) and origin_event_id:
            blocked_until = _safe_float(blocked_events.get(origin_event_id), 0)
            if blocked_until > now:
                return "天气事件重试冷却中"
            if origin_event_id in blocked_events:
                blocked_events.pop(origin_event_id, None)
        day = _today_key()
        if _single_line(user.get("weather_proactive_budget_day"), 16) != day:
            user["weather_proactive_budget_day"] = day
            user["weather_proactive_budget_count"] = 0
            user["weather_proactive_last_at"] = 0
        count = _safe_int(user.get("weather_proactive_budget_count"), 0, 0, 20)
        cap = _safe_int(getattr(self, "weather_proactive_daily_cap", 3), 3, 1, 8)
        if count >= cap:
            return "天气主动日配额已用尽"
        last_at = _safe_float(user.get("weather_proactive_last_at"), 0)
        if last_at > now:
            # Wall-clock rollback or a stale persisted timestamp must not turn
            # the negative interval into an effectively permanent cooldown.
            user["weather_proactive_last_at"] = 0
            last_at = 0.0
        cooldown = 6 * 3600 if source in {"weather_context", "weather_alert"} else 4 * 3600
        if last_at > 0 and now - last_at < cooldown:
            return "天气主动仍在冷却期"
        return ""

    def _remember_weather_proactive_block(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float,
        reason: str,
    ) -> None:
        source = _single_line(candidate.get("source"), 40).lower()
        if source not in {"weather_alert", "environment_change", "weather_context"} and not bool(candidate.get("weather_linked")):
            return
        event_id = self._proactive_origin_event_id(candidate, source=source)
        if not event_id:
            return
        blocked_until = now + (6 * 3600 if source in {"weather_alert", "weather_context"} else 4 * 3600)
        raw = user.get("weather_proactive_blocked_events")
        events = dict(raw) if isinstance(raw, dict) else {}
        events[event_id] = blocked_until
        # Keep the state bounded and discard stale entries while touching it.
        events = {
            key: value for key, value in events.items()
            if _safe_float(value, 0) > now
        }
        if len(events) > 32:
            events = dict(sorted(events.items(), key=lambda item: _safe_float(item[1], 0), reverse=True)[:32])
        user["weather_proactive_blocked_events"] = events

    def _remember_weather_proactive_accept(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float,
    ) -> None:
        source = _single_line(candidate.get("source"), 40).lower()
        if source not in {"weather_alert", "environment_change", "weather_context"} and not bool(candidate.get("weather_linked")):
            return
        if candidate.get("_mobile_location_transition_key") and candidate.get("weather_linked"):
            return
        score = _safe_int(candidate.get("score"), 0, 0, 100)
        if source in {"weather_alert", "environment_change"} and score >= 90:
            return
        day = _today_key()
        if _single_line(user.get("weather_proactive_budget_day"), 16) != day:
            user["weather_proactive_budget_day"] = day
            user["weather_proactive_budget_count"] = 0
        user["weather_proactive_budget_count"] = _safe_int(user.get("weather_proactive_budget_count"), 0, 0, 20) + 1
        user["weather_proactive_last_at"] = now

    def _ordinary_weather_topic_available(self, user: dict[str, Any]) -> bool:
        repeated = getattr(self, "_recent_proactive_topic_repeated", None)
        if not callable(repeated):
            return True
        try:
            return not bool(repeated(user, "ordinary_weather_topic"))
        except Exception:
            return True
