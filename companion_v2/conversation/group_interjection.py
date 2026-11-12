"""Group self-initiated speech: interjection budget, cooldown and feedback.

V1 let the role interject into a busy group, bounded by a minimum interval and a
per-group daily count, and let member reactions adjust how eager it was later
(``_group_interjection_allowed`` / ``_update_group_interjection_feedback``). V2
deliberately has no separate unprompted send path: a group turn exists only
because an admitted member message opened it, the decision stays with the role
through ``group.participation.choose``, and anything the role says still leaves
through the ordinary authorized delivery chain. This module therefore owns the
bookkeeping half of V1's feature -- how often the role itself spoke in this
group, and how the group reacted -- and hands the result to the prompt as a
reference rather than a hard quota, which is what
``CORE_GROUP_PARTICIPATION_20260925.md`` requires ("没有固定触发词、回复配额或
强制疲劳阈值").

The same ledger carries repeat-follow acts, because V1 counted both against one
per-group daily budget (``group_interject_max_daily``). Records are owner-scoped
documents on the shared ``runtime_metadata`` table, so this slice adds no table
and no schema version. An act only counts once its original delivery receipt
exists: a preview turn, a not-sent turn or an unknown outcome never turns into a
"the role already spoke here" fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

from .group_observation import GroupPresenceStore, parse_moment
from .group_timing import ADDRESS_MARKERS, clean_group_text, repeat_burst, repeat_decision, repeat_signature


SPEECH_KIND = "speech"
INTERJECTION = "interjection"
REPEAT_FOLLOW = "repeat_follow"
REPEAT_INTERRUPT = "repeat_interrupt"
ACT_KINDS = (INTERJECTION, REPEAT_FOLLOW, REPEAT_INTERRUPT)

# Feedback keeps V1's direction and adds a floor so one ignored attempt cannot
# remove the ability entirely: being answered holds the tendency, being ignored
# lowers it, and it recovers as later attempts are answered again.
WEIGHT_START = 0.7
WEIGHT_MIN = 0.2
WEIGHT_MAX = 1.0
ACKNOWLEDGED_STEP = 0.15
IGNORED_STEP = 0.2

# V1's refusal and natural-opening vocabulary for the current message.
REFUSAL_MARKERS = (r"别插", r"别接", r"别吵", r"别回", r"闭嘴", r"别打断")
OPENING_MARKERS = ((r"有没有人|谁懂|救命|怎么回事|咋办", "open_question"),
                   (r"笑死|绷不住|太离谱", "joke_reaction"))


def _low(value, default=0.0):
    if type(value) is bool or type(value) not in (int, float):
        return default
    return float(value)


def day_key(at, timezone_name):
    """The group's local calendar day; an unusable zone falls back to UTC."""
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        zone = timezone.utc
    return at.astimezone(zone).date().isoformat()


