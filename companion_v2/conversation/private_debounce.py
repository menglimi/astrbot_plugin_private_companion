"""Adaptive private-chat debounce policy.

Ordinary private replies use AstrBot's core pipeline, where every inbound event
would otherwise become its own model request.  This module owns only the pure
timing decision: how long to wait after the latest plain-text message before
letting one merged turn proceed.

The window is bounded by the operator's ``min``/``max`` and by a hard total
ceiling measured from the first message of the burst.  Length is a proxy for
completeness: a long message is usually a finished thought, while a very short
one is often the first bubble of a multi-part message.  Terminal punctuation
nudges the decision in the same direction.  Nothing here reads storage, calls a
model or sends.
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULTS = {
    "private_debounce_enabled": True,
    "private_debounce_semantic_enabled": False,
    "private_debounce_min_ms": 1500,
    "private_debounce_max_ms": 4000,
    "private_debounce_total_ms": 8000,
}
# Defaults stay adaptive; the wider range also preserves fixed legacy windows.
LIMITS = {
    "private_debounce_min_ms": (10, 120000),
    "private_debounce_max_ms": (10, 120000),
    "private_debounce_total_ms": (10, 120000),
}
MAX_MERGED_CHARS = 8000


@dataclass(frozen=True)
class Policy:
    enabled: bool = True
    min_seconds: float = 1.5
    max_seconds: float = 4.0
    total_seconds: float = 8.0
    semantic_enabled: bool = False

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.semantic_enabled) is not bool:
            raise ValueError("private debounce switch must be boolean")
        for name in ("min_seconds", "max_seconds", "total_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0.01 <= value <= 120:
                raise ValueError("private debounce timing is out of range")
        if self.min_seconds > self.max_seconds:
            raise ValueError("private debounce minimum exceeds maximum")
        if self.total_seconds < self.max_seconds:
            raise ValueError("private debounce total is shorter than one window")

    @classmethod
    def from_milliseconds(cls, enabled, minimum, maximum, total, *, semantic_enabled=False):
        return cls(enabled=enabled, min_seconds=minimum / 1000,
                   max_seconds=maximum / 1000, total_seconds=total / 1000, semantic_enabled=semantic_enabled)


def visible_characters(text):
    """Count non-whitespace characters, so punctuation and emoji are not zero."""
    return max(1, sum(1 for char in str(text or "") if not char.isspace()))


def _ends_complete(text):
    raw = str(text or "").rstrip()
    return raw.endswith(("。", "！", "？", "!", "?", "\n"))


def _ends_incomplete(text):
    raw = str(text or "").rstrip()
    return raw.endswith(("，", ",", "、", "；", ";", "：", ":", "…", "..."))


def quiet_seconds(text, policy):
    """Return the adaptive quiet window for the latest message in the burst.

    The result is always inside ``policy.min_seconds..policy.max_seconds``; the
    caller adds the total ceiling measured from the first message.
    """
    if not policy.enabled:
        return 0.0
    span = max(0.0, policy.max_seconds - policy.min_seconds)
    ratio = min(1.0, visible_characters(text) / 60.0)
    quiet = policy.max_seconds - span * ratio
    if _ends_complete(text):
        quiet -= 0.2
    elif _ends_incomplete(text):
        quiet += 0.3
    else:
        quiet += 0.2
    return min(policy.max_seconds, max(policy.min_seconds, quiet))


def merge_texts(texts, *, limit=MAX_MERGED_CHARS):
    """Merge consecutive plain-text inputs in order, without inventing text."""
    merged = "\n".join(str(text).strip() for text in texts if str(text).strip())
    return merged[-limit:] if len(merged) > limit else merged
