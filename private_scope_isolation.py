from __future__ import annotations

import re
from typing import Any


GROUP_SCOPE_MARKERS = (
    "private_companion_group_context_v1",
    "private_companion_group_injection_guard_v1",
    "private_companion_group_persona_denoise_v1",
    "private_companion_group_high_intensity_reply_guard_v1",
    "private_companion_group_cycle_boundary_v1",
    "private_companion_member_safety_hidden_marker_v1",
    "private_companion_expression_voice_group_v1",
    "private_companion_group_image_vision_v1",
)
_GROUP_MARKER_PATTERN = "|".join(re.escape(marker) for marker in GROUP_SCOPE_MARKERS)
_GROUP_BLOCK_RE = re.compile(
    rf"\n*\s*<!--\s*(?:{_GROUP_MARKER_PATTERN})\s*-->.*?"
    rf"(?=\n\s*<!--\s*private_companion_[a-z0-9_]+_v1\s*-->\s*|\Z)",
    re.DOTALL,
)


def strip_group_scope_prompt_artifacts(text: Any) -> str:
    """Remove only Companion-owned group blocks from a private prompt surface."""
    cleaned = str(text or "")
    if not cleaned or "private_companion_" not in cleaned:
        return cleaned
    cleaned = _GROUP_BLOCK_RE.sub("\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _is_private(event: Any) -> bool:
    try:
        return bool(getattr(event, "is_private_chat", lambda: False)())
    except Exception:
        return ":FriendMessage:" in str(
            getattr(event, "unified_msg_origin", "") or ""
        )


def _clean_text_field(target: Any, field: str) -> bool:
    value = (
        target.get(field)
        if isinstance(target, dict)
        else getattr(target, field, None)
    )
    if not isinstance(value, str):
        return False
    cleaned = strip_group_scope_prompt_artifacts(value)
    if cleaned == value:
        return False
    try:
        if isinstance(target, dict):
            target[field] = cleaned
        else:
            setattr(target, field, cleaned)
    except Exception:
        return False
    return True


def sanitize_private_request_group_artifacts(event: Any, req: Any) -> int:
    """Scrub retained group-only prompt surfaces before private dispatch."""
    if not _is_private(event):
        return 0

    changed = sum(
        _clean_text_field(req, field) for field in ("system_prompt", "prompt")
    )

    for part in getattr(req, "extra_user_content_parts", None) or ():
        changed += int(_clean_text_field(part, "text"))

    contexts = getattr(req, "contexts", None)
    if isinstance(contexts, list):
        for item in contexts:
            if isinstance(item, dict):
                changed += int(_clean_text_field(item, "content"))

    fragments = getattr(req, "_private_companion_turn_prompt_fragments", None)
    if isinstance(fragments, list):
        kept = []
        for item in fragments:
            marker = str(item.get("marker") or "") if isinstance(item, dict) else ""
            source = str(item.get("source") or "") if isinstance(item, dict) else ""
            if source == "group" or any(
                group_marker in marker for group_marker in GROUP_SCOPE_MARKERS
            ):
                changed += 1
                continue
            kept.append(item)
        if len(kept) != len(fragments):
            try:
                req._private_companion_turn_prompt_fragments = kept
            except Exception:
                pass

    return changed


__all__ = [
    "GROUP_SCOPE_MARKERS",
    "sanitize_private_request_group_artifacts",
    "strip_group_scope_prompt_artifacts",
]
