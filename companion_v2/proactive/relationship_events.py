"""Scheduled relationship contacts, with private text used only when a route asks for it."""

import re
from datetime import datetime, timedelta

from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.foundation.refs import ScopeKind

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


RELATIONSHIP_PURPOSES = ("affection", "care", "play", "repair", "invitation")

# One slot is one conversational opening. Give the model a purpose-specific
# direction instead of the same generic paragraph for every purpose, so a skip
# means "this specific opening is not natural", not "the template did not fit".
SLOT_PURPOSE_BRIEFS = {
    "affection": "关系窗口：可以表达一点具体的想念、亲近或撒娇；不把对方的沉默、旧情绪或未发生的事写成事实。",
    "care": "关系窗口：从角色自己注意到的小事或对方已知近况表达关心；不推断对方没吃饭、没睡好、生病或正在忙。",
    "play": "关系窗口：可以发起一个具体的轻松玩法、玩笑或小挑战；不假装已经一起做过，也不催促对方接梗。",
    "repair": "关系窗口：只有近期确有摩擦、误解或未收尾的话时才接住并修复；没有具体摩擦就换个独立切口或跳过。",
    "invitation": "关系窗口：可以提出一个具体、低压力的一起做某事；不写成已经约好，也不把邀约当成对方必须答应。",
}
SLOT_FALLBACK_BRIEF = ("关系窗口：由角色带来一个具体的小念头、观察或轻问题；不要求重大事件，"
                       "不把沉默、旧情绪或推测写成事实，没有合适内容就跳过。")

# Only needed to finish validating short-lived candidates written by older versions.
_LEGACY_PURPOSE_CUES = {
    "affection": re.compile(r"想你|喜欢你|爱你|在乎你|抱抱|miss you|love you|care about you", re.I),
    "care": re.compile(r"累了|疲惫|难过|伤心|焦虑|压力大|撑不住|tired|exhausted|sad|anxious", re.I),
    "play": re.compile(r"开个玩笑|讲个笑话|玩游戏|一起玩|好玩的|joke|play a game|let's play", re.I),
    "repair": re.compile(r"你刚才|你之前|刚刚那句|你说错|让我不舒服|别这样|stop doing|that hurt", re.I),
    "invitation": re.compile(r"一起.{0,12}(看|玩|去|做|吃|听)|我们.{0,12}(一起|下次)|let's (watch|play|go|try)", re.I),
}


