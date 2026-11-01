"""Post-draft persona judgement: the model may only allow or withhold one contact.

V1 reviewed a planned proactive message against the role's persona and worldview
with a model judge that had its own threshold, cache and daily call allowance. V2
keeps that judgement where the draft already exists: after the sealed body passed
the presentation guards and before anything is committed or queued.

The judge runs as its own supervised background task, so its model call has its
own request, its own usage record and its own receipt; the contact's own model
decision stays the single generation that produced the body. The judgement only
answers "may this leave". It cannot rewrite the body, change the audience, the
identity, the sources or the authorization of the contact it judges, and an
unavailable, failed or unknown judgement is not a pass: the contact is withheld
and the reason is recorded on the contact it withheld.
"""

from dataclasses import dataclass
from datetime import timedelta
import json

from companion_v2.conversation.foreground import (BackgroundPromptTrigger, PromptMaterial,
                                                  PromptRequest, SealedForegroundContext)
from companion_v2.conversation.private_history import INSTRUCTION as DIALOGUE_INSTRUCTION
from companion_v2.delivery.history import CONTEXT_INSTRUCTION
from companion_v2.execution.models import digest, encode, stamp

from .configuration import GROUP, PURPOSES, judge_exempt_purposes
from .prompts import TOPIC_CONTINUITY_INSTRUCTION, evidence_materials, history_materials


KIND = "proactive.judge"
# The host owns the selectable output formats and reusing the contact format keeps
# the judgement on the same JSON-only contract; the judge prompt is distinguished by
# its own material purpose and by the judge task that issues it.
OUTPUT_FORMAT = "proactive_contact"
DRAFT_PURPOSE = "proactive_draft"
DECISIONS = ("send", "drop")
INSTRUCTION = """现在不是要你再写一条消息，而是替这个角色做一次发送前的自检。
上面的人格就是判定标准：检查这条消息是否符合当前人格、世界观、关系边界和此刻语境。自然的小感想、轻问候、好奇或独立话题本身就能成立，不要求重大新事件或用户先表达同等意愿；没有具体矛盾或越界时，允许角色主动开口。
若提供 role_state（角色此刻的精力、作息、忙碌或心情状态），同时检查这条主动消息的语气与精神头是否同角色当前状态自洽。
它是否重复、是否过度解读对方、是否宣称未发生的事、是否越过授权，都属于判定范围。
结合提供的近期问答和实际已发消息，按时间辨明谁说过、做过什么，用户是否已经回答或展示过，后文是否有纠正、转向或收尾。重点判断这次开口是否接得上，而不只是单句像不像角色；缺少历史不等于之前没聊过，也不补猜附件内容或用户反应。
重复看意思和进展，不按相同词语或话题出现次数判定。常用口吻可以保留，不因单个语气词或熟悉的话题就拦下。
未回复不等于拒绝，已完成的一轮普通问答也不等于一个未获回应的主动邀约；经过合适间隔的独立内容可以放行，不把沉默本身当作不适合联系的证据。明确要求停止、短时间连续追问同一邀约或重复索取评价时仍应拦下。
如果最近一条已送达的消息是角色自己的回复、之后没有新的用户输入，这次主动应是沉默后的独立开口：正文若以“哦？/那/所以/照这么说/接着”等承接词开头、复述或续写那条回复、把同一条旧梗当新内容继续，应 drop；除非它提供了新的、可核对的信息或明确的新角度。
contact_evidence 是本次机会经核验的来源材料，thought 只是候选话头，不证明内容已经发生或用户希望听。依据来源实际支持的范围检查正文：计划时间结束只说明预定时段已过，不证明执行、完成作品或取得新进展；不把角色自己写下的计划、心愿和旧回复当作完成回执或用户意愿。
你只能选择放行或拦下，不能改写正文、不能更换受众、不能补写事实，也不要输出正文。
只输出 JSON，不添加 Markdown 围栏：
{"decision":"send","score":85,"reason":"一句短理由"} 或 {"decision":"drop","score":20,"reason":"一句短理由"}。
score 是 0 到 100 的整数，表示你对这个判断的把握与符合程度；理由里不要暴露检查过程。
角色状态只用于检查语气是否自洽，不要求正文复述状态。生活分享、轻巧关心和自然收尾本身可以成立；若正文主要是连续汇报角色自身状态，结合近期实际已发内容判断是否还有交流价值，而不是见到生活细节就拦下。把普通关心写成对用户的饮食、作息、健康训诫时应 drop。
涉及用户具体习惯或身体事实的断言（例如「你又/总是/老是……」后接某种饮食、作息或健康习惯）必须有材料直接支持；人格里的关心健康、监督饮食不是证据，无法核对时 drop。
"""


