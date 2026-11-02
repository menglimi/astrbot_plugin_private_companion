"""Proposed language-style observations with explicit evidence references."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import unicodedata
from typing import Mapping

from .rule_details import FORM_SCHEMA, normalize as normalize_form


DIMENSIONS = frozenset({
    "sentence_rhythm",
    "emoji",
    "punctuation",
    "directness",
    "diction",
    "formatting",
    "other",
})
CHANNELS = ("private", "group", "proactive", "qzone", "tts")
RELATIONSHIP_STAGES = ("stranger", "familiar", "close", "any")
EMOTION_GATES = ("normal", "positive", "low", "guarded", "any")
INTENTS = frozenset({
    "acknowledgement", "question", "request", "help", "comfort", "play", "tease",
    "intimacy", "boundary", "emotion", "casual", "proactive", "any",
})
APPLICABILITY_FIELDS = frozenset({
    "channels", "relationship_stages", "emotion_gates", "intent", "keywords",
})
MAX_KEYWORDS = 8
MAX_KEYWORD_CHARS = 32
MAX_SIGNALS_PER_RUN = 12
MAX_SUMMARY_CHARS = 500
MAX_OBSERVATION_CHARS = 800
MAX_PROPOSAL_CHARS = 1200

# One run asks for a whole candidate set at once, and every candidate carries an
# observation, a proposal, an applicability map and a concrete expression form.
# Measured answers (2026-10-30): summary plus three short candidates and their
# JSON envelope occupied 1098-1318 output tokens, i.e. 300-370 tokens per
# candidate. The ceiling such a request executes under is
# ``model.role.expression.learn.max_output_tokens``, which defaults to the
# reply-sized ceiling of the same window, and a thinking model spends part of it
# on its own reasoning before it writes one visible character (Gemini 3 counts
# thinking inside max_output_tokens). Asking for the product maximum regardless
# of that ceiling is how one run ended with a 123-character body while ~1968
# tokens of its ceiling never became visible text, so the count is derived here
# instead of fixed.
LEARNING_SIGNAL_TOKENS = 400
LEARNING_SUMMARY_TOKENS = 150
LEARNING_ANSWER_SHARE = (2, 3)
CHARS_PER_TOKEN = (9, 5)
MODE_SIGNAL_CAPS = {"light": 3, "balanced": 6, "aggressive": 12}


def signal_limit(mode, max_output_tokens):
    """How many complete candidates fit in the ceiling this run can use.

    Keeps the answer inside ``LEARNING_ANSWER_SHARE`` of the ceiling so the
    model's own reasoning keeps the remainder, and never exceeds the product cap
    of the mode. A ceiling too small for even one candidate still asks for one:
    the mode's own evidence rules decide whether it is worth persisting.
    """
    cap = MODE_SIGNAL_CAPS[mode]
    if type(max_output_tokens) is not int or max_output_tokens < 1:
        return 1
    share, total = LEARNING_ANSWER_SHARE
    usable = max_output_tokens * share // total - LEARNING_SUMMARY_TOKENS
    return max(1, min(cap, usable // LEARNING_SIGNAL_TOKENS))


def text_budget(limit):
    """The total character budget the same estimate pays for.

    Measured answers run 1.88-1.96 characters per output token; 1.8 keeps the
    written bound in the same place as the token estimate it comes from.
    """
    characters, tokens = CHARS_PER_TOKEN
    estimated = limit * LEARNING_SIGNAL_TOKENS + LEARNING_SUMMARY_TOKENS
    return estimated * characters // tokens


def candidate_signature(dimension, observation, proposal, applicability=None, expression=None):
    """Normalize formatting differences without collapsing distinct rule meaning."""
    normalize = lambda value: " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    checked = normalize_applicability(applicability)
    return (normalize(dimension), normalize(observation), normalize(proposal),
            json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            tuple((name, normalize(value)) for name, value in sorted(normalize_form(expression).items()))
            if expression is not None else None)


def form_identity(expression):
    """Identify the same concrete form without equating different situations.

    Observation, wording advice and confidence may improve with new evidence;
    they do not make the same situation/pattern a new expression rule.
    """
    if expression is None:
        return None
    form = normalize_form(expression)
    compact = lambda value: " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return form["kind"], compact(form["situation"]), compact(form["pattern"])


def normalize_applicability(raw=None, *, default_channels=None):
    """Return the bounded V1-compatible applicability contract for one rule."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping) or set(raw) - APPLICABILITY_FIELDS:
        raise ValueError("invalid_applicability")

    channels_default = CHANNELS if default_channels is None else tuple(default_channels)
    if (not channels_default or any(type(value) is not str or value not in CHANNELS
                                    for value in channels_default)):
        raise ValueError("invalid_applicability")

    def values(field, choices, fallback, *, maximum):
        value = raw.get(field)
        if value is None or value == []:
            return list(fallback)
        if (type(value) is not list or not value or len(value) > maximum
                or any(type(item) is not str or item not in choices for item in value)
                or len(set(value)) != len(value)
                or "any" in value and len(value) != 1):
            raise ValueError("invalid_applicability")
        return sorted(value, key=choices.index)

    channels = values("channels", CHANNELS, channels_default, maximum=len(CHANNELS))
    stages = values("relationship_stages", RELATIONSHIP_STAGES, ("any",), maximum=len(RELATIONSHIP_STAGES))
    emotions = values("emotion_gates", EMOTION_GATES, ("any",), maximum=len(EMOTION_GATES))
    intent = raw.get("intent", "any")
    if type(intent) is not str or intent not in INTENTS:
        raise ValueError("invalid_applicability")

    keywords = raw.get("keywords", [])
    if (type(keywords) is not list or len(keywords) > MAX_KEYWORDS
            or any(type(value) is not str for value in keywords)):
        raise ValueError("invalid_applicability")
    accepted, seen = [], set()
    unsafe_markers = ("ignore previous", "system prompt", "developer message", "忽略之前", "系统提示词", "调用工具")
    for value in keywords:
        keyword = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
        key = keyword.casefold()
        if (not keyword or len(keyword) > MAX_KEYWORD_CHARS or key in seen
                or any(marker in key for marker in unsafe_markers)):
            raise ValueError("invalid_applicability")
        seen.add(key)
        accepted.append(keyword)
    return {"channels": channels, "relationship_stages": stages, "emotion_gates": emotions,
            "intent": intent, "keywords": accepted}


