# -*- coding: utf-8 -*-
"""LlmToolActionsReactionCorePart02Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_core.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 343 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionCoreMixin）。
"""
from __future__ import annotations

from .llm_tool_actions_reaction_core_shared import (
    _REACTION_LOG_DECISIONS,
    _REACTION_LOG_DELIVERY_CODES,
    _REACTION_LOG_MATCH_BASES,
    _REACTION_LOG_REASONS,
    _REACTION_LOG_STAGES,
    _REACTION_LOG_STATUSES,
    _REACTION_LOG_TRIGGER_MODES,
)
from .llm_tool_actions_reaction_core_shared import Any
from .llm_tool_actions_reaction_core_shared import _safe_float
from .llm_tool_actions_reaction_core_shared import _safe_int
from .llm_tool_actions_reaction_core_shared import _single_line
from .llm_tool_actions_reaction_core_shared import ensure_reaction_expression_state
from .llm_tool_actions_reaction_core_shared import hashlib
from .llm_tool_actions_reaction_core_shared import json
from .llm_tool_actions_reaction_core_shared import logger
from .llm_tool_actions_reaction_core_shared import re
from .llm_tool_actions_reaction_core_shared import reaction_expression_auto_disabled
from .llm_tool_actions_reaction_core_shared import reaction_expression_explicit_opt_out
from .llm_tool_actions_reaction_core_shared import reaction_expression_explicit_request
from .llm_tool_actions_reaction_core_shared import reaction_expression_normalize_probability
from .llm_tool_actions_reaction_core_shared import runtime_persona_setting
from .llm_tool_actions_reaction_core_shared import uuid



