# -*- coding: utf-8 -*-
"""photo_prompt_context 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_prompt_context.py，仅调整模块级依赖的导入来源。
"""
import re
from collections.abc import Mapping, Sequence
from typing import Any
from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptRenderMode,
    PromptSection,
    exact_text,
    prompt_section,
    render_prompt_sections,
)

from .photo_prompt_context_part01 import (
    _COMPACTED_MARKER,
    _RECENT_OUTFIT_CONTINUITY_PATTERN,
    _audit,
    _clip,
    _conflicts_in_section,
    _excluded_outfit_terms,
    _photo_content,
    _replace_photo_prompt_section,
    _sanitize_field,
    _section_conflict_sanitization_enabled,
    _specific_outfit_items,
    _split_embedded_polarity,
    _validated_photo_prompt_section,
    _value,
)


def _sanitize_sections(
    sections: Sequence[PromptSection],
    wardrobe: Any,
) -> tuple[
    list[PromptSection],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    active_category = str(_value(wardrobe, "category", "") or "").strip().lower()
    authoritative = bool(_value(wardrobe, "lock_outfit", False) and active_category)
    excluded_categories = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "excluded_categories", ()) or ())
        if str(item or "").strip()
    )
    excluded_outfit_terms = _excluded_outfit_terms(wardrobe)
    authoritative_items = _specific_outfit_items(_value(wardrobe, "requested_outfit_text", ""))
    sanitized: list[PromptSection] = []
    detected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    effective_roles = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "effective_reference_roles", ()) or ())
        if str(item or "").strip()
    )
    for section in sections:
        payload = _photo_content(section)
        if payload is None:
            raise TypeError("sections must contain PromptSection values with PhotoPromptContent")
        if (
            not _section_conflict_sanitization_enabled(section)
            or payload.domain_source in {"user_request", "wardrobe_decision"}
        ):
            sanitized.append(section)
            continue
        if section.title != "global_fixed_prompt":
            positive_text, embedded_negative = _split_embedded_polarity(payload.positive)
            if embedded_negative:
                section = _replace_photo_prompt_section(
                    section,
                    positive=positive_text,
                    negative=", ".join(
                        part
                        for part in (payload.negative.strip(), embedded_negative)
                        if part
                    ),
                )
                payload = _photo_content(section)
                if payload is None:
                    raise AssertionError("typed photo section replacement lost its payload")
        if payload.domain_source == "recent_continuity" and "outfit" not in effective_roles:
            match = _RECENT_OUTFIT_CONTINUITY_PATTERN.search(payload.positive)
            if match:
                detected.append(
                    _audit(
                        section,
                        rule="inactive_reference_outfit_role",
                        category="reference_outfit",
                        action="detected",
                        text=match.group(0),
                    )
                )
                removed.append(
                    _audit(
                        section,
                        rule="inactive_reference_outfit_role",
                        category="reference_outfit",
                        action="clause_rewritten",
                        text=match.group(0),
                    )
                )
                rewritten = _RECENT_OUTFIT_CONTINUITY_PATTERN.sub("", payload.positive)
                rewritten = re.sub(r"\s+,", ",", rewritten)
                rewritten = re.sub(r",\s*([.;。；])", r"\1", rewritten)
                section = _replace_photo_prompt_section(
                    section,
                    positive=rewritten.strip(),
                )
                payload = _photo_content(section)
                if payload is None:
                    raise AssertionError("typed photo section replacement lost its payload")
        positive, positive_found, positive_removed = _sanitize_field(
            section,
            payload.positive,
            negative=False,
            active_category=active_category,
            authoritative=authoritative,
            authoritative_items=authoritative_items,
            excluded_categories=excluded_categories,
            excluded_outfit_terms=excluded_outfit_terms,
        )
        negative, negative_found, negative_removed = _sanitize_field(
            section,
            payload.negative,
            negative=True,
            active_category=active_category,
            authoritative=authoritative,
            authoritative_items=authoritative_items,
            excluded_categories=excluded_categories,
            excluded_outfit_terms=excluded_outfit_terms,
        )
        detected.extend((*positive_found, *negative_found))
        removed.extend((*positive_removed, *negative_removed))
        if any(
            item["action"] == "section_dropped"
            for item in (*positive_removed, *negative_removed)
        ):
            positive = ""
            negative = ""
        sanitized.append(
            _replace_photo_prompt_section(
                section,
                positive=positive,
                negative=negative,
            )
        )

    residual: list[dict[str, Any]] = []
    for index, section in enumerate(sanitized):
        payload = _photo_content(section)
        if payload is None:
            raise AssertionError("sanitized photo section lost its typed payload")
        if (
            not _section_conflict_sanitization_enabled(section)
            or payload.domain_source in {"user_request", "wardrobe_decision"}
        ):
            continue
        found = _conflicts_in_section(
            section,
            active_category=active_category,
            authoritative=authoritative,
            authoritative_items=authoritative_items,
            excluded_categories=excluded_categories,
            excluded_outfit_terms=excluded_outfit_terms,
        )
        if not found:
            continue
        detected.extend(found)
        for conflict in found:
            removed.append(
                {
                    **conflict,
                    "action": "section_dropped",
                }
            )
        sanitized[index] = _replace_photo_prompt_section(
            section,
            positive="",
            negative="",
        )

    for section in sanitized:
        payload = _photo_content(section)
        if payload is None:
            raise AssertionError("sanitized photo section lost its typed payload")
        if (
            not _section_conflict_sanitization_enabled(section)
            or payload.domain_source in {"user_request", "wardrobe_decision"}
        ):
            continue
        residual.extend(
            _conflicts_in_section(
                section,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
    return sanitized, detected, removed, residual


def _scan_residual_conflicts(
    sections: Sequence[PromptSection],
    wardrobe: Any,
) -> list[dict[str, Any]]:
    active_category = str(_value(wardrobe, "category", "") or "").strip().lower()
    authoritative = bool(_value(wardrobe, "lock_outfit", False) and active_category)
    authoritative_items = _specific_outfit_items(_value(wardrobe, "requested_outfit_text", ""))
    excluded_categories = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "excluded_categories", ()) or ())
        if str(item or "").strip()
    )
    excluded_outfit_terms = _excluded_outfit_terms(wardrobe)
    residual: list[dict[str, Any]] = []
    for section in sections:
        payload = _photo_content(section)
        if payload is None:
            raise TypeError("sections must contain PromptSection values with PhotoPromptContent")
        if (
            not _section_conflict_sanitization_enabled(section)
            or payload.domain_source in {"user_request", "wardrobe_decision"}
        ):
            continue
        residual.extend(
            _conflicts_in_section(
                section,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
    return residual


def _apply_budget(
    sections: list[PromptSection],
    indexes: list[int],
    budget: int,
    *,
    field: str = "positive",
) -> None:
    remaining = budget
    for index in indexes:
        section = sections[index]
        payload = _photo_content(section)
        if payload is None:
            raise TypeError("sections must contain PromptSection values with PhotoPromptContent")
        if payload.protected or section.title == "global_fixed_prompt":
            continue
        current = getattr(payload, field)
        clipped = _clip(current, remaining, preserve_tail=True) if remaining > 0 else ""
        sections[index] = _replace_photo_prompt_section(section, **{field: clipped})
        remaining -= len(clipped)
        if clipped and remaining > 0:
            remaining -= 1


def _budget_sections(sections: list[PromptSection]) -> list[PromptSection]:
    result = list(sections)
    indexes = lambda *sources: [
        index
        for index, section in enumerate(result)
        if (_photo_content(section) is not None)
        and _photo_content(section).domain_source in sources
    ]
    _apply_budget(result, indexes("wardrobe_decision"), 420)
    _apply_budget(result, indexes("reference_fallback"), 320)
    _apply_budget(result, indexes("scene_context", "visual_memory"), 700)
    _apply_budget(result, indexes("preset"), 140)
    _apply_budget(result, indexes("fixed_prompt"), 600)

    for index in indexes("recent_continuity"):
        section = result[index]
        payload = _photo_content(section)
        if payload is None:
            raise AssertionError("budgeted photo section lost its typed payload")
        if payload.protected:
            continue
        limit = 460 if payload.positive.startswith("Recent-photo continuity:") else 280
        result[index] = _replace_photo_prompt_section(
            section,
            positive=_clip(payload.positive, limit, preserve_tail=True),
        )
    _apply_budget(
        result,
        indexes("edit_contract", "composition", "recent_continuity"),
        680,
    )
    _apply_budget(
        result,
        [
            index
            for index, section in enumerate(result)
            if (_photo_content(section) is not None)
            and _photo_content(section).domain_source
            not in {"user_request", "composition"}
        ],
        230,
        field="negative",
    )
    return result


def _join_field(
    sections: Sequence[PromptSection],
    sources: frozenset[str],
    field: str = "positive",
) -> str:
    return "\n".join(
        value
        for section in sections
        for payload in (_photo_content(section),)
        if payload is not None and payload.domain_source in sources
        for value in (getattr(payload, field).strip(),)
        if value
    )


def _strip_compaction_markers(text: Any) -> str:
    return re.sub(
        r"\s*\.\.\. \[section compacted\] \.\.\.\s*",
        ", ",
        str(text or ""),
    )


def _render_photo_wire(text: str) -> str:
    return render_prompt_sections(
        [
            prompt_section(
                key="photo.rendered_wire",
                title="图片提示词",
                source="photo_prompt_context",
                content=exact_text(text),
            )
        ],
        mode=PromptRenderMode.PHOTO_PROMPT,
    )


def _assemble(sections: Sequence[PromptSection], prompt_format: str) -> str:
    groups = (
        (
            "User image request",
            _join_field(sections, frozenset({"user_request"})),
        ),
        (
            "Reference and wardrobe ruling",
            _join_field(sections, frozenset({"wardrobe_decision", "reference_fallback"})),
        ),
        (
            "Scene, style and final preset",
            _join_field(
                sections,
                frozenset({"scene_context", "visual_memory", "preset", "fixed_prompt"}),
            ),
        ),
        (
            "Composition and continuity",
            _join_field(
                sections,
                frozenset({"edit_contract", "composition", "recent_continuity"}),
            ),
        ),
    )
    positive_blocks = [f"[{label}]\n{_strip_compaction_markers(text)}" for label, text in groups if text]
    user_negative = _join_field(sections, frozenset({"user_request"}), "negative")
    decision_negative = "\n".join(
        payload.negative.strip()
        for section in sections
        for payload in (_photo_content(section),)
        if payload is not None
        and payload.domain_source != "user_request"
        and payload.negative.strip()
    )
    negative = ", ".join(value for value in (decision_negative, user_negative) if value)
    negative = _strip_compaction_markers(negative)
    mode = str(prompt_format or "traditional").strip().lower().replace("-", "_")
    if mode in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion"}:
        # NAI mode: avoid [] section labels (down-weight syntax) and express negatives via negative weight.
        prompt = "\n\n".join(
            f"{label}:\n{_strip_compaction_markers(text)}" for label, text in groups if text
        )
        wire = f"{prompt}\n\n-1.5::{negative}::".strip() if negative else prompt.strip()
        return _render_photo_wire(wire)
    if mode in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"}:
        prompt = "\n\n".join(positive_blocks)
        wire = f"{prompt}\n\nAvoid {negative}.".strip() if negative else prompt.strip()
        return _render_photo_wire(wire)
    prompt = "Positive prompt:\n" + "\n\n".join(positive_blocks)
    if negative:
        prompt += f"\n\nNegative prompt:\n{negative}"
    return _render_photo_wire(prompt.strip())


_LOCAL_VISUAL_PROMPT_SOURCES = frozenset(
    {
        "user_request",
        "visual_memory",
        "scene_context",
        "preset",
        "fixed_prompt",
        "composition",
    }
)


def _local_visual_section_text(section: PromptSection) -> str:
    """Project a sanitized section into text that an image model may render."""
    payload = _photo_content(section)
    if payload is None:
        raise TypeError("section must carry PhotoPromptContent")
    text = payload.positive.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = re.sub(
        r"(?im)^\s*\[(?:User image request|Reference and wardrobe ruling|"
        r"Scene, style and final preset|Composition and continuity)\]\s*$",
        "",
        text,
    )
    if payload.domain_source == "user_request":
        text = re.sub(r"^\s*positive\s+prompt\s*:\s*", "", text, flags=re.I)
        text = re.sub(r"^\s*user\s+request\s*:\s*", "", text, flags=re.I)
    elif payload.domain_source == "scene_context":
        text = re.sub(r"^\s*resolved\s+selfie\s+scene\s+facts\s*:\s*", "", text, flags=re.I)
        text = re.split(
            r"\s*An explicit scene or location in the current request overrides\b",
            text,
            maxsplit=1,
            flags=re.I,
        )[0]
    elif payload.domain_source == "visual_memory":
        text = re.sub(r"^\s*visual\s+continuity\s+reference\s*:\s*", "", text, flags=re.I)
    elif payload.domain_source == "preset":
        text = re.sub(r"^\s*scene\s+preset\s*:\s*", "", text, flags=re.I)
    elif payload.domain_source == "fixed_prompt":
        text = re.sub(r"^\s*additional\s+(?:fixed\s+prompt|outfit\s+preference)\s*:\s*", "", text, flags=re.I)
    elif payload.domain_source == "composition":
        lower_name = section.title.strip().lower()
        if lower_name == "relationship_role_reference":
            text = "two distinct people in one coherent scene" if "shared frame" in text.lower() else ""
        elif lower_name == "composition":
            if "multi-person" in text.lower():
                text = "multi-person portrait, distinct people, one coherent scene"
            elif "back-view" in text.lower():
                text = "single character, back view, coherent outfit, natural environmental portrait"
            elif "mirror" in text.lower():
                text = "single character mirror portrait, coherent outfit, visible face"
            else:
                text = "single character, coherent outfit, one continuous scene, visible face, upper-body or three-quarter portrait"
        elif lower_name == "subject_count":
            text = "multi-person portrait, distinct referenced people" if "multi-person" in text.lower() else "single character, one person"
        else:
            text = ""
    text = text.replace(_COMPACTED_MARKER, ", ")
    text = re.sub(r"\s*\n+\s*", ", ", text)
    text = re.sub(r"\s*;\s*", ", ", text)
    text = re.sub(r"(?:\s*,\s*){2,}", ", ", text)
    return text.strip(" \t\r\n,.;；。")


def compile_local_photo_prompt(
    sections: Sequence[PromptSection],
    prompt_format: str,
) -> str:
    """Compile renderable positive content for single-text local image workflows.

    Traditional prompt envelopes contain orchestration metadata intended for an
    LLM. ComfyUI/SDGen often feed their only text input directly to CLIP/T5, so
    those labels, decisions and negative blocks must not share that input.
    """
    normalized_sections: list[PromptSection] = []
    for value in sections:
        section = _validated_photo_prompt_section(value)
        if section is None:
            raise TypeError("sections must contain compatible photo prompt sections")
        normalized_sections.append(section)
    mode = str(prompt_format or "traditional").strip().lower().replace("-", "_")
    if mode != "traditional":
        return _assemble(normalized_sections, mode)
    values: list[str] = []
    seen: set[str] = set()
    for section in normalized_sections:
        payload = _photo_content(section)
        if payload is None:
            raise AssertionError("normalized photo section lost its typed payload")
        if payload.domain_source not in _LOCAL_VISUAL_PROMPT_SOURCES:
            continue
        value = _local_visual_section_text(section)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return _render_photo_wire(", ".join(values).strip())


def _reference_roles(reference: Any, wardrobe: Any) -> tuple[str, ...]:
    original = _value(reference, "reference_roles", ()) or ()
    effective = _value(wardrobe, "effective_reference_roles", ()) or ()
    return tuple(
        dict.fromkeys(
            str(item or "").strip().lower()
            for item in (*original, *effective)
            if str(item or "").strip()
        )
    )
