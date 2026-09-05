# -*- coding: utf-8 -*-
"""Pure domain rules for deciding whether an observed group message targets the bot."""
from __future__ import annotations

from typing import Any, Callable


BOT_SCENE_TRIGGERS = frozenset(
    {
        "at_bot",
        "reply_bot",
        "mention_bot_name",
        "bot_conversation_followup",
    }
)
OTHER_SCENE_TRIGGERS = frozenset({"at_other", "reply_other"})


def group_message_addresses_bot(
    item: Any,
    *,
    bot_markers: tuple[Any, ...],
    normalize: Callable[[Any, int], str],
) -> bool:
    """Return whether recorded message fields identify the bot as the target.

    Structured target metadata takes precedence over the final text-marker
    fallback.  The caller supplies its existing text normalizer so truncation
    and coercion remain byte-for-byte compatible with legacy behavior.
    """
    if not isinstance(item, dict):
        return False
    talking_to = normalize(item.get("talking_to"), 40).lower()
    trigger = normalize(item.get("scene_trigger"), 40).lower()
    at_targets = item.get("at_targets") if isinstance(item.get("at_targets"), list) else []
    if (
        talking_to == "bot"
        or trigger in BOT_SCENE_TRIGGERS
        or trigger.startswith("group_wakeup_")
    ):
        return True
    if any(isinstance(target, dict) and bool(target.get("is_bot")) for target in at_targets):
        return True
    if talking_to not in {"", "group", "bot"} or trigger in OTHER_SCENE_TRIGGERS:
        return False
    if at_targets:
        return False
    folded = normalize(item.get("text"), 140).casefold()
    return any(
        str(marker or "").strip().casefold() in folded
        for marker in bot_markers
        if str(marker or "").strip()
    )