def applicability_matches_channel(applicability, channel):
    if type(channel) is not str or channel not in CHANNELS:
        return False
    try:
        checked = normalize_applicability(applicability)
    except (TypeError, ValueError):
        return False
    return channel in checked["channels"]


def laya_intent_matches(applicability, decision):
    """Use only explicit Laya outcomes; unknown outcomes do not satisfy narrow rules."""
    try:
        checked = normalize_applicability(applicability)
    except (TypeError, ValueError):
        return False
    target = checked["intent"]
    if target == "any":
        return True
    if not isinstance(decision, Mapping):
        return False
    reply_intent = decision.get("reply_intent")
    reply_style = decision.get("reply_style")
    compatible = {
        "answer": {"question", "request", "help"},
        "acknowledge": {"acknowledgement"},
        "ask": {"question"},
        "comfort": {"comfort", "emotion"},
        "explain": {"help"},
        "summarize": set(),
    }.get(reply_intent, set())
    if reply_style == "playful":
        compatible = compatible | {"play", "tease", "intimacy", "casual"}
    return target in compatible

INSTRUCTION = """只观察可借鉴的表达形式：句长、节奏、标点、措辞、直接程度和排版，捕捉长期相处中自然耳濡目染的呼吸节拍、软化语气的小助词或熟稔接话方式。区分明确反馈与偶然写法，不从少量样本推断身份、性格、情绪、健康、关系或稳定偏好；证据不足就不提候选，一次说法不概括为长期习惯。
每项引用输入中的真实 evidence_handle，写清可复用形式及使用条件；proposal 写成角色能以自身性格底色自然化用的表达习惯，而非机械复读；这里只提出待审核候选，不宣称已学习或保存。
填写 applicability：channels 选 private/group/proactive/qzone/tts，必须包含来源频道；relationship_stages 选 stranger/familiar/close/any，emotion_gates 选 normal/positive/low/guarded/any，intent 选 acknowledgement/question/request/help/comfort/play/tease/intimacy/boundary/emotion/casual/proactive/any，keywords 最多 8 个简短情境词。其他条件依据不足时用 any 或空关键词，不猜身份、关系或心情。条件是建议的使用范围，不是事实声明，应用时缺失不等于匹配。
每条候选都必须填写 expression：kind=style 时 pattern 是能被自然改写的具体说法；kind=grammar 时 pattern 是可复用的句法结构。observation 和 expression.situation 说明证据发生的情境，不要用“口语化、自然、简短”代替实际模板；语法习惯不要伪装成口头禅。实在没有具体形式时返回空 signals，不要省略 expression 字段凑候选。
仅输出符合给定 JSON Schema 的 JSON，不附推理过程。"""

