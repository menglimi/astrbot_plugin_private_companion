# -*- coding: utf-8 -*-
"""DailyStateTickTickCorePart05Mixin。

由 tmp/refactor/dstc_split.py 从 daily_state_tick_tick_core.py 的 _tick_user 段级拆分而来（1 段）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

import re
from typing import Any
from .helpers import (
    _safe_float,
    _safe_int,
    _single_line,
    normalize_legacy_tag_text,
)
from .persona_config import runtime_persona_setting
from .daily_state_tick_shared import (
    _now_ts,
    _today_key,
)


class DailyStateTickTickCorePart05Mixin:
    """_tick_user 的 poststate 段。"""

    async def _tick_user_poststate(
        self,
        user_id,
        action_summary,
        audit_id,
        delivered_has_photo,
        delivery_complete,
        delivery_note,
        delivery_umo,
        due_timer_id,
        effective_action_for_send,
        extra_components,
        image_path,
        is_troubleshooting_for_send,
        photo_subject_owner_for_send,
        planned_action_for_send,
        planned_chain_for_send,
        planned_followup_kind_for_send,
        planned_motive_for_send,
        planned_opener_mode_for_send,
        planned_topic_for_send,
        reason,
        route_key_for_send,
        route_settlement_for_send,
        send_umo_for_send,
        text,
    ):
        """_tick_user 段：发送后状态结算与下一次排程。"""
        memory_companion_proactive_payload: dict[str, Any] = {}
        async with self._data_lock:
            current = self._get_user(user_id)
            simulation_active = self._simulation_active(current)
            self._reset_daily_counter_if_needed(current)
            current["last_sent"] = _now_ts()
            if send_umo_for_send:
                current["umo"] = send_umo_for_send
            visible_text = self._visible_text_without_tts_reading(text, limit=500)
            current["last_companion_message"] = _single_line(visible_text, 500)
            current["last_proactive_message"] = _single_line(visible_text, 500)
            staged_expression_recorder = getattr(self, "_record_staged_expression_rule_injection", None)
            if callable(staged_expression_recorder):
                staged_expression_recorder(current, visible_text, channel="proactive")
            current["last_proactive_sent_at"] = current["last_sent"]
            current["last_companion_message_at"] = current["last_sent"]
            current["last_proactive_reason"] = reason
            location_reason = _single_line(reason, 40)
            location_event_type = _single_line(current.get("planned_mobile_location_event_type"), 32)
            if (
                location_reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}
                or location_event_type
                or _single_line(current.get("planned_mobile_location_transition_key"), 80)
            ) and not simulation_active:
                current["last_mobile_location_humanization_at"] = current["last_sent"]
                current["last_mobile_location_humanization_kind"] = location_reason or location_event_type
            if not simulation_active:
                self._commit_mobile_location_arrival_after_send(current)
            if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
                current["last_external_link_share_at"] = current["last_sent"]
            if reason == "memory_echo":
                memory_echo_context = (
                    current.get("memory_echo_context")
                    if isinstance(current.get("memory_echo_context"), dict)
                    else {}
                )
                current["last_memory_echo_key"] = _single_line(
                    memory_echo_context.get("echo_key"),
                    40,
                )
                current["last_memory_echo_at"] = current["last_sent"]
                current["memory_echo_context"] = {}
            if reason == "mood_checkin":
                mood_context = (
                    current.get("mood_checkin_context")
                    if isinstance(current.get("mood_checkin_context"), dict)
                    else {}
                )
                current["last_mood_checkin_key"] = _single_line(mood_context.get("check_key"), 40)
                current["last_mood_checkin_at"] = current["last_sent"]
                current["mood_checkin_context"] = {}
            if reason == "absence_miss":
                absence_context = (
                    current.get("absence_miss_context")
                    if isinstance(current.get("absence_miss_context"), dict)
                    else {}
                )
                current["last_absence_miss_key"] = _single_line(absence_context.get("episode_key"), 40)
                current["last_absence_miss_at"] = current["last_sent"]
                current["absence_miss_context"] = {}
            if reason == "game_invite":
                game_context = (
                    current.get("game_invite_context")
                    if isinstance(current.get("game_invite_context"), dict)
                    else {}
                )
                current["last_game_invite_key"] = _single_line(game_context.get("invite_key"), 40)
                current["last_game_invite_at"] = current["last_sent"]
                current["game_invite_context"] = {}
            if reason in {"birthday_eve_hint", "birthday_celebration", "birthday_makeup", "birthday_afterglow"}:
                birthday_event = current.get("birthday_event") if isinstance(current.get("birthday_event"), dict) else {}
                birthday_context = current.get("planned_birthday_event_context") if isinstance(current.get("planned_birthday_event_context"), dict) else {}
                birthday_year = _safe_int(birthday_context.get("observance_year"), self._environment_now().year)
                if reason == "birthday_eve_hint":
                    birthday_event["eve_year"] = birthday_year
                elif reason in {"birthday_celebration", "birthday_makeup"}:
                    birthday_event["celebrated_year"] = birthday_year
                    birthday_event["celebrated_at"] = current["last_sent"]
                    birthday_event["delivery_mode"] = effective_action_for_send or planned_action_for_send or "message"
                else:
                    birthday_event["afterglow_year"] = birthday_year
                    birthday_event["afterglow_at"] = current["last_sent"]
                current["birthday_event"] = birthday_event
            if reason == "special_day_greeting":
                special_context = (
                    current.get("planned_special_day_context")
                    if isinstance(current.get("planned_special_day_context"), dict)
                    else {}
                )
                receipt_key = _single_line(special_context.get("receipt_key"), 80)
                if receipt_key:
                    receipts = current.get("special_day_greeting_receipts")
                    if not isinstance(receipts, dict):
                        receipts = {}
                        current["special_day_greeting_receipts"] = receipts
                    receipts[receipt_key] = current["last_sent"]
                    # Keep migrations and long-lived profiles bounded.
                    if len(receipts) > 24:
                        for old_key, _old_at in sorted(receipts.items(), key=lambda item: _safe_float(item[1], 0))[:-24]:
                            receipts.pop(old_key, None)
                current["planned_special_day_context"] = {}
            if reason == "insomnia_night":
                context = current.get("insomnia_night_context") if isinstance(current.get("insomnia_night_context"), dict) else {}
                current["insomnia_night_sent_key"] = _single_line(
                    context.get("night_key"),
                ) or self._insomnia_night_key(current["last_sent"])
                current["insomnia_night_context"] = {}
            current["last_proactive_action"] = effective_action_for_send or planned_action_for_send or "message"
            current["last_proactive_behavior_summary"] = action_summary
            current["last_proactive_motive"] = planned_motive_for_send
            if not is_troubleshooting_for_send and delivered_has_photo:
                photo_caption = ""
                if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                    photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 260)
                self._remember_recent_photo_share_snapshot(
                    current,
                    caption=photo_caption,
                    topic=planned_topic_for_send,
                    motive=planned_motive_for_send,
                    reason=reason,
                    subject_owner=photo_subject_owner_for_send,
                    sent_at=current["last_sent"],
                )
            self._clear_pending_proactive_send_retry(current)
            food_prompt_hint = " ".join(
                _single_line(value, 120)
                for value in (
                    planned_motive_for_send,
                    current.get("planned_proactive_topic"),
                    normalize_legacy_tag_text(current.get("planned_proactive_reason")),
                )
            )
            if reason in {"meal_care", "meal_care_followup"} or any(token in food_prompt_hint for token in ("吃什么", "吃点", "饭", "饭点", "嘴馋", "饿", "吃的")):
                current["last_food_prompt_at"] = current["last_sent"]
            self._remember_proactive_topic(
                current,
                text=visible_text or text,
                topic=current.get("planned_proactive_topic"),
                motive=planned_motive_for_send,
            )
            if reason == "activity_share":
                self._remember_global_activity_share(
                    user_id,
                    current,
                    text=visible_text or text,
                    action_summary=action_summary,
                )
            if reason == "group_share":
                sidecar_checker = getattr(self, "_group_share_text_has_life_sidecar", None)
                if callable(sidecar_checker) and sidecar_checker(visible_text or text):
                    current["last_group_share_life_sidecar_at"] = current["last_sent"]
            self._mark_planned_candidate_status(current, "sent", "已发送")
            self._update_proactive_audit(
                audit_id,
                status="sent",
                note=(
                    "排障临时主动消息已发送"
                    if is_troubleshooting_for_send and delivery_complete
                    else "排障临时主动消息部分送达"
                    if is_troubleshooting_for_send
                    else "已真实发送"
                    if delivery_complete
                    else f"已部分送达：{delivery_note or '后续组件被取消或发送失败'}"
                ),
                text=visible_text or text,
                image_path=image_path,
                extra_count=len(extra_components),
                action=current["last_proactive_action"],
                reason="troubleshooting_test" if is_troubleshooting_for_send else reason,
                sent_at=current["last_sent"],
                expects_reply=bool(route_settlement_for_send.get("await_reply")),
            )
            if is_troubleshooting_for_send:
                self._record_troubleshooting_proactive_result(
                    user_id,
                    current,
                    ok=True,
                    detail="已完成排障临时主动消息发送与归档调用，原主动计划已恢复",
                    outcome_type="completed",
                    text=visible_text or text,
                    action=current["last_proactive_action"],
                    reason=reason or "check_in",
                    extra_count=len(extra_components),
                )
                self._restore_troubleshooting_proactive_plan(current)
                self._save_data_sync(sections={"users", "troubleshooting_test_results"})
                return None
            self._note_proactive_daypart_sent(current, current["last_sent"])
            opener_mode = planned_opener_mode_for_send
            followup_kind = planned_followup_kind_for_send
            allow_route_followup = bool(route_settlement_for_send.get("allow_automatic_followup"))
            self._settle_proactive_route_state(
                current,
                route_key=route_key_for_send,
                settlement=route_settlement_for_send,
                sent_at=current["last_sent"],
                count_delivery=not simulation_active,
            )
            if bool(route_settlement_for_send.get("await_reply")) and audit_id:
                current["last_proactive_reply_audit_id"] = str(audit_id)
                current["last_proactive_reply_audit_sent_at"] = current["last_sent"]
                current["last_proactive_reply_audit_outcome"] = "pending"
                current["last_proactive_reply_audit_outcome_at"] = 0
            if self._private_user_role(current) == "friend":
                current["pending_followup_event"] = {}
                current["suspended_proactive"] = {}
            elif allow_route_followup and reason == "meal_care":
                planned_meal = current.get("planned_meal_care_context") if isinstance(current.get("planned_meal_care_context"), dict) else {}
                meal_key = _single_line(planned_meal.get("meal_key"), 20) or self._current_food_time_key()
                meal_label = _single_line(planned_meal.get("meal_label"), 12) or self._food_menu_time_label(meal_key) or "这顿饭"
                followup_minutes = _safe_int(
                    runtime_persona_setting(self, "meal_care_followup_minutes", 45),
                    45,
                    15,
                    180,
                )
                meal_context = {
                    "active": True,
                    "date": _today_key(),
                    "meal_key": meal_key,
                    "meal_label": meal_label,
                    "stage": "awaiting_status",
                    "asked_at": current["last_sent"],
                    "followup_due_at": current["last_sent"] + followup_minutes * 60,
                    "expires_at": current["last_sent"] + max(4 * 3600, followup_minutes * 120),
                    "followup_count": 0,
                }
                current["meal_check_context"] = meal_context
                asked_meals = current.setdefault("meal_care_asked", [])
                if not isinstance(asked_meals, list):
                    asked_meals = []
                    current["meal_care_asked"] = asked_meals
                if meal_key not in asked_meals:
                    asked_meals.append(meal_key)
                current["pending_followup_event"] = self._meal_care_followup_event(current, now=current["last_sent"]) or {}
            elif allow_route_followup and reason == "meal_care_followup":
                meal_context = current.get("meal_check_context") if isinstance(current.get("meal_check_context"), dict) else {}
                if meal_context:
                    meal_context["followup_count"] = 1
                    meal_context["followup_sent_at"] = current["last_sent"]
                    meal_context["followup_due_at"] = 0
                    current["meal_check_context"] = meal_context
                current["pending_followup_event"] = {}
            elif allow_route_followup and opener_mode == "name_only":
                current["suspended_proactive"] = self._build_suspended_proactive_payload(
                    opener_text=text,
                    reason=reason,
                    action=current["last_proactive_action"],
                    motive=current["last_proactive_motive"],
                    action_summary=action_summary,
                    chain=planned_chain_for_send,
                )
            elif allow_route_followup and followup_kind == "suspended_opener":
                suspended = current.get("suspended_proactive")
                if isinstance(suspended, dict) and suspended.get("active"):
                    suspended["complaint_sent"] = True
                    second = suspended.get("second_followup")
                    if isinstance(second, dict) and second:
                        after_minutes = _safe_int(second.get("after_minutes"), 45, 0, 240)
                        second_reason = _single_line(second.get("reason"), 40) or "morning_greeting"
                        if second_reason == "morning_greeting":
                            after_minutes = max(after_minutes, 90)
                        current["pending_followup_event"] = {
                            "date": _today_key(),
                            "window": self._window_from_delay_minutes(after_minutes, width_minutes=18),
                            "reason": second_reason,
                            "action": "message",
                            "why": "前一条早晨试探后还差个具体点,如果还想续,就把那一点补上。",
                            "topic": _single_line(second.get("topic"), 80) or "早安余韵",
                            "motive": self._normalize_internal_motive_text(_single_line(second.get("motive"), 100)),
                            "scene": "早晨那句试探之后又过了一阵",
                            "tone": _single_line(second.get("tone"), 30) or "克制一点,把重点补上",
                            "impulse": "早晨那句还差个重点,想补完整",
                            "_scheduled_ts": _now_ts() + after_minutes * 60,
                            "_cancel_on_inbound": True,
                        }
            elif allow_route_followup and followup_kind == "chain_followup":
                next_chain_followup = self._build_followup_event_from_chain(
                    planned_chain_for_send,
                    origin_reason=reason,
                    origin_action=current["last_proactive_action"],
                    now_ts=_now_ts(),
                )
                if isinstance(next_chain_followup, dict):
                    current["pending_followup_event"] = next_chain_followup
            elif allow_route_followup and planned_chain_for_send and not current.get("pending_followup_event"):
                next_chain_followup = self._build_followup_event_from_chain(
                    planned_chain_for_send,
                    origin_reason=reason,
                    origin_action=current["last_proactive_action"],
                    now_ts=_now_ts(),
                )
                if isinstance(next_chain_followup, dict):
                    current["pending_followup_event"] = next_chain_followup
            if simulation_active:
                self._consume_simulation_event(current)
            else:
                current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                self._note_action_sent(
                    current,
                    current["last_proactive_action"],
                    reason=reason,
                    text=text,
                    motive=planned_motive_for_send,
                    action_summary=action_summary,
                    source=_single_line(current.get("planned_proactive_source"), 40),
                )
                existing_followup = current.get("pending_followup_event")
                if self._private_user_role(current) == "friend":
                    current["pending_followup_event"] = {}
                elif isinstance(existing_followup, dict) and existing_followup:
                    current["pending_followup_event"] = existing_followup
                elif followup_kind in {"suspended_opener", "chain_followup"} or opener_mode == "name_only":
                    current["pending_followup_event"] = {}
                elif allow_route_followup:
                    current["pending_followup_event"] = self._maybe_make_unanswered_screen_peek_event(
                        current,
                        reason,
                        current["last_proactive_action"],
                    ) or self._maybe_make_followup_event(
                        current,
                        reason,
                        current["last_proactive_action"],
                    ) or {}
                if self._is_greeting_reason(reason):
                    self._reset_daily_counter_if_needed(current)
                    sent_greetings = current.setdefault("greetings_sent", [])
                    if not isinstance(sent_greetings, list):
                        sent_greetings = []
                        current["greetings_sent"] = sent_greetings
                    if reason not in sent_greetings:
                        sent_greetings.append(reason)
                if reason == "morning_greeting":
                    current["morning_greeting_sent_at"] = _safe_float(current.get("last_sent"), 0) or _now_ts()
                    current["morning_greeting_reply_at"] = 0
                self._mark_textual_greeting_sent(current, visible_text or text, sent_at=current["last_sent"])
                self._clear_llm_timer_event(current, event_id=due_timer_id)
                burst_was_active = bool(current.get("planned_proactive_burst"))
                burst_index_before_send = _safe_int(current.get("proactive_burst_index"), 0, 0)
                max_burst_getter = getattr(self, "_proactive_burst_max_messages", None)
                max_burst_messages = (
                    max_burst_getter()
                    if callable(max_burst_getter)
                    else _safe_int(runtime_persona_setting(self, "proactive_burst_max_messages", 2), 2, 2, 3)
                )
                if burst_was_active:
                    current["planned_proactive_burst"] = False
                    current["proactive_burst_origin_id"] = ""
                    if burst_index_before_send + 1 >= max_burst_messages:
                        current["proactive_burst_index"] = 0
                next_timer = self._get_active_llm_timer(current)
                burst_scheduled = False
                burst_has_followup_slot = burst_was_active and burst_index_before_send + 1 < max_burst_messages
                if not burst_was_active or burst_has_followup_slot:
                    burst_scheduler = getattr(self, "_maybe_schedule_proactive_burst", None)
                    if callable(burst_scheduler):
                        burst_scheduled = bool(
                            burst_scheduler(
                                current,
                                now=_now_ts(),
                                reason=reason,
                                source=_single_line(current.get("last_proactive_source"), 40)
                                or _single_line(current.get("planned_proactive_source"), 40),
                                action=_single_line(current.get("last_proactive_action"), 40),
                                motive=_single_line(current.get("last_proactive_motive"), 160),
                                topic=_single_line(current.get("planned_proactive_topic"), 80),
                            )
                        )
                if not burst_scheduled and (
                    isinstance(next_timer, dict)
                    and self._llm_timer_can_use_internal_scheduler(next_timer)
                    and _safe_float(next_timer.get("scheduled_ts"), 0) > _now_ts()
                ):
                    self._reset_planned_proactive_delivery_state(current)
                    current["next_proactive_at"] = _safe_float(next_timer.get("scheduled_ts"), 0)
                    current["planned_proactive_reason"] = normalize_legacy_tag_text(next_timer.get("reason")) or "check_in"
                    current["planned_proactive_action"] = normalize_legacy_tag_text(next_timer.get("action")) or "message"
                    current["planned_proactive_source"] = "timer"
                    current["planned_proactive_motive"] = _single_line(next_timer.get("motive"), 140)
                    current["planned_proactive_topic"] = _single_line(next_timer.get("topic"), 60)
                    current["planned_proactive_impulse_id"] = ""
                    current["planned_proactive_window_start_at"] = current["next_proactive_at"]
                    active_span, grace_span = self._proactive_impulse_default_window_seconds(
                        current["planned_proactive_reason"],
                        source="timer",
                    )
                    current["planned_proactive_best_until_at"] = current["next_proactive_at"] + active_span
                    current["planned_proactive_expire_at"] = current["next_proactive_at"] + active_span + grace_span
                    semantics = self._planned_proactive_semantics(current)
                    current["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
                    current["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
                    current["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
                    current["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
                    self._set_planned_proactive_trigger(
                        current,
                        message_id=_single_line(next_timer.get("trigger_message_id"), 120),
                        umo=_single_line(next_timer.get("trigger_umo"), 160),
                        created_at=_safe_float(next_timer.get("trigger_ts"), 0),
                    )
                    current["planned_event_chain"] = [] if self._private_user_role(current) == "friend" else (
                        list(next_timer.get("chain") or []) if isinstance(next_timer.get("chain"), list) else []
                    )
                    current["planned_opener_mode"] = ""
                    current["planned_followup_kind"] = ""
                    current["planned_proactive_quota_exempt"] = False
                    self._store_planned_proactive_route_fields(current, {**next_timer, "source": "timer"})
                else:
                    self._clear_pending_proactive_plan(current)
                    schedule_now = _now_ts()
                    next_delay = self._friend_proactive_spread_delay_hours(current, now=schedule_now)
                    self._schedule_next_proactive(current, now=schedule_now, delay_hours=next_delay)
            self._save_data_sync(
                sections={
                    "users",
                    "proactive_candidate_pool",
                    "proactive_audit_log",
                    "proactive_runtime",
                    "troubleshooting_test_results",
                }
            )
            current_snapshot = dict(current)
            if not simulation_active and visible_text:
                memory_companion_proactive_payload = {
                    "user": current_snapshot,
                    "user_id": user_id,
                    "text": visible_text,
                    "umo": delivery_umo,
                    "reason": reason,
                    "action": current.get("last_proactive_action") or effective_action_for_send or planned_action_for_send or "message",
                    "motive": planned_motive_for_send,
                    "action_summary": action_summary,
                    "image_path": image_path,
                    "extra_count": len(extra_components),
                }
        return (memory_companion_proactive_payload,)
