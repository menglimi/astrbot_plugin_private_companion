"""Read Telegram private-text routes from AstrBot's own event and adapter.

No poller, webhook, Telegram request or replacement event is created here, and
the Telegram SDK is not imported: native objects are read by attribute only.
"""


def event_session(event):
    """Return the private chat of one human, topic-free text message.

    AstrBot only converts ``Update.message``. The raw message check also excludes
    button interactions, which use the adapter's separate callback route.
    Private topics need an explicit check because the adapter shares their
    session with the main private chat.
    """
    try:
        message = event.message_obj
        raw = message.raw_message.message
        chat, sender = raw.chat, raw.from_user
        admitted = (chat.type == "private" and sender.is_bot is False
                    and type(chat.id) is int and chat.id == sender.id
                    and str(sender.id) == str(message.sender.user_id)
                    and isinstance(raw.text, str) and bool(raw.text.strip())
                    and getattr(raw, "message_thread_id", None) is None
                    and not getattr(raw, "is_topic_message", False)
                    and getattr(raw, "direct_messages_topic", None) is None)
    except (AttributeError, TypeError):
        admitted = False
    if not admitted:
        raise ValueError("Telegram event is not a human private text message")
    return str(chat.id)


def explicit_target(settings, target):
    """Telegram admission never inherits a wildcard or a window alias."""
    return (target is not None and target in settings.targets
            and target.participant_id != "*"
            and settings.window_link_for_target(target.target_id) is None)


def application_running(platform, client):
    """Whether ``client`` is the bot of the adapter's current, running application.

    ``application.running`` is the SDK's public lifecycle flag. The two
    adapter-private flags only narrow it further (restart and shutdown windows);
    a host that renames them still answers on the public one.
    """
    try:
        application = platform.application
        return (platform.get_client() is client and application.bot is client
                and application.running is True
                and getattr(platform, "_application_started", True) is True
                and not getattr(platform, "_terminating", False)
                and type(client.id) is int and client.id > 0)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
