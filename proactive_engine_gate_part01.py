# -*- coding: utf-8 -*-
"""ProactiveEngineGatePart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_gate.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 666 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineGateMixin）。
"""
from __future__ import annotations

from .proactive_engine_gate_shared import logger
from .proactive_engine_gate_shared import Any
from .proactive_engine_gate_shared import PROACTIVE_ROUTE_REGISTRY
from .proactive_engine_gate_shared import _engine_host
from .proactive_engine_gate_shared import _engine_proactive_window_timezone
from .proactive_engine_gate_shared import _safe_float
from .proactive_engine_gate_shared import _safe_int
from .proactive_engine_gate_shared import _single_line
from .proactive_engine_gate_shared import runtime_persona_setting



class ProactiveEngineGatePart01Mixin:
    """ProactiveEngineGatePart01Mixin（从 ProactiveEngineGateMixin 拆出）。"""


    def _should_send(self, user: dict[str, Any]) -> tuple[bool, str]:
        self._recover_stale_proactive_sending(user)
        user_id = str(user.get("user_id") or user.get("id") or "")
        planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        is_troubleshooting = planned_source == "troubleshooting"
        if not self._user_enabled_for_proactive(user_id, user):
            self._clear_pending_proactive_plan(user)
            return False, "私聊对象未启用"
        if self._proactive_generation_disabled(user):
            self._suspend_user_proactive_generation(user)
            reason_formatter = getattr(self, "_format_daily_limit_disabled_reason", None)
            if callable(reason_formatter):
                return False, reason_formatter(user)
            return False, "每日上限为 0，主动生成已停止"
        if user.get("proactive_sending"):
            return False, "上一条主动消息仍在发送中"
        umo_filled = False
        filler = getattr(self, "_ensure_private_user_umo", None)
        if callable(filler):
            try:
                umo_filled = bool(filler(user_id, user))
            except Exception:
                umo_filled = False
        if not user.get("umo"):
            return False, "缺少私聊会话"
        if umo_filled:
            logger.info(
                "已为主动私聊对象补全 UMO: user=%s umo=%s",
                _single_line(user_id, 40),
                _single_line(user.get("umo"), 120),
            )
        daily_limit = self._effective_user_daily_limit(user)
        if daily_limit <= 0:
            reason_formatter = getattr(self, "_format_daily_limit_disabled_reason", None)
            if callable(reason_formatter):
                return False, reason_formatter(user)
            return False, "每日上限为 0"
        if self._simulation_active(user):
            return self._should_send_simulation(user)
        now = _engine_host._now_ts()
        due_timer_active = self._has_due_llm_timer(user, now=now)
        planned_timezone = _single_line(
            user.get("planned_proactive_window_timezone"),
            64,
        )
        current_timezone = _engine_proactive_window_timezone(self)
        if (
            planned_timezone
            and planned_timezone != current_timezone
            and planned_source not in {"timer", "troubleshooting", "simulation"}
            and not due_timer_active
        ):
            self._mark_planned_candidate_status(
                user,
                "blocked",
                "运行时区已变化，旧主动窗口已作废",
            )
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now)
            return False, "运行时区已变化，已重新安排主动窗口"
        timeliness = self._planned_proactive_timeliness_level(user)
        if not is_troubleshooting:
            route_preflight_getter = getattr(self, "_planned_proactive_route_preflight", None)
            if callable(route_preflight_getter):
                route_preflight = route_preflight_getter(user, now=now)
            else:
                route = PROACTIVE_ROUTE_REGISTRY.route_for(
                    reason=planned_reason,
                    source=planned_source,
                    semantic_kind=user.get("planned_proactive_semantic_kind"),
                    kind=user.get("planned_proactive_kind"),
                )
                route_preflight = route.preflight(
                    user,
                    {
                        "reason": planned_reason,
                        "source": planned_source,
                        "trigger_message_id": user.get("planned_proactive_trigger_message_id"),
                        "trigger_inbound_count": user.get("planned_proactive_trigger_inbound_count"),
                        "private_inbound_count": user.get("private_inbound_count"),
                        "expire_at": user.get("planned_proactive_expire_at"),
                    },
                    now=now,
                )
            user["planned_proactive_route_preflight_action"] = _single_line(route_preflight.action, 32)
            user["planned_proactive_route_preflight_note"] = _single_line(route_preflight.reason, 180)
            if not route_preflight.allowed:
                note = _single_line(route_preflight.reason, 160) or "主动路线准入未通过"
                if route_preflight.action == "defer":
                    delay = route_preflight.defer_minutes
                    self._defer_or_replace_planned_impulse(
                        user,
                        now=now,
                        note=note,
                        delay_minutes=delay if delay != (0.0, 0.0) else (30.0, 90.0),
                        block_current=False,
                    )
                else:
                    self._mark_planned_candidate_status(user, "blocked", note)
                    self._clear_pending_proactive_plan(user)
                return False, note
        if (
            not is_troubleshooting
            and not due_timer_active
            and (planned_source == "creative_writing" or planned_reason == "creative_share")
            and not bool(runtime_persona_setting(self, "enable_creative_writing", False))
        ):
            self._mark_planned_candidate_status(user, "blocked", "创作功能未开启，已清理旧的创作分享候选")
            user["creative_share_context"] = {}
            self._clear_pending_proactive_plan(user)
            schedule_save = getattr(self, "_schedule_data_save", None)
            if callable(schedule_save):
                schedule_save(sections={"users"})
            return False, "创作功能未开启"
        if not is_troubleshooting and planned_source == "timer" and not due_timer_active:
            self._clear_llm_timer_internal_plan_fields(user)
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now)
            return False, "对话临时预约已交给官方定时计划"
        planned_impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        planned_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
        if (
            not is_troubleshooting
            and planned_expire_at > 0
            and now > planned_expire_at
            and not due_timer_active
            and planned_source != "timer"
        ):
            expired_note = "潜在念头窗口已过期" if planned_impulse_id else "主动计划窗口已过期"
            self._mark_planned_candidate_status(user, "blocked", expired_note)
            self._clear_pending_proactive_plan(user)
            if not self._materialize_best_proactive_impulse(user, now=now):
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "原主动计划已过期,已重新挑选"
        silence_reason_getter = getattr(self, "_friend_unanswered_silence_reason", None)
        silence_reason = silence_reason_getter(user, now=now) if callable(silence_reason_getter) else ""
        if (
            silence_reason
            and not is_troubleshooting
            and not due_timer_active
            and planned_source not in {"timer", "simulation"}
        ):
            blocker = getattr(self, "_block_friend_unanswered_pending_proactive", None)
            if callable(blocker):
                blocker(user, note=silence_reason, now=now)
            self._mark_planned_candidate_status(user, "blocked", silence_reason)
            self._clear_pending_proactive_plan(user)
            return False, silence_reason
        if (
            not is_troubleshooting
            and
            self._proactive_rest_block_until(
                user,
                now=now,
                reason=user.get("planned_proactive_reason"),
                source=planned_source,
            ) > now
            and not due_timer_active
        ):
            return False, "用户明确休息中"
        busy_until = 0.0
        busy_block_kind = ""
        busy_block_note = ""
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if not is_troubleshooting and not due_timer_active and callable(busy_context_getter):
            try:
                busy_context = busy_context_getter(
                    user,
                    now=now,
                    reason=user.get("planned_proactive_reason"),
                    source=planned_source,
                )
                if isinstance(busy_context, dict):
                    busy_until = _safe_float(busy_context.get("until"), 0.0)
                    busy_block_kind = _single_line(busy_context.get("kind"), 40)
                    busy_block_note = _single_line(busy_context.get("note"), 160)
            except Exception:
                busy_until = 0.0
        elif not is_troubleshooting and not due_timer_active and callable(busy_gate):
            try:
                busy_until = _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason=user.get("planned_proactive_reason"),
                        source=planned_source,
                    ),
                    0.0,
                )
            except Exception:
                busy_until = 0.0
        if busy_until > now and (timeliness == "routine" or busy_block_kind == "external_realtime"):
            defer_busy = getattr(self, "_defer_proactive_for_busy", None)
            changed = bool(defer_busy(user, now=now, until=busy_until)) if callable(defer_busy) else False
            if changed:
                external_realtime = busy_block_kind == "external_realtime"
                defer_note = (
                    "Bot 正在与用户实时共处，已顺延到共同活动结束后"
                    if external_realtime
                    else "Bot 当前日程忙碌，已顺延到忙完后"
                )
                self._mark_planned_candidate_status(user, "deferred", defer_note)
                schedule_save = getattr(self, "_schedule_data_save", None)
                if callable(schedule_save):
                    schedule_save(sections={"users"})
                logger.info(
                    "%s已顺延主动消息: user=%s until=%s reason=%s source=%s detail=%s",
                    "实时共处期间" if external_realtime else "繁忙回复闸门",
                    _single_line(user.get("user_id") or user.get("umo") or user.get("nickname"), 80),
                    int(busy_until),
                    _single_line(user.get("planned_proactive_reason"), 48) or "check_in",
                    planned_source or "unknown",
                    busy_block_note or "-",
                )
            if busy_block_kind == "external_realtime":
                return False, "正在实时共处，普通主动消息已顺延"
            return False, "Bot 当前日程忙碌，主动消息已顺延"
        # Refresh time-sensitive rituals before the quiet-hours and quota gates
        # so their narrow midnight window is not hidden behind an older plan.
        if not is_troubleshooting and not due_timer_active and self._promote_earlier_daily_greeting_event(user, now=now):
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            impulse_value = self._planned_impulse_value(user, now=now)
            window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        post_goodnight_active = self._post_goodnight_group_activity_is_fresh(user, now=now)
        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        ritual_context = (
            user.get("planned_birthday_event_context")
            if planned_reason == "birthday_celebration"
            else user.get("planned_special_day_context")
        )
        if not isinstance(ritual_context, dict):
            ritual_context = {}
        midnight_ritual_active = planned_reason in {"birthday_celebration", "special_day_greeting"} and (
            _single_line(ritual_context.get("delivery_timing"), 24) == "midnight"
        )
        insomnia_slot_available = planned_reason == "insomnia_night" and self._can_send_insomnia_night_message(user, now=now)
        if (
            not is_troubleshooting
            and self._is_quiet_time()
            and not insomnia_slot_available
            and not midnight_ritual_active
            and not post_goodnight_active
        ):
            return False, "免打扰时段"
        pre_gate_next_at = _safe_float(user.get("next_proactive_at"), 0)
        if not is_troubleshooting and not due_timer_active:
            if pre_gate_next_at <= 0:
                self._schedule_next_proactive(user, now=now)
                return False, "已安排下一次候选主动时间"
            if now < pre_gate_next_at:
                return False, "未到候选主动时间"
        relationship_mode = self._current_relationship_gate_mode(user, now=now) if not is_troubleshooting else ""
        emotion_mode = self._current_emotion_gate_mode(user, now=now) if not is_troubleshooting else ""
        relationship_blocked = relationship_mode == "backoff"
        emotion_blocked = emotion_mode == "hurt"
        if relationship_blocked or emotion_blocked:
            interaction = user.get("current_interaction") if isinstance(user.get("current_interaction"), dict) else {}
            gate_until = _safe_float(interaction.get("expires_at"), 0)
            if relationship_blocked:
                gate_until = max(gate_until, now + 6 * 3600)
            before_next_at = _safe_float(user.get("next_proactive_at"), 0)
            adjuster = getattr(self, "_defer_or_clean_emotion_blocked_plan", None)
            if callable(adjuster):
                adjusted_reason = adjuster(user, now=now)
            else:
                adjusted_reason = "情绪/关系状态处于收敛期"
            after_next_at = _safe_float(user.get("next_proactive_at"), 0)
            if after_next_at <= now and gate_until > now:
                after_next_at = gate_until + _engine_host.random.uniform(15 * 60, 75 * 60)
                user["next_proactive_at"] = after_next_at
                user["planned_proactive_window_start_at"] = after_next_at
                user["planned_proactive_best_until_at"] = after_next_at + 45 * 60
                user["planned_proactive_expire_at"] = after_next_at + 90 * 60
            logger.info(
                "统一互动/联系边界闸门拦截主动: mode=%s gate_until=%s reason=%s",
                relationship_mode or emotion_mode,
                int(gate_until),
                _single_line(interaction.get("reason"), 80),
            )
            return False, adjusted_reason

        planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        if due_timer_active and planned_source != "timer":
            self._promote_due_llm_timer_plan(user, now=now)
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            self._schedule_next_proactive(user, now=now)
            return False, "已安排下一次候选主动时间"
        impulse_value = self._planned_impulse_value(user, now=now)
        window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        if (
            not is_troubleshooting
            and planned_impulse_id
            and window_phase == "tail"
            and impulse_value < 0.28
            and not due_timer_active
            and timeliness == "routine"
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="低价值念头已过最佳表达窗口",
                delay_minutes=(45, 120),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "低价值念头已过最佳窗口,已重新挑选"
        if not is_troubleshooting and self._promote_earlier_daily_greeting_event(user, now=now):
            planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
            planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or planned_source
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            impulse_value = self._planned_impulse_value(user, now=now)
            window_phase, window_detail = self._planned_impulse_window_phase(user, now=now)
        if (
            not is_troubleshooting
            and
            not due_timer_active
            and planned_source != "timer"
            and self._in_llm_timer_silence_window(user, now=now)
        ):
            self._remember_silenced_plan_for_timer(user, now=now)
            self._promote_upcoming_llm_timer_plan(user, now=now)
            return False, "用户预约静默窗口"
        if now < next_at:
            return False, "未到候选主动时间"
        delivery = self._ensure_planned_proactive_delivery_state(user, now=now)
        if (
            not is_troubleshooting
            and not due_timer_active
            and _single_line(delivery.get("freshness"), 24) == "immediate"
            and _safe_float(delivery.get("best_until_at"), 0) > 0
            and now > _safe_float(delivery.get("best_until_at"), 0)
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="即时主动已越过自然表达窗口",
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(1.0, 3.0))
            return False, "即时主动已越过自然表达窗口,已重新挑选"
        if not is_troubleshooting and self._is_proactive_plan_stale(user, now=now) and not due_timer_active:
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(1, 4))
            return False, "候选主动计划已过期,已重新安排"
        inner_readiness = self._proactive_inner_readiness(user, now=now)
        inner_score = _safe_float(inner_readiness.get("score"), 0.55)
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and inner_score < 0.36
            and impulse_value < 0.72
            and timeliness == "routine"
        ):
            logger.debug(
                "Bot 表达温度偏低，交由正文提示收敛为短句而不延后: user=%s detail=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(inner_readiness.get("detail"), 120),
            )
        social_relay_note = self._unverified_social_relay_plan_reason(
            user,
            source=planned_source,
            has_trigger=bool(_single_line(user.get("planned_proactive_trigger_message_id"), 120)),
        )
        if not is_troubleshooting and social_relay_note:
            self._mark_planned_candidate_status(user, "blocked", social_relay_note)
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(1.5, 4.5))
            return False, social_relay_note
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and self._is_greeting_reason(planned_reason)
            and self._recent_activity_satisfies_greeting(user, planned_reason, now=now)
        ):
            self._mark_greeting_satisfied_by_inbound(user, planned_reason)
            self._mark_planned_candidate_status(user, "blocked", "用户在该问候窗口附近已经自然聊过")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "用户在该问候窗口附近已经自然聊过"
        suppressed_raw = user.get("greetings_suppressed_by_inbound", [])
        suppressed_greetings: set[str] = set()
        if isinstance(suppressed_raw, list):
            suppressed_greetings = {str(item).strip() for item in suppressed_raw if str(item).strip()}
        if (
            not is_troubleshooting
            and planned_reason in suppressed_greetings
            and self._is_greeting_reason(planned_reason)
            and planned_source != "timer"
            and not due_timer_active
        ):
            self._mark_planned_candidate_status(user, "blocked", "用户在该问候窗口内已经活跃过")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "用户在该问候窗口内已经活跃过"
        self._reset_daily_counter_if_needed(user)
        if (
            not is_troubleshooting
            and planned_reason == "morning_greeting"
            and planned_source != "timer"
            and not due_timer_active
            and self._greeting_was_sent_today(user, planned_reason)
        ):
            self._mark_planned_candidate_status(user, "blocked", "今天已经自然说过早安")
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 5))
            return False, "今天已经自然说过早安"
        if (
            not is_troubleshooting
            and not self._proactive_daily_limit_is_unlimited(daily_limit)
            and _safe_int(user.get("sent_today"), 0) >= daily_limit
            and not insomnia_slot_available
        ):
            if not due_timer_active:
                self._schedule_next_proactive(user, now=now, delay_hours=(8, 16))
            return False, "已达每日上限"
        idle_minutes = self._effective_user_idle_minutes(user)
        recent_activity_at = self._latest_private_user_activity_ts(user)
        if (
            not is_troubleshooting
            and not due_timer_active
            and not self._post_goodnight_group_activity_is_fresh(user, now=now)
            and now - recent_activity_at < idle_minutes * 60
        ):
            idle_limit = (
                self._effective_user_greeting_idle_minutes(user) * 60
                if self._is_greeting_reason(planned_reason)
                else idle_minutes * 60
            )
            timely_idle_floor = 0.0
            if timeliness == "urgent":
                timely_idle_floor = 2 * 60.0
            elif timeliness == "timely":
                timely_idle_floor = 5 * 60.0
            if now - recent_activity_at < (min(idle_limit, timely_idle_floor) if timely_idle_floor > 0 else idle_limit):
                if self._is_sticky_greeting_reason(planned_reason):
                    self._reschedule_greeting_within_window(user, planned_reason, now=now)
                else:
                    replaced = self._defer_or_replace_planned_impulse(
                        user,
                        now=now,
                        note="用户刚活跃过,当前念头先收住",
                        delay_minutes=(max(8.0, idle_limit / 60 * 0.5), max(15.0, idle_limit / 60 + 8.0)),
                        block_current=impulse_value < 0.52,
                    )
                    if replaced:
                        return False, "用户刚活跃过,已换用更贴近当前节奏的念头"
                return False, "用户刚活跃过"
        min_interval = self._effective_min_interval_seconds(user)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(user) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        if timeliness == "urgent":
            min_interval = min(min_interval, 2 * 60.0)
        elif timeliness == "timely":
            min_interval = min(min_interval, 10 * 60.0)
        if (
            not is_troubleshooting
            and not due_timer_active
            and not bool(user.get("planned_proactive_burst"))
            and now - _safe_float(user.get("last_sent"), 0) < min_interval
        ):
            if self._is_sticky_greeting_reason(planned_reason):
                self._reschedule_greeting_within_window(user, planned_reason, now=now)
            else:
                remaining_minutes = max(5.0, (min_interval - (now - _safe_float(user.get("last_sent"), 0))) / 60)
                self._defer_or_replace_planned_impulse(
                    user,
                    now=now,
                    note="距离上次主动太近,当前念头先压低",
                    delay_minutes=(remaining_minutes, remaining_minutes + 30.0),
                    block_current=False,
                )
            return False, "发送间隔不足"
        planned_action = str(user.get("planned_proactive_action") or "message")
        normalizer = getattr(self, "_normalize_existing_plan_for_emotion", None)
        if not is_troubleshooting and callable(normalizer):
            emotion_note = normalizer(user, now=now)
            if emotion_note:
                planned_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or planned_reason
                planned_action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or planned_action or "message"
                if _safe_float(user.get("next_proactive_at"), 0) > now + 1:
                    return False, emotion_note
        if not is_troubleshooting and self._private_user_role(user) == "friend":
            before_friend_sanitize = (
                planned_reason,
                planned_action,
                _single_line(user.get("planned_proactive_topic"), 80),
                _single_line(user.get("planned_proactive_motive"), 180),
            )
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=planned_reason,
                action=planned_action,
                topic=_single_line(user.get("planned_proactive_topic"), 80),
                motive=_single_line(user.get("planned_proactive_motive"), 180),
            )
            user["planned_proactive_reason"] = sanitized["reason"]
            user["planned_proactive_action"] = sanitized["action"]
            user["planned_proactive_topic"] = sanitized["topic"]
            user["planned_proactive_motive"] = sanitized["motive"]
            planned_reason = sanitized["reason"]
            planned_action = sanitized["action"]
            after_friend_sanitize = (
                planned_reason,
                planned_action,
                sanitized["topic"],
                sanitized["motive"],
            )
            if after_friend_sanitize != before_friend_sanitize:
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_semantic_kind"] = ""
                user["planned_proactive_anchor_type"] = ""
                user["planned_proactive_semantic_score"] = 0
                user["planned_proactive_semantic_note"] = ""
                user["planned_proactive_need_layer"] = ""
                user["planned_proactive_need_drive"] = ""
                user["planned_proactive_need_note"] = ""
                user["planned_proactive_model_judge_signature"] = ""
                user["planned_proactive_model_judge_result"] = {}
                user["planned_proactive_model_judge_at"] = 0
                self._mark_planned_candidate_status(user, "accepted", "次要用户未回应状态下已降级为低压主动")
        if not is_troubleshooting and not self._friend_can_receive_proactive_reason(user, planned_reason, planned_action):
            self._clear_pending_proactive_plan(user)
            self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "次要用户关系不接收敏感主动"
        planned_semantics = self._planned_proactive_semantics(user)
        semantic_score = _safe_float(planned_semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(planned_semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(planned_semantics.get("risk"), 0.0)
        semantic_blocked = bool(planned_semantics.get("blocker"))
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and (semantic_blocked or semantic_risk >= 0.70)
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="候选语义不够自然: " + _single_line(planned_semantics.get("note"), 120),
                delay_minutes=(90, 240),
                block_current=semantic_blocked or semantic_risk >= 0.70,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "候选语义不够自然,已重新挑选"
        if semantic_score < 0.32 and semantic_pressure >= 0.58:
            logger.debug(
                "候选由头偏弱且压力偏高，交由正文提示改成低压短句: user=%s note=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(planned_semantics.get("note"), 120),
            )
        persona_alignment = self._planned_proactive_persona_alignment(user, now=now)
        persona_fit = _safe_float(persona_alignment.get("score"), 0.55)
        persona_blocked = bool(persona_alignment.get("blocker"))
        persona_threshold = 0.48 if self._private_user_role(user) == "friend" else 0.42
        if (
            not is_troubleshooting
            and not due_timer_active
            and planned_source != "timer"
            and persona_blocked
        ):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="人格/世界观贴合度不足: " + _single_line(persona_alignment.get("note"), 120),
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "人格/世界观贴合度不足,已重新挑选"
        if persona_fit < persona_threshold:
            logger.debug(
                "人格贴合度偏低，交由人格判定/正文生成修正而不延后: user=%s fit=%.2f note=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                persona_fit,
                _single_line(persona_alignment.get("note"), 120),
            )
        if due_timer_active:
            return True, "ok(timer)"
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)
        if (
            not is_troubleshooting
            and ignored_streak >= 2
            and impulse_value < (0.72 if self._private_user_role(user) == "friend" else 0.66)
            and timeliness == "routine"
        ):
            logger.debug(
                "连续未回应时保留低压候选，由提示词缩短且禁止追问: user=%s ignored=%s value=%.2f",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                ignored_streak,
                impulse_value,
            )
        if not is_troubleshooting and not self._is_reason_allowed_now(planned_reason, user):
            if self._is_sticky_greeting_reason(planned_reason):
                self._reschedule_greeting_within_window(user, planned_reason, now=now)
                return False, "问候仍在窗口内,稍后再试"
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="计划动机不适合当前时间",
                delay_minutes=(45, 150),
                block_current=window_phase == "tail" or impulse_value < 0.6,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now)
            return False, "计划动机不适合当前时间"
        if self._private_user_role(user) == "friend" and self._action_has_photo_text(planned_action):
            fallback_action = self._fallback_action_for_unavailable(planned_action, user)
            if fallback_action != planned_action:
                planned_action = fallback_action
                user["planned_proactive_action"] = planned_action
        if not self._action_is_available(planned_action, user):
            load_defer_note = self._photo_text_load_defer_note(planned_action)
            if load_defer_note:
                self._defer_planned_photo_text_for_load(user, now=now, note=load_defer_note)
                return False, load_defer_note
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="动作不可用或媒体额度不足",
                delay_minutes=(90, 240),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "动作不可用或媒体额度不足"
        if not is_troubleshooting and timeliness == "routine" and self._planned_proactive_recently_repeated(user):
            replaced = self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="近期主题过于相似",
                delay_minutes=(120, 360),
                block_current=True,
            )
            if not replaced and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=(2, 6))
            return False, "近期主动主题过于相似"
        if not is_troubleshooting and timeliness == "routine" and self._planned_event_exceeds_daypart_cap(user, planned_reason, next_at):
            delay = self._friend_proactive_spread_delay_hours(user, now=now)
            if delay is None:
                delay = (7.5, 10.5) if self._proactive_daypart_bucket_for_timestamp(next_at) == "late_night" else (2.5, 5.0)
            self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note="当前时段主动已足够",
                delay_minutes=(delay[0] * 60, delay[1] * 60),
                block_current=False,
            )
            if _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=now, delay_hours=delay)
            if self._private_user_role(user) == "friend":
                return False, "朋友主动已按日内节奏延后"
            return False, "当前时段主动已足够,已避开扎堆"
        return True, "ok"
