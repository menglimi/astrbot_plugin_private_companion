"""Adapt a governed V2 reply to AstrBot's active message pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import is_dataclass
from inspect import isawaitable
import json
from typing import Any

from companion_v2.execution.models import TaskContext, TaskError, encode, require
from companion_v2.foundation.outcome import Outcome


CORE_REPLY_CONTEXT_EXTRA = "_companion_v2_core_reply_context"


Authorize = Callable[[Any], Awaitable[Any]]
OnDenied = Callable[[Any, BaseException], Awaitable[Any] | Any]
OnSent = Callable[[Any, str], Any]
OnDelivery = Callable[[str], Any]
PrepareSend = Callable[[Any, Any, Callable[..., Awaitable[Any]], int, int], Awaitable[Any] | Any]


def _require_authorization_result(result: Any) -> None:
    if not isinstance(result, Outcome):
        raise TypeError("core reply authorization must return an Outcome")
    require(result)


def restore_core_reply_prepared(prepared: Mapping) -> Mapping:
    """Reconstruct captured values after reload without choosing new settings."""
    from companion_v2.conversation.voice_reply import VoiceReplyPolicy

    restored = {}
    context = prepared.get("context")
    if is_dataclass(context) and not isinstance(context, TaskContext):
        current = TaskContext.restore(json.loads(encode(context)))
        if current.key != context.key:
            raise TaskError("core_reply_context_changed", "reload changed the captured reply context")
        restored["context"] = current
    for name in ("voice_reply_policy", "configured_voice_reply_policy"):
        policy = prepared.get(name)
        if is_dataclass(policy) and not isinstance(policy, VoiceReplyPolicy):
            restored[name] = VoiceReplyPolicy(**json.loads(encode(policy)))
    return {**prepared, **restored} if restored else prepared


def is_core_sendable_message(message: Any) -> bool:
    """Distinguish reply content from empty streams and agent metadata."""
    if isinstance(message, str):
        return bool(message.strip())
    if getattr(message, "type", None) in {"tool_call", "tool_result", "agent_stats", "reasoning", "break"}:
        return False
    chain = getattr(message, "chain", None)
    if isinstance(chain, (list, tuple)):
        return any(bool(getattr(component, "text", "").strip())
                   if isinstance(getattr(component, "text", None), str) else True
                   for component in chain)
    return bool(_plain_text(message).strip())


def _plain_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if getattr(message, "type", None) in {"tool_call", "tool_result", "agent_stats", "reasoning", "break"}:
        return ""
    getter = getattr(message, "get_plain_text", None)
    return getter() if callable(getter) else ""


def sent_core_text(message: Any) -> str:
    """Observe the spoken text of native TTS as well as delivered plain text."""
    text = _plain_text(message)
    chain = getattr(message, "chain", ())
    spoken = [text for component in chain
              if getattr(getattr(component, "type", None), "name", None) == "Record"
              for text in (getattr(component, "_companion_v2_spoken_text", None),
                           getattr(component, "text", None))
              if isinstance(text, str) and text.strip()][:1]
    # Native dual output includes the same body as Plain and Record. Keep one
    # observation for it, while retaining genuinely additional spoken content.
    for body in dict.fromkeys(spoken):
        if body.strip() not in text:
            text = text + ("\n" if text else "") + body
    return text


def is_core_reply_stream(event: Any) -> bool:
    get_result = getattr(event, "get_result", None)
    result = get_result() if callable(get_result) else None
    return getattr(getattr(result, "result_content_type", None), "name", None) == "STREAMING_RESULT"


def _is_core_reply_result(event: Any) -> bool:
    getter = getattr(event, "get_result", None)
    if not callable(getter):
        return True
    result = getter()
    if getattr(getattr(result, "result_content_type", None), "name", None) in {"LLM_RESULT", "STREAMING_RESULT"}:
        return True
    check = getattr(result, "is_llm_result", None)
    return bool(check()) if callable(check) else False


def prepare_core_reply_event(
    event: Any,
    prepared: Any,
    *,
    authorize: Authorize,
    on_denied: OnDenied | None = None,
    on_sent: OnSent | None = None,
    on_delivery: OnDelivery | None = None,
    on_reply_end: Callable[[], Any] | None = None,
    prepare_send: PrepareSend | None = None,
    media_submit: Callable | None = None,
    owner: Any = None,
) -> None:
    """Attach reply authorization and guard final delivery on this event."""
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict) or "_original_send" not in state:
        state = {
            "_original_send": event.send,
            "_original_send_streaming": getattr(event, "send_streaming", None),
        }

        async def authorize_delivery() -> None:
            try:
                result = await state["authorize"](state["prepared"])
                # Retained wrappers outlive their module during hot reload.
                # Check the current owner's Outcome class, just as we call
                # that owner's authorization callback through this state.
                state["_require_authorization_result"](result)
            except BaseException as error:
                callback = state.get("on_denied")
                if not state.get("denial_notified"):
                    state["denial_notified"] = True
                    if callback is not None:
                        try:
                            callback_result = callback(state["prepared"], error)
                            if isawaitable(callback_result):
                                await callback_result
                        except BaseException:
                            pass
                notify_delivery("denied")
                raise

        async def authorized_send(message: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return await guarded_send(message, *args, **kwargs)
            except BaseException:
                finish_core_reply_presence(event, force=True)
                raise

        async def guarded_send(message: Any, *args: Any, **kwargs: Any) -> Any:
            if getattr(message, "type", None) in {"tool_call", "tool_result", "agent_stats", "reasoning", "break"}:
                return await state["_original_send"](message, *args, **kwargs)
            if not state.get("delivery_guard_active"):
                from .core_reply_presentation import strip_internal_expression_message
                message = strip_internal_expression_message(message)
                if message is None:
                    return None
                return await state["_original_send"](message, *args, **kwargs)
            if state.get("group_participation", {}).get("mode") == "skip":
                return None
            if state["send_unknown"]:
                raise TaskError("core_reply_delivery_unknown", "the platform send outcome is unknown; do not replay this reply")
            from .core_reply_reaction_delivery import capture_core_reaction_intent
            capture_core_reaction_intent(event, message)
            from .core_reply_voice import capture_core_voice_message
            message = capture_core_voice_message(event, message)
            from .core_reply_presentation import present_core_reply_message
            voice_source = getattr(message, "_companion_v2_voice_source", _plain_text(message))
            message = present_core_reply_message(event, message, from_stream=True)
            if message is None:
                return None
            if state.get("_voice_authored") and hasattr(message, "chain"):
                object.__setattr__(message, "_companion_v2_voice_source", voice_source)
            callback = state.get("prepare_send")
            if callback is not None:
                message = callback(state["prepared"], message, authorize_delivery,
                                   state.get("voice_segment_index", 0),
                                   state.get("voice_segment_count", 1))
                if isawaitable(message):
                    message = await message
            from .core_reply_media_delivery import native_reply_messages, native_wire_message
            from .core_reply_voice import commit_pending_voice_claim, release_pending_voice_claim
            result = None
            for part in native_reply_messages(event, message):
                try:
                    await authorize_delivery()
                    wire = await native_wire_message(event, part)
                except BaseException:
                    release_pending_voice_claim(event)
                    raise
                state["send_attempts"] += 1
                notify_delivery("started")
                from .core_reply_behavior import observe_core_behavior_delivery
                observe_core_behavior_delivery(event, part, "started")
                try:
                    result = await state["_original_send"](wire, *args, **kwargs)
                except BaseException:
                    state["send_unknown"] += 1
                    notify_delivery("unknown")
                    observe_core_behavior_delivery(event, part, "unknown")
                    raise
                if result is False:
                    release_pending_voice_claim(event)
                    state["send_failed"] += 1
                    notify_delivery("failed")
                    observe_core_behavior_delivery(event, part, "failed")
                    raise TaskError("core_reply_platform_unavailable", "the platform did not accept this reply")
                state["send_returns"] += 1
                from astrbot.core.message.components import Record
                if any(isinstance(component, Record) for component in getattr(part, "chain", ())):
                    commit_pending_voice_claim(event)
                notify_delivery("returned")
                observe_core_behavior_delivery(event, part, "returned")
                notify_sent(sent_core_text(part))
                try:
                    from .core_reply_reaction_delivery import observe_core_reaction_returned
                    observe_core_reaction_returned(event, part, submit=state.get("media_submit"))
                except Exception:
                    pass
            return result

        def notify_delivery(stage: str) -> None:
            callback = state.get("on_delivery")
            if callback is not None:
                try:
                    callback(stage)
                except Exception:
                    pass
            if stage in {"denied", "unknown", "failed"}:
                finish_core_reply_presence(event, force=True)

        def notify_sent(text: str) -> None:
            callback = state.get("on_sent")
            if callback is not None and text:
                try:
                    # The callback schedules its own background work. The core
                    # send never awaits storage, extraction or diagnostics.
                    callback(state["prepared"], text)
                except Exception:
                    pass

        async def authorized_streaming(*args: Any, **kwargs: Any) -> Any:
            try:
                return await guarded_streaming(*args, **kwargs)
            except BaseException:
                finish_core_reply_presence(event, force=True)
                raise

        async def guarded_streaming(*args: Any, **kwargs: Any) -> Any:
            # AstrBot skips result decorators for an active stream with no chain.
            # Its own streaming result therefore activates the send check here.
            if not state.get("delivery_guard_active") and not is_core_reply_stream(event):
                return await state["_original_send_streaming"](*args, **kwargs)
            if state.get("group_participation", {}).get("mode") == "skip":
                notify_delivery("finished")
                restore_core_reply_event(event)
                return None
            state["_presence_stream_active"] = True
            state["delivery_guard_active"] = True
            attempts_before = state["send_attempts"]
            returned_before = state["send_returns"]
            unknown_before = state["send_unknown"]
            chunks = []
            stream_started = False
            original_args = list(args)
            original_kwargs = dict(kwargs)
            stream = original_args[0] if original_args else original_kwargs.get("generator")
            from .core_reply_segmentation import send_group_stream, uses_managed_group_stream
            from .core_reply_presentation import (
                core_reply_presentation, present_core_reply_stream, presented_stream,
                strip_internal_expression_stream,
            )
            managed_group_stream = hasattr(stream, "__aiter__") and uses_managed_group_stream(event)
            presentation = core_reply_presentation(event)
            platform_getter = getattr(event, "get_platform_name", None)
            platform = (platform_getter() if callable(platform_getter)
                        else getattr(getattr(event, "platform_meta", None), "name", None))
            native_bubbles = presentation is not None and platform == "aiocqhttp"
            if stream is not None and hasattr(stream, "__aiter__"):
                stream = strip_internal_expression_stream(stream)
            prepared_stream = (presentation if not managed_group_stream and not native_bubbles
                               and hasattr(stream, "__aiter__") else None)
            if prepared_stream is not None:
                stream = present_core_reply_stream(event, stream)
            accounted_by_parts = managed_group_stream or native_bubbles
            state["_stream_send_accounted_by_parts"] = accounted_by_parts

            def start_stream() -> None:
                nonlocal stream_started
                if not stream_started:
                    stream_started = True
                    state["send_attempts"] += 1
                    notify_delivery("stream_started")

            if stream is not None and hasattr(stream, "__aiter__"):
                async def observed_stream():
                    async for chunk in stream:
                        if not accounted_by_parts and is_core_sendable_message(chunk):
                            start_stream()
                        chunks.append(_plain_text(chunk))
                        yield chunk
                if original_args:
                    original_args[0] = observed_stream()
                else:
                    original_kwargs["generator"] = observed_stream()
            try:
                await authorize_delivery()
                if not hasattr(stream, "__aiter__") and is_core_sendable_message(stream):
                    start_stream()
                try:
                    if managed_group_stream:
                        generator = original_args[0] if original_args else original_kwargs["generator"]
                        result = await send_group_stream(event, generator)
                    else:
                        with presented_stream(prepared_stream):
                            result = await state["_original_send_streaming"](*original_args, **original_kwargs)
                except BaseException:
                    if (stream_started and state["send_unknown"] == unknown_before
                            and not state.get("denial_notified")):
                        state["send_unknown"] += 1
                        notify_delivery("unknown")
                    raise
                if stream_started and state["send_returns"] == returned_before:
                    state["send_returns"] += 1
                    notify_delivery("returned")
                    notify_sent("".join(chunks))
                return result
            finally:
                # RespondStage returns directly for streaming, without its usual
                # after_message_sent hook. Do not leave wrappers on this event.
                if stream_started and state["send_attempts"] > attempts_before + 1:
                    # QQ implements streaming through event.send. Count its
                    # real send invocations once, rather than the outer stream.
                    state["send_attempts"] -= 1
                notify_delivery("finished")
                state["_presence_stream_active"] = False
                restore_core_reply_event(event)

        state["_send_wrapper"] = authorized_send
        state["_streaming_wrapper"] = authorized_streaming
        state["_authorize_delivery"] = authorize_delivery
        state["_notify_delivery"] = notify_delivery
        state["_notify_sent"] = notify_sent

    state.pop("_text_presentation", None)
    state.pop("_text_presentation_removed", None)
    old_task, old_callback = state.pop("_presence_owner_task", None), state.pop("_presence_task_done", None)
    if old_task is not None and old_callback is not None:
        old_task.remove_done_callback(old_callback)
    state.update(
        prepared=prepared,
        authorize=authorize,
        _require_authorization_result=_require_authorization_result,
        on_denied=on_denied,
        on_sent=on_sent,
        on_delivery=on_delivery,
        on_reply_end=on_reply_end,
        prepare_send=prepare_send,
        media_submit=media_submit,
        owner=owner,
        delivery_guard_active=False,
        denial_notified=False,
        send_attempts=0,
        send_returns=0,
        send_unknown=0,
        send_failed=0,
        _presence_finished=False,
        _presence_agent_running=False,
        _presence_stream_active=False,
    )
    event.set_extra(CORE_REPLY_CONTEXT_EXTRA, state)
    if on_reply_end is not None:
        # The native message task owns intake, the agent and RespondStage. Its
        # completion is a cleanup backstop for SDK paths with no final hook.
        task = asyncio.current_task()
        if task is not None:
            def reply_task_done(_task):
                if event.get_extra(CORE_REPLY_CONTEXT_EXTRA) is state:
                    finish_core_reply_presence(event, force=True)
            state["_presence_owner_task"], state["_presence_task_done"] = task, reply_task_done
            task.add_done_callback(reply_task_done)
    event.send = state["_send_wrapper"]
    if callable(state["_original_send_streaming"]):
        event.send_streaming = state["_streaming_wrapper"]
    from .core_reply_voice import prepare_core_reply_voice
    prepare_core_reply_voice(event)
    if isinstance(prepared, Mapping) and prepared.get("reaction_reply_enabled"):
        event.set_extra("enable_streaming", False)


def adopt_core_reply_event(
    event: Any,
    *,
    owner: Any,
    prepared: Any = None,
    authorize: Authorize,
    on_denied: OnDenied | None = None,
    on_sent: OnSent | None = None,
    on_delivery: OnDelivery | None = None,
    on_reply_end: Callable[[], Any] | None = None,
    prepare_send: PrepareSend | None = None,
    media_submit: Callable | None = None,
) -> bool:
    """Move an in-flight reply guard to the current plugin instance.

    The original send wrappers stay in place because they resolve callbacks
    through ``state`` on every call.  Replacing those callbacks lets a newly
    loaded host re-authenticate the same native event without replaying an
    already attempted platform send.
    """
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if (not isinstance(state, dict) or "_original_send" not in state
            or "_send_wrapper" not in state):
        return False
    if prepared is None:
        prepared = state.get("prepared")
    if not isinstance(prepared, Mapping):
        return False
    changed_owner = state.get("owner") is not owner
    if changed_owner:
        prepared = restore_core_reply_prepared(prepared)
        old_task, old_callback = state.pop("_presence_owner_task", None), state.pop("_presence_task_done", None)
        if old_task is not None and old_callback is not None:
            old_task.remove_done_callback(old_callback)
        # A previous host may have denied before reaching the platform.  A
        # clean authorization from the new host can safely recover that send;
        # unknown or already-attempted sends stay fenced by guarded_send.
        if not state.get("send_unknown") and not state.get("send_attempts"):
            state["denial_notified"] = False
            state["_presence_finished"] = False
            state.pop("_text_presentation", None)
            state.pop("_text_presentation_removed", None)
    state.update(
        prepared=prepared,
        owner=owner,
        authorize=authorize,
        _require_authorization_result=_require_authorization_result,
        on_denied=on_denied,
        on_sent=on_sent,
        on_delivery=on_delivery,
        on_reply_end=on_reply_end,
        prepare_send=prepare_send,
        media_submit=media_submit,
    )
    if changed_owner and on_reply_end is not None:
        task = asyncio.current_task()
        if task is not None:
            def reply_task_done(_task):
                if event.get_extra(CORE_REPLY_CONTEXT_EXTRA) is state:
                    finish_core_reply_presence(event, force=True)
            state["_presence_owner_task"], state["_presence_task_done"] = task, reply_task_done
            task.add_done_callback(reply_task_done)
    return True


def activate_core_reply_delivery_guard(event: Any) -> None:
    """Guard only AstrBot's final response, after result decorators have run."""
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(state, dict) and _is_core_reply_result(event):
        state["delivery_guard_active"] = True
        # A native agent can yield several results around tool calls. AstrBot's
        # after-send hook restores methods after each result, so re-arm here.
        if event.send == state["_original_send"]:
            event.send = state["_send_wrapper"]
        if getattr(event, "send_streaming", None) == state.get("_original_send_streaming"):
            if callable(state.get("_original_send_streaming")):
                event.send_streaming = state["_streaming_wrapper"]