class LlmToolActionsReactionCorePart02Mixin:
    """LlmToolActionsReactionCorePart02Mixin（从 LlmToolActionsReactionCoreMixin 拆出）。"""


    @staticmethod
    def _set_reaction_expression_authorization(
        event: Any, authorization: dict[str, Any]
    ) -> None:
        try:
            setattr(
                event,
                "_private_companion_reaction_expression_authorization",
                authorization,
            )
        except Exception:
            pass
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter(
                    "private_companion_reaction_expression_authorization",
                    authorization,
                )
            except Exception:
                pass

    def _reaction_expression_trace_id(self, event: Any) -> str:
        authorization = self._reaction_expression_authorization(event)
        trace_id = _single_line(
            authorization.get("trace_id") or authorization.get("nonce"),
            12,
        ).casefold()
        if not re.fullmatch(r"[0-9a-f]{12}", trace_id):
            trace_id = _single_line(
                getattr(
                    event,
                    "_private_companion_reaction_expression_trace_id",
                    "",
                ),
                12,
            ).casefold()
        if not re.fullmatch(r"[0-9a-f]{12}", trace_id):
            trace_id = uuid.uuid4().hex[:12]
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_trace_id",
                    trace_id,
                )
            except Exception:
                pass
        return trace_id

    def _log_reaction_expression_event(
        self,
        event: Any,
        *,
        stage: str,
        decision: str,
        trace_id: Any = "",
        reason: Any = "",
        scope: Any = "",
        status: Any = "",
        found: bool | None = None,
        sent: bool | None = None,
        image_id: Any = "",
        confidence: Any = None,
        cache_hit: bool | None = None,
        latency_ms: Any = None,
        delivery: Any = "",
        match_basis: Any = "",
        error_type: Any = "",
        feedback_signal: Any = "",
        feedback_score: Any = None,
        trigger_mode: Any = "",
        trigger_confidence: Any = None,
        configured_probability: Any = None,
        effective_probability: Any = None,
        cooldown_seconds: Any = None,
    ) -> None:
        """Write one privacy-safe, correlation-friendly reaction runtime event."""

        def safe_code(value: Any, allowed: frozenset[str]) -> str:
            normalized = _single_line(value, 80).casefold()
            if not normalized:
                return ""
            return normalized if normalized in allowed else "other"

        try:
            normalized_trace_id = _single_line(trace_id, 12).casefold()
            if not re.fullmatch(r"[0-9a-f]{12}", normalized_trace_id):
                normalized_trace_id = self._reaction_expression_trace_id(event)
            payload: dict[str, Any] = {
                "trace_id": normalized_trace_id,
                "stage": safe_code(stage, _REACTION_LOG_STAGES) or "decision",
                "decision": safe_code(decision, _REACTION_LOG_DECISIONS) or "skip",
            }
            for key, value, allowed in (
                ("status", status, _REACTION_LOG_STATUSES),
                ("reason", reason, _REACTION_LOG_REASONS),
                ("delivery", delivery, _REACTION_LOG_DELIVERY_CODES),
                ("match_basis", match_basis, _REACTION_LOG_MATCH_BASES),
            ):
                normalized = safe_code(value, allowed)
                if normalized:
                    payload[key] = normalized
            normalized_scope = safe_code(
                scope,
                frozenset({"private", "group", "unknown"}),
            )
            if normalized_scope:
                payload["scope"] = normalized_scope
            normalized_image_id = _single_line(image_id, 160)
            if normalized_image_id:
                if re.fullmatch(
                    r"pc-local:[0-9a-f]{16,64}",
                    normalized_image_id,
                    flags=re.I,
                ):
                    payload["asset_ref"] = normalized_image_id
                else:
                    payload["asset_ref"] = hashlib.sha256(
                        normalized_image_id.encode("utf-8", errors="replace")
                    ).hexdigest()[:12]
            normalized_error_type = _single_line(error_type, 80)
            if normalized_error_type and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]{0,79}",
                normalized_error_type,
            ):
                payload["error_type"] = normalized_error_type
            normalized_feedback_signal = safe_code(
                feedback_signal,
                frozenset({"positive", "negative", "neutral"}),
            )
            if normalized_feedback_signal:
                payload["feedback_signal"] = normalized_feedback_signal
            normalized_trigger_mode = safe_code(
                trigger_mode,
                _REACTION_LOG_TRIGGER_MODES,
            )
            if normalized_trigger_mode:
                payload["trigger_mode"] = normalized_trigger_mode
            for key, value in (
                ("found", found),
                ("sent", sent),
                ("cache_hit", cache_hit),
            ):
                if value is not None:
                    payload[key] = bool(value)
            if feedback_score is not None:
                payload["feedback_score"] = max(
                    -20,
                    min(20, _safe_int(feedback_score, 0, -20, 20)),
                )
            for key, value, maximum, digits in (
                ("confidence", confidence, 1.0, 3),
                ("latency_ms", latency_ms, 3_600_000.0, 2),
                ("configured_probability", configured_probability, 1.0, 4),
                ("effective_probability", effective_probability, 1.0, 4),
                ("cooldown_seconds", cooldown_seconds, 86_400.0, 2),
            ):
                if value is not None:
                    payload[key] = round(
                        _safe_float(value, 0.0, 0.0, maximum),
                        digits,
                    )
            logger.info(
                "[ReactionExpression] %s",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            # Diagnostics must never alter the reply or delivery path.
            return

    @staticmethod
    def _reaction_expression_match_basis(lookup: Any) -> str:
        if not isinstance(lookup, dict):
            return ""
        provider = _single_line(lookup.get("provider"), 80)
        image_id = _single_line(lookup.get("image_id"), 160)
        if provider == "private_companion_library" or image_id.startswith("pc-local:"):
            return "tags_emotions_intents"
        return "provider_score" if lookup.get("success") else ""

    def _reaction_expression_local_trigger(
        self,
        event: Any,
        user: dict[str, Any],
        *,
        configured_probability: float,
        scope_key: str = "",
    ) -> dict[str, Any]:
        """Resolve a local trigger layer without another model pass.

        The inbound pipeline already maintains a small intent/emotion profile.
        Reuse only high-confidence, non-boundary signals here.  A configured
        probability of zero remains an explicit opt-out; cooldown and duplicate
        protection are still enforced by ``evaluate_reaction_expression_gate``.
        """
        base_probability = reaction_expression_normalize_probability(
            configured_probability,
            0.2,
        )
        default = {
            "mode": "probability",
            "reason": "random_offer",
            "source": "configured_probability",
            "confidence": 0.0,
            "bypass_probability": False,
        }
        if not isinstance(user, dict):
            return default
        inbound_text = ""
        try:
            inbound_text = _single_line(getattr(event, "message_str", ""), 500)
        except Exception:
            inbound_text = ""
        if reaction_expression_explicit_opt_out(inbound_text):
            return {
                "mode": "explicit_opt_out",
                "reason": "explicit_opt_out",
                "source": "user_message",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        state = ensure_reaction_expression_state(user)
        # Keep an explicit user boundary across turns. A direct request for a
        # particular reaction image is still allowed and handled by the tool
        # path; it does not silently re-enable automatic attachments.
        auto_disabled = reaction_expression_auto_disabled(state, scope_key)
        if auto_disabled and not reaction_expression_explicit_request(inbound_text):
            return {
                "mode": "explicit_opt_out",
                "reason": "explicit_opt_out_persisted",
                "source": "user_preference",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        if auto_disabled and reaction_expression_explicit_request(inbound_text):
            # Explicit requests use the ordinary tool path for this turn but
            # do not erase the persisted automatic-attachment boundary.
            return {
                "mode": "explicit_request",
                "reason": "explicit_request",
                "source": "user_message",
                "confidence": 1.0,
                "bypass_probability": False,
            }
        if base_probability <= 0:
            return default
        if not bool(runtime_persona_setting(self, 'reaction_expression_semantic_trigger_enabled', True)):
            return default

        profile = user.get("intent_profile")
        if not isinstance(profile, dict) or not profile:
            return default
        profile_text = re.sub(r"\s+", "", _single_line(profile.get("text"), 500))
        current_text = re.sub(r"\s+", "", inbound_text)
        if profile_text and (not current_text or profile_text != current_text):
            return default
        intent = _single_line(profile.get("intent"), 32).casefold()
        emotion_event = _single_line(profile.get("emotion_event"), 40).casefold()
        emotion_target = _single_line(profile.get("emotion_target"), 40).casefold()
        source = _single_line(profile.get("source"), 48).casefold()
        confidence = max(
            _safe_float(profile.get("confidence"), 0.0, 0.0, 1.0),
            _safe_float(profile.get("emotion_confidence"), 0.0, 0.0, 1.0),
        )
        intensity = _safe_float(profile.get("emotion_intensity"), 0.0, 0.0, 100.0)
        profile_snapshot = {
            "intent": intent,
            "emotion_event": emotion_event,
            "emotion_target": emotion_target,
            "emotion_intensity": intensity,
            "confidence": _safe_float(
                profile.get("confidence"), 0.0, 0.0, 1.0
            ),
            "emotion_confidence": _safe_float(
                profile.get("emotion_confidence"), 0.0, 0.0, 1.0
            ),
            "source": source,
            "boundary_durable": bool(profile.get("boundary_durable")),
            "text": _single_line(profile.get("text"), 240),
        }
        semantic_bypass_blocked = bool(
            profile_snapshot["boundary_durable"]
            or intent in {"boundary", "help", "task", "code", "search", "empty"}
            or source
            in {
                "diagnostic_skip",
                "durable_boundary_rule",
                "single_turn_boundary",
                "weak_boundary_ignored",
            }
            or emotion_event in {"hurt", "external_negative"}
        )
        preference = state.get("preference")
        preference_score = (
            _safe_int(preference.get("score"), 0, -20, 20)
            if isinstance(preference, dict)
            else 0
        )

        # A negative reaction history should not be overridden by a semantic
        # shortcut.  It still participates in the ordinary probability path.
        if preference_score < 0:
            return {
                "mode": "feedback_bias",
                "reason": "negative_feedback_respect",
                "source": "feedback_preference",
                "confidence": 0.0,
                "bypass_probability": False,
                "profile_snapshot": profile_snapshot,
            }

        # These are existing local classifier outcomes, not a second model
        # judgement.  Deliberately exclude hurt/boundary/diagnostic signals so
        # a tense conversation does not receive an unwanted reaction image.
        positive_events = {"comfort_need", "comfort", "praise", "apology"}
        positive_intents = {"play", "intimacy"}
        target_allows_event = emotion_target in {"", "self", "bot"}
        if emotion_event == "praise" and emotion_target not in {"", "bot"}:
            target_allows_event = False
        strong_event = (
            emotion_event in positive_events
            and target_allows_event
            and intensity >= 38
            and confidence >= 0.62
        )
        strong_intent = intent in positive_intents and confidence >= 0.72
        if not semantic_bypass_blocked and (strong_event or strong_intent):
            return {
                "mode": "strong_emotion" if strong_event else "semantic_rule",
                "reason": "local_emotion_signal" if strong_event else "local_intent_signal",
                "source": source or ("emotion_event" if strong_event else "intent"),
                "confidence": round(confidence, 3),
                "bypass_probability": True,
                "profile_snapshot": profile_snapshot,
            }

        if preference_score > 0:
            return {
                "mode": "feedback_bias",
                "reason": "positive_feedback_bias",
                "source": "feedback_preference",
                "confidence": min(1.0, preference_score / 10.0),
                "bypass_probability": False,
                "profile_snapshot": profile_snapshot,
            }
        default["profile_snapshot"] = profile_snapshot
        return default
