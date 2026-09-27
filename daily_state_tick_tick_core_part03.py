# -*- coding: utf-8 -*-
"""DailyStateTickTickCorePart03Mixin。

由 tmp/refactor/dstc_split.py 从 daily_state_tick_tick_core.py 的 _tick_user 段级拆分而来（2 段）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

from .helpers import (
    _safe_int,
    _single_line,
    normalize_legacy_tag_text,
)
from .daily_state_tick_shared import (
    _now_ts,
    logger,
)


class DailyStateTickTickCorePart03Mixin:
    """_tick_user 的 guard_outbound / guard_delivery 段。"""

    async def _tick_user_guard_outbound(
        self,
        user_id,
        user,
        action_summary,
        audit_id,
        creative_share_context_for_send,
        due_timer_id,
        effective_action_for_send,
        extra_components,
        friend_proactive_for_send,
        image_path,
        is_troubleshooting_for_send,
        photo_subject_owner_for_send,
        planned_action_for_send,
        planned_chain_for_send,
        planned_delivery_snapshot,
        planned_followup_kind_for_send,
        planned_motive_for_send,
        planned_opener_mode_for_send,
        planned_topic_for_send,
        proactive_quote_message_id,
        reason,
        render_failure_stage,
        route_key_for_send,
        route_options_for_send,
        route_settlement_for_send,
        send_umo_for_send,
        task_start_private_activity_at,
        task_start_private_inbound_count,
        text,
    ):
        """_tick_user 段：出站校验 / 元叙述泄漏 / 去重 / 时间一致性 / 近似重复。"""
        outbound_validator = getattr(self, "_validate_proactive_outbound_candidate", None)
        if callable(outbound_validator):
            try:
                outbound_validation = outbound_validator(
                    text,
                    umo=send_umo_for_send,
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
                return None
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
            return None
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
                return None
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
            return None
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
                return None
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
                return None
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
        return has_new_user_message, text


    async def _tick_user_guard_delivery(
        self,
        user_id,
        user,
        action_summary,
        audit_id,
        creative_share_context_for_send,
        due_timer_id,
        effective_action_for_send,
        extra_components,
        friend_proactive_for_send,
        has_new_user_message,
        image_path,
        is_troubleshooting_for_send,
        photo_subject_owner_for_send,
        planned_action_for_send,
        planned_chain_for_send,
        planned_delivery_snapshot,
        planned_followup_kind_for_send,
        planned_motive_for_send,
        planned_opener_mode_for_send,
        planned_topic_for_send,
        proactive_quote_message_id,
        reason,
        render_failure_stage,
        route_key_for_send,
        route_options_for_send,
        route_settlement_for_send,
        send_umo_for_send,
        text,
    ):
        """_tick_user 段：生成期并发 / 新鲜度 / 刚聊过 / 回执与空内容拦截。"""
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
                return None
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
            return None
        if self._proactive_generation_disabled(user):
            async with self._data_lock:
                current_disabled = self._get_user(str(user_id))
                if self._suspend_user_proactive_generation(current_disabled):
                    self._save_data_sync(sections={"users"})
            return None
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
            return None
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
            return None
        sticker_pending_getter = getattr(self, "_proactive_sticker_only_pending", None)
        try:
            sticker_only_pending = bool(sticker_pending_getter(user.get("umo"))) if callable(sticker_pending_getter) else False
        except Exception:
            sticker_only_pending = False
        if not text and not image_path and not extra_components and not sticker_only_pending:
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
            return None
        return ()
