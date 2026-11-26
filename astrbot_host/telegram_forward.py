"""Telegram delivers a forwarded message as the forwarder's own text; say whose words it holds."""

from companion_v2.sources.privacy import redact_text

_LABELED = "_companion_v2_telegram_forward"


def _inline(value):
    return " ".join(redact_text(str(value or "")).split()).replace("[", "［").replace("]", "］")


def _source(origin):
    if origin.type == "user":
        name = origin.sender_user.full_name
    elif origin.type == "hidden_user":
        name = origin.sender_user_name
    else:
        name = getattr(getattr(origin, "sender_chat" if origin.type == "chat" else "chat", None), "title", "")
    return _inline(name)[:80] or "未知"


def telegram_forward_origin(event):
    getter = getattr(event, "get_platform_name", None)
    if not callable(getter) or getter() != "telegram":
        return None
    update = getattr(event.message_obj, "raw_message", None)
    return getattr(getattr(update, "message", None), "forward_origin", None)


def label_telegram_forward(event):
    """Wrap a private Telegram forward in source markers before commands, merging and memory read it."""
    origin, message = telegram_forward_origin(event), event.message_obj
    if origin is None or not message.message or event.get_extra(_LABELED):
        return
    from astrbot.core.message.components import Plain

    # The body cannot close its own section, and the markers match no request keyword.
    opening, closing = f"[以下消息转自：{_source(origin)}]\n", "[以上消息转自他人]"
    body = "".join(part.text for part in message.message if isinstance(part, Plain)).replace("[以", "［以")
    parts, placed = [], not body.strip()
    for part in message.message:
        if not isinstance(part, Plain):
            parts.append(part)
        elif not placed:
            parts.append(Plain(text=body))
            placed = True
    message.message = [Plain(text=opening), *parts, Plain(text=f"\n{closing}")]
    message.message_str = event.message_str = opening + (f"{body}\n" if body.strip() else "") + closing
    event.set_extra(_LABELED, True)
