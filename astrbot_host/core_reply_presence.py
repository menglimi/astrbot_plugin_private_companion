"""Connect native reply events to the optional, recipient-scoped typing lease."""


def observe_native_reply_presence(application, prepared, stage):
    if not isinstance(prepared, dict):
        return
    attention = getattr(application, "role_attention", None)
    lease = prepared.get("role_attention")
    if stage == "finished" and attention is not None and lease is not None:
        attention.finish(lease)
    presence = getattr(application, "presence", None)
    context, turn_id = prepared.get("context"), prepared.get("turn_id")
    if presence is None or context is None or not turn_id:
        return
    if stage == "finished":
        presence.finish_native_reply(context, turn_id)
    elif stage == "returned":
        presence.note_native_reply_sent(context)
    else:
        presence.native_reply_stage(context, turn_id, stage)


def agent_presence_begin(event):
    from .core_reply import CORE_REPLY_CONTEXT_EXTRA
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(state, dict):
        state["_presence_agent_running"] = True


def agent_presence_done(event, response):
    """Generation can finish before delivery, including inside a live stream."""
    from .core_reply import CORE_REPLY_CONTEXT_EXTRA, finish_core_reply_presence, is_core_sendable_message
    state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(state, dict):
        return
    state["_presence_agent_running"] = False
    chain = getattr(response, "result_chain", None)
    text = (getattr(response, "completion_text", None) or "").strip()
    pending = (is_core_sendable_message(chain) if chain is not None else bool(text))
    sent = event.get_extra("_send_message_to_user_current_session_plain_texts", [])
    components = getattr(chain, "chain", ())
    if text and isinstance(sent, list) and text in sent and all(
            getattr(getattr(part, "type", None), "name", None) in {"Plain", "At", "Reply"}
            for part in components):
        pending = False  # AstrBot skips the already delivered tool text.
    if getattr(response, "role", None) != "assistant" or not pending:
        finish_core_reply_presence(event)


def result_presence(event):
    """RespondStage has no after-send callback for empty or deduplicated bodies."""
    from .core_reply import finish_core_reply_presence, is_core_sendable_message
    getter = getattr(event, "get_result", None)
    result = getter() if callable(getter) else None
    kind = getattr(getattr(result, "result_content_type", None), "name", None)
    if kind == "STREAMING_RESULT":
        if getattr(result, "async_stream", None) is None:
            finish_core_reply_presence(event, force=True)
        return
    if result is None or kind == "STREAMING_FINISH" or not is_core_sendable_message(result):
        finish_core_reply_presence(event)
        return
    text = result.get_plain_text().strip()
    sent = event.get_extra("_send_message_to_user_current_session_plain_texts", [])
    if text and isinstance(sent, list) and text in sent and all(
            getattr(getattr(part, "type", None), "name", None) in {"Plain", "At", "Reply"}
            for part in result.chain):
        finish_core_reply_presence(event)
