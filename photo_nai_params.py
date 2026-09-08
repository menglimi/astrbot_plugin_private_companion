# -*- coding: utf-8 -*-
"""Small, format-aware helpers for user supplied NAI prompt parameters."""

from __future__ import annotations

import math
import re
from typing import Any


PHOTO_NAI_PARAMS_CACHE_MAX_AGE_SECONDS = 2 * 60 * 60
_NAI_MODES = frozenset({"nai", "novelai", "nai_4", "nai_4.5"})


def _split_tags(value: Any) -> list[str]:
    text = str(value or "").replace("\uFF0C", ",")
    tags: list[str] = []
    stack: list[str] = []
    weighted = False
    start = 0
    cursor = 0
    closing = {"{": "}", "[": "]", "(": ")"}
    # Commas inside NAI weight and character groups are not tag boundaries.
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if text.startswith("::", cursor):
            weighted = not weighted
            cursor += 2
            continue
        if char in closing:
            stack.append(closing[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == "," and not stack and not weighted:
            tag = text[start:cursor].strip()
            if tag:
                tags.append(tag)
            start = cursor + 1
        cursor += 1
    tail = text[start:].strip()
    if tail:
        tags.append(tail)
    return tags


def extract_user_photo_nai_params(text: Any) -> str:
    candidate = str(text or "")
    if not candidate:
        return ""
    match = re.search(r"masterpiece[\s,，]*best\s+quality\b", candidate, flags=re.I)
    if not match:
        return ""
    block = candidate[match.start():]
    block = re.split(r"[\n\r。！？!?]", block, maxsplit=1)[0]
    block = re.sub(r"\s+", " ", block.replace("\uFF0C", ",")).strip(" ,")
    return block if len(re.split(r",\s*", block)) >= 8 else ""


def cache_user_photo_nai_params(
    user: dict[str, Any], text: Any, *, received_at: float
) -> None:
    params = extract_user_photo_nai_params(text)
    if params:
        user["last_photo_nai_params"] = params
        user["last_photo_nai_params_at"] = received_at
    else:
        user.pop("last_photo_nai_params", None)
        user.pop("last_photo_nai_params_at", None)


def recent_cached_photo_nai_params(user: Any, *, now: float) -> str:
    if not isinstance(user, dict):
        return ""
    value = str(user.get("last_photo_nai_params") or "").strip()
    if not value:
        return ""
    try:
        cached_at = float(user.get("last_photo_nai_params_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return ""
    if (
        not math.isfinite(cached_at)
        or cached_at <= 0
        or not 0 <= now - cached_at <= PHOTO_NAI_PARAMS_CACHE_MAX_AGE_SECONDS
    ):
        return ""
    return value


def merge_user_photo_nai_params(
    content: Any, inherited: Any, *, prompt_format: Any
) -> str:
    """Merge explicit user tags without flattening other prompt formats."""

    original = str(content or "").strip()
    mode = str(prompt_format or "").strip().lower().replace("-", "_")
    if mode not in _NAI_MODES:
        return original
    inherited_tags = _split_tags(inherited)
    if not inherited_tags:
        return original
    merged: list[str] = []
    seen: set[str] = set()
    for tag in inherited_tags:
        key = tag.casefold()
        if key not in seen:
            merged.append(tag)
            seen.add(key)
    for tag in _split_tags(original):
        if tag in {"人物", "{人物}", "[人物]"}:
            continue
        key = tag.casefold()
        if key not in seen:
            merged.append(tag)
            seen.add(key)
    return ", ".join(merged)


__all__ = [
    "PHOTO_NAI_PARAMS_CACHE_MAX_AGE_SECONDS",
    "cache_user_photo_nai_params",
    "extract_user_photo_nai_params",
    "merge_user_photo_nai_params",
    "recent_cached_photo_nai_params",
]
