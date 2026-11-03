"""The additions one reply part carries, derived from its own sealed batch.

Nothing here is decided at delivery time from live settings. The component order
and the quote switches come from the reply settings that were captured when the
turn sealed, the quoted message and the addressed member come from the sealed
inputs, and the part sequence comes from the sealed parts. Both the generation
that sealed the reply and the delivery that dispatches it call this module with
the same immutable inputs, so the message that leaves is the message that was
reviewed -- without a second plan column and without touching the payload hash
that already binds text, originals and their position.
"""

from __future__ import annotations

import json

from companion_v2.delivery.plan import CONTENT_KINDS, OutboundPlan


DEFAULT_ORDER = "[]"
MAX_ORDER_KINDS = len(CONTENT_KINDS)


def component_order(settings):
    """The explicit component order one captured snapshot declares, if any."""
    raw = settings.get("component_order", DEFAULT_ORDER)
    if type(raw) is not str or len(raw.encode("utf-8")) > 512:
        raise ValueError("component order requires a short JSON string list")
    try:
        items = json.loads(raw or DEFAULT_ORDER)
    except (ValueError, TypeError) as error:
        raise ValueError("component order requires a JSON string list") from error
    if type(items) is not list or len(items) > MAX_ORDER_KINDS:
        raise ValueError("component order contains too many entries")
    if any(type(item) is not str or item not in CONTENT_KINDS for item in items):
        raise ValueError("component order names an unsupported kind")
    if len(set(items)) != len(items):
        raise ValueError("component order repeats a kind")
    return tuple(items)


def captured_settings(batch):
    """The reply settings sealed with one batch, or an empty snapshot."""
    contributions = json.loads(batch["contributions_json"])
    captured = contributions.get("reply_settings")
    if captured is None:
        return {}
    settings = captured.get("settings")
    if type(settings) is not dict:
        raise ValueError("sealed reply settings are malformed")
    return dict(settings)


def sealed_inputs(runtime, batch_id):
    """The native message number and account of each sealed input, in order."""
    rows = runtime.read_all("SELECT event_json, withdrawn FROM conversation_inputs "
                            "WHERE batch_id = ? ORDER BY position", (batch_id,))
    facts = []
    for row in rows:
        if row["withdrawn"]:
            continue
        document = json.loads(row["event_json"])
        principal = document.get("principal") or {}
        facts.append((str(document.get("event_id", "")), str(principal.get("native_account_id", ""))))
    return tuple(facts)


def _native_number(value):
    """Only a platform message number or account id may be quoted or addressed.

    OneBot v11 ids are signed integers. Some implementations (SnowLuma among
    them) legitimately hand out negative message ids, so a leading ``-`` is part
    of the id rather than a malformed value; dropping it made 引用触发消息 look
    disabled on those hosts.
    """
    if type(value) is not str or not value.isascii():
        return ""
    digits = value[1:] if value.startswith("-") else value
    if not digits.isdecimal():
        return ""
    if not 1 <= len(digits) <= 20 or digits.startswith("0"):
        return ""
    return value


def _native_message_id(value):
    """OneBot message ids are signed integers, unlike account ids."""
    if type(value) is not str or not value.isascii():
        return ""
    digits = value[1:] if value.startswith("-") else value
    if not digits.isdecimal() or not 1 <= len(digits) <= 19 or digits.startswith("0"):
        return ""
    return value


def quote_target(settings, *, text, inputs):
    """The native message this part quotes, or an empty string."""
    if not settings.get("reply_quote_enabled", False):
        return ""
    if not settings.get("reply_quote_group_enabled", True):
        return ""
    if not inputs:
        return ""
    threshold = settings.get("reply_quote_skip_chars", 0)
    if threshold and len("".join(text.split())) <= threshold:
        return ""
    return _native_message_id(inputs[0][0])


def mention_targets(settings, *, inputs):
    """The members this part addresses by platform account, if enabled."""
    if not settings.get("reply_mention_speaker_enabled", False) or not inputs:
        return ()
    sender = _native_number(inputs[0][1])
    return (sender,) if sender else ()


def outbound_plan(*, settings, scope_kind, sequence, first_text_sequence, text, inputs):
    """The sealed additions for one part, or None when it carries none."""
    order = component_order(settings)
    quote, mentions = "", ()
    # A quote names the message this turn answers, so it belongs to the first
    # visible answer of the turn; addressing the speaker follows the same rule.
    if (scope_kind == "group" and text.strip()
            and (sequence == first_text_sequence or not settings.get("reply_quote_first_part_only", True))):
        quote = quote_target(settings, text=text, inputs=inputs)
        mentions = mention_targets(settings, inputs=inputs)
    plan = OutboundPlan(quote, mentions, (), order)
    return None if plan.empty else plan


def first_text_sequence(parts):
    """The sequence number of the first part that carries visible text."""
    for index, text in enumerate(parts, 1):
        if isinstance(text, str) and text.strip():
            return index
    return 0
