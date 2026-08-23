# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .persona_config import runtime_persona_setting


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
        cleaned[target_index] = [*replies, *cleaned[target_index]]
    return cleaned, True


def flatten_component_chunks(chunks: list[list[Any]]) -> list[Any]:
    flattened: list[Any] = []
    for chunk in chunks or []:
        flattened.extend(chunk or [])
    return flattened
