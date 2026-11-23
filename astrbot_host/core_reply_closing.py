"""Run the deterministic private closing guard before AstrBot wakes its reply model.

The guard is intentionally pre-model and side-effect free: it reads the already
authorized native dialogue and the per-window reply settings, then either returns
``suppress_reply=True`` or leaves the ordinary core path untouched.  It never
calls a model, sends, writes storage or decides identity.  A missing application,
history, clock or configuration always fails open (reply continues), because a
stale read must never turn into silence.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from time import perf_counter

from companion_v2.conversation import private_closing
from companion_v2.conversation.native_history import dialogue
from companion_v2.conversation.reply_settings import GROUP
from companion_v2.execution.models import TaskContext, require


logger = logging.getLogger(__name__)
_PRESENTATION_PREFIX = GROUP + "."
_HEAVY_CONTENT = frozenset({"Image", "Record", "Video", "File", "Forward", "Node", "Nodes", "Json"})


def _has_heavy_content(event):
    """True for an image/audio/video/file/forward part, never for At/Reply/Face."""
    from .core_reply_forwards import get_parsed_forwards

    if get_parsed_forwards(event) is not None:
        return True
    kinds = set()
    for part in getattr(getattr(event, "message_obj", None), "message", ()) or ():
        part_type = getattr(part, "type", None)
        kinds.add(getattr(part_type, "value", part_type))
    return bool(kinds & _HEAVY_CONTENT)


def _read_dialogue(application, context, at):
    messages = getattr(application, "messages", None)
    if messages is None:
        raise RuntimeError("private closing guard has no message owner")
    require(messages.tasks.authorize_context(context, action="conversation.reply", observed_at=at))
    return dialogue(messages, context, at, limit=8, max_chars=4000)


def _settings(application, context, at):
    owner = getattr(application, "reply_settings", None)
    if owner is None:
        raise RuntimeError("private closing guard has no reply settings owner")
    snapshot = owner.snapshot(context.actor, context, at)
    values = {key.removeprefix(_PRESENTATION_PREFIX): value
              for key, value in snapshot.values.items()}
    return private_closing.Settings.from_values(values)


def prepare_private_closing_decision(event, prepared, *, application, now=None):
    """Return the pre-model closing decision and append its optional guidance.

    The caller merges ``suppress_reply`` with the ordinary decision result and
    owns the actual default-reply suppression, ledger row and presence cleanup.
    """
    started = perf_counter()
    result = {
        "status": "skipped", "reason": "not_private", "suppress_reply": False,
        "action": "reply", "last_reply_at": None, "categories": (),
        "last_assistant_closing": False, "prior_user_closing": False,
        "window_seconds": None, "instruction": "",
    }
    if not isinstance(prepared, dict):
        return result
    if prepared.get("channel") != "private":
        prepared["private_closing"] = result
        return result
    context = prepared.get("context")
    if application is None or not isinstance(context, TaskContext):
        result.update(reason="application_unavailable", elapsed_ms=_elapsed(started))
        prepared["private_closing"] = result
        _log(prepared, result)
        return result
    native = prepared.get("native_input")
    at = getattr(native, "at", None)
    if not isinstance(at, datetime):
        at = now() if callable(now) else datetime.now(timezone.utc)
    try:
        settings = _settings(application, context, at)
    except Exception as error:
        result.update(reason="settings_unavailable", elapsed_ms=_elapsed(started),
                      detail=type(error).__name__)
        prepared["private_closing"] = result
        _log(prepared, result)
        return result
    if not settings.enabled:
        result.update(status="ok", reason="disabled", elapsed_ms=_elapsed(started),
                      window_seconds=settings.window_seconds)
        prepared["private_closing"] = result
        _log(prepared, result)
        return result
    try:
        recent = _read_dialogue(application, context, at)
    except Exception as error:
        result.update(reason="history_unavailable", elapsed_ms=_elapsed(started),
                      detail=type(error).__name__)
        prepared["private_closing"] = result
        _log(prepared, result)
        return result
    text = str(prepared.get("text") or getattr(native, "text", "") or "")
    decision = private_closing.closing_decision(
        settings, recent, current_text=text, at=at,
        has_media=_has_heavy_content(event))
    instruction = private_closing.closing_instruction(settings, decision)
    result = {**decision, "status": "ok", "instruction": instruction,
              "elapsed_ms": _elapsed(started)}
    if instruction:
        prepared["prompt_instruction"] = (
            str(prepared.get("prompt_instruction") or "") + "\n\n" + instruction).strip()
    prepared["private_closing"] = result
    _log(prepared, result)
    return result


def _elapsed(started):
    return round((perf_counter() - started) * 1000)


def _log(prepared, result):
    line = ("[CompanionV2] 私聊收尾判断 event_ref=%s turn_id=%s status=%s action=%s "
            "reason=%s last_reply_at=%s categories=%s elapsed_ms=%s")
    fields = (
        prepared.get("event_ref"), prepared.get("turn_id"), result.get("status"),
        result.get("action"), result.get("reason"), result.get("last_reply_at"),
        ",".join(result.get("categories") or ()) or "-", result.get("elapsed_ms", 0),
    )
    if result.get("suppress_reply") or result.get("last_assistant_closing") or result.get("prior_user_closing"):
        logger.info(line, *fields)
    else:
        logger.debug(line, *fields)
