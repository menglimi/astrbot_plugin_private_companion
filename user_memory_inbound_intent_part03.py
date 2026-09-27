# -*- coding: utf-8 -*-
"""UserMemoryInboundIntentPart03Mixin。

由 tools/split_mixin_domain.py 从 user_memory_inbound_intent.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 364 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryInboundIntentMixin）。
"""
from __future__ import annotations

import re
from .companion_interaction_expression import current_interaction_projection
from .domains.affect.interaction_dynamics import settle_interaction_dynamics
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import logger
from typing import Any



class UserMemoryInboundIntentPart03Mixin:
    """UserMemoryInboundIntentPart03Mixin（从 UserMemoryInboundIntentMixin 拆出）。"""


    def _settle_current_interaction_from_intent(self, user: dict[str, Any], intent: dict[str, Any]) -> None:
        """Settle the short-term expression authority from one private-chat event.

        The legacy relationship-state projection is still maintained below for
        compatibility and diagnostics, but it no longer drives expression.
        """
        emotion_enabled = bool(runtime_persona_setting(self, "enable_emotion_simulation", True))
        relation_enabled = bool(runtime_persona_setting(self, "enable_relationship_state_machine", True))
        if not (emotion_enabled or relation_enabled):
            return
        now = _now_ts()
        role_getter = getattr(self, "_private_user_role", None)
        try:
            role = role_getter(user, str(user.get("user_id") or "")) if callable(role_getter) else str(user.get("relationship_role") or "friend")
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        relationship_mode = str(user.get("relationship_mode") or "normal")
        existing = current_interaction_projection(
            user.get("current_interaction"),
            relationship_role=role,
            relationship_mode=relationship_mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
            now=now,
        )
        inbound_intent = str(intent.get("intent") or "chat").strip().lower()
        intent_confidence = _safe_float(intent.get("confidence"), 0.5, 0.0)
        emotion_event = str(intent.get("emotion_event") or "neutral").strip().lower()
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), intent_confidence, 0.0)
        intensity = _safe_int(intent.get("emotion_intensity"), 0, 0, 100)
        target = _single_line(intent.get("emotion_target"), 24).lower() or "none"
        pressure = _safe_int(intent.get("pressure"), 0, 0, 5)
        hurt_threshold = _safe_int(runtime_persona_setting(self, "emotional_gate_hurt_threshold", 70), 70, 10, 100)
        avoidant_threshold = _safe_int(runtime_persona_setting(self, "emotional_gate_refuse_threshold", 90), 90, 20, 100)
        if avoidant_threshold <= hurt_threshold:
            avoidant_threshold = min(100, hurt_threshold + 5)
        recovery_per_hour = _safe_int(runtime_persona_setting(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60)
        max_hurt_minutes = _safe_int(runtime_persona_setting(self, "emotional_gate_max_hurt_minutes", 90), 90, 10, 720)
        boundary_durable = bool(intent.get("boundary_durable"))
        contact = user.get("contact_preference")
        contact_state = dict(contact) if isinstance(contact, dict) else {}
        contact_active = bool(
            contact_state.get("active")
            or contact_state.get("no_contact")
            or contact_state.get("backoff")
            or str(contact or "").strip().lower() in {"no_contact", "backoff", "avoid", "stop"}
        )
        boundary_event = relation_enabled and inbound_intent == "boundary" and boundary_durable and intent_confidence >= 0.82
        explicit_recovery = (
            (relation_enabled and inbound_intent in {"intimacy", "play"} and intent_confidence >= 0.68)
            or (emotion_enabled and emotion_event in {"apology", "comfort", "praise"} and emotion_confidence >= 0.65)
        )
        manual_override_active = bool(
            existing.get("manual_override")
            and (
                not existing.get("expires_at")
                or _safe_float(existing.get("expires_at"), 0) > now
            )
        )
        if boundary_event:
            contact_active = True
            user["contact_preference"] = {
                "mode": "no_contact",
                "active": True,
                "no_contact": True,
                "source": "automatic",
                "reason_code": "explicit_user_boundary",
                "updated_at": now,
            }
        elif manual_override_active:
            if contact_active and str(existing.get("expression_band") or "relaxed") != "avoidant":
                contact_active = False
                user["contact_preference"] = {
                    "mode": "normal",
                    "active": False,
                    "no_contact": False,
                    "backoff": False,
                    "source": "manual",
                    "reason_code": "manual_interaction_override_retained",
                    "updated_at": now,
                }
            event_recorder = getattr(self, "_record_interaction_emotion_event", None)
            if callable(event_recorder):
                event_recorder(
                    user, intent,
                    band=str(existing.get("expression_band") or "relaxed"),
                    reason_code="manual_override_retained",
                    status="ignored",
                    expires_at=_safe_float(existing.get("expires_at"), 0),
                )
            user["current_interaction"] = existing
            return
        elif contact_active and explicit_recovery:
            contact_active = False
            user["contact_preference"] = {
                "mode": "normal",
                "active": False,
                "no_contact": False,
                "source": "automatic",
                "reason_code": "explicit_user_reengagement",
                "updated_at": now,
            }

        band = "relaxed"
        expires_at = 0.0
        reason_code = "interaction_neutral"
        if contact_active:
            band = "avoidant"
            reason_code = "contact_boundary_active"
        elif (
            emotion_enabled
            and emotion_event in {"hurt", "boundary_violation"}
            and target in {"bot", "ambiguous"}
            and emotion_confidence >= 0.65
            and intensity >= hurt_threshold
        ):
            violation_severity = _safe_int(intent.get("violation_severity"), 1, 1, 3)
            if emotion_event == "boundary_violation":
                intensity = max(intensity, 58 + violation_severity * 14)
            band = "avoidant" if intensity >= avoidant_threshold or violation_severity >= 3 else "hurt"
            recovery_load = recovery_per_hour + max(0, intensity - hurt_threshold)
            recovery_minutes = max(10, (recovery_load * 60 + recovery_per_hour - 1) // recovery_per_hour)
            expires_at = now + min(max_hurt_minutes, recovery_minutes) * 60
            reason_code = "boundary_violation" if emotion_event == "boundary_violation" else ("severe_hurt_event" if band == "avoidant" else "hurt_event")
        elif relation_enabled and inbound_intent == "play" and intent_confidence >= 0.68:
            band = "lively"
            expires_at = now + 6 * 3600
            reason_code = "playful_interaction"
        elif relation_enabled and inbound_intent == "intimacy" and intent_confidence >= 0.68:
            band = "close" if role == "owner" and relationship_mode == "owner_exclusive" else "warm"
            expires_at = now + 6 * 3600
            reason_code = "intimate_interaction"
        elif emotion_enabled and emotion_event in {"apology", "comfort", "praise", "comfort_need", "external_negative"} and emotion_confidence >= 0.65:
            band = "lively" if emotion_event == "praise" else "warm"
            expires_at = now + (6 * 3600 if emotion_event in {"praise", "comfort"} else 4 * 3600)
            reason_code = f"emotion_{emotion_event}"
        elif relation_enabled and pressure >= 2 and intent_confidence >= 0.65:
            band = "relaxed"
            expires_at = now + 2 * 3600
            reason_code = "interaction_pressure"
        elif (
            existing.get("source") == "automatic"
            and _safe_float(existing.get("expires_at"), 0) > now
            and str(existing.get("expression_band") or "relaxed") != "relaxed"
        ):
            event_recorder = getattr(self, "_record_interaction_emotion_event", None)
            if callable(event_recorder):
                event_recorder(
                    user, intent,
                    band=str(existing.get("expression_band") or "relaxed"),
                    reason_code="active_interaction_retained",
                    status="ignored",
                    expires_at=_safe_float(existing.get("expires_at"), 0),
                )
            user["current_interaction"] = existing
            return

        dynamics: dict[str, Any] = {}
        dynamics_kind = emotion_event if emotion_event != "neutral" else inbound_intent
        prior_expires_at = _safe_float(existing.get("expires_at"), 0)
        if not contact_active and dynamics_kind in {"hurt", "apology", "comfort", "praise", "intimacy", "play"}:
            dynamics = settle_interaction_dynamics(
                existing,
                requested_band=band,
                event_kind=dynamics_kind,
                intensity=intensity or pressure * 20,
                now=now,
            )
            if dynamics:
                band = str(dynamics.get("expression_band") or band)
                hard_expires_at = expires_at
                try:
                    negative_dynamics = float(dynamics.get("polarity") or 0) < 0
                except (TypeError, ValueError):
                    negative_dynamics = False
                if negative_dynamics and prior_expires_at > now:
                    hard_expires_at = min(hard_expires_at, prior_expires_at) if hard_expires_at > 0 else prior_expires_at
                dynamic_expires_at = _safe_float(dynamics.get("expires_at"), hard_expires_at)
                if hard_expires_at > 0:
                    dynamics["hard_expires_at"] = hard_expires_at
                    dynamics["expires_at"] = min(dynamic_expires_at, hard_expires_at)
                    expires_at = hard_expires_at
                else:
                    expires_at = dynamic_expires_at

        event_recorder = getattr(self, "_record_interaction_emotion_event", None)
        emotion_event_record = event_recorder(
            user,
            intent,
            band=band,
            reason_code=reason_code,
            status="applied",
            expires_at=expires_at,
        ) if callable(event_recorder) else None
        interaction_payload = {
            "expression_band": band,
            "source": "automatic",
            "reason": reason_code,
            "updated_at": now,
            "expires_at": expires_at,
            "manual_override": False,
            "last_event_id": (emotion_event_record or {}).get("event_id", ""),
            "trace_id": (emotion_event_record or {}).get("trace_id", ""),
        }
        if dynamics:
            interaction_payload.update(dynamics)
        user["current_interaction"] = current_interaction_projection(
            interaction_payload,
            relationship_role=role,
            relationship_mode=relationship_mode,
            relationship_score=user.get("relationship_score"),
            normal_interaction_band_cap=runtime_persona_setting(self, "normal_interaction_band_cap", "warm"),
            now=now,
        )
        logger.info(
            "互动状态已统一结算: band=%s reason=%s expires=%s",
            band,
            reason_code,
            int(expires_at) if expires_at else 0,
        )

    def _update_relationship_state_from_intent(self, user: dict[str, Any], intent: dict[str, Any]) -> None:
        if not isinstance(intent, dict):
            return
        if not bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", True)):
            return
        # REQ-040: the seven-band interaction projection is the only durable
        # relationship-expression state.  Legacy relationship_state is not
        # produced or consumed any more.
        self._settle_current_interaction_from_intent(user, intent)
        user.pop("relationship_state", None)
        return

    def _remember_passive_reply_topic(self, user: dict[str, Any], text: str, inbound_text: str = "") -> None:
        if not runtime_persona_setting(self, "enable_passive_topic_suppression", True):
            return
        signature = self._proactive_topic_signature(text, inbound_text)
        if not signature:
            return
        recent = self._cleanup_recent_passive_topics(user)
        recent.append({"ts": _now_ts(), "signature": signature, "text": _single_line(text, 120)})
        del recent[:-18]

    @staticmethod
    def _music_album_reply_needs_disambiguation_fix(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        return any(
            token in compact
            for token in (
                "哪个专辑",
                "哪一个专辑",
                "我不太确定你说的是哪一个",
                "你说的是哪一个",
                "发到哪里",
                "私聊里还是群里",
            )
        )

    @staticmethod
    def _music_album_reply_from_context(context: dict[str, Any], *, user_text: str = "") -> str:
        album = _single_line(context.get("album"), 60)
        artist = _single_line(context.get("artist"), 40)
        platform = _single_line(context.get("platform"), 24)
        parts: list[str] = []
        if artist and album:
            parts.append(f"看到了，这是 {artist} 的《{album}》专辑。")
        elif album:
            parts.append(f"看到了，这张是《{album}》专辑。")
        elif artist:
            parts.append(f"看到了，这是 {artist} 的专辑卡。")
        else:
            parts.append("看到了，这是一张音乐专辑卡。")
        if platform:
            parts.append(f"来源是{platform}。")
        if re.search(r"(发|列|整理|曲目|歌单|几首歌)", str(user_text or "")):
            parts.append("如果你要，我可以直接把这张专辑的曲目列出来。")
        if re.search(r"(发到哪里|私聊|群里)", str(user_text or "")):
            parts.append("你要是愿意，也可以告诉我发到私聊还是群里。")
        else:
            parts.append("你要是愿意，我也可以直接帮你把曲目列出来。")
        return "".join(parts)

    def _smart_silence_trigger_reason(self, inbound_text: str) -> str:
        cleaned = _single_line(inbound_text, 260)
        if not cleaned:
            return ""
        compact = re.sub(r"\s+", "", cleaned)
        if not compact:
            return ""
        direct_markers = (
            "别聊这个",
            "不要聊这个",
            "不聊这个",
            "别说这个",
            "不要说这个",
            "别提这个",
            "不要提这个",
            "不想聊这个",
            "不想说这个",
            "不想继续",
            "别继续",
            "不要继续",
            "别问了",
            "不要问了",
            "别追问",
            "不要追问",
            "到此为止",
            "这个话题到此为止",
            "结束这个话题",
            "结束话题",
            "换个话题",
            "跳过这个",
            "略过这个",
            "打住",
            "停一下",
            "先别说了",
            "先不说了",
            "别说了",
            "不要回复",
            "不用回复",
            "别回了",
            "不必回复",
        )
        for marker in direct_markers:
            if marker in compact:
                return marker
        topic_patterns = (
            r"(这个|这件事|这事|这话|这个话题|这话题).{0,8}(算了|别聊|别说|别提|不聊|不说|不提|跳过|略过|到此为止)",
            r"(算了|够了|停|打住).{0,8}(别聊|别说|别问|别提|不聊|不说|不问|不提)",
            r"(别|不要|不用).{0,6}(安慰|解释|分析|劝|讲道理|追问)",
        )
        for pattern in topic_patterns:
            if re.search(pattern, compact):
                return "topic_boundary"
        return ""

    def _smart_silence_contextual_trigger_reason(
        self,
        inbound_text: str,
        response_text: str = "",
        *,
        session_kind: str = "",
    ) -> str:
        boundary = self._smart_silence_trigger_reason(inbound_text)
        if boundary:
            return boundary
        mode = str(runtime_persona_setting(self, "smart_silence_judge_mode", "boundary_only") or "boundary_only").strip().lower()
        if mode != "contextual":
            return ""
        inbound = _single_line(inbound_text, 260)
        response = _single_line(response_text, 600)
        compact = re.sub(r"\s+", "", inbound)
        response_compact = re.sub(r"\s+", "", response)
        if not compact or not response_compact:
            return ""
        if len(compact) <= 16 and re.fullmatch(r"(嗯+|恩+|哦+|噢+|喔+|行|好|好吧|可以|算了|没事|不用了|随便|先这样|就这样|知道了|了解了|收到|ok|OK|嗯嗯|啊这|呃|em+|额)", compact, flags=re.I):
            if re.search(r"(吗|呢|吧|要不要|需不需要|可以.*吗|要是|如果|我可以|我帮你|继续|再|还|解释|分析|建议|聊|说)", response_compact):
                return "short_disengage"
        if re.search(r"(算了|没事|不用了|先这样|就这样|不管了|随便吧|无所谓了)", compact):
            if re.search(r"(那我|我来|我帮|可以继续|继续|再说|要不要|需不需要|解释|分析|建议|追问|为什么|怎么)", response_compact):
                return "soft_disengage"
        if re.search(r"(困了|睡了|睡觉|去睡|先睡|晚安|下了|走了|忙去了|开会|上课|工作了|不方便)", compact):
            if re.search(r"(吗|呢|要不要|继续|再聊|我陪|我等|说说|聊聊|解释|分析|建议)", response_compact):
                return "leaving_or_busy"
        if session_kind == "group" and len(compact) <= 12 and re.fullmatch(r"(哈哈+|草+|笑死|乐|绷|6+|？+|\\?+|啊？|啥|什么鬼|不是吧|好家伙)", compact):
            if len(response_compact) >= 18 and re.search(r"(我觉得|可能|其实|要不|建议|可以|因为|所以|解释|分析)", response_compact):
                return "group_reaction_not_request"
        return ""
