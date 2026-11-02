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
        return isinstance(event.get_extra(CORE_REPLY_CONTEXT_EXTRA), dict)
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
    return _repair(getattr(req, "contexts", None), event, where="request")


def repair_run_context_history(event: Any, run_context: Any) -> ToolPairingRepair:
    """Repair the messages the runner will actually send, if a hook got there first."""
    return _repair(getattr(run_context, "messages", None), event, where="run_context")
