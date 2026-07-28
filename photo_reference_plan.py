from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .photo_reference_intent import REFERENCE_ROLES, ReferenceIntent


__all__ = [
    "ReferenceBinding",
    "PhotoReferencePlan",
    "ReferenceFallback",
    "build_photo_reference_plan",
    "project_reference_plan_for_backend",
    "evaluate_reference_fallback",
]


@dataclass(frozen=True)
class ReferenceBinding:
    reference_id: str
    path: str
    roles: tuple[str, ...]
    priority: int
    preserve: tuple[str, ...]
    ignore: tuple[str, ...]


@dataclass(frozen=True)
class PhotoReferencePlan:
    bindings: tuple[ReferenceBinding, ...]
    primary_reference_id: str
    selection_reason: str
    fallback_reason: str


@dataclass(frozen=True)
class ReferenceFallback:
    requested_roles: tuple[str, ...]
    fulfilled_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    message: str


def _roles(values: Iterable[Any]) -> tuple[str, ...]:
    selected = {str(value or "").strip().lower() for value in values}
    return tuple(role for role in REFERENCE_ROLES if role in selected)


def build_photo_reference_plan(
    intent: ReferenceIntent,
    candidates: Iterable[Mapping[str, Any]],
) -> PhotoReferencePlan:
    requested = set(_roles(intent.requested_roles))
    excluded = set(_roles(intent.excluded_roles))
    bindings: list[ReferenceBinding] = []
    for index, candidate in enumerate(candidates):
        reference_id = str(candidate.get("id") or f"reference-{index + 1}").strip()
        path = str(candidate.get("path") or candidate.get("source") or "").strip()
        if not reference_id or not path:
            continue
        kind = str(candidate.get("kind") or "").strip().lower()
        candidate_roles = set(_roles(candidate.get("reference_roles") or ()))
        available_roles = set(
            _roles(candidate.get("available_reference_roles") or candidate_roles)
        )
        if kind == "source":
            candidate_roles.add("source")
            available_roles.add("source")
        assigned = tuple(
            role for role in REFERENCE_ROLES if role in candidate_roles & requested - excluded
        )
        if not assigned:
            continue
        try:
            configured_priority = int(candidate.get("priority") or 0)
        except (TypeError, ValueError):
            configured_priority = 0
        default_priority = {
            "source": 1000,
            "explicit": 900,
            "recent_sent_photo": 800,
            "daily_outfit": 600,
            "library": 500,
            "persona": 400,
        }.get(kind, 300)
        ignored = tuple(
            role
            for role in REFERENCE_ROLES
            if role in candidate_roles | available_roles and role not in assigned
        )
        bindings.append(
            ReferenceBinding(
                reference_id=reference_id,
                path=path,
                roles=assigned,
                priority=configured_priority or default_priority,
                preserve=assigned,
                ignore=ignored,
            )
        )

    bindings.sort(key=lambda item: (-item.priority, -len(item.roles), item.reference_id))
    bound_roles = {role for binding in bindings for role in binding.roles}
    missing = requested - bound_roles
    if "source" in missing:
        return PhotoReferencePlan(tuple(bindings), "", "no_usable_reference", "missing_source_reference")
    if not bindings:
        reason = "no_reference_requested" if not requested else "no_usable_reference"
        fallback = "" if not requested else "missing_requested_roles"
        return PhotoReferencePlan((), "", reason, fallback)

    primary = bindings[0]
    selection_reason = (
        "explicit_source_reference"
        if "source" in primary.roles
        else "highest_priority_role_match"
    )
    fallback_reason = "missing_requested_roles" if missing else ""
    return PhotoReferencePlan(
        tuple(bindings),
        primary.reference_id,
        selection_reason,
        fallback_reason,
    )


def project_reference_plan_for_backend(
    plan: PhotoReferencePlan,
    *,
    max_images: int,
) -> tuple[tuple[ReferenceBinding, ...], str]:
    capacity = max(0, int(max_images or 0))
    primary = next(
        (
            binding
            for binding in plan.bindings
            if binding.reference_id == plan.primary_reference_id
        ),
        None,
    )
    ordered = ([primary] if primary is not None else []) + [
        binding for binding in plan.bindings if binding is not primary
    ]
    submitted = tuple(ordered[:capacity])
    submitted_roles = {role for binding in submitted for role in binding.roles}
    textual_roles = _roles(
        role
        for binding in plan.bindings
        for role in binding.roles
        if role not in submitted_roles
    )
    if not textual_roles:
        return submitted, ""
    roles_text = ", ".join(textual_roles)
    return (
        submitted,
        "Additional visual reference responsibilities could not be submitted: "
        f"{roles_text}. Follow the user's textual requirements for these roles and "
        "do not claim exact visual matching.",
    )


def evaluate_reference_fallback(
    intent: ReferenceIntent,
    plan: PhotoReferencePlan,
    *,
    submitted_reference_ids: Iterable[str] | None = None,
) -> ReferenceFallback:
    submitted = set(submitted_reference_ids or ())
    fulfilled = _roles(
        role
        for binding in plan.bindings
        if submitted_reference_ids is None or binding.reference_id in submitted
        for role in binding.roles
    )
    requested = _roles(intent.requested_roles)
    optional_roles = {"identity"} if intent.source == "workflow_default" else set()
    missing = tuple(
        role
        for role in requested
        if role not in fulfilled and role not in optional_roles
    )
    if "source" in missing:
        message = "缺少可用的改图原图，已停止改图。"
    elif missing:
        labels = {
            "identity": "人物身份",
            "pose": "姿势",
            "scene": "场景",
            "style": "画风",
        }
        parts: list[str] = []
        if "outfit" in missing:
            prefix = "已保持人物身份，但" if "identity" in fulfilled else ""
            parts.append(
                f"{prefix}没有找到匹配的服装参考图，本次服装按文字要求生成。"
            )
        if "continuity" in missing:
            parts.append("没有找到可用的上一张图片，无法完整保持人物、服装和场景连续性。")
        remaining = tuple(
            role for role in missing if role not in {"outfit", "continuity"}
        )
        if remaining:
            parts.append(
                "以下参考要求未能满足："
                + "、".join(labels.get(role, role) for role in remaining)
                + "。本次会按明确的文字要求继续生成。"
            )
        message = "".join(parts)
    else:
        message = ""
    return ReferenceFallback(requested, fulfilled, missing, message)
