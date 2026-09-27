# -*- coding: utf-8 -*-
"""DailyStateTickTickCoreMixin。

由 tmp/refactor/dstc_split.py 段级拆分：_tick_user 的 8 个阶段方法已下沉到
daily_state_tick_tick_core_part01..part04.py，宿主只保留顺序编排。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTickMixin）。
"""
from __future__ import annotations

from .daily_state_tick_tick_core_part01 import DailyStateTickTickCorePart01Mixin
from .daily_state_tick_tick_core_part02 import DailyStateTickTickCorePart02Mixin
from .daily_state_tick_tick_core_part03 import DailyStateTickTickCorePart03Mixin
from .daily_state_tick_tick_core_part04 import DailyStateTickTickCorePart04Mixin
from .daily_state_tick_tick_core_part05 import DailyStateTickTickCorePart05Mixin
from typing import Any


class DailyStateTickTickCoreMixin(
    DailyStateTickTickCorePart01Mixin,
    DailyStateTickTickCorePart02Mixin,
    DailyStateTickTickCorePart03Mixin,
    DailyStateTickTickCorePart04Mixin,
    DailyStateTickTickCorePart05Mixin,
):
    """_tick_user 的顺序编排器，阶段实现分散在各 part mixin。"""


    async def _tick_user(self, user_id: str, user: Any) -> None:
        """Process one user while the outer tick keeps sequential ordering."""
        _stage = await self._tick_user_precheck(
            user_id,
            user,
        )
        if _stage is None:
            return
        due_timer_id, expected_model_signature, is_troubleshooting_for_send, user = _stage

        _stage = await self._tick_user_route_lock(
            user_id,
            user,
            due_timer_id,
            expected_model_signature,
            is_troubleshooting_for_send,
        )
        if _stage is None:
            return
        audit_id, planned_delivery_snapshot, route_key_for_send, route_options_for_send, route_settlement_for_send = _stage

        _stage = await self._tick_user_prepare(
            user_id,
            user,
            audit_id,
            due_timer_id,
            is_troubleshooting_for_send,
            planned_delivery_snapshot,
            route_key_for_send,
            route_options_for_send,
            route_settlement_for_send,
        )
        if _stage is None:
            return
        action_summary, creative_share_context_for_send, effective_action_for_send, extra_components, friend_proactive_for_send, image_path, photo_subject_owner_for_send, planned_action_for_send, planned_chain_for_send, planned_followup_kind_for_send, planned_motive_for_send, planned_opener_mode_for_send, planned_topic_for_send, proactive_quote_message_id, reason, render_failure_stage, review_candidate_text, send_umo_for_send, task_start_private_activity_at, task_start_private_inbound_count, text = _stage

        _stage = await self._tick_user_review(
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
        )
        if _stage is None:
            return
        (text,) = _stage

        _stage = await self._tick_user_guard_outbound(
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
        )
        if _stage is None:
            return
        has_new_user_message, text = _stage

        _stage = await self._tick_user_guard_delivery(
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
        )
        if _stage is None:
            return

        _stage = await self._tick_user_send(
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
        )
        if _stage is None:
            return
        action_summary, delivered_has_photo, delivery_complete, delivery_note, delivery_umo, effective_action_for_send, extra_components, image_path, text = _stage

        _stage = await self._tick_user_poststate(
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
        )
        if _stage is None:
            return
        (memory_companion_proactive_payload,) = _stage

        if memory_companion_proactive_payload:
            await self._memory_companion_record_proactive_message(**memory_companion_proactive_payload)
