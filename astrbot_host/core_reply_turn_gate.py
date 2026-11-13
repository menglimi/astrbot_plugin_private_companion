"""Let a turn say one thing, even when a tool round writes prose first.

AstrBot delivers a model round's text *before* it runs that round's tools, so a
turn that calls a tool and then answers says the tool-step prose and then the
answer.  The gate watches each round's model response stream:

* a round the host will dispatch a tool for -- the host's own predicate, the
  normalized tool call names it checks before running tools -- wrote a draft: its
  text is withheld from that round, streamed fragments included, and kept as the
  turn's draft candidate;
* a round without tool calls is the answer: its text is delivered unchanged and
  the draft is dropped, even when the two are word for word the same;
* a turn that ends without any text still delivers the retained draft once,
  unless the group participation decision explicitly chose to keep listening.

Withheld text goes back on the response object before the SDK dispatches the tool,
so the assistant history and the tool call IDs and arguments keep exactly what the
model said. A listening decision removes only fully withheld prose from this
turn's tool messages, keeping their calls, results and reasoning intact.
Every step fails open: when the gate cannot be installed, or a
decision raises, the round behaves exactly as it does without the gate.

The turn can also end *without* a final round: the step budget runs out, the user
stops the turn, the provider raises, or the provider answers with an error.  Each
of those exits releases the draft exactly once -- through the next round when one
exists, through the event when none does -- because a turn that wrote something
must never send nothing unless it chose to keep listening.

Why the response stream and not the yielded items: ``on_agent_begin`` -- the first
hook that carries the run context -- fires from inside the first ``step()`` call,
so a wrapper installed there can only see the second round on.  The response
stream is looked up after that hook, which is the one point that still sees the
round that writes the draft.

Known limitation: with true incremental streaming the downstream publishes every
fragment as it arrives, so a tool round whose prose differs from the answer still
sends two messages.  The gate only withholds text it can put back; it never
withholds fragments that already reached the reader.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

CORE_TURN_GATE_EXTRA = "_companion_v2_core_turn_gate"
RESPONSE_STREAM = "_iter_llm_responses_with_fallback"
STEP = "step"
# Only plain text is a draft.  A round that also carries a mention or a quote is
# shown as it is: losing the mention with the prose is worse than a second
# message, so the gate would rather not fix that round at all.
TEXT_COMPONENTS = frozenset({"Plain"})
DECORATION_COMPONENTS = frozenset({"At", "Reply"})

logger = logging.getLogger(__name__)


def install_core_reply_turn(event, run_context):
    """Attach the round gate to this turn's runner.  Returns None when unusable."""
    try:
        from .core_reply import CORE_REPLY_CONTEXT_EXTRA
        from .core_reply_tool_dispatch import CORE_TOOL_DISPATCH_EXTRA
        existing = turn_gate(event)
        if not isinstance(event.get_extra(CORE_REPLY_CONTEXT_EXTRA), dict):
            _finish_gate(existing)
            return None
        if not isinstance(event.get_extra(CORE_TOOL_DISPATCH_EXTRA), dict):
            # The dispatch observer owns the point where withheld text goes back
            # before the tool runs; without it the gate could only corrupt history.
            _finish_gate(existing)
            logger.warning("[CompanionV2] 工具轮文本闸门不可用 reason=no_dispatch_observer")
            return None
        runner = discover_runner(event, run_context)
        if runner is None:
            _finish_gate(existing)
            logger.warning("[CompanionV2] 工具轮文本闸门不可用 reason=hook_missing")
            return None
        if isinstance(existing, dict):
            if (not existing.get("closed") and existing.get("runner") is runner
                    and not _turn_over(existing)):
                return existing
            # The previous turn on this event ended without the runner reporting
            # done; drop its wrapper before this turn installs its own.
            _finish_gate(existing)
        source = getattr(runner, RESPONSE_STREAM, None)
        source_step = getattr(runner, STEP, None)
        if not callable(source) or not callable(getattr(runner, "_handle_function_tools", None)):
            logger.warning("[CompanionV2] 工具轮文本闸门不可用 reason=no_response_stream")
            return None
        gate = {"runner": runner, "run_context": run_context, "event": event, "methods": {},
                "hold_fragments": _hold_fragments(event, runner), "draft": None,
                "withheld_rounds": [],
                "emitted": False, "blank": None, "failed": False, "rounds_with_tools": 0,
                "deferred": 0, "released": 0, "restored": False, "closed": False,
                "had_tools": getattr(getattr(runner, "req", None), "func_tool", None) is not None}

        async def gated_responses(*args, **kwargs):
            try:
                async for response in _turn_round(gate, source(*args, **kwargs)):
                    yield response
            except (GeneratorExit, asyncio.CancelledError):
                # The SDK stops reading a round once it has the response it needs.
                # That is normal: keep the wrapper and the withheld text for the
                # tool dispatch that follows, and let the turn close out.
                raise
            except BaseException:
                # A provider that raises ends the turn here.  The draft is the
                # only thing the turn said, so it goes out before the error.
                await _send_draft(gate, "provider_error")
                _finish_gate(gate)
                raise
            finally:
                if _done(runner):
                    _finish_gate(gate)

        async def gated_step(*args, **kwargs):
            try:
                async for item in source_step(*args, **kwargs):
                    if getattr(item, "type", None) == "aborted":
                        # The abort response is not delivered as text; release the
                        # draft before it so the turn is not silent.
                        for extra in _draft_agent_responses(gate, "aborted"):
                            yield extra
                    yield item
            except (GeneratorExit, asyncio.CancelledError):
                raise
            except BaseException:
                await _send_draft(gate, "step_error")
                _finish_gate(gate)
                raise
            if _turn_over(gate):
                # The step budget ran out (or the runner finished) with no round
                # left to carry the draft: say it now, once.
                for extra in _draft_agent_responses(gate, "turn_end"):
                    yield extra
                _finish_gate(gate)

        local = getattr(runner, "__dict__", {})
        gate["methods"][RESPONSE_STREAM] = (RESPONSE_STREAM in local, local.get(RESPONSE_STREAM),
                                            gated_responses)
        if callable(source_step):
            gate["methods"][STEP] = (STEP in local, local.get(STEP), gated_step)
        try:
            setattr(runner, RESPONSE_STREAM, gated_responses)
            if callable(source_step):
                setattr(runner, STEP, gated_step)
        except Exception as error:
            _finish_gate(gate)
            logger.warning("[CompanionV2] 工具轮文本闸门不可用 reason=install_failed error_type=%s",
                           type(error).__name__)
            return None
        event.set_extra(CORE_TURN_GATE_EXTRA, gate)
        if not gate["hold_fragments"]:
            logger.info("[CompanionV2] 工具轮文本闸门仅拦截终响应 reason=not_buffered")
        return gate
    except Exception as error:
        logger.warning("[CompanionV2] 工具轮文本闸门不可用 reason=install_failed error_type=%s",
                       type(error).__name__)
        return None


