"""Keep the stored tool-call history legal for every provider we may fall back to.

A conversation that reaches a provider with an ``assistant`` message carrying
``tool_calls`` but no matching ``tool`` messages is rejected outright:

* OpenAI-compatible relays answer ``An assistant message with 'tool_calls' must
  be followed by tool messages responding to each 'tool_call_id'`` (HTTP 400),
* Gemini relays answer ``functionCall appears before N pending functionResponse
  part(s)`` (HTTP 400),
* the Anthropic adapter cannot rebuild a ``tool_result`` block at all.

Once one such entry is persisted, every later request to that conversation fails
until the pair is repaired by hand, so a single lost tool result is enough to
silence the character permanently. The host does not heal this direction: its
request sanitizer drops orphaned ``tool`` messages, but never answers an
assistant ``tool_calls`` whose responses are gone.

This module is that repair, expressed once so both the core reply path and the
native path can share it. It is deliberately conservative:

* it never drops a *legal* pair, so multi-round tool history survives verbatim;
* a call answered by nothing keeps its place in the model's plan and receives a
  synthetic failure result, so the model can react instead of losing the turn;
* an isolated ``tool`` message (its ``assistant`` was trimmed away) is dropped,
  because no provider accepts a tool result without a matching call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

# The text a repaired call answers with. It has to read as a failure to the
# model -- never as a successful tool result -- or the character would narrate
# work that never happened.
MISSING_TOOL_RESULT_TEXT = (
    "error: this tool call has no stored result; treat it as not executed in "
    "this conversation and answer from the visible message history only."
)

MISSING_TOOL_RESULT_CODE = "history_tool_result_missing"


@dataclass(frozen=True)
class ToolPairingRepair:
    """What ``repair_tool_call_pairing`` changed, for logging and tests."""

    filled: int = 0
    """Assistant tool calls that received a synthetic failure result."""
    dropped_isolated_tool_messages: int = 0
    """Tool messages that had no assistant call left to answer."""

    @property
    def changed(self) -> bool:
        return bool(self.filled or self.dropped_isolated_tool_messages)


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _role(item: Any) -> str:
    return str(_field(item, "role", "") or "")


def _call_ids(tool_calls: Any) -> list[str]:
    """Collect non-empty ``tool_calls[].id`` values in the model's own order."""
    ids: list[str] = []
    for call in tool_calls or ():
        identifier = _field(call, "id")
        if isinstance(identifier, str) and identifier:
            ids.append(identifier)
    return ids


def _default_factory(call_id: str, text: str) -> Any:
    """Build a tool message shaped like the conversation it lands in."""
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def repair_tool_call_pairing(
    messages: list[Any],
    *,
    placeholder: str = MISSING_TOOL_RESULT_TEXT,
    factory: Callable[[str, str], Any] | None = None,
) -> ToolPairingRepair:
    """Repair ``assistant.tool_calls``/``tool`` pairing in place.

    Args:
        messages: Conversation messages, as pydantic ``Message`` objects or as
            plain OpenAI-style dicts. Repaired in place; legal messages are left
            untouched and in their original order.
        placeholder: Result text used for calls whose response is missing.
        factory: Builds a tool message for ``(tool_call_id, text)``. Defaults to
            an OpenAI-style dict; the core reply path passes the host ``Message``
            constructor instead.

    Returns:
        ``ToolPairingRepair`` describing what changed.
    """
    if not messages:
        return ToolPairingRepair()

    build = factory or _default_factory
    repaired: list[Any] = []
    filled = 0
    dropped = 0
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        role = _role(message)
        if role == "assistant":
            ids = _call_ids(_field(message, "tool_calls"))
            if ids:
                repaired.append(message)
                index += 1
                answered: set[str] = set()
                while index < total and _role(messages[index]) == "tool":
                    answer = _field(messages[index], "tool_call_id")
                    if answer in ids and answer not in answered:
                        repaired.append(messages[index])
                        answered.add(answer)
                    else:
                        # A duplicate or a stray answer for another call: the
                        # provider rejects both, and the call it claims to
                        # answer is filled below anyway.
                        dropped += 1
                    index += 1
                for call_id in ids:
                    if call_id in answered:
                        continue
                    repaired.append(build(call_id, placeholder))
                    answered.add(call_id)
                    filled += 1
                continue
            repaired.append(message)
            index += 1
            continue
        if role == "tool":
            # No assistant call in this position: a trimmed or filtered tool
            # response. Every provider rejects it, so it cannot stay.
            dropped += 1
            index += 1
            continue
        repaired.append(message)
        index += 1

    report = ToolPairingRepair(filled=filled, dropped_isolated_tool_messages=dropped)
    if report.changed:
        messages[:] = repaired
    return report


def tool_call_pairing_is_legal(messages: list[Any]) -> bool:
    """Whether ``messages`` already satisfies ``repair_tool_call_pairing``."""
    pending: set[str] = set()
    for message in messages or ():
        role = _role(message)
        if role == "assistant":
            ids = _call_ids(_field(message, "tool_calls"))
            if ids:
                if pending:
                    return False
                pending = set(ids)
            elif pending:
                return False
            continue
        if role == "tool":
            answer = _field(message, "tool_call_id")
            if answer not in pending:
                return False
            pending.discard(answer)
            continue
        if pending:
            return False
    return not pending