FORM_INSTRUCTION = """每条候选填写 expression：kind=style 是具体情境表达，pattern 保留有辨识度的短说法或带占位的说法，例如在彼此已有共识、不需解释时用“懂的都懂”；kind=grammar 是可复用句法，pattern 写出结构，例如“先用两三个字确认，再省略主语接一句补充”。situation 说明何时合适，avoid 说明何时别用，proposal 说明角色怎样自然化用。
不要把“更自然、简短口语化、温柔一些”这样的泛泛评价当作学到的表达。短而有辨识度的原话可以保留，长句、私人经历和身份信息不照搬；一次出现可成为局部情境候选，但不要宣称是长期习惯。样本、情境、说法、用法须对应，不能编造证据。对照 existing_rules 的情境与模板：同一说法、同一用法即使 observation 或 proposal 有新措辞也不要另提候选；已拒绝或已停用的规则也不要擅自复活。同一情境下不同的说法，以及相同说法在真正不同情境中的用法可以共存。"""

MODE_GUIDANCE = {
    "light": "轻量学习：只提炼跨情境通用的句长、停顿呼吸感、标点和排版习惯，最多提出{limit}条。不要提取场景短语或口头禅。",
    "balanced": "均衡学习：兼顾稳定语法习惯和有重复证据的情境表达与接话节奏，最多提出{limit}条；保留角色自己的表达底色，自然化用而不模仿用户身份。",
    "aggressive": "深入学习：可提出最多{limit}条细分的情境表达、熟稔语气和句法模板，明确使用条件；不因强度提高而降低证据要求。",
}
BUDGET_INSTRUCTION = "本次调用可用输出预算只够 {limit} 条：summary 与全部候选的文字合计不超过 {characters} 字，条数以这条为上限，宁可少提也不凑满。"


def mode_instruction(mode, limit):
    """One mode's guidance with the candidate count this run can afford."""
    return MODE_GUIDANCE[mode].format(limit=limit) + "\n" + BUDGET_INSTRUCTION.format(
        limit=limit, characters=text_budget(limit))


