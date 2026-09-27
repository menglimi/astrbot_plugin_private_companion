# -*- coding: utf-8 -*-
"""LlmToolActionsReactionCorePart03Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_core.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 379 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionCoreMixin）。
"""
from __future__ import annotations
from .llm_tool_actions_reaction_core_shared import Any
from .llm_tool_actions_reaction_core_shared import _now_ts
from .llm_tool_actions_reaction_core_shared import _safe_float
from .llm_tool_actions_reaction_core_shared import _safe_int
from .llm_tool_actions_reaction_core_shared import _single_line
from .llm_tool_actions_reaction_core_shared import ensure_reaction_expression_state
from .llm_tool_actions_reaction_core_shared import evaluate_reaction_expression_gate
from .llm_tool_actions_reaction_core_shared import normalize_reaction_expression_intent
from .llm_tool_actions_reaction_core_shared import random
from .llm_tool_actions_reaction_core_shared import re
from .llm_tool_actions_reaction_core_shared import reaction_expression_effective_probability
from .llm_tool_actions_reaction_core_shared import reaction_expression_high_frequency
from .llm_tool_actions_reaction_core_shared import reaction_expression_normalize_probability
from .llm_tool_actions_reaction_core_shared import reaction_expression_scope_state
from .llm_tool_actions_reaction_core_shared import runtime_persona_setting
from .llm_tool_actions_reaction_core_shared import uuid



