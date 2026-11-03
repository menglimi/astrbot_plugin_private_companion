"""AstrBot's public decision API, shared by Laya and proactive review."""

import asyncio
from collections.abc import Mapping
import json
import logging
import math

from companion_v2.decision.client import LayaClientError
from companion_v2.decision import proactive
from companion_v2.conversation.foreground import ModelDraft
from companion_v2.execution.models import ProviderBinding, TaskError, digest
from companion_v2.model_gateway.models import (
    Amount, LocalProvider, MAX_MODEL_OUTPUT_TOKENS, ProviderResponse,
)

logger = logging.getLogger(__name__)


def _types():
    try:
        from astrbot.api import provider
        return provider.DecisionChoiceQuestion, provider.DecisionScoreQuestion, provider.DecisionChoiceAnswer, provider.DecisionScoreAnswer
    except (ImportError, AttributeError) as error:
        raise LayaClientError("astrbot_decision_unavailable", "当前 AstrBot 未提供官方判断接口") from error


def decision_catalog(context):
    """Only loaded decision providers; never read or expose provider credentials."""
    if not callable(getattr(getattr(context, "decision", None), "ask", None)):
        return []
    manager = getattr(context, "provider_manager", None)
    result = []
    for native in getattr(manager, "decision_provider_insts", ()) or ():
        try:
            meta = native.meta()
            kind = getattr(meta.provider_type, "value", meta.provider_type)
            if kind != "decision" or not callable(getattr(native, "ask", None)):
                continue
            identifier = meta.id
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            result.append({"id": identifier, "name": identifier,
                           "model": str(getattr(meta, "model", "") or "")})
        except (AttributeError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda item: item["id"])


def decision_status(context):
    return {"astrbot_available": callable(getattr(getattr(context, "decision", None), "ask", None)),
            "astrbot_providers": decision_catalog(context)}


def _probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _distribution(value, keys):
    if (not isinstance(value, Mapping) or set(value) != set(keys)
            or not all(_probability(p) for p in value.values())
            or abs(sum(value.values()) - 1) > 1e-3):
        raise ValueError("invalid decision distribution")
    return dict(value)


async def ask(context, questions, state, *, provider_id=""):
    """Convert only supported typed questions and verify the host's typed answers."""
    choice_type, score_type, choice_answer, score_answer = _types()
    service = getattr(context, "decision", None)
    if not callable(getattr(service, "ask", None)):
        raise LayaClientError("astrbot_decision_unavailable", "当前 AstrBot 未提供官方判断接口")
    typed = {}
    for name, question in questions.items():
        if question["type"] == "choice":
            typed[name] = choice_type(instructions=question["instructions"], criteria=question["criteria"])
        elif question["type"] == "score":
            typed[name] = score_type(instructions=question["instructions"], criteria=list(question["criteria"]))
        else:
            raise LayaClientError("laya_contract_invalid", "不支持的判断题型")
    try:
        answers = await service.ask(typed, state=state, provider_id=provider_id or None)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Provider exceptions can include upstream URLs or credentials.
        raise LayaClientError("astrbot_decision_failed", "AstrBot 判断调用失败，请查看宿主日志") from error
    try:
        if not isinstance(answers, Mapping) or set(answers) != set(questions):
            raise ValueError("missing decision answers")
        result = {}
        for name, question in questions.items():
            answer = answers[name]
            if not _probability(answer.confidence):
                raise ValueError("invalid decision confidence")
            if question["type"] == "choice":
                if not isinstance(answer, choice_answer):
                    raise ValueError("wrong decision answer type")
                probabilities = _distribution(answer.probabilities, question["criteria"])
                if (answer.choice not in probabilities or not _probability(answer.probability)
                        or abs(probabilities[answer.choice] - answer.probability) > 1e-3
                        or answer.probability + 1e-3 < max(probabilities.values())):
                    raise ValueError("invalid selected answer")
                result[name] = {"type": "choice", "choice": answer.choice,
                                "probabilities": probabilities, "answer_confidence": answer.probability,
                                "confidence": answer.confidence}
            else:
                if not isinstance(answer, score_answer):
                    raise ValueError("wrong decision answer type")
                legend = {str(i): text for i, text in enumerate(question["criteria"])}
                probabilities = _distribution(answer.probabilities, legend)
                expected = sum(int(i) * p for i, p in probabilities.items())
                if (dict(answer.legend) != legend or type(answer.score) not in (int, float)
                        or not math.isfinite(answer.score) or abs(answer.score - expected) > 1e-3):
                    raise ValueError("invalid weighted score")
                result[name] = {"type": "score", "score": answer.score,
                                "probabilities": probabilities, "confidence": answer.confidence}
        return result
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise LayaClientError("laya_contract_invalid", "AstrBot 判断结果不符合本次问题契约") from error


class AstrBotDecisionModel:
    """A task-restricted gateway adapter. The host API reports no token receipt."""

    def __init__(self, context, provider_id):
        self.context, self.provider_id = context, provider_id
        self.native = context.get_provider_by_id(provider_id)
        self.model = str(self.native.meta().model or "")
        self.binding = ProviderBinding("model.decision", provider_id, "1", self._fingerprint())

    def _fingerprint(self):
        return digest([self.provider_id, self.native.meta().model, getattr(self.native, "provider_config", {})])

    def _current(self):
        if (self.context.get_provider_by_id(self.provider_id) is not self.native
                or self._fingerprint() != self.binding.generation):
            raise TaskError("host_provider_changed", "the selected decision provider changed")

    def local_provider(self):
        return LocalProvider(self.binding, ("text", "decision"), MAX_MODEL_OUTPUT_TOKENS,
            self.reserve, self.generate, transport="astrbot_decision", model=self.model,
            task_kinds=("proactive.judge",), supports_hard_budget=False)

    def reserve(self, envelope):
        self._current()
        state = proactive.state_for(envelope)
        size = len(json.dumps({"state": state, "questions": proactive.questions()}, ensure_ascii=False).encode())
        if size > 131072:
            raise TaskError("model_input_limit", "decision review materials exceed the local input bound")
        return Amount(size + envelope.max_output_tokens, 0)

    async def generate(self, envelope):
        self._current()
        state = proactive.state_for(envelope)
        if envelope.before_dispatch:
            envelope.before_dispatch()
        self._current()
        answers = await ask(self.context, proactive.questions(), state, provider_id=self.provider_id)
        self._current()
        # No invented zero usage or synthetic provider receipt. The gateway keeps
        # the completed result and explicitly unconfirmed accounting separately.
        return ProviderResponse("generated", draft=ModelDraft(proactive.output(answers), ()))

    async def probe(self):
        self._current()
        await ask(self.context, {"probe": {"type": "choice", "instructions": "Select ready.",
            "criteria": {"ready": "Ready", "other": "Other"}}}, "Connection test", provider_id=self.provider_id)
        self._current()


def assemble_decision_providers(context):
    adapters = []
    for item in decision_catalog(context):
        try:
            adapters.append(AstrBotDecisionModel(context, item["id"]))
        except (AttributeError, TypeError, ValueError, TaskError) as error:
            # A provider being removed must not prevent ordinary text startup.
            logger.warning("AstrBot decision adapter unavailable error_type=%s", type(error).__name__)
    return tuple(adapters)
