"""Delegate native media sends through a context local to one admitted event."""

from copy import copy, deepcopy

from astrbot.core.agent.tool import ToolSet
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.tools.message_tools import SendMessageToUserTool

from companion_v2.execution.models import TaskError, digest
from .core_reply import CORE_REPLY_CONTEXT_EXTRA, sent_core_text
from .core_reply_presentation import present_core_reply_message
from .core_reply_media_delivery import native_reply_messages, native_wire_message


class _CurrentReplyContext:
    def __init__(self, original, tool, arguments):
        self._original, self._tool, self._arguments = original, tool, arguments
        self.error = None

    def __getattr__(self, name):
        return getattr(self._original, name)

    async def send_message(self, session, message_chain):
        try:
            return await self._send_message(session, message_chain)
        except BaseException as error:
            self.error = error
            raise

    async def _send_message(self, session, message_chain):
        tool, state = self._tool, self._tool._state
        tool.assert_current()
        if state.get("group_participation", {}).get("mode") == "skip":
            raise TaskError("group_participation_skipped", "this group turn chose to keep listening")
        if str(session) != tool._origin:
            raise TaskError("core_reply_tool_target_changed", "native reply tools can send only to their admitted session")
        chain = getattr(message_chain, "chain", ())
        image_parts = [part for part in chain if isinstance(part, Image)]
        image_only = bool(image_parts)
        if image_only:
            derive = getattr(message_chain, "derive", None)
            message_chain = derive(image_parts) if callable(derive) else MessageChain(chain=image_parts)
        else:
            from .core_reply_reaction_delivery import capture_core_reaction_intent
            capture_core_reaction_intent(tool._event, message_chain)
            from .core_reply_voice_authoring import author_core_voice_message
            await author_core_voice_message(tool._event, message_chain)
            from .core_reply_voice import capture_core_voice_message
            message_chain = capture_core_voice_message(tool._event, message_chain)
        voice_source = message_chain.get_plain_text()
        message_chain = present_core_reply_message(tool._event, message_chain)
        if message_chain is None:
            raise TaskError("core_reply_text_removed", "the configured text rules removed all message content; nothing was sent")
        if state.get("_voice_authored") and not image_only:
            object.__setattr__(message_chain, "_companion_v2_voice_source", voice_source)
        try:
            await state["_authorize_delivery"]()
        except BaseException:
            try:
                from .core_reply_tracking import observe_core_reply_tool_delivery
                observe_core_reply_tool_delivery(tool._event, "denied")
            except Exception:
                pass
            raise
        key = digest([tool._origin, self._arguments])
        attempts = state.setdefault("_native_send_outcomes", {})
        if state["send_unknown"] or attempts.get(key) in {"started", "unknown", "partial_returned"}:
            raise TaskError("core_reply_delivery_unknown", "the previous native send outcome is unknown; do not replay it")
        callback = state.get("prepare_send")
        if callable(callback) and not image_only:
            from inspect import isawaitable
            message_chain = callback(state["prepared"], message_chain, state["_authorize_delivery"], 0, 1)
            if isawaitable(message_chain):
                message_chain = await message_chain
        from .core_reply_voice import commit_pending_voice_claim, release_pending_voice_claim
        returned = 0
        # native_reply_messages can return an empty tuple now that a quote or @
        # with no body is dropped. This value is what AstrBot's tool turns into
        # its own reply, and its `if not sent` branch reads a falsy one as
        # "failed to find platform for session ...": consuming the decoration is
        # a no-op success, not a platform failure, so the loop starts from a
        # truthy result and every real send overwrites it.
        result = True
        for part in native_reply_messages(tool._event, message_chain):
            try:
                await state["_authorize_delivery"]()
                wire = await native_wire_message(tool._event, part)
            except BaseException:
                release_pending_voice_claim(tool._event)
                raise
            attempts[key] = "started"
            state["send_attempts"] += 1
            tool.observe("started")
            from .core_reply_behavior import observe_core_behavior_delivery
            observe_core_behavior_delivery(tool._event, part, "started")
            try:
                result = await self._original.send_message(session, wire)
            except BaseException:
                attempts[key] = "unknown"
                state["send_unknown"] += 1
                tool.observe("unknown")
                observe_core_behavior_delivery(tool._event, part, "unknown")
                tool.observe("finished")
                raise
            if result is False:
                release_pending_voice_claim(tool._event)
                attempts[key] = "partial_returned" if returned else "failed"
                state["send_failed"] += 1
                tool.observe("failed")
                observe_core_behavior_delivery(tool._event, part, "failed")
                tool.observe("finished")
                raise TaskError("core_reply_platform_unavailable", "AstrBot did not find a platform for this session; nothing was sent")
            state["send_returns"] += 1
            returned += 1
            from astrbot.core.message.components import Record
            if any(isinstance(component, Record) for component in getattr(part, "chain", ())):
                commit_pending_voice_claim(tool._event)
            tool.observe("returned", result=result)
            observe_core_behavior_delivery(tool._event, part, "returned")
            state["_notify_sent"](sent_core_text(part))
            _remember_sent_text(tool._event, part)
            try:
                from .core_reply_reaction_delivery import observe_core_reaction_returned
                observe_core_reaction_returned(tool._event, part, submit=tool._submit)
            except Exception:
                pass
        attempts[key] = "returned"
        # AstrBot also registers the raw tool text after this call. Keep the
        # presented body so a matching final result remains deduplicated.
        _remember_sent_text(tool._event, message_chain)
        try:
            from .core_reply_reactions import observe_core_media_send_returned
            observe_core_media_send_returned(tool._event, message_chain, submit=tool._submit)
        except Exception:
            pass
        tool.observe("finished")
        return result


