# -*- coding: utf-8 -*-
"""handle_private_message 阶段 2（fastlane / locked_head）。

由 tmp/refactor/mpp2_split.py 从 message_pipeline_part02.py 的 handle_private_message 段级拆分而来。
本模块无类：阶段为模块级 async 函数，显式接收 self。
阶段体与拆分前逐字节相同（缩进归一）；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
"""
from __future__ import annotations

from typing import Any

from .helpers import (
    _missing_optional_model_dependency,
    _reset_unanswered_proactive,
    _safe_float,
    _safe_int,
    _single_line,
)
from .message_pipeline_part02_shared import _StageNext
from .message_pipeline_part01 import _persona_value
from .message_pipeline_shared import logger
from .photo_nai_params import cache_user_photo_nai_params


async def _handle_private_message_fastlane(
    self,
    calendar_observation_result,
    event,
    forward_only_prompt,
    received_ts,
    reference_media_with_text,
    sender_display_name,
    text,
    user_id,
):
    """handle_private_message 段：轻量私聊快路径：去重、状态结算、记忆桥与提前放行。"""
    raw_users = self.data.get("users", {})
    fast_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
    fast_target_user = self._private_passive_profile_available(user_id, fast_user)
    if (
        fast_target_user
        and text
        and not forward_only_prompt
        and not reference_media_with_text
        and await self._maybe_resume_pending_atrelay_request(event, user_id, text)
    ):
        return
    if (
        fast_target_user
        and text
        and not forward_only_prompt
        and not reference_media_with_text
        and await self._maybe_handle_direct_atrelay_request(event, text)
    ):
        return
    if (
        fast_target_user
        and text
        and not forward_only_prompt
        and not bool(_persona_value(self, 'enable_smart_message_debounce', False))
        and self._message_debounce_seconds("text") <= 0
        and self._is_lightweight_private_passive_inbound(text)
        and not self._meal_care_requires_full_reply(fast_user, text)
        and not self._is_private_image_only_message(event, text)
    ):
        if self._is_recent_poke_echo(fast_user, text):
            logger.info("忽略 poke 回流事件,不计入用户新消息: %s", user_id)
            return
        if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
            self._record_passive_no_reply(
                event,
                source="私聊去重",
                reason="重复私聊事件被忽略",
                detail=text,
                level="info",
            )
            event.stop_event()
            return
        self._note_private_user_umo(user_id, fast_user, event.unified_msg_origin)
        self._note_private_display_name_observation(fast_user, user_id, sender_display_name, now=received_ts)
        fast_user["last_seen"] = received_ts
        fast_user["last_activity_at"] = received_ts
        self._note_private_inbound_activity(fast_user, received_ts, text=text)
        self._mark_greetings_satisfied_by_recent_activity(fast_user, activity_ts=received_ts)
        self._note_morning_greeting_reply(fast_user, now=received_ts)
        if self._cancel_inbound_conflicting_greeting(
            fast_user,
            now=received_ts,
            user_id=user_id,
            trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
        ):
            logger.info("用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
            if not self._simulation_active(fast_user) and _safe_float(fast_user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(fast_user, now=received_ts)
        safe_text = self._sanitize_orphan_tts_placeholders(text)
        fast_user["last_user_message"] = safe_text or text
        fast_user["last_user_message_at"] = received_ts
        cache_user_photo_nai_params(fast_user, safe_text or text, received_at=received_ts)
        self._note_user_chronotype_from_inbound(fast_user, safe_text or text, received_ts)
        fast_intent_profile = self._analyze_inbound_intent(text)
        boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
        if callable(boundary_enricher):
            fast_intent_profile = boundary_enricher(fast_user, fast_intent_profile)
        fast_user["intent_profile"] = fast_intent_profile
        violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
        if callable(violation_settler):
            violation_settler(
                fast_user,
                fast_intent_profile,
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        if self._clear_state_share_proactive_after_user_status_question(fast_user, user_id=user_id, text=safe_text or text, now=received_ts):
            if not self._simulation_active(fast_user) and _safe_float(fast_user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(fast_user, now=received_ts)
        try:
            read_view_getter = getattr(self, "_req041_relationship_read_view", None)
            fast_read_user = (
                read_view_getter(event, fast_user, kind="private")
                if callable(read_view_getter) else fast_user
            )
            scoped_read_getter = getattr(self, "_req041_scoped_private_read_view", None)
            if callable(scoped_read_getter):
                fast_read_user = scoped_read_getter(event, fast_read_user)
            await self._memory_companion_apply_emotional_drift(
                event=event,
                user_id=user_id,
                user=fast_user,
            )
            self._memory_companion_attach_private_context(
                event,
                user_id=user_id,
                user=fast_read_user,
                text=safe_text or text,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "私聊轻量链路记忆桥缺少可选模型依赖，已跳过记忆增强: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
        rest_silence_applied = self._apply_user_rest_silence_from_message(fast_user, safe_text or text, now=received_ts)
        fast_user["inbound_count"] = _safe_int(fast_user.get("inbound_count"), 0) + 1
        self._apply_relationship_event(
            fast_user,
            1,
            reason_code="fast_inbound",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
        fast_user["episode_message_count"] = _safe_int(fast_user.get("episode_message_count"), 0, 0) + 1
        if _safe_float(fast_user.get("awaiting_reply_since"), 0) > 0:
            audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
            if callable(audit_outcome_recorder):
                audit_outcome_recorder(
                    fast_user,
                    received_at=received_ts,
                    message_id=self._event_message_id(event),
                )
            fast_user["reply_count"] = _safe_int(fast_user.get("reply_count"), 0) + 1
            self._note_action_reply_feedback(
                fast_user,
                str(fast_user.get("last_proactive_action") or "message"),
                text,
            )
            self._apply_relationship_event(
                fast_user,
                2,
                reason_code="fast_proactive_reply",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
            fast_user["awaiting_reply_since"] = 0
            fast_user["last_reply_at"] = received_ts
            fast_user["last_private_reply_at"] = received_ts
            fast_user["pending_followup_event"] = {}
            fast_user["planned_proactive_quota_exempt"] = False
        _reset_unanswered_proactive(fast_user, replied_at=received_ts)
        fast_user["friend_unanswered_silenced_since"] = 0
        fast_user["friend_unanswered_silence_note"] = ""
        fast_user_is_owner = self._private_user_role(fast_user, user_id) == "owner"
        fast_meal_care_result: dict[str, Any] = {}
        if fast_user_is_owner:
            fast_meal_care_result = self._handle_meal_care_inbound(
                fast_user,
                safe_text or text,
                now=received_ts,
            )
        fast_interaction_warmth_applied = (
            bool(_persona_value(self, "enable_custom_relationship_stage_policy", False))
            and fast_user_is_owner
            and self._apply_interaction_warmth_to_state(text, fast_user)
        )
        fast_calendar_observation_result: dict[str, Any] = {}
        observer = getattr(self, "_agenda_observe_calendar_message", None)
        if callable(observer):
            try:
                fast_calendar_observation_result = observer(
                    text=safe_text or text,
                    event_time=received_ts,
                    source_ref=self._event_message_id(event) or f"{str(getattr(event, 'unified_msg_origin', '') or '')}:{received_ts}",
                    conversation_id=str(getattr(event, "unified_msg_origin", "") or ""),
                    source_user_id=user_id,
                    target_user_id=user_id,
                    subject_actor_id=str(getattr(self, "bot_personal_subject", "") or "bot_self"),
                ) or {}
            except Exception as exc:
                logger.warning(
                    "轻量私聊日历候选观察失败，已放行当前回复: user=%s error=%s",
                    user_id,
                    _single_line(exc, 160),
                )
        if fast_interaction_warmth_applied:
            self._apply_relationship_event(
                fast_user,
                1,
                reason_code="fast_interaction_warmth",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        fast_save_sections = {"users"}
        if fast_meal_care_result.get("foods"):
            fast_save_sections.add("food_menu")
        if fast_interaction_warmth_applied:
            fast_save_sections.update({"state_conditions", "daily_state"})
        fast_save_sections.update(fast_calendar_observation_result.get("changed_sections") or ())
        self._schedule_data_save(sections=fast_save_sections)
        if (
            rest_silence_applied
            and _safe_float(fast_user.get("user_rest_until"), 0) > received_ts
            and self._user_rest_signal_should_block_current_reply(safe_text or text)
        ):
            self._stop_private_reply_after_user_rest_signal(event, user_id, safe_text or text)
            return
        return

    rest_silence_early_block = False
    rest_silence_early_text = ""
    return _StageNext((calendar_observation_result, event, forward_only_prompt, received_ts, reference_media_with_text, rest_silence_early_block, rest_silence_early_text, sender_display_name, text, user_id))

async def _handle_private_message_locked_head(
    self,
    calendar_observation_result,
    event,
    forward_only_prompt,
    received_ts,
    reference_media_with_text,
    rest_silence_early_block,
    rest_silence_early_text,
    sender_display_name,
    text,
    user_id,
):
    """handle_private_message 段：锁内用户解析、去重判定与图文增强前置。"""
    users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
    existing_user = users.get(user_id) if isinstance(users, dict) else None
    is_target_user = self._private_passive_profile_available(
        user_id,
        existing_user if isinstance(existing_user, dict) else None,
    )
    if not is_target_user:
        logger.info(
            "非目标/未启用私聊不记录陪伴资料: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        return
    user = self._get_user(user_id)
    if self._is_recent_poke_echo(user, text):
        logger.info("忽略 poke 回流事件,不计入用户新消息: %s", user_id)
        return
    if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
        self._schedule_data_save(sections={"inbound_debounce_stats"})
        self._record_passive_no_reply(
            event,
            source="私聊去重",
            reason="重复私聊事件被忽略",
            detail=text,
            level="info",
        )
        event.stop_event()
        return
    smart_debounce_state_changed = False
    if is_target_user and text and not forward_only_prompt and not reference_media_with_text:
        smart_debounce_state_changed = bool(self._maybe_record_smart_message_debounce_followup(
            scope=f"private:{user_id}",
            sender_id=user_id,
            text=text,
            now=received_ts,
        ))
    private_image_enhancement_enabled = (
        self._feature_enabled_or_temp_unlocked("enable_private_image_self_recognition")
        and bool(_persona_value(self, 'enable_message_debounce', _persona_value(self, 'enable_semantic_message_debounce', True)))
        and self._message_debounce_seconds("image") > 0
    )
    private_image_only = (
        is_target_user
        and private_image_enhancement_enabled
        and self._is_private_image_only_message(event, text)
    )
    return _StageNext((calendar_observation_result, event, forward_only_prompt, is_target_user, private_image_enhancement_enabled, private_image_only, received_ts, reference_media_with_text, rest_silence_early_block, rest_silence_early_text, sender_display_name, smart_debounce_state_changed, text, user, user_id))
