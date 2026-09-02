# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .persona_config import runtime_persona_setting


LLM_SEGMENT_MARKER = "<<PRIVATE_COMPANION_SPLIT>>"
LLM_SEGMENT_PLACEHOLDER = "{{split_marker}}"

_ESCAPED_LLM_SEGMENT_MARKER_PATTERN = re.compile(
    r"(?:&lt;|&#0*60;){2}\s*PRIVATE_COMPANION_SPLIT\s*(?:&gt;|&#0*62;){2}",
    flags=re.IGNORECASE,
)
_MARKDOWN_ESCAPED_LLM_SEGMENT_MARKER_PATTERN = re.compile(
    r"\\<\\<\s*PRIVATE\s*_\s*COMPANION\s*_\s*SPLIT\s*\\>\\>",
    flags=re.IGNORECASE,
)
_PLACEHOLDER_PATTERN = re.compile(
    r"\{\{\s*split_marker\s*\}\}",
    flags=re.IGNORECASE,
)
_SPACED_LLM_SEGMENT_MARKER_PATTERN = re.compile(
    r"<<\s*PRIVATE\s*_\s*COMPANION\s*_\s*SPLIT\s*>>",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class LlmSegmentParseResult:
    """One scan of an LLM reply's reserved segmentation protocol."""

    segments: tuple[str, ...]
    sanitized_text: str
    boundary_kinds: tuple[str, ...]
    exact_boundary_count: int = 0
    recovered_boundary_count: int = 0
    cleaned_only_count: int = 0
    fenced_token_count: int = 0
    quoted_token_count: int = 0
    escaped_token_count: int = 0
    placeholder_token_count: int = 0

    @property
    def controlled(self) -> bool:
        return len(self.segments) >= 2 and bool(self.boundary_kinds)

    @property
    def suppress_plugin_rule_split(self) -> bool:
        return self.fenced_token_count > 0


def _next_markdown_fence_state(
    line: str,
    state: tuple[str, int] | None,
) -> tuple[str, int] | None:
    """Track CommonMark-style backtick/tilde fences by marker and width."""
    stripped = str(line or "").strip()
    if state is None:
        opening = re.match(r"^(`{3,}|~{3,})", stripped)
        if opening is None:
            return None
        fence = opening.group(1)
        return fence[0], len(fence)
    marker, width = state
    if re.fullmatch(rf"{re.escape(marker)}{{{width},}}\s*", stripped):
        return None
    return state


def strip_llm_segment_marker_lines(
    text: Any,
    *,
    marker: str = LLM_SEGMENT_MARKER,
) -> str:
    """Compatibility wrapper that removes every reserved control token."""
    return sanitize_llm_segment_control_tokens(text, marker=marker)


def has_fenced_llm_segment_marker(
    text: Any,
    *,
    marker: str = LLM_SEGMENT_MARKER,
) -> bool:
    """Return whether an exact marker line appears inside a Markdown fence."""
    marker = str(marker or LLM_SEGMENT_MARKER).strip()
    if not marker:
        return False
    fence_state: tuple[str, int] | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        next_fence_state = _next_markdown_fence_state(line, fence_state)
        if next_fence_state != fence_state:
            fence_state = next_fence_state
            continue
        if fence_state is not None and line == marker:
            return True
    return False


def split_llm_controlled_text(text: Any, *, marker: str = LLM_SEGMENT_MARKER) -> tuple[list[str], bool]:
    """Split an LLM reply on an exact standalone control-marker line.

    The marker is deliberately strict: array-like variants and inline mentions
    remain ordinary user-visible text. Code fences and quoted lines are also
    ignored so examples or quoted instructions do not accidentally create
    outbound message boundaries.
    """
    result = parse_llm_segment_control(text, marker=marker)
    if not result.sanitized_text:
        return [], False
    if not result.controlled:
        return [result.sanitized_text], False
    return list(result.segments), True


def _replace_reserved_tokens(value: str, *, marker: str) -> tuple[str, dict[str, int]]:
    counts = {"escaped": 0, "placeholder": 0, "marker": 0}
    replacement_token = "\x00PRIVATE_COMPANION_SEGMENT_TOKEN\x00"

    def replace(pattern: re.Pattern[str], key: str, source: str) -> str:
        def repl(_match: re.Match[str]) -> str:
            counts[key] += 1
            return replacement_token

        return pattern.sub(repl, source)

    cleaned = replace(_ESCAPED_LLM_SEGMENT_MARKER_PATTERN, "escaped", value)
    cleaned = replace(
        _MARKDOWN_ESCAPED_LLM_SEGMENT_MARKER_PATTERN,
        "escaped",
        cleaned,
    )
    cleaned = replace(_PLACEHOLDER_PATTERN, "placeholder", cleaned)
    marker_pattern = (
        _SPACED_LLM_SEGMENT_MARKER_PATTERN
        if marker == LLM_SEGMENT_MARKER
        else re.compile(re.escape(marker), flags=re.IGNORECASE)
    )
    cleaned = replace(marker_pattern, "marker", cleaned)
    if any(counts.values()):
        token_pattern = re.escape(replacement_token)
        cleaned = re.sub(
            rf"(?<=[^\s])[ \t]*(?:{token_pattern}[ \t]*)+(?=[^\s])",
            " ",
            cleaned,
        )
        cleaned = re.sub(
            rf"[ \t]*(?:{token_pattern}[ \t]*)+",
            "",
            cleaned,
        )
    return cleaned, counts


def parse_llm_segment_control(
    text: Any,
    *,
    marker: str = LLM_SEGMENT_MARKER,
) -> LlmSegmentParseResult:
    """Parse high-confidence boundaries while removing all reserved tokens.

    An exact standalone marker is a boundary. A marker attached to text on only
    one side of its line is recovered as a boundary. Fully inline, quoted,
    fenced, escaped and placeholder forms are cleanup-only.
    """

    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    marker = str(marker or LLM_SEGMENT_MARKER).strip()
    if not normalized or not marker:
        return LlmSegmentParseResult(
            segments=(normalized,) if normalized else (),
            sanitized_text=normalized,
            boundary_kinds=(),
        )
    marker_pattern = (
        _SPACED_LLM_SEGMENT_MARKER_PATTERN
        if marker == LLM_SEGMENT_MARKER
        else re.compile(re.escape(marker), flags=re.IGNORECASE)
    )
    if not any(
        pattern.search(normalized)
        for pattern in (
            _ESCAPED_LLM_SEGMENT_MARKER_PATTERN,
            _MARKDOWN_ESCAPED_LLM_SEGMENT_MARKER_PATTERN,
            _PLACEHOLDER_PATTERN,
            marker_pattern,
        )
    ):
        return LlmSegmentParseResult(
            segments=(normalized,),
            sanitized_text=normalized,
            boundary_kinds=(),
        )

    segments: list[str] = []
    boundary_kinds: list[str] = []
    current: list[str] = []
    pending_boundary: str | None = None
    exact_count = 0
    recovered_count = 0
    cleaned_only_count = 0
    fenced_count = 0
    quoted_count = 0
    escaped_count = 0
    placeholder_count = 0
    fence_state: tuple[str, int] | None = None

    def append_current() -> bool:
        nonlocal pending_boundary, exact_count, recovered_count
        body = "\n".join(current).strip("\n")
        current.clear()
        if not body.strip():
            return False
        if segments and pending_boundary:
            boundary_kinds.append(pending_boundary)
            if pending_boundary == "exact":
                exact_count += 1
            else:
                recovered_count += 1
        segments.append(body)
        pending_boundary = None
        return True

    for raw_line in normalized.split("\n"):
        stripped = raw_line.strip()
        next_fence_state = _next_markdown_fence_state(stripped, fence_state)
        fence_transition = next_fence_state != fence_state
        inside_fence = fence_state is not None or (
            fence_transition and next_fence_state is not None
        )
        quoted = raw_line.lstrip().startswith(">")

        if not inside_fence and not quoted and raw_line.count(marker) == 1:
            left, right = raw_line.split(marker, 1)
            left_has_text = bool(left.strip())
            right_has_text = bool(right.strip())
            if not (left_has_text and right_has_text):
                if left_has_text:
                    current.append(left.rstrip())
                boundary_kind = "exact" if not left_has_text and not right_has_text else "recovered"
                if append_current():
                    pending_boundary = boundary_kind
                else:
                    cleaned_only_count += 1
                if right_has_text:
                    current.append(right.lstrip())
                fence_state = next_fence_state
                continue

        cleaned, counts = _replace_reserved_tokens(raw_line, marker=marker)
        token_count = counts["escaped"] + counts["placeholder"] + counts["marker"]
        escaped_count += counts["escaped"]
        placeholder_count += counts["placeholder"]
        cleaned_only_count += token_count
        if inside_fence:
            fenced_count += token_count
        if quoted:
            quoted_count += token_count
            if token_count and not cleaned.lstrip("> ").strip():
                cleaned = ""
        current.append(cleaned)
        fence_state = next_fence_state

    appended_final = append_current()
    if not appended_final and pending_boundary:
        cleaned_only_count += 1
        pending_boundary = None
    controlled = len(segments) >= 2 and len(boundary_kinds) == len(segments) - 1
    if not controlled:
        cleaned_only_count += exact_count + recovered_count
        exact_count = 0
        recovered_count = 0
        boundary_kinds = []

    sanitized = "\n".join(segments).strip("\n")
    return LlmSegmentParseResult(
        segments=tuple(segments) if controlled else ((sanitized,) if sanitized else ()),
        sanitized_text=sanitized,
        boundary_kinds=tuple(boundary_kinds),
        exact_boundary_count=exact_count,
        recovered_boundary_count=recovered_count,
        cleaned_only_count=cleaned_only_count,
        fenced_token_count=fenced_count,
        quoted_token_count=quoted_count,
        escaped_token_count=escaped_count,
        placeholder_token_count=placeholder_count,
    )


def sanitize_llm_segment_control_tokens(
    text: Any,
    *,
    marker: str = LLM_SEGMENT_MARKER,
) -> str:
    """Remove reserved segmentation tokens without making send boundaries."""
    return parse_llm_segment_control(text, marker=marker).sanitized_text


COMPONENT_STRATEGIES = frozenset({"inline", "separate", "previous", "next"})
DEFAULT_COMPONENT_ORDER = (
    "voice",
    "at",
    "text",
    "face",
    "image",
    "other",
    "reaction",
)
COMPONENT_ORDER_KINDS = frozenset(DEFAULT_COMPONENT_ORDER)

_COMPONENT_STRATEGY_ALIASES = {
    "inline": "inline",
    "embed": "inline",
    "embedded": "inline",
    "same_message": "inline",
    "same-message": "inline",
    "嵌入": "inline",
    "同一消息": "inline",
    "separate": "separate",
    "standalone": "separate",
    "separate_before": "separate",
    "separate_after": "separate",
    "单独": "separate",
    "独立": "separate",
    "previous": "previous",
    "follow_previous": "previous",
    "follow-previous": "previous",
    "跟随上段": "previous",
    "next": "next",
    "follow_next": "next",
    "follow-next": "next",
    "跟随下段": "next",
    "接下文": "next",
}


def normalize_component_strategy(value: Any, default: str = "inline") -> str:
    normalized_default = str(default or "inline").strip().lower()
    if normalized_default not in COMPONENT_STRATEGIES:
        normalized_default = "inline"
    raw = str(value or "").strip()
    if not raw:
        return normalized_default
    normalized = _COMPONENT_STRATEGY_ALIASES.get(raw)
    if normalized:
        return normalized
    return _COMPONENT_STRATEGY_ALIASES.get(raw.lower(), normalized_default)


def normalize_component_order(value: Any) -> list[str]:
    """Normalize a user-editable component order and append new component kinds."""
    source: Any = value
    if isinstance(source, str):
        raw = source.strip()
        if not raw:
            source = []
        else:
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = None
            source = decoded if isinstance(decoded, list) else raw.replace(",", " ").split()
    elif isinstance(source, set):
        source = [kind for kind in DEFAULT_COMPONENT_ORDER if kind in source]
    elif not isinstance(source, (list, tuple)):
        source = []

    normalized: list[str] = []
    for item in source:
        kind = str(item or "").strip().lower()
        if kind in COMPONENT_ORDER_KINDS and kind not in normalized:
            normalized.append(kind)
    normalized.extend(kind for kind in DEFAULT_COMPONENT_ORDER if kind not in normalized)
    return normalized


def component_kind(component: Any) -> str:
    """Classify AstrBot components without depending on one framework version."""
    if bool(getattr(component, "_private_companion_reaction_expression", False)):
        return "reaction"
    name = component.__class__.__name__.strip().lower()
    if name in {"record", "audio", "voice", "voice_message", "voicemessage"}:
        return "voice"
    if name in {"image", "picture", "photo"}:
        return "image"
    if name in {"at", "mention", "mentionuser", "mention_user"}:
        return "at"
    if name in {"face", "emoji", "emoticon", "sticker"}:
        return "face"
    if name in {"reply", "quote"}:
        return "reply"
    return "other"


def component_strategies_from_owner(owner: Any) -> dict[str, str]:
    """Read component placement with backward-compatible defaults."""
    reaction_mode = str(
        runtime_persona_setting(owner, "reaction_expression_delivery_mode", "separate_after")
        or "separate_after"
    ).strip().lower()
    reaction_strategy = "inline" if reaction_mode == "same_message" else "separate"
    return {
        "voice": normalize_component_strategy(
            runtime_persona_setting(owner, "segmented_proactive_voice_strategy", "separate"),
            "separate",
        ),
        "image": normalize_component_strategy(
            runtime_persona_setting(owner, "segmented_proactive_image_strategy", "separate"),
            "separate",
        ),
        "at": normalize_component_strategy(
            runtime_persona_setting(owner, "segmented_proactive_at_strategy", "inline"),
            "inline",
        ),
        "face": normalize_component_strategy(
            runtime_persona_setting(owner, "segmented_proactive_face_strategy", "inline"),
            "inline",
        ),
        "reaction": reaction_strategy,
        "other": normalize_component_strategy(
            runtime_persona_setting(owner, "segmented_proactive_other_strategy", "separate"),
            "separate",
        ),
    }


def component_order_from_owner(owner: Any) -> list[str]:
    """Read the visual component order with a forward-compatible default."""
    return normalize_component_order(
        runtime_persona_setting(
            owner,
            "segmented_proactive_component_order",
            DEFAULT_COMPONENT_ORDER,
        )
    )


def split_plain_component_chain(
    chain: list[Any],
    *,
    plain_type: type,
    split_text: Callable[[str], list[str]],
    fallback_line_split: bool = False,
) -> list[list[Any]]:
    """Split text components while keeping media/components as atomic chunks."""
    chunks: list[list[Any]] = []
    for comp in chain or []:
        if isinstance(comp, plain_type):
            text = str(getattr(comp, "text", "") or "").strip()
            if not text:
                continue
            if fallback_line_split:
                segments = [part.strip() for part in text.splitlines() if part.strip()]
            else:
                segments = split_text(text)
            segments = [str(segment or "").strip() for segment in segments if str(segment or "").strip()] or [text]
            chunks.extend([[plain_type(segment)] for segment in segments])
            continue
        chunks.append([comp])
    return chunks


def split_plain_component_chain_detailed(
    chain: list[Any],
    *,
    plain_type: type,
    split_text: Callable[[str], list[str]],
) -> tuple[list[list[Any]], bool, bool, str]:
    chunks: list[list[Any]] = []
    changed = False
    split_changed = False
    text_parts: list[str] = []
    plain_buffer: list[str] = []

    def flush_plain_buffer() -> None:
        nonlocal changed, split_changed
        if not plain_buffer:
            return
        raw_text = "".join(plain_buffer).strip()
        plain_buffer.clear()
        if not raw_text:
            changed = True
            return
        text_parts.append(raw_text)
        segments = [str(item or "").strip() for item in split_text(raw_text) if str(item or "").strip()]
        if not segments:
            changed = True
            return
        if len(segments) != 1 or segments[0] != raw_text:
            changed = True
        if len(segments) > 1:
            split_changed = True
        for segment in segments:
            chunks.append([plain_type(segment)])

    for comp in chain or []:
        if isinstance(comp, plain_type):
            plain_buffer.append(str(getattr(comp, "text", "") or ""))
            continue
        flush_plain_buffer()
        chunks.append([comp])
    flush_plain_buffer()
    return chunks, changed, split_changed, "".join(text_parts).strip()


def plan_component_chunks(
    chain: list[Any],
    *,
    plain_type: type,
    split_text: Callable[[str], list[str]],
    strategies: Mapping[str, str] | None = None,
    component_order: Any = None,
    classify: Callable[[Any], str] = component_kind,
) -> tuple[list[list[Any]], bool, bool, str]:
    """Split text and place media around the nearest text message.

    Reply/quote components are only bound to the first text-bearing chunk. When
    the result is media-only, discard the quote so adapters cannot turn it into
    an orphan quote card or attach it to a leading voice/image message.
    """
    source_chain = list(chain or [])
    reply_components: list[Any] = []
    content_chain: list[Any] = []
    for component in source_chain:
        if classify(component) == "reply":
            reply_components.append(component)
        else:
            content_chain.append(component)

    units, text_changed, split_changed, full_text = split_plain_component_chain_detailed(
        content_chain,
        plain_type=plain_type,
        split_text=split_text,
    )
    if component_order is not None:
        order_rank = {
            kind: index for index, kind in enumerate(normalize_component_order(component_order))
        }
        units = sorted(
            units,
            key=lambda unit: order_rank.get(
                "text" if unit and isinstance(unit[0], plain_type) else classify(unit[0]),
                len(order_rank),
            ),
        )
    normalized_strategies = {
        str(key): normalize_component_strategy(value, "inline")
        for key, value in dict(strategies or {}).items()
    }

    chunks: list[list[Any]] = []
    pending_next: list[Any] = []
    for unit in units:
        if not unit:
            continue
        component = unit[0]
        if isinstance(component, plain_type):
            chunks.append([*pending_next, *unit])
            pending_next.clear()
            continue

        kind = classify(component)
        strategy = normalized_strategies.get(
            kind,
            normalized_strategies.get("other", "separate"),
        )
        strategy = normalize_component_strategy(strategy, "separate")
        if strategy == "separate":
            chunks.append(list(unit))
            continue
        if strategy == "previous":
            if chunks:
                chunks[-1].extend(unit)
            else:
                chunks.append(list(unit))
            continue
        if strategy == "next":
            pending_next.extend(unit)
            continue

        # Inline means the same MessageChain as adjacent text. Prefer the text
        # immediately before the component; otherwise hold it for the next text.
        if chunks and any(isinstance(item, plain_type) for item in chunks[-1]):
            chunks[-1].extend(unit)
        else:
            pending_next.extend(unit)

    if pending_next:
        chunks.append(list(pending_next))

    if reply_components:
        target_index = next(
            (
                index
                for index, chunk in enumerate(chunks)
                if any(isinstance(item, plain_type) for item in chunk)
            ),
            -1,
        )
        if target_index >= 0:
            chunks[target_index] = [*reply_components, *chunks[target_index]]

    chunks = [chunk for chunk in chunks if chunk]

    def sequence_key(component: Any) -> tuple[str, Any]:
        if isinstance(component, plain_type):
            return ("plain", str(getattr(component, "text", "") or "").strip())
        return ("component", id(component))

    source_visible = [
        component
        for component in source_chain
        if not isinstance(component, plain_type)
        or str(getattr(component, "text", "") or "").strip()
    ]
    planned = flatten_component_chunks(chunks)
    reordered = [sequence_key(item) for item in planned] != [
        sequence_key(item) for item in source_visible
    ]
    changed = bool(text_changed or len(chunks) != 1 or reordered)
    return chunks, changed, split_changed, full_text


def bind_reply_components_to_first_text(
    chunks: list[list[Any]],
    *,
    plain_type: type,
    classify: Callable[[Any], str] = component_kind,
    reply_components: list[Any] | None = None,
) -> tuple[list[list[Any]], bool]:
    """Move reply components to the first text chunk, dropping media-only quotes."""
    replies = list(reply_components or [])
    cleaned: list[list[Any]] = []
    changed = False
    for chunk in chunks or []:
        cleaned_chunk: list[Any] = []
        for component in chunk or []:
            if classify(component) == "reply":
                if all(component is not existing for existing in replies):
                    replies.append(component)
                changed = True
                continue
            cleaned_chunk.append(component)
        if cleaned_chunk:
            cleaned.append(cleaned_chunk)

    if not replies:
        return cleaned, changed
    target_index = next(
        (
            index
            for index, chunk in enumerate(cleaned)
            if any(isinstance(item, plain_type) for item in chunk)
        ),
        -1,
    )
    if target_index >= 0:
        target_chunk = cleaned[target_index]
        insert_at = 0
        while (
            insert_at < len(target_chunk)
            and classify(target_chunk[insert_at]) in {"voice", "image", "reaction"}
        ):
            insert_at += 1
        cleaned[target_index] = [
            *target_chunk[:insert_at],
            *replies,
            *target_chunk[insert_at:],
        ]
    return cleaned, True


def flatten_component_chunks(chunks: list[list[Any]]) -> list[Any]:
    flattened: list[Any] = []
    for chunk in chunks or []:
        flattened.extend(chunk or [])
    return flattened
