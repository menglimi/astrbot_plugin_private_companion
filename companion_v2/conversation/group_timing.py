"""Pure short-input timing and repeat signatures over admitted group material.

The legacy repeat reference remains for old batch prompts. Live literal actions
use the persisted V1 probability draw in repeat_actions, not that comparison.
"""

from dataclasses import dataclass
from datetime import datetime
import re

from companion_v2.foundation.refs import ScopeKind

from .reply_settings import group_wakeup_settings


# Repeat following keeps V1's bounds: one short signature, one bounded window,
# and a hard ceiling on how much either tendency may grow.
REPEAT_VERSION = "group_repeat.v1"
MAX_SIGNATURE_CHARS = 30
MAX_BURST_SPEAKERS = 20
ADDRESS_MARKER = "[提及机器人]"
SELF_MENTION = "<提及自己>"
ADDRESS_MARKERS = (ADDRESS_MARKER, SELF_MENTION)
WEIGHT_CEILING = 0.85
MAX_INTERRUPT_CHARS = 80
MEDIA_PLACEHOLDERS = frozenset((
    "[图片]", "【图片】", "图片", "[语音]", "【语音】", "语音", "[视频]", "【视频】", "视频",
    "[文件]", "【文件】", "文件", "[合并转发]", "[分享卡片]"))
MEDIA_PLACEHOLDER_PATTERN = re.compile(
    r"(?:\[(?:图片|语音|音频|视频|文件|合并转发|引用消息|分享卡片)(?:不可用|\s*\d+)?\]"
    r"|【(?:图片|语音|音频|视频|文件|合并转发|引用消息|分享卡片)(?:不可用|\s*\d+)?】)")
TRAILING_MARKS = re.compile(r"[!！?？。.,，~～…]+$")


def clean_group_text(text):
    """Remove adapter media labels before deriving any group text signal."""
    compact = " ".join(MEDIA_PLACEHOLDER_PATTERN.sub(" ", str(text or "")).split())
    return "" if TRAILING_MARKS.sub("", compact).strip() in MEDIA_PLACEHOLDERS else compact


def strip_address_markers(text):
    for marker in ADDRESS_MARKERS:
        text = text.replace(marker, " ")
    return text


