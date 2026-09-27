# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Mixin。

由 tmp/refactor/lta2_split.py 对 LlmToolActionsPhotoGeneratePart02Mixin._pc_generate_photo_impl 做段级拆分：
  - seg1 gates: 入口闸门：运行时就绪、主动/用户请求授权（方法体 72 行）
  - seg2 classify: 开关与形态判定：模式、生图后端、workflow/intent 归类（方法体 82 行）
  - seg3 identity: 请求者识别、私聊/群聊授权与额度闸门（方法体 239 行）
  - seg4 references: 参考图收集、合影授权、稳定化与改图/自拍补图（方法体 291 行）
  - seg5 generate: 提示词组装、超时预算与生图调用（方法体 189 行）
  - seg6 delivery: 计费尝试、投递与记忆记录（方法体 119 行）
  - seg7 payload: 结果回执组装与失败分支收口（方法体 234 行）
专属阶段方法已下沉到 llm_tool_actions_photo_generate_part02_partNN.py，宿主只保留顺序编排壳。
阶段方法零改动：段体与拆分前逐字节相同，仅段末追加一条 `return _StageNext(...)` 交还活跃局部名；
阶段里原本的 return 保持原样，宿主收到非 _StageNext 的返回值即原样 return。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from typing import Any

from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .llm_tool_actions_photo_generate_part02_part01 import LlmToolActionsPhotoGeneratePart02Part01Mixin
from .llm_tool_actions_photo_generate_part02_part02 import LlmToolActionsPhotoGeneratePart02Part02Mixin
from .llm_tool_actions_photo_generate_part02_part03 import LlmToolActionsPhotoGeneratePart02Part03Mixin
from .llm_tool_actions_photo_generate_part02_part04 import LlmToolActionsPhotoGeneratePart02Part04Mixin
from .llm_tool_actions_photo_generate_part02_part05 import LlmToolActionsPhotoGeneratePart02Part05Mixin
from .llm_tool_actions_photo_generate_part02_part06 import LlmToolActionsPhotoGeneratePart02Part06Mixin


class LlmToolActionsPhotoGeneratePart02Mixin(
    LlmToolActionsPhotoGeneratePart02Part01Mixin,
    LlmToolActionsPhotoGeneratePart02Part02Mixin,
    LlmToolActionsPhotoGeneratePart02Part03Mixin,
    LlmToolActionsPhotoGeneratePart02Part04Mixin,
    LlmToolActionsPhotoGeneratePart02Part05Mixin,
    LlmToolActionsPhotoGeneratePart02Part06Mixin,
):
    """_pc_generate_photo_impl 的顺序编排器，阶段实现分散在各 part mixin。"""


    async def _pc_generate_photo_impl(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        kind: str = "text2img",
        reference_image_path: str = "",
        reference_image_paths: Any = None,
        image_size: str = "",
        send: bool = True,
        caption: str = "",
        scene_preset: str = "",
        **kwargs,
    ) -> str:
        _stage = await self._pc_generate_photo_impl_gates(caption, event, image_size, kind, kwargs, prompt, reference_image_path, reference_image_paths, scene_preset, send)
        if not isinstance(_stage, _StageNext):
            return _stage
        proactive_request, public_receipt, tool_started_at = _stage

        _stage = await self._pc_generate_photo_impl_classify(caption, event, image_size, kind, kwargs, proactive_request, prompt, public_receipt, reference_image_path, reference_image_paths, scene_preset, send, tool_started_at)
        if not isinstance(_stage, _StageNext):
            return _stage
        compact_prompt, content, group_photo_requested, inbound_photo_text, inherited_nai_params, intent_kind, legacy_generator, scope_checker, structured_generator, visible_caption, workflow_kind = _stage

        _stage = await self._pc_generate_photo_impl_identity(compact_prompt, content, event, group_photo_requested, image_size, inbound_photo_text, inherited_nai_params, intent_kind, kwargs, legacy_generator, proactive_request, public_receipt, reference_image_path, reference_image_paths, scene_preset, scope_checker, send, structured_generator, tool_started_at, visible_caption, workflow_kind)
        if not isinstance(_stage, _StageNext):
            return _stage
        photo_scope, request_scope, requester, requester_id = _stage

        _stage = await self._pc_generate_photo_impl_references(compact_prompt, content, event, group_photo_requested, image_size, inbound_photo_text, inherited_nai_params, intent_kind, kwargs, legacy_generator, photo_scope, proactive_request, public_receipt, reference_image_path, reference_image_paths, request_scope, requester, requester_id, scene_preset, send, structured_generator, tool_started_at, visible_caption, workflow_kind)
        if not isinstance(_stage, _StageNext):
            return _stage
        reference_path, resolved_reference_paths, send_image = _stage

        _stage = await self._pc_generate_photo_impl_generate(content, event, image_size, inbound_photo_text, inherited_nai_params, intent_kind, kwargs, legacy_generator, photo_scope, proactive_request, public_receipt, reference_path, request_scope, requester, requester_id, resolved_reference_paths, scene_preset, send_image, structured_generator, tool_started_at, visible_caption, workflow_kind)
        if not isinstance(_stage, _StageNext):
            return _stage
        actual_reference_path, backend_name, content, failure_stage, final_presets, final_scene_preset, generation_completed, generation_metadata, generation_session_key, image_path, note, ok, preset_text, reference_usage_known, session_key, used_reference = _stage

        _stage = await self._pc_generate_photo_impl_delivery(actual_reference_path, backend_name, content, event, failure_stage, final_presets, final_scene_preset, generation_completed, generation_metadata, generation_session_key, image_path, intent_kind, note, ok, photo_scope, preset_text, public_receipt, reference_usage_known, requester, requester_id, resolved_reference_paths, send_image, session_key, used_reference, visible_caption, workflow_kind)
        if not isinstance(_stage, _StageNext):
            return _stage
        annotator, delivery, delivery_deferred, sent = _stage

        _stage = await self._pc_generate_photo_impl_payload(actual_reference_path, annotator, backend_name, content, delivery, delivery_deferred, event, failure_stage, final_presets, final_scene_preset, generation_completed, generation_metadata, generation_session_key, image_path, intent_kind, note, ok, preset_text, public_receipt, reference_usage_known, resolved_reference_paths, send_image, sent, used_reference, visible_caption, workflow_kind)
        if not isinstance(_stage, _StageNext):
            return _stage
