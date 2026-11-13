"""Observe AstrBot's completed context reset without capturing a command as input."""

from contextvars import ContextVar
from functools import wraps

from companion_v2.conversation.reset import ConversationWindow, reset_window
from companion_v2.execution.models import digest, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind
from companion_v2.adapters.astrbot.group import group_event_route

from .group_settings import GroupTarget


SUCCESS_MARKER = "_clean_group_context_session"
OBSERVED_MARKER = "_companion_v2_context_reset_observed"


def install_native_reset_marker(native_context):
    """Bridge the native third-party runner's missing success marker in memory.

    Only the reset/new command's completed state-clearing operation marks its
    event. Permission refusals and failed clears never reach that point. The
    ContextVar keeps concurrent windows separate and is released in finally.
    """
    from astrbot.builtin_stars.builtin_commands.commands import conversation as native

    active = ContextVar("companion_v2_native_context_reset", default=None)
    commands = native.ConversationCommands
    original_clear = getattr(native, "_clear_third_party_agent_runner_state", None)
    if not callable(original_clear):
        # AstrBot 4.27.60 and some vendor builds do not expose the internal
        # runner reset helper this bridge wraps.  The reset marker is optional;
        # plugin startup must not fail because the host is older.
        return lambda: None
    if callable(getattr(commands, "reset", None)):
        bindings = ((commands, "reset"), (commands, "new_conv"))
    else:
        from astrbot.builtin_stars.builtin_commands.main import Main

        bindings = ((Main, "reset"), (commands, "new_conv"))
    originals = {binding: getattr(*binding) for binding in bindings}

    @wraps(original_clear)
    async def clear(context, umo, agent_runner_type):
        await original_clear(context, umo, agent_runner_type)
        event = active.get()
        if event is not None and context is native_context and event.unified_msg_origin == umo:
            event.set_extra(SUCCESS_MARKER, True)

    def decorate(original):
        @wraps(original)
        async def command(instance, event):
            token = active.set(event if instance.context is native_context else None)
            try:
                return await original(instance, event)
            finally:
                active.reset(token)
        return command

    replacements = {binding: decorate(original) for binding, original in originals.items()}
    native._clear_third_party_agent_runner_state = clear
    for (owner, name), command in replacements.items():
        setattr(owner, name, command)

    def retire():
        if native._clear_third_party_agent_runner_state is clear:
            native._clear_third_party_agent_runner_state = original_clear
        for (owner, name), command in replacements.items():
            if getattr(owner, name) is command:
                setattr(owner, name, originals[(owner, name)])

    return retire


async def observe_context_reset(controller, event):
    getter = getattr(event, "get_extra", None)
    if not callable(getter) or getter(SUCCESS_MARKER, False) is not True:
        return Outcome.success({"handled": False})
    if getter(OBSERVED_MARKER, False) is True:
        return Outcome.success({"handled": True, "replayed": True})
    target = controller.target_for_event(event)
    if target is None or controller._terminated:
        return Outcome.success({"handled": False})
    try:
        # The exact native event, client identity and configured target were
        # authenticated above. /new may already have replaced the native cid.
        if isinstance(target, GroupTarget):
            route = group_event_route(event)
            kind, native_scope_id, session_id = ScopeKind.GROUP, route.group_id, route.session_id
        else:
            kind, native_scope_id, session_id = ScopeKind.PRIVATE, target.participant_id, target.participant_id
        async with controller._control_lock:
            if controller.runtime is None:
                from companion_v2.storage.runtime import StorageRuntime
                root = controller.profile_root
                if root is None or not (root / "runtime" / StorageRuntime.DATABASE_FILENAME).is_file():
                    return Outcome.success({"handled": False})
                controller._open()
            window = ConversationWindow(controller.runtime.installation_id, target.platform_id, kind,
                                        native_scope_id, target.bot_id, target.account_id, session_id)
            reset_id = "host-reset-" + digest([window, str(event.message_obj.message_id), event.created_at])
            session = controller.session or controller.last_session
            if session is None:
                result = reset_window(controller.runtime, window, reset_id=reset_id, at=controller.now())
            else:
                async with session._intake[target.target_id]:
                    result = require(session.application.reset_conversation_window(window, reset_id=reset_id))
            event.set_extra(OBSERVED_MARKER, True)
        return Outcome.success(result)
    except Exception as error:
        return controller._failure(error, operation="conversation_reset")
