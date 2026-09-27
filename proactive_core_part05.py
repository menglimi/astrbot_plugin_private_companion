# -*- coding: utf-8 -*-
"""ProactivePart05Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 579 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import _proactive_setting_value
from .proactive_core_shared import Any
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import datetime
from .proactive_core_shared import math
from .proactive_core_shared import random
from .proactive_core_shared import re
from .proactive_core_shared import timedelta



class ProactivePart05Mixin:
    """ProactivePart05Mixin（从 ProactiveMixin 拆出）。"""


    def _experimental_proactive_motivation(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        drive: dict[str, Any] | None = None,
        temperature: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        drive = drive if isinstance(drive, dict) else self._bot_proactive_drive(user, now=now)
        temperature = temperature if isinstance(temperature, dict) else self._relationship_proactive_temperature(user, now=now)
        incentive = self._experimental_proactive_incentive(user, now=now)
        arousal = self._experimental_proactive_arousal(user, now=now)
        drive_score = _safe_float(drive.get("score"), 0.55)
        temp_score = _safe_float(temperature.get("score"), 0.55)
        incentive_score = _safe_float(incentive.get("score"), 0.5)
        arousal_fit = _safe_float(arousal.get("score"), 0.5)
        score = drive_score * 0.25 + temp_score * 0.18 + incentive_score * 0.37 + arousal_fit * 0.20
        score = max(0.05, min(1.0, score))
        label = "适合行动" if score >= 0.66 else "先收住" if score <= 0.40 else "观望"
        detail = (
            f"驱力{drive_score:.2f} 诱因{incentive_score:.2f} "
            f"唤醒{_safe_float(arousal.get('level'), 0.55):.2f}/适配{arousal_fit:.2f}"
        )
        return {
            "score": score,
            "label": label,
            "detail": detail,
            "drive": drive,
            "temperature": temperature,
            "incentive": incentive,
            "arousal": arousal,
        }

    def _soft_daily_target(self, user: dict[str, Any]) -> float:
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return 0.0
        if bool(self._proactive_intensity_effect("ignore_soft_daily_target", False)):
            return float(daily_limit)
        role = self._private_user_role(user)
        relationship_target = min(daily_limit, self._relationship_proactive_soft_target(user))
        explicit_quota = self._user_profile_override_int(user, "proactive_daily_limit")
        if role == "owner" or explicit_quota is not None:
            # The configured daily limit should remain the main pacing signal for
            # primary users and explicit per-user quotas. Relationship state still
            # controls hard boundaries and influences tone/readiness.
            relationship_target = daily_limit
        if relationship_target <= 0:
            return 0.0
        state = self.data.get("daily_state", {})
        important_dates = self._get_relevant_important_dates()
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        quota_ratio = _safe_float(self._proactive_quota_policy(user).get("target_ratio"), 0.9, 0.0)
        ratio = quota_ratio if role == "owner" or explicit_quota is not None else 0.68
        if energy > 80:
            ratio += 0.06
        elif energy < 40:
            ratio -= 0.02
        if isinstance(active_conditions, list) and active_conditions:
            ratio += min(0.1, len(active_conditions) * 0.03)
        if important_dates:
            ratio += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        ratio = max(0.45, min(1.0 if role == "owner" or explicit_quota is not None else 0.95, ratio))
        if relationship_target == 1:
            ratio = max(ratio, 0.75)
        return max(0.6, relationship_target * ratio)

    def _daily_intensity_factor(self, user: dict[str, Any]) -> float:
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            return 0.0
        sent_today = _safe_int(user.get("sent_today"), 0)
        soft_target = self._soft_daily_target(user)
        no_cost_mode = bool(self._proactive_intensity_effect("ignore_soft_daily_target", False))
        capacity_factor = min(2.6 if no_cost_mode else 1.35, 0.9 + daily_limit * (0.055 if no_cost_mode else 0.08))
        if soft_target <= 0:
            return max(0.35, capacity_factor)
        usage = sent_today / soft_target
        if usage < 0.2:
            pressure = 1.18
        elif usage < 0.5:
            pressure = 1.03
        elif usage < 0.85:
            pressure = 0.88
        elif usage < 1.0:
            pressure = 0.72
        else:
            pressure = 0.9 if no_cost_mode else 0.5
        readiness = self._proactive_inner_readiness(user)
        inner_factor = 0.74 + _safe_float(readiness.get("score"), 0.55) * 0.55
        quota_multiplier = _safe_float(
            self._proactive_quota_policy(user).get("moment_probability_multiplier"),
            1.0,
            0.0,
        )
        return max(
            0.25,
            min(2.4 if no_cost_mode else 1.8, capacity_factor * pressure * inner_factor * quota_multiplier),
        )

    def _fallback_proactive_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float]:
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        tier_policy = self._proactive_quota_policy(user)
        tier = _safe_int(tier_policy.get("tier"), 0, 0, 5)
        configured_range = tier_policy.get("delay_range_hours")

        def tune(delay: tuple[float, float]) -> tuple[float, float]:
            low, high = delay
            if tier <= 0 or not isinstance(configured_range, (list, tuple)) or len(configured_range) < 2:
                return low, high
            policy_low = max(0.05, _safe_float(configured_range[0], low, 0.05))
            policy_high = max(policy_low + 0.05, _safe_float(configured_range[1], high, policy_low + 0.05))
            if tier <= 2:
                return max(low, policy_low), max(max(low, policy_low) + 0.05, min(high, policy_high))
            tuned_low = max(policy_low, min(low, policy_high * 0.72))
            tuned_high = max(tuned_low + 0.05, min(high, policy_high))
            return tuned_low, tuned_high

        if self._private_user_role(user) == "friend":
            spread_delay = self._friend_proactive_spread_delay_hours(user, now=now_dt.timestamp())
            if spread_delay is not None:
                return tune(spread_delay)
        sent_today = _safe_int(user.get("sent_today"), 0)
        remaining_target = max(1, math.ceil(max(0.0, self._soft_daily_target(user) - sent_today)))
        readiness_score = _safe_float(self._proactive_inner_readiness(user, now=now_dt.timestamp()).get("score"), 0.55)
        if readiness_score < 0.38:
            return tune((2.5, 6.0) if self._private_user_role(user) != "friend" else (8.0, 16.0))
        counts = self._today_proactive_daypart_counts(user)
        current_bucket = self._proactive_daypart_bucket_for_minute(now_dt.hour * 60 + now_dt.minute)
        if current_bucket == "late_night" and _safe_int(counts.get("late_night"), 0, 0) >= 1:
            return tune((7.5, 10.5))
        if current_bucket == "evening" and _safe_int(counts.get("evening"), 0, 0) >= 1 and remaining_target <= 2:
            return tune((3.0, 5.0))

        if now_dt.hour < 12:
            if remaining_target >= 4:
                return tune((0.45, 1.4))
            if remaining_target >= 3:
                return tune((0.75, 2.0))
            if remaining_target >= 2:
                return tune((1.0, 2.8))
            return tune((1.6, 4.2))
        if now_dt.hour < 18:
            if remaining_target >= 3:
                return tune((0.55, 1.8))
            if remaining_target >= 2:
                return tune((0.9, 2.6))
            return tune((1.8, 4.5))
        if remaining_target >= 2:
            return tune((0.5, 1.6))
        return tune((0.9, 2.4))

    def _proactive_hour_activity_weights(self) -> list[float]:
        raw = _proactive_setting_value(self, "proactive_hour_activity_curve", "")
        values = list(raw) if isinstance(raw, (list, tuple)) else str(raw or "").replace("，", ",").split(",")
        parsed: list[float] = []
        for value in values[:24]:
            try:
                parsed.append(max(0.05, min(2.0, float(value))))
            except (TypeError, ValueError):
                parsed.append(1.0)
        if len(parsed) != 24:
            parsed = [0.22, 0.16, 0.12, 0.10, 0.10, 0.14, 0.28, 0.50, 0.66, 0.72, 0.78, 0.92, 1.0, 0.94, 0.82, 0.74, 0.78, 0.92, 1.0, 0.98, 0.88, 0.70, 0.48, 0.32]
        return parsed

    def _sample_proactive_timestamp(
        self,
        user: dict[str, Any],
        *,
        now: float,
        delay_hours: tuple[float, float],
        reason: str = "",
    ) -> float:
        """Sample future slots by activity preference instead of uniform wall time."""
        low = max(0.05, _safe_float(delay_hours[0], 0.25, 0.05))
        high = max(low + 0.05, _safe_float(delay_hours[1], low + 0.5, low + 0.05))
        start = now + low * 3600
        end = now + high * 3600
        weights = self._proactive_hour_activity_weights()
        chronotype_blend = getattr(self, "_chronotype_hour_weights", None)
        if callable(chronotype_blend):
            # 全局曲线与该用户自己的活跃直方图混合，冷启动（样本不足）时保持全局。
            weights = chronotype_blend(user, weights)
        slots: list[tuple[float, float]] = []
        slot = math.ceil(start / 1800.0) * 1800.0
        while slot <= end and len(slots) < 160:
            local = self._environment_fromtimestamp(slot)
            slots.append((slot, weights[local.hour]))
            slot += 1800.0
        if not slots:
            return now + random.uniform(low, high) * 3600
        return random.choices([item[0] for item in slots], weights=[item[1] for item in slots], k=1)[0]

    def _maybe_schedule_proactive_burst(
        self,
        user: dict[str, Any],
        *,
        now: float,
        reason: str,
        source: str,
        action: str,
        motive: str,
        topic: str,
    ) -> bool:
        if not bool(_proactive_setting_value(self, "enable_proactive_burst", False)) or bool(user.get("planned_proactive_burst")):
            return False
        if source in {"timer", "troubleshooting", "simulation", "weather_alert", "body_monitor", "environment_change"}:
            return False
        if reason in {"open_loop_followup", "activity_followup", "goodnight_screen_check"}:
            return False
        limit = self._effective_user_daily_limit(user)
        if limit <= 0 or _safe_int(user.get("sent_today"), 0, 0) + 1 >= limit:
            return False
        max_messages_getter = getattr(self, "_proactive_burst_max_messages", None)
        max_messages = (
            max_messages_getter()
            if callable(max_messages_getter)
            else _safe_int(_proactive_setting_value(self, "proactive_burst_max_messages", 2), 2, 2, 3)
        )
        current_index = _safe_int(user.get("proactive_burst_index"), 0, 0, max_messages)
        if current_index + 1 >= max_messages:
            return False
        low = max(10, _safe_int(_proactive_setting_value(self, "proactive_burst_gap_min_seconds", 45), 45, 10, 600))
        high = max(low, _safe_int(_proactive_setting_value(self, "proactive_burst_gap_max_seconds", 180), 180, low, 900))
        scheduled = now + random.uniform(low, high)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_burst"] = True
        user["proactive_burst_index"] = current_index + 1
        user["proactive_burst_origin_id"] = _single_line(user.get("planned_proactive_impulse_id"), 20)
        user["planned_proactive_source"] = _single_line(source, 40) or "proactive"
        user["planned_proactive_reason"] = _single_line(reason, 40) or "check_in"
        user["planned_proactive_action"] = _single_line(action, 40) or "message"
        user["planned_proactive_topic"] = _single_line(topic, 80)
        burst_motive = f"{motive}；这是同一阵念头里的第{current_index + 2}条短消息，换一个角度，不重复上一条。"
        normalizer = getattr(self, "_normalize_internal_motive_text", None)
        normalized_burst_motive = normalizer(burst_motive) if callable(normalizer) else burst_motive
        user["planned_proactive_motive"] = _single_line(normalized_burst_motive, 180)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason, source=source)
        user["planned_proactive_window_start_at"] = scheduled
        user["planned_proactive_best_until_at"] = scheduled + min(active_span, 20 * 60)
        user["planned_proactive_expire_at"] = scheduled + min(grace_span, 45 * 60)
        user["planned_proactive_delivery_state"] = "burst"
        return True

    def _proactive_burst_max_messages(self) -> int:
        return _safe_int(_proactive_setting_value(self, "proactive_burst_max_messages", 2), 2, 2, 3)

    def _friend_proactive_spread_delay_hours(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> tuple[float, float] | None:
        if self._private_user_role(user) != "friend":
            return None
        sent_today = _safe_int(user.get("sent_today"), 0)
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 1 or sent_today <= 0:
            return None
        ignored_slowdown = self._unanswered_slowdown_count(user)
        max_cooldown = self._effective_proactive_float(
            "friend_unanswered_max_cooldown_hours",
            max(1.0, _safe_float(_proactive_setting_value(self, "friend_unanswered_max_cooldown_hours", 60.0), 60.0, 1.0)),
            minimum=1.0,
            maximum=168.0,
        )
        base_interval_hours = max(0.25, self._effective_user_min_interval_minutes(user) / 60.0)
        unanswered_floor = 0.0
        if ignored_slowdown > 0:
            unanswered_floor = min(max_cooldown, 1.5 * (2 ** min(ignored_slowdown - 1, 3)))

        def cap_delay(delay: tuple[float, float]) -> tuple[float, float]:
            low, high = delay
            high = min(max_cooldown, high)
            low = min(low, max(0.25, high))
            return (low, high)

        low_multiplier, high_multiplier = (0.9, 1.6)
        if sent_today <= 1:
            low_multiplier, high_multiplier = (0.85, 1.5)
        elif sent_today <= 2 and daily_limit >= 3:
            low_multiplier, high_multiplier = (1.0, 1.9)
        else:
            low_multiplier, high_multiplier = (1.2, 2.4)
        low = max(0.25, base_interval_hours * low_multiplier, unanswered_floor)
        high = max(low + 0.35, base_interval_hours * high_multiplier, unanswered_floor * 1.45)
        return cap_delay((low, high))

    def _delay_hours_until_local_window(
        self,
        now_dt: datetime,
        start_minute: int,
        end_minute: int,
    ) -> tuple[float, float]:
        base = datetime.combine(now_dt.date(), datetime.min.time(), tzinfo=now_dt.tzinfo)
        start_dt = base + timedelta(minutes=start_minute)
        end_dt = base + timedelta(minutes=end_minute)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        if end_dt <= now_dt + timedelta(minutes=20):
            start_dt += timedelta(days=1)
            end_dt += timedelta(days=1)
        start_dt = max(start_dt, now_dt + timedelta(hours=3))
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=90)
        min_hours = max(0.25, (start_dt - now_dt).total_seconds() / 3600)
        max_hours = max(min_hours + 0.5, (end_dt - now_dt).total_seconds() / 3600)
        return (min_hours, max_hours)

    def _current_emotion_gate_mode(self, user: dict[str, Any], *, now: float | None = None) -> str:
        projection = self._relationship_proactive_temperature(user, now=now).get("expression_decision")
        band = _single_line(projection.get("expression_band"), 24) if isinstance(projection, dict) else ""
        if band == "hurt":
            return "hurt"
        if band == "avoidant" and not _single_line(projection.get("blocker"), 40):
            return "refusing"
        return ""

    def _current_relationship_gate_mode(self, user: dict[str, Any], *, now: float | None = None) -> str:
        projection = self._relationship_proactive_temperature(user, now=now).get("expression_decision")
        if not isinstance(projection, dict):
            return ""
        if _single_line(projection.get("blocker"), 40) == "contact_boundary" or _single_line(projection.get("safety_mode"), 40).startswith("contact_boundary"):
            return "backoff"
        return ""

    @staticmethod
    def _proactive_reason_is_intimate(reason: str) -> bool:
        return str(reason or "") in {
            "insomnia_night",
            "state_share",
            "diary_share",
            "evening_greeting",
        }

    @staticmethod
    def _proactive_action_is_intimate(action: str) -> bool:
        parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        return bool(parts & {"poke", "voice", "photo_text", "screen_peek"})

    @staticmethod
    def _proactive_text_is_intimate(*parts: Any) -> bool:
        text = " ".join(_single_line(part, 120) for part in parts if _single_line(part, 120))
        return bool(re.search(r"贴贴|抱抱|亲亲|摸摸|揉揉|蹭蹭|逗你|撒娇|想你|黏|贴近|靠近|坏心思|亲密|睡前|床|小屁股", text, re.I))

    def _low_pressure_proactive_replacement(
        self,
        *,
        mode: str,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
    ) -> tuple[str, str, str, str]:
        if mode == "careful":
            return (
                "quiet_care",
                "message",
                "感觉用户这会儿可能有点累或压力,只低压地问一句,不追问、不要求回复",
                topic or "低压关心",
            )
        if mode in {"hurt", "refusing"}:
            return (
                "quiet_care",
                "message",
                "Bot 还在收敛情绪,只保留一条很短的低压关心；不贴近、不撒娇、不追问",
                topic or "收敛后的低压关心",
            )
        return reason, action, motive, topic

    def _apply_emotion_to_planned_proactive(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        scheduled: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        mode = self._current_emotion_gate_mode(user, now=check_now) or self._current_relationship_gate_mode(user, now=check_now)
        result = {
            "reason": reason,
            "action": action,
            "motive": motive,
            "topic": topic,
            "scheduled": scheduled,
            "mode": mode,
            "note": "",
            "blocked": False,
        }
        intimate = (
            self._proactive_reason_is_intimate(reason)
            or self._proactive_action_is_intimate(action)
            or self._proactive_text_is_intimate(reason, action, motive, topic)
        )
        if mode == "attached":
            if reason in {"check_in", "quiet_care"} and random.random() < 0.28:
                result["reason"] = "activity_share"
                result["motive"] = motive or "刚刚有个轻轻的小想法,想自然分享一下"
                result["topic"] = topic or "轻分享"
                result["note"] = "情绪 attached: 提高轻分享倾向"
            photo_probability = max(0.0, min(1.0, float(_proactive_setting_value(self, "proactive_photo_text_probability", 0.18))))
            if action == "message" and reason in {"activity_share", "diary_share", "background_schedule"} and self._photo_text_available(user) and random.random() < photo_probability:
                result["action"] = self._fallback_action_for_unavailable("photo_text", user)
                result["note"] = (result["note"] + "；" if result["note"] else "") + "情绪 attached: 轻分享可带图"
            return result
        if mode == "careful":
            if action != "message" or reason not in {"quiet_care", "check_in"} or intimate:
                new_reason, new_action, new_motive, new_topic = self._low_pressure_proactive_replacement(
                    mode=mode,
                    reason=reason,
                    action=action,
                    motive=motive,
                    topic=topic,
                )
                result.update(reason=new_reason, action=new_action, motive=new_motive, topic=new_topic)
                result["note"] = "关系 careful: 只保留低压关心"
            return result
        if mode in {"hurt", "refusing", "backoff"}:
            if str(user.get("planned_proactive_source") or "") == "timer":
                return result
            delay = 2.5 * 3600 if mode == "hurt" else 5.5 * 3600
            if scheduled and scheduled > 0:
                result["scheduled"] = max(scheduled, check_now + random.uniform(delay, delay + 2.5 * 3600))
            if intimate or action != "message" or mode in {"refusing", "backoff"}:
                new_reason, new_action, new_motive, new_topic = self._low_pressure_proactive_replacement(
                    mode="hurt" if mode == "hurt" else "refusing",
                    reason=reason,
                    action=action,
                    motive=motive,
                    topic=topic,
                )
                result.update(reason=new_reason, action=new_action, motive=new_motive, topic=new_topic)
                result["note"] = f"情绪 {mode}: 延后并清理亲密主动候选"
            elif mode == "hurt":
                result["note"] = "情绪 hurt: 候选延后"
            return result
        return result

    def _defer_or_clean_emotion_blocked_plan(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        mode = self._current_emotion_gate_mode(user, now=check_now) or self._current_relationship_gate_mode(user, now=check_now)
        if mode not in {"hurt", "refusing", "backoff"}:
            return "情绪/关系状态处于收敛期"
        if str(user.get("planned_proactive_source") or "") == "timer":
            return "情绪/关系状态处于收敛期,预约主动保留"
        reason = str(user.get("planned_proactive_reason") or "")
        action = str(user.get("planned_proactive_action") or "message")
        motive = _single_line(user.get("planned_proactive_motive"), 140)
        topic = _single_line(user.get("planned_proactive_topic"), 60)
        intimate = (
            self._proactive_reason_is_intimate(reason)
            or self._proactive_action_is_intimate(action)
            or self._proactive_text_is_intimate(reason, action, motive, topic)
        )
        expression_projection = self._relationship_proactive_temperature(user, now=check_now).get("expression_decision")
        gate_until = (
            _safe_float(expression_projection.get("proactive_cooldown_until"), 0)
            if isinstance(expression_projection, dict)
            else 0
        )
        base_after = max(check_now + 90 * 60, gate_until + random.uniform(15 * 60, 75 * 60))
        if intimate or mode in {"refusing", "backoff"}:
            self._mark_planned_candidate_status(user, "deferred", f"情绪 {mode}: 亲密主动候选已清理/延后")
            self._clear_pending_proactive_plan(user)
            if mode == "hurt":
                user["next_proactive_at"] = base_after + random.uniform(20 * 60, 90 * 60)
                user["planned_proactive_reason"] = "quiet_care"
                user["planned_proactive_action"] = "message"
                user["planned_proactive_source"] = "emotion_gate"
                user["planned_proactive_motive"] = "Bot 还在收敛情绪,只留一条很短的低压关心,不贴近也不追问"
                user["planned_proactive_topic"] = "情绪收敛后的低压关心"
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_window_start_at"] = user["next_proactive_at"]
                active_span, grace_span = self._proactive_impulse_default_window_seconds(
                    user["planned_proactive_reason"],
                    source="emotion_gate",
                )
                user["planned_proactive_best_until_at"] = user["next_proactive_at"] + active_span
                user["planned_proactive_expire_at"] = user["next_proactive_at"] + active_span + grace_span
                semantics = self._planned_proactive_semantics(user)
                user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
                self._store_planned_proactive_route_fields(
                    user,
                    {
                        "source": "emotion_gate",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": user["next_proactive_at"],
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                    },
                )
                item = self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": "emotion_gate",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": user["next_proactive_at"],
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                        "score": 32,
                    },
                    status="accepted",
                    note="情绪 hurt: 恢复后低压关心候选",
                    user=user,
                )
                user["planned_candidate_id"] = item.get("id", "")
                saver = getattr(self, "_schedule_data_save", None)
                if callable(saver):
                    saver(sections={"users"})
                return "情绪 hurt 收敛中,亲密主动候选已延后"
            scheduler = getattr(self, "_schedule_next_proactive", None)
            if callable(scheduler):
                scheduler(user, now=base_after, delay_hours=(0.5, 2.0))
            if _safe_float(user.get("next_proactive_at"), 0) <= check_now:
                user["next_proactive_at"] = base_after
                user["planned_proactive_window_start_at"] = base_after
                user["planned_proactive_best_until_at"] = base_after + 45 * 60
                user["planned_proactive_expire_at"] = base_after + 90 * 60
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"users"})
            return f"情绪/关系 {mode} 收敛中,亲密主动候选已清理"
        defer = getattr(self, "_defer_or_replace_planned_impulse", None)
        if callable(defer):
            delay_minutes = max(1.0, (base_after - check_now) / 60)
            defer(
                user,
                now=check_now,
                note=f"情绪 {mode}: 主动候选延后",
                delay_minutes=(delay_minutes, delay_minutes + 30.0),
                block_current=False,
            )
        else:
            self._mark_planned_candidate_status(user, "deferred", f"情绪 {mode}: 主动候选延后")
            user["next_proactive_at"] = max(_safe_float(user.get("next_proactive_at"), 0), base_after)
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            saver(sections={"users"})
        return f"情绪 {mode} 收敛中,主动候选已延后"

    def _normalize_existing_plan_for_emotion(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        if str(user.get("planned_proactive_source") or "") == "timer":
            return ""
        reason = str(user.get("planned_proactive_reason") or "")
        action = str(user.get("planned_proactive_action") or "message")
        motive = _single_line(user.get("planned_proactive_motive"), 140)
        topic = _single_line(user.get("planned_proactive_topic"), 60)
        scheduled = _safe_float(user.get("next_proactive_at"), 0)
        adjusted = self._apply_emotion_to_planned_proactive(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            scheduled=scheduled,
            now=check_now,
        )
        note = _single_line(adjusted.get("note"), 160)
        if not note:
            return ""
        user["planned_proactive_reason"] = str(adjusted.get("reason") or reason)
        user["planned_proactive_action"] = str(adjusted.get("action") or action)
        user["planned_proactive_motive"] = _single_line(adjusted.get("motive"), 140) or motive
        user["planned_proactive_topic"] = _single_line(adjusted.get("topic"), 60) or topic
        user["next_proactive_at"] = _safe_float(adjusted.get("scheduled"), scheduled)
        self._mark_planned_candidate_status(user, "accepted", note)
        saver = getattr(self, "_schedule_data_save", None)
        if callable(saver):
            saver(sections={"users"})
        return note

    def _friend_proactive_scheduled_too_early(
        self,
        user: dict[str, Any],
        scheduled_at: float,
    ) -> bool:
        if self._private_user_role(user) != "friend" or scheduled_at <= 0:
            return False
        last_sent = _safe_float(user.get("last_sent"), 0)
        if last_sent <= 0:
            return False
        return scheduled_at - last_sent < self._effective_min_interval_seconds(user)
