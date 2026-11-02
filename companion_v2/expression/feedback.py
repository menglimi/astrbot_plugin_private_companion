"""Feedback accounting for adopted expression rules.

Only confirmed deliveries can create a feedback window. A later input from the
same role and conversation may then update the rules that were in that prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import re

from companion_v2.execution.models import digest, stamp


logger = logging.getLogger(__name__)
INJECTION_PREFIX = "role.expression.feedback.injection.v1:"
PENDING_PREFIX = "role.expression.feedback.pending.v1:"
USAGE_PREFIX = "role.expression.feedback.usage.v1:"
FEEDBACK_WINDOW = timedelta(minutes=10)
NEGATIVE_REVIEW_THRESHOLD = 2

_NEGATIVE = re.compile(
    r"(别这么说|别这样说|别学|不像你|正常说话|好尬|尴尬|油腻|别夹|别装|"
    r"这个语气.{0,6}(?:怪|烦|恶心|不喜欢)|你怎么说话|说话怎么.{0,6}怪|闭嘴|吵死)",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"(?:(?:这样说|这个语气|你这么说|你这样说|这个说法|这句话).{0,8}"
    r"(?:好|喜欢|自然|舒服|可爱|对味))|"
    r"(?:(?:好喜欢|很喜欢).{0,8}(?:你这样|这个语气|你这么说))",
    re.IGNORECASE,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def injection_key(batch_id):
    return INJECTION_PREFIX + digest(batch_id)


def pending_key(owner_key, scope_key):
    return PENDING_PREFIX + digest([owner_key, scope_key])


def _usage_prefix(owner_key, scope_key):
    return USAGE_PREFIX + digest([owner_key, scope_key]) + ":"


def _read(connection, key):
    row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return None
    return value if type(value) is dict else None


def _write(connection, key, value):
    connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, _json(value)))


def stage_injection(runtime, *, batch_id, owner_key, scope_key, signal_ids, at):
    """Remember which rules entered a generated turn, pending its delivery receipt."""
    identifiers = []
    seen = set()
    try:
        for item in signal_ids:
            if type(item) is not str or not item or len(item) > 512:
                return
            if item not in seen:
                seen.add(item)
                identifiers.append(item)
    except TypeError:
        return
    identifiers = tuple(identifiers)
    if (type(batch_id) is not str or not batch_id or type(owner_key) is not str or not owner_key
            or type(scope_key) is not str or not scope_key or not identifiers
            or len(identifiers) > 16):
        return
    value = {"batch_id": batch_id, "owner_key": owner_key, "scope_key": scope_key,
             "signal_ids": list(identifiers), "staged_at": stamp(at)}
    with runtime.transaction() as connection:
        _write(connection, injection_key(batch_id), value)


def confirm_delivery(connection, *, batch_id, owner_key, scope_key, delivered_at, rotation_record,
                     family_scopes=None):
    """Promote a staged injection only after its first trusted delivery receipt.

    ``family_scopes`` receives the injected rules' windows other than this one
    and returns the conversation's scope family, exactly as injection reads it.
    It is only asked when such a rule exists, so a batch learned in the answered
    window reads no conversation table. An injected rule may come from an
    earlier window or the V1 group identity scope, and the feedback window must
    keep every rule the family owns; otherwise a rotated rule would never hear
    that the reply was disliked. A rule of another relationship never enters
    this window, and rotation counters stay under each rule's own scope.
    """
    key = pending_key(owner_key, scope_key)
    candidate = _read(connection, injection_key(batch_id))
    current = _read(connection, key)
    if type(current) is dict and current.get("batch_id") == batch_id:
        connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (injection_key(batch_id),))
        return

    previous_time = None
    if type(current) is dict:
        try:
            previous_time = datetime.fromisoformat(current["delivered_at"])
        except (KeyError, TypeError, ValueError):
            previous_time = None
    if previous_time is not None and delivered_at < previous_time:
        connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (injection_key(batch_id),))
        return

    valid_candidate = (
        type(candidate) is dict
        and set(candidate) == {"batch_id", "owner_key", "scope_key", "signal_ids", "staged_at"}
        and candidate["batch_id"] == batch_id
        and candidate["owner_key"] == owner_key
        and candidate["scope_key"] == scope_key
        and type(candidate["signal_ids"]) is list
        and bool(candidate["signal_ids"])
        and all(type(item) is str and item and len(item) <= 512 for item in candidate["signal_ids"])
        and len(set(candidate["signal_ids"])) == len(candidate["signal_ids"])
    )
    if valid_candidate:
        identifiers = tuple(candidate["signal_ids"])
        source_scopes, by_source_scope = {}, {}
        for signal_id in identifiers:
            signal = connection.execute("""SELECT scope_key FROM expression_style_signals
                WHERE signal_id = ? AND owner_key = ? AND state = 'adopted'""",
                (signal_id, owner_key)).fetchone()
            if signal is not None:
                source_scopes[signal_id] = signal["scope_key"]
                by_source_scope.setdefault(signal["scope_key"], []).append(signal_id)
        for source_scope, source_ids in by_source_scope.items():
            rotation_record(connection, owner_key, source_scope, tuple(source_ids), delivered_at)
        # A batch learned in the answered window is answered without reading the
        # conversation tables; the family is only resolved when an injected rule
        # really lives in another window. Either way each rule is taken once.
        family = {scope_key}
        foreign = {scope for scope in source_scopes.values() if scope != scope_key}
        if foreign and family_scopes is not None:
            try:
                resolved = family_scopes(foreign)
            except Exception as error:
                # The receipt shares the caller's savepoint with the rotation
                # record, so a broken family read must not escape: fall back to
                # the answered window alone, which is what a family without
                # earlier windows would have been. The failure is only named by
                # its type, so no conversation content reaches the log.
                logger.warning("V2 expression feedback family unavailable, using the answered window: %s",
                               type(error).__name__)
                resolved = ()
            for family_key in resolved or ():
                if type(family_key) is str and family_key:
                    family.add(family_key)
        family_identifiers = tuple(signal_id for signal_id in identifiers
                                   if source_scopes.get(signal_id) in family)
        if family_identifiers:
            _write(connection, key, {"batch_id": batch_id, "owner_key": owner_key,
                "scope_key": scope_key, "signal_ids": list(family_identifiers),
                "delivered_at": stamp(delivered_at), "seen_after": 0})
        else:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
    else:
        connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
    connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (injection_key(batch_id),))


def classify(text):
    if type(text) is not str:
        return ""
    cleaned = " ".join(text.strip().split())[:260]
    if not cleaned:
        return ""
    if _NEGATIVE.search(cleaned):
        return "negative"
    return "positive" if _POSITIVE.search(cleaned) else ""


def _usage_entry(value, signal_id):
    try:
        entry = json.loads(value)
    except (TypeError, ValueError):
        return None
    required = {"signal_id", "positive_count", "negative_count", "last_feedback",
                "last_feedback_at", "review_reason"}
    if (type(entry) is not dict or set(entry) != required or entry["signal_id"] != signal_id
            or type(entry["positive_count"]) is not int or entry["positive_count"] < 0
            or type(entry["negative_count"]) is not int or entry["negative_count"] < 0
            or entry["last_feedback"] not in (None, "positive", "negative")
            or entry["last_feedback_at"] is not None and type(entry["last_feedback_at"]) is not str
            or entry["review_reason"] is not None and type(entry["review_reason"]) is not str):
        return None
    return entry


def usage(runtime, owner_key, scope_key):
    prefix = _usage_prefix(owner_key, scope_key)
    rows = runtime.read_all("SELECT key, value FROM runtime_metadata WHERE key LIKE ?", (prefix + "%",))
    result = {}
    for row in rows:
        signal_id = row["key"][len(prefix):]
        if not signal_id or len(signal_id) != 64:
            continue
        try:
            document = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
        entry = _usage_entry(row["value"], document.get("signal_id") if type(document) is dict else "")
        if entry is not None and row["key"] == prefix + digest(entry["signal_id"]):
            result[entry["signal_id"]] = entry
    return result


def consume_feedback(connection, *, owner_key, scope_key, channel, text, received_at):
    """Apply one same-conversation input to the prior delivered rule set, if eligible.

    The delivery receipt already staged only the scope family's rules, so a
    rewritten conversation keeps counting the same rule under the one scope it
    was learned in instead of losing it or copying it into the current window.
    """
    key = pending_key(owner_key, scope_key)
    pending = _read(connection, key)
    required = {"batch_id", "owner_key", "scope_key", "signal_ids", "delivered_at", "seen_after"}
    if (type(pending) is not dict or set(pending) != required
            or pending["owner_key"] != owner_key or pending["scope_key"] != scope_key
            or type(pending["batch_id"]) is not str or not pending["batch_id"]
            or type(pending["signal_ids"]) is not list
            or not pending["signal_ids"]
            or any(type(item) is not str or not item or len(item) > 512 for item in pending["signal_ids"])
            or len(set(pending["signal_ids"])) != len(pending["signal_ids"])
            or type(pending["seen_after"]) is not int or pending["seen_after"] < 0):
        return {}
    if channel not in ("private", "group") or received_at.tzinfo is None or received_at.utcoffset() is None:
        return {}
    try:
        delivered_at = datetime.fromisoformat(pending["delivered_at"])
    except (TypeError, ValueError):
        connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
        return {}
    if delivered_at.tzinfo is None or delivered_at.utcoffset() is None:
        connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
        return {}
    if received_at <= delivered_at:
        return {}
    if received_at - delivered_at > FEEDBACK_WINDOW:
        connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
        return {}

    signal = classify(text)
    seen_after = pending["seen_after"] + 1
    if not signal:
        max_unmatched = 3 if channel == "group" else 1
        if seen_after >= max_unmatched:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
        else:
            _write(connection, key, {**pending, "seen_after": seen_after})
        return {}

    updated, demoted = 0, 0
    for signal_id in dict.fromkeys(pending["signal_ids"]):
        if type(signal_id) is not str or not signal_id or len(signal_id) > 512:
            continue
        row = connection.execute("""SELECT scope_key, state FROM expression_style_signals
            WHERE signal_id = ? AND owner_key = ?""",
            (signal_id, owner_key)).fetchone()
        if row is None or row["state"] != "adopted":
            continue
        source_scope = row["scope_key"]
        metadata_key = _usage_prefix(owner_key, source_scope) + digest(signal_id)
        stored = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (metadata_key,)).fetchone()
        previous = _usage_entry(stored["value"], signal_id) if stored is not None else None
        previous = previous or {"signal_id": signal_id, "positive_count": 0, "negative_count": 0,
            "last_feedback": None, "last_feedback_at": None, "review_reason": None}
        count_field = "positive_count" if signal == "positive" else "negative_count"
        previous[count_field] += 1
        previous["last_feedback"] = signal
        previous["last_feedback_at"] = stamp(received_at)
        updated += 1
        if (signal == "negative" and previous["negative_count"] >= NEGATIVE_REVIEW_THRESHOLD
                and previous["negative_count"] > previous["positive_count"]):
            reason = "收到两次以上明确负向反馈，已退回复审"
            decision_key = digest(["expression-style-feedback.v1", pending["batch_id"], signal_id,
                                   previous["negative_count"], previous["positive_count"]])
            changed = connection.execute("""UPDATE expression_style_signals
                SET state = 'pending', decided_at = ?, decided_by = 'expression_feedback', decision_key = ?
                WHERE signal_id = ? AND owner_key = ? AND scope_key = ? AND state = 'adopted'""",
                (stamp(received_at), decision_key, signal_id, owner_key, source_scope))
            if changed.rowcount:
                previous["review_reason"] = reason
                demoted += 1
        _write(connection, metadata_key, previous)
    connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (key,))
    return {"signal": signal, "updated_rules": updated, "demoted_rules": demoted}
