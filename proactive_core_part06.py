# -*- coding: utf-8 -*-
"""ProactivePart06Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 583 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import logger
from .proactive_core_shared import Any
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import _unanswered_proactive_count
from .proactive_core_shared import random



class ProactivePart06Mixin:
    """ProactivePart06Mixin（从 ProactiveMixin 拆出）。"""


    def _random_proactive_impulse_context(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> dict[str, Any]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias") or state.get("mood"), 24)
        note = _single_line(state.get("note"), 120)
        ignored_streak = _unanswered_proactive_count(user)
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        last_sent = _safe_float(user.get("last_sent"), 0)
        reasons: list[str] = []
        suggest_soft_reason = False
        if awaiting_since > 0:
            silent_hours = (now - awaiting_since) / 3600.0
            if silent_hours >= 3.0:
                reasons.append(f"已等待回复 {silent_hours:.1f}h")
                suggest_soft_reason = True
        elif ignored_streak >= 1 and last_sent > 0 and now - last_sent >= 3 * 3600:
            reasons.append(f"未回应 {ignored_streak} 次")
            suggest_soft_reason = True
        low_energy = energy <= 45
        quiet_mood = mood in {"安静", "疲惫", "低落", "收声", "困倦"}
        if low_energy or quiet_mood or any(token in note for token in ("疲惫", "困", "低电量", "收声", "慢一点", "安静")):
            reasons.append(f"状态偏低({energy}/{mood or '平稳'})")
            suggest_soft_reason = True
        if not reasons:
            reasons.append("当前没有更高优先级事件，可生成一个自然、具体、低压力的日常念头")
        return {
            "allowed": True,
            "reasons": reasons,
            "suggest_soft_reason": suggest_soft_reason,
        }

    def _queue_event_driven_proactive_impulses(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> int:
        queued = 0
        event_sources = (
            ("pending_followup", self._pick_pending_followup_event(user, now)),
            ("open_loop", self._pick_open_loop_followup_event(user, now)),
            ("birthday_celebration", self._pick_birthday_celebration_event(user, now)),
            ("special_day_ritual", self._pick_special_day_greeting_event(user, now=now)),
            ("night_care", self._pick_insomnia_night_event(user, now=now)),
            ("meal_care", self._pick_meal_care_event(user, now=now)),
            ("daily_greeting", self._pick_daily_greeting_event(user, now)),
            ("mobile_location", self._pick_mobile_location_arrival_event(user, now=now)),
            ("anonymous_area", self._pick_mobile_anonymous_area_event(user, now=now)),
            ("birthday_curiosity", self._pick_birthday_curiosity_event(user, now)),
            ("habit", self._habit_proactive_event_for_user(user, now=now)),
            ("state", self._pick_state_need_event(user, now=now)),
            ("story", self._pick_story_plan_event(now, user=user)),
        )
        for source, event in event_sources:
            if not isinstance(event, dict):
                continue
            social_relay_note = self._unverified_social_relay_plan_reason(
                event,
                source=source,
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            )
            if social_relay_note:
                self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": source,
                        "reason": _single_line(event.get("reason"), 40) or "check_in",
                        "action": _single_line(event.get("action"), 40) or "message",
                        "scheduled_ts": _safe_float(event.get("_scheduled_ts"), now),
                        "topic": _single_line(event.get("topic"), 80),
                        "motive": _single_line(event.get("motive"), 180),
                        "score": 0,
                    },
                    status="blocked",
                    note=social_relay_note,
                    user=user,
                )
                continue
            reason = _single_line(event.get("reason"), 40) or "check_in"
            motive = _single_line(event.get("motive"), 140)
            action = _single_line(event.get("action"), 40)
            if not action:
                if not motive:
                    motive = self._choose_proactive_motive(reason, user, planned_event=event)
                action = self._choose_action_for_reason(reason, user, motive=motive)
            elif not motive:
                motive = self._choose_proactive_motive(reason, user, action=action, planned_event=event)
            action = self._maybe_upgrade_planned_message_action(
                action,
                reason=reason,
                user=user,
                motive=motive,
                planned_event=event,
            )
            topic = _single_line(event.get("topic"), 60) or self._choose_proactive_topic(reason, user)
            if self._action_has_photo_text(action) and self._private_user_role(user) != "friend":
                photo_patch = self._photo_text_plan_field_patch(
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    planned_event=event,
                )
                topic = _single_line(photo_patch.get("topic"), 60) or topic
                motive = _single_line(photo_patch.get("motive"), 140) or motive
            if self._private_user_role(user) == "friend":
                friend_safe = self._sanitize_friend_proactive_plan_fields(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = friend_safe["reason"]
                action = friend_safe["action"]
                topic = friend_safe["topic"]
                motive = friend_safe["motive"]
            candidate = dict(event)
            candidate["origin_event_id"] = self._proactive_origin_event_id(event, source=source)
            candidate["reason"] = reason
            candidate["action"] = action
            candidate["topic"] = topic
            candidate["motive"] = motive
            impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
            if not isinstance(impulse, dict):
                for key in ("lifecycle_status", "lifecycle_note", "lifecycle_updated_at", "expired_at"):
                    if key in candidate:
                        event[key] = candidate.get(key)
                continue
            rest_until = self._proactive_rest_block_until(
                user,
                now=now,
                reason=reason,
                source=source,
            )
            if rest_until > now and _safe_float(impulse.get("window_start_at"), 0) < rest_until:
                shift = rest_until - _safe_float(impulse.get("window_start_at"), 0) + random.uniform(20 * 60, 90 * 60)
                impulse["window_start_at"] = _safe_float(impulse.get("window_start_at"), 0) + shift
                impulse["preferred_ts"] = _safe_float(impulse.get("preferred_ts"), 0) + shift
                impulse["best_until_at"] = _safe_float(impulse.get("best_until_at"), 0) + shift
                impulse["expire_at"] = _safe_float(impulse.get("expire_at"), 0) + shift
            busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
            busy_until = 0.0
            if callable(busy_gate):
                try:
                    busy_until = _safe_float(
                        busy_gate(user, now=now, reason=reason, source=source),
                        0.0,
                    )
                except Exception:
                    busy_until = 0.0
            if busy_until > now and _safe_float(impulse.get("window_start_at"), 0) < busy_until:
                shift = busy_until - _safe_float(impulse.get("window_start_at"), 0)
                impulse["window_start_at"] = _safe_float(impulse.get("window_start_at"), 0) + shift
                impulse["preferred_ts"] = _safe_float(impulse.get("preferred_ts"), 0) + shift
                impulse["best_until_at"] = _safe_float(impulse.get("best_until_at"), 0) + shift
                impulse["expire_at"] = _safe_float(impulse.get("expire_at"), 0) + shift
            if self._queue_proactive_impulse(user, impulse):
                queued += 1
                if source == "anonymous_area":
                    pending = user.get("mobile_anonymous_area_pending")
                    if isinstance(pending, dict):
                        pending["candidate_at"] = now
        return queued

    def _queue_random_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float,
        delay_hours: tuple[float, float],
    ) -> dict[str, Any] | None:
        context = self._random_proactive_impulse_context(user, now=now)
        if not bool(context.get("allowed")):
            return None
        reason, scheduled = self._draw_random_reason_slot(user, now=now, delay_hours=delay_hours)
        if bool(context.get("suggest_soft_reason")) and reason in {"check_in", "state_share"}:
            reason = "quiet_care"
        motive = self._choose_proactive_motive(reason, user)
        action = self._choose_action_for_reason(reason, user, motive=motive)
        action = self._maybe_upgrade_planned_message_action(
            action,
            reason=reason,
            user=user,
            motive=motive,
            planned_event=None,
        )
        topic = self._choose_proactive_topic(reason, user)
        emotion_adjustment = self._apply_emotion_to_planned_proactive(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            scheduled=scheduled,
            now=now,
        )
        reason = str(emotion_adjustment.get("reason") or reason)
        action = str(emotion_adjustment.get("action") or action)
        motive = _single_line(emotion_adjustment.get("motive"), 140) or motive
        topic = _single_line(emotion_adjustment.get("topic"), 60) or topic
        scheduled = _safe_float(emotion_adjustment.get("scheduled"), scheduled)
        if self._action_has_photo_text(action) and self._private_user_role(user) != "friend":
            photo_patch = self._photo_text_plan_field_patch(
                reason=reason,
                topic=topic,
                motive=motive,
            )
            topic = _single_line(photo_patch.get("topic"), 60) or topic
            motive = _single_line(photo_patch.get("motive"), 140) or motive
        if self._private_user_role(user) == "friend":
            friend_safe = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
            )
            reason = friend_safe["reason"]
            action = friend_safe["action"]
            topic = friend_safe["topic"]
            motive = friend_safe["motive"]
        vague_seek_user = (
            str(action or "message") == "message"
            and self._is_vague_seek_user_motive(reason, action, motive, topic)
        )
        if vague_seek_user:
            scheduled = max(scheduled, now + random.uniform(1.5 * 3600, 3.5 * 3600))
            scheduled = self._move_timestamp_into_reason_window(scheduled, reason, user)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(reason, source="random")
        impulse = self._build_proactive_impulse(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            source="random",
            window_start_at=scheduled,
            preferred_ts=scheduled,
            best_until_at=scheduled + active_span,
            expire_at=scheduled + active_span + grace_span,
        )
        if vague_seek_user:
            impulse["salience"] = min(_safe_float(impulse.get("salience"), 0.4), 0.34)
            impulse["urgency"] = min(_safe_float(impulse.get("urgency"), 0.3), 0.22)
        return self._queue_proactive_impulse(user, impulse)

    def _draw_random_reason_slot(
        self,
        user: dict[str, Any],
        *,
        now: float,
        delay_hours: tuple[float, float],
    ) -> tuple[str, float]:
        """Pick a reason whose time window is open near the sampled slot.

        Reasons are weighted blind to the clock; an evening draw of a daytime
        reason (activity_share 10:00-18:30) would otherwise be pushed to the
        next morning and silence the whole night. Redraw a few times and keep
        the earliest slot.
        """
        horizon = now + max(1.0, _safe_float(delay_hours[1], 1.0, 0.05)) * 3600
        best: tuple[str, float] | None = None
        for _ in range(6):
            reason = self._choose_planned_reason()
            scheduled = self._sample_proactive_timestamp(user, now=now, delay_hours=delay_hours, reason=reason)
            scheduled = self._move_timestamp_into_reason_window(scheduled, reason, user)
            if best is None or scheduled < best[1]:
                best = (reason, scheduled)
            if scheduled <= horizon:
                break
        return best

    def _proactive_window_timezone(self) -> str:
        return (
            _single_line(
                getattr(self, "environment_perception_timezone", ""),
                64,
            )
            or "Asia/Shanghai"
        )

    def _invalidate_timezone_derived_state(
        self,
        previous_timezone: str = "",
        current_timezone: str = "",
        *,
        schedule_save: bool = True,
    ) -> dict[str, Any]:
        """Invalidate derived wall-clock state without touching explicit timers."""

        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {"changed": False, "sections": []}
        current = _single_line(
            current_timezone or self._proactive_window_timezone(),
            64,
        ) or "Asia/Shanghai"
        runtime = data.get("proactive_runtime")
        if not isinstance(runtime, dict):
            runtime = {}
            data["proactive_runtime"] = runtime
        stored = _single_line(runtime.get("window_timezone"), 64)
        previous = _single_line(previous_timezone, 64) or stored
        if not previous:
            runtime["window_timezone"] = current
            sections = {"proactive_runtime"}
            if schedule_save:
                saver = getattr(self, "_schedule_data_save", None)
                if callable(saver):
                    saver(sections=sections, delay=0.1)
            return {
                "changed": True,
                "initialized": True,
                "sections": sorted(sections),
                "cleared_plans": 0,
                "blocked_candidates": 0,
            }
        if previous == current:
            runtime["window_timezone"] = current
            return {"changed": False, "sections": []}

        now = _now_ts()
        exempt_sources = {"timer", "troubleshooting", "simulation"}
        blocked_candidates = 0
        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list):
            for candidate in pool:
                if not isinstance(candidate, dict):
                    continue
                status = _single_line(candidate.get("status"), 24).lower()
                lifecycle = _single_line(
                    candidate.get("lifecycle_status"),
                    24,
                ).lower()
                source = self._normalize_legacy_proactive_text(
                    candidate.get("source"),
                    limit=40,
                )
                if (
                    source in exempt_sources
                    or status in {"sent", "blocked", "skipped", "expired", "cancelled", "dropped"}
                    or lifecycle in {"skipped", "expired"}
                ):
                    continue
                candidate["status"] = "blocked"
                candidate["lifecycle_status"] = "skipped"
                candidate["note"] = "运行时区已变化，旧时间窗口已作废"
                candidate["lifecycle_note"] = "运行时区已变化，旧时间窗口已作废"
                candidate["updated_ts"] = now
                candidate["lifecycle_updated_at"] = now
                blocked_candidates += 1

        cleared_plans = 0
        users = data.get("users")
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                impulses = user.get("proactive_impulses")
                if isinstance(impulses, list):
                    for impulse in impulses:
                        if not isinstance(impulse, dict):
                            continue
                        source = self._normalize_legacy_proactive_text(
                            impulse.get("source"),
                            limit=40,
                        )
                        state = _single_line(impulse.get("state"), 24).lower()
                        if source in exempt_sources or state not in {"", "queued", "deferred"}:
                            continue
                        impulse["state"] = "blocked"
                        impulse["last_status"] = "blocked"
                        impulse["last_note"] = "运行时区已变化，旧时间窗口已作废"
                        impulse["updated_ts"] = now
                planned_source = self._normalize_legacy_proactive_text(
                    user.get("planned_proactive_source"),
                    limit=40,
                )
                has_plan = bool(
                    _safe_float(user.get("next_proactive_at"), 0)
                    or planned_source
                    or _single_line(user.get("planned_candidate_id"), 40)
                )
                if has_plan and planned_source not in exempt_sources:
                    self._clear_pending_proactive_plan(user)
                    user.pop("planned_weather_alert_context", None)
                    user.pop("planned_environment_change_context", None)
                    cleared_plans += 1

        terminal_history: dict[str, Any] = {}
        alert_state = data.get("weather_alert_awareness")
        if isinstance(alert_state, dict) and isinstance(
            alert_state.get("terminal_event_identities"),
            dict,
        ):
            terminal_history = dict(alert_state["terminal_event_identities"])
        data["weather_alert_awareness"] = {
            "initialized": False,
            "baseline_ids": [],
            "pending_events": [],
            "terminal_event_identities": terminal_history,
            "next_check_at": 0,
            "config_key": "",
            "window_timezone": current,
        }
        data["environment_change_awareness"] = {
            "initialized": False,
            "next_check_at": 0,
            "window_timezone": current,
        }
        data["daily_weather"] = {}
        data["weather_alerts"] = {}
        runtime["window_timezone"] = current
        runtime["timezone_changed_at"] = now
        runtime["previous_window_timezone"] = previous
        sections = {
            "users",
            "proactive_candidate_pool",
            "proactive_runtime",
            "daily_weather",
            "weather_alerts",
            "weather_alert_awareness",
            "environment_change_awareness",
        }
        if schedule_save:
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections=sections, delay=0.1)
        logger.info(
            "运行时区变化，已作废旧派生窗口: from=%s to=%s plans=%s candidates=%s",
            previous,
            current,
            cleared_plans,
            blocked_candidates,
        )
        return {
            "changed": True,
            "initialized": False,
            "sections": sorted(sections),
            "cleared_plans": cleared_plans,
            "blocked_candidates": blocked_candidates,
        }

    def _schedule_next_proactive(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        delay_hours: tuple[float, float] | None = None,
    ):
        user_id = str(user.get("user_id") or user.get("id") or "")
        if not self._user_enabled_for_proactive(user_id, user):
            self._clear_pending_proactive_plan(user)
            return
        if self._proactive_generation_disabled(user):
            self._suspend_user_proactive_generation(user)
            return
        now = now or _now_ts()
        timer_event = self._get_active_llm_timer(user)
        if not (isinstance(timer_event, dict) and self._llm_timer_can_use_internal_scheduler(timer_event)):
            pause_reason = self._proactive_unanswered_pause_reason(user)
            if pause_reason:
                self._block_friend_unanswered_pending_proactive(user, note=pause_reason, now=now)
                self._clear_pending_proactive_plan(user)
                logger.info(
                    "连续未回应达到配置阈值,暂停普通主动: user=%s unanswered=%s threshold=%s",
                    _single_line(user_id, 40) or "unknown",
                    _unanswered_proactive_count(user),
                    self._proactive_unanswered_pause_after(),
                )
                return
            silence_reason_getter = getattr(self, "_friend_unanswered_silence_reason", None)
            silence_reason = silence_reason_getter(user, now=now) if callable(silence_reason_getter) else ""
            if silence_reason:
                self._block_friend_unanswered_pending_proactive(user, note=silence_reason, now=now)
                self._clear_pending_proactive_plan(user)
                logger.info(
                    "次要用户连续未回应,停止安排普通主动: user=%s ignored=%s reason=%s",
                    _single_line(user_id, 40) or "unknown",
                    _unanswered_proactive_count(user),
                    _single_line(silence_reason, 120),
                )
                return
        if delay_hours is None:
            delay_hours = self._fallback_proactive_delay_hours(user, now=now)
        delay_factor = self._effective_proactive_float("delay_factor", 1.0, minimum=0.2, maximum=1.0)
        if delay_hours is not None and delay_factor < 1.0:
            delay_hours = (
                max(0.05, delay_hours[0] * delay_factor),
                max(0.08, delay_hours[1] * delay_factor),
            )
        intensity_factor = self._daily_intensity_factor(user)
        if delay_hours is not None and intensity_factor > 0:
            widen = max(0.85, min(1.8, 1.25 - intensity_factor * 0.45))
            delay_hours = (delay_hours[0] * widen, delay_hours[1] * widen)
        if delay_hours is not None:
            cycle_multiplier = self._cycle_proactive_frequency_profile()["private_interval_multiplier"]
            if cycle_multiplier > 1.0:
                delay_hours = (
                    delay_hours[0] * cycle_multiplier,
                    delay_hours[1] * cycle_multiplier,
                )
        planned_event = self._pick_best_planned_event(user, now)
        default_reason = (
            str(planned_event.get("reason") or "")
            if isinstance(planned_event, dict)
            else self._choose_planned_reason()
        ) or "check_in"
        if isinstance(timer_event, dict) and self._llm_timer_can_use_internal_scheduler(timer_event):
            timer_scheduled = _safe_float(timer_event.get("scheduled_ts"), 0)
            if timer_scheduled > now:
                user["next_proactive_at"] = timer_scheduled
                user["planned_proactive_reason"] = _single_line(timer_event.get("reason"), 40) or default_reason
                user["planned_proactive_action"] = _single_line(timer_event.get("action"), 24) or "message"
                user["planned_proactive_source"] = "timer"
                user["planned_proactive_motive"] = self._normalize_internal_motive_text(
                    _single_line(timer_event.get("motive"), 140)
                )
                user["planned_proactive_topic"] = _single_line(timer_event.get("topic"), 60) or (
                    _single_line(planned_event.get("topic"), 60)
                    if isinstance(planned_event, dict)
                    else self._choose_proactive_topic(default_reason, user)
                )
                user["planned_proactive_impulse_id"] = ""
                user["planned_proactive_window_start_at"] = timer_scheduled
                user["planned_proactive_window_timezone"] = self._proactive_window_timezone()
                active_span, grace_span = self._proactive_impulse_default_window_seconds(
                    user["planned_proactive_reason"],
                    source="timer",
                )
                user["planned_proactive_best_until_at"] = timer_scheduled + active_span
                user["planned_proactive_expire_at"] = timer_scheduled + active_span + grace_span
                semantics = self._planned_proactive_semantics(user)
                user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
                self._set_planned_proactive_trigger(
                    user,
                    message_id=_single_line(timer_event.get("trigger_message_id"), 120),
                    umo=_single_line(timer_event.get("trigger_umo"), 160),
                    created_at=_safe_float(timer_event.get("trigger_ts"), 0),
                )
                user["planned_event_chain"] = (
                    []
                    if self._private_user_role(user) == "friend"
                    else list(timer_event.get("chain") or [])
                    if isinstance(timer_event.get("chain"), list)
                    else []
                )
                user["planned_opener_mode"] = ""
                user["planned_followup_kind"] = ""
                user["planned_proactive_quota_exempt"] = False
                self._store_planned_proactive_route_fields(user, timer_event)
                item = self._record_proactive_candidate(
                    str(user.get("user_id") or user.get("id") or ""),
                    {
                        "source": "timer",
                        "reason": user["planned_proactive_reason"],
                        "action": user["planned_proactive_action"],
                        "scheduled_ts": timer_scheduled,
                        "topic": user["planned_proactive_topic"],
                        "motive": user["planned_proactive_motive"],
                        "score": 100,
                    },
                    status="accepted",
                    note="用户预约/定时主动",
                    user=user,
                )
                user["planned_candidate_id"] = item.get("id", "")
                return
        self._cleanup_proactive_impulses(user, now=now)
        self._queue_event_driven_proactive_impulses(user, now=now)
        active_impulses = [
            item
            for item in self._cleanup_proactive_impulses(user, now=now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        if self._random_impulse_slot_open(active_impulses, now=now, delay_hours=delay_hours):
            self._queue_random_proactive_impulse(user, now=now, delay_hours=delay_hours)
        if not self._materialize_best_proactive_impulse(user, now=now):
            self._clear_pending_proactive_plan(user)