@dataclass(frozen=True)
class InterjectionPolicy:
    """One bounded interjection rule set, read from the registered settings."""

    enabled: bool = False
    min_interval_seconds: int = 10800
    max_daily: int = 2
    feedback_enabled: bool = True
    feedback_window_seconds: int = 600
    address_words: tuple = ()

    def __post_init__(self):
        for name in ("enabled", "feedback_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for name in ("min_interval_seconds", "max_daily", "feedback_window_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if type(self.address_words) is not tuple or any(
                type(word) is not str or not word or len(word) > 40 for word in self.address_words):
            raise ValueError("address words must be a bounded tuple of short strings")


def addresses_role(text, words):
    """Deterministic "this message is aimed at the role" reference.

    The host marks a direct mention in the admitted text; configured direct words
    and the role's own configured name are the other registered hints. Nothing
    here decides participation -- it only separates an addressed reply from a
    self-initiated one for the budget and the feedback signal.
    """
    compact = clean_group_text(text)
    if not compact:
        return False
    if any(marker in compact for marker in ADDRESS_MARKERS):
        return True
    return any(word in compact for word in words)


def openings(text, *, mood_top=""):
    """Natural openings and refusals of the current message, as reason codes."""
    compact = clean_group_text(text)
    reasons = []
    if any(re.search(pattern, compact) for pattern in REFUSAL_MARKERS):
        reasons.append("refusal")
    for pattern, code in OPENING_MARKERS:
        if re.search(pattern, compact):
            reasons.append(code)
    if mood_top == "tease":
        reasons.append("playful_mood")
    elif mood_top in ("serious", "tension"):
        reasons.append("sober_mood")
    return tuple(dict.fromkeys(reasons))


def delivered_at(runtime, batch_id, origin_key):
    """When that original turn really reached this group, from its own receipt."""
    row = runtime.read_one("""SELECT MAX(p.delivered_at) AS delivered_at FROM conversation_parts p
        JOIN conversation_sent_history h ON h.part_id = p.part_id AND h.batch_id = p.batch_id
        JOIN conversation_delivery_receipts r ON r.part_id = p.part_id AND r.receipt_ref = p.receipt_ref
        WHERE p.batch_id = ? AND p.state = 'delivered' AND h.origin_key = ?""", (batch_id, origin_key))
    return parse_moment(row["delivered_at"]) if row is not None and row["delivered_at"] else None


class GroupSpeechLedger:
    """One bounded record per group owner for what the role itself said here."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.store = GroupPresenceStore(runtime)

    def load(self, owner):
        raw = self.store.read(owner, SPEECH_KIND) or {}
        repeat = raw.get("repeat") if type(raw.get("repeat")) is dict else {}
        raw_signature = str(repeat.get("signature") or "")
        signature = repeat_signature(raw_signature)
        pending = raw.get("pending") if type(raw.get("pending")) is dict else None
        if pending is not None and pending.get("kind") in (REPEAT_FOLLOW, REPEAT_INTERRUPT):
            pending_signature = repeat_signature(pending.get("signature"))
            pending = ({**pending, "signature": pending_signature} if pending_signature else None)
        awaiting = raw.get("awaiting") if type(raw.get("awaiting")) is dict else None
        return {"day": str(raw.get("day") or ""), "used_today": max(0, int(_low(raw.get("used_today")))),
                "last_act_at": str(raw.get("last_act_at") or ""),
                "weight": min(WEIGHT_MAX, max(WEIGHT_MIN, _low(raw.get("weight"), WEIGHT_START))),
                "pending": pending, "awaiting": awaiting, "recorded_batch": str(raw.get("recorded_batch") or ""),
                "repeat": {"signature": signature,
                           "acts": max(0, int(_low(repeat.get("acts")))) if signature == raw_signature else 0,
                           "last_act_at": str(repeat.get("last_act_at") or "") if signature == raw_signature else ""}}

    def resolve(self, record, *, at, day, policy, items, origin_key, timezone_name):
        """Confirm or drop the last decision and settle its feedback.

        Called while reading, before this turn's own decision is computed: an act
        becomes a fact only after its receipt exists, and a reaction counts only
        when a later admitted member message either is aimed at the role inside
        the feedback window or never arrives at all.
        """
        record = dict(record)
        if day != record["day"]:
            record["day"], record["used_today"] = day, 0
        confirmed, feedback = "", ""
        # Only a window that was already open can be judged here: an act confirmed
        # in this very call has not had its chance to be answered yet.
        awaiting = record.get("awaiting")
        pending = record.get("pending")
        if pending is not None:
            moment = delivered_at(self.runtime, str(pending.get("batch_id") or ""), origin_key)
            decided = parse_moment(pending.get("at"))
            if moment is not None:
                confirmed = str(pending.get("kind") or "")
                record["pending"] = None
                if day_key(moment, timezone_name) == day:
                    record["used_today"] += 1
                record["last_act_at"] = moment.isoformat()
                if confirmed in (REPEAT_FOLLOW, REPEAT_INTERRUPT):
                    previous = record["repeat"]
                    acts = previous["acts"] + 1 if previous["signature"] == str(pending.get("signature") or "") else 1
                    record["repeat"] = {"signature": str(pending.get("signature") or ""), "acts": acts,
                                        "last_act_at": moment.isoformat()}
                record["awaiting"] = {"kind": confirmed, "at": moment.isoformat()}
            elif decided is None or (at - decided).total_seconds() > policy.feedback_window_seconds:
                # An undelivered, not-sent or unknown decision is never counted.
                record["pending"] = None
        if awaiting is not None and policy.feedback_enabled:
            start = parse_moment(awaiting.get("at"))
            if start is None:
                record["awaiting"] = None if record.get("awaiting") == awaiting else record["awaiting"]
            else:
                end = min(at, start + timedelta(seconds=policy.feedback_window_seconds))
                seen = [item for item in items if item.get("kind") == "member"
                        and start < (parse_moment(item.get("received_at")) or start) <= end]
                if any(addresses_role(item.get("text"), policy.address_words) for item in seen):
                    feedback = "acknowledged"
                elif at >= start + timedelta(seconds=policy.feedback_window_seconds):
                    feedback = "ignored"
                if feedback:
                    step = ACKNOWLEDGED_STEP if feedback == "acknowledged" else -IGNORED_STEP
                    record["weight"] = round(min(WEIGHT_MAX, max(WEIGHT_MIN, record["weight"] + step)), 4)
                    if record.get("awaiting") == awaiting:
                        record["awaiting"] = None  # a newer act keeps its own open window
        return {"record": record, "confirmed": confirmed, "feedback": feedback}

    @staticmethod
    def act(record, *, kind, batch_id, at, day, signature=""):
        """Record this turn's decision; it stays pending until its receipt exists."""
        if kind not in ACT_KINDS:
            raise ValueError("unsupported group self-initiated speech kind")
        if record.get("recorded_batch") == batch_id and type(record.get("pending")) is dict:
            return dict(record)
        record = dict(record)
        record["day"] = day
        record["recorded_batch"] = batch_id
        record["pending"] = {"batch_id": batch_id, "kind": kind, "at": at.isoformat(), "signature": signature}
        return record

    def persist(self, connection, owner, record):
        self.store.write(connection, owner, SPEECH_KIND, record)


def interjection_state(policy, record, *, at, initiative, reasons, interest_probability):
    """The bounded interjection reference handed to the group prompt."""
    last = parse_moment(record["last_act_at"])
    elapsed = max(0.0, (at - last).total_seconds()) if last is not None else None
    remaining = max(0, policy.max_daily - record["used_today"])
    cooled = policy.min_interval_seconds <= 0 or elapsed is None or elapsed >= policy.min_interval_seconds
    state = ("disabled" if not policy.enabled else "daily_closed" if policy.max_daily <= 0 else
             "daily_used" if remaining <= 0 else "cooldown" if not cooled else
             "refused" if "refusal" in reasons else "open")
    return {"enabled": policy.enabled, "initiative": bool(initiative), "state": state,
            "used_today": record["used_today"], "max_daily": policy.max_daily, "remaining": remaining,
            "elapsed_minutes": None if elapsed is None else round(elapsed / 60),
            "min_interval_minutes": round(policy.min_interval_seconds / 60),
            "weight": round(record["weight"], 4), "feedback_enabled": policy.feedback_enabled,
            "interest_percent": max(0, min(100, round(interest_probability * record["weight"] * 100))),
            "reasons": tuple(reasons), "allowed": state == "open"}


def repeat_state(policy, record, *, at, burst):
    """The repeat decision plus the bounded facts the prompt may quote."""
    repeat = record["repeat"]
    last_act = parse_moment(repeat["last_act_at"])
    same_burst = bool(burst and repeat["signature"] == burst["signature"] and last_act is not None
                      and 0 <= (at - last_act).total_seconds() <= max(1, policy.window_seconds))
    cooled = (policy.cooldown_seconds <= 0 or last_act is None
              or (at - last_act).total_seconds() >= policy.cooldown_seconds)
    decision = repeat_decision(policy, burst, acts=repeat["acts"], same_burst=same_burst,
                               cooled=cooled, used_today=record["used_today"])
    return {"burst": burst, "decision": decision, "acts": repeat["acts"], "same_burst": same_burst,
            "used_today": record["used_today"], "max_daily": policy.max_daily,
            "cooldown_minutes": round(policy.cooldown_seconds / 60), "threshold": policy.threshold,
            "distinct_users_only": policy.distinct_users_only, "window_seconds": policy.window_seconds,
            "interrupt_text": policy.interrupt_text, "allowed": bool(decision["action"])}


def visible_burst(items, text, *, at, policy):
    """The current message's own repeat burst inside the visible window."""
    signature = repeat_signature(text)
    return None if not signature else repeat_burst(signature, items, at=at, window_seconds=policy.window_seconds)


def best_burst(items, texts, *, at, policy):
    """The strongest burst among this turn's own admitted messages.

    A turn may carry several merged messages from the same member. Comparing
    them by (count, distinct, signature) keeps the choice deterministic instead
    of depending on the merge order.
    """
    best = None
    for text in texts:
        burst = visible_burst(items, text, at=at, policy=policy)
        if burst is None:
            continue
        if best is None or (burst["count"], burst["distinct"], burst["signature"]) > (
                best["count"], best["distinct"], best["signature"]):
            best = burst
    return best
