"""Revocable read-only memory scope. Original ownership and writes never change."""
from copy import deepcopy

from companion_v2.execution.models import TaskError, require, stamp
from .account_links import PrivateAccountLinks, member_for

CONFIRMATION = "share-linked-long-term-memory"
HISTORY_CONFIRMATION = "include-existing-linked-memory"


def validate_policy(value):
    if (type(value) is not dict or set(value) - {"enabled", "history", "confirmation"}
            or type(value.get("enabled")) is not bool
            or value.get("history") not in ("since_enabled", "all")):
        raise TaskError("account_link_memory_invalid", "choose an explicit memory switch and history range")


def apply_policy(group, previous, at):
    old = (previous or {}).get("memory", {"enabled": False, "history": "since_enabled"})
    same_members = bool(previous and previous["members"] == group["members"]
                        and previous["persona_id"] == group["persona_id"])
    supplied = group.get("memory")
    policy = deepcopy(supplied if supplied is not None else old)
    # Adding an account cannot silently extend an old authorization.
    if supplied is None and not same_members:
        policy["enabled"] = False
    changed = (not same_members or not old.get("enabled")
               or old.get("history") != policy["history"])
    if policy["enabled"] and changed:
        expected = HISTORY_CONFIRMATION if policy["history"] == "all" else CONFIRMATION
        if policy.pop("confirmation", None) != expected:
            raise TaskError("account_link_memory_confirmation_required", "confirm memory sharing and its history range")
    policy.pop("confirmation", None)
    group["memory"] = policy
    group["memory_since"] = stamp(at) if changed else previous.get("memory_since", stamp(at))


def read_scope(application, context, purpose):
    """Re-resolve all routes and permissions on every lease validation."""
    runtime = application.runtime
    links = PrivateAccountLinks(runtime)
    state = links.read()
    current = member_for(context)
    group = next((g for g in state["groups"] if current in g["members"]), None)
    persona = getattr(application, "private_account_link_persona", None)
    resolve = getattr(application, "private_account_link_resolve", None)
    if not callable(persona) or not callable(resolve):
        raise TaskError("account_link_memory_scope_unavailable", "current memory persona and routes are required")
    native_persona = persona(current)
    if not native_persona:
        raise TaskError("account_link_memory_scope_unavailable", "one current native persona is required")
    shared = bool(group and group.get("memory", {}).get("enabled") is True)
    if group and shared and group["persona_id"] != native_persona:
        raise TaskError("account_link_memory_scope_changed", "linked persona changed")
    members = group["members"] if shared else [current]
    scopes = []
    for member in members:
        original = resolve(member)
        if member_for(original) != member or persona(member) != native_persona:
            raise TaskError("account_link_memory_scope_changed", "linked route or persona changed")
        require(application.tasks.check_context_access(original, action="conversation.reply", observed_at=application.now()))
        application.memory.writer.access.check(original)
        if purpose == "recall" and not application.memory.writer.access.policy_for(original).automatic_context:
            raise TaskError("account_link_memory_access_denied", "automatic memory is disabled for a linked account")
        identity = getattr(application, "private_account_link_memory_identity", None)
        native = identity(original) if callable(identity) else {
            "owner_bot_id": member["account_id"], "platform_name": ""}
        if (not isinstance(native, dict) or not isinstance(native.get("owner_bot_id"), str)
                or not native["owner_bot_id"].strip() or native["owner_bot_id"] == "self"
                or not isinstance(native.get("platform_name"), str)):
            raise TaskError("account_link_memory_scope_unavailable", "verified native memory identity is required")
        scopes.append({**member, "memory_identity": native, "session_id": f"{original.scope.platform}:FriendMessage:{original.scope.session_id}",
                       "since": group["memory_since"] if shared and member != current
                           and group["memory"]["history"] == "since_enabled" else None})
    return {"schema": "companion.linked-memory-read.v1", "revision": state["revision"],
            "group_id": group["id"] if shared else None, "persona_id": native_persona,
            "reader_user_id": current["native_account_id"],
            "members": scopes}
