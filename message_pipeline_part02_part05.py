# -*- coding: utf-8 -*-
"""handle_private_message 阶段 5（lock_memory）。

由 tmp/refactor/mpp2_split.py 从 message_pipeline_part02.py 的 handle_private_message 段级拆分而来。
本模块无类：阶段为模块级 async 函数，显式接收 self。
阶段体与拆分前逐字节相同（缩进归一）；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
)
from .message_pipeline_part02_shared import _StageNext
from .companion_interaction_expression import current_interaction_projection
from .message_pipeline_part01 import _persona_value
from .message_pipeline_shared import logger
from .photo_nai_params import cache_user_photo_nai_params


async def _handle_private_message_lock_memory(
    self,
    calendar_observation_result,
    event,
    expression_feedback,
    is_target_user,
    private_memory_managed,
    private_memory_revision,
    received_ts,
    rest_silence_early_block,
    rest_silence_early_text,
    smart_debounce_state_changed,
    text,
    user,
    user_id,
):
    """handle_private_message 段：记忆/表达/日历观察、食物照护反馈与记忆上下文挂载。"""
    if text:
        safe_text = self._sanitize_orphan_tts_placeholders(text)
        user["last_user_message"] = safe_text or text
        user["last_user_message_at"] = received_ts
        cache_user_photo_nai_params(user, safe_text or text, received_at=received_ts)
        self._note_user_chronotype_from_inbound(user, safe_text or text, received_ts)
        if is_target_user and self._clear_state_share_proactive_after_user_status_question(user, user_id=user_id, text=safe_text or text, now=received_ts):
            if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=received_ts)
        rest_silence_applied = self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
        if rest_silence_applied and _safe_float(user.get("user_rest_until"), 0) > received_ts:
            if self._user_rest_signal_should_block_current_reply(safe_text or text):
                rest_silence_early_block = bool(is_target_user)
                rest_silence_early_text = safe_text or text
        self._apply_private_image_vision_negative_feedback(user, safe_text or text)
        reaction_expression_feedback = self._apply_reaction_expression_feedback(
            user,
            safe_text or text,
            scope_key=self._reaction_expression_scope_key(event, user_id),
        )
        if reaction_expression_feedback:
            self._log_reaction_expression_event(
                event,
                stage="feedback",
                decision="recorded",
                reason="feedback_private",
                scope="private",
                image_id=reaction_expression_feedback.get("image_id"),
                feedback_signal=reaction_expression_feedback.get("signal"),
                feedback_score=reaction_expression_feedback.get("score"),
            )
        expression_feedback = self._apply_expression_rule_feedback(
            user,
            safe_text or text,
            channel="private",
        )
        if expression_feedback:
            logger.info(
                "表达规则收到用户反馈: user=%s signal=%s updated=%s demoted=%s",
                user_id,
                _single_line(expression_feedback.get("signal"), 16),
                _safe_int(expression_feedback.get("updated_rules"), 0, 0),
                _safe_int(expression_feedback.get("demoted_rules"), 0, 0),
            )
        private_memory_write_allowed = self._req041_private_memory_write_allowed(user)
        private_memory_managed = self._req041_private_memory_managed()
        private_memory_revision = (
            self._req041_prepare_authoritative_private_memory(user)
            if private_memory_write_allowed and private_memory_managed else None
        )
        if private_memory_write_allowed and private_memory_managed and private_memory_revision is None:
            private_memory_write_allowed = False
        if private_memory_write_allowed and is_target_user:
            observer = getattr(self, "_agenda_observe_calendar_message", None)
            if callable(observer):
                try:
                    calendar_observation_result = observer(
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
                        "日历候选观察失败，已放行当前回复: user=%s error=%s",
                        user_id,
                        _single_line(exc, 160),
                    )
        if private_memory_write_allowed:
            user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
        if self._expression_private_learning_source_enabled(user, user_id):
            self._update_expression_profile_from_message(user, safe_text or text)
            self._refresh_expression_voice_profile()
        if private_memory_write_allowed:
            self._update_companion_memory_from_message(user, safe_text or text)
            self._update_open_loops_from_message(user, safe_text or text)
            self._update_action_preferences_from_message(user, safe_text or text)
            self._update_user_behavior_habits_from_message(user, safe_text or text)
            if private_memory_managed:
                self._req041_commit_authoritative_private_memory(
                    user,
                    expected_revision=private_memory_revision,
                    operation_id="req041-private-message:" + (
                        self._event_message_id(event) or uuid.uuid4().hex
                    ),
                )
        if (
            not rest_silence_early_block
            and (
                _persona_value(self, 'enable_intent_emotion_analysis', True)
                or _persona_value(self, 'enable_relationship_state_machine', True)
                or _persona_value(self, 'enable_emotion_simulation', True)
            )
        ):
            intent_profile = self._analyze_inbound_intent(text)
            boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
            if callable(boundary_enricher):
                intent_profile = boundary_enricher(user, intent_profile)
            violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
            if callable(violation_settler):
                violation_settler(
                    user,
                    intent_profile,
                    event_id=self._event_message_id(event),
                    now=received_ts,
                )
            if _persona_value(self, 'enable_intent_emotion_analysis', True):
                user["intent_profile"] = intent_profile
            if self._should_use_llm_emotion_judgement(text, intent_profile):
                # Model review does not block the current passive reply; it keeps using cached emotion state.
                observed_event = self._record_interaction_emotion_event(
                    user,
                    intent_profile,
                    band=str(current_interaction_projection(
                        user.get("current_interaction"),
                        relationship_role=self._private_user_role(user, user_id),
                        relationship_mode=user.get("relationship_mode", "normal"),
                        now=_now_ts(),
                    ).get("expression_band") or "relaxed"),
                    reason_code="target_review_pending",
                    status="observed",
                )
                emotion_review_id = uuid.uuid4().hex
                user["pending_emotion_judgement"] = {
                    "review_id": emotion_review_id,
                    "message_event_id": self._event_message_id(event),
                    "text": _single_line(text, 240),
                    "created_at": _now_ts(),
                    "local": deepcopy(intent_profile),
                    "observed_event": observed_event or {},
                }
                self._create_lifecycle_background_task(
                    self._refine_inbound_emotion_with_model(
                        user_id,
                        text,
                        deepcopy(intent_profile),
                        review_id=emotion_review_id,
                    ),
                    label="inbound_emotion_refine",
                )
            else:
                self._update_relationship_state_from_intent(user, intent_profile)
        if is_target_user and self._cancel_inbound_conflicting_greeting(
            user,
            now=_now_ts(),
            user_id=user_id,
            trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
        ):
            logger.info("用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
            if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(user, now=_now_ts())
    user_is_owner = self._private_user_role(user, user_id) == "owner"
    food_feedback = self._detect_food_feedback(text) if text else {"is_food": False}
    food_feedback_detected = bool(text) and user_is_owner and bool(
        food_feedback.get("is_food") and food_feedback.get("actionable")
    )
    food_feedback_applied = food_feedback_detected and self._apply_food_feedback_to_state(text)
    used_food_items: list[str] = []
    meal_care_result: dict[str, Any] = {}
    food_feedback_actionable = bool(
        food_feedback.get("actionable")
        or food_feedback.get("feeding")
        or food_feedback.get("bot_directed")
    )
    if user_is_owner and food_feedback_actionable:
        user["last_food_feedback_at"] = _now_ts()
        user["last_food_feedback_text"] = _single_line(text, 120)
    active_meal_care = bool(self._meal_care_active_context(user, now=received_ts)) if user_is_owner else False
    if food_feedback.get("is_food") and not active_meal_care:
        used_food_items = self._mark_food_menu_item_used_from_text(text) if user_is_owner else []
        if used_food_items:
            user["last_food_menu_choice"] = {
                "ts": _now_ts(),
                "items": used_food_items,
                "text": _single_line(text, 120),
            }
    if user_is_owner and text:
        meal_care_result = self._handle_meal_care_inbound(user, safe_text or text, now=received_ts)
        if meal_care_result.get("foods"):
            user["last_food_menu_choice"] = {
                "ts": _now_ts(),
                "items": list(meal_care_result.get("foods") or []),
                "text": _single_line(text, 120),
                "source": "meal_care_reply",
            }
    care_feedback = self._detect_care_feedback(text) if text else {"is_care": False}
    care_feedback_detected = bool(text) and user_is_owner and bool(care_feedback.get("is_care"))
    care_feedback_applied = care_feedback_detected and self._apply_care_feedback_to_state(text)
    if care_feedback_applied:
        self._apply_relationship_event(
            user,
            2,
            reason_code="care_feedback",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
    interaction_warmth_applied = (
        bool(_persona_value(self, 'enable_custom_relationship_stage_policy', False))
        and bool(text)
        and is_target_user
        and user_is_owner
        and self._apply_interaction_warmth_to_state(text, user)
    )
    if interaction_warmth_applied:
        self._apply_relationship_event(
            user,
            1,
            reason_code="interaction_warmth",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
    schedule_adjustment_applied = (
        bool(text)
        and is_target_user
        and user_is_owner
        and self._record_schedule_adjustment_from_interaction(text, user)
    )
    if schedule_adjustment_applied:
        self._apply_relationship_event(
            user,
            1,
            reason_code="schedule_adjustment",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
    if food_feedback_applied:
        self._apply_relationship_event(
            user,
            1,
            reason_code="food_feedback",
            event_id=self._event_message_id(event),
            now=received_ts,
        )

    response = ""
    if is_target_user:
        try:
            read_view_getter = getattr(self, "_req041_relationship_read_view", None)
            relationship_read_user = (
                read_view_getter(event, user, kind="private")
                if callable(read_view_getter) else user
            )
            scoped_read_getter = getattr(self, "_req041_scoped_private_read_view", None)
            if callable(scoped_read_getter):
                relationship_read_user = scoped_read_getter(event, relationship_read_user)
            self._memory_companion_attach_private_context(
                event,
                user_id=user_id,
                user=relationship_read_user,
                text=(safe_text if text else "") or text,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "私聊记忆上下文挂载缺少可选模型依赖，已跳过记忆增强: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
    return _StageNext((calendar_observation_result, care_feedback_detected, event, expression_feedback, food_feedback_applied, food_feedback_detected, interaction_warmth_applied, is_target_user, meal_care_result, private_memory_managed, private_memory_revision, response, rest_silence_early_block, rest_silence_early_text, schedule_adjustment_applied, smart_debounce_state_changed, text, used_food_items, user, user_id))
