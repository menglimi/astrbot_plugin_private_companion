"""Request-scoped interception of AstrBot's native group context."""

from __future__ import annotations

import copy
import json
from typing import Any

from astrbot.core.agent.message import Message

try:
    from astrbot.core.agent.message import bind_checkpoint_messages
except ImportError:  # AstrBot versions before persisted conversation checkpoints.
    bind_checkpoint_messages = None


GROUP_CONTEXT_STASH_ATTR = "_private_companion_astrbot_group_context_stash"
ASTRBOT_GROUP_ICL_MARKERS = (
    "<system_reminder>",
    "You are in a group chat.",
    "--- BEGIN CONTEXT---",
    "--- END CONTEXT ---",
    "</system_reminder>",
)


def _part_text(part: Any) -> str:
    if isinstance(part, dict):
        return str(part.get("text") or part.get("content") or "")
    return str(getattr(part, "text", "") or getattr(part, "content", "") or "")


def is_astrbot_group_icl_part(part: Any) -> bool:
    """Recognize the bounded reminder emitted by AstrBot GroupChatContext."""

    text = _part_text(part).strip()
    if not text.startswith(ASTRBOT_GROUP_ICL_MARKERS[0]) or not text.endswith(
        ASTRBOT_GROUP_ICL_MARKERS[-1]
    ):
        return False
    return all(marker in text for marker in ASTRBOT_GROUP_ICL_MARKERS[1:-1])


def strip_astrbot_group_icl_parts(parts: Any) -> tuple[list[Any], int]:
    """Return request parts without AstrBot's native group ICL block."""

    if not isinstance(parts, list):
        return [], 0
    kept: list[Any] = []
    removed = 0
    for part in parts:
        if is_astrbot_group_icl_part(part):
            removed += 1
        else:
            kept.append(part)
    return kept, removed


def _request_contexts(req: Any) -> list[Any] | None:
    contexts = getattr(req, "contexts", None)
    if isinstance(contexts, list):
        return contexts
    if isinstance(contexts, str):
        try:
            parsed = json.loads(contexts)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _bind_history_snapshot(history: list[dict[str, Any]]) -> list[Message]:
    if callable(bind_checkpoint_messages):
        return bind_checkpoint_messages(history)
    messages: list[Message] = []
    for item in history:
        if str(item.get("role") or "") == "_checkpoint":
            continue
        message = Message.model_validate(item)
        if item.get("_no_save"):
            message._no_save = True
        messages.append(message)
    return messages


def intercept_astrbot_group_context(event: Any, req: Any) -> dict[str, Any]:
    """Hide native group context from this provider request without deleting it."""

    existing = getattr(event, GROUP_CONTEXT_STASH_ATTR, None)
    if isinstance(existing, dict):
        return {
            "history_intercepted": bool(existing.get("history_intercepted")),
            "history_messages": len(existing.get("history") or []),
            "group_icl_removed": int(existing.get("group_icl_removed") or 0),
            "already_intercepted": True,
        }

    original_parts = getattr(req, "extra_user_content_parts", None)
    kept_parts, removed_icl = strip_astrbot_group_icl_parts(
        original_parts
    )
    conversation = getattr(req, "conversation", None)
    contexts = _request_contexts(req)
    history_intercepted = conversation is not None and contexts is not None
    history = copy.deepcopy(contexts) if history_intercepted else []

    stash = {
        "request": req,
        "conversation_id": str(getattr(conversation, "cid", "") or ""),
        "history": history,
        "history_intercepted": history_intercepted,
        "group_icl_removed": removed_icl,
        "restored": False,
        "restore_failed": False,
    }
    setattr(event, GROUP_CONTEXT_STASH_ATTR, stash)
    try:
        if history_intercepted:
            req.contexts = []
        if isinstance(original_parts, list):
            req.extra_user_content_parts = kept_parts
    except Exception:
        if history_intercepted:
            req.contexts = contexts
        if isinstance(original_parts, list):
            req.extra_user_content_parts = original_parts
        try:
            delattr(event, GROUP_CONTEXT_STASH_ATTR)
        except Exception:
            pass
        raise
    return {
        "history_intercepted": history_intercepted,
        "history_messages": len(history),
        "group_icl_removed": removed_icl,
    }


def _restore_failure(stash: dict[str, Any], reason: str) -> dict[str, Any]:
    stash["restore_failed"] = True
    request = stash.get("request")
    if request is not None:
        # Prevent AstrBot from overwriting the stored conversation with a
        # provider-visible message list that intentionally omitted old history.
        try:
            request.conversation = None
        except Exception:
            pass
    return {"restored": False, "failed": True, "reason": reason}


def restore_astrbot_group_history(event: Any, run_context: Any) -> dict[str, Any]:
    """Reattach intercepted history after generation and before AstrBot saves."""

    stash = getattr(event, GROUP_CONTEXT_STASH_ATTR, None)
    if not isinstance(stash, dict) or not stash.get("history_intercepted"):
        return {"restored": False, "failed": False, "reason": "not_intercepted"}
    if stash.get("restored"):
        return {"restored": False, "failed": False, "reason": "already_restored"}

    request = stash.get("request")
    conversation = getattr(request, "conversation", None) if request is not None else None
    expected_cid = str(stash.get("conversation_id") or "")
    actual_cid = str(getattr(conversation, "cid", "") or "")
    if conversation is None or (expected_cid and actual_cid != expected_cid):
        return _restore_failure(stash, "conversation_changed")

    messages = getattr(run_context, "messages", None)
    if not isinstance(messages, list):
        return _restore_failure(stash, "run_context_messages_unavailable")

    raw_history = stash.get("history")
    if not isinstance(raw_history, list):
        return _restore_failure(stash, "history_snapshot_invalid")
    try:
        if all(isinstance(item, dict) for item in raw_history):
            historical_messages = _bind_history_snapshot(copy.deepcopy(raw_history))
        elif all(isinstance(item, Message) for item in raw_history):
            historical_messages = copy.deepcopy(raw_history)
        else:
            return _restore_failure(stash, "history_snapshot_unsupported")
    except Exception:
        return _restore_failure(stash, "history_snapshot_bind_failed")

    try:
        request.contexts = copy.deepcopy(raw_history)
    except Exception:
        return _restore_failure(stash, "request_context_restore_failed")
    insert_at = 1 if messages and getattr(messages[0], "role", "") == "system" else 0
    messages[insert_at:insert_at] = historical_messages
    stash["restored"] = True
    stash["history"] = []
    return {
        "restored": True,
        "failed": False,
        "reason": "restored",
        "history_messages": len(historical_messages),
    }


__all__ = [
    "ASTRBOT_GROUP_ICL_MARKERS",
    "GROUP_CONTEXT_STASH_ATTR",
    "intercept_astrbot_group_context",
    "is_astrbot_group_icl_part",
    "restore_astrbot_group_history",
    "strip_astrbot_group_icl_parts",
]
