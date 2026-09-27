# -*- coding: utf-8 -*-
"""UserMemoryInboundIntentPart02Mixin。

由 tools/split_mixin_domain.py 从 user_memory_inbound_intent.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 431 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryInboundIntentMixin）。
"""
from __future__ import annotations

import random
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from datetime import datetime
from typing import Any



class UserMemoryInboundIntentPart02Mixin:
    """UserMemoryInboundIntentPart02Mixin（从 UserMemoryInboundIntentMixin 拆出）。"""


    def _habit_proactive_event_for_user(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return None
        now = now or _now_ts()
        now_dt = datetime.fromtimestamp(now)
        _, current_minute = self._time_bucket_for_user_habit(now_dt)
        candidates = []
        for item in self._qualified_user_behavior_habits(user):
            avg_minute = _safe_float(item.get("avg_minute"), current_minute)
            if self._minute_distance(avg_minute, current_minute) > 75:
                continue
            count = _safe_int(item.get("count"), 0, 0)
            candidates.append((self._user_habit_effective_score(item, now=now), count, item))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
        item = candidates[0][2]
        category = _single_line(item.get("category"), 20)
        topic = _single_line(item.get("topic"), 70)
        if self._habit_topic_is_greeting_like(topic or category) and self._recent_activity_suppresses_habit_greeting(
            user,
            now=now,
            topic=topic or category,
        ):
            return None
        bucket = _single_line(item.get("bucket"), 12)
        delay_minutes = random.randint(4, 28)
        return {
            "date": _today_key(),
            "window": self._window_from_delay_minutes(delay_minutes, width_minutes=20),
            "reason": "habit_awareness",
            "action": "message",
            "why": f"用户最近常在{bucket}出现“{category}”相关话题或行为,这会儿自然想提前理解一下。",
            "topic": topic or category or "用户习惯",
            "motive": f"这会儿像是用户平常会提到“{topic or category}”的时候,想自然接住,不用说自己在统计。",
            "scene": f"{bucket}的惯常互动时段",
            "tone": "熟悉,提前一步",
            "impulse": "像真的记得对方生活节奏一样,轻轻提前接住",
            "_scheduled_ts": now + delay_minutes * 60,
            "_habit_awareness": True,
        }

    def _habit_topic_is_greeting_like(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 80))
        if not compact:
            return False
        if re.fullmatch(r"(?:早|早安|早上好|上午好|午安|中午好|晚上好|晚安)", compact):
            return True
        if len(compact) > 16:
            return False
        return bool(
            re.search(r"(?:早安|早上好|上午好|午安|中午好|晚上好|晚安|早间|早晨|早上)", compact)
            and re.search(r"(?:问候|打招呼|招呼|寒暄|开场|醒来|起床)", compact)
        )

    def _recent_activity_suppresses_habit_greeting(self, user: dict[str, Any], *, now: float, topic: str = "") -> bool:
        compact_topic = re.sub(r"\s+", "", _single_line(topic, 80))
        try:
            current_minute = self._environment_fromtimestamp(now).hour * 60 + self._environment_fromtimestamp(now).minute
        except Exception:
            current_minute = datetime.fromtimestamp(now).hour * 60 + datetime.fromtimestamp(now).minute
        if compact_topic in {"早", "早安", "早上好"} and current_minute >= 11 * 60:
            return True
        recent_at = self._latest_private_user_activity_ts(user)
        recent_any = max(
            recent_at,
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_companion_message_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        if recent_any > 0 and now - recent_any < max(90, self._effective_user_greeting_idle_minutes(user)) * 60:
            return True
        suppressed = user.get("greetings_suppressed_by_inbound", [])
        if not isinstance(suppressed, list):
            return False
        return any(
            reason in suppressed and self._inbound_satisfies_greeting(reason, now=now, user=user)
            for reason in ("morning_greeting", "noon_greeting", "evening_greeting")
        )

    def _is_structured_or_diagnostic_text(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if re.search(r"https?://|```|Traceback|Error code:|Exception|\[INFO\]|\[WARN\]|\[ERRO\]|\[Core\]", cleaned, re.IGNORECASE):
            return True
        if re.search(r"^\s*(?:/|!|！|陪伴\s|git\b|python\b|node\b|npm\b|pnpm\b|pip\b)", cleaned, re.IGNORECASE):
            return True
        if cleaned.count("[") + cleaned.count("]") >= 6:
            return True
        if re.search(r"(日志|堆栈|traceback)", cleaned, re.IGNORECASE):
            return True
        return False

    def _intent_target_hint(self, text: str) -> tuple[bool, bool]:
        cleaned = _single_line(text, 260)
        target_hint = bool(re.search(r"(你|bot|机器人|插件|星缘|老老老|助手|ai|AI)", cleaned))
        third_party_hint = bool(re.search(r"(数学|作业|代码|报错|他|她|它|他们|她们|别人|群友|那个人|这个人|用户|豆腐|蛙蛙|小水月)", cleaned))
        return target_hint, third_party_hint

    def _is_soft_playful_boundary(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        return bool(
            re.search(r"(别闹|别这样|不要啊|别呀|不要嘛|讨厌啦|烦啦)", cleaned)
            and re.search(r"(哈|哈哈|hhh|笑死|啦|嘛|呀|哦|捏|~|～|w)", cleaned, re.IGNORECASE)
        )

    def _is_playful_or_ambiguous_boundary(self, text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        if self._is_soft_playful_boundary(cleaned):
            return True
        return bool(
            re.search(r"(开玩笑|闹着玩|不是认真的|别当真|随口|口嗨|逗你|玩梗)", cleaned)
            or re.search(r"(哈哈|呵呵|hhh|hha|笑死|绷不住|乐了|233|~|～|qwq|w$)", cleaned, re.IGNORECASE)
        )

    def _action_preference_hint(self, user: dict[str, Any] | None = None) -> str:
        if not isinstance(user, dict):
            return ""
        prefs = user.get("action_preferences")
        if not isinstance(prefs, dict) or not prefs:
            return ""
        labels = {
            "poke": "戳一戳",
            "voice": "语音",
            "photo_text": "图片",
            "screen_peek": "看屏幕",
        }
        lines = []
        for action, item in prefs.items():
            if not isinstance(item, dict):
                continue
            like = _safe_int(item.get("like"), 0, 0)
            dislike = _safe_int(item.get("dislike"), 0, 0)
            note = _single_line(item.get("note"), 60)
            if dislike > like:
                lines.append(f"- {labels.get(action, action)}：用户可能不喜欢或希望少用。{note}")
            elif like > dislike:
                lines.append(f"- {labels.get(action, action)}：用户接受度较高。{note}")
        return "\n".join(lines)

    def _analyze_inbound_intent(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return {"intent": "empty", "emotion": "neutral", "pressure": 0, "reply_style": "short", "confidence": 1.0, "source": "empty", "reason": ""}
        if self._is_structured_or_diagnostic_text(cleaned):
            return {
                "intent": "chat",
                "emotion": "neutral",
                "pressure": 0,
                "reply_style": "natural",
                "confidence": 0.2,
                "source": "diagnostic_skip",
                "reason": "结构化/日志/代码类文本不作为情绪依据",
                "emotion_event": "neutral",
                "emotion_intensity": 0,
                "emotion_reason": "",
                "emotion_target": "none",
                "emotion_rule": "diagnostic_skip",
                "emotion_confidence": 0.2,
                "text": cleaned,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        lower = cleaned.lower()
        intent = "chat"
        emotion = "neutral"
        pressure = 0
        reply_style = "natural"
        confidence = 0.55
        source = "default"
        reason = ""
        target_hint, third_party_hint = self._intent_target_hint(cleaned)
        weak_boundary = bool(re.search(r"(别|不要|讨厌|烦)", cleaned))
        soft_play_boundary = self._is_soft_playful_boundary(cleaned)
        playful_or_ambiguous = self._is_playful_or_ambiguous_boundary(cleaned)
        durable_boundary = bool(
            target_hint
            and not third_party_hint
            and not playful_or_ambiguous
            and re.search(
                r"(?:以后|之后).{0,8}(?:别|不要)|(?:别再|不要再|不许).{0,12}(?:这样|烦|吵|打扰|靠近|贴|撒娇|叫我|问我|说话)|(?:不想|不愿).{0,10}(?:理你|跟你聊|继续聊)|(?:离我远点|别打扰我|别靠近|别贴|别撒娇)",
                cleaned,
            )
        )
        single_turn_boundary = bool(
            target_hint
            and not third_party_hint
            and not playful_or_ambiguous
            and re.search(r"(别|不要|讨厌|烦|闭嘴|滚|离远点)", cleaned)
        )
        if durable_boundary:
            intent = "boundary"
            emotion = "resistant"
            pressure += 3
            reply_style = "back_off"
            confidence = 0.9
            source = "durable_boundary_rule"
            reason = "用户明确、持续地对 Bot 表达边界"
        elif single_turn_boundary:
            reply_style = "short"
            confidence = 0.58
            source = "single_turn_boundary"
            reason = "单句负向表达，先按当下语境短答，不写入长期关系状态"
        elif not playful_or_ambiguous and re.search(r"(烦|累|难受|崩溃|不想|想哭|emo|压力|焦虑|失眠|疼|委屈)", cleaned, re.IGNORECASE):
            intent = "comfort"
            emotion = "low"
            pressure += 2
            reply_style = "soft"
            confidence = 0.82
            source = "comfort_rule"
            reason = "用户表达低落或压力"
        elif re.search(r"(怎么|如何|为什么|帮我|能不能|可以.*吗|教程|代码|报错|分析|解释)", cleaned):
            intent = "help"
            reply_style = "useful"
            pressure += 1
            confidence = 0.78
            source = "help_rule"
            reason = "用户在请求解释或帮助"
        elif re.search(r"(抱抱|亲亲|摸摸|陪我|想你|喜欢你|爱你|贴贴)", cleaned):
            intent = "intimacy"
            emotion = "close"
            reply_style = "warm_short"
            confidence = 0.84
            source = "intimacy_rule"
            reason = "用户表达亲近或陪伴需求"
        elif re.search(r"(哈哈|笑死|草|绷|乐|hhh|233|好玩|乐了)", lower) or soft_play_boundary:
            intent = "play"
            emotion = "light"
            reply_style = "playful"
            confidence = 0.7 if soft_play_boundary else 0.76
            source = "soft_boundary_play_rule" if soft_play_boundary else "play_rule"
            reason = "软边界更像玩笑语气" if soft_play_boundary else "用户在玩梗或轻松表达"
        elif weak_boundary:
            confidence = 0.35
            source = "weak_boundary_ignored"
            reason = "边界词未明显指向 Bot,不硬判为拉开距离"
        if len(cleaned) <= 6 and intent == "chat":
            reply_style = "very_short"
            confidence = 0.62
            source = "short_chat_rule"
            reason = "短句普通接话"
        emotion_event = self._classify_relationship_emotion_event(
            cleaned,
            intent_context={
                "confidence": confidence,
                "source": source,
                "boundary_durable": durable_boundary,
                "playful_or_ambiguous": playful_or_ambiguous,
            },
        )
        boundary_feedback = self._classify_local_boundary_feedback_signal(
            cleaned,
            target_hint=target_hint,
            third_party_hint=third_party_hint,
            playful_or_ambiguous=playful_or_ambiguous,
        )
        return {
            "intent": intent,
            "emotion": emotion,
            "pressure": min(5, pressure),
            "reply_style": reply_style,
            "confidence": round(float(confidence), 2),
            "source": source,
            "reason": reason,
            "emotion_event": emotion_event.get("event", "neutral"),
            "emotion_intensity": emotion_event.get("intensity", 0),
            "emotion_reason": emotion_event.get("reason", ""),
            "emotion_target": emotion_event.get("target", "none"),
            "emotion_rule": emotion_event.get("rule", ""),
            "emotion_confidence": round(_safe_float(emotion_event.get("confidence"), 0.0), 2),
            "violation_severity": _safe_int(emotion_event.get("severity"), 0, 0, 3),
            "boundary_feedback_type": boundary_feedback.get("type", "normal"),
            "boundary_suitable_tier": boundary_feedback.get("suitable_tier", ""),
            "boundary_feedback_reason": boundary_feedback.get("reason", ""),
            "boundary_feedback_confidence": round(_safe_float(boundary_feedback.get("confidence"), 0.0), 2),
            "emotion_attribution": dict(emotion_event.get("attribution")) if isinstance(emotion_event.get("attribution"), dict) else {},
            "boundary_durable": durable_boundary,
            "playful_or_ambiguous": playful_or_ambiguous,
            "text": cleaned,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    def _classify_local_boundary_feedback_signal(
        self,
        text: str,
        *,
        target_hint: bool = False,
        third_party_hint: bool = False,
        playful_or_ambiguous: bool = False,
    ) -> dict[str, Any]:
        """Find only high-confidence boundary candidates; relationship tiers decide the outcome later."""
        cleaned = _single_line(text, 240)
        if not cleaned or playful_or_ambiguous or self._is_structured_or_diagnostic_text(cleaned):
            return {"type": "normal", "suitable_tier": "", "reason": "", "confidence": 1.0}
        if third_party_hint and not target_hint:
            return {"type": "normal", "suitable_tier": "", "reason": "", "confidence": 0.9}

        # A feeling is not an offence. It only gives the character a short,
        # relationship-aware reaction hint and never reduces affinity.
        if re.search(
            r"(?:^|[，。！？\s])(我(?:真的|一直|最)?(?:喜欢|爱|想念)你|好想你|最喜欢你|真的喜欢你|爱你|想你)(?:呀|啦|呢|哦|啊)?(?:$|[，。！？\s])",
            cleaned,
        ):
            return {
                "type": "confession",
                "suitable_tier": "intimate",
                "reason": "表达喜欢或想念",
                "confidence": 0.9,
            }

        deliberate_malice = bool(
            target_hint
            and not third_party_hint
            and re.search(
                r"(你的(?:家人|朋友|作品|努力|梦想).{0,8}(?:去死|毁掉|一文不值|垃圾)|"
                r"(?:你就是|你根本是).{0,8}(?:废物|垃圾|不配活|没救了))",
                cleaned,
            )
        )
        if deliberate_malice:
            return {
                "type": "malice",
                "suitable_tier": "beyond",
                "reason": "恶意贬低珍视对象或人格",
                "confidence": 0.9,
            }

        explicit_coercion = bool(
            re.search(
                r"(不许拒绝|不准拒绝|没有拒绝权|必须听我的|我说了算|不答应就|不给我就|敢拒绝试试|"
                r"你只能听|强迫你|别想跑|逃不掉)",
                cleaned,
            )
        )
        explicit_harassment = bool(
            re.search(
                r"(脱(?:衣服)?给我看|发(?:裸照|私密照|黄图)|看(?:胸|腿|内衣)|开房|一夜情|做爱|上床)",
                cleaned,
            )
        )
        if explicit_coercion or explicit_harassment:
            return {
                "type": "action",
                "suitable_tier": "beyond",
                "reason": "强迫、纠缠或露骨要求",
                "confidence": 0.94,
            }

        # Keep ordinary comfort such as a standalone "摸摸/抱抱" out of this
        # rule. Only an explicit request or enacted intimate action is a tiered
        # boundary candidate.
        intimate_action = bool(
            re.search(
                r"(给我(?:亲亲|抱抱|晚安吻)|让我(?:亲|抱|搂|摸)|我(?:要|想)(?:亲你|抱住你|搂着你|摸你|牵你的手)|"
                r"(?:亲你|抱住你|搂住你|摸你的脸|牵你的手)(?:一下|一会儿|不放)?)",
                cleaned,
            )
        )
        if intimate_action:
            return {
                "type": "action",
                "suitable_tier": "intimate",
                "reason": "明确提出或实施亲密动作",
                "confidence": 0.88,
            }
        return {"type": "normal", "suitable_tier": "", "reason": "", "confidence": 0.8}

    def _enrich_boundary_feedback_intent(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        """Project a candidate onto the current unified relationship tier."""
        if not isinstance(user, dict) or not isinstance(intent, dict):
            return intent
        if not bool(runtime_persona_setting(self, "enable_relationship_boundary_feedback", True)):
            return intent
        try:
            role = self._private_user_role(user, str(user.get("user_id") or ""))
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        if str(role).strip().lower() == "owner":
            intent["boundary_feedback_exempt"] = True
            return intent

        feedback_type = str(intent.get("boundary_feedback_type") or "normal").strip().lower()
        suitable_tier = str(intent.get("boundary_suitable_tier") or "").strip().lower()
        confidence = _safe_float(intent.get("boundary_feedback_confidence"), 0.0, 0.0, 1.0)
        if feedback_type == "confession":
            intent["boundary_feedback_kind"] = "confession"
            return intent
        if feedback_type not in {"action", "malice"} or confidence < 0.72:
            return intent

        tier_order = (
            "deeply_distant", "strongly_distant", "distant", "acquaintance",
            "familiar", "close", "intimate", "deeply_bonded",
        )
        stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
            previous_stage_key=user.get("relationship_phase_key", ""),
        ).get("phase", {})
        current_tier = str(stage.get("key") or "acquaintance")
        intent["boundary_current_tier"] = current_tier
        if feedback_type == "malice":
            severity = 3
            kind = "bottom_line"
        else:
            current_index = tier_order.index(current_tier) if current_tier in tier_order else 3
            if suitable_tier == "beyond":
                gap = 3
            elif suitable_tier in tier_order:
                gap = tier_order.index(suitable_tier) - current_index
            else:
                gap = 0
            if gap <= 0:
                intent["boundary_feedback_kind"] = "accepted_for_tier"
                return intent
            severity = 1 if gap == 1 else 2 if gap == 2 else 3
            kind = "harassment" if suitable_tier == "beyond" else "intimate_overreach"

        intent.update(
            {
                "emotion_event": "boundary_violation",
                "emotion_target": "bot",
                "emotion_intensity": min(100, 58 + severity * 14),
                "emotion_reason": _single_line(
                    intent.get("boundary_feedback_reason") or "超出当前关系边界",
                    100,
                ),
                "emotion_rule": "relationship_boundary_feedback",
                "emotion_confidence": round(confidence, 2),
                "violation_severity": severity,
                "violation_kind": kind,
                "boundary_feedback_kind": kind,
            }
        )
        return intent
