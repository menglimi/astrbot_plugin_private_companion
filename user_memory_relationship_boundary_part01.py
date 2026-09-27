# -*- coding: utf-8 -*-
"""UserMemoryRelationshipBoundaryPart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_relationship_boundary.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 441 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryRelationshipBoundaryMixin）。
"""
from __future__ import annotations

import json
import math
import re
from .conversation_prompt_section import prompt_section
from .domains.affect.emotion_targeting import classify_emotion_target
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import _render_user_memory_background_prompt, logger
from datetime import datetime
from typing import Any



class UserMemoryRelationshipBoundaryPart01Mixin:
    """UserMemoryRelationshipBoundaryPart01Mixin（从 UserMemoryRelationshipBoundaryMixin 拆出）。"""


    def _classify_relationship_emotion_event(self, text: str, intent_context: dict[str, Any] | None = None) -> dict[str, Any]:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return {"event": "neutral", "intensity": 0, "reason": "", "target": "none", "rule": "", "confidence": 1.0}
        if self._is_structured_or_diagnostic_text(cleaned):
            return {"event": "neutral", "intensity": 0, "reason": "结构化/日志/代码类文本不作为情绪依据", "target": "none", "rule": "diagnostic_skip", "confidence": 0.2}
        attribution = classify_emotion_target(cleaned)
        if attribution["target"] == "self":
            return {"event": "comfort_need", "intensity": 62, "reason": "用户自我否定或低落", "target": "self", "rule": "self_low", "confidence": attribution["confidence"], "attribution": attribution}
        if attribution["speech_act"] in {"quote", "third_party_report"}:
            return {"event": "external_negative", "intensity": 54, "reason": "引用或第三方负面内容", "target": "other", "rule": attribution["reason_code"], "confidence": attribution["confidence"], "attribution": attribution}
        atrelay_checker = getattr(self, "_message_looks_like_atrelay_request", None)
        if callable(atrelay_checker):
            try:
                if atrelay_checker(cleaned):
                    return {"event": "neutral", "intensity": 0, "reason": "转述/带话请求不作为 Bot 自身情绪依据", "target": "other", "rule": "atrelay_skip", "confidence": 0.86}
            except Exception:
                pass
        lower = cleaned.lower()
        intent_source = str((intent_context or {}).get("source") or "")
        boundary_durable = bool((intent_context or {}).get("boundary_durable"))
        playful_or_ambiguous = bool((intent_context or {}).get("playful_or_ambiguous"))
        strong_single_turn_abuse = bool(
            attribution.get("target") == "bot"
            and re.search(r"(恶心|废物|垃圾|没用|工具人|假的|别装|别演).{0,10}(闭嘴|滚)|你.{0,8}(恶心|废物|垃圾|没用).{0,8}(闭嘴|滚)", cleaned)
        )
        if playful_or_ambiguous or intent_source in {"soft_boundary_play_rule", "weak_boundary_ignored"} or (
            intent_source == "single_turn_boundary" and not strong_single_turn_abuse
        ):
            return {"event": "neutral", "intensity": 0, "reason": "玩笑或单句边界不作为情绪余波依据", "target": "none", "rule": "playful_or_single_boundary", "confidence": 0.8}
        target_hint, third_party_hint = self._intent_target_hint(cleaned)
        self_low = bool(re.search(r"(我好|我真|我太|我是不是|我就是|我是).{0,12}(废物|垃圾|没用|傻|笨|恶心|讨厌)", cleaned))
        direct_bot_negative = bool(
            re.search(r"(讨厌你|烦你|不想理你|你.{0,4}(滚|闭嘴)|你(?:真|也|就是|是|太|真的|这个|怎么这么|为什么这么).{0,8}(恶心|废物|垃圾|没用|太吵|打扰|烦死|吵死))", cleaned)
            or re.search(r"((bot|机器人|插件|助手|ai|AI).{0,8}(垃圾|废物|恶心|没用)|(垃圾|废物|恶心|没用).{0,8}(bot|机器人|插件|助手|ai|AI))", cleaned)
        )
        severe_hurt = (
            "滚" in cleaned
            or "闭嘴" in cleaned
            or "恶心" in cleaned
            or "废物" in cleaned
            or "垃圾" in cleaned
            or "讨厌你" in cleaned
            or "烦你" in cleaned
            or "不想理你" in cleaned
            or re.search(r"(只是|不过|不就是).{0,6}(bot|机器人|工具|代码)", lower)
        )
        if severe_hurt and not attribution["auto_settle"]:
            return {"event": "neutral", "intensity": 0, "reason": "负面目标不明确，等待复核", "target": attribution["target"], "rule": attribution["reason_code"], "confidence": attribution["confidence"], "attribution": attribution}
        identity_hurt = bool(
            re.search(r"(玻璃心|假装|演的|装的|设定|工具人|没感情|别装|别演|虚拟的|假的)", cleaned)
            and target_hint
        )
        mild_hurt = bool(
            re.search(r"(太烦|吵死|烦死|没用|笨死|傻)", cleaned)
            and target_hint
        )
        apology = bool(
            re.search(r"(对不起|抱歉|我错了|不是故意|原谅|别生气|别难过|哄哄|哄你)", cleaned)
            and not re.search(r"(对不起有用|道歉有用|抱歉有用|对不起没用|谁对不起|凭什么道歉|不用道歉|不需要道歉|不必道歉)", cleaned)
        )
        comfort = bool(re.search(r"(摸摸|贴贴|抱抱|亲亲|乖|不哭|别伤心|陪你|抱一下)", cleaned))
        praise = bool(re.search(r"(喜欢你|爱你|可爱|厉害|真好|谢谢你|辛苦|最棒|夸夸)", cleaned))
        if self_low:
            return {"event": "comfort_need", "intensity": 62, "reason": "用户自我否定或低落", "target": "self", "rule": "self_low", "confidence": 0.88}
        # Keep violations high-confidence: explicit coercion or targeted abuse
        # only. Ordinary intimacy, teasing, quotes, and contact boundaries do
        # not reduce the relationship score.
        coercion = bool(
            target_hint
            and not third_party_hint
            and re.search(r"(不许拒绝|不准拒绝|没有拒绝权|必须听我的|我说了算|不答应就|不给我就|敢拒绝试试|你只能听|强迫你)", cleaned)
        )
        if coercion:
            severity = 3
            return {
                "event": "boundary_violation",
                "intensity": min(100, 58 + severity * 14),
                "reason": "明确越过角色底线",
                "target": "bot",
                "rule": "explicit_boundary_violation",
                "confidence": 0.94,
                "severity": severity,
                "attribution": attribution,
            }
        if intent_source == "durable_boundary_rule" and not direct_bot_negative and not identity_hurt:
            return {"event": "neutral", "intensity": 0, "reason": "用户在表达相处边界", "target": "bot", "rule": "boundary_goes_relationship", "confidence": 0.82}
        if third_party_hint and severe_hurt and not direct_bot_negative:
            return {"event": "external_negative", "intensity": 54, "reason": "用户在评价第三方", "target": "other", "rule": "third_party_negative", "confidence": 0.78}
        if severe_hurt:
            confidence = 0.9 if direct_bot_negative else (0.72 if target_hint and not third_party_hint else 0.58)
            return {
                "event": "hurt",
                "intensity": 90 if direct_bot_negative else 72,
                "reason": "强否定或驱赶",
                "target": "bot" if direct_bot_negative else "ambiguous",
                "rule": "severe_hurt",
                "confidence": confidence,
                "attribution": attribution,
            }
        if identity_hurt:
            return {"event": "hurt", "intensity": 76 if boundary_durable else 60, "reason": "否定情感真实性或人格", "target": "bot", "rule": "identity_hurt", "confidence": 0.84 if boundary_durable else 0.68}
        if mild_hurt:
            return {"event": "hurt", "intensity": 48, "reason": "轻度否定或拉开距离", "target": "bot", "rule": "mild_hurt", "confidence": 0.66}
        if apology:
            return {"event": "apology", "intensity": 68, "reason": "道歉或修复", "target": "bot", "rule": "apology", "confidence": 0.84}
        if comfort:
            return {"event": "comfort", "intensity": 46, "reason": "安抚亲密互动", "target": "bot", "rule": "comfort", "confidence": 0.78}
        if praise:
            return {"event": "praise", "intensity": 38, "reason": "正向肯定", "target": "bot" if target_hint else "ambiguous", "rule": "praise", "confidence": 0.78 if target_hint else 0.56}
        return {"event": "neutral", "intensity": 0, "reason": "", "target": "none", "rule": "", "confidence": _safe_float((intent_context or {}).get("confidence"), 0.5)}

    def _emotion_judgement_provider_id(self) -> str:
        return self._task_provider(
            runtime_persona_setting(self, "emotion_judgement_provider_id", ""),
            runtime_persona_setting(self, "troubleshooting_provider_id", ""),
            runtime_persona_setting(self, "relationship_analysis_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
            runtime_persona_setting(self, "llm_provider_id", ""),
        )

    def _should_use_llm_emotion_judgement(self, text: str, intent: dict[str, Any]) -> bool:
        if not bool(runtime_persona_setting(self, "enable_llm_emotion_judgement", False)):
            return False
        if bool(intent.get("boundary_feedback_exempt")):
            return False
        if self._is_structured_or_diagnostic_text(text):
            return False
        attribution = intent.get("emotion_attribution") if isinstance(intent.get("emotion_attribution"), dict) else {}
        if attribution.get("auto_settle") is True and _safe_float(attribution.get("confidence"), 0.0) >= 0.85:
            return False
        source = str(intent.get("source") or "")
        if bool(intent.get("playful_or_ambiguous")) or source in {"weak_boundary_ignored", "soft_boundary_play_rule", "single_turn_boundary"}:
            return False
        mode = str(runtime_persona_setting(self, "emotion_judgement_mode", "suspicious") or "suspicious").lower()
        if mode in {"off", "none", "disabled"}:
            return False
        if mode in {"always", "all"}:
            return True
        confidence = _safe_float(intent.get("confidence"), 0.5)
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), confidence)
        event = str(intent.get("emotion_event") or "neutral")
        return (
            event != "neutral"
            or confidence < 0.72
            or emotion_confidence < 0.72
            or source == "durable_boundary_rule"
            or bool(re.search(r"(别|不要|讨厌|烦|滚|闭嘴|对不起|抱歉|喜欢你|爱你|摸摸|抱抱)", text))
        )

    def _normalize_llm_emotion_judgement_payload(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        event = str(payload.get("event") or payload.get("emotion_event") or "").strip().lower()
        aliases = {
            "none": "neutral",
            "normal": "neutral",
            "negative_to_bot": "hurt",
            "hurt_bot": "hurt",
            "repair": "apology",
            "apologize": "apology",
            "soothe": "comfort",
            "low_self": "comfort_need",
            "external": "external_negative",
        }
        event = aliases.get(event, event)
        allowed_events = {"neutral", "hurt", "boundary_violation", "apology", "comfort", "praise", "comfort_need", "external_negative"}
        if event not in allowed_events:
            return None
        target = str(payload.get("target") or "").strip().lower()
        target_aliases = {
            "bot_self": "bot",
            "assistant": "bot",
            "character": "bot",
            "user": "self",
            "third_party": "other",
            "unknown": "ambiguous",
        }
        target = target_aliases.get(target, target)
        if target not in {"bot", "self", "other", "ambiguous", "none"}:
            return None
        raw_confidence = payload.get("confidence")
        if isinstance(raw_confidence, bool):
            return None
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            return None
        raw_intensity = payload.get("intensity")
        if isinstance(raw_intensity, bool):
            return None
        try:
            intensity = int(raw_intensity)
        except (TypeError, ValueError):
            return None
        if intensity < 0 or intensity > 100:
            return None
        if event == "neutral":
            intensity = 0
            target = "none"
        elif intensity <= 0:
            intensity = 60
        severity = payload.get("severity")
        if event == "boundary_violation":
            severity = _safe_int(severity, max(1, min(3, (intensity - 40) // 20)), 1, 3)
        interaction_type = str(payload.get("interaction_type") or payload.get("type") or "normal").strip().lower()
        if interaction_type not in {"confession", "action", "malice", "normal"}:
            interaction_type = "normal"
        suitable_tier = str(payload.get("suitable_tier") or "").strip().lower()
        allowed_tiers = {
            "deeply_distant", "strongly_distant", "distant", "acquaintance",
            "familiar", "close", "intimate", "deeply_bonded", "beyond", "",
        }
        if suitable_tier not in allowed_tiers:
            suitable_tier = ""
        normalized = {
            "event": event,
            "target": target,
            "intensity": intensity,
            "confidence": round(confidence, 2),
            "reason": _single_line(payload.get("reason"), 100) or "模型复核",
            **({"severity": severity} if event == "boundary_violation" else {}),
        }
        normalized["interaction_type"] = interaction_type
        normalized["suitable_tier"] = suitable_tier
        return normalized

    def _merge_llm_emotion_judgement(self, base_intent: dict[str, Any], payload: Any) -> dict[str, Any] | None:
        normalized = self._normalize_llm_emotion_judgement_payload(payload)
        if not isinstance(base_intent, dict) or not isinstance(normalized, dict):
            return None
        event = normalized["event"]
        target = normalized["target"]
        intensity = normalized["intensity"]
        confidence = normalized["confidence"]
        if confidence < 0.65:
            return None
        local_source = str(base_intent.get("source") or "")
        local_text = _single_line(base_intent.get("text"), 240)
        strong_single_turn_abuse = bool(
            local_source == "single_turn_boundary"
            and re.search(r"(恶心|废物|垃圾|没用|工具人|假的|别装|别演).{0,10}(闭嘴|滚)|你.{0,8}(恶心|废物|垃圾|没用).{0,8}(闭嘴|滚)", local_text)
        )
        if event in {"hurt", "boundary_violation"} and (
            bool(base_intent.get("playful_or_ambiguous"))
            or local_source in {"weak_boundary_ignored", "soft_boundary_play_rule"}
            or (local_source == "single_turn_boundary" and not strong_single_turn_abuse)
        ):
            return None
        if event == "hurt" and local_source == "durable_boundary_rule":
            strong_negative = bool(
                re.search(r"(滚|闭嘴|恶心|废物|垃圾|讨厌你|烦你|不想理你|没感情|假的|别装|别演|工具人)", local_text)
            )
            if not strong_negative:
                return None
        if event in {"hurt", "boundary_violation"} and target not in {"bot", "ambiguous"}:
            event = "external_negative" if target == "other" else "neutral"
            intensity = 54 if event == "external_negative" else 0
        reason = normalized["reason"]
        merged = dict(base_intent)
        merged.update(
            {
                "emotion_event": event,
                "emotion_intensity": intensity,
                "emotion_reason": reason,
                "emotion_target": target,
                "emotion_rule": "llm_emotion_judgement",
                "emotion_confidence": round(float(confidence), 2),
                "llm_emotion_judgement": True,
                "llm_emotion_judgement_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "boundary_feedback_type": normalized.get("interaction_type", "normal"),
                "boundary_suitable_tier": normalized.get("suitable_tier", ""),
                "boundary_feedback_reason": reason,
                "boundary_feedback_confidence": round(float(confidence), 2),
            }
        )
        if event == "boundary_violation":
            merged["violation_severity"] = _safe_int(normalized.get("severity"), 1, 1, 3)
        return merged

    async def _refine_inbound_emotion_with_model(
        self,
        user_id: str,
        text: str,
        local_intent: dict[str, Any],
        *,
        review_id: str = "",
    ) -> None:
        cleaned = _single_line(text, 240)
        if not cleaned or not isinstance(local_intent, dict):
            return
        expected_review_id = _single_line(review_id, 64)
        prompt = prompt_section(
            key="background.memory.emotion_judgement",
            title="情绪事件复核",
            source="user_memory",
            content=f"""
You classify whether one inbound message changes the Bot's short-term emotional afterglow and whether it crosses the current relationship boundary. Do not write a reply.

Allowed event values: neutral, hurt, boundary_violation, apology, comfort, praise, comfort_need, external_negative.
target must be bot, self, other, ambiguous, or none.
Only classify hurt when the message clearly targets the Bot/current character. Be conservative with jokes, flirting, logs, code, and quoted text.
Only classify boundary_violation for explicit coercion, threats, or repeated targeted degradation that overrides the character's right to refuse. Do not infer it from ordinary intimacy or ambiguous language.
A boundary such as less intimacy, no flirting, no approaching, or no interruptions should normally be neutral; relationship-distance logic handles it separately.
Also classify interaction_type:
- confession: a feeling such as liking, loving, or missing the character. A confession itself is not a violation.
- action: an explicit intimate action/request, coercion, harassment, or socially intrusive act.
- malice: deliberate degradation of the character, something the character cherishes, or a person the character cares about.
- normal: everything else, including standalone comfort like "摸摸/抱抱", ordinary joking, quoted text, and third-party discussion.
suitable_tier is the minimum fitting relationship tier for an action: deeply_distant, strongly_distant, distant, acquaintance, familiar, close, intimate, deeply_bonded, or beyond. Use beyond only when no relationship tier makes the act acceptable. For confession, use intimate but keep event neutral or praise. The local relationship projection decides whether an action actually crosses a boundary.
Calibrate confidence honestly. Values below 0.65 are valid results but will keep the local judgement instead of overriding it.
Write reason as one short Chinese phrase suitable for a diagnostics panel.
Return JSON only:
{{"event":"neutral|hurt|boundary_violation|apology|comfort|praise|comfort_need|external_negative","target":"bot|self|other|ambiguous|none","intensity":0-100,"severity":1-3,"interaction_type":"confession|action|malice|normal","suitable_tier":"tier or beyond","confidence":0.0-1.0,"reason":"brief reason"}}

User message:
{cleaned}

Local classifier result:
{json.dumps({k: local_intent.get(k) for k in ("intent", "emotion", "source", "reason", "emotion_event", "emotion_target", "emotion_intensity", "emotion_reason", "emotion_confidence", "boundary_feedback_type", "boundary_suitable_tier", "boundary_current_tier")}, ensure_ascii=False)}

Character-specific bottom-line baseline (reference only; empty means use the conservative general rule):
{_single_line(runtime_persona_setting(self, "relationship_boundary_bottom_line_baseline", ""), 600) or "未单独配置"}
""".strip(),
        )
        provider_id = self._emotion_judgement_provider_id()
        raw = ""
        payload = None
        normalized = None
        request_failed = False
        try:
            raw = await self._llm_call(
                _render_user_memory_background_prompt(prompt),
                max_tokens=180,
                provider_id=provider_id,
                task="emotion_judgement",
            ) or ""
            payload = self._extract_json_payload(raw)
            normalized = self._normalize_llm_emotion_judgement_payload(payload)
            refined = self._merge_llm_emotion_judgement(local_intent, payload)
        except Exception as exc:
            refined = None
            request_failed = True
            logger.debug(
                "Emotion judgement request failed: error_type=%s",
                type(exc).__name__,
            )
        async with self._data_lock:
            user = self._get_user(user_id)
            pending = user.get("pending_emotion_judgement") if isinstance(user.get("pending_emotion_judgement"), dict) else {}
            pending_review_id = _single_line(pending.get("review_id"), 64)
            if expected_review_id:
                if pending_review_id != expected_review_id:
                    return
            elif pending_review_id or _single_line(pending.get("text"), 240) != cleaned:
                return
            intent_to_apply = refined if isinstance(refined, dict) else dict(local_intent)
            boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
            if callable(boundary_enricher):
                intent_to_apply = boundary_enricher(user, intent_to_apply)
            observed = pending.get("observed_event") if isinstance(pending.get("observed_event"), dict) else {}
            if observed:
                intent_to_apply["_emotion_revision_of"] = {
                    "event_id": observed.get("event_id"),
                    "trace_id": observed.get("trace_id"),
                    "revision": _safe_int(observed.get("revision"), 1, 1) + 1,
                }
            if runtime_persona_setting(self, "enable_intent_emotion_analysis", True):
                user["intent_profile"] = intent_to_apply
            violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
            if callable(violation_settler):
                violation_settler(
                    user,
                    intent_to_apply,
                    event_id=_single_line(pending.get("message_event_id") or observed.get("event_id"), 96),
                    now=_now_ts(),
                )
            self._update_relationship_state_from_intent(user, intent_to_apply)
            user["pending_emotion_judgement"] = {}
            reviewed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            if refined:
                review_status = "applied"
                review_outcome = "model_applied"
            elif normalized:
                review_status = "kept_local"
                review_outcome = "low_confidence" if normalized.get("confidence", 0.0) < 0.65 else "local_guard"
            else:
                review_status = "failed"
                review_outcome = "request_failed" if request_failed else ("empty_response" if not raw else "invalid_response")
            user["last_emotion_judgement"] = {
                "status": review_status,
                "outcome": review_outcome,
                "event": (normalized or {}).get("event", ""),
                "target": (normalized or {}).get("target", ""),
                "intensity": (normalized or {}).get("intensity", 0),
                "confidence": (normalized or {}).get("confidence", 0.0),
                "reason": (normalized or {}).get("reason", ""),
                "reviewed_at": reviewed_at,
            }
            if refined:
                user.pop("last_emotion_judgement_error", None)
                logger.info(
                    "Emotion judgement completed: user=%s event=%s target=%s intensity=%s confidence=%s reason=%s",
                    user_id,
                    refined.get("emotion_event"),
                    refined.get("emotion_target"),
                    refined.get("emotion_intensity"),
                    refined.get("emotion_confidence"),
                    _single_line(refined.get("emotion_reason"), 80),
                )
            elif normalized:
                user.pop("last_emotion_judgement_error", None)
                logger.info(
                    "Emotion judgement retained local result: user=%s outcome=%s event=%s confidence=%s",
                    user_id,
                    review_outcome,
                    normalized.get("event"),
                    normalized.get("confidence"),
                )
            else:
                user["last_emotion_judgement_error"] = review_outcome
            self._save_data_sync(sections={"users"})

    def _decay_relationship_mood_score(self, state: dict[str, Any], *, now: float | None = None) -> int:
        now = now or _now_ts()
        score = _safe_int(state.get("mood_score"), 0, -100, 100)
        last_ts = _safe_float(state.get("mood_updated_ts"), 0)
        if score == 0 or last_ts <= 0 or now <= last_ts:
            state["mood_updated_ts"] = now
            return score
        hours = max(0.0, (now - last_ts) / 3600)
        recovery = max(
            1,
            _safe_int(runtime_persona_setting(self, "emotional_gate_recovery_per_hour", 24), 24, 1, 60),
        )
        delta = int(hours * recovery)
        if delta <= 0:
            return score
        if score < 0:
            score = min(0, score + delta)
        else:
            score = max(0, score - max(1, delta // 2))
        state["mood_score"] = score
        state["mood_updated_ts"] = now
        return score
