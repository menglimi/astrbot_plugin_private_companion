# -*- coding: utf-8 -*-
"""photo_prompt_context 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_prompt_context.py，仅调整模块级依赖的导入来源。
"""
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from . import photo_wardrobe_decision as _wardrobe_rules
from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptRenderMode,
    PromptSection,
    exact_text,
    prompt_section,
    render_prompt_sections,
)


_SANITIZER_VERSION = 3

_SECTION_SOURCES = frozenset(
    {
        "user_request",
        "visual_memory",
        "scene_context",
        "preset",
        "fixed_prompt",
        "recent_continuity",
        "wardrobe_decision",
        "managed_reference",
        "reference_fallback",
        "composition",
        "edit_contract",
    }
)

_SELFIE_WORKFLOWS = frozenset({"selfie", "portrait", "自拍", "人像"})

_EDIT_WORKFLOWS = frozenset({"edit", "改图", "修图", "重绘", "p图"})

_GENERIC_WARDROBE_PATTERN = re.compile(
    r"衣服|服装|衣着|穿搭|配饰|服饰|着装|原来的衣服|参考图中的衣服|"
    r"\b(?:clothes|clothing|outfit|wardrobe|attire|garment|accessor(?:y|ies))\b",
    flags=re.I,
)

_DAILY_OUTFIT_PATTERN = re.compile(
    r"(?:今日穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)",
    flags=re.I,
)

_RECENT_OUTFIT_CONTINUITY_PATTERN = re.compile(
    r"(?:,\s*)?(?:(?:preserve\s+)?(?:the\s+)?)?exact\s+outfit\s+and\s+accessories"
    r"|(?:，\s*)?(?:精确保留|保持|延续)?(?:参考图(?:中|里的)?)?(?:完整|原有|相同)?服装(?:和|与)配饰",
    flags=re.I,
)

_SPECIFIC_OUTFIT_ITEM_PATTERN = re.compile(
    r"连衣裙|裙子|短裙|长裙|吊带|衬衫|外套|夹克|西装|制服|汉服|旗袍|和服|洛丽塔|"
    r"裤子|裤|毛衣|卫衣|T恤|背心|上衣|套装|袜子|袜|鞋子|鞋|"
    r"\b(?:dress|skirt|shirt|blouse|coat|jacket|suit|uniform|hoodie|sweater|pants|trousers|shorts|top)\b",
    flags=re.I,
)

_EMBEDDED_NEGATION_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|not|avoid|without|no|exclude|skip|remove)\s+"
    r"|(?:不要|避免|禁止|不许|不得|别(?:再)?)(?:穿|用|使用|选)?\s*",
    flags=re.I,
)

_NEGATIVE_TO_POSITIVE_TRANSITION_PATTERN = re.compile(
    r"\b(?:but|instead|however)\b\s*"
    r"|(?:但|不过|可是|而是)?(?:改穿|换成|换上|换为|改为|要穿|穿上)\s*",
    flags=re.I,
)

_OUTFIT_ROTATION_NEGATIVE_PATTERN = re.compile(
    r"(?:\b(?:same|repeat(?:ed|ing)?|reus(?:e|ed|ing)|recent(?:ly)?|previous(?:ly)?|"
    r"histor(?:y|ical)|rotation|duplicate)\b.{0,80}\b(?:outfits?|wardrobes?|clothes|clothing|attire)\b"
    r"|\b(?:outfits?|wardrobes?|clothes|clothing|attire)\b.{0,80}\b(?:same|repeat(?:ed|ing)?|"
    r"reus(?:e|ed|ing)|recent(?:ly)?|previous(?:ly)?|histor(?:y|ical)|rotation|duplicate)\b"
    r"|(?:重复|复用|沿用|相同|同一|最近|近期|历史|此前|之前|上一套|用过|穿过|轮换|轮替)"
    r".{0,40}(?:穿搭|衣服|服装|衣着|着装|配饰)"
    r"|(?:穿搭|衣服|服装|衣着|着装|配饰).{0,40}"
    r"(?:重复|复用|沿用|相同|同一|最近|近期|历史|此前|之前|上一套|用过|穿过|轮换|轮替))",
    flags=re.I,
)