def turn_gate(event):
    """The gate installed on this event, if any."""
    getter = getattr(event, "get_extra", None)
    state = getter(CORE_TURN_GATE_EXTRA) if callable(getter) else None
    return state if isinstance(state, dict) else None


def discover_runner(event, run_context):
    """Locate the runner that drives *this* turn, or None.

    The plugin already addresses the active runner this way for tool dispatch,
    call binding and telemetry.  A host that drops that entry leaves all of them
    inert, and the gate follows the same contract instead of guessing from object
    graphs and risking another session's runner.
    """
    from .core_reply_tool_dispatch import active_core_runner
    return active_core_runner(event, run_context)


def restore_core_turn_text(event):
    """Put withheld text back.  Safe to call at any time, including never withheld."""
    gate = turn_gate(event)
    if isinstance(gate, dict):
        _restore_blank(gate)


def _is_skipped(event):
    from .core_reply import CORE_REPLY_CONTEXT_EXTRA
    getter = getattr(event, "get_extra", None)
    core = getter(CORE_REPLY_CONTEXT_EXTRA) if callable(getter) else None
    return isinstance(core, dict) and core.get("group_participation", {}).get("mode") == "skip"


def _clear_response_text(response):
    # Detach first: completion_text setters can rewrite the attached chain.
    response.result_chain = None
    response.completion_text = ""


