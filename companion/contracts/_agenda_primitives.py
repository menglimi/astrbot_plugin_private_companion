# -*- coding: utf-8 -*-
"""Dependency-leaf primitives shared by agenda and calendar contracts."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AgendaContractError(ValueError):
    """Raised when a value cannot satisfy the local agenda contract."""


def _text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def stable_id(prefix: str, *parts: Any) -> str:
    """Return a deterministic, order-stable identifier for JSON-like parts."""

    def canonical(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): canonical(value[key]) for key in sorted(value, key=str)}
        if isinstance(value, (list, tuple)):
            return [canonical(item) for item in value]
        if isinstance(value, set):
            return sorted(canonical(item) for item in value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        return value

    raw = json.dumps(canonical(parts), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{_text(prefix, 48) or 'agenda'}-{digest}"


def timezone_or_default(timezone_name: Any = "Asia/Shanghai") -> ZoneInfo:
    try:
        return ZoneInfo(_text(timezone_name, 64) or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        # Minimal installations (including the bundled test runtime) may not
        # ship tzdata.  A fixed +08:00 fallback preserves local calendar
        # semantics instead of making normalization fail outright.
        try:
            return ZoneInfo("Asia/Shanghai")
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            return timezone(timedelta(hours=8))  # type: ignore[return-value]


__all__ = ["AgendaContractError", "stable_id", "timezone_or_default"]
