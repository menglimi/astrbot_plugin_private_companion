# -*- coding: utf-8 -*-
"""Pure query/change operations for the legacy ``companion_memory.items`` DTO."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .helpers import _safe_float, _safe_int, _single_line


MemorySignature = Callable[[Any], str]


def normalize_memory_items(
    raw_items: Any,
    *,
    now: float,
    max_items: int,
    signature_for: MemorySignature,
) -> list[dict[str, Any]]:
    """Return the legacy normalized list without mutating the input records."""
    if not isinstance(raw_items, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        text = _single_line(raw.get("text"), 260)
        if not text:
            continue
        created_ts = _safe_float(raw.get("created_ts"), 0)
        created_at = _single_line(raw.get("created_at"), 24)
        if created_ts <= 0 and created_at:
            try:
                created_ts = datetime.strptime(created_at, "%Y-%m-%d %H:%M").timestamp()
            except (TypeError, ValueError, OverflowError):
                created_ts = now
        if created_ts > 0 and now - created_ts > 180 * 86400:
            continue
        signature = signature_for(text)
        if not signature or signature in seen:
            continue
        seen.add(signature)
        item = dict(raw)
        item["text"] = text
        item["created_ts"] = created_ts or now
        normalized.append(item)
    normalized.sort(
        key=lambda item: (
            _safe_int(item.get("weight"), 1, 0),
            _safe_float(item.get("created_ts"), 0),
        ),
        reverse=True,
    )
    return normalized[:max_items]


def relevant_memory_items(
    items: list[dict[str, Any]],
    *,
    hint: str = "",
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Query normalized records using the existing weight/relevance ordering."""
    if not items:
        return []
    hint_text = _single_line(hint, 260).lower()
    if not hint_text:
        return items[: max(1, limit)]

    import re

    weighted: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        text = _single_line(item.get("text"), 260).lower()
        score = _safe_int(item.get("weight"), 1, 0)
        tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[a-z0-9_]{3,24}", text)
        if text and any(token and token in hint_text for token in tokens):
            score += 4
        weighted.append((score, item))
    weighted.sort(
        key=lambda pair: (pair[0], _safe_float(pair[1].get("created_ts"), 0)),
        reverse=True,
    )
    return [item for _, item in weighted[: max(1, limit)]]
