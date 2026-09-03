from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

try:
    from markdown_it import MarkdownIt
except Exception:  # pragma: no cover - exercised by the conservative fallback
    MarkdownIt = None  # type: ignore[assignment]


MARKDOWN_BLOCK_TOKEN_PATTERN = r"\x00PCMARKDOWNBLOCK[0-9]+\x00"
_MARKDOWN_BLOCK_TOKEN_RE = re.compile(MARKDOWN_BLOCK_TOKEN_PATTERN)
_MARKDOWN_HINT_RE = re.compile(
    r"(?m)(?:^[ ]{0,3}(?:#{1,6}[ \t]+|>|(?:[-+*]|\d+[.)])[ \t]+|```|~~~)|"
    r"^[ ]{0,3}\|.*\|[ \t]*$|"
    r"(?:`[^`\n]+`|!?\[[^\]\n]+\]\([^\n)]+\)|\*\*[^*\n]+\*\*|__[^_\n]+__))"
)

_ATOMIC_BLOCK_TYPES = frozenset(
    {
        "blockquote_open",
        "bullet_list_open",
        "ordered_list_open",
        "table_open",
        "fence",
        "code_block",
        "html_block",
        "heading_open",
        "hr",
    }
)
_STRUCTURED_INLINE_TYPES = frozenset(
    {
        "code_inline",
        "em_open",
        "strong_open",
        "s_open",
        "link_open",
        "image",
        "html_inline",
        "hardbreak",
    }
)


@dataclass(frozen=True)
class MarkdownProtection:
    protected_text: str
    replacements: tuple[tuple[str, str], ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.replacements)

    def restore(self, value: Any) -> str:
        restored = str(value or "")
        for token, original in self.replacements:
            restored = restored.replace(token, original)
        return restored

    @staticmethod
    def contains_token(value: Any) -> bool:
        return bool(_MARKDOWN_BLOCK_TOKEN_RE.search(str(value or "")))


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\r\n|\r|\n", text):
        offsets.append(match.end())
    if offsets[-1] < len(text):
        offsets.append(len(text))
    return offsets


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _exclude_trailing_line_ending(text: str, start: int, end: int) -> int:
    while end > start:
        if text[max(start, end - 2) : end] == "\r\n":
            end -= 2
            continue
        if text[end - 1 : end] in {"\n", "\r"}:
            end -= 1
            continue
        break
    return end


def _token_line_ranges(text: str) -> list[tuple[int, int]]:
    if MarkdownIt is None:
        return [(0, len(text))] if _MARKDOWN_HINT_RE.search(text) else []
    try:
        parser = MarkdownIt("commonmark").enable("table")
        tokens = parser.parse(text)
    except Exception:
        return [(0, len(text))] if _MARKDOWN_HINT_RE.search(text) else []

    line_offsets = _line_offsets(text)
    line_count = len(line_offsets) - 1
    line_ranges: list[tuple[int, int]] = []
    for token in tokens:
        token_map = getattr(token, "map", None)
        if (
            not isinstance(token_map, list)
            or len(token_map) != 2
            or not all(isinstance(item, int) for item in token_map)
        ):
            continue
        start_line = max(0, min(line_count, token_map[0]))
        end_line = max(start_line, min(line_count, token_map[1]))
        if start_line >= end_line:
            continue
        protect = str(getattr(token, "type", "") or "") in _ATOMIC_BLOCK_TYPES
        if str(getattr(token, "type", "") or "") == "inline":
            child_types = {
                str(getattr(child, "type", "") or "")
                for child in (getattr(token, "children", None) or [])
            }
            protect = bool(child_types & _STRUCTURED_INLINE_TYPES)
        if protect:
            start = line_offsets[start_line]
            end = _exclude_trailing_line_ending(
                text,
                start,
                line_offsets[end_line],
            )
            line_ranges.append((start, end))

    # Reference definitions are intentionally absent from rendered token streams.
    for line_index in range(line_count):
        line = text[line_offsets[line_index] : line_offsets[line_index + 1]]
        if re.match(r"^[ ]{0,3}\[[^\]\n]+\]:[ \t]*\S+", line):
            start = line_offsets[line_index]
            end = _exclude_trailing_line_ending(text, start, start + len(line))
            line_ranges.append((start, end))
    return _merge_ranges(line_ranges)


def protect_markdown_blocks(text: Any) -> MarkdownProtection:
    source = str(text or "")
    if not source:
        return MarkdownProtection(source)
    ranges = _token_line_ranges(source)
    if not ranges:
        return MarkdownProtection(source)

    parts: list[str] = []
    replacements: list[tuple[str, str]] = []
    cursor = 0
    for index, (start, end) in enumerate(ranges):
        token = f"\x00PCMARKDOWNBLOCK{index}\x00"
        parts.append(source[cursor:start])
        parts.append(token)
        replacements.append((token, source[start:end]))
        cursor = end
    parts.append(source[cursor:])
    return MarkdownProtection("".join(parts), tuple(replacements))
