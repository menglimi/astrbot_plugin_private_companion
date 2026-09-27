# -*- coding: utf-8 -*-
"""DailyStateTickTickCorePart04Mixin。

由 tmp/refactor/dstc_split.py 从 daily_state_tick_tick_core.py 的 _tick_user 段级拆分而来（1 段）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

import re
from .constants import _REASON_TEXT
from .helpers import (
    _safe_int,
    _single_line,
)
from .persona_config import runtime_persona_setting
from .daily_state_tick_shared import (
    _now_ts,
    logger,
)


class DailyStateTickTickCorePart04Mixin:
    """_tick_user 的 send 段。"""

    async def _tick_user_send(
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
        planned_followup_kind_for_send,
        planned_motive_for_send,
        planned_opener_mode_for_send,
        planned_topic_for_send,
        proactive_quote_message_id,
        reason,
        route_key_for_send,
        route_options_for_send,
        route_settlement_for_send,
        send_umo_for_send,
        text,
    ):
        """_tick_user 段：实际投递与归档。"""
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
                return None
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
            return None
        finally:
            async with self._data_lock:
                current_for_clear = self._get_user(user_id)
                current_for_clear["proactive_sending"] = False
                current_for_clear["proactive_sending_started_at"] = 0
                self._save_data_sync(sections={"users"})
        return action_summary, delivered_has_photo, delivery_complete, delivery_note, delivery_umo, effective_action_for_send, extra_components, image_path, text