def schema(limit=MAX_SIGNALS_PER_RUN):
    """The exact output contract for one run, bounded to the derived count."""
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "maxLength": MAX_SUMMARY_CHARS},
            "signals": {
                "type": "array",
                "maxItems": limit,
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension": {"type": "string", "enum": sorted(DIMENSIONS)},
                        "observation": {"type": "string", "maxLength": MAX_OBSERVATION_CHARS},
                        "proposal": {"type": "string", "maxLength": MAX_PROPOSAL_CHARS},
                        "applicability": {
                            "type": "object",
                            "properties": {
                                "channels": {"type": "array", "items": {"type": "string", "enum": list(CHANNELS)},
                                             "minItems": 1, "maxItems": len(CHANNELS)},
                                "relationship_stages": {"type": "array", "items": {"type": "string", "enum": list(RELATIONSHIP_STAGES)},
                                                        "minItems": 1, "maxItems": len(RELATIONSHIP_STAGES)},
                                "emotion_gates": {"type": "array", "items": {"type": "string", "enum": list(EMOTION_GATES)},
                                                  "minItems": 1, "maxItems": len(EMOTION_GATES)},
                                "intent": {"type": "string", "enum": sorted(INTENTS)},
                                "keywords": {"type": "array", "maxItems": MAX_KEYWORDS,
                                             "items": {"type": "string", "maxLength": MAX_KEYWORD_CHARS}},
                            },
                            "required": sorted(APPLICABILITY_FIELDS),
                            "additionalProperties": False,
                        },
                        "expression": FORM_SCHEMA,
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence_handles": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["dimension", "observation", "proposal", "applicability", "expression", "confidence", "evidence_handles"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "signals"],
        "additionalProperties": False,
    }


SCHEMA = schema()


@dataclass(frozen=True)
class StyleSignal:
    dimension: str
    observation: str
    proposal: str
    applicability: dict
    confidence: float
    evidence_handles: tuple[str, ...]
    expression: dict | None = None


@dataclass(frozen=True)
class ExpressionLearningOutput:
    summary: str
    signals: tuple[StyleSignal, ...]


def parse_output(raw: object, *, allowed_evidence: set[str] | frozenset[str], default_channels=None) -> ExpressionLearningOutput:
    """Validate a model-authored candidate; never persists or activates the result."""
    if type(raw) is str:
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("invalid_json") from error
    if not isinstance(raw, Mapping) or set(raw) != {"summary", "signals"}:
        raise ValueError("invalid_shape")
    summary, signals = raw["summary"], raw["signals"]
    if (type(summary) is not str or len(summary) > MAX_SUMMARY_CHARS
            or not isinstance(signals, list) or len(signals) > MAX_SIGNALS_PER_RUN):
        raise ValueError("invalid_shape")
    accepted = []
    for item in signals:
        required = {"dimension", "observation", "proposal", "confidence", "evidence_handles", "expression"}
        if not isinstance(item, Mapping) or not required <= set(item) or set(item) - required - {"applicability"}:
            raise ValueError("invalid_signal")
        dimension = item["dimension"]
        observation, proposal = item["observation"], item["proposal"]
        confidence, references = item["confidence"], item["evidence_handles"]
        if (dimension not in DIMENSIONS or type(observation) is not str or not observation.strip()
                or len(observation) > MAX_OBSERVATION_CHARS):
            raise ValueError("invalid_signal")
        if type(proposal) is not str or not proposal.strip() or len(proposal) > MAX_PROPOSAL_CHARS:
            raise ValueError("invalid_signal")
        try:
            applicability = normalize_applicability(item.get("applicability"), default_channels=default_channels)
        except (TypeError, ValueError):
            raise ValueError("invalid_applicability") from None
        if default_channels is not None:
            source_channels = set(default_channels)
            applicability["channels"] = sorted(set(applicability["channels"]) | source_channels,
                                               key=CHANNELS.index)
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid_confidence")
        if (not isinstance(references, list) or not references or
                any(type(reference) is not str or reference not in allowed_evidence for reference in references) or
                len(set(references)) != len(references)):
            raise ValueError("invalid_evidence")
        try:
            expression = normalize_form(item["expression"])
        except (TypeError, ValueError):
            raise ValueError("invalid_expression_form") from None
        accepted.append(StyleSignal(dimension, observation.strip(), proposal.strip(), applicability, float(confidence),
                                    tuple(references), expression))
    return ExpressionLearningOutput(summary.strip(), tuple(accepted))
