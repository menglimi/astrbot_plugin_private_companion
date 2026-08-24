"""Pure helpers for composing reconciled REQ-041 runtime views.

The helpers deliberately have no AstrBot dependency so the namespace boundary
can be tested independently from the host runtime.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from .expression_scope_ownership import runtime_binding_is_approved
except ImportError:  # pragma: no cover - direct-module test compatibility
    from expression_scope_ownership import runtime_binding_is_approved


PRIVATE_FIELDS = frozenset({
    "nickname", "style", "profile_origin", "auto_profile_created",
    "companion_memory", "intent_profile", "dialogue_episodes", "open_loops",
    "behavior_habits", "action_preferences", "action_consequences", "state_continuity",
    "recent_reply_topics", "birthday_profile", "birthday_curiosity_opt_out",
    "birthday_curiosity_asked_at", "birthday_curiosity_answered_at", "expression_profile",
    "profile_fact_revision",
})

GROUP_SHARED_FIELDS = frozenset({
    "recent_messages", "recent_bot_replies", "slang_terms", "slang_meanings", "topic_signatures", "topic_threads",
    "group_episodes", "relationship_edges", "atmosphere", "interjection_feedback",
    "expression_profile",
})

GROUP_MEMBER_FIELDS = frozenset({
    "name", "identity_name", "group_role", "group_role_label", "count", "last_seen",
    "display_name_events", "recent_phrases",
})


def _projection_fields(projection: Any) -> dict[str, Any] | None:
    if not isinstance(projection, dict) or projection.get("ok") is not True:
        return None
    fields = projection.get("fields")
    return fields if isinstance(fields, dict) else {}


def _persona_expression_profile(projection: Any) -> dict[str, Any]:
    fields = _projection_fields(projection)
    profile = fields.get("expression_profile") if isinstance(fields, dict) else None
    return deepcopy(profile) if isinstance(profile, dict) else {}


def overlay_private_runtime_view(base: Any, projection: Any, persona_projection: Any = None) -> Any:
    """Overlay only allowlisted private-domain fields from a reconciled projection."""
    if not isinstance(base, dict):
        return base
    fields = _projection_fields(projection)
    if fields is None:
        return base
    view = dict(base)
    for key, value in fields.items():
        if key in PRIVATE_FIELDS:
            view[key] = deepcopy(value)
    view["persona_global_expression_profile"] = _persona_expression_profile(persona_projection)
    view["req041_scoped_read_generation"] = "new"
    return view


def overlay_group_runtime_view(
    base: Any,
    shared_projection: Any,
    *,
    sender_id: str = "",
    member_projection: Any = None,
    persona_projection: Any = None,
) -> Any:
    """Compose one group view without admitting private or another-group fields."""
    if not isinstance(base, dict):
        return base
    shared_fields = _projection_fields(shared_projection)
    member_fields = _projection_fields(member_projection)
    if shared_fields is None and member_fields is None:
        return base
    view = deepcopy(base)
    if shared_fields is not None:
        for key, value in shared_fields.items():
            if key in GROUP_SHARED_FIELDS:
                view[key] = deepcopy(value)
    if member_fields is not None and sender_id:
        members = view.setdefault("members", {})
        if isinstance(members, dict):
            member = dict(members.get(sender_id) or {})
            for key, value in member_fields.items():
                if key in GROUP_MEMBER_FIELDS:
                    member[key] = deepcopy(value)
            members[sender_id] = member
    view["persona_global_expression_profile"] = _persona_expression_profile(persona_projection)
    view["req041_scoped_read_generation"] = "new"
    return view


def scoped_approved_expression_rules(context_owner: Any) -> list[dict[str, Any]] | None:
    """Return current-namespace rules, or ``None`` when legacy selection still owns the read.

    An empty list is authoritative for a reconciled namespace and therefore
    must not fall back to the legacy cross-source aggregate.  The same
    fail-closed empty result applies while an already-bound scoped namespace
    is being reconciled after a write.
    """
    if not isinstance(context_owner, dict):
        return None
    generation = context_owner.get("req041_scoped_read_generation")
    if generation not in {"new", "new_unavailable"}:
        return None
    if generation == "new_unavailable":
        return []
    profile = context_owner.get("expression_profile")
    if not isinstance(profile, dict):
        return []
    local_rules = profile.get("learned_rules") if isinstance(profile, dict) else []
    global_profile = context_owner.get("persona_global_expression_profile")
    global_rules = global_profile.get("learned_rules") if isinstance(global_profile, dict) else []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in list(local_rules if isinstance(local_rules, list) else []) + list(
        global_rules if isinstance(global_rules, list) else []
    ):
        if not isinstance(item, dict) or not runtime_binding_is_approved(item.get("scope_binding")):
            continue
        binding = item.get("scope_binding")
        key = (str(binding.get("application_namespace") or ""), str(item.get("id") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(deepcopy(item))
    return result


__all__ = [
    "GROUP_MEMBER_FIELDS", "GROUP_SHARED_FIELDS", "PRIVATE_FIELDS",
    "overlay_group_runtime_view", "overlay_private_runtime_view",
    "scoped_approved_expression_rules",
]