def _photo_content(value: Any) -> PhotoPromptContent | None:
    if not isinstance(value, PromptSection) or value.children:
        return None
    payload = value.content
    if not isinstance(payload, PhotoPromptContent):
        return None
    if payload.domain_source not in _SECTION_SOURCES:
        return None
    return payload


def _validated_photo_prompt_section(value: Any) -> PromptSection | None:
    """Accept only canonical sections carrying typed photo-domain content."""

    return value if _photo_content(value) is not None else None


_MISSING = object()


def _replace_photo_prompt_section(
    section: PromptSection,
    *,
    positive: Any = _MISSING,
    negative: Any = _MISSING,
) -> PromptSection:
    payload = _photo_content(section)
    if payload is None:
        raise TypeError("section must carry PhotoPromptContent")
    updates: dict[str, Any] = {}
    if positive is not _MISSING:
        updates["positive"] = positive
    if negative is not _MISSING:
        updates["negative"] = negative
    return replace(section, content=replace(payload, **updates))


def _photo_prompt_section_payload(value: Any) -> dict[str, Any]:
    """Serialize one photo section to the stable external six-field contract."""

    section = _validated_photo_prompt_section(value)
    if section is None:
        raise TypeError("value is not a compatible photo prompt section")
    payload = _photo_content(section)
    if payload is None:
        raise TypeError("value is not a compatible photo prompt section")
    return {
        "name": section.title,
        "source": payload.domain_source,
        "positive": payload.positive,
        "negative": payload.negative,
        "protected": payload.protected,
        "sanitize_conflicts": payload.sanitize_conflicts,
    }


def _section_conflict_sanitization_enabled(section: PromptSection) -> bool:
    payload = _photo_content(section)
    if payload is None:
        raise TypeError("section must carry PhotoPromptContent")
    if payload.sanitize_conflicts is not None:
        return payload.sanitize_conflicts
    return not payload.protected


@dataclass(frozen=True, slots=True)
class ResolvedPhotoPromptContext:
    final_prompt: str
    complete_prompt: str
    prompt_sections: tuple[PromptSection, ...]
    reference: Any
    detected_conflicts: tuple[dict[str, Any], ...]
    removed_conflicts: tuple[dict[str, Any], ...]
    residual_conflicts: tuple[dict[str, Any], ...]
    reference_removed: dict[str, Any] | None
    sanitizer_version: int = _SANITIZER_VERSION


def _value(subject: Any, name: str, default: Any = None) -> Any:
    if isinstance(subject, Mapping):
        return subject.get(name, default)
    return getattr(subject, name, default)


_CLIP_BOUNDARY_CHARS = frozenset(" \t\r\n,.;:!?，。；：！？")

_CLIP_BOUNDARY_PATTERN = re.compile(r"[\s,.;:!?，。；：！？]+")

_COMPACTED_MARKER = " ... [section compacted] ... "

_NEGATIVE_LABEL_MAX_WORDS = 6


def _clip_prefix_at_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    candidate = text[:limit]
    if len(text) <= limit or text[limit] in _CLIP_BOUNDARY_CHARS:
        return candidate.rstrip()
    matches = list(_CLIP_BOUNDARY_PATTERN.finditer(candidate))
    return candidate[: matches[-1].end()].rstrip() if matches else ""


def _clip_tail_at_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    start = max(0, len(text) - limit)
    candidate = text[start:]
    if start == 0 or text[start - 1] in _CLIP_BOUNDARY_CHARS:
        return candidate.lstrip()
    match = _CLIP_BOUNDARY_PATTERN.search(candidate)
    return candidate[match.end():].lstrip() if match else ""


def _clip(value: Any, limit: int, *, preserve_tail: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit <= 0 or len(text) <= limit:
        return text
    marker = _COMPACTED_MARKER
    if not preserve_tail or limit <= len(marker) + 40:
        return _clip_prefix_at_boundary(text, limit)
    available = limit - len(marker)
    head_size = max(20, int(available * 0.62))
    tail_size = max(20, available - head_size)
    head = _clip_prefix_at_boundary(text, head_size)
    tail = _clip_tail_at_boundary(text, tail_size)
    if head and tail:
        return f"{head}{marker}{tail}"
    return head or tail


def _categories(value: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            category
            for category, _start, _end, _matched in _wardrobe_rules._outfit_category_matches(value)
        )
    )


def _compatible(category: str, active_category: str) -> bool:
    return category == active_category


