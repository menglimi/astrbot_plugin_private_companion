"""Keep a turn alive when the model asks for a tool this request never declared.

A request's tool declarations are the only thing a provider is allowed to call.
The model can still name a tool it learned somewhere else -- most often from the
history we ourselves wrote: every companion turn stores its assistant tool calls,
so a later request that carries *no* companion tools (V2 bailed out to AstrBot's
own pipeline, or the lean manifest dropped the action) shows the model an example
of calling ``companion_v2_*`` and the model imitates it.

AstrBot answers such a call with ``未找到指定的工具`` and moves on; nothing names
the turn, nothing names what *was* available, and one host branch returns before
writing any tool result at all, which leaves the assistant tool call unanswered
and makes the *next* request fail with HTTP 400 -- a whole reply lost with no log
line anywhere.

This guard is the execution-time safety net for both shapes:

* every requested-but-undeclared name is reported once per turn with the tool
  name, a summary of what this request really declares, and the turn's identity;
* the round always keeps a matching tool result, so the loop can continue on the
  host's normal "no such tool" path instead of dying on an unpaired call.

It never removes a call, never rewrites an argument and never answers on the
user's behalf: the plugin cannot make an undeclared tool executable, only make
the miss visible and survivable.  History that advertises undeclared companion
tools is dropped earlier, in ``core_reply_history_guard``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

MANAGED_TURN_EXTRA = "_companion_v2_managed_turn"
CORE_TOOL_GUARD_EXTRA = "_companion_v2_core_tool_guard"
HANDLE_FUNCTION_TOOLS = "_handle_function_tools"
COMPANION_TOOL_PREFIX = "companion_v2_"
# Enough names to recognise the manifest in a log line, few enough to stay one line.
SUMMARY_LIMIT = 12


def mark_managed_turn(event, *, channel: str, event_ref: str) -> dict:
    """Mark a turn of a conversation this plugin owns, before any V2 decision.

    The guard and the history repair both have to know whether an agent run is
    ours.  A V2-admitted turn answers that with its context extra; a turn V2
    declined early (``继续 AstrBot 核心处理``) has no such state but is still the
    same conversation, and it is exactly where the model echoed a name we had
    written into the history.  ``channel`` is kept so the mark survives even when
    a later hook replaces the request.
    """
    marker = {"channel": channel, "event_ref": event_ref}
    setter = getattr(event, "set_extra", None)
    if callable(setter):
        try:
            setter(MANAGED_TURN_EXTRA, marker)
        except Exception:
            return marker
    return marker


def managed_turn(event) -> dict | None:
    """The composition-owned turn marker, if this event carries one."""
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return None
    try:
        marker = getter(MANAGED_TURN_EXTRA)
    except Exception:
        return None
    return marker if isinstance(marker, dict) else None


def is_managed_request(event) -> bool:
    """Whether this agent run belongs to a conversation this plugin serves."""
    try:
        from .core_reply import CORE_REPLY_CONTEXT_EXTRA
        if isinstance(event.get_extra(CORE_REPLY_CONTEXT_EXTRA), dict):
            return True
    except Exception:
        pass
    return managed_turn(event) is not None


_MISSING = object()


def declared_tool_names(request) -> frozenset[str] | None:
    """Every tool name this request declares, or None when it cannot be read.

    An explicit ``func_tool is None`` means "this request declares nothing" and
    answers with an empty set.  A request that does not carry the attribute at all
    is a different thing -- it comes from a caller whose shape we do not know --
    and answers None, because rewriting history against an unreadable declaration
    is how a working turn would be broken.
    """
    if request is None:
        return None
    tool_set = getattr(request, "func_tool", _MISSING)
    if tool_set is _MISSING:
        return None
    if tool_set is None:
        return frozenset()
    names: set[str] = set()
    getter = getattr(tool_set, "names", None)
    if callable(getter):
        try:
            names.update(str(name) for name in getter() if isinstance(name, str))
        except Exception:
            return None
    for tool in getattr(tool_set, "tools", None) or ():
        name = getattr(tool, "name", None)
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def turn_identity(event) -> tuple[str, str]:
    """``(turn_id, event_ref)`` for logs, from V2 state when the turn has any."""
    marker = managed_turn(event) or {}
    turn, reference = "-", str(marker.get("event_ref") or "-")
    try:
        from .core_reply import CORE_REPLY_CONTEXT_EXTRA
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    except Exception:
        core = None
    prepared = core.get("prepared") if isinstance(core, dict) else None
    if isinstance(prepared, Mapping):
        candidate = prepared.get("turn_id")
        if isinstance(candidate, str) and candidate:
            turn = candidate
        reference = str(prepared.get("event_ref") or reference)
    return turn, reference


def summarize_names(names) -> str:
    """``count:n1,n2,…`` -- a summary a log reader can compare with a manifest."""
    ordered = sorted(names)
    head = ",".join(ordered[:SUMMARY_LIMIT])
    suffix = ",…" if len(ordered) > SUMMARY_LIMIT else ""
    return f"{len(ordered)}:{head or '-'}{suffix}"


class _RoundGap:
    """The tool calls of one round that this request cannot execute."""

    __slots__ = ("requests", "available", "state", "event")

    def __init__(self, event, state, requests, available):
        self.event, self.state = event, state
        self.requests, self.available = requests, available

    def fill(self, blocks) -> int:
        """Answer every still-unanswered call so the round keeps a legal history."""
        if blocks is None:
            return 0
        answered = {getattr(block, "tool_call_id", None) for block in blocks}
        added = 0
        for call_id, name in self.requests:
            if not call_id or call_id in answered:
                continue
            blocks.append(_tool_result(call_id, self._message(name)))
            answered.add(call_id)
            added += 1
        if added:
            self.state["filled"] = self.state.get("filled", 0) + added
            turn, reference = turn_identity(self.event)
            logger.warning(
                "[CompanionV2] 未挂载工具调用已补上工具结果 reason=tool_not_in_request "
                "filled=%s available=%s turn=%s event_ref=%s",
                added, summarize_names(self.available), turn, reference)
        return added

    def build(self):
        blocks = []
        self.fill(blocks)
        return blocks

    def _message(self, name):
        """Answer in the host's own words so the model reacts the way it always does."""
        available = ",".join(sorted(self.available)) or "none"
        return f"error: Tool {name} not found. Available tools are: {available}"


