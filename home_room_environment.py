# -*- coding: utf-8 -*-
"""Small, credential-free projection of the existing persona weather service."""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .persona_config import runtime_persona_setting


def _text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _timestamp(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (TypeError, ValueError):
        return 0


def weather_condition(summary: str) -> str:
    # Providers already supply descriptions. This only picks a visual; it does
    # not invent a forecast or ask a model to interpret the weather.
    for condition, pattern in (
        ("snow", r"雪|冰粒|snow|sleet|blizzard"),
        ("storm", r"雷|thunder|storm"),
        ("rain", r"雨|rain|drizzle|shower"),
        ("mist", r"雾|霾|fog|mist|haze"),
        ("cloudy", r"多云|阴|cloud|overcast"),
        ("clear", r"晴|clear|sunny"),
    ):
        if re.search(pattern, summary, re.IGNORECASE):
            return condition
    return "unknown"


async def home_room_environment(plugin: Any, *, timeout: float = 12) -> dict[str, Any]:
    """Refresh only when the existing weather cache is due; never force it."""
    resolver = getattr(plugin, "_weather_window_timezone", None)
    timezone = _text(resolver() if callable(resolver) else "Asia/Shanghai", 64)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        timezone = "Asia/Shanghai"
    refresh_seconds = max(60, int(runtime_persona_setting(plugin, "weather_refresh_minutes", 90) or 90) * 60)
    key_getter = getattr(plugin, "_weather_context_config_key", None)
    config_key = key_getter() if callable(key_getter) else None
    cached = getattr(plugin, "data", {}).get("daily_weather", {})
    cached = dict(cached) if isinstance(cached, dict) else {}
    weather: dict[str, Any] = {}
    enabled = bool(runtime_persona_setting(plugin, "enable_weather_context", True))
    getter = getattr(plugin, "_ensure_weather_context", None)
    if enabled and callable(getter):
        try:
            result = await asyncio.wait_for(getter(), timeout=timeout)
            weather = dict(result) if isinstance(result, dict) else {}
        except Exception:
            # Do not return upstream exception text (it can contain API URLs).
            pass
    now = time.time()
    # Settings can change while the provider is responding. Neither its old
    # result nor an old-place cache may be rendered under the new location.
    current_key_getter = getattr(plugin, "_weather_context_config_key", None)
    current_key = current_key_getter() if callable(current_key_getter) else None
    enabled = bool(runtime_persona_setting(plugin, "enable_weather_context", True))

    def usable(item: dict[str, Any]) -> bool:
        return bool(
            enabled
            and config_key == current_key
            and (current_key is None or item.get("config_key") == current_key)
            and item.get("source") not in (None, "", "none", "disabled")
            and _text(item.get("prompt")) not in ("", "暂无天气信息")
            and 0 < _timestamp(item.get("fetched_ts")) <= now + 60
        )

    fresh_result = usable(weather)
    if not fresh_result:
        weather = cached if usable(cached) else {}
    updated_at = _timestamp(weather.get("fetched_ts"))
    summary = _text(weather.get("prompt"))
    age = max(0, now - updated_at) if updated_at else None
    stale = bool(weather) and (not fresh_result or age >= refresh_seconds)
    temperature = re.search(r"(?<![\d.])(-?\d{1,3}(?:\.\d+)?)\s*(?:°\s*C|℃|摄氏度)", summary, re.IGNORECASE)
    return {
        "server_ts": now,
        "timezone": timezone,
        "poll_seconds": 300,
        "weather": {
            "status": "disabled" if not enabled else "stale" if stale else "live" if weather else "unavailable",
            "summary": summary,
            "condition": weather_condition(summary),
            "temperature_c": float(temperature.group(1)) if temperature else None,
            "source": _text(weather.get("source"), 40),
            "location": _text(weather.get("location_label"), 120),
            "updated_at": updated_at or None,
            "stale_after_seconds": refresh_seconds,
        },
    }