class RelationshipContactEvents:
    """Offer enabled relationship purposes in a normal contact window."""

    def __init__(self, contacts, messages):
        self.contacts, self.messages = contacts, messages
        self.runtime = contacts.runtime

    def _text(self, context, event, at):
        if context.scope.kind is not ScopeKind.PRIVATE:
            return ""
        if any(getattr(event, name, ()) for name in ("images", "audios", "videos", "forwards", "quotes")):
            return ""
        source = self.messages._read_source(event.source, context, event.capability, at)
        if source.content_hash != event.input_hash or digest(source.payload) != source.content_hash:
            raise TaskError("contact_input_changed", "relationship candidate requires its original private text")
        return source.payload[:8192]

    def select(self, context, preferences, event, at, *, purpose=None, prompt_judgement=False):
        text = self._text(context, event, at)
        if not text or purpose not in RELATIONSHIP_PURPOSES or preferences.reason(purpose):
            return None
        opportunity = Opportunity("relationship-diagnostic-" + digest([context.key, event.scoped_key, purpose, stamp(at)]),
            context.scope, at, at + timedelta(minutes=4))
        return self._candidate(context, preferences, event, (purpose,), at, mode="source",
                               opportunity=opportunity, source=event.source)

    def prompt_candidates(self, context, preferences, event, at, *, opportunity=None):
        """Use the slot as the cause; one slot rotates through one enabled purpose.

        Opening five relationship purposes for one otherwise generic slot produced
        five near-identical model calls and five near-identical skipped records.
        The slot is one conversational opening, so choose one purpose by a stable
        seed and let the next slot rotate to another.
        """
        purposes = tuple(purpose for purpose in RELATIONSHIP_PURPOSES if not preferences.reason(purpose))
        if not purposes:
            return None
        seed = digest([context.key, opportunity.opportunity_id if opportunity is not None else "",
                       "relationship-slot-purpose"])
        chosen = purposes[int(seed[:8], 16) % len(purposes)]
        return self._candidate(context, preferences, event, (chosen,), at, mode="slot", opportunity=opportunity)

    def _candidate(self, context, preferences, event, purposes, at, *, mode, opportunity, source=None):
        if not purposes:
            return None
        if opportunity is None or not opportunity.opened_at <= at < opportunity.expires_at:
            return None
        task_id = self.contacts.task_id(context, opportunity)
        if self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,)) or self.runtime.read_one(
                "SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,)):
            return None
        marker = {"mode": mode, "purposes": list(purposes), "event_key": event.scoped_key,
                  "event_hash": event.input_hash, "expires_at": stamp(opportunity.expires_at)}
        summary = (SLOT_PURPOSE_BRIEFS.get(purposes[0], SLOT_FALLBACK_BRIEF)
                   if mode == "slot" else
                   "这是已确认私聊窗口中的关系主动联系机会；真实私聊只作背景，由角色自己发起自然表达，不当成刚收到的新输入，也不把用途标签当成事实。")
        sources = (source,) if source is not None else ()
        intents = tuple(ContactIntent(purpose, Thought(
            "relationship-thought-" + digest([marker, purpose]), opportunity, ThoughtKind.CONVERSATION,
            summary, EvidenceSnapshot(context.scope, at, sources), 1.0)) for purpose in purposes)
        return opportunity, intents, marker

    def validate(self, context, event, marker, at):
        if type(marker) is not dict:
            raise TaskError("contact_relationship_invalid", "relationship candidate marker is invalid")

        if set(marker) == {"purpose", "event_key", "event_hash", "expires_at"}:
            mode, purposes = "cue", (marker["purpose"],)
        elif set(marker) == {"mode", "purposes", "event_key", "event_hash", "expires_at"}:
            mode, purposes = marker["mode"], marker["purposes"]
            if (mode not in ("cue", "prompt", "slot", "source") or type(purposes) is not list
                    or not purposes or any(type(purpose) is not str for purpose in purposes)
                    or len(set(purposes)) != len(purposes)):
                raise TaskError("contact_relationship_invalid", "relationship candidate marker is invalid")
        else:
            raise TaskError("contact_relationship_invalid", "relationship candidate marker is invalid")
        if (any(type(purpose) is not str or purpose not in RELATIONSHIP_PURPOSES for purpose in purposes)
                or marker["event_key"] != event.scoped_key or not marker["expires_at"]):
            raise TaskError("contact_relationship_changed", "relationship candidate no longer matches its turn")
        if marker["event_hash"] != event.input_hash or event.received_at > at:
            raise TaskError("contact_relationship_changed", "relationship opportunity lost its original private turn")
        if mode in ("cue", "prompt"):
            text = self._text(context, event, at)
            if (digest(text) != marker["event_hash"]
                    or mode == "cue" and not any(_LEGACY_PURPOSE_CUES[purpose].search(text) for purpose in purposes)):
                raise TaskError("contact_relationship_changed", "legacy relationship evidence changed")
            preferences = require(self.contacts.preferences.read(context, observed_at=at))
            expires = event.received_at + timedelta(
                seconds=preferences.values["event_max_age_seconds"])
            if marker["expires_at"] != stamp(expires) or at >= expires:
                raise TaskError("contact_relationship_expired", "legacy relationship evidence is no longer current")
        else:
            try:
                expires = datetime.fromisoformat(marker["expires_at"])
            except (TypeError, ValueError):
                raise TaskError("contact_relationship_invalid", "relationship opportunity expiry is invalid")
            if stamp(expires) != marker["expires_at"] or at >= expires:
                raise TaskError("contact_relationship_expired", "relationship opportunity is no longer current")
            preferences = require(self.contacts.preferences.read(context, observed_at=at))
        if mode == "cue":
            preferences.require_contact(purposes[0])
        elif not any(preferences.reason(purpose) is None for purpose in purposes):
            raise TaskError("contact_relationship_expired", "no relationship purpose remains enabled")