def restore_core_reply_event(event: Any) -> None:
    """Restore native event send methods after AstrBot finishes its response."""
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict):
        return
    state["delivery_guard_active"] = False
    event.send = state["_original_send"]
    original_streaming = state.get("_original_send_streaming")
    if callable(original_streaming):
        event.send_streaming = original_streaming
    finish_core_reply_presence(event)


def finish_core_reply_presence(event: Any, *, force=False) -> None:
    """Close once, after the whole reply rather than an intermediate tool send."""
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict) or state.get("_presence_finished"):
        return
    if not force and (state.get("_presence_agent_running") or state.get("_presence_stream_active")):
        return
    state["_presence_finished"] = True
    task, done = state.pop("_presence_owner_task", None), state.pop("_presence_task_done", None)
    if task is not None and done is not None:
        task.remove_done_callback(done)
    callback = state.get("on_reply_end")
    if callback is not None:
        try:
            callback()
        except Exception:
            pass  # Optional presentation must not turn a returned send into an error.


def repair_tool_call_history(contexts: Any) -> int:
    """Drop tool-call batches whose tool results are no longer in the history.

    AstrBot keeps one shared context list; trimming, another plugin's history
    cleanup or an interrupted tool batch can leave an assistant message with
    ``tool_calls`` but no matching ``tool`` messages.  OpenAI-compatible
    providers reject that whole request with a 400, so remove the orphan batch
    (and its stray tool messages) while keeping the assistant's visible text.
    """
    if not isinstance(contexts, list):
        return 0
    output, removed, index = [], 0, 0
    while index < len(contexts):
        message = contexts[index]
        if not (isinstance(message, dict) and message.get("role") == "assistant"
                and message.get("tool_calls")):
            output.append(message)
            index += 1
            continue
        end = index + 1
        while (end < len(contexts) and isinstance(contexts[end], dict)
               and contexts[end].get("role") == "tool"):
            end += 1
        calls = [call for call in message["tool_calls"] if isinstance(call, dict)]
        answered = {str(part.get("tool_call_id")) for part in contexts[index + 1:end]
                    if isinstance(part, dict)}
        identifiers = [str(call.get("id")) for call in calls if call.get("id") is not None]
        if identifiers and set(identifiers) <= answered:
            output.append(message)
            output.extend(contexts[index + 1:end])
            index = end
            continue
        removed += 1
        if str(message.get("content") or "").strip():
            output.append({key: value for key, value in message.items() if key != "tool_calls"})
        index = end
    if removed:
        contexts[:] = output
    return removed


