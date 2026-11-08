"""Repair illegal tool-call pairing in the history we are about to send.

The core reply path hands the persisted conversation to the SDK, which converts
it into the exact message list every provider (and every fallback provider) sees.
If one stored entry is an ``assistant`` message with ``tool_calls`` and no
matching ``tool`` message, the whole turn dies with HTTP 400 on OpenAI-compatible
relays and on Gemini relays alike, and the character answers with the persona's
custom error line instead of a reply -- observed in production as the literal
``出错了......`` persona fallback.

Nothing in the host heals that direction, and third-party request hooks rewrite
``req.contexts`` behind us, so the legality check has to run *after* every other
request hook and *before* the SDK freezes the request.

The same hook also removes the other half of that contract: history may not
advertise a tool the request does not declare.  Our own turns are the source of
those examples -- they store their assistant tool calls -- so a later request
without companion tools (V2 declined the turn and AstrBot's own pipeline answers,
or the lean manifest dropped the action) shows the model a call it can copy but
cannot execute.  Observed in production twice in 48h as AstrBot's
``未找到指定的工具`` on ``companion_v2_group_participation_choose``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from companion_v2.conversation.history_legality import (
    MISSING_TOOL_RESULT_TEXT,
    ToolPairingRepair,
    repair_tool_call_pairing,
)
from .core_reply import CORE_REPLY_CONTEXT_EXTRA
from .core_reply_tool_guard import (
    declared_tool_names,
    drop_undeclared_companion_calls,
    is_managed_request,
    summarize_names,
    turn_identity,
)


logger = logging.getLogger(__name__)


def _event_ref(event: Any) -> str:
    try:
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    except Exception:
        return "unknown"
    prepared = core.get("prepared") if isinstance(core, dict) else None
    if isinstance(prepared, Mapping):
        return str(prepared.get("event_ref") or "unknown")
    return "unknown"


def _is_companion_turn(event: Any) -> bool:
    """Stay inside this plugin's jurisdiction, like every other core-path hook."""
    try:
        return is_managed_request(event)
    except Exception:
        return False


def _tool_message(call_id: str, text: str) -> Any:
    from astrbot.core.agent.message import Message
    return Message(role="tool", tool_call_id=call_id, content=text)


def _factory_for(messages: list[Any]):
    """Keep repaired messages in the shape of the list they join."""
    for message in messages:
        if not isinstance(message, Mapping):
            return _tool_message
    return None


def _repair(messages: Any, event: Any, *, where: str) -> ToolPairingRepair:
    if not _is_companion_turn(event):
        return ToolPairingRepair()
    if not isinstance(messages, list) or not messages:
        return ToolPairingRepair()
    try:
        report = repair_tool_call_pairing(
            messages,
            placeholder=MISSING_TOOL_RESULT_TEXT,
            factory=_factory_for(messages),
        )
    except Exception:
        # This check exists so that a damaged history does not cost the turn, so it
        # must never be the reason one is lost. The host logs hook errors and keeps
        # going, but leaning on that would leave the repair silently skipped, with
        # nothing naming why the same request failed again.
        logger.warning("[CompanionV2] 历史工具调用配对复核失败，保留原样继续 where=%s event_ref=%s",
                       where, _event_ref(event), exc_info=True)
        return ToolPairingRepair()
    if report.changed:
        # Visible on purpose: a silent repair would hide whichever hook dropped
        # the result, and this line is the only signal that a turn was saved
        # from a permanent provider error.
        logger.warning(
            "[CompanionV2] 历史工具调用配对已修复 where=%s filled=%s dropped_isolated=%s event_ref=%s",
            where, report.filled, report.dropped_isolated_tool_messages, _event_ref(event),
        )
    return report


def repair_request_history(event: Any, req: Any) -> ToolPairingRepair:
    """Repair ``req.contexts`` before the SDK converts it into agent messages."""
    if not _is_companion_turn(event):
        return ToolPairingRepair()
    report = _repair(getattr(req, "contexts", None), event, where="request")
    drop_undeclared_companion_history(getattr(req, "contexts", None), req, event, where="request")
    return report


def repair_run_context_history(event: Any, run_context: Any) -> ToolPairingRepair:
    """Repair the messages the runner will actually send, if a hook got there first."""
    if not _is_companion_turn(event):
        return ToolPairingRepair()
    report = _repair(getattr(run_context, "messages", None), event, where="run_context")
    try:
        from .core_reply_tool_dispatch import active_core_runner
        runner = active_core_runner(event, run_context)
    except Exception:
        runner = None
    if runner is not None:
        # The request the runner holds is the one that will be sent, so its tool
        # set -- not a re-derived one -- decides which example stays.
        drop_undeclared_companion_history(getattr(run_context, "messages", None),
                                          getattr(runner, "req", None), event, where="run_context")
    return report


def drop_undeclared_companion_history(messages: Any, req: Any, event: Any, *, where: str,
                                      declared=None) -> dict | None:
    """Take the undeclared companion example out of the history we are sending.

    The model imitates what the history shows it, and this plugin is the only
    writer of ``companion_v2_*`` calls.  When the request declares none of them --
    V2 declined the turn and AstrBot's own pipeline answers, or the lean manifest
    dropped the action -- those stored calls are the example that makes the model
    ask for a tool it cannot execute.  Dropping call and result together keeps the
    pairing the repair above just proved; every other plugin's tools stay as they
    are.
    """
    if not isinstance(messages, list) or not messages:
        return None
    names = declared if declared is not None else declared_tool_names(req)
    if names is None:
        return None
    try:
        report = drop_undeclared_companion_calls(messages, names)
    except Exception:
        # This hook exists so a damaged history does not cost the turn; it must
        # never be the reason one is lost, but a silent skip would leave the same
        # echo in place with nothing naming why.
        logger.warning("[CompanionV2] 历史未挂载工具调用清理失败，保留原样继续 where=%s event_ref=%s",
                       where, _event_ref(event), exc_info=True)
        return None
    if not report:
        return None
    turn, reference = turn_identity(event)
    logger.warning(
        "[CompanionV2] 历史中本轮未挂载的陪伴工具调用已移除 reason=tool_not_in_request "
        "where=%s tools=%s calls=%s results=%s emptied=%s declared=%s turn=%s event_ref=%s",
        where, ",".join(report["names"]), report["calls"], report["results"],
        report["emptied"], summarize_names(names), turn, reference,
    )
    return report
