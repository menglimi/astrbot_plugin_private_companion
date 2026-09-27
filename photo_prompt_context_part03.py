# -*- coding: utf-8 -*-
"""photo_prompt_context 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 photo_prompt_context.py，仅调整模块级依赖的导入来源。
"""
import hashlib
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
    ResolvedPhotoPromptContext,
    _EDIT_WORKFLOWS,
    _SELFIE_WORKFLOWS,
    _audit,
    _compatible,
    _photo_content,
    _preview,
    _replace_photo_prompt_section,
    _split_clauses,
    _validated_photo_prompt_section,
    _value,
)
from .photo_prompt_context_part02 import (
    _assemble,
    _budget_sections,
    _reference_roles,
    _sanitize_sections,
    _scan_residual_conflicts,
)


def _sanitize_reference(reference: Any, wardrobe: Any, workflow_kind: str) -> tuple[Any, dict[str, Any] | None]:
    if reference is None:
        return None, None
    workflow = str(workflow_kind or "").strip().lower()
    if workflow in _EDIT_WORKFLOWS or workflow not in _SELFIE_WORKFLOWS:
        return reference, None
    roles = _reference_roles(reference, wardrobe)
    ignored_roles = frozenset(
        str(item or "").strip().lower()
        for item in (_value(reference, "ignored_reference_roles", ()) or ())
        if str(item or "").strip()
    )
    if "outfit" not in roles and "outfit" not in ignored_roles:
        return reference, None
    if (
        str(_value(reference, "kind", "") or "").strip().lower() == "recent_sent_photo"
        and "outfit" in ignored_roles
    ):
        return reference, None
    active_category = str(_value(wardrobe, "category", "") or "").strip().lower()
    authoritative = bool(_value(wardrobe, "lock_outfit", False) and active_category)
    excluded = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "excluded_categories", ()) or ())
        if str(item or "").strip()
    )
    reference_category = str(_value(reference, "outfit_category", "") or "").strip().lower()
    reason = ""
    category = reference_category or "unknown"
    if reference_category and reference_category in excluded:
        reason = "reference_outfit_excluded"
    elif authoritative and reference_category and not _compatible(reference_category, active_category):
        reason = "reference_outfit_conflict"
    elif authoritative and not reference_category and active_category not in {"reference_outfit"}:
        reason = "reference_outfit_unknown"
    if not reason:
        return reference, None
    raw = str(_value(reference, "id", "") or _value(reference, "source", "") or "reference")
    return None, {
        "source": "reference",
        "section": raw,
        "rule": reason,
        "category": category,
        "action": "reference_removed",
        "preview": _preview(raw),
        "sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest(),
        "effective_reference_roles": [],
    }


def _remove_reference_dependent_context(
    sections: Sequence[PromptSection],
) -> tuple[list[PromptSection], list[dict[str, Any]], list[dict[str, Any]]]:
    sanitized: list[PromptSection] = []
    detected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    reference_pattern = re.compile(
        r"\b(?:this|the|selected|provided|incompatible)\s+(?:image\s+)?reference\b"
        r"|\breference\s+(?:image|controls|responsibility)\b",
        flags=re.I,
    )
    for section in sections:
        payload = _photo_content(section)
        if payload is None:
            raise TypeError("sections must contain PromptSection values with PhotoPromptContent")
        if payload.domain_source == "user_request":
            sanitized.append(section)
            continue
        if payload.domain_source == "recent_continuity" and (
            payload.positive or payload.negative
        ):
            for text in (payload.positive, payload.negative):
                if not text:
                    continue
                detected.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="detected",
                        text=text,
                    )
                )
                removed.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="section_dropped",
                        text=text,
                    )
                )
            sanitized.append(
                _replace_photo_prompt_section(
                    section,
                    positive="",
                    negative="",
                )
            )
            continue

        fields: dict[str, str] = {}
        for field in ("positive", "negative"):
            text = getattr(payload, field)
            clauses, separator = _split_clauses(text)
            kept: list[str] = []
            for clause in clauses:
                if not reference_pattern.search(clause):
                    kept.append(clause)
                    continue
                detected.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="detected",
                        text=clause,
                    )
                )
                removed.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="clause_removed",
                        text=clause,
                    )
                )
            fields[field] = separator.join(kept)
        sanitized.append(_replace_photo_prompt_section(section, **fields))
    return sanitized, detected, removed


def resolve_photo_prompt_context(
    *,
    wardrobe: Any,
    sections: Sequence[PromptSection],
    prompt_format: str,
    workflow_kind: str,
    reference: Any = None,
) -> ResolvedPhotoPromptContext:
    clean_reference, reference_removed = _sanitize_reference(reference, wardrobe, workflow_kind)
    prepared: list[PromptSection] = []
    for value in sections:
        section = _validated_photo_prompt_section(value)
        if section is None:
            raise TypeError("sections must contain compatible photo prompt sections")
        prepared.append(section)
    detected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    if reference_removed:
        prepared, reference_detected, reference_removals = _remove_reference_dependent_context(prepared)
        detected.extend(reference_detected)
        removed.extend(reference_removals)
    sanitized, section_detected, section_removed, residual = _sanitize_sections(tuple(prepared), wardrobe)
    detected.extend(section_detected)
    removed.extend(section_removed)
    complete_prompt = _assemble(sanitized, prompt_format)
    budgeted = _budget_sections(sanitized)
    residual.extend(_scan_residual_conflicts(budgeted, wardrobe))
    return ResolvedPhotoPromptContext(
        final_prompt=_assemble(budgeted, prompt_format),
        complete_prompt=complete_prompt,
        prompt_sections=tuple(budgeted),
        reference=clean_reference,
        detected_conflicts=tuple(detected),
        removed_conflicts=tuple(removed),
        residual_conflicts=tuple(residual),
        reference_removed=reference_removed,
    )