def _remember_sent_text(event, message):
    text = message.get_plain_text().strip()
    if text:
        key = "_send_message_to_user_current_session_plain_texts"
        texts = event.get_extra(key, [])
        texts = list(texts) if isinstance(texts, list) else []
        if text not in texts:
            texts.append(text)
            event.set_extra(key, texts)


class CurrentReplySendTool(SendMessageToUserTool):
    """Preserve AstrBot component building, media resolution and deduplication."""

    def __init__(self, original, event, state, submit):
        super().__init__(name=original.name, description=original.description,
                         parameters=deepcopy(original.parameters), active=original.active,
                         handler_module_path=original.handler_module_path)
        self._original, self._event, self._state = original, event, state
        self._prepared, self._origin, self._submit = state["prepared"], event.unified_msg_origin, submit

    def assert_current(self):
        if (self._event.get_extra(CORE_REPLY_CONTEXT_EXTRA) is not self._state
                or self._state.get("prepared") is not self._prepared
                or self._event.unified_msg_origin != self._origin):
            raise TaskError("core_reply_tool_event_changed", "the admitted native reply event changed")

    def observe(self, stage, result=None):
        self._state["_notify_delivery"](stage)
        if stage != "finished":
            try:
                from .core_reply_tracking import observe_core_reply_tool_delivery
                observe_core_reply_tool_delivery(self._event, stage, result=result)
            except Exception:
                pass

    async def call(self, context, **kwargs):
        self.assert_current()
        if getattr(context.context, "event", None) is not self._event:
            raise TaskError("core_reply_tool_event_mismatch", "native send belongs to another event")
        selected = kwargs.get("session") or self._origin
        if selected != self._origin:
            try:
                target = MessageSession.from_str(selected) if isinstance(selected, str) else selected
            except Exception:
                raise TaskError("core_reply_tool_target_changed", "native reply tools require the current session") from None
            if str(target) != self._origin:
                raise TaskError("core_reply_tool_target_changed", "native reply tools require the current session")
        # Copy only the two per-call context objects. Shared Context and tool
        # instances remain untouched, including during concurrent tool calls.
        local = copy(context)
        local.context = copy(context.context)
        sender = _CurrentReplyContext(context.context.context, self, deepcopy(kwargs.get("messages")))
        local.context.context = sender
        result = await self._original.call(local, **kwargs)
        # Newer SDK tools turn send exceptions into strings. Keep the original
        # authority/unknown-outcome error authoritative across SDK versions.
        if sender.error is not None:
            raise sender.error
        return result


def inject_core_reply_send_tool(event, req, *, submit=None):
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    tools = getattr(getattr(req, "func_tool", None), "tools", ())
    if not isinstance(state, dict) or not tools:
        return False
    replacements = []
    for tool in tools:
        original = tool._original if isinstance(tool, CurrentReplySendTool) else tool
        replacements.append(CurrentReplySendTool(original, event, state, submit)
                            if isinstance(original, SendMessageToUserTool) and original.handler is None else tool)
    if any(new is not old for new, old in zip(replacements, tools)):
        req.func_tool = ToolSet(tools=replacements)
        return True
    return False