def _without_text(content):
    if isinstance(content, str):
        return None
    return [part for part in content or () if getattr(part, "type", None) != "text"] or None


def _tool_batches(runner):
    batches = getattr(getattr(runner, "req", None), "tool_calls_result", None)
    if batches is None:
        return ()
    return tuple(batches) if isinstance(batches, (list, tuple)) else (batches,)


def _bind_completed_tool_batches(gate):
    batches = _tool_batches(gate.get("runner"))
    for withheld in gate.get("withheld_rounds", ()):
        index = withheld.get("batch_index")
        if index is not None and index < len(batches) and "message" not in withheld:
            withheld["message"] = batches[index].tool_calls_info


def begin_core_reply_tool_batch(event, response):
    """Bind withheld prose to the batch AstrBot is actually about to execute.

    The host appends one ToolCallsResult after dispatch returns. Its request's
    batch list survives context compression, and tool_calls_info is also the
    object appended to run_context.messages. Response identity stays stable
    when skills_like replaces the selected call IDs with executable calls.
    """
    gate = turn_gate(event)
    if gate is None:
        return
    _bind_completed_tool_batches(gate)
    for withheld in gate.get("withheld_rounds", ()):
        if withheld["response"] is response:
            withheld["batch_index"] = len(_tool_batches(gate.get("runner")))
            return


def discard_skipped_core_reply(event):
    """Discard this turn's fully withheld prose after a valid listening choice.

    Calls and results stay paired. Existing messages and rounds that exposed
    text incrementally, media, mentions or quotes are never rewritten.
    """
    if not _is_skipped(event):
        return False
    gate = turn_gate(event)
    if gate is None:
        return True
    gate["draft"] = None
    pending = gate.get("blank")
    if pending:
        _clear_response_text(pending[0])
        gate["blank"] = None
    # AstrBot appends each tool_calls_info object to run_context.messages too;
    # updating that object changes both histories without indexing a list that
    # context compression can shorten or replace.
    _bind_completed_tool_batches(gate)
    for withheld in gate.get("withheld_rounds", ()):
        _clear_response_text(withheld["response"])
        message = withheld.get("message")
        if message is not None:
            message.content = _without_text(message.content)
    return True


async def close_core_reply_turn(event, response=None, *, release=False):
    """Close the turn's gate: never leave withheld text or a wrapper behind.

    ``release`` is set by the agent-done hook, the one exit that knows the turn is
    over.  A send hook also calls this to clean up, but a send can happen in the
    middle of a tool turn, so it must not release or close anything early.
    """
    discard_skipped_core_reply(event)
    gate = turn_gate(event)
    if not isinstance(gate, dict) or gate.get("closed"):
        return
    if release:
        if _should_release_on_close(gate, response):
            await _send_draft(gate, "turn_end")
        elif gate.get("draft"):
            # The final answer is being delivered; the draft is superseded.
            gate["draft"] = None
            logger.info("[CompanionV2] 工具轮正文被最终回答取代 reason=answer_wins")
    if _turn_over(gate):
        _finish_gate(gate)
        if gate.get("rounds_with_tools") or gate.get("released") or gate.get("failed"):
            logger.info("[CompanionV2] 工具轮文本闸门结束 rounds_with_tools=%s deferred=%s "
                        "released=%s emitted=%s failed=%s", gate.get("rounds_with_tools"),
                        gate.get("deferred"), gate.get("released"), gate.get("emitted"),
                        gate.get("failed"))
    else:
        _restore_blank(gate)