def _tool_result(call_id: str, text: str):
    from astrbot.core.agent.message import ToolCallMessageSegment
    return ToolCallMessageSegment(role="tool", tool_call_id=call_id, content=text)


def _blocks_result(blocks):
    """The host reads ``kind`` and ``tool_call_result_blocks``; nothing else."""
    return SimpleNamespace(kind="tool_call_result_blocks", tool_call_result_blocks=blocks,
                           message_chain=None, cached_image=None)


def _round_text(response) -> bool:
    text = getattr(response, "completion_text", None)
    if isinstance(text, str) and text.strip():
        return True
    return bool(getattr(response, "result_chain", None))


def observe_round(event, state, request, response):
    """Report this round's undeclared tool names once, and return what to fill."""
    names = [name for name in (getattr(response, "tools_call_name", None) or ())
             if isinstance(name, str) and name]
    if not names:
        return None
    declared = declared_tool_names(request)
    if declared is None:
        return None
    unknown = list(dict.fromkeys(name for name in names if name not in declared))
    if not unknown:
        return None
    provider = getattr(getattr(state.get("runner"), "provider", None), "provider_config", {}) or {}
    provider_id = provider.get("id", "-") if isinstance(provider, Mapping) else "-"
    turn, reference = turn_identity(event)
    warned = state.setdefault("warned", {})
    for name in unknown:
        seen = warned.get(name, 0) + 1
        warned[name] = seen
        if seen > 1:
            continue
        logger.warning(
            "[CompanionV2] 模型请求了本轮未挂载的工具，已记录并继续 reason=tool_not_in_request "
            "tool=%s available=%s turn=%s event_ref=%s provider=%s round_text=%s",
            name, summarize_names(declared), turn, reference, provider_id,
            "yes" if _round_text(response) else "no")
    identifiers = list(getattr(response, "tools_call_ids", None) or ())
    requests = [(call_id, name) for name, call_id in zip(names, identifiers)
                if name in unknown and isinstance(call_id, str) and call_id]
    return _RoundGap(event, state, requests, declared)


