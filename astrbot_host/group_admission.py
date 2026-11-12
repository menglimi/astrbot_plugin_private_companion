"""Group trigger facts from the authenticated native message components."""

from collections.abc import Mapping
from companion_v2.adapters.astrbot.group_mentions import bot_mention_ids, official_mention_targets


def group_trigger_facts(event, account_id):
    message = event.message_obj
    raw = getattr(message, "raw_message", None)
    segments = raw.get("message") if isinstance(raw, Mapping) else None
    parts = segments if isinstance(segments, list) else getattr(message, "message", ())
    bot_ids = bot_mention_ids(event, account_id)
    mentioned, mentions_others, mentions_all, has_image, replies = False, False, False, False, []
    for part in parts:
        if isinstance(part, Mapping):
            kind, data = part.get("type"), part.get("data", {})
            if not isinstance(data, Mapping):
                continue
            qq, identifier = data.get("qq"), data.get("id")
        else:
            kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
            qq, identifier = getattr(part, "qq", None), getattr(part, "id", None)
        if kind in ("at", "At"):
            if str(qq) in bot_ids:
                mentioned = True
            elif str(qq) == "all":
                mentions_all = True
            else:
                mentions_others = True
        elif kind in ("reply", "Reply") and identifier is not None:
            replies.append(str(identifier))
        elif kind in ("image", "Image"):
            has_image = True
    for target in official_mention_targets(event):
        if target in bot_ids:
            mentioned = True
        elif target == "all":
            mentions_all = True
        else:
            mentions_others = True
    quoted_bot = any(
        getattr(getattr(part, "type", None), "value", getattr(part, "type", None)) == "Reply"
        and str(getattr(part, "id", "")) in replies
        and str(getattr(part, "sender_id", "")) == account_id
        for part in getattr(message, "message", ()))
    # is_wake also means an event listener activated, so it is not a reply trigger.
    return {"mentioned": mentioned,
            "native_wakeup": getattr(event, "is_at_or_wake_command", False) is True,
            "mentions_others": mentions_others, "reply_ids": tuple(dict.fromkeys(replies)),
            "mentions_all": mentions_all, "quoted_bot": quoted_bot, "has_image": has_image}


def mentioned_account_ids(event):
    """Read @ targets from the authenticated native component chain."""
    message = getattr(getattr(event, "message_obj", None), "message", ())
    found = []
    for part in message or ():
        if isinstance(part, Mapping):
            kind, data = part.get("type"), part.get("data", {})
            target = data.get("qq") if isinstance(data, Mapping) else None
        else:
            kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
            target = getattr(part, "qq", None)
        if kind not in ("at", "At") or target is None:
            continue
        value = str(target)
        if value.isascii() and value.isdecimal():
            found.append(value)
    return tuple(dict.fromkeys(found))