async def _turn_round(gate, source):
    """Decide what one model round may show, then yield exactly that."""
    fragments, final = [], None
    try:
        async for response in source:
            if getattr(response, "is_chunk", False):
                if _is_skipped(gate.get("event")):
                    continue
                if gate["hold_fragments"]:
                    fragments.append(response)
                else:
                    yield response
                continue
            if final is not None:
                # A provider that ends one round twice is malformed.  Keep the
                # last response -- the newest word -- and say so; never drop it
                # silently.
                logger.warning("[CompanionV2] 工具轮文本闸门同轮出现第二个终响应，保留最后一个 "
                               "reason=extra_final")
            final = response
        if final is None:
            # The stream ended without a final response (stop requested, provider
            # gone).  Show what that round already produced, as it does today.
            for response in (() if discard_skipped_core_reply(gate.get("event")) else fragments):
                yield response
            return
        try:
            pending = (_round_plan(gate, fragments, final)
                       if not gate.get("failed") or _is_skipped(gate.get("event")) else None)
        except Exception as error:
            # Fail open with everything this round produced -- duplicating is
            # recoverable, swallowing a reply is not.
            gate["failed"] = True
            _restore_blank(gate)
            logger.warning("[CompanionV2] 工具轮文本闸门判定失败 reason=detector_error error_type=%s",
                           type(error).__name__)
            pending = None
        if pending is None:
            # Fail open with everything this round still holds.  (Unwithheld
            # fragments already went out above, so the list is empty then.)
            pending = [*fragments, final]
        for response in pending:
            yield response
    finally:
        if _error_round(final):
            # An error response ends the turn without on_agent_done; close here so
            # the next turn on this event installs a fresh gate instead of
            # reusing this one.
            _finish_gate(gate)


def _round_plan(gate, fragments, final):
    """The responses this round may expose, after the draft rules are applied."""
    if discard_skipped_core_reply(gate.get("event")):
        _clear_response_text(final)
        return [final]
    kinds = set()
    for response in (*fragments, final):
        kinds |= _kinds(response)
    visible = bool(kinds - {"none"})
    if _tool_round(final):
        gate["rounds_with_tools"] += 1
        if "media" in kinds:
            # Media is a different delivery, never a repeat.
            if visible:
                gate["draft"], gate["emitted"] = None, True
            return [*fragments, final]
        if "decorated" in kinds:
            # A mention or a quote is part of what the turn said.  Withholding it
            # would drop the mention together with the prose, so show the round
            # as it is; a repeat is recoverable, a lost mention is not.
            if "text" in kinds:
                gate["draft"], gate["emitted"] = None, True
            return [*fragments, final]
        if "text" not in kinds:
            # A tool round without text says nothing that could become a draft.
            return [*fragments, final]
        withheld = {"fragments": fragments, "text": _text_of(final),
                    "chain": getattr(final, "result_chain", None)}
        if gate["hold_fragments"]:
            # Only the latest draft can be what the turn meant to say.
            gate["draft"], gate["deferred"] = withheld, gate["deferred"] + 1
        else:
            # These fragments already reached the reader; the turn's answer is
            # still the next round's, so nothing is left to release later.
            gate["draft"], gate["emitted"] = None, True
        if not _blank(gate, final):
            # The text could not be withheld, so this round is not a draft: show
            # everything it produced rather than hold a copy that is still live.
            gate["draft"], gate["emitted"] = None, True
            return [*fragments, final]
        if gate["hold_fragments"]:
            gate.setdefault("withheld_rounds", []).append({"response": final})
        return [final]
    if _error_round(final):
        return _error_plan(gate, fragments, final)
    if not visible and gate["draft"]:
        # The turn ends without text: the draft is the only thing left to say, so
        # say it once rather than not at all.
        released = gate["draft"]
        gate["draft"], gate["emitted"], gate["released"] = None, True, gate["released"] + 1
        logger.info("[CompanionV2] 工具轮正文已释放 reason=released_draft rounds=%s",
                    gate.get("rounds_with_tools"))
        return _draft_responses(released)
    if visible:
        # The answer wins over every draft, including an identical one.
        gate["draft"], gate["emitted"] = None, True
    return [*fragments, final]


