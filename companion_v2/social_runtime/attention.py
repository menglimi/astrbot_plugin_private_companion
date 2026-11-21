"""One explainable salience function for replies and memory intake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re


QUESTION = re.compile(r"[?？]|(?:吗|么|呢|如何|怎么|为什么|啥|哪|谁|何时|几点)[。！!\s]*$")
STAKE = re.compile(r"求助|帮我|难过|伤心|生病|医院|考试|面试|生日|纪念日|吵架|分手|危险|害怕|对不起")
EMOTION = re.compile(r"[!！]{2,}|哭|开心|激动|担心|焦虑|委屈|喜欢|讨厌|想你")
STABLE = re.compile(
    r"我(?:是|在|有|要|会|喜欢|不喜欢|正在|准备|平时|通常|经常|从不|"
    r"确实不(?:喝|吃|抽|用)|不(?:喝|吃|抽|用))|以后|一直|最近|今天|明天|下周|记住|别忘"
)
ADDRESS = re.compile(r"^\s*@|(?:你|妳|您|你们)[，,:：\s]*(?:能|会|觉得|知道|帮)|"
                     r"^\s*(?:记住|帮我|告诉我|查一下|看看|改成|撤回|删除|回复|回答|解释|请你|请帮)")


@dataclass(frozen=True)
class AttentionDecision:
    action: str
    score: float
    reason: str
    due_at: datetime | None = None


def salience(text: str, *, private: bool, previous_topic: str = "", recent_load: int = 0,
             interruption: float = 0.0, reciprocity: bool = False, media: bool = False) -> tuple[float, dict]:
    value = " ".join(str(text or "").split())[:2000]
    addressed = private or bool(ADDRESS.search(value))
    question = bool(QUESTION.search(value))
    stake = bool(STAKE.search(value))
    novelty = bool(value and (len(value) >= 8 or STABLE.search(value)) and value != previous_topic)
    emotion = bool(EMOTION.search(value))
    parts = {"addressed": addressed, "question": question, "stake": stake,
             "novelty": novelty, "emotion": emotion, "reciprocity": reciprocity}
    score = (.30 * addressed + .20 * question + .15 * stake + .15 * novelty
             + .10 * emotion + .10 * reciprocity - .25 * min(1.0, max(0.0, interruption))
             - .20 * min(1.0, max(0, recent_load) / 8))
    if media:
        score = max(score, .45 if private else .35)
    return round(max(0.0, min(1.0, score)), 3), parts


def decide(text: str, *, private: bool, now: datetime, previous_topic: str = "",
           last_reply_at: datetime | None = None, recent_load: int = 0,
           interruption_seconds: float = 0.0, reciprocity: bool = False,
           media: bool = False) -> AttentionDecision:
    score, parts = salience(text, private=private, previous_topic=previous_topic,
        recent_load=recent_load, interruption=min(1.0, interruption_seconds / 300),
        reciprocity=reciprocity, media=media)
    if interruption_seconds > 0 and (private or score >= .4):
        return AttentionDecision("defer", score, "occupied", now + timedelta(seconds=interruption_seconds))
    recent_reply = last_reply_at is not None and now - last_reply_at < timedelta(minutes=2)
    if private:
        if recent_reply and score < .46 and not parts["question"] and not parts["stake"] and not media:
            return AttentionDecision("observe", score, "recent_exchange")
        return AttentionDecision("respond_now", score, "direct_contact")
    if parts["question"] or parts["addressed"] or parts["stake"] or media:
        return AttentionDecision("respond_now", score, "addressed_or_significant")
    if recent_reply and (text.strip() == previous_topic or recent_load >= 3):
        return AttentionDecision("observe", score, "conversation_has_room")
    if recent_load >= 6 and parts["novelty"]:
        return AttentionDecision("remember_only", score, "attention_budget")
    if recent_load >= 8 and not parts["novelty"]:
        return AttentionDecision("ignore", score, "repeated_low_signal")
    return AttentionDecision("respond_now", score, "available")


def memory_worthy(text: str) -> bool:
    """Small talk stays in the transcript; durable extraction needs a signal."""
    value = " ".join(str(text or "").split())
    score, parts = salience(value, private=False)
    return bool(parts["stake"] or parts["novelty"] or parts["emotion"] or score >= .25)