def inject_core_reply_prompt(event: Any, req: Any) -> None:
    """Append only turn-stable V2 guidance to the selected core request."""
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict):
        return

    prepared = state.get("prepared")
    system_prompt = getattr(req, "system_prompt", "") or ""
    # AstrBot already constructs the selected persona and its example dialogs.
    # The captured native instruction is only an authorization fingerprint.
    previous = state.pop("_stable_prompt_instruction", None)
    if isinstance(previous, str) and previous and previous in system_prompt:
        system_prompt = system_prompt.replace(previous, "", 1).rstrip()
    instruction = (prepared.get("stable_prompt_instruction") if isinstance(prepared, Mapping)
                   else getattr(prepared, "stable_prompt_instruction", None))
    if isinstance(instruction, str) and instruction:
        if instruction not in system_prompt:
            system_prompt = (f"{system_prompt.rstrip()}\n\n{instruction}"
                             if system_prompt.strip() else instruction)
        state["_stable_prompt_instruction"] = instruction
    req.system_prompt = system_prompt
    from .core_reply_voice import prepare_core_reply_voice, voice_prompt_instruction
    voice_instruction = voice_prompt_instruction(
        prepared.get("voice_reply_policy") if isinstance(prepared, Mapping) else None,
        prepared.get("voice_provider_instruction", "") if isinstance(prepared, Mapping) else "")
    previous_voice = state.pop("_voice_prompt_instruction", None)
    if previous_voice and previous_voice in req.system_prompt:
        req.system_prompt = req.system_prompt.replace(previous_voice, "", 1).rstrip()
    if voice_instruction:
        req.system_prompt = (req.system_prompt.rstrip() + "\n\n" + voice_instruction).lstrip()
        state["_voice_prompt_instruction"] = voice_instruction
    prepare_core_reply_voice(event)