def _error_plan(gate, fragments, final):
    """An error round still says the draft the turn already wrote.

    The error response is the round's final response, so the draft rides on it:
    the turn says what it wrote and then what went wrong, instead of dropping the
    draft because the round is not a tool round.
    """
    draft = gate.get("draft")
    if not draft:
        return [*fragments, final]
    gate["draft"], gate["emitted"], gate["released"] = None, True, gate["released"] + 1
    text = _draft_text(draft)
    if text:
        current = getattr(final, "completion_text", "") or ""
        final.completion_text = text + ("\n" + current if current else "")
    logger.info("[CompanionV2] 工具轮正文随错误释放 reason=error_round rounds=%s",
                gate.get("rounds_with_tools"))
    return [*fragments, final]


def _kinds(response):
    """What one response would put on the wire: text, decoration, media, nothing."""
    chain = getattr(response, "result_chain", None)
    components = list(getattr(chain, "chain", None) or ()) if chain is not None else []
    kinds = set()
    for part in components:
        name = getattr(getattr(part, "type", None), "name", None)
        if name in TEXT_COMPONENTS:
            if isinstance(getattr(part, "text", None), str) and part.text.strip():
                kinds.add("text")
        elif name in DECORATION_COMPONENTS:
            kinds.add("decorated")
        else:
            kinds.add("media")
    if not kinds and _text_of(response):
        kinds.add("text")
    return kinds or {"none"}


def _draft_text(draft):
    """The words a withheld draft would say, from its final text or its fragments."""
    text = draft.get("text") or ""
    if text:
        return text
    return "".join(_text_of(fragment) for fragment in draft.get("fragments") or ())


def _draft_responses(draft):
    """The responses that put a withheld draft back on the wire."""
    fragments = list(draft.get("fragments") or ())
    # A streaming round keeps its words in the fragments, so the release has to
    # carry them too: the turn said this, and the assistant history should say so.
    return [*fragments, _release_response(_draft_text(draft), draft.get("chain"))]


def _draft_agent_responses(gate, reason):
    """Turn-end release: the draft as agent responses the host will deliver."""
    if discard_skipped_core_reply(gate.get("event")):
        return []
    draft = gate.get("draft")
    if not draft:
        return []
    gate["draft"], gate["emitted"], gate["released"] = None, True, gate["released"] + 1
    logger.info("[CompanionV2] 工具轮正文已释放 reason=%s rounds=%s", reason,
                gate.get("rounds_with_tools"))
    return _agent_responses(draft, streaming=bool(getattr(gate.get("runner"), "streaming", False)))


def _agent_responses(draft, *, streaming):
    """The agent responses that carry a draft when no model round is left.

    ``run_agent`` delivers a non-streaming turn from the ``llm_result`` chain and
    a streaming one from ``streaming_delta`` chains; the other type is dropped.
    Exactly one carrier is emitted so the draft is said once, never twice.
    """
    from astrbot.core.agent.response import AgentResponse, AgentResponseData
    from astrbot.core.message.message_event_result import MessageChain
    fragments = list(draft.get("fragments") or ())
    if streaming:
        responses = [AgentResponse(type="streaming_delta",
                                   data=AgentResponseData(chain=chain))
                     for chain in _fragment_chains(fragments)]
        if responses:
            return responses
        text = _draft_text(draft)
        return [AgentResponse(type="streaming_delta",
                              data=AgentResponseData(chain=MessageChain().message(text)))] if text else []
    chain = draft.get("chain")
    text = _draft_text(draft)
    if chain is None or not getattr(chain, "chain", None):
        chain = MessageChain().message(text) if text else None
    if chain is None:
        return []
    return [AgentResponse(type="llm_result", data=AgentResponseData(chain=chain))]


def _fragment_chains(fragments):
    """The chains a held streaming draft already produced, in order."""
    from astrbot.core.message.message_event_result import MessageChain
    chains = []
    for fragment in fragments:
        chain = getattr(fragment, "result_chain", None)
        if chain is None:
            text = _text_of(fragment)
            if not text:
                continue
            chain = MessageChain().message(text)
        chains.append(chain)
    return chains


