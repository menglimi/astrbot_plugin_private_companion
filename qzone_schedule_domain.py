"""Pure domain rules for QQ Zone publishing schedules and draft lengths."""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

MinuteRange = tuple[int, int]


def merge_windows(windows: Sequence[MinuteRange]) -> list[MinuteRange]:
    merged: list[MinuteRange] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def parse_windows(
    raw: Any,
    *,
    merge: Callable[[Sequence[MinuteRange]], list[MinuteRange]] = merge_windows,
) -> list[MinuteRange]:
    windows: list[MinuteRange] = []
    for line in str(raw or "").replace("；", "\n").replace(",", "\n").splitlines():
        text = line.strip()
        if not text:
            continue
        match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
        if not match:
            continue
        start_h, start_m, end_h, end_m = (int(part) for part in match.groups())
        if start_h > 23 or start_m > 59 or end_h > 24 or end_m > 59:
            continue
        if end_h == 24 and end_m != 0:
            continue
        start = start_h * 60 + start_m
        end = min(end_h * 60 + end_m, 24 * 60)
        if end == start:
            continue
        if end < start:
            windows.extend(((start, 24 * 60), (0, end)))
        else:
            windows.append((start, end))
    return merge(windows)


def subtract_ranges(window: MinuteRange, blocked: Sequence[MinuteRange]) -> list[MinuteRange]:
    pieces = [window]
    for block_start, block_end in blocked:
        remaining: list[MinuteRange] = []
        for start, end in pieces:
            if block_end <= start or block_start >= end:
                remaining.append((start, end))
                continue
            if start < block_start:
                remaining.append((start, min(block_start, end)))
            if end > block_end:
                remaining.append((max(block_end, start), end))
        pieces = [(start, end) for start, end in remaining if end > start]
    return pieces


def hhmm_to_minutes(value: Any) -> int | None:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 47 or minute > 59:
        return None
    return hour * 60 + minute


def length_profile_sequence(
    count: int,
    *,
    choose: Callable[[Sequence[str]], str],
    chance: Callable[[], float],
    choose_index: Callable[[int], int],
    shuffle: Callable[[list[str]], None],
) -> list[str]:
    if count <= 1:
        return [choose(("short", "medium"))]
    sequence = ["short" if index % 2 == 0 else "medium" for index in range(count)]
    if count >= 3 and chance() < 0.35:
        sequence[choose_index(count)] = "long"
    shuffle(sequence)
    return sequence


def length_profile_range(profile: Any, profiles: dict[str, tuple[int, int]]) -> tuple[int, int]:
    key = re.sub(r"\s+", " ", str(profile or "")).strip()[:16] or "medium"
    return profiles.get(key, profiles["medium"])


def slot_is_night(planned_at: float, *, day_start: float, night_ranges: Sequence[MinuteRange]) -> bool:
    minutes = (planned_at - day_start) / 60.0
    return any(start <= minutes < end for start, end in night_ranges)


def text_length_ok(text: Any, *, profile_range: tuple[int, int], hard_limit: int) -> bool:
    length = len(re.sub(r"\s+", "", str(text or "")))
    if length > hard_limit:
        return False
    low, high = profile_range
    return low - 8 <= length <= high + 12


def ngram_shared_count(left: Any, right: Any, *, n: int = 3) -> int:
    a = re.sub(r"\s+", "", str(left or ""))
    b = re.sub(r"\s+", "", str(right or ""))
    if len(a) < n or len(b) < n:
        return 1 if a and a == b else 0
    grams_a = {a[index : index + n] for index in range(len(a) - n + 1)}
    grams_b = {b[index : index + n] for index in range(len(b) - n + 1)}
    return len(grams_a & grams_b)
