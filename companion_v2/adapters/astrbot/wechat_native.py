"""Read WeChat Native routes through the adapter's own event/session API.

No Native transport, private client requests or replacement event is created here.
"""

from importlib import import_module


def session_target(platform, session_id):
    module = import_module(type(platform).__module__.rsplit(".", 1)[0] + ".event")
    return module.session_target(session_id, platform.native_config.account_id)


def event_session(event):
    platform = event.adapter
    package = type(platform).__module__.rsplit(".", 1)[0]
    module = import_module(package + ".event")
    # Native owns event provenance, including retained events across reloads.
    # Re-importing its event class here can reject the live adapter's generation.
    if not import_module(package + ".tool_support").is_native_event(event):
        raise ValueError("WeChat Native requires its original event")
    message, session, meta = event.message_obj, event.session, event.platform_meta
    raw = message.raw_message
    thread = session_target(platform, session.session_id)
    group = bool(message.group_id)
    kind = "GroupMessage" if group else "FriendMessage"
    sender = message.sender.user_id
    # Use the adapter's public constructor too: it owns member-session semantics.
    expected = module.session_id(platform.native_config.account_id, thread,
        sender if group and platform.native_config.unique_session else None)
    if (meta.name != "wechat_native" or meta.id != platform.meta().id
            or platform.meta().name != meta.name
            or session.platform_id != meta.id or session.platform_name != meta.id
            or getattr(message.type, "value", None) != kind
            or getattr(session.message_type, "value", None) != kind
            or session.session_id != expected or message.session_id != expected
            or event.unified_msg_origin != f"{meta.id}:{kind}:{expected}"
            or not isinstance(raw, dict) or raw.get("accountId") != platform.native_config.account_id
            or raw.get("direction") != "incoming" or raw.get("threadId") != thread
            or raw.get("senderId") != sender or raw.get("id") != message.message_id
            or raw.get("recall") is not None
            or not sender or sender == platform.bot_self_id
            or message.self_id != platform.bot_self_id
            or (group and message.group_id != thread)):
        raise ValueError("WeChat Native event does not identify its original account and chat")
    return session.session_id
