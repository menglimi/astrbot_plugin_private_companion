# -*- coding: utf-8 -*-
"""handle_private_message 阶段 6（lock_commit / tail）。

由 tmp/refactor/mpp2_split.py 从 message_pipeline_part02.py 的 handle_private_message 段级拆分而来。
本模块无类：阶段为模块级 async 函数，显式接收 self。
阶段体与拆分前逐字节相同（缩进归一）；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
"""
from __future__ import annotations

from .helpers import (
    _safe_int,
    _single_line,
)
from .message_pipeline_part02_shared import _StageNext
from .message_pipeline_shared import logger


async def _handle_private_message_lock_commit(
    self,
    calendar_observation_result,
    care_feedback_detected,
    event,
    expression_feedback,
    food_feedback_applied,
    food_feedback_detected,
    interaction_warmth_applied,
    is_target_user,
    meal_care_result,
    private_memory_managed,
    private_memory_revision,
    response,
    rest_silence_early_block,
    rest_silence_early_text,
    schedule_adjustment_applied,
    smart_debounce_state_changed,
    text,
    used_food_items,
    user,
    user_id,
):
    """handle_private_message 段：save_sections 汇总与用户快照。"""
    save_sections = {"users"}
    if private_memory_managed and private_memory_revision is not None:
        save_sections.add("_req041_private_memory")
    save_sections.update(calendar_observation_result.get("changed_sections") or ())
    if expression_feedback:
        save_sections.update(expression_feedback.get("updated_sections") or ())
        if _safe_int(expression_feedback.get("updated_rules"), 0, 0) > 0:
            save_sections.add("expression_voice_profile")
    if self._expression_private_learning_source_enabled(user, user_id):
        save_sections.add("expression_voice_profile")
    if smart_debounce_state_changed:
        save_sections.add("smart_message_debounce")
    if food_feedback_detected:
        save_sections.update(
            {
                "last_food_state_feedback_at",
                "last_food_state_feedback_text",
            }
        )
    if food_feedback_applied:
        save_sections.update(
            {
                "state_conditions",
                "daily_state",
            }
        )
    if care_feedback_detected:
        save_sections.add("state_conditions")
    if interaction_warmth_applied:
        save_sections.update({"state_conditions", "daily_state"})
    if used_food_items:
        save_sections.add("food_menu")
    if meal_care_result.get("foods"):
        save_sections.add("food_menu")
    if schedule_adjustment_applied:
        save_sections.update(
            {
                "schedule_adjustments",
                "dialogue_outfit_override",
                "detail_enhanced_segments",
                "daily_plan",
                "daily_story_plan",
                "daily_state",
            }
        )
        if isinstance(self.data.get("daily_state"), dict) and isinstance(
            self.data["daily_state"].get("sleep_runtime"), dict
        ):
            save_sections.add("daily_state")
    self._schedule_data_save(sections=save_sections)
    user_snapshot = dict(user)
    return _StageNext((event, is_target_user, response, rest_silence_early_block, rest_silence_early_text, schedule_adjustment_applied, text, user_id, user_snapshot))

async def _handle_private_message_tail(
    self,
    event,
    is_target_user,
    response,
    rest_silence_early_block,
    rest_silence_early_text,
    schedule_adjustment_applied,
    text,
    user_id,
    user_snapshot,
):
    """handle_private_message 段：尾部收口：放行判定、回复、后台刷新任务。"""
    if not is_target_user:
        logger.info(
            "非目标/未启用私聊放行默认主链: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        return
    if is_target_user and rest_silence_early_block:
        self._stop_private_reply_after_user_rest_signal(event, user_id, rest_silence_early_text or text)
        return
    if is_target_user and schedule_adjustment_applied:
        self._create_lifecycle_background_task(
            self._kick_proactive_loop_once(),
            label="kick_proactive_loop_inbound",
        )
    if response:
        await self._reply(event, response)
        event.stop_event()
    elif is_target_user:
        pass
    if is_target_user:
        self._create_lifecycle_background_task(
            self._maybe_refresh_companion_memory(user_id, user_snapshot),
            label="refresh_companion_memory_inbound",
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_dialogue_episode(user_id, user_snapshot),
            label="refresh_dialogue_episode_inbound",
        )
    return _StageNext(())