@dataclass(frozen=True)
class RepeatPolicy:
    """One bounded repeat rule set, read from the registered group settings."""

    enabled: bool = True
    threshold: int = 4
    distinct_users_only: bool = False
    follow_probability: float = 0.18
    interrupt_probability: float = 0.10
    interrupt_step: float = 0.12
    interrupt_text: str = "禁止复读"
    window_seconds: int = 120
    cooldown_seconds: int = 10800
    max_daily: int = 2

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.distinct_users_only) is not bool:
            raise ValueError("repeat switches must be boolean")
        for name in ("threshold", "window_seconds", "cooldown_seconds", "max_daily"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.threshold < 1:
            raise ValueError("the repeat threshold must be positive")
        for name in ("follow_probability", "interrupt_probability", "interrupt_step"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a fraction between 0 and 1")
        if (type(self.interrupt_text) is not str or len(self.interrupt_text) > MAX_INTERRUPT_CHARS
                or any(ord(char) < 32 for char in self.interrupt_text)):
            raise ValueError("the repeat interrupt text must be one short visible line")


def repeat_signature(text):
    """V1's stable burst signature: one short line, no media placeholder.

    Platform adapters collapse every image-only message to the same visible
    placeholder, so those never accumulate under one signature. Trailing
    punctuation is dropped, because "哈哈" and "哈哈！" are the same repeat.
    """
    compact = clean_group_text(strip_address_markers(str(text or "")))
    if not compact or compact in MEDIA_PLACEHOLDERS:
        return ""
    compact = clean_group_text(TRAILING_MARKS.sub("", compact).strip())
    return compact if 1 <= len(compact) <= MAX_SIGNATURE_CHARS else ""


def repeat_burst(signature, items, *, at, window_seconds):
    """Count one same-content burst inside the window, oldest first.

    ``items`` are already-admitted group statements carrying ``text``,
    ``speaker_id`` and ``received_at``. Only visible material is counted, so a
    repeat outside the authorized window never accumulates, and the burst keeps
    no member identity beyond the transient speaker list used for counting.
    """
    if not signature:
        return None
    speakers, events, count, first, last, text = [], [], 0, None, None, ""
    for item in items:
        if item.get("kind") not in (None, "member"):
            continue  # only members repeat each other; the role's own copy is the act, not the burst
        raw = item.get("received_at")
        try:
            when = datetime.fromisoformat(raw) if type(raw) is str else None
        except ValueError:
            when = None
        if when is None or when > at or (at - when).total_seconds() > max(1, window_seconds):
            continue
        if repeat_signature(item.get("text")) != signature:
            speakers, events, count, first, last, text = [], [], 0, None, None, ""
            continue
        count += 1
        if item.get("event_id") is not None:
            events.append(item["event_id"])
        speaker = str(item.get("speaker_id") or "")
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        first = when if first is None or when < first else first
        last = when if last is None or when > last else last
        text = str(item.get("text") or "").strip() or text
    if not count:
        return None
    return {"version": REPEAT_VERSION, "signature": signature, "text": text[:MAX_SIGNATURE_CHARS],
            "count": count, "distinct": len(speakers), "speakers": tuple(speakers[:MAX_BURST_SPEAKERS]),
            "first_at": first, "last_at": last, "event_ids": tuple(events)}


def repeat_decision(policy, burst, *, acts=0, same_burst=False, cooled=True, used_today=0):
    """Decide whether the role joins, interrupts, or leaves this burst alone.

    V1 rolled ``random()`` against the follow/interrupt probabilities and raised
    both by one step whenever it did not act. V2 keeps the operator-visible
    tendencies and the step, but resolves them as a deterministic comparison so
    recovery and replay cannot change the outcome:

    * below the threshold, over the daily budget or inside the cooldown: no act;
    * first act in a burst: the larger tendency wins, a tie stays silent (V1's
      zero-probability case also stayed silent);
    * the burst keeps repeating after the role already joined it: the interrupt
      tendency is re-read with ``step * acts`` added, and it stops following
      once it reaches the follow tendency. A burst is never joined twice.
    """
    if not policy.enabled:
        return {"action": "", "reason": "repeat_disabled"}
    if burst is None or not burst.get("signature"):
        return {"action": "", "reason": "repeat_no_burst"}
    reached = burst["distinct"] if policy.distinct_users_only else burst["count"]
    if reached < policy.threshold:
        return {"action": "", "reason": "repeat_below_threshold"}
    if policy.max_daily <= 0 or used_today >= policy.max_daily:
        return {"action": "", "reason": "repeat_daily_cap"}
    if not same_burst and not cooled:
        return {"action": "", "reason": "repeat_cooldown"}
    follow = min(WEIGHT_CEILING, policy.follow_probability)
    interrupt = min(WEIGHT_CEILING, policy.interrupt_probability + policy.interrupt_step * max(0, acts))
    if acts:
        # The role already joined this burst and the group kept repeating it.
        if interrupt >= follow:
            return {"action": "interrupt", "text": policy.interrupt_text, "reason": "repeat_escalated"}
        return {"action": "", "reason": "repeat_already_joined"}
    if interrupt > follow:
        return {"action": "interrupt", "text": policy.interrupt_text, "reason": "repeat_interrupt_preferred"}
    if follow > interrupt:
        return {"action": "follow", "text": burst["text"], "reason": "repeat_follow"}
    return {"action": "", "reason": "repeat_weight_tie"}


def short_input_wait(event, text, values):
    if event.scope.kind is not ScopeKind.GROUP or any(getattr(event, field, ())
            for field in ("images", "quotes", "forwards", "audios", "videos")):
        return 0
    settings = group_wakeup_settings(values)
    if not settings["group_wakeup_enabled"]:
        return 0
    compact = "".join(strip_address_markers(text).split())
    if not 1 <= len(compact) <= 2 or compact[-1] in "?？。.!！~～…":
        return 0
    # All admitted fragments may need a follow-up; participation stays with the role.
    return settings["group_wakeup_short_text_wait_ms"] / 1000