async def _send_draft(gate, reason):
    """Release the draft through the event when no round is left to carry it."""
    if discard_skipped_core_reply(gate.get("event")):
        return False
    draft = gate.get("draft")
    if not draft:
        return False
    gate["draft"], gate["emitted"], gate["released"] = None, True, gate["released"] + 1
    text = _draft_text(draft)
    logger.info("[CompanionV2] 工具轮正文已释放 reason=%s rounds=%s", reason,
                gate.get("rounds_with_tools"))
    if not text:
        return False
    event = gate.get("event")
    send = getattr(event, "send", None)
    if not callable(send):
        return False
    try:
        from astrbot.core.message.message_event_result import MessageChain
        chain = draft.get("chain")
        message = (chain if chain is not None and getattr(chain, "chain", None)
                   else MessageChain().message(text))
        await send(message)
    except Exception as error:
        logger.warning("[CompanionV2] 工具轮正文释放失败 reason=send_failed error_type=%s",
                       type(error).__name__)
        return False
    return True


def _release_response(text, chain):
    """A final response the SDK can finish a round with; it carries no tool call."""
    return SimpleNamespace(role="assistant", completion_text=text, result_chain=chain,
                           is_chunk=False, reasoning_content=None, reasoning_signature=None,
                           usage=None, tools_call_name=[], tools_call_args=[], tools_call_ids=[])


def _tool_round(response):
    """Whether the host will dispatch a tool for this round.

    This is the host's own predicate -- the normalized tool call names it checks
    before running tools -- so the gate cannot disagree with AstrBot about whether
    a round's text is a draft.  It is also provider neutral: every provider shape
    is normalized into these fields before this point, and an error response is
    never a tool round.
    """
    if _error_round(response):
        return False
    return bool(getattr(response, "tools_call_name", None))


def _error_round(response):
    return getattr(response, "role", None) == "err"


def _text_of(response):
    text = getattr(response, "completion_text", None)
    if isinstance(text, str) and text.strip():
        return text
    chain = getattr(response, "result_chain", None)
    getter = getattr(chain, "get_plain_text", None)
    plain = getter() if callable(getter) else ""
    return plain if isinstance(plain, str) else ""


def _blank(gate, response):
    """Withhold this response's text until the tool dispatch puts it back."""
    _restore_blank(gate)
    if gate.get("blank"):
        # The previous response could not be restored; do not withhold another.
        return False
    saved = (getattr(response, "completion_text", ""), getattr(response, "result_chain", None))
    # Record the restore point before mutating: a setter that raises halfway must
    # never leave the chain unrecoverable.
    gate["blank"] = (response, saved)
    try:
        # Detach the chain first: the text setter rewrites whatever chain is still
        # attached, which would otherwise empty the very chain saved above.
        response.result_chain = None
        response.completion_text = ""
    except Exception:
        try:
            response.result_chain = saved[1]
            response.completion_text = saved[0]
        except Exception:
            # Keep the restore point so a later restore can still try.
            return False
        gate["blank"] = None
        return False
    return True


def _restore_blank(gate):
    pending = gate.get("blank")
    if not pending:
        return
    response, (text, chain) = pending
    if _is_skipped(gate.get("event")):
        _clear_response_text(response)
        gate["blank"] = None
        return
    try:
        response.completion_text = text
        response.result_chain = chain
    except Exception:
        # Keep the restore point: a later attempt may still succeed.
        return
    gate["blank"] = None


def _finish_gate(gate):
    """Close the gate and put every rewritten object back."""
    if not isinstance(gate, dict):
        return
    discard_skipped_core_reply(gate.get("event"))
    gate["closed"] = True
    _restore_blank(gate)
    _restore_methods(gate)