def _split_embedded_polarity(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw or not _EMBEDDED_NEGATION_PATTERN.search(raw):
        return raw, ""

    positive_parts: list[str] = []
    negative_parts: list[str] = []
    sentences = re.split(r"[;；.!?。！？]+", raw)
    for sentence in sentences:
        polarity = "positive"
        for comma_part in re.split(r"[,，]+", sentence):
            remaining = comma_part.strip()
            while remaining:
                if polarity == "positive":
                    marker = _EMBEDDED_NEGATION_PATTERN.search(remaining)
                    if marker is None:
                        positive_parts.append(remaining)
                        break
                    before = remaining[:marker.start()].strip()
                    if before:
                        positive_parts.append(before)
                    remaining = remaining[marker.end():].strip()
                    polarity = "negative"
                    continue

                transition = _NEGATIVE_TO_POSITIVE_TRANSITION_PATTERN.search(remaining)
                if transition is None:
                    negative_parts.append(remaining)
                    break
                before = remaining[:transition.start()].strip()
                if before:
                    negative_parts.append(before)
                remaining = remaining[transition.end():].strip()
                polarity = "positive"

    return ", ".join(positive_parts), ", ".join(negative_parts)


def _excluded_outfit_terms(wardrobe: Any) -> tuple[str, ...]:
    raw = _wardrobe_rules._clean_text(_value(wardrobe, "excluded_outfit_text", ""), 1200)
    terms: list[str] = []
    for clause in re.split(r"(?:\r?\n+|[；;，,。]+)", raw):
        cleaned = _wardrobe_rules._clean_text(clause, 240).strip(" ,.;；。，")
        if not cleaned:
            continue
        _negative, content = _wardrobe_rules._negative_clause_content(cleaned)
        term = _wardrobe_rules._clean_text(content or cleaned, 160).lower()
        if term:
            terms.append(term)
    return tuple(dict.fromkeys(terms))


def _specific_outfit_items(value: Any) -> frozenset[str]:
    normalized = _wardrobe_rules._clean_text(value, 1200).lower()
    return frozenset(match.group(0).lower() for match in _SPECIFIC_OUTFIT_ITEM_PATTERN.finditer(normalized))


def _generic_wardrobe_is_compatible(text: str, active_category: str) -> bool:
    normalized = _wardrobe_rules._clean_text(text, 4000).lower()
    if re.search(
        r"\b(?:one|single)\s+(?:single\s+)?coherent\s+outfit\b"
        r"|\bthe\s+same\s+outfit\b"
        r"|\b(?:requested|authoritative|resolved)\s+(?:wardrobe|outfit)\b"
        r"|\b(?:current|selected|established|stable|consistent)\s+(?:wardrobe|outfit|attire)\b"
        r"|\bwardrobe\s+(?:decision|ruling)\b"
        r"|\bconflicting\s+(?:schedule\s+location\s+or\s+)?wardrobe\b",
        normalized,
        flags=re.I,
    ):
        return True
    return active_category == "reference_outfit" and bool(
        re.search(r"\bexact\s+outfit\s+and\s+accessories\b", normalized, flags=re.I)
    )


def _preview(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)data:image/[^;\s]+;base64,[a-z0-9+/=]+", "[image-data]", text)
    text = re.sub(
        r"(?i)(?:[a-z]:\\[^,;\r\n]+|/(?:[^/\r\n,;]+/)+[^,;\r\n]*)",
        "[path]",
        text,
    )
    return text[:120]


def _audit(
    section: PromptSection,
    *,
    rule: str,
    category: str,
    action: str,
    text: str,
) -> dict[str, Any]:
    raw = str(text or "")
    payload = _photo_content(section)
    if payload is None:
        raise TypeError("section must carry PhotoPromptContent")
    return {
        "source": payload.domain_source,
        "section": section.title,
        "rule": rule,
        "category": category,
        "action": action,
        "preview": _preview(raw),
        "sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest(),
    }


def _positive_conflict(
    text: str,
    *,
    active_category: str,
    authoritative: bool,
    excluded_categories: frozenset[str],
    excluded_outfit_terms: tuple[str, ...],
) -> tuple[str, str] | None:
    categories = _categories(text)
    daily = bool(_DAILY_OUTFIT_PATTERN.search(text))
    if authoritative:
        if daily and active_category != "daily_outfit":
            return "daily_outfit_conflict", "daily_outfit"
        incompatible = next(
            (category for category in categories if not _compatible(category, active_category)),
            "",
        )
        if incompatible:
            return "incompatible_wardrobe", incompatible
        if (
            not categories
            and not _generic_wardrobe_is_compatible(text, active_category)
            and (
                _wardrobe_rules._contains_specific_outfit_text(text)
                or _GENERIC_WARDROBE_PATTERN.search(text)
            )
        ):
            return "unverified_wardrobe", "unknown"
    excluded = next((category for category in categories if category in excluded_categories), "")
    if excluded:
        return "excluded_wardrobe", excluded
    normalized = _wardrobe_rules._clean_text(text, 4000).lower()
    if next((term for term in excluded_outfit_terms if term in normalized), ""):
        return "excluded_outfit_item", "specific_outfit"
    return None


def _negative_conflict(
    text: str,
    *,
    active_category: str,
    authoritative: bool,
    authoritative_items: frozenset[str],
) -> tuple[str, str] | None:
    if not authoritative:
        return None
    # Rotation/history exclusions constrain reuse; they do not reject the
    # authoritative outfit category or an item that merely appears in history.
    if _OUTFIT_ROTATION_NEGATIVE_PATTERN.search(str(text or "")):
        return None
    categories = _categories(text)
    denied = next((category for category in categories if _compatible(category, active_category)), "")
    if denied:
        explicit_negation, _ = _wardrobe_rules._negative_clause_content(text)
        label_like = len(str(text).strip().split()) <= _NEGATIVE_LABEL_MAX_WORDS
        if explicit_negation or label_like:
            return "authoritative_wardrobe_negated", denied
    denied_item = next(
        (item for item in _specific_outfit_items(text) if item in authoritative_items),
        "",
    )
    if denied_item:
        return "authoritative_outfit_item_negated", denied_item
    return None


def _split_clauses(text: str) -> tuple[list[str], str]:
    raw = str(text or "").strip()
    if not raw:
        return [], "；"
    structural = [
        item.strip()
        for item in re.split(r"(?:\r?\n+|[；;]+|(?<=[.!?。！？])\s+)", raw)
        if item.strip()
    ]
    if len(structural) > 1:
        return structural, "；"
    comma_parts = [item.strip() for item in re.split(r"[,，]+", raw) if item.strip()]
    if len(comma_parts) > 1:
        return comma_parts, ", "
    return [raw], "；"


def _sanitize_field(
    section: PromptSection,
    text: str,
    *,
    negative: bool,
    active_category: str,
    authoritative: bool,
    authoritative_items: frozenset[str],
    excluded_categories: frozenset[str],
    excluded_outfit_terms: tuple[str, ...],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not text:
        return "", [], []
    clauses, separator = _split_clauses(text)
    conflicts: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []
    kept: list[str] = []
    for clause in clauses:
        conflict = (
            _negative_conflict(
                clause,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
            )
            if negative
            else _positive_conflict(
                clause,
                active_category=active_category,
                authoritative=authoritative,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
        if conflict is None:
            kept.append(clause)
            continue
        rule, category = conflict
        conflicts.append(
            _audit(section, rule=rule, category=category, action="detected", text=clause)
        )
        action = (
            "section_dropped"
            if len(clauses) == 1 and rule == "unverified_wardrobe"
            else "clause_removed"
        )
        removals.append(
            _audit(section, rule=rule, category=category, action=action, text=clause)
        )
    return separator.join(kept), conflicts, removals


def _conflicts_in_section(
    section: PromptSection,
    *,
    active_category: str,
    authoritative: bool,
    authoritative_items: frozenset[str],
    excluded_categories: frozenset[str],
    excluded_outfit_terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    payload = _photo_content(section)
    if payload is None:
        raise TypeError("section must carry PhotoPromptContent")
    for text, negative in ((payload.positive, False), (payload.negative, True)):
        conflict = (
            _negative_conflict(
                text,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
            )
            if negative
            else _positive_conflict(
                text,
                active_category=active_category,
                authoritative=authoritative,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
        if conflict:
            rule, category = conflict
            found.append(
                _audit(section, rule=rule, category=category, action="residual", text=text)
            )
    return found