def install_core_reply_tool_guard(event, run_context):
    """Watch this turn's tool rounds from the host's own dispatch point.

    Installed on top of the existing dispatch observer, so the guard sees a round
    before anything else looks at it.  Every step fails open: an unusable host
    shape returns None and the turn behaves exactly as it does without the guard.
    """
    try:
        if not is_managed_request(event):
            return None
        from .core_reply_tool_dispatch import active_core_runner
        runner = active_core_runner(event, run_context)
        if runner is None:
            return None
        existing = event.get_extra(CORE_TOOL_GUARD_EXTRA)
        if (isinstance(existing, dict) and existing.get("runner") is runner
                and not existing.get("restored")):
            return existing
        original = getattr(runner, HANDLE_FUNCTION_TOOLS, None)
        if not callable(original):
            return None
        local = getattr(runner, "__dict__", {})
        state = {"runner": runner, "run_context": run_context, "warned": {}, "filled": 0,
                 "previous": (HANDLE_FUNCTION_TOOLS in local, local.get(HANDLE_FUNCTION_TOOLS)),
                 "wrapper": None, "restored": False}

        async def guarded_tools(*args, **kwargs):
            try:
                gap = observe_round(event, state, _argument(args, kwargs, "req", 0),
                                    _argument(args, kwargs, "llm_response", 1))
            except Exception as error:
                # A detector that raises must never cost the round AstrBot is
                # already executing; report it and behave as if not installed.
                logger.warning("[CompanionV2] 工具缺席兜底判定失败 reason=detector_error error_type=%s",
                               type(error).__name__)
                gap = None
            carried = False
            try:
                async for item in original(*args, **kwargs):
                    if gap is not None and getattr(item, "kind", None) == "tool_call_result_blocks":
                        carried = True
                        try:
                            gap.fill(item.tool_call_result_blocks)
                        except Exception as error:
                            logger.warning(
                                "[CompanionV2] 工具结果补齐失败 reason=shape_changed error_type=%s",
                                type(error).__name__)
                    yield item
            except (GeneratorExit, asyncio.CancelledError):
                # The host stopped reading this round; the turn's own close hook
                # puts the runner back. Disarming here would leave later rounds of
                # the same turn unguarded.
                raise
            if gap is not None and not carried:
                # The host answered nothing for this round (its own early return
                # for a request without tools). Writing the results here keeps the
                # assistant tool call paired, which is what the next request needs.
                blocks = gap.build()
                if blocks:
                    yield _blocks_result(blocks)

        state["wrapper"] = guarded_tools
        try:
            setattr(runner, HANDLE_FUNCTION_TOOLS, guarded_tools)
        except Exception as error:
            logger.warning("[CompanionV2] 工具缺席兜底不可用 reason=install_failed error_type=%s",
                           type(error).__name__)
            return None
        event.set_extra(CORE_TOOL_GUARD_EXTRA, state)
        return state
    except Exception as error:
        logger.warning("[CompanionV2] 工具缺席兜底不可用 reason=install_failed error_type=%s",
                       type(error).__name__)
        return None


def _argument(args, kwargs, name, index):
    if len(args) > index:
        return args[index]
    return kwargs.get(name)


