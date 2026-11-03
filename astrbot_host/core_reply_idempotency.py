"""Never deliver the same body twice in a row inside one reply turn.

Where this net runs
-------------------
Only deliveries that reach the guarded send loop are compared: ``event.send``
inside ``guarded_send``, on the native part that loop is about to hand to the
platform.  ``event.send_streaming`` is *not* covered by this net.  The managed
group stream happens to re-enter the guarded loop (``send_group_stream`` ->
``event.send``) and is therefore compared; every other streaming implementation,
including ``aiocqhttp`` native bubbles, leaves through
``_original_send_streaming`` and is never seen here.  A delivery that never goes
through the guard (another plugin, an SDK shortcut, a turn that was not
prepared through ``prepare_core_reply_event``) keeps its own behaviour.

What it removes
---------------
AstrBot hands one turn's text to the platform more than once in several ways that
are outside the reply's own control: a buffered group stream carries a round that
repeated itself, a tool round's prose and the answer land on the same words, or an
earlier layer re-sends after rescuing a partial failure.  The turn gate removes the
cause; this net removes the effect on the guarded send path, including when that
gate was never installed.

It drops a delivery only when every one of these holds, so it can never swallow
the only reply of a turn:

* same turn -- the net is bound to this turn's identity (the inbound event
  reference plus the ordinal of the reply snapshot that the turn prepared) and a
  net left by another turn is never consulted;
* the previous delivery of that exact body and decoration was accepted;
* the body is exactly equal, punctuation and spacing included, and so is every
  decoration: the @ targets (``qq``/``account``) and the quoted message id.  Two
  messages with the same words but a different mention or quote read differently
  and are two deliveries, not one;
* the delivery is pure text: a picture, a record or a file is a different effect;
* nothing about this turn's delivery is unknown or already failed;
* only the adjacent second copy -- a third identical body is delivered again, so a
  repeat that comes from somewhere else stays visible instead of being hidden here.
"""

from __future__ import annotations

import hashlib
import logging

from .core_reply import CORE_REPLY_CONTEXT_EXTRA

# The net lives on the turn's reply state and is removed when the turn ends.
DELIVERY_NET_KEY = "_delivery_net"
# The identity the net is bound to.  Kept apart from the net so a missing reset
# can never make the current turn read another turn's net.
DELIVERY_TURN_KEY = "_delivery_turn"
# Ordinal of the reply snapshot seen on this event.  It never repeats, so even a
# stale net that survived its turn cannot match a later turn by accident.
DELIVERY_SEQUENCE_KEY = "_delivery_turn_sequence"
# Component types that carry nothing but reply text.  Anything else is a different
# delivery and is never treated as a repeat.
TEXT_COMPONENTS = frozenset({"Plain", "At", "Reply"})

logger = logging.getLogger(__name__)


def begin_core_reply_delivery_turn(event) -> bool:
    """Bind the net to this turn's identity.

    Returns ``True`` only when a new turn started, which is exactly when the
    caller must forget what the previous turn delivered.  Re-entering
    ``prepare_core_reply_event`` with the same reply snapshot is the same turn and
    keeps the net, so a path that prepares twice does not silently lose dedupe.
    """
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return False
    snapshot = core.get("prepared")
    event_ref = _event_ref(core)
    current = core.get(DELIVERY_TURN_KEY)
    if (isinstance(current, dict) and current.get("event_ref") == event_ref
            and current.get("snapshot") is snapshot):
        return False
    sequence = core.get(DELIVERY_SEQUENCE_KEY)
    sequence = sequence + 1 if isinstance(sequence, int) and sequence >= 0 else 1
    core[DELIVERY_SEQUENCE_KEY] = sequence
    core[DELIVERY_TURN_KEY] = {"event_ref": event_ref, "sequence": sequence,
                               "snapshot": snapshot}
    return True


def reset_core_reply_delivery_net(event) -> None:
    """Start a fresh net; the caller already bound a new turn identity."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(core, dict):
        core.pop(DELIVERY_NET_KEY, None)


def finish_core_reply_delivery_turn(event) -> bool:
    """Remove the net with the turn that owned it.  Called when the turn ends."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return False
    cleared = core.pop(DELIVERY_NET_KEY, None) is not None
    core.pop(DELIVERY_TURN_KEY, None)
    return cleared


