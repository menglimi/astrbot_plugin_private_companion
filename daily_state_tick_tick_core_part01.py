# -*- coding: utf-8 -*-
"""DailyStateTickTickCorePart01Mixin。

由 tmp/refactor/dstc_split.py 从 daily_state_tick_tick_core.py 的 _tick_user 段级拆分而来（3 段）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

from typing import Any
from .helpers import (
    _normalize_photo_subject_owner,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    normalize_legacy_tag_text,
)
from .daily_state_tick_shared import (
    _now_ts,
    logger,
)


class DailyStateTickTickCorePart01Mixin:
    """_tick_user 的 precheck / route_lock / prepare 段。"""

    async def _tick_user_precheck(self, user_id, user):
        """_tick_user 段：启用判定 / 免打扰改期 / 模型人格判定。"""
        if isinstance(user, dict):
            user["user_id"] = str(user.get("user_id") or user_id)
        if not isinstance(user, dict) or not self._user_enabled_for_proactive(str(user_id), user):
            if isinstance(user, dict) and _safe_float(user.get("next_proactive_at"), 0) > 0:
                async with self._data_lock:
                    current_for_clear = self._get_user(str(user_id))
                    if self._is_troubleshooting_proactive_plan(user):
                        self._append_troubleshooting_proactive_step(current_for_clear, "到点执行", "error", "目标私聊对象未启用")
                        self._record_troubleshooting_proactive_result(
                            str(user_id),
                            current_for_clear,
                            ok=False,
                            detail="临时主动任务到点，但目标私聊对象未启用",
                            error="目标私聊对象未启用",
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_clear)
                    else:
                        self._clear_pending_proactive_plan(current_for_clear)
                    save_sections = {"users"}
                    if self._is_troubleshooting_proactive_plan(user):
                        save_sections.add("troubleshooting_test_results")
                    self._save_data_sync(sections=save_sections)
            return None
        now = _now_ts()
        due_timer_id = self._due_internal_llm_timer_id(user, now=now)
        is_troubleshooting_for_send = self._is_troubleshooting_proactive_plan(user)
        should_send, reason = self._should_send(user)
        if not should_send:
            async with self._data_lock:
                if self._sync_live_user_proactive_schedule(user_id, user):
                    self._save_data_sync(sections={"users"})
        if not should_send:
            if not is_troubleshooting_for_send and _safe_float(user.get("next_proactive_at"), 0) <= now:
                guard_reason = _single_line(reason, 120)
                if "免打扰" in guard_reason and normalize_legacy_tag_text(user.get("planned_proactive_source")) != "timer":
                    async with self._data_lock:
                        current_for_quiet = self._get_user(user_id)
                        handled, quiet_note = self._defer_planned_proactive_to_quiet_end(
                            current_for_quiet,
                            now=now,
                        )
                        if handled:
                            self._sync_live_user_proactive_schedule(user_id, current_for_quiet)
                            self._save_data_sync(sections={"users"})
                            logger.info(
                                "免打扰主动任务已一次性改期: user=%s next=%s note=%s",
                                user_id,
                                int(max(0, _safe_float(current_for_quiet.get("next_proactive_at"), now) - now)),
                                _single_line(quiet_note, 120),
                            )
                    if handled:
                        self._debug_tick_skip(user_id, quiet_note)
                        return None
                if any(token in guard_reason for token in ("情绪", "关系", "收敛", "免打扰", "安静", "太频繁", "刚聊过")):
                    async with self._data_lock:
                        current_for_guard = self._get_user(user_id)
                        if _safe_float(current_for_guard.get("next_proactive_at"), 0) <= now:
                            delay_minutes = (30.0, 90.0)
                            self._defer_or_replace_planned_impulse(
                                current_for_guard,
                                now=now,
                                note="主动发送检查未通过，候选按当前节奏延后",
                                delay_minutes=delay_minutes,
                                block_current=False,
                            )
                            user["next_proactive_at"] = current_for_guard["next_proactive_at"]
                            user["planned_proactive_window_start_at"] = current_for_guard["planned_proactive_window_start_at"]
                            user["planned_proactive_best_until_at"] = current_for_guard["planned_proactive_best_until_at"]
                            user["planned_proactive_expire_at"] = current_for_guard["planned_proactive_expire_at"]
                            user["planned_proactive_origin_at"] = current_for_guard.get("planned_proactive_origin_at", 0)
                            user["planned_proactive_origin_key"] = current_for_guard.get("planned_proactive_origin_key", "")
                            user["planned_proactive_freshness"] = current_for_guard.get("planned_proactive_freshness", "")
                            user["planned_proactive_delivery_state"] = current_for_guard.get("planned_proactive_delivery_state", "")
                            self._save_data_sync(sections={"users"})
                            logger.info(
                                "主动发送检查未通过且无未来调度,已兜底延后: user=%s reason=%s delay=%ss",
                                user_id,
                                guard_reason,
                                int(max(0, _safe_float(current_for_guard.get("next_proactive_at"), now) - now)),
                            )
            if is_troubleshooting_for_send and now >= _safe_float(user.get("next_proactive_at"), 0):
                async with self._data_lock:
                    current_for_failed_check = self._get_user(user_id)
                    self._append_troubleshooting_proactive_step(current_for_failed_check, "到点执行", "error", reason)
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_failed_check,
                        ok=False,
                        detail="临时主动任务到点，但主动发送检查未通过",
                        error=reason,
                    )
                    self._restore_troubleshooting_proactive_plan(current_for_failed_check)
                    self._save_data_sync(sections={"users", "troubleshooting_test_results"})
            self._debug_tick_skip(user_id, reason)
            return None

        expected_model_signature = self._planned_proactive_model_judge_signature(user)
        if not is_troubleshooting_for_send and not due_timer_id:
            model_judgement: dict[str, Any] = {}
            try:
                model_judgement = await self._review_planned_proactive_with_model(user, now=now)
            except Exception as e:
                logger.warning(
                    "主动模型人格判定异常,降级本地判定: user=%s error=%s",
                    user_id,
                    _single_line(e, 160),
                )
                model_judgement = {"decision": "send", "score": 0, "reason": "模型判定异常,降级本地"}
            model_decision = str(model_judgement.get("decision") or "send")
            if model_decision in {"defer", "drop", "rewrite"}:
                async with self._data_lock:
                    current_for_model = self._get_user(user_id)
                    current_signature = self._planned_proactive_model_judge_signature(current_for_model)
                    judged_signature = _single_line(model_judgement.get("signature"), 80) or current_signature
                    if current_signature != judged_signature:
                        self._debug_tick_skip(user_id, "模型判定期间计划已变化,本轮重新检查", prefix="跳过")
                        return None
                    if model_decision == "rewrite":
                        changed = self._apply_proactive_model_rewrite(current_for_model, model_judgement)
                        model_judgement["signature"] = self._planned_proactive_model_judge_signature(current_for_model)
                        expected_model_signature = _single_line(model_judgement.get("signature"), 80)
                        self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                        if not changed:
                            note = "模型人格判定要求改写,但未给出有效替换字段"
                            self._defer_or_replace_planned_impulse(
                                current_for_model,
                                now=_now_ts(),
                                note=note,
                                delay_minutes=(60, 150),
                                block_current=False,
                            )
                            self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                            self._debug_tick_skip(user_id, note, prefix="延后")
                            return None
                        if changed:
                            self._mark_planned_candidate_status(
                                current_for_model,
                                "accepted",
                                "模型人格判定改写计划: " + _single_line(model_judgement.get("reason"), 120),
                            )
                            logger.info(
                                "模型人格判定已改写主动计划: user=%s reason=%s",
                                user_id,
                                _single_line(model_judgement.get("reason"), 120),
                            )
                        user = dict(current_for_model)
                        self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                    elif model_decision == "defer":
                        note = "模型人格判定延后: " + _single_line(model_judgement.get("reason"), 120)
                        delay = _safe_int(model_judgement.get("delay_minutes"), 90, 20, 360)
                        self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                        replaced = self._defer_or_replace_planned_impulse(
                            current_for_model,
                            now=_now_ts(),
                            note=note,
                            delay_minutes=(delay, delay + 45),
                            block_current=False,
                        )
                        if not replaced and _safe_float(current_for_model.get("next_proactive_at"), 0) <= 0:
                            self._schedule_next_proactive(current_for_model, now=_now_ts(), delay_hours=(1.5, 4.0))
                        self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                        self._debug_tick_skip(user_id, note, prefix="延后")
                        return None
                    elif model_decision == "drop":
                        note = "模型人格判定丢弃: " + _single_line(model_judgement.get("reason"), 120)
                        self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                        self._mark_planned_candidate_status(current_for_model, "blocked", note)
                        self._clear_pending_proactive_plan(current_for_model)
                        self._schedule_next_proactive(current_for_model, now=_now_ts(), delay_hours=(2.0, 6.0))
                        self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                        self._debug_tick_skip(user_id, note, prefix="取消")
                        return None
            else:
                async with self._data_lock:
                    current_for_model_cache = self._get_user(user_id)
                    current_signature = self._planned_proactive_model_judge_signature(current_for_model_cache)
                    judged_signature = _single_line(model_judgement.get("signature"), 80) or current_signature
                    if current_signature == judged_signature:
                        self._cache_proactive_model_judgement(current_for_model_cache, model_judgement, now=_now_ts())
                        self._save_data_sync(sections={"users"})
        return due_timer_id, expected_model_signature, is_troubleshooting_for_send, user


    async def _tick_user_route_lock(
        self,
        user_id,
        user,
        due_timer_id,
        expected_model_signature,
        is_troubleshooting_for_send,
    ):
        """_tick_user 段：持锁做路线结算与审计开单。"""
        async with self._data_lock:
            current_for_mark = self._get_user(user_id)
            if (
                not is_troubleshooting_for_send
                and not due_timer_id
                and expected_model_signature
                and self._planned_proactive_model_judge_signature(current_for_mark) != expected_model_signature
            ):
                self._debug_tick_skip(user_id, "模型判定后计划已变化,本轮重新检查", prefix="跳过")
                return None
            if not self._user_enabled_for_proactive(str(user_id), current_for_mark):
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current_for_mark, "到点执行", "error", "目标私聊对象已禁用")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_mark,
                        ok=False,
                        detail="临时主动任务到点，但目标私聊对象已禁用",
                        error="目标私聊对象已禁用",
                    )
                    self._restore_troubleshooting_proactive_plan(current_for_mark)
                else:
                    self._clear_pending_proactive_plan(current_for_mark)
                save_sections = {"users"}
                if is_troubleshooting_for_send:
                    save_sections.add("troubleshooting_test_results")
                self._save_data_sync(sections=save_sections)
                self._debug_tick_skip(user_id, "私聊对象未启用")
                return None
            self._recover_stale_proactive_sending(current_for_mark)
            if current_for_mark.get("proactive_sending"):
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current_for_mark, "到点执行", "error", "已有主动发送正在进行")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_mark,
                        ok=False,
                        detail="临时主动任务到点，但已有主动发送正在进行",
                        error="已有主动发送正在进行",
                    )
                    self._restore_troubleshooting_proactive_plan(current_for_mark)
                    save_sections = {"users"}
                    if is_troubleshooting_for_send:
                        save_sections.add("troubleshooting_test_results")
                    self._save_data_sync(sections=save_sections)
                self._debug_tick_skip(user_id, "主动发送仍在进行中")
                return None
            current_reason = normalize_legacy_tag_text(current_for_mark.get("planned_proactive_reason"))
            planned_meal_context = (
                current_for_mark.get("planned_meal_care_context")
                if isinstance(current_for_mark.get("planned_meal_care_context"), dict)
                else {}
            )
            meal_followup_context = (
                current_for_mark.get("meal_check_context")
                if isinstance(current_for_mark.get("meal_check_context"), dict)
                else {}
            )
            if (
                not is_troubleshooting_for_send
                and not due_timer_id
                and current_reason in {"meal_care", "meal_care_followup"}
                and (
                    (
                        current_reason == "meal_care"
                        and (
                            self._meal_care_interval_remaining(current_for_mark, now=_now_ts()) > 0
                            or self._food_prompt_cooldown_remaining(current_for_mark, now=_now_ts()) > 0
                        )
                    )
                    or (
                        current_reason == "meal_care_followup"
                        and self._meal_care_followup_blocked_by_newer_food_prompt(
                            current_for_mark,
                            meal_followup_context,
                            now=_now_ts(),
                        )
                    )
                )
            ):
                self._mark_planned_candidate_status(current_for_mark, "blocked", "近期已经聊过饮食，本次饭点关心或补问进入共享冷却")
                self._clear_pending_proactive_plan(current_for_mark)
                current_for_mark["planned_meal_care_context"] = {}
                self._schedule_next_proactive(current_for_mark, now=_now_ts())
                self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                self._debug_tick_skip(user_id, "近期已经聊过饮食，本次饭点关心或补问进入共享冷却", prefix="取消")
                return None
            if (
                not is_troubleshooting_for_send
                and not due_timer_id
                and current_reason == "meal_care"
                and _single_line(planned_meal_context.get("meal_key"), 20) == "breakfast"
                and self._breakfast_waiting_for_morning_reply(current_for_mark)
            ):
                self._mark_planned_candidate_status(current_for_mark, "blocked", "早餐关心等待用户回应早安")
                self._clear_pending_proactive_plan(current_for_mark)
                current_for_mark["planned_meal_care_context"] = {}
                self._schedule_next_proactive(current_for_mark, now=_now_ts())
                self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                self._debug_tick_skip(user_id, "早餐关心等待用户回应早安", prefix="取消")
                return None
            if (
                not is_troubleshooting_for_send
                and not due_timer_id
                and self._is_greeting_reason(current_reason)
            ):
                suppressed_greetings = current_for_mark.get("greetings_suppressed_by_inbound", [])
                if isinstance(suppressed_greetings, list) and current_reason in suppressed_greetings:
                    self._mark_planned_candidate_status(current_for_mark, "blocked", "用户在该问候窗口内已经活跃过")
                    self._clear_pending_proactive_plan(current_for_mark)
                    self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                    self._debug_tick_skip(user_id, "问候窗口已被用户互动占掉", prefix="取消")
                    return None
                recent_user_at = self._latest_user_activity_ts(current_for_mark)
                idle_limit = self._effective_user_greeting_idle_minutes(current_for_mark) * 60
                if recent_user_at > 0 and _now_ts() - recent_user_at < idle_limit:
                    if self._recent_activity_satisfies_greeting(
                        current_for_mark,
                        current_reason,
                        now=_now_ts(),
                    ):
                        self._mark_greeting_satisfied_by_inbound(current_for_mark, current_reason)
                        self._clear_pending_proactive_plan(current_for_mark)
                    else:
                        self._reschedule_greeting_within_window(current_for_mark, current_reason, now=_now_ts())
                    self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                    self._debug_tick_skip(user_id, "用户刚自然来聊,已取消或延后问候主动")
                    return None
            recent_chat_guard_reason = self._route_recent_chat_guard_reason(
                current_for_mark,
                now=_now_ts(),
                planned_reason=current_reason,
                due_timer_active=bool(due_timer_id),
                is_troubleshooting=is_troubleshooting_for_send,
            )
            if recent_chat_guard_reason:
                self._defer_route_for_recent_chat(
                    current_for_mark,
                    now=_now_ts(),
                    note=recent_chat_guard_reason,
                )
                self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                logger.info(
                    "刚聊完,延后本轮普通主动: user=%s reason=%s planned=%s/%s",
                    user_id,
                    _single_line(recent_chat_guard_reason, 120),
                    _single_line(current_reason, 40),
                    _single_line(current_for_mark.get("planned_proactive_action"), 24),
                )
                self._debug_tick_skip(user_id, recent_chat_guard_reason, prefix="延后")
                return None
            current_for_mark["proactive_sending"] = True
            current_for_mark["proactive_sending_started_at"] = _now_ts()
            planned_route_for_send = self._planned_proactive_route(current_for_mark)
            route_key_for_send = _single_line(planned_route_for_send.key, 40) or "relational"
            route_options_for_send = self._planned_proactive_route_delivery_options(current_for_mark)
            route_settlement_for_send = self._planned_proactive_route_settlement(current_for_mark)
            planned_delivery_snapshot = self._ensure_planned_proactive_delivery_state(current_for_mark, now=_now_ts())
            audit_id = self._append_proactive_audit(
                user_id,
                current_for_mark,
                status="running",
                note="排障临时主动消息链路已开始" if is_troubleshooting_for_send else "主动发送链路已开始",
            )
            if is_troubleshooting_for_send:
                self._append_troubleshooting_proactive_step(current_for_mark, "到点执行", "ok", "主动循环已接手临时任务")
                self._record_troubleshooting_proactive_result(
                    user_id,
                    current_for_mark,
                    ok=True,
                    detail="主动循环已接手，正在生成主动消息",
                    pending=True,
                    outcome_type="generating",
                    action=str(current_for_mark.get("planned_proactive_action") or "message"),
                    reason=normalize_legacy_tag_text(current_for_mark.get("planned_proactive_reason")) or "check_in",
                )
            self._save_data_sync(sections={"users", "troubleshooting_test_results"})
        return audit_id, planned_delivery_snapshot, route_key_for_send, route_options_for_send, route_settlement_for_send


    async def _tick_user_prepare(
        self,
        user_id,
        user,
        audit_id,
        due_timer_id,
        is_troubleshooting_for_send,
        planned_delivery_snapshot,
        route_key_for_send,
        route_options_for_send,
        route_settlement_for_send,
    ):
        """_tick_user 段：组装计划字段并渲染主动消息。"""
        planned_action_for_send = str(user.get("planned_proactive_action") or "message")
        planned_motive_for_send = _single_line(user.get("planned_proactive_motive"), 140)
        planned_topic_for_send = _single_line(user.get("planned_proactive_topic"), 80)
        planned_chain_for_send = (
            list(user.get("planned_event_chain") or [])
            if isinstance(user.get("planned_event_chain"), list)
            else []
        )
        creative_share_context_for_send = (
            dict(user.get("creative_share_context") or {})
            if isinstance(user.get("creative_share_context"), dict)
            else {}
        )
        self._ensure_private_user_umo(user_id, user)
        send_umo_for_send = _single_line(user.get("umo"), 180)
        friend_proactive_for_send = self._private_user_role(user) == "friend"
        if friend_proactive_for_send:
            planned_chain_for_send = []
        proactive_quote_message_id = (
            self._planned_proactive_quote_message_id(user, send_umo_for_send)
            if bool(route_options_for_send.get("quote_anchor"))
            else ""
        )
        planned_opener_mode_for_send = str(user.get("planned_opener_mode") or "")
        planned_followup_kind_for_send = str(user.get("planned_followup_kind") or "")
        if not is_troubleshooting_for_send and normalize_legacy_tag_text(user.get("planned_proactive_reason")) == "activity_share":
            duplicate_block_remaining = self._activity_share_duplicate_block_remaining(user)
            if duplicate_block_remaining > 0:
                note = _single_line(user.get("activity_share_duplicate_block_note"), 100) or "同一日常碎片刚刚已分享给其他私聊对象"
                async with self._data_lock:
                    current_for_duplicate_cooldown = self._get_user(user_id)
                    current_for_duplicate_cooldown["proactive_sending"] = False
                    current_for_duplicate_cooldown["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_duplicate_cooldown, "blocked", note)
                    self._clear_pending_proactive_plan(current_for_duplicate_cooldown)
                    self._update_proactive_audit(audit_id, status="cancelled", note=f"活动分享去重冷却中: {note}")
                    self._schedule_next_proactive(current_for_duplicate_cooldown, now=_now_ts(), delay_hours=(2.0, 5.0))
                    self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
                logger.info(
                    "活动分享去重冷却中,跳过本轮主动: user=%s remain=%.0fs note=%s",
                    user_id,
                    duplicate_block_remaining,
                    note,
                )
                self._debug_tick_skip(user_id, "活动分享去重冷却中", prefix="取消")
                return None
        if self._action_has_photo_text(planned_action_for_send) and not self._photo_text_available(user):
            fallback_action = self._fallback_action_for_unavailable(planned_action_for_send, user)
            if fallback_action != planned_action_for_send:
                logger.info(
                    "主动发图能力不可用,发送前已降级: user=%s requested=%s fallback=%s",
                    user_id,
                    planned_action_for_send,
                    fallback_action,
                )
                planned_action_for_send = fallback_action
                async with self._data_lock:
                    current_for_fallback = self._get_user(user_id)
                    current_for_fallback["planned_proactive_action"] = fallback_action
                    self._mark_planned_candidate_status(
                        current_for_fallback,
                        "accepted",
                        "photo_text 后端不可用,已降级为普通主动消息",
                    )
                    self._save_data_sync(sections={"users", "proactive_candidate_pool"})
        load_defer_note = self._photo_text_load_defer_note(planned_action_for_send, force_refresh=True)
        if load_defer_note:
            async with self._data_lock:
                current_for_defer = self._get_user(user_id)
                self._defer_planned_photo_text_for_load(current_for_defer, now=_now_ts(), note=load_defer_note)
                current_for_defer["proactive_sending"] = False
                current_for_defer["proactive_sending_started_at"] = 0
                self._update_proactive_audit(audit_id, status="deferred", note=load_defer_note)
                self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
            self._debug_tick_skip(user_id, load_defer_note, prefix="延后")
            return None
        group_share_block_reason = ""
        if normalize_legacy_tag_text(user.get("planned_proactive_reason")) == "group_share":
            async with self._data_lock:
                current_for_group_check = self._get_user(user_id)
                checker = getattr(self, "_group_share_send_block_reason", None)
                if callable(checker):
                    group_share_block_reason = checker(user_id, current_for_group_check)
                if group_share_block_reason:
                    current_for_group_check["proactive_sending"] = False
                    current_for_group_check["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_group_check, "blocked", group_share_block_reason)
                    self._clear_pending_proactive_plan(current_for_group_check)
                    current_for_group_check["group_share_context"] = {}
                    self._update_proactive_audit(audit_id, status="cancelled", note=group_share_block_reason)
                    self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
            if group_share_block_reason:
                logger.info(
                    "群聊分享主动发送前复核取消: user=%s reason=%s",
                    user_id,
                    group_share_block_reason,
                )
                self._debug_tick_skip(user_id, group_share_block_reason, prefix="取消")
                return None
        task_start_private_activity_at = self._latest_private_user_activity_ts(user)
        task_start_private_inbound_count = _safe_int(user.get("private_inbound_count"), 0)
        render_failure_stage = ""
        pending_send_retry = None if is_troubleshooting_for_send else self._pending_proactive_send_retry(user)
        photo_subject_owner_for_send = ""
        if pending_send_retry:
            reason = _single_line(pending_send_retry.get("reason"), 40) or normalize_legacy_tag_text(user.get("planned_proactive_reason")) or "check_in"
            text = _single_line(pending_send_retry.get("text"), 1200)
            image_path = _path_text(pending_send_retry.get("image_path"), 1000)
            extra_components = []
            action_summary = _single_line(pending_send_retry.get("action_summary"), 500)
            photo_subject_owner_for_send = _normalize_photo_subject_owner(
                pending_send_retry.get("photo_subject_owner")
            )
            effective_action_for_send = _single_line(pending_send_retry.get("action"), 40) or planned_action_for_send or "message"
            logger.info(
                "复用待重发主动消息: user=%s retry=%s text=%s image=%s",
                user_id,
                _safe_int(pending_send_retry.get("retry_count"), 0, 0, 10),
                _single_line(text, 100),
                bool(image_path),
            )
        else:
            try:
                reason, text, image_path, extra_components, action_summary, effective_action_for_send = await self._render_message(user)
                photo_subject_owner_for_send = _normalize_photo_subject_owner(
                    user.pop("_proactive_photo_subject_owner", "")
                )
            except Exception as e:
                logger.warning("主动消息生成失败: user=%s error=%s", user_id, _single_line(e, 160), exc_info=True)
                async with self._data_lock:
                    current_after_render_failure = self._get_user(user_id)
                    current_after_render_failure["proactive_sending"] = False
                    current_after_render_failure["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_after_render_failure, "LLM 渲染", "error", f"生成失败: {_single_line(e, 120)}")
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_after_render_failure,
                            ok=False,
                            detail="主动循环已触发，但 LLM 渲染失败",
                            error=f"生成失败: {_single_line(e, 160)}",
                        )
                        self._restore_troubleshooting_proactive_plan(current_after_render_failure)
                    else:
                        failure_note = f"生成失败: {_single_line(e, 140)}"
                        # Keep the failed candidate observable, but defer its
                        # impulse as a retry instead of immediately
                        # re-materializing the same thought on every tick.
                        deferred = self._defer_or_replace_planned_impulse(
                            current_after_render_failure,
                            now=_now_ts(),
                            note=failure_note,
                            delay_minutes=(60.0, 180.0),
                            block_current=False,
                        )
                        if not deferred and _safe_float(current_after_render_failure.get("next_proactive_at"), 0) <= _now_ts():
                            self._schedule_next_proactive(
                                current_after_render_failure,
                                now=_now_ts(),
                                delay_hours=(1, 3),
                            )
                    self._update_proactive_audit(audit_id, status="failed", note=f"生成失败: {_single_line(e, 140)}")
                    self._save_proactive_tick_state(
                        {"users", "proactive_candidate_pool", "proactive_audit_log", "troubleshooting_test_results"}
                    )
                return None
        render_failure_stage = _single_line(user.pop("_proactive_render_failure_stage", ""), 240)
        if is_troubleshooting_for_send:
            async with self._data_lock:
                current_after_render_ok = self._get_user(user_id)
                self._append_troubleshooting_proactive_step(
                    current_after_render_ok,
                    "LLM 渲染",
                    "ok",
                    f"reason={reason or 'check_in'} / action={effective_action_for_send or planned_action_for_send or 'message'}",
                )
                self._record_troubleshooting_proactive_result(
                    user_id,
                    current_after_render_ok,
                    ok=True,
                    detail="主动消息已生成，准备发送前复核",
                    pending=True,
                    outcome_type="reviewing",
                    text=text,
                    action=effective_action_for_send or planned_action_for_send or "message",
                    reason=reason or "check_in",
                    extra_count=len(extra_components),
                )
                self._save_data_sync(sections={"users", "troubleshooting_test_results"})
        review_candidate_text = text
        if not review_candidate_text and (image_path or extra_components):
            if image_path:
                review_candidate_text = "（无文字，仅随主动消息发送图片）"
            elif extra_components:
                review_candidate_text = f"（无文字，仅随主动消息发送 {len(extra_components)} 个附加组件）"
        return action_summary, creative_share_context_for_send, effective_action_for_send, extra_components, friend_proactive_for_send, image_path, photo_subject_owner_for_send, planned_action_for_send, planned_chain_for_send, planned_followup_kind_for_send, planned_motive_for_send, planned_opener_mode_for_send, planned_topic_for_send, proactive_quote_message_id, reason, render_failure_stage, review_candidate_text, send_umo_for_send, task_start_private_activity_at, task_start_private_inbound_count, text
