# -*- coding: utf-8 -*-
"""handle_private_message 阶段 4（lock_state）。

由 tmp/refactor/mpp2_split.py 从 message_pipeline_part02.py 的 handle_private_message 段级拆分而来。
本模块无类：阶段为模块级 async 函数，显式接收 self。
阶段体与拆分前逐字节相同（缩进归一）；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
"""
from __future__ import annotations

from typing import Any

from .helpers import (
    _now_ts,
    _reset_unanswered_proactive,
    _safe_float,
    _safe_int,
)
from .message_pipeline_part02_shared import _StageNext
from .message_pipeline_part01 import _persona_value


async def _handle_private_message_lock_state(
    self,
    calendar_observation_result,
    event,
    is_target_user,
    received_ts,
    rest_silence_early_block,
    rest_silence_early_text,
    sender_display_name,
    smart_debounce_state_changed,
    text,
    user,
    user_id,
):
    """handle_private_message 段：用户状态结算与安全文本派生。"""
    user["umo"] = event.unified_msg_origin
    self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
    if not is_target_user:
        user["enabled"] = False
        self._clear_pending_proactive_plan(user)
    user["last_seen"] = _now_ts()
    user["last_activity_at"] = received_ts or _now_ts()
    self._note_private_inbound_activity(user, received_ts or _now_ts(), text=text)
    self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts or _now_ts())
    self._note_morning_greeting_reply(user, now=received_ts or _now_ts())
    private_memory_managed = False
    private_memory_revision = None
    # Feedback is only evaluated for textual inbound messages, but the
    # final persistence section is shared by all private message types.
    expression_feedback: dict[str, Any] = {}
    if text:
        user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
    self._apply_relationship_event(
        user,
        1,
        reason_code="inbound",
        event_id=self._event_message_id(event),
        now=received_ts,
    )
    suspended = user.get("suspended_proactive")
    if (
        isinstance(suspended, dict)
        and suspended.get("active")
        and _now_ts() - _safe_float(suspended.get("created_at"), 0) <= _persona_value(self, 'proactive_reply_context_hours', 12) * 3600
    ):
        suspended["resume_ready"] = True
        suspended["complaint_enabled"] = False
        suspended["complaint_sent"] = True
        suspended["second_followup"] = {}
        user["pending_followup_event"] = {}
        user["planned_proactive_quota_exempt"] = False
    if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
        audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
        if callable(audit_outcome_recorder):
            audit_outcome_recorder(
                user,
                received_at=received_ts,
                message_id=self._event_message_id(event),
            )
        user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
        self._note_action_reply_feedback(
            user,
            str(user.get("last_proactive_action") or "message"),
            text,
        )
        self._apply_relationship_event(
            user,
            2,
            reason_code="proactive_reply",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
        user["awaiting_reply_since"] = 0
        user["last_reply_at"] = _now_ts()
        user["last_private_reply_at"] = user["last_reply_at"]
        user["pending_followup_event"] = {}
        user["planned_proactive_quota_exempt"] = False
    _reset_unanswered_proactive(user, replied_at=received_ts)
    user["friend_unanswered_silenced_since"] = 0
    user["friend_unanswered_silence_note"] = ""
    return _StageNext((calendar_observation_result, event, expression_feedback, is_target_user, private_memory_managed, private_memory_revision, received_ts, rest_silence_early_block, rest_silence_early_text, smart_debounce_state_changed, text, user, user_id))