def should_drop_core_reply_duplicate(event, part) -> bool:
    """Whether this delivery repeats the one just accepted in the same turn."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return False
    key = _delivery_key(part)
    if key is None:
        return False
    if core.get("send_unknown") or core.get("send_failed"):
        # Unknown or failed delivery makes a resend the safer choice than silence.
        return False
    net = _turn_net(core)
    if net is None or not net["confirmed"] or net["dropped"] or net["key"] != key:
        return False
    net["dropped"] += 1
    logger.info("[CompanionV2] 同一轮相邻重复正文已丢弃 reason=duplicate_adjacent "
                "turn=%s event_ref=%s digest=%s",
                _turn_label(core), _event_ref(core), _digest(key[0]))
    return True


def record_core_reply_delivery(event, part) -> None:
    """Remember the delivery the platform just accepted, so the next copy is seen."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict):
        return
    net = _ensure_net(core)
    if net is None:
        return
    key = _delivery_key(part)
    if key is None:
        # Media breaks adjacency: the text after it is not the text before it.
        net["key"], net["confirmed"] = None, False
        return
    net["key"], net["confirmed"] = key, True


def _turn_net(core):
    """The net of the current turn, or ``None`` when there is nothing to trust.

    A missing net (the reset was skipped) or a net left by an earlier turn is
    never consulted.  The current turn starts one on its first accepted delivery,
    so the skipped reset still leaves adjacency dedupe working without inheriting
    the previous turn's "already delivered" record.
    """
    identity = _turn_identity(core)
    if identity is None:
        return None
    net = core.get(DELIVERY_NET_KEY)
    return net if isinstance(net, dict) and net.get("turn") == identity else None


def _ensure_net(core):
    net = _turn_net(core)
    if net is not None:
        return net
    identity = _turn_identity(core)
    if identity is None:
        return None
    net = {"turn": identity, "key": None, "confirmed": False, "dropped": 0}
    core[DELIVERY_NET_KEY] = net
    return net


def _turn_identity(core):
    turn = core.get(DELIVERY_TURN_KEY)
    if not isinstance(turn, dict):
        return None
    return (turn.get("event_ref"), turn.get("sequence"))


def _turn_label(core) -> str:
    turn = core.get(DELIVERY_TURN_KEY)
    if not isinstance(turn, dict):
        return "unknown#0"
    return "%s#%s" % (turn.get("event_ref", "unknown"), turn.get("sequence", 0))


def _delivery_key(part):
    """What makes two deliveries the same: the text plus every decoration.

    The plain text alone is not enough.  ``@甲`` and ``@乙``, or the same words
    quoting two different messages, read differently even though the words match;
    dropping the second one would lose the mention or the quote the turn meant to
    send.  Only when both the text and the ordered decorations are equal is this
    the same delivery repeated.
    """
    if not _plain_only(part):
        return None
    decorations = []
    for component in getattr(part, "chain", ()) or ():
        name = getattr(getattr(component, "type", None), "name", None)
        if name == "At":
            decorations.append(("At", str(getattr(component, "qq", "")),
                                str(getattr(component, "account", ""))))
        elif name == "Reply":
            decorations.append(("Reply", str(getattr(component, "id", ""))))
    return (_plain_text(part), tuple(decorations))


def _plain_only(part) -> bool:
    components = list(getattr(part, "chain", None) or ())
    if not components:
        return False
    for component in components:
        if getattr(getattr(component, "type", None), "name", None) not in TEXT_COMPONENTS:
            return False
    return bool(_plain_text(part))


def _plain_text(part) -> str:
    if isinstance(part, str):
        return part
    getter = getattr(part, "get_plain_text", None)
    text = getter() if callable(getter) else ""
    return text if isinstance(text, str) else ""


def _event_ref(core):
    """The inbound event this reply belongs to, for identity and logs.

    ``prepared`` carries ``event_ref`` on some hosts and ``turn_id``/``event_id``
    on others; any stable per-event reference is enough, because the turn ordinal
    is what keeps two turns apart.
    """
    prepared = core.get("prepared") if isinstance(core, dict) else None
    getter = getattr(prepared, "get", None)
    if callable(getter):
        for name in ("event_ref", "turn_id", "event_id"):
            value = getter(name)
            if isinstance(value, str) and value:
                return value
    return "unknown"


def _digest(text) -> str:
    """Identify the body in logs without writing the conversation into them."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