@dataclass(frozen=True)
class JudgeVerdict:
    """One bounded judgement; ``model_call_id`` is empty for a cached answer.

    ``pending`` marks the state between the sealed draft and the model's answer:
    the contact exists, its body is final, and nothing may leave until the
    judgement resolves.
    """

    decision: str
    score: int
    reason: str
    signature: str
    allowed: bool
    cached: bool = False
    profile: str = KIND
    model_call_id: str = ""
    failure: str = ""
    pending: bool = False
    task_id: str = ""

    def document(self):
        return {"state": "pending" if self.pending else "resolved", "signature": self.signature,
                "decision": self.decision or None, "score": self.score, "reason": self.reason,
                "allowed": self.allowed, "cached": self.cached, "profile": self.profile,
                "model_call_id": self.model_call_id or None, "failure": self.failure or None,
                "task_id": self.task_id or None}


def judge_request(job):
    """Review the finished body against its verified conversation, without rewriting."""
    draft = {
        "purpose": PURPOSES[job["purpose"]], "purpose_id": job["purpose"],
        "draft_body": job["body"], "contact_now": job["at"],
        "draft_decision_confidence": job["confidence"],
        "preference_note": job["note"]}
    if job.get("role_state"):
        draft["role_state"] = job["role_state"]
    if job.get("thought"):
        draft["thought"] = job["thought"]
    materials = [PromptMaterial(GROUP, DRAFT_PURPOSE, json.dumps(draft, ensure_ascii=False))]
    materials.extend(evidence_materials(job.get("records", ())))
    materials.extend(history_materials(job.get("history", ()), dialogue=job.get("dialogue", ()),
                                       last_reply=job.get("last_reply")))
    trigger = BackgroundPromptTrigger(job["cause_id"], job["context"].actor.principal,
                                      job["context"].scope, job["observed_at"])
    return PromptRequest(trigger, job["role_instruction"],
                         SealedForegroundContext(tuple(materials), (), ()),
                         INSTRUCTION + "\n" + TOPIC_CONTINUITY_INSTRUCTION +
                         "\n" + DIALOGUE_INSTRUCTION + "\n" + CONTEXT_INSTRUCTION,
                         job["context"].role_binding, output_format=OUTPUT_FORMAT)


def judge_output(text):
    """A strictly bounded judgement; anything else is an unusable answer."""
    data = json.loads(text)
    allowed = {"decision", "score", "reason"}
    if (not isinstance(data, dict) or set(data) != allowed
            or data["decision"] not in DECISIONS or type(data["score"]) is not int
            or not 0 <= data["score"] <= 100
            or type(data["reason"]) is not str or not 1 <= len(data["reason"].strip()) <= 200):
        raise ValueError("judgement must use its explicit decision, score and reason contract")
    return data["decision"], data["score"], data["reason"].strip()


def signature_for(purpose, body, *, review_context):
    return "judge-" + digest([purpose, body, review_context])


def exempt_purposes(preferences):
    return judge_exempt_purposes(preferences.values["proactive_persona_judge_exempt_purposes"])


class PersonaJudge:
    """Cache and daily allowance for the judged contacts, read from existing records."""

    def __init__(self, runtime):
        self.runtime = runtime

    def cached(self, context, signature, at, *, ttl_minutes, threshold):
        if ttl_minutes <= 0:
            return None
        row = self.runtime.read_one("""SELECT record_json FROM proactive_contacts
            WHERE origin_key = ? AND created_at >= ?
            AND json_extract(record_json, '$.judge.signature') = ?
            ORDER BY created_at DESC, contact_id DESC LIMIT 1""",
            (context.key, stamp(at - timedelta(minutes=ttl_minutes)), signature))
        if row is None:
            return None
        try:
            previous = json.loads(row["record_json"])["judge"]
        except (KeyError, TypeError, ValueError):
            return None
        if (not isinstance(previous, dict) or previous.get("failure")
                or previous.get("decision") not in DECISIONS or type(previous.get("score")) is not int):
            return None
        return JudgeVerdict(previous["decision"], previous["score"], previous.get("reason", ""), signature,
                            previous["decision"] == "send" and previous["score"] >= threshold, cached=True)

    @staticmethod
    def denied(signature, failure, *, reason):
        return JudgeVerdict("drop", 0, reason, signature, False, failure=failure)

    @staticmethod
    def pending(signature, task_id):
        return JudgeVerdict("", 0, "已封存草稿，等待主动人格判定", signature, False,
                            pending=True, task_id=task_id)

    def withheld(self, origin_key, contact_id, failure, *, reason):
        """Mark one unresolved draft as withheld, without touching its sealed body.

        Used when a judgement can no longer be completed: an unknown judgement is
        never a pass, so the draft stays archived as a skip and is not delivered
        or replayed.
        """
        with self.runtime.transaction() as connection:
            row = connection.execute("""SELECT origin_key, record_json FROM proactive_contacts
                WHERE contact_id = ?""", (contact_id,)).fetchone()
            if row is None or row["origin_key"] != origin_key:
                return False
            data = json.loads(row["record_json"])
            if data.get("decision") != "judge_pending":
                return False
            data["decision"] = "skip"
            data["judge"] = {**(data.get("judge") or {}), "state": "resolved", "decision": "drop",
                             "score": 0, "allowed": False, "reason": reason, "failure": failure,
                             "cached": False}
            connection.execute("UPDATE proactive_contacts SET record_json = ? WHERE contact_id = ?",
                               (encode(data), contact_id))
        return True
