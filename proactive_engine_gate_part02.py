# -*- coding: utf-8 -*-
"""ProactiveEngineGatePart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_gate.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 442 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineGateMixin）。
"""
from __future__ import annotations
from .proactive_engine_gate_shared import Any
from .proactive_engine_gate_shared import _engine_host
from .proactive_engine_gate_shared import _safe_float
from .proactive_engine_gate_shared import _safe_int
from .proactive_engine_gate_shared import _single_line



class ProactiveEngineGatePart02Mixin:
    """ProactiveEngineGatePart02Mixin（从 ProactiveEngineGateMixin 拆出）。"""


    def _proactive_decision_factors(self, user: dict[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
        now = _engine_host._now_ts() if now is None else now
        factors: list[dict[str, Any]] = []

        def add(
            key: str,
            label: str,
            passed: bool,
            score: int,
            detail: str = "",
            *,
            blocker: bool = False,
        ) -> None:
            factors.append(
                {
                    "key": key,
                    "label": label,
                    "passed": bool(passed),
                    "score": int(score),
                    "detail": _single_line(detail, 160),
                    "blocker": bool(blocker),
                }
            )

        user_id = str(user.get("user_id") or user.get("id") or "")
        enabled = self._user_enabled_for_proactive(user_id, user)
        add("enabled", "用户启用", enabled, 18 if enabled else -80, "已启用" if enabled else "私聊对象未启用", blocker=not enabled)

        has_session = bool(user.get("umo"))
        add("session", "私聊会话", has_session, 12 if has_session else -70, "会话可用" if has_session else "缺少私聊会话", blocker=not has_session)

        if user.get("proactive_sending"):
            add("sending", "发送占用", False, -60, "上一条主动消息仍在发送中", blocker=True)
        else:
            add("sending", "发送占用", True, 6, "当前没有发送占用")

        daily_limit = self._effective_user_daily_limit(user)
        sent_today = _safe_int(user.get("sent_today"), 0)
        unlimited_daily_limit = self._proactive_daily_limit_is_unlimited(daily_limit)
        under_limit = daily_limit > 0 and (unlimited_daily_limit or sent_today < daily_limit)
        daily_limit_text = self._format_proactive_daily_limit(daily_limit)
        if daily_limit <= 0:
            add("daily_limit", "每日上限", False, -55, "每日上限为 0", blocker=True)
        else:
            add(
                "daily_limit",
                "每日上限",
                under_limit,
                8 if under_limit else -40,
                f"{sent_today}/{daily_limit_text}",
                blocker=not under_limit,
            )

        due_timer_active = self._has_due_llm_timer(user, now=now)
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        timeliness = self._planned_proactive_timeliness_level(user)
        if timeliness != "routine":
            add(
                "timeliness",
                "消息时效",
                True,
                8 if timeliness == "urgent" else 5,
                "紧急事件：放宽普通频率闸门" if timeliness == "urgent" else "短时效事件：适度放宽普通频率闸门",
                blocker=False,
            )
        rest_until = self._proactive_rest_block_until(
            user,
            now=now,
            reason=user.get("planned_proactive_reason"),
            source=source,
        )
        rest_blocked = rest_until > now and not due_timer_active
        add(
            "rest",
            "休息静默",
            not rest_blocked,
            5 if not rest_blocked else -45,
            "未命中静默" if not rest_blocked else "用户明确休息中",
            blocker=rest_blocked,
        )

        busy_until = 0.0
        busy_block_kind = ""
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter(
                    user,
                    now=now,
                    reason=user.get("planned_proactive_reason"),
                    source=source,
                )
                if isinstance(busy_context, dict):
                    busy_until = _safe_float(busy_context.get("until"), 0.0)
                    busy_block_kind = _single_line(busy_context.get("kind"), 40)
            except Exception:
                busy_until = 0.0
        elif callable(busy_gate):
            try:
                busy_until = _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason=user.get("planned_proactive_reason"),
                        source=source,
                    ),
                    0.0,
                )
            except Exception:
                busy_until = 0.0
        busy_blocked = (
            busy_until > now
            and not due_timer_active
            and (timeliness == "routine" or busy_block_kind == "external_realtime")
        )
        add(
            "bot_busy",
            "Bot 忙碌日程",
            not busy_blocked,
            4 if not busy_blocked else -35,
            (
                "短时效事件不受普通日程忙碌顺延"
                if busy_until > now and not busy_blocked
                else "当前不忙"
                if not busy_blocked
                else f"顺延到 {self._environment_fromtimestamp(busy_until).strftime('%H:%M')} 后"
            ),
            blocker=busy_blocked,
        )

        quiet_blocked = (
            self._is_quiet_time()
            and not self._can_send_insomnia_night_message(user)
            and not self._post_goodnight_group_activity_is_fresh(user, now=now)
        )
        add(
            "quiet_hours",
            "免打扰",
            not quiet_blocked,
            4 if not quiet_blocked else -42,
            "当前可发" if not quiet_blocked else "处于免打扰时段",
            blocker=quiet_blocked,
        )

        relationship_mode = self._current_relationship_gate_mode(user, now=now)
        emotion_mode = self._current_emotion_gate_mode(user, now=now)
        relationship_blocked = relationship_mode == "backoff"
        emotion_blocked = emotion_mode == "hurt"
        relation_ok = not (relationship_blocked or emotion_blocked)
        relation_detail = f"mode={relationship_mode or emotion_mode}" if not relation_ok else "状态平稳"
        add(
            "relationship_gate",
            "关系/情绪闸门",
            relation_ok,
            7 if relation_ok else -48,
            relation_detail,
            blocker=not relation_ok,
        )

        next_at = _safe_float(user.get("next_proactive_at"), 0)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if next_at <= 0:
            add("planned", "候选计划", False, -12, "尚未安排下一次候选")
        else:
            due = now >= next_at
            add(
                "planned",
                "候选计划",
                due,
                10 if due else -10,
                (
                    self._environment_fromtimestamp(next_at).strftime("%m-%d %H:%M:%S")
                    if next_at > 0
                    else "未安排"
                ),
                blocker=False,
            )
        impulse_value = self._planned_impulse_value(user, now=now)
        window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        phase_labels = {
            "before": "窗口未开始",
            "best": "最佳窗口",
            "tail": "窗口尾段",
            "expired": "已经过期",
            "unknown": "未记录",
        }
        window_ok = window_phase not in {"expired"}
        add(
            "impulse_window",
            "念头窗口",
            window_ok,
            7 if window_phase == "best" else 2 if window_phase == "tail" else -6 if window_phase == "before" else -35 if window_phase == "expired" else 0,
            f"{phase_labels.get(window_phase, window_phase)}｜{window_detail}",
            blocker=window_phase == "expired",
        )
        add(
            "impulse_value",
            "念头价值",
            impulse_value >= 0.55,
            8 if impulse_value >= 0.85 else 4 if impulse_value >= 0.65 else -8,
            f"{impulse_value:.2f}｜越高越像当前角色真的想说",
            blocker=False,
        )
        inner_readiness = self._proactive_inner_readiness(user, now=now)
        drive = inner_readiness.get("drive") if isinstance(inner_readiness.get("drive"), dict) else {}
        temperature = inner_readiness.get("temperature") if isinstance(inner_readiness.get("temperature"), dict) else {}
        inner_score = _safe_float(inner_readiness.get("score"), 0.55)
        add(
            "bot_drive",
            "Bot 开口欲",
            inner_score >= 0.36 or timeliness != "routine",
            7 if inner_score >= 0.72 else 3 if inner_score >= 0.5 else -14,
            f"{inner_score:.2f}｜{_single_line(inner_readiness.get('label'), 40)}｜{_single_line(drive.get('detail'), 70)}",
            blocker=inner_score < 0.28 and timeliness == "routine",
        )
        motivation = inner_readiness.get("motivation") if isinstance(inner_readiness.get("motivation"), dict) else {}
        if motivation:
            motivation_score = _safe_float(motivation.get("score"), 0.5)
            add(
                "experimental_motivation",
                "实验动机调度",
                motivation_score >= 0.40,
                6 if motivation_score >= 0.66 else 2 if motivation_score >= 0.50 else -10,
                f"{motivation_score:.2f}｜{_single_line(motivation.get('label'), 24)}｜{_single_line(motivation.get('detail'), 100)}",
                blocker=motivation_score < 0.28,
            )
        temp_score = _safe_float(temperature.get("score"), 0.55)
        add(
            "relationship_temperature",
            "主动表达温度",
            temp_score >= 0.34,
            7 if temp_score >= 0.7 else 3 if temp_score >= 0.48 else -16,
            f"{temp_score:.2f}｜{_single_line(temperature.get('label'), 24)}｜{_single_line(temperature.get('detail'), 80)}",
            blocker=temp_score < 0.24,
        )
        planned_impulse = self._planned_proactive_impulse(user)
        hesitation_count = _safe_int(planned_impulse.get("hesitation_count"), 0, 0, 20) if isinstance(planned_impulse, dict) else 0
        if hesitation_count > 0:
            add(
                "hesitation_memory",
                "犹豫记忆",
                True,
                min(6, 2 + hesitation_count),
                f"同一候选曾延后 {hesitation_count} 次｜{_single_line(planned_impulse.get('hesitation_note'), 80)}",
                blocker=False,
            )
        semantics = self._planned_proactive_semantics(user)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        semantic_ok = not bool(semantics.get("blocker")) and semantic_risk < 0.45 and not (semantic_score < 0.32 and semantic_pressure >= 0.58)
        add(
            "candidate_semantics",
            "候选语义",
            semantic_ok,
            8 if semantic_score >= 0.68 else 4 if semantic_score >= 0.48 else -18,
            (
                f"{_single_line(semantics.get('kind'), 30)}/{_single_line(semantics.get('anchor_type'), 30)}"
                f"｜语义{semantic_score:.2f} 压力{semantic_pressure:.2f} 风险{semantic_risk:.2f}"
                f"｜{_single_line(semantics.get('note'), 70)}"
            ),
            blocker=not semantic_ok,
        )
        persona_alignment = self._planned_proactive_persona_alignment(user, now=now)
        persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
        persona_threshold = 0.48 if self._private_user_role(user) == "friend" else 0.42
        persona_blocked = bool(persona_alignment.get("blocker")) and source != "timer"
        persona_ok = due_timer_active or source == "timer" or (not persona_blocked and persona_fit >= persona_threshold)
        add(
            "persona_fit",
            "人格/世界观贴合",
            persona_ok,
            8 if persona_fit >= 0.78 else 4 if persona_fit >= 0.58 else -18,
            f"{persona_fit:.2f}｜{_single_line(persona_alignment.get('note'), 110)}",
            blocker=not persona_ok,
        )
        model_signature = self._planned_proactive_model_judge_signature(user)
        model_judgement = self._cached_proactive_model_judgement(user, signature=model_signature, now=now)
        if isinstance(model_judgement, dict):
            model_decision = str(model_judgement.get("decision") or "")
            model_score = _safe_int(model_judgement.get("score"), 0, 0, 100)
            model_ok = model_decision in {"send", "rewrite"}
            add(
                "model_persona_judge",
                "模型人格判定",
                model_ok,
                8 if model_decision == "send" else 4 if model_decision == "rewrite" else -30,
                f"{model_decision or 'unknown'}｜{model_score}/100｜{_single_line(model_judgement.get('reason'), 90)}",
                blocker=not model_ok,
            )
        else:
            add(
                "model_persona_judge",
                "模型人格判定",
                True,
                0,
                "未执行；硬规则通过且到点发送前执行",
                blocker=False,
            )

        last_seen = self._latest_private_user_activity_ts(user)
        idle_minutes = self._effective_user_idle_minutes(user)
        if self._is_greeting_reason(planned_reason):
            idle_minutes = self._effective_user_greeting_idle_minutes(user)
        idle_seconds = max(0, idle_minutes) * 60
        if timeliness == "urgent":
            idle_seconds = min(idle_seconds, 2 * 60.0)
        elif timeliness == "timely":
            idle_seconds = min(idle_seconds, 5 * 60.0)
        idle_elapsed = now - last_seen if last_seen > 0 else 999999999.0
        idle_passed = due_timer_active or idle_elapsed >= idle_seconds
        add(
            "idle",
            "用户空闲",
            idle_passed,
            9 if idle_passed else -28,
            (
                f"已空闲 {self._format_elapsed(max(0, idle_elapsed))} / 至少 {self._format_elapsed(idle_seconds)}"
                if last_seen > 0
                else "暂无活跃记录"
            ),
            blocker=not idle_passed and not due_timer_active,
        )

        last_sent = _safe_float(user.get("last_sent"), 0)
        min_interval = self._effective_min_interval_seconds(user)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(user) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        if timeliness == "urgent":
            min_interval = min(min_interval, 2 * 60.0)
        elif timeliness == "timely":
            min_interval = min(min_interval, 10 * 60.0)
        send_elapsed = now - last_sent if last_sent > 0 else 999999999.0
        interval_passed = due_timer_active or send_elapsed >= min_interval
        add(
            "interval",
            "发送间隔",
            interval_passed,
            8 if interval_passed else -25,
            (
                f"已过 {self._format_elapsed(max(0, send_elapsed))} / 至少 {self._format_elapsed(min_interval)}"
                if last_sent > 0
                else "还没有主动发送记录"
            ),
            blocker=not interval_passed and not due_timer_active,
        )

        if planned_reason:
            reason_allowed = due_timer_active or self._is_reason_allowed_now(planned_reason, user)
            add(
                "reason_window",
                "时段适配",
                reason_allowed,
                6 if reason_allowed else -18,
                planned_reason,
                blocker=not reason_allowed and not due_timer_active,
            )

        planned_action = str(user.get("planned_proactive_action") or "message")
        action_ok = self._action_is_available(planned_action, user)
        add(
            "action",
            "动作可用",
            action_ok,
            6 if action_ok else -24,
            planned_action or "message",
            blocker=not action_ok,
        )

        repeated = self._planned_proactive_recently_repeated(user)
        dedupe_passed = not repeated or timeliness != "routine"
        add(
            "dedupe",
            "主题去重",
            dedupe_passed,
            6 if dedupe_passed else -20,
            (
                "同一事件仍由事件指纹去重，普通话题重复不阻断"
                if repeated and timeliness != "routine"
                else "近期无重复"
                if not repeated
                else "近期主动主题过于相似"
            ),
            blocker=not dedupe_passed,
        )

        total_score = 50 + sum(int(item.get("score") or 0) for item in factors)
        factors.append(
            {
                "key": "total",
                "label": "综合评分",
                "passed": total_score >= 50,
                "score": max(0, min(100, total_score)),
                "detail": "分数越高越适合现在发",
                "blocker": False,
            }
        )
        return factors

    def _passes_proactive_moment(self, user: dict[str, Any]) -> bool:
        hour = self._environment_now().hour
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        active_conditions = state.get("conditions", []) if isinstance(state, dict) else []
        current_item = self._proactive_current_agenda_item()
        can_do = self.data.get("can_do", [])
        important_dates = self._get_relevant_important_dates()
        ignored_streak = _safe_int(user.get("ignored_streak"), 0)

        probability = 0.32
        if 8 <= hour <= 11:
            probability += 0.16
        elif 14 <= hour <= 17:
            probability += 0.16
        elif 19 <= hour <= 22:
            probability += 0.18
        else:
            probability -= 0.05

        if energy < 40:
            probability += 0.12
        elif energy > 80:
            probability += 0.06
        if active_conditions:
            probability += min(0.18, len(active_conditions) * 0.06)
        if current_item:
            probability += 0.08
        if isinstance(can_do, list) and can_do:
            probability += 0.12
        if current_item and _single_line(current_item.get("message_seed"), 80):
            probability += 0.12
        if important_dates:
            probability += 0.1 if _safe_int(important_dates[0].get("_days_until"), 0) == 0 else 0.05
        unanswered_weight = _safe_float(
            self._proactive_quota_policy(user).get("unanswered_interval_weight"),
            1.0,
            0.0,
        )
        probability -= min(0.18, ignored_streak * 0.07) * min(1.0, unanswered_weight)
        probability *= self._daily_intensity_factor(user)
        probability = max(0.12, min(0.9, probability))
        return _engine_host.random.random() < probability
