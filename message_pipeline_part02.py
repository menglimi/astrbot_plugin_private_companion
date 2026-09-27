# -*- coding: utf-8 -*-
"""handle_private_message 的顺序编排壳。

由 tmp/refactor/mpp2_split.py 对 handle_private_message 做段级拆分：
  - seg1 ingress: 入站判定与最小用户档案建立（含小 data_lock 块）（38 行）
  - seg2 quickexit: 早已回复 / 非目标 / 自然语言生图 / 主动专属 / 转发 / 空事件闸门（182 行）
  - seg3 fastlane: 轻量私聊快路径：去重、状态结算、记忆桥与提前放行（206 行）
  - seg4 locked_head: 锁内用户解析、去重判定与图文增强前置（46 行）
  - seg5 lock_buffer: 图文混合上下文、转发缓冲、单图防抖与文本收口（253 行）
  - seg6 lock_state: 用户状态结算与安全文本派生（65 行）
  - seg7 lock_memory: 记忆/表达/日历观察、食物照护反馈与记忆上下文挂载（264 行）
  - seg8 lock_commit: save_sections 汇总与用户快照（51 行）
  - seg9 tail: 尾部收口：放行判定、回复、后台刷新任务（29 行）
专属阶段函数已下沉到 message_pipeline_part02_partNN.py，宿主只保留顺序编排壳。
handle_private_message 仍是本模块顶层 AsyncFunctionDef，装饰器与签名逐字节不变。
阶段函数零改动：段体与拆分前逐字节相同（缩进归一），仅段末追加一条
`return _StageNext(...)` 交还活跃局部名；阶段里原本的 return 保持原样，
宿主收到非 _StageNext 的返回值即原样 return。
原先那条 680 行的 `async with self._data_lock:` 头留在宿主，内层语句按段拆分；
锁覆盖范围与拆分前完全一致。
"""
from __future__ import annotations

from typing import Any

from .message_pipeline_part01 import _coalesce_event_data_saves
from .message_pipeline_part02_shared import _StageNext

from .message_pipeline_part02_part01 import (
    _handle_private_message_ingress,
    _handle_private_message_quickexit,
)

from .message_pipeline_part02_part02 import (
    _handle_private_message_fastlane,
    _handle_private_message_locked_head,
)

from .message_pipeline_part02_part03 import (
    _handle_private_message_lock_buffer,
)

from .message_pipeline_part02_part04 import (
    _handle_private_message_lock_state,
)

from .message_pipeline_part02_part05 import (
    _handle_private_message_lock_memory,
)

from .message_pipeline_part02_part06 import (
    _handle_private_message_lock_commit,
    _handle_private_message_tail,
)


@_coalesce_event_data_saves
async def handle_private_message(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
    """记录私聊互动、图片防抖、用户画像和主动陪伴反馈。"""
    _stage = await _handle_private_message_ingress(self, event)
    if not isinstance(_stage, _StageNext):
        return _stage
    auto_profile_created, calendar_observation_result, event, received_ts, sender_display_name, text, user_id = _stage

    _stage = await _handle_private_message_quickexit(self, auto_profile_created, calendar_observation_result, event, received_ts, sender_display_name, text, user_id)
    if not isinstance(_stage, _StageNext):
        return _stage
    calendar_observation_result, event, forward_only_prompt, received_ts, reference_media_with_text, sender_display_name, text, user_id = _stage

    _stage = await _handle_private_message_fastlane(self, calendar_observation_result, event, forward_only_prompt, received_ts, reference_media_with_text, sender_display_name, text, user_id)
    if not isinstance(_stage, _StageNext):
        return _stage
    calendar_observation_result, event, forward_only_prompt, received_ts, reference_media_with_text, rest_silence_early_block, rest_silence_early_text, sender_display_name, text, user_id = _stage

    async with self._data_lock:
        _stage = await _handle_private_message_locked_head(self, calendar_observation_result, event, forward_only_prompt, received_ts, reference_media_with_text, rest_silence_early_block, rest_silence_early_text, sender_display_name, text, user_id)
        if not isinstance(_stage, _StageNext):
            return _stage
        calendar_observation_result, event, forward_only_prompt, is_target_user, private_image_enhancement_enabled, private_image_only, received_ts, reference_media_with_text, rest_silence_early_block, rest_silence_early_text, sender_display_name, smart_debounce_state_changed, text, user, user_id = _stage

        _stage = await _handle_private_message_lock_buffer(self, calendar_observation_result, event, forward_only_prompt, is_target_user, private_image_enhancement_enabled, private_image_only, received_ts, reference_media_with_text, rest_silence_early_block, rest_silence_early_text, sender_display_name, smart_debounce_state_changed, text, user, user_id)
        if not isinstance(_stage, _StageNext):
            return _stage
        calendar_observation_result, event, is_target_user, received_ts, rest_silence_early_block, rest_silence_early_text, sender_display_name, smart_debounce_state_changed, text, user, user_id = _stage

        _stage = await _handle_private_message_lock_state(self, calendar_observation_result, event, is_target_user, received_ts, rest_silence_early_block, rest_silence_early_text, sender_display_name, smart_debounce_state_changed, text, user, user_id)
        if not isinstance(_stage, _StageNext):
            return _stage
        calendar_observation_result, event, expression_feedback, is_target_user, private_memory_managed, private_memory_revision, received_ts, rest_silence_early_block, rest_silence_early_text, smart_debounce_state_changed, text, user, user_id = _stage

        _stage = await _handle_private_message_lock_memory(self, calendar_observation_result, event, expression_feedback, is_target_user, private_memory_managed, private_memory_revision, received_ts, rest_silence_early_block, rest_silence_early_text, smart_debounce_state_changed, text, user, user_id)
        if not isinstance(_stage, _StageNext):
            return _stage
        calendar_observation_result, care_feedback_detected, event, expression_feedback, food_feedback_applied, food_feedback_detected, interaction_warmth_applied, is_target_user, meal_care_result, private_memory_managed, private_memory_revision, response, rest_silence_early_block, rest_silence_early_text, schedule_adjustment_applied, smart_debounce_state_changed, text, used_food_items, user, user_id = _stage

        _stage = await _handle_private_message_lock_commit(self, calendar_observation_result, care_feedback_detected, event, expression_feedback, food_feedback_applied, food_feedback_detected, interaction_warmth_applied, is_target_user, meal_care_result, private_memory_managed, private_memory_revision, response, rest_silence_early_block, rest_silence_early_text, schedule_adjustment_applied, smart_debounce_state_changed, text, used_food_items, user, user_id)
        if not isinstance(_stage, _StageNext):
            return _stage
        event, is_target_user, response, rest_silence_early_block, rest_silence_early_text, schedule_adjustment_applied, text, user_id, user_snapshot = _stage

    _stage = await _handle_private_message_tail(self, event, is_target_user, response, rest_silence_early_block, rest_silence_early_text, schedule_adjustment_applied, text, user_id, user_snapshot)
    if not isinstance(_stage, _StageNext):
        return _stage
