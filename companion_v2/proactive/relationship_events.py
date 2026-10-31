"""Scheduled relationship contacts, with private text used only when a route asks for it."""

import re
from datetime import datetime, timedelta

from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.foundation.refs import ScopeKind

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


RELATIONSHIP_PURPOSES = ("affection", "care", "play", "repair", "invitation")

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
        """Use the slot as the cause; recent wording is not a prerequisite for reaching out."""
        purposes = tuple(purpose for purpose in RELATIONSHIP_PURPOSES if not preferences.reason(purpose))
        return self._candidate(context, preferences, event, purposes, at, mode="slot", opportunity=opportunity)

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
        summary = ("这是一次按私聊节律开放的关系主动联系窗口；联系目的只表示表达方向，不表示用户最近说过对应的话。"
                   "由角色带来自己的具体感想、亲近表达、轻问题或邀请，不需要等用户先表达同样的情绪，也不需要先有重大事件。"
                   "结合关系距离与近期交流决定怎样开口；候选不合适时可换个同样符合本轮目的的切口，确实重复或越界时再跳过。"
                   "不得把沉默、旧情绪、推测的感受或未发生的约定写成事实。"
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