def close_core_reply_tool_guard(event, run_context=None):
    """Put the runner's own dispatch method back; safe to call more than once."""
    getter = getattr(event, "get_extra", None)
    state = getter(CORE_TOOL_GUARD_EXTRA) if callable(getter) else None
    if not isinstance(state, dict) or state.get("restored"):
        return
    if run_context is not None and state.get("run_context") is not run_context:
        return
    state["restored"] = True
    runner, wrapper = state.get("runner"), state.get("wrapper")
    had_local, previous = state.get("previous") or (False, None)
    try:
        if getattr(runner, HANDLE_FUNCTION_TOOLS, None) is wrapper:
            if had_local:
                setattr(runner, HANDLE_FUNCTION_TOOLS, previous)
            else:
                delattr(runner, HANDLE_FUNCTION_TOOLS)
    except Exception:
        pass
    if state.get("filled") or state.get("warned"):
        logger.info("[CompanionV2] 工具缺席兜底结束 missing_tools=%s filled=%s",
                    ",".join(sorted(state.get("warned") or ())) or "-", state.get("filled", 0))


def undeclared_companion_calls(messages, declared) -> list[dict[str, Any]]:
    """Assistant tool calls to this plugin that ``declared`` does not offer.

    ``declared is None`` means the request's declarations could not be read, which
    is not the same as "declares nothing": nothing is reported then.
    """
    if declared is None:
        return []
    found = []
    for message in messages or ():
        if _value(message, "role") != "assistant":
            continue
        for call in _value(message, "tool_calls") or ():
            name = _call_name(call)
            if not isinstance(name, str) or not name.startswith(COMPANION_TOOL_PREFIX):
                continue
            if name in declared:
                continue
            found.append({"name": name, "id": _call_id(call), "message": message})
    return found


def drop_undeclared_companion_calls(messages, declared) -> dict[str, Any]:
    """Remove the companion tool calls this request cannot execute, with results.

    Both sides go together so the pairing the caller just proved stays legal: a
    call without its result kills the next request, and a result without its call
    is rejected as well.  Declared companion tools are untouched, and no other
    plugin's tool calls are ever rewritten here -- only our own namespace, which
    is what this plugin wrote into the history in the first place.
    """
    if not isinstance(messages, list) or not messages:
        return {}
    dropped = undeclared_companion_calls(messages, declared)
    if not dropped:
        return {}
    names = sorted({item["name"] for item in dropped})
    ids = {item["id"] for item in dropped if isinstance(item.get("id"), str) and item["id"]}
    by_message: dict[int, set[str]] = {}
    for item in dropped:
        by_message.setdefault(id(item["message"]), set()).add(item["name"])
    survivors, results, emptied = [], 0, 0
    for message in messages:
        if _value(message, "role") == "tool" and _value(message, "tool_call_id") in ids:
            results += 1
            continue
        wanted = by_message.get(id(message))
        if wanted:
            calls = list(_value(message, "tool_calls") or ())
            kept = [call for call in calls if _call_name(call) not in wanted]
            if len(kept) != len(calls):
                if kept:
                    _set_value(message, "tool_calls", kept)
                else:
                    _drop_value(message, "tool_calls")
                    if not _has_content(message):
                        # The message existed only to carry the call; keeping an
                        # empty assistant turn would make the model answer its own
                        # silence.
                        emptied += 1
                        continue
        survivors.append(message)
    messages[:] = survivors
    return {"names": names, "calls": len(dropped), "results": results, "emptied": emptied}


def _has_content(message) -> bool:
    content = _value(message, "content")
    if isinstance(content, str):
        return bool(content.strip())
    return bool(content)


def _set_value(message, key, value) -> bool:
    if isinstance(message, Mapping):
        if not isinstance(message, dict):
            return False
        message[key] = value
        return True
    try:
        setattr(message, key, value)
    except Exception:
        return False
    return True


def _drop_value(message, key) -> bool:
    if isinstance(message, Mapping):
        if not isinstance(message, dict) or key not in message:
            return False
        message.pop(key, None)
        return True
    try:
        setattr(message, key, None)
    except Exception:
        return False
    return True


def _value(item, key, default=None):
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _call_name(call):
    name = _value(_value(call, "function"), "name")
    return name if isinstance(name, str) else None


def _call_id(call):
    identifier = _value(call, "id")
    return identifier if isinstance(identifier, str) else None