def _restore_methods(gate):
    if gate.get("restored"):
        return
    gate["restored"] = True
    runner = gate.get("runner")
    for name, (had_local, old_local, wrapper) in (gate.get("methods") or {}).items():
        try:
            if getattr(runner, name, None) is wrapper:
                if had_local:
                    setattr(runner, name, old_local)
                else:
                    delattr(runner, name)
        except Exception:
            pass


def _hold_fragments(event, runner):
    """Whether this turn's fragments can be withheld without costing a stream.

    A non-streaming runner has no fragments to withhold at all, so holding is
    free.  A streaming runner is held only when the turn's text is collected
    downstream before sending; otherwise its fragments are already on their way to
    the reader and only the final response can be withheld.

    Known limitation: with true incremental streaming the downstream publishes
    every fragment as it arrives, so a tool round whose prose differs from the
    answer still sends two messages.  The gate only withholds text it can put
    back; it never withholds fragments that already reached the reader.
    """
    if not getattr(runner, "streaming", False):
        return True
    return _downstream_buffers(event)


def _downstream_buffers(event):
    """Whether the plugin's own delivery already collects a whole turn's text.

    A missing dependency, a changed shape or a broken detector is *unknown*, not
    "not buffered": the gate reports the concrete reason and then declines to
    withhold fragments, because an unjudgeable stream must never cost a reply.
    """
    try:
        from .core_reply_segmentation import uses_managed_group_stream
    except ImportError as error:
        logger.warning("[CompanionV2] 工具轮文本闸门无法判定下游缓冲 reason=dependency_missing "
                       "error_type=%s", type(error).__name__)
        return False
    try:
        managed = uses_managed_group_stream(event)
    except ImportError as error:
        logger.warning("[CompanionV2] 工具轮文本闸门无法判定下游缓冲 reason=dependency_missing "
                       "error_type=%s", type(error).__name__)
        return False
    except (AttributeError, TypeError, KeyError) as error:
        logger.warning("[CompanionV2] 工具轮文本闸门无法判定下游缓冲 reason=shape_changed "
                       "error_type=%s", type(error).__name__)
        return False
    except Exception as error:
        logger.warning("[CompanionV2] 工具轮文本闸门无法判定下游缓冲 reason=detector_error "
                       "error_type=%s", type(error).__name__)
        return False
    if managed:
        try:
            from .core_reply import CORE_REPLY_CONTEXT_EXTRA
            core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            state = (core.get("_group_segmentation") or core.get("_reply_segmentation")
                     if isinstance(core, dict) else None)
            policy = state.get("policy") if isinstance(state, dict) else None
            # send_group_stream sends one chain per turn unless the policy
            # publishes every fragment as it arrives.
            return not (getattr(policy, "enabled", False)
                        and getattr(policy, "streaming_mode", "") == "incremental")
        except (AttributeError, TypeError, KeyError) as error:
            logger.warning("[CompanionV2] 工具轮文本闸门无法判定下游缓冲 reason=shape_changed "
                           "error_type=%s", type(error).__name__)
            return False
        except Exception as error:
            logger.warning("[CompanionV2] 工具轮文本闸门无法判定下游缓冲 reason=detector_error "
                           "error_type=%s", type(error).__name__)
            return False
    getter = getattr(event, "get_extra", None)
    return callable(getter) and getter("enable_streaming") is False


def _should_release_on_close(gate, response):
    """Whether the turn ended without a round that could carry the draft."""
    if not gate.get("draft"):
        return False
    if _aborted(gate.get("runner")):
        return True
    if _error_round(response):
        return True
    if response is None:
        return False
    return not _text_of(response) and not getattr(response, "result_chain", None)


def _turn_over(gate):
    """Whether the turn has no step left that could carry the draft."""
    runner = gate.get("runner")
    if _done(runner) or _aborted(runner):
        return True
    if gate.get("had_tools") and getattr(getattr(runner, "req", None), "func_tool", None) is None:
        # run_agent removes the tool set before its forced final step. A model
        # may still return tool-call fields there, but no further step follows.
        return True
    return False


def _done(runner):
    check = getattr(runner, "done", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False


def _aborted(runner):
    check = getattr(runner, "was_aborted", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False
