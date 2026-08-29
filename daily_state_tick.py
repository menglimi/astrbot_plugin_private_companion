# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
from typing import Any


from .constants import _REASON_TEXT
from .helpers import (
    _normalize_outbound_punctuation_flow,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    normalize_legacy_tag_text,
)
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


class DailyStateTickMixin:
    def _save_proactive_tick_state(self, sections: set[str]) -> None:
        """Persist tick state while tolerating legacy test/extension adapters."""
        saver = getattr(self, "_save_data_sync", None)
        if not callable(saver):
            return
        try:
            saver(sections=sections)
        except TypeError as exc:
            # Older integrations exposed _save_data_sync() without the
            # incremental sections argument. Keep the active loop usable for
            # those adapters, but do not hide unrelated TypeErrors.
            if "unexpected keyword argument" not in str(exc) or "sections" not in str(exc):
                raise
            saver()

    @staticmethod
    def _proactive_similarity_guard_enabled(
        user: dict[str, Any],
        *,
        is_troubleshooting: bool,
        action: str,
        timeliness: str,
        duplicate_policy: str,
        enabled_policies: frozenset[str] | None = None,
    ) -> bool:
        """Burst follow-ups intentionally use a second angle, not duplicate text."""
        active_policies = (
            {"semantic", "content_fingerprint", "life_event"}
            if enabled_policies is None
            else enabled_policies
        )
        return bool(
            not is_troubleshooting
            and (action or "message") == "message"
            and not bool(user.get("planned_proactive_burst"))
            and timeliness == "routine"
            and duplicate_policy in active_policies
        )

    """Execute one user's proactive tick outside the daily-state capability module."""

    def _route_recent_chat_guard_reason(
        self,
        user: dict[str, Any],
        *,
        now: float,
        planned_reason: str,
        due_timer_active: bool,
        is_troubleshooting: bool,
    ) -> str:
        options = self._planned_proactive_route_delivery_options(user)
        policy = _single_line(options.get("recent_chat_policy"), 40) or "defer"
        if policy in {"bypass", "anchor_check"}:
            return ""
        note = self._recent_chat_proactive_guard_reason(
            user,
            now=now,
            planned_reason=planned_reason,
            planned_source=normalize_legacy_tag_text(user.get("planned_proactive_source")),
            due_timer_active=due_timer_active,
            is_troubleshooting=is_troubleshooting,
        )
        if note and policy == "short_defer":
            return note.replace("普通主动延后", "分享路线短暂避让")
        return note

    def _defer_route_for_recent_chat(self, user: dict[str, Any], *, now: float, note: str) -> None:
        options = self._planned_proactive_route_delivery_options(user)
        if _single_line(options.get("recent_chat_policy"), 40) == "short_defer":
            self._defer_or_replace_planned_impulse(
                user,
                now=now,
                note=note,
                delay_minutes=(5.0, 15.0),
                block_current=False,
            )
            self._mark_planned_candidate_status(user, "deferred", note)
            return
        self._defer_proactive_for_recent_chat(user, now=now, note=note)

    def _settle_proactive_route_state(
        self,
        user: dict[str, Any],
        *,
        route_key: str,
        settlement: dict[str, Any],
        sent_at: float,
        count_delivery: bool = True,
    ) -> None:
        user["last_proactive_kind"] = route_key
        if count_delivery:
            route_counts = user.setdefault("proactive_route_sent_counts", {})
            if not isinstance(route_counts, dict):
                route_counts = {}
                user["proactive_route_sent_counts"] = route_counts
            route_counts[route_key] = _safe_int(route_counts.get(route_key), 0, 0) + 1
            if bool(settlement.get("await_reply")):
                user["ignored_streak"] = _safe_int(user.get("ignored_streak"), 0) + 1
                user["awaiting_reply_since"] = sent_at
        for context_key in settlement.get("clear_context_keys", ()):
            clean_key = _single_line(context_key, 80)
            if clean_key:
                user[clean_key] = {}

    async def _tick_user(self, user_id: str, user: Any) -> None:
        """Process one user while the outer tick keeps sequential ordering."""
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
            return
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
                        return
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
            return

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
                        return
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
                            return
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
                        return
                    elif model_decision == "drop":
                        note = "模型人格判定丢弃: " + _single_line(model_judgement.get("reason"), 120)
                        self._cache_proactive_model_judgement(current_for_model, model_judgement, now=_now_ts())
                        self._mark_planned_candidate_status(current_for_model, "blocked", note)
                        self._clear_pending_proactive_plan(current_for_model)
                        self._schedule_next_proactive(current_for_model, now=_now_ts(), delay_hours=(2.0, 6.0))
                        self._save_data_sync(sections={"users", "proactive_candidate_pool"})
                        self._debug_tick_skip(user_id, note, prefix="取消")
                        return
            else:
                async with self._data_lock:
                    current_for_model_cache = self._get_user(user_id)
                    current_signature = self._planned_proactive_model_judge_signature(current_for_model_cache)
                    judged_signature = _single_line(model_judgement.get("signature"), 80) or current_signature
                    if current_signature == judged_signature:
                        self._cache_proactive_model_judgement(current_for_model_cache, model_judgement, now=_now_ts())
                        self._save_data_sync(sections={"users"})

        async with self._data_lock:
            current_for_mark = self._get_user(user_id)
            if (
                not is_troubleshooting_for_send
                and not due_timer_id
                and expected_model_signature
                and self._planned_proactive_model_judge_signature(current_for_mark) != expected_model_signature
            ):
                self._debug_tick_skip(user_id, "模型判定后计划已变化,本轮重新检查", prefix="跳过")
                return
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
                return
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
                return
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
                return
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
                return
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
                    return
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
                    return
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
                return
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
                return
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
            return
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
                return
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
                return
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
        if review_candidate_text:
            try:
                review_decision = await self._review_proactive_message_send_decision(
                    user,
                    review_candidate_text,
                    reason=reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                    action=effective_action_for_send or planned_action_for_send or "message",
                    motive=planned_motive_for_send,
                    topic=planned_topic_for_send,
                    action_summary=action_summary,
                    image_path=image_path,
                )
            except Exception as exc:
                review_enabled = bool(runtime_persona_setting(self, "enable_proactive_message_review", True))
                if review_enabled:
                    review_failure_signature = self._proactive_topic_signature(
                        " ".join(
                            _single_line(value, 240)
                            for value in (
                                review_candidate_text,
                                reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                                effective_action_for_send or planned_action_for_send or "message",
                                planned_motive_for_send,
                                planned_topic_for_send,
                            )
                            if value
                        )
                    )
                    async with self._data_lock:
                        current_for_review_error = self._get_user(user_id)
                        failure_state = current_for_review_error.get("proactive_review_failure_backoff")
                        if not isinstance(failure_state, dict):
                            failure_state = {}
                        previous_count = (
                            _safe_int(failure_state.get("count"), 0, 0, 10)
                            if str(failure_state.get("signature") or "") == review_failure_signature
                            else 0
                        )
                        failure_count = previous_count + 1
                        current_for_review_error["proactive_review_failure_backoff"] = {
                            "signature": review_failure_signature,
                            "count": failure_count,
                            "last_error": _single_line(exc, 160),
                            "updated_at": _now_ts(),
                        }
                        self._save_data_sync(sections={"users"})
                    if failure_count >= 3:
                        review_strength_getter = getattr(self, "_proactive_review_strength", None)
                        review_strength = (
                            review_strength_getter()
                            if callable(review_strength_getter)
                            else str(runtime_persona_setting(self, "proactive_review_strength", "lenient") or "lenient")
                        )
                        if review_strength == "strict":
                            logger.warning(
                                "主动消息发送前价值复核连续失败,严格模式放弃本条候选避免反复调用: count=%s error=%s",
                                failure_count,
                                _single_line(exc, 120),
                            )
                            review_decision = {"decision": "drop", "reason": "发送前价值复核连续失败，已放弃本条候选"}
                        else:
                            logger.warning(
                                "主动消息发送前价值复核连续失败,按%s强度放行原候选避免主动归零: count=%s error=%s",
                                review_strength or "lenient",
                                failure_count,
                                _single_line(exc, 120),
                            )
                            review_decision = {
                                "decision": "send",
                                "reason": "发送前价值复核连续失败，已按当前强度放行",
                                "review_fallback": True,
                                "review_fallback_reason": _single_line(exc, 180),
                            }
                    else:
                        delay_minutes = min(240, 45 * (2 ** max(0, failure_count - 1)))
                        logger.warning(
                            "主动消息发送前价值复核失败,本轮延后重试: count=%s delay=%s error=%s",
                            failure_count,
                            delay_minutes,
                            _single_line(exc, 120),
                        )
                        review_decision = {
                            "decision": "defer",
                            "delay_minutes": delay_minutes,
                            "reason": f"发送前价值复核失败，稍后重试（第 {failure_count} 次）",
                        }
                else:
                    logger.debug("主动消息发送前本地复核失败,按原文继续: %s", _single_line(exc, 120))
                    review_decision = {"decision": "send"}
            decision = str(review_decision.get("decision") or "send").lower() if isinstance(review_decision, dict) else "send"
            review_fallback_release = bool(
                isinstance(review_decision, dict)
                and review_decision.get("review_fallback")
                and decision in {"send", "rewrite"}
            )
            if review_fallback_release:
                async with self._data_lock:
                    review_runtime = self.data.setdefault("proactive_review_runtime", {})
                    if not isinstance(review_runtime, dict):
                        review_runtime = {}
                        self.data["proactive_review_runtime"] = review_runtime
                    release_count = _safe_int(review_runtime.get("consecutive_fallback_releases"), 0, 0) + 1
                    review_runtime["consecutive_fallback_releases"] = release_count
                    review_runtime["last_fallback_release_at"] = _now_ts()
                    review_runtime["last_fallback_reason"] = _single_line(
                        review_decision.get("review_fallback_reason") or review_decision.get("reason"),
                        180,
                    )
                    self._save_data_sync(sections={"proactive_review_runtime"})
                if release_count == 10 or release_count % 10 == 0:
                    logger.warning(
                        "主动复核模型已连续放行 %s 条原文，请检查 RESPONSE_REVIEW_PROVIDER_ID",
                        release_count,
                    )
            review_model_ok = bool(
                isinstance(review_decision, dict) and review_decision.get("review_model_ok")
            )
            ordinary_release = decision in {"send", "rewrite"} and not bool(
                isinstance(review_decision, dict) and review_decision.get("review_fallback")
            )
            if review_model_ok or ordinary_release:
                async with self._data_lock:
                    current_for_review_ok = self._get_user(user_id)
                    if isinstance(current_for_review_ok.get("proactive_review_failure_backoff"), dict):
                        current_for_review_ok["proactive_review_failure_backoff"] = {}
                    review_runtime = self.data.get("proactive_review_runtime")
                    if isinstance(review_runtime, dict) and _safe_int(review_runtime.get("consecutive_fallback_releases"), 0) > 0:
                        review_runtime["consecutive_fallback_releases"] = 0
                        review_runtime["last_recovered_at"] = _now_ts()
                    self._save_data_sync(sections={"users", "proactive_review_runtime"})
            if decision == "defer":
                delay_minutes = max(
                    5,
                    min(240, _safe_int(review_decision.get("delay_minutes"), 60, 5, 240)),
                )
                note = _single_line(review_decision.get("reason"), 180) or f"发送前复核建议延后 {delay_minutes} 分钟"
                stale_checker = getattr(self, "_stale_proactive_review_defer_release_reason", None)
                stale_note = ""
                if callable(stale_checker):
                    try:
                        stale_note = _single_line(
                            stale_checker(
                                user,
                                note=note,
                                reason=reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                            ),
                            180,
                        )
                    except Exception:
                        stale_note = ""
                stale_candidate = bool(stale_note)
                if stale_candidate:
                    note = stale_note
                async with self._data_lock:
                    current_for_review_defer = self._get_user(user_id)
                    current_for_review_defer["proactive_sending"] = False
                    current_for_review_defer["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send and not stale_candidate:
                        self._append_troubleshooting_proactive_step(
                            current_for_review_defer,
                            "发送前价值复核",
                            "ok",
                            f"候选已延后 {delay_minutes} 分钟：{note}",
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_review_defer)
                    else:
                        replacer = getattr(self, "_defer_or_replace_planned_impulse", None)
                        handled = False
                        replacer_called = False
                        if callable(replacer):
                            try:
                                replacer_called = True
                                handled = bool(
                                    replacer(
                                        current_for_review_defer,
                                        now=_now_ts(),
                                        note=note,
                                        delay_minutes=(float(delay_minutes), float(delay_minutes) + 3.0),
                                        block_current=stale_candidate,
                                    )
                                )
                            except Exception as exc:
                                replacer_called = False
                                logger.debug("复核延后更新候选失败，回退直接排程: %s", _single_line(exc, 120))
                        if stale_candidate and not replacer_called:
                            self._mark_planned_candidate_status(current_for_review_defer, "cancelled", note)
                            self._clear_pending_proactive_plan(current_for_review_defer)
                        if not handled and _safe_float(current_for_review_defer.get("next_proactive_at"), 0) <= _now_ts():
                            self._schedule_next_proactive(
                                current_for_review_defer,
                                now=_now_ts(),
                                delay_hours=(delay_minutes / 60.0, (delay_minutes + 3) / 60.0),
                            )
                        if not stale_candidate:
                            self._mark_planned_candidate_status(current_for_review_defer, "deferred", note)
                        self._clear_pending_proactive_send_retry(current_for_review_defer)
                    self._update_proactive_audit(
                        audit_id,
                        status="cancelled" if stale_candidate else "deferred",
                        note=note,
                        text=text or review_candidate_text,
                    )
                    self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
                logger.info(
                    "主动消息发送前复核%s: user=%s delay=%s reason=%s",
                    "作废过期候选" if stale_candidate else "延后",
                    user_id,
                    delay_minutes,
                    note,
                )
                self._debug_tick_skip(user_id, note, prefix="作废" if stale_candidate else "延后")
                return
            if decision == "rewrite":
                rewritten_text = str(review_decision.get("text") or "").strip()
                if rewritten_text:
                    rewritten_text = _normalize_outbound_punctuation_flow(rewritten_text).strip()
                    original_text_before_rewrite = str(text or review_candidate_text or "").strip()
                    logger.info(
                        "主动消息发送前已润色: user=%s before=%s after=%s",
                        user_id,
                        _single_line(original_text_before_rewrite, 100),
                        _single_line(rewritten_text, 100),
                    )
                    text = rewritten_text
                    self._schedule_reply_interception_forward(
                        "rewrite",
                        source="主动消息价值复核",
                        reason=_single_line(review_decision.get("reason"), 240) or "发送前价值复核轻改写",
                        source_session=send_umo_for_send,
                        before=original_text_before_rewrite,
                        after=text,
                    )
                    async with self._data_lock:
                        self._update_proactive_audit(
                            audit_id,
                            status="running",
                            note="发送前价值复核轻改写",
                            text=text,
                            original_text=original_text_before_rewrite,
                            final_text=text,
                        )
                        if is_troubleshooting_for_send:
                            current_for_review_rewrite = self._get_user(user_id)
                            self._append_troubleshooting_proactive_step(
                                current_for_review_rewrite,
                                "发送前价值复核",
                                "ok",
                                "复核模型建议轻改写："
                                f"由「{_single_line(original_text_before_rewrite, 70)}」"
                                f"改为「{_single_line(text, 70)}」",
                            )
                            self._record_troubleshooting_proactive_result(
                                user_id,
                                current_for_review_rewrite,
                                ok=True,
                                detail="主动消息已通过发送前价值复核，复核模型建议轻改写",
                                pending=True,
                                outcome_type="reviewing",
                                text=text or review_candidate_text,
                                original_text=original_text_before_rewrite,
                                final_text=text,
                                action=effective_action_for_send or planned_action_for_send or "message",
                                reason=reason or "check_in",
                                extra_count=len(extra_components),
                            )
                        self._save_data_sync(
                            sections={"users", "proactive_audit_log", "troubleshooting_test_results"}
                        )
            elif decision == "drop":
                note = _single_line(review_decision.get("reason"), 120) or "proactive final content gate dropped the candidate"
                async with self._data_lock:
                    current_for_review = self._get_user(user_id)
                    current_for_review["proactive_sending"] = False
                    current_for_review["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_for_review, "Final content gate", "error", note)
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_review,
                            ok=False,
                            detail="Generated proactive message was rejected by the final content gate",
                            outcome_type="content_rejected",
                            error=note,
                            text=text or review_candidate_text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_review)
                    else:
                        self._mark_planned_candidate_status(current_for_review, "blocked", note)
                        self._clear_pending_proactive_plan(current_for_review)
                        self._schedule_next_proactive(current_for_review, now=_now_ts(), delay_hours=(1.5, 4.0))
                    self._update_proactive_audit(audit_id, status="cancelled", note=note, text=text or review_candidate_text)
                    self._save_data_sync(
                        sections={
                            "users",
                            "proactive_candidate_pool",
                            "proactive_audit_log",
                            "troubleshooting_test_results",
                        }
                    )
                logger.info("Proactive final content gate dropped: user=%s reason=%s text=%s", user_id, note, _single_line(text, 120))
                self._debug_tick_skip(user_id, note, prefix="dropped")
                return
        outbound_validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        if callable(outbound_validator):
            try:
                outbound_validation = outbound_validator(
                    text,
                    image_path=image_path,
                    extra_components=extra_components,
                    reason=reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                    action=effective_action_for_send or planned_action_for_send or "message",
                    source="send",
                )
            except Exception:
                outbound_validation = {"decision": "send", "text": text}
            outbound_decision = str(outbound_validation.get("decision") or "send")
            if outbound_decision == "drop":
                note = _single_line(outbound_validation.get("reason"), 120) or "主动正文未通过发送前本地校验"
                empty_render_failure = not text and not image_path and not extra_components
                if empty_render_failure and render_failure_stage:
                    note = _single_line(f"主动行为没有产出可发送内容：{render_failure_stage}", 360)
                async with self._data_lock:
                    current_for_outbound_guard = self._get_user(user_id)
                    current_for_outbound_guard["proactive_sending"] = False
                    current_for_outbound_guard["proactive_sending_started_at"] = 0
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(current_for_outbound_guard, "内容检查", "error", note)
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_for_outbound_guard,
                            ok=False,
                            detail="主动消息已生成，但未通过发送前本地校验",
                            error=note,
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current_for_outbound_guard)
                    else:
                        self._mark_planned_candidate_status(current_for_outbound_guard, "blocked", note)
                        self._clear_pending_proactive_plan(current_for_outbound_guard)
                        if empty_render_failure:
                            materialized = self._materialize_best_proactive_impulse(current_for_outbound_guard, now=_now_ts())
                            if not materialized:
                                self._schedule_next_proactive(current_for_outbound_guard, now=_now_ts(), delay_hours=(0.33, 1.0))
                        else:
                            self._schedule_next_proactive(current_for_outbound_guard, now=_now_ts(), delay_hours=(1.5, 4.0))
                    self._update_proactive_audit(audit_id, status="cancelled", note=note, text=text)
                    self._clear_pending_proactive_send_retry(current_for_outbound_guard)
                    self._save_data_sync(
                        sections={
                            "users",
                            "proactive_candidate_pool",
                            "proactive_audit_log",
                            "troubleshooting_test_results",
                        }
                    )
                logger.warning(
                    "主动消息发送前统一校验拦截: user=%s reason=%s text=%s",
                    user_id,
                    note,
                    _single_line(text, 180),
                )
                self._debug_tick_skip(user_id, note, prefix="取消")
                return
            if outbound_decision == "rewrite":
                validated_text = _single_line(outbound_validation.get("text"), 1200)
                if validated_text != text:
                    text_before_validation_rewrite = text
                    logger.warning(
                        "主动消息发送前统一校验改写: user=%s reason=%s before=%s after=%s",
                        user_id,
                        _single_line(outbound_validation.get("reason"), 120),
                        _single_line(text, 160),
                        _single_line(validated_text, 160),
                    )
                    text = validated_text
                    self._schedule_reply_interception_forward(
                        "rewrite",
                        source="主动消息统一校验",
                        reason=_single_line(outbound_validation.get("reason"), 240) or "发送前统一校验改写",
                        source_session=send_umo_for_send,
                        before=text_before_validation_rewrite,
                        after=text,
                    )
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_leak_checker) and text and meta_leak_checker(text):
            instruction_leak_checker = getattr(self, "_is_proactive_instruction_leak_text", None)
            note = (
                "主动正文疑似内部提示词/发送指令泄漏"
                if callable(instruction_leak_checker) and instruction_leak_checker(text)
                else "主动正文疑似工具循环/内部发送摘要泄漏"
            )
            async with self._data_lock:
                current_for_meta_leak = self._get_user(user_id)
                current_for_meta_leak["proactive_sending"] = False
                current_for_meta_leak["proactive_sending_started_at"] = 0
                self._mark_planned_candidate_status(current_for_meta_leak, "blocked", note)
                self._update_proactive_audit(audit_id, status="cancelled", note=note, text=text)
                self._clear_pending_proactive_send_retry(current_for_meta_leak)
                self._clear_pending_proactive_plan(current_for_meta_leak)
                self._schedule_next_proactive(current_for_meta_leak, now=_now_ts(), delay_hours=(1.5, 4.0))
                self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
            logger.warning(
                "主动消息发送前硬拦截元叙述泄漏: user=%s text=%s",
                user_id,
                _single_line(text, 180),
            )
            self._debug_tick_skip(user_id, note, prefix="取消")
            return
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            cleaned_text = placeholder_cleaner(text)
            if cleaned_text != text:
                logger.warning(
                    "主动消息清理到孤儿 TTS 占位符: user=%s before=%s after=%s",
                    user_id,
                    _single_line(text, 120),
                    _single_line(cleaned_text, 120),
                )
                text = cleaned_text
        if not is_troubleshooting_for_send and reason == "activity_share":
            async with self._data_lock:
                current_for_dedupe = self._get_user(user_id)
                duplicate_note = self._activity_share_recently_sent_elsewhere(
                    user_id,
                    current_for_dedupe,
                    text=text,
                    action_summary=action_summary,
                )
                if duplicate_note:
                    self._block_duplicate_activity_share_for_user(
                        current_for_dedupe,
                        duplicate_note=duplicate_note,
                        seconds=90 * 60,
                    )
                    removed_can_do = self._remove_can_do_targets(
                        [
                            current_for_dedupe.get("planned_proactive_topic"),
                            current_for_dedupe.get("planned_proactive_motive"),
                            action_summary,
                            text,
                            duplicate_note,
                        ]
                    )
                    current_for_dedupe["proactive_sending"] = False
                    current_for_dedupe["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_dedupe, "blocked", "同一日常碎片刚刚已分享给其他私聊对象")
                    self._clear_pending_proactive_plan(current_for_dedupe)
                    audit_note = f"跨用户活动分享去重: {duplicate_note}"
                    if removed_can_do:
                        audit_note = f"{audit_note}；已移除候选碎片 {len(removed_can_do)} 条"
                    self._update_proactive_audit(audit_id, status="cancelled", note=audit_note)
                    self._schedule_next_proactive(current_for_dedupe, now=_now_ts(), delay_hours=(2.0, 5.0))
                    self._save_data_sync(
                        sections={"users", "proactive_candidate_pool", "proactive_audit_log", "can_do"}
                    )
            if duplicate_note:
                logger.info(
                    "取消重复活动分享: user=%s duplicate=%s",
                    user_id,
                    _single_line(duplicate_note, 100),
                )
                self._debug_tick_skip(user_id, "同一日常碎片刚刚已分享给其他私聊对象", prefix="取消")
                return
        time_mismatch_reason = ""
        checker = getattr(self, "_proactive_time_mismatch_reason", None)
        if callable(checker):
            try:
                time_mismatch_reason = checker(
                    text,
                    reason=reason,
                    action=effective_action_for_send or planned_action_for_send or "message",
                )
            except Exception as exc:
                logger.debug("主动消息时间一致性复核失败: %s", _single_line(exc, 120))
                time_mismatch_reason = ""
        if time_mismatch_reason:
            logger.info(
                "主动消息时间不一致,已取消发送: user=%s reason=%s",
                user_id,
                _single_line(time_mismatch_reason, 160),
            )
            async with self._data_lock:
                current_for_time_guard = self._get_user(user_id)
                current_for_time_guard["proactive_sending"] = False
                current_for_time_guard["proactive_sending_started_at"] = 0
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current_for_time_guard, "时间复核", "error", time_mismatch_reason)
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_time_guard,
                        ok=False,
                        detail="主动消息已生成，但发送前时间一致性复核未通过",
                        error=time_mismatch_reason,
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._restore_troubleshooting_proactive_plan(current_for_time_guard)
                else:
                    self._mark_planned_candidate_status(current_for_time_guard, "blocked", time_mismatch_reason)
                    self._clear_pending_proactive_plan(current_for_time_guard)
                    self._schedule_next_proactive(current_for_time_guard, now=_now_ts(), delay_hours=(1.5, 4.0))
                self._update_proactive_audit(audit_id, status="cancelled", note=time_mismatch_reason)
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "proactive_audit_log",
                        "troubleshooting_test_results",
                    }
                )
            self._debug_tick_skip(user_id, "主动消息时间不一致", prefix="取消")
            return
        if not is_troubleshooting_for_send and (effective_action_for_send or planned_action_for_send or "message") == "message":
            async with self._data_lock:
                current_for_similarity_guard = self._get_user(user_id)
                timeliness_getter = getattr(self, "_planned_proactive_timeliness_level", None)
                timeliness = (
                    timeliness_getter(current_for_similarity_guard)
                    if callable(timeliness_getter)
                    else "routine"
                )
                similar_note = ""
                duplicate_policy = _single_line(route_options_for_send.get("duplicate_policy"), 40)
                if self._proactive_similarity_guard_enabled(
                    current_for_similarity_guard,
                    is_troubleshooting=is_troubleshooting_for_send,
                    action=effective_action_for_send or planned_action_for_send or "message",
                    timeliness=timeliness,
                    duplicate_policy=duplicate_policy,
                    enabled_policies=self._proactive_dedup_enabled_policies(),
                ):
                    similar_note = self._recent_proactive_text_duplicate_reason(
                        current_for_similarity_guard,
                        text=text,
                        topic=current_for_similarity_guard.get("planned_proactive_topic"),
                        motive=planned_motive_for_send,
                        now=_now_ts(),
                    )
                if similar_note:
                    current_for_similarity_guard["proactive_sending"] = False
                    current_for_similarity_guard["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_similarity_guard, "blocked", similar_note)
                    self._clear_pending_proactive_plan(current_for_similarity_guard)
                    self._schedule_next_proactive(current_for_similarity_guard, now=_now_ts(), delay_hours=(0.5, 1.5))
                    self._update_proactive_audit(audit_id, status="cancelled", note=similar_note, text=text)
                    self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
            if similar_note:
                logger.info(
                    "主动消息正文近似重复,已取消: user=%s reason=%s text=%s",
                    user_id,
                    _single_line(similar_note, 120),
                    _single_line(text, 120),
                )
                self._debug_tick_skip(user_id, similar_note, prefix="取消")
                return
        if (
            not is_troubleshooting_for_send
            and route_key_for_send == "ritual"
            and (effective_action_for_send or planned_action_for_send or "message") == "message"
        ):
            async with self._data_lock:
                current_for_greeting_text = self._get_user(user_id)
                textual_greeting_note = self._textual_greeting_duplicate_reason(
                    current_for_greeting_text,
                    text,
                    now=_now_ts(),
                )
                if textual_greeting_note:
                    current_for_greeting_text["proactive_sending"] = False
                    current_for_greeting_text["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_greeting_text, "blocked", textual_greeting_note)
                    self._clear_pending_proactive_plan(current_for_greeting_text)
                    self._schedule_next_proactive(current_for_greeting_text, now=_now_ts(), delay_hours=(2.0, 5.0))
                    self._update_proactive_audit(audit_id, status="cancelled", note=textual_greeting_note, text=text)
                    self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
            if textual_greeting_note:
                logger.info(
                    "主动消息正文命中重复问候,已取消: user=%s reason=%s text=%s",
                    user_id,
                    _single_line(textual_greeting_note, 120),
                    _single_line(text, 120),
                )
                self._debug_tick_skip(user_id, textual_greeting_note, prefix="取消")
                return
        if is_troubleshooting_for_send:
            async with self._data_lock:
                current_after_time_guard = self._get_user(user_id)
                self._append_troubleshooting_proactive_step(current_after_time_guard, "时间复核", "ok", "未发现明显错时内容")
                self._record_troubleshooting_proactive_result(
                    user_id,
                    current_after_time_guard,
                    ok=True,
                    detail="发送前复核通过，准备发送",
                    pending=True,
                    outcome_type="sending",
                    text=text,
                    action=effective_action_for_send or planned_action_for_send or "message",
                    reason=reason or "check_in",
                    extra_count=len(extra_components),
                )
                self._save_data_sync(sections={"users", "troubleshooting_test_results"})
        async with self._data_lock:
            current_after_render = self._get_user(user_id)
            has_new_user_message = (
                self._latest_private_user_activity_ts(current_after_render) > task_start_private_activity_at
                or _safe_int(current_after_render.get("private_inbound_count"), 0) > task_start_private_inbound_count
            )
        if has_new_user_message:
            if is_troubleshooting_for_send:
                logger.info(
                    "排障临时主动检测到生成期间有新消息,继续发送以验证链路: %s",
                    user_id,
                )
                async with self._data_lock:
                    current_for_warn = self._get_user(user_id)
                    self._append_troubleshooting_proactive_step(
                        current_for_warn,
                        "并发保护",
                        "warn",
                        "生成期间检测到新消息；排障测试继续发送以验证链路",
                    )
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_warn,
                        ok=True,
                        detail="生成期间检测到新消息；排障测试继续发送以验证链路",
                        pending=True,
                        outcome_type="sending",
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._save_data_sync(sections={"users", "troubleshooting_test_results"})
            elif not bool(route_options_for_send.get("cancel_if_new_inbound", True)):
                logger.info(
                    "生成期间收到新消息，但 %s 路线保留独立投递: user=%s",
                    route_key_for_send,
                    user_id,
                )
            else:
                logger.info(
                    "用户在主动消息生成期间已有新消息,%s 路线取消本次发送: %s",
                    route_key_for_send,
                    user_id,
                )
                async with self._data_lock:
                    current_for_clear = self._get_user(user_id)
                    current_for_clear["proactive_sending"] = False
                    current_for_clear["proactive_sending_started_at"] = 0
                    self._update_proactive_audit(audit_id, status="cancelled", note="用户在生成期间发来新消息,已取消本次主动")
                    self._save_data_sync(sections={"users", "proactive_audit_log"})
                return
        delivery_freshness_reason = ""
        if not is_troubleshooting_for_send:
            async with self._data_lock:
                current_for_freshness = self._get_user(user_id)
                delivery_freshness_reason = self._planned_proactive_send_freshness_reason(
                    current_for_freshness,
                    planned_delivery_snapshot,
                    now=_now_ts(),
                )
                if delivery_freshness_reason:
                    current_for_freshness["proactive_sending"] = False
                    current_for_freshness["proactive_sending_started_at"] = 0
                    self._mark_planned_candidate_status(current_for_freshness, "blocked", delivery_freshness_reason)
                    self._clear_pending_proactive_send_retry(current_for_freshness)
                    self._clear_pending_proactive_plan(current_for_freshness)
                    self._schedule_next_proactive(current_for_freshness, now=_now_ts(), delay_hours=(1.5, 4.0))
                    self._update_proactive_audit(audit_id, status="cancelled", note=delivery_freshness_reason)
                    self._save_data_sync(sections={"users", "proactive_candidate_pool", "proactive_audit_log"})
        if delivery_freshness_reason:
            logger.info(
                "主动候选在生成期间失效,已取消发送: user=%s reason=%s",
                user_id,
                _single_line(delivery_freshness_reason, 120),
            )
            self._debug_tick_skip(user_id, delivery_freshness_reason, prefix="取消")
            return
        if self._proactive_generation_disabled(user):
            async with self._data_lock:
                current_disabled = self._get_user(str(user_id))
                if self._suspend_user_proactive_generation(current_disabled):
                    self._save_data_sync(sections={"users"})
            return
        async with self._data_lock:
            current_for_recent_chat = self._get_user(user_id)
            recent_chat_guard_reason = self._route_recent_chat_guard_reason(
                current_for_recent_chat,
                now=_now_ts(),
                planned_reason=reason or normalize_legacy_tag_text(user.get("planned_proactive_reason")),
                due_timer_active=bool(due_timer_id),
                is_troubleshooting=is_troubleshooting_for_send,
            )
            if recent_chat_guard_reason:
                current_for_recent_chat["proactive_sending"] = False
                current_for_recent_chat["proactive_sending_started_at"] = 0
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current_for_recent_chat, "发送前复核", "error", recent_chat_guard_reason)
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_for_recent_chat,
                        ok=False,
                        detail="主动消息已生成，但发送前发现用户刚聊过，已取消",
                        error=recent_chat_guard_reason,
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._restore_troubleshooting_proactive_plan(current_for_recent_chat)
                else:
                    self._defer_route_for_recent_chat(
                        current_for_recent_chat,
                        now=_now_ts(),
                        note=recent_chat_guard_reason,
                    )
                self._update_proactive_audit(audit_id, status="deferred", note=recent_chat_guard_reason)
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "proactive_audit_log",
                        "troubleshooting_test_results",
                    }
                )
        if recent_chat_guard_reason:
            logger.info(
                "发送前发现刚聊完,延后普通主动: user=%s reason=%s",
                user_id,
                _single_line(recent_chat_guard_reason, 120),
            )
            self._debug_tick_skip(user_id, recent_chat_guard_reason, prefix="延后")
            return
        if text and self._is_proactive_delivery_receipt_text(text):
            note = "主动正文是工具/执行状态回执，已取消发送"
            logger.warning(
                "主动消息发送前拦截执行回执: user=%s text=%s",
                user_id,
                _single_line(text, 160),
            )
            async with self._data_lock:
                current = self._get_user(user_id)
                current["proactive_sending"] = False
                current["proactive_sending_started_at"] = 0
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current, "内容检查", "error", note)
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current,
                        ok=False,
                        detail="主动消息已生成，但正文是工具/执行状态回执",
                        error=note,
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._restore_troubleshooting_proactive_plan(current)
                else:
                    self._mark_planned_candidate_status(current, "dropped", note)
                    self._clear_pending_proactive_send_retry(current)
                    self._clear_pending_proactive_plan(current)
                    self._schedule_next_proactive(current, now=_now_ts(), delay_hours=(2, 8))
                self._update_proactive_audit(audit_id, status="dropped", note=note, text=text)
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "proactive_audit_log",
                        "troubleshooting_test_results",
                    }
                )
            self._debug_tick_skip(user_id, note, prefix="放弃")
            return
        if not text and not image_path and not extra_components:
            empty_note = "主动行为没有产出可发送内容"
            if render_failure_stage:
                empty_note = _single_line(f"{empty_note}：{render_failure_stage}", 360)
            async with self._data_lock:
                current = self._get_user(user_id)
                current["proactive_sending"] = False
                current["proactive_sending_started_at"] = 0
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current, "内容检查", "error", empty_note)
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current,
                        ok=False,
                        detail=empty_note,
                        error="主动消息两级渲染仍为空",
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                    )
                    self._restore_troubleshooting_proactive_plan(current)
                elif self._simulation_active(current):
                    self._consume_simulation_event(current)
                else:
                    self._mark_planned_candidate_status(current, "dropped", empty_note)
                    self._clear_pending_proactive_plan(current)
                    materialized = self._materialize_best_proactive_impulse(current, now=_now_ts())
                    if not materialized:
                        self._schedule_next_proactive(current, now=_now_ts(), delay_hours=(0.33, 1.0))
                self._update_proactive_audit(audit_id, status="dropped", note=empty_note)
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "proactive_audit_log",
                        "troubleshooting_test_results",
                    }
                )
            self._debug_tick_skip(user_id, empty_note, prefix="放弃")
            return
        try:
            reason_label = _REASON_TEXT.get(reason, reason or "check_in")
            target_name = _single_line(
                user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"),
                24,
            )
            reason_label = reason_label.replace("{name}", target_name)
            reason_detail = "；".join(
                item
                for item in (
                    f"话题={planned_topic_for_send}" if planned_topic_for_send else "",
                    f"动机={planned_motive_for_send}" if planned_motive_for_send else "",
                )
                if item
            )
            logger.info(
                "准备主动发送给 %s: reason=%s(%s) action=%s quote=%s umo=%s text=%s image=%s extra=%s%s",
                user_id,
                reason,
                reason_label,
                effective_action_for_send or planned_action_for_send or "message",
                bool(proactive_quote_message_id),
                send_umo_for_send,
                _single_line(text, 120),
                bool(image_path),
                len(extra_components),
                f" detail={reason_detail}" if reason_detail else "",
            )
            delivered = await self._send_proactive_message_chain(
                send_umo_for_send,
                text,
                image_path,
                extra_components=extra_components,
                quote_message_id=proactive_quote_message_id,
                disable_segmenting=(
                    bool(route_options_for_send.get("disable_segmenting"))
                    or self._proactive_send_disables_segmenting(
                        reason,
                        friend_proactive=friend_proactive_for_send,
                    )
                ),
            )
            if not delivered:
                outcome_note = _single_line(getattr(delivered, "note", ""), 180)
                cancel_note = "主动发送在实际投递前被取消或清空，未计入发送记录"
                if outcome_note:
                    cancel_note = f"{cancel_note}：{outcome_note}"
                logger.info(
                    "主动消息未实际投递: user=%s reason=%s action=%s",
                    user_id,
                    reason,
                    effective_action_for_send or planned_action_for_send or "message",
                )
                async with self._data_lock:
                    current_cancelled = self._get_user(user_id)
                    if is_troubleshooting_for_send:
                        self._append_troubleshooting_proactive_step(
                            current_cancelled,
                            "主动发送",
                            "error",
                            cancel_note,
                        )
                        self._record_troubleshooting_proactive_result(
                            user_id,
                            current_cancelled,
                            ok=False,
                            detail=cancel_note,
                            outcome_type="delivery_cancelled",
                            error=cancel_note,
                            text=text,
                            action=effective_action_for_send or planned_action_for_send or "message",
                            reason=reason or "check_in",
                            extra_count=len(extra_components),
                        )
                        self._restore_troubleshooting_proactive_plan(current_cancelled)
                    elif self._simulation_active(current_cancelled):
                        self._consume_simulation_event(current_cancelled)
                    else:
                        self._mark_planned_candidate_status(current_cancelled, "dropped", cancel_note)
                        self._clear_pending_proactive_send_retry(current_cancelled)
                        self._clear_pending_proactive_plan(current_cancelled)
                        materialized = self._materialize_best_proactive_impulse(
                            current_cancelled,
                            now=_now_ts(),
                        )
                        if not materialized:
                            self._schedule_next_proactive(
                                current_cancelled,
                                now=_now_ts(),
                                delay_hours=(0.5, 2.0),
                            )
                    self._update_proactive_audit(
                        audit_id,
                        status="dropped",
                        note=cancel_note,
                        text=text,
                    )
                    self._save_data_sync(
                        sections={
                            "users",
                            "proactive_candidate_pool",
                            "proactive_audit_log",
                            "troubleshooting_test_results",
                        }
                    )
                self._debug_tick_skip(user_id, cancel_note, prefix="取消")
                return
            delivery_complete = bool(getattr(delivered, "complete", True))
            delivery_note = _single_line(getattr(delivered, "note", ""), 200)
            if hasattr(delivered, "delivered_text"):
                requested_image_path = image_path
                requested_extra_components = list(extra_components)
                text = str(getattr(delivered, "delivered_text", "") or "")
                image_path = requested_image_path if bool(getattr(delivered, "image_delivered", False)) else ""
                delivered_extra_count = _safe_int(
                    getattr(delivered, "extra_components_delivered", 0),
                    0,
                    0,
                    len(requested_extra_components),
                )
                extra_components = requested_extra_components[:delivered_extra_count]
                action_for_delivery = effective_action_for_send or planned_action_for_send or "message"
                effective_action_for_send, action_summary, delivered_has_photo = self._reconcile_proactive_delivery_metadata(
                    text=text,
                    image_path=image_path,
                    extra_components=extra_components,
                    action=action_for_delivery,
                    action_summary=action_summary,
                    delivery_complete=delivery_complete,
                )
            else:
                delivered_has_photo = bool(image_path) or self._proactive_components_contain_image(extra_components)
            if not delivery_complete:
                logger.warning(
                    "主动消息仅部分投递，后续只按真实送达内容归档: user=%s reason=%s note=%s",
                    user_id,
                    reason,
                    delivery_note or "部分组件被取消或发送失败",
                )
            if image_path:
                annotator = getattr(self, "_annotate_recent_photo_generation", None)
                if callable(annotator):
                    delivered_photo_caption = ""
                    if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                        delivered_photo_caption = _single_line(
                            re.split(r"[:：]", str(action_summary), maxsplit=1)[-1],
                            160,
                        )
                    annotator(
                        image_path=image_path,
                        session_key=send_umo_for_send,
                        trigger="proactive",
                        sent=True,
                        caption=delivered_photo_caption,
                        tool_name="proactive_photo",
                    )
            async with self._data_lock:
                current_after_send = self._get_user(user_id)
                sent_at = _now_ts()
                delivered_text = self._visible_text_without_tts_reading(text, limit=500)
                current_after_send["last_proactive_message"] = _single_line(delivered_text, 500)
                current_after_send["last_proactive_sent_at"] = sent_at
                current_after_send["last_proactive_delivery_umo"] = _single_line(
                    getattr(delivered, "delivery_umo", "") or send_umo_for_send,
                    180,
                )
                delivery_success_recorder = getattr(self, "_note_private_delivery_success", None)
                if callable(delivery_success_recorder):
                    delivery_success_recorder(user_id, current_after_send, send_umo_for_send)
                current_after_send["last_proactive_delivery_inbound_count"] = _safe_int(
                    current_after_send.get("inbound_count"),
                    0,
                )
                current_after_send["last_proactive_reply_context_consumed_for"] = 0
                location_reason = _single_line(reason, 40)
                location_event_type = _single_line(
                    current_after_send.get("planned_mobile_location_event_type"),
                    32,
                )
                if (
                    location_reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}
                    or location_event_type
                    or _single_line(current_after_send.get("planned_mobile_location_transition_key"), 80)
                ) and not self._simulation_active(current_after_send):
                    current_after_send["last_mobile_location_humanization_at"] = sent_at
                    current_after_send["last_mobile_location_humanization_kind"] = location_reason or location_event_type
                if not self._simulation_active(current_after_send):
                    self._commit_mobile_location_arrival_after_send(current_after_send)
                if reason == "group_share":
                    remember_group_share = getattr(self, "_remember_recent_group_share_snapshot", None)
                    if callable(remember_group_share):
                        remember_group_share(
                            current_after_send,
                            share_context=current_after_send.get("group_share_context"),
                            shared_text=delivered_text,
                            sent_at=sent_at,
                            delivery_umo=send_umo_for_send,
                        )
                self._save_data_sync(sections={"users"})
            if not is_troubleshooting_for_send and reason == "creative_share":
                # Keep a per-user anchor before history archival so an immediate reply has context.
                self._remember_recent_creative_share_snapshot(
                    user,
                    creative_context=creative_share_context_for_send,
                    shared_text=text,
                    sent_at=_now_ts(),
                )
            if is_troubleshooting_for_send:
                async with self._data_lock:
                    current_after_send = self._get_user(user_id)
                    self._append_troubleshooting_proactive_step(current_after_send, "主动发送", "ok", "已调用 AstrBot 主动发送接口")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_after_send,
                        ok=True,
                        detail="主动消息已发送，准备写入会话历史",
                        pending=True,
                        outcome_type="archiving",
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._save_data_sync(sections={"users", "troubleshooting_test_results"})
            logger.info(
                "主动发送完成: user=%s reason=%s action=%s complete=%s",
                user_id,
                reason,
                planned_action_for_send or "message",
                delivery_complete,
            )
            delivery_umo = str(
                getattr(delivered, "delivery_umo", "") or send_umo_for_send
            ).strip()
            assistant_archive_text = self._delivered_assistant_text_from_chain(
                list(getattr(delivered, "delivered_chain", ()) or ()),
                fallback_text=text,
            )
            await self._archive_proactive_message_to_conversation(
                user=user,
                umo=delivery_umo,
                user_prompt=self._build_proactive_archive_user_prompt(
                    reason=reason,
                    action=effective_action_for_send or planned_action_for_send or "message",
                    motive=planned_motive_for_send,
                    action_summary=action_summary,
                ),
                assistant_response=assistant_archive_text,
            )
            await self._record_final_assistant_in_livingmemory(
                umo=delivery_umo,
                assistant_response=assistant_archive_text,
                delivery_id=str(audit_id or f"proactive:{user_id}:{_now_ts():.6f}"),
            )
            if is_troubleshooting_for_send:
                async with self._data_lock:
                    current_after_archive = self._get_user(user_id)
                    self._append_troubleshooting_proactive_step(current_after_archive, "历史归档", "ok", "已调用 AstrBot 会话历史写入")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_after_archive,
                        ok=True,
                        detail="已完成排障临时主动消息发送与归档调用",
                        pending=True,
                        outcome_type="finalizing",
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                    )
                    self._save_data_sync(sections={"users", "troubleshooting_test_results"})
        except Exception as e:
            formatter = getattr(self, "_format_send_exception", None)
            error_text = formatter(e) if callable(formatter) else (_single_line(str(e), 180) or repr(e))
            diagnostic_detail = f"{e.__class__.__name__}: {_single_line(str(e) or repr(e), 2300)}"
            logger.warning("发送给 %s 失败: %s", user_id, error_text)
            async with self._data_lock:
                current_after_failure = self._get_user(user_id)
                delivery_failure_recorder = getattr(self, "_note_private_delivery_failure", None)
                if callable(delivery_failure_recorder):
                    delivery_failure_recorder(user_id, current_after_failure, send_umo_for_send, error_text)
                if is_troubleshooting_for_send:
                    self._append_troubleshooting_proactive_step(current_after_failure, "主动发送", "error", f"发送失败: {_single_line(error_text, 120)}")
                    self._record_troubleshooting_proactive_result(
                        user_id,
                        current_after_failure,
                        ok=False,
                        detail="主动消息已生成，但发送失败",
                        outcome_type="delivery_failed",
                        error=f"发送失败: {_single_line(error_text, 160)}",
                        text=text,
                        action=effective_action_for_send or planned_action_for_send or "message",
                        reason=reason or "check_in",
                        extra_count=len(extra_components),
                        diagnostic_detail=diagnostic_detail,
                    )
                    self._restore_troubleshooting_proactive_plan(current_after_failure)
                else:
                    planned_snapshot = self._planned_proactive_status_snapshot(current_after_failure)
                    retry_note = self._store_or_advance_proactive_send_retry(
                        current_after_failure,
                        text=text,
                        image_path=image_path,
                        extra_components=extra_components,
                        reason=reason or "check_in",
                        action=effective_action_for_send or planned_action_for_send or "message",
                        action_summary=action_summary,
                        error_text=error_text,
                        photo_subject_owner=photo_subject_owner_for_send,
                        now=_now_ts(),
                    )
                    retry_payload = current_after_failure.get("pending_proactive_send_retry")
                    if isinstance(retry_payload, dict) and retry_payload.get("active"):
                        self._mark_planned_candidate_status(
                            current_after_failure,
                            "deferred",
                            retry_note,
                            planned_snapshot=planned_snapshot,
                        )
                self._update_proactive_audit(
                    audit_id,
                    status="failed",
                    note=f"发送失败: {_single_line(error_text, 140)}",
                    diagnostic_detail=diagnostic_detail,
                )
                self._save_data_sync(
                    sections={
                        "users",
                        "proactive_candidate_pool",
                        "proactive_audit_log",
                        "troubleshooting_test_results",
                    }
                )
            return
        finally:
            async with self._data_lock:
                current_for_clear = self._get_user(user_id)
                current_for_clear["proactive_sending"] = False
                current_for_clear["proactive_sending_started_at"] = 0
                self._save_data_sync(sections={"users"})

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
                return
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
        if memory_companion_proactive_payload:
            await self._memory_companion_record_proactive_message(**memory_companion_proactive_payload)