class LlmToolActionsReactionCorePart03Mixin:
    """LlmToolActionsReactionCorePart03Mixin（从 LlmToolActionsReactionCoreMixin 拆出）。"""


    def _reaction_expression_local_fallback_intent(
        self,
        event: Any,
        visible_text: Any,
        authorization: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a conservative intent when the model omits the hidden tag.

        This reuses the inbound classifier state that already authorized the
        opportunity. Normal rates stay limited to high-confidence social or
        emotional turns; the explicit 100% mode also covers a plain social
        reply when the model omitted its optional tag.
        """
        if not isinstance(authorization, dict) or not authorization.get("authorized"):
            return {}
        if authorization.get("consumed"):
            return {}
        trigger_mode = _single_line(authorization.get("trigger_mode"), 40).casefold()
        high_frequency = bool(authorization.get("high_frequency_mode")) or reaction_expression_high_frequency(
            authorization.get(
                "configured_probability",
                runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2),
            )
        )
        allowed_modes = {"semantic_rule", "strong_emotion"}
        if high_frequency:
            # At 100% the probability gate has already granted the opportunity;
            # do not make delivery depend on the model remembering an optional
            # hidden tag. Boundary and feedback checks below still apply.
            allowed_modes.add("probability")
        if trigger_mode not in allowed_modes:
            return {}
        try:
            user_id = _single_line(
                authorization.get("user_id")
                or getattr(event, "get_sender_id", lambda: "")(),
                160,
            )
        except Exception:
            user_id = ""
        if not user_id:
            return {}
        profile = authorization.get("profile_snapshot")
        if not isinstance(profile, dict) or not profile:
            user = self._reaction_expression_state_owner(
                event,
                user_id,
                create=False,
            )
            if not isinstance(user, dict):
                user = None
            profile = user.get("intent_profile") if isinstance(user, dict) else None
        if not isinstance(profile, dict) or not profile:
            if not high_frequency:
                return {}
            context_text = _single_line(visible_text, 700)
            if not self._reaction_expression_has_visible_text(context_text):
                return {}
            return normalize_reaction_expression_intent(
                query="开心回应",
                context=context_text,
                purpose="日常回应",
                emotion="开心",
                intensity=2,
                candidate_queries=["开心回应", "轻松互动", "日常分享"],
                candidate_limit=_safe_int(
                    runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                    6,
                    1,
                    16,
                ),
            )
        profile_text = re.sub(r"\s+", "", _single_line(profile.get("text"), 500))
        current_text = re.sub(
            r"\s+",
            "",
            _single_line(getattr(event, "message_str", ""), 500),
        )
        if profile_text and (not current_text or profile_text != current_text):
            return {}
        intent_name = _single_line(profile.get("intent"), 32).casefold()
        emotion_event = _single_line(profile.get("emotion_event"), 40).casefold()
        emotion_target = _single_line(profile.get("emotion_target"), 40).casefold()
        confidence = max(
            _safe_float(profile.get("confidence"), 0.0, 0.0, 1.0),
            _safe_float(profile.get("emotion_confidence"), 0.0, 0.0, 1.0),
        )
        intensity = _safe_float(profile.get("emotion_intensity"), 0.0, 0.0, 100.0)
        confidence_floor = 0.62 if high_frequency else 0.72
        if confidence < confidence_floor or bool(profile.get("boundary_durable")):
            return {}
        if emotion_target not in {"", "self", "bot"}:
            return {}

        presets: dict[str, tuple[str, str, list[str]]] = {
            "play": ("接住玩笑", "轻松", ["轻松接梗", "开心吐槽", "无语摊手"]),
            "intimacy": ("回应亲近", "亲昵", ["害羞亲近", "撒娇回应", "温柔陪伴"]),
            "comfort": ("温柔安慰", "心疼", ["安慰陪伴", "温柔抱抱", "心疼安慰"]),
        }
        event_presets: dict[str, tuple[str, str, list[str]]] = {
            "praise": ("回应夸奖", "开心", ["开心被夸", "害羞开心", "收到夸奖"]),
            "apology": ("温和回应道歉", "温柔", ["温柔原谅", "轻轻安慰", "没关系"]),
            "comfort_need": ("接住低落", "温柔", ["安慰陪伴", "抱抱安慰", "温柔鼓励"]),
        }
        if emotion_event == "comfort" and emotion_target in {"", "bot"}:
            preset = (
                "回应安抚",
                "安心",
                ["被安慰后安心", "收到安抚", "温柔回应关心"],
            )
        else:
            preset = event_presets.get(emotion_event) or presets.get(intent_name)
            if not preset and high_frequency and intent_name in {
                "chat",
                "social",
                "conversation",
                "greeting",
            }:
                preset = (
                    "日常回应",
                    "开心",
                    ["开心回应", "轻松互动", "日常分享"],
                )
        if not preset:
            return {}
        purpose_text, emotion_text, candidates = preset
        level = max(0, min(5, int(round(intensity / 20.0))))
        if level <= 0:
            level = 2 if trigger_mode == "semantic_rule" else 3
        context_text = _single_line(visible_text, 700)
        inbound_text = _single_line(profile.get("text"), 240)
        if inbound_text and inbound_text not in context_text:
            context_text = _single_line(
                f"用户语境：{inbound_text}；回复正文：{context_text}",
                1000,
            )
        return normalize_reaction_expression_intent(
            query=candidates[0],
            context=context_text,
            purpose=purpose_text,
            emotion=emotion_text,
            intensity=level,
            candidate_queries=candidates,
            candidate_limit=_safe_int(
                runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6),
                6,
                1,
                16,
            ),
        )

    async def _preauthorize_reaction_expression_prompt(
        self, event: Any
    ) -> bool:
        now = _now_ts()
        existing = self._reaction_expression_authorization(event)
        if existing:
            if now > _safe_float(existing.get("expires_at"), 0.0):
                self._log_reaction_expression_event(
                    event,
                    stage="gate",
                    decision="deny",
                    reason="authorization_expired",
                    scope=existing.get("scope") or self._reaction_expression_scope(event),
                )
                return False
            return bool(existing.get("authorized") and not existing.get("consumed"))
        scope = self._reaction_expression_scope(event)
        authorization: dict[str, Any] = {
            "authorized": False,
            "reason": "experiment_disabled",
            "authorized_at": now,
            "expires_at": now + 600.0,
            "consumed": False,
            "model_omission_recorded": False,
            "scope": scope,
            "trace_id": self._reaction_expression_trace_id(event),
        }
        if not bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)):
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False
        if not self._reaction_image_provider_available():
            authorization["reason"] = "provider_unavailable"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False

        allowed = (
            bool(runtime_persona_setting(self, 'reaction_expression_private_enabled', True))
            if scope == "private"
            else bool(runtime_persona_setting(self, 'reaction_expression_group_enabled', False))
            if scope == "group"
            else False
        )
        if not allowed:
            authorization["reason"] = f"{scope}_disabled"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False
        try:
            user_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            user_id = ""
        if scope == "private" and user_id:
            user_id = self._reaction_expression_event_storage_id(event, user_id)
        if not user_id:
            authorization["reason"] = "missing_user"
            self._set_reaction_expression_authorization(event, authorization)
            self._log_reaction_expression_event(
                event,
                stage="gate",
                decision="deny",
                reason=authorization["reason"],
                scope=scope,
            )
            return False

        scope_key = self._reaction_expression_scope_key(event, user_id)
        configured_probability = reaction_expression_normalize_probability(
            runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2),
            0.2,
        )
        cooldown = _safe_float(
            runtime_persona_setting(self, 'reaction_expression_cooldown_seconds', 180),
            180.0,
            0.0,
            86400.0,
        )
        async with self._data_lock:
            user = self._reaction_expression_state_owner(event, user_id)
            if not isinstance(user, dict):
                return False
            state = ensure_reaction_expression_state(user)
            scoped_state = reaction_expression_scope_state(state, scope_key)
            probability = reaction_expression_effective_probability(
                state, configured_probability
            )
            swing_probability = getattr(self, "_swing_probability", None)
            if callable(swing_probability):
                probability = swing_probability(probability, user=user)
            trigger = self._reaction_expression_local_trigger(
                event,
                user,
                configured_probability=configured_probability,
                scope_key=scope_key,
            )
            gate_probability = (
                1.0 if bool(trigger.get("bypass_probability")) else probability
            )
            if trigger.get("mode") == "explicit_opt_out":
                gate = {"allowed": False, "reason": "explicit_opt_out", "probability": gate_probability}
            else:
                semantic_offer_cooldown = (
                    min(60.0, cooldown) if cooldown > 0 and trigger.get("bypass_probability") else 0.0
                )
                last_offer_at = _safe_float(scoped_state.get("last_offer_at"), 0.0)
                if (
                    semantic_offer_cooldown > 0
                    and last_offer_at > 0
                    and now - last_offer_at < semantic_offer_cooldown
                ):
                    gate = {
                        "allowed": False,
                        "reason": "semantic_cooldown",
                        "probability": gate_probability,
                    }
                else:
                    gate = evaluate_reaction_expression_gate(
                        scoped_state,
                        {"signature": ""},
                        now=now,
                        probability=gate_probability,
                        cooldown_seconds=cooldown,
                        # Avoid drawing random state for deterministic semantic tiers;
                        # this keeps ordinary probability behavior and testability
                        # unchanged while making the bypass explicit in diagnostics.
                        random_value=random.random() if gate_probability < 1.0 else 0.0,
                    )
        authorization.update(
            {
                "authorized": bool(gate.get("allowed")),
                "reason": _single_line(gate.get("reason"), 80) or "gate",
                "user_id": user_id,
                "scope": scope,
                "scope_key": scope_key,
                "nonce": uuid.uuid4().hex,
                "configured_probability": configured_probability,
                "effective_probability": probability,
                "high_frequency_mode": reaction_expression_high_frequency(
                    configured_probability
                ),
                "gate_probability": gate_probability,
                "trigger_mode": _single_line(trigger.get("mode"), 40) or "probability",
                "trigger_reason": _single_line(trigger.get("reason"), 80),
                "trigger_source": _single_line(trigger.get("source"), 80),
                "trigger_confidence": _safe_float(
                    trigger.get("confidence"), 0.0, 0.0, 1.0
                ),
                "profile_snapshot": (
                    dict(trigger.get("profile_snapshot"))
                    if isinstance(trigger.get("profile_snapshot"), dict)
                    else {}
                ),
            }
        )
        self._set_reaction_expression_authorization(event, authorization)
        if authorization["authorized"]:
            if authorization.get("trigger_mode") in {"semantic_rule", "strong_emotion"}:
                async with self._data_lock:
                    user = self._reaction_expression_state_owner(event, user_id)
                    if not isinstance(user, dict):
                        return bool(authorization["authorized"])
                    state = ensure_reaction_expression_state(user)
                    reaction_expression_scope_state(state, scope_key)["last_offer_at"] = now
                    self._persist_reaction_expression_state(
                        sections={"reaction_expression_group_states"}
                        if scope == "group"
                        else {"users"}
                    )
            self._note_reaction_expression_runtime(offers=1, last_reason="offered")
        self._note_reaction_expression_runtime(
            trigger_mode=authorization.get("trigger_mode"),
            last_reason=authorization.get("trigger_reason") or authorization.get("reason"),
        )
        self._log_reaction_expression_event(
            event,
            stage="gate",
            decision="allow" if authorization["authorized"] else "deny",
            reason=authorization["reason"],
            scope=scope,
            configured_probability=configured_probability,
            effective_probability=gate_probability,
            cooldown_seconds=cooldown,
            trigger_mode=trigger.get("mode"),
            trigger_confidence=trigger.get("confidence"),
        )
        return bool(authorization["authorized"])

    def _consume_reaction_expression_authorization(
        self,
        event: Any,
        *,
        user_id: str,
        scope_key: str,
    ) -> tuple[bool, str]:
        authorization = self._reaction_expression_authorization(event)
        if not authorization:
            return False, "not_preauthorized"
        reason = _single_line(authorization.get("reason"), 80) or "not_preauthorized"
        if not authorization.get("authorized"):
            return False, reason
        if authorization.get("consumed"):
            return False, "authorization_consumed"
        if _now_ts() > _safe_float(authorization.get("expires_at"), 0.0):
            return False, "authorization_expired"
        if _single_line(authorization.get("user_id"), 160) != user_id:
            return False, "authorization_user_mismatch"
        if _single_line(authorization.get("scope_key"), 240) != scope_key:
            return False, "authorization_scope_mismatch"
        authorization["consumed"] = True
        self._set_reaction_expression_authorization(event, authorization)
        return True, "authorized"
