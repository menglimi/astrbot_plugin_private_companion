# -*- coding: utf-8 -*-
"""DailyStateTickTickCorePart02Mixin。

由 tmp/refactor/dstc_split.py 从 daily_state_tick_tick_core.py 的 _tick_user 段级拆分而来（1 段）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

from .helpers import (
    _normalize_outbound_punctuation_flow,
    _safe_float,
    _safe_int,
    _single_line,
    normalize_legacy_tag_text,
)
from .persona_config import runtime_persona_setting
from .daily_state_tick_shared import (
    _now_ts,
    logger,
)


class DailyStateTickTickCorePart02Mixin:
    """_tick_user 的 review 段。"""

    async def _tick_user_review(
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
        review_candidate_text,
        route_key_for_send,
        route_options_for_send,
        route_settlement_for_send,
        send_umo_for_send,
        task_start_private_activity_at,
        task_start_private_inbound_count,
        text,
    ):
        """_tick_user 段：发送前价值复核（放行 / 延后 / 改写 / 丢弃）。"""
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
                return None
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
                return None
        return (text,)
