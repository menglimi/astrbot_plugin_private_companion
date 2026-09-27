# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Part04Mixin。

由 tmp/refactor/lta2_split.py 从 llm_tool_actions_photo_generate_part02.py 的 _pc_generate_photo_impl 段级拆分而来（generate）。
段体与拆分前逐字节相同；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from .helpers import (
    _now_ts,
    _path_text,
    _single_line,
)
from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .llm_tool_actions_shared import logger
from .photo_nai_params import (
    merge_user_photo_nai_params,
    recent_cached_photo_nai_params,
)


class LlmToolActionsPhotoGeneratePart02Part04Mixin:
    """_pc_generate_photo_impl 的 generate 段。"""

    async def _pc_generate_photo_impl_generate(
        self,
        content,
        event,
        image_size,
        inbound_photo_text,
        inherited_nai_params,
        intent_kind,
        kwargs,
        legacy_generator,
        photo_scope,
        proactive_request,
        public_receipt,
        reference_path,
        request_scope,
        requester,
        requester_id,
        resolved_reference_paths,
        scene_preset,
        send_image,
        structured_generator,
        tool_started_at,
        visible_caption,
        workflow_kind,
    ):
        """_pc_generate_photo_impl 段：提示词组装、超时预算与生图调用。"""
        prompt_format_mode = ""
        prompt_format_getter = getattr(self, "_photo_generation_prompt_format_mode", None)
        if callable(prompt_format_getter):
            try:
                prompt_format_mode = (
                    _single_line(prompt_format_getter(), 40).lower() or "traditional"
                )
            except Exception as exc:
                logger.debug(
                    "tool 生图读取提示词格式失败，保留原始提示词: %s",
                    _single_line(exc, 160),
                )
                prompt_format_mode = "traditional"
        if (
            workflow_kind != "edit"
            and not proactive_request
            and request_scope == "private"
            and not inbound_photo_text.strip()
            and not inherited_nai_params
        ):
            inherited_nai_params = recent_cached_photo_nai_params(
                requester,
                now=_now_ts(),
            )
        content = merge_user_photo_nai_params(
            content,
            inherited_nai_params,
            prompt_format=prompt_format_mode,
        )
        prompt_builder = getattr(self, "_build_natural_language_photo_prompt_sections", None)
        use_natural_prompt_builder = not callable(prompt_format_getter) or prompt_format_mode in {
            "natural_language",
            "natural",
            "prose",
            "description",
            "自然语言",
            "自然语言描述",
        }
        if callable(prompt_builder) and use_natural_prompt_builder:
            prompt_sections = prompt_builder(
                prompt=content,
                kind="selfie" if intent_kind == "sticker" else intent_kind,
                has_reference=bool(resolved_reference_paths),
                memory_context="",
            )
            prompt_text = content
        else:
            prompt_sections = None
            prompt_text = content
        preset_text = _single_line(scene_preset or kwargs.get("preset") or kwargs.get("scene"), 80)
        workflow_default_preset = "表情包场景" if intent_kind == "sticker" else ""

        event_umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        session_key = event_umo or "tool_photo"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(event_umo, requester_id)
            if callable(continuity_composer)
            else ""
        )
        generation_session_key = f"tool_photo_{session_key}"
        outer_timeout = self._photo_tool_call_timeout_seconds()
        timeout_margin = max(2.0, min(8.0, outer_timeout * 0.1))
        generation_timeout = outer_timeout - (time.monotonic() - tool_started_at) - timeout_margin
        if generation_timeout <= 0:
            generation_timeout = 0.01
        generation_kwargs = {
            "event": event,
            "workflow_kind": workflow_kind,
            "prompt_text": prompt_text,
            "request_text": content,
            "session_key": generation_session_key,
            "continuity_key": continuity_key,
            "requester_user_id": requester_id,
            "requester_is_private": bool(
                (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
            ),
            "reference_image_path": reference_path,
            "reference_image_paths": list(resolved_reference_paths),
            "image_size": _single_line(image_size or kwargs.get("size"), 40),
            "requested_scene_preset": preset_text,
            "suggested_scene_preset": preset_text,
            "workflow_default_scene_preset": workflow_default_preset,
            "prompt_sections": prompt_sections,
        }
        if callable(prompt_format_getter):
            generation_kwargs["prompt_format"] = prompt_format_mode
        try:
            generation_output = await asyncio.wait_for(
                structured_generator(**generation_kwargs)
                if callable(structured_generator)
                else legacy_generator(**generation_kwargs),
                timeout=generation_timeout,
            )
        except asyncio.TimeoutError:
            actual_error = (
                f"生图未能在 AstrBot 工具调用时限 {outer_timeout:g} 秒内完成；"
                "本次工具调用没有生成或发送图片。"
            )
            logger.warning(
                "pc_generate_photo 在外层工具超时前主动结束: session=%s timeout=%.1fs budget=%.1fs",
                session_key,
                outer_timeout,
                generation_timeout,
            )
            await self._note_photo_tool_quota_attempt(
                event,
                requester_id=requester_id,
                requester=requester if isinstance(requester, dict) else None,
                photo_scope=photo_scope,
                image_path="",
            )
            return public_receipt(
                {
                    "status": "timeout",
                    "success": False,
                    "generated": False,
                    "send_requested": send_image,
                    "sent": False,
                    "message": actual_error,
                    "actual_error": actual_error,
                    "actionable_hint": "请如实告诉用户本次没有出图、没有发送；不要声称已经发出。可稍后重试，或让管理员提高 AstrBot tool_call_timeout/缩短生图后端超时。",
                    "must_not_claim_sent": True,
                    "retryable": True,
                },
                ensure_ascii=False,
            )
        generation_metadata: dict[str, Any] = {}
        if hasattr(generation_output, "as_legacy_tuple"):
            backend_name, image_path, note = generation_output.as_legacy_tuple()
            generation_metadata = {
                "trace_id": _single_line(getattr(generation_output, "trace_id", ""), 80),
                "reference_used": bool(getattr(generation_output, "reference_used", False)),
                "reference_path": _path_text(getattr(generation_output, "reference_selected_path", ""), 1000),
                "reference_id": _single_line(getattr(generation_output, "reference_id", ""), 60),
                "reference_kind": _single_line(getattr(generation_output, "reference_kind", ""), 40),
                "reference_roles": list(getattr(generation_output, "reference_roles", ()) or ()),
                "wardrobe_mode": _single_line(getattr(generation_output, "wardrobe_mode", ""), 40),
                "wardrobe_category": _single_line(getattr(generation_output, "wardrobe_category", ""), 40),
                "outfit_locked": bool(getattr(generation_output, "outfit_locked", False)),
                "daily_outfit_removed": bool(getattr(generation_output, "daily_outfit_removed", False)),
                "preset_names": list(getattr(generation_output, "preset_names", ()) or ()),
                "preset_hint": _single_line(getattr(generation_output, "preset_hint", ""), 80),
                "preset_source": _single_line(getattr(generation_output, "preset_source", ""), 40),
                "suggestion_status": _single_line(getattr(generation_output, "suggestion_status", ""), 60),
                "prompt_hash": _single_line(getattr(generation_output, "prompt_hash", ""), 80),
                "prompt_path": _single_line(getattr(generation_output, "prompt_path", ""), 1000),
                "reference_requested_roles": list(getattr(generation_output, "reference_requested_roles", ()) or ()),
                "reference_excluded_roles": list(getattr(generation_output, "reference_excluded_roles", ()) or ()),
                "continuity_mode": _single_line(getattr(generation_output, "continuity_mode", ""), 30),
                "reference_confidence": getattr(generation_output, "reference_confidence", 0.0),
                "reference_plan": list(getattr(generation_output, "reference_plan", ()) or ()),
                "reference_fulfilled_roles": list(getattr(generation_output, "reference_fulfilled_roles", ()) or ()),
                "reference_missing_roles": list(getattr(generation_output, "reference_missing_roles", ()) or ()),
                "reference_fallback_message": _single_line(getattr(generation_output, "reference_fallback_message", ""), 260),
                # A provider may have accepted and generated the image while
                # its result URL could not be materialized locally. Keep that
                # state separate from ``generated`` (which means a usable
                # local file) so the reply model receives an accurate receipt.
                "generation_completed": bool(getattr(generation_output, "generation_completed", False)),
                "failure_stage": _single_line(getattr(generation_output, "failure_stage", ""), 40),
            }
        else:
            backend_name, image_path, note = generation_output
            metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
            if callable(metadata_getter):
                generation_metadata = metadata_getter(
                    image_path=image_path,
                    session_key=generation_session_key,
                ) or {}
        generation_completed = bool(generation_metadata.get("generation_completed"))
        failure_stage = _single_line(generation_metadata.get("failure_stage"), 40)
        reference_usage_known = "reference_used" in generation_metadata
        actual_reference_path = _path_text(
            generation_metadata.get("reference_path") or reference_path,
            1000,
        )
        used_reference = bool(generation_metadata.get("reference_used"))
        final_presets = [
            _single_line(value, 60)
            for value in (
                generation_metadata.get("preset_names")
                or generation_metadata.get("presets")
                or []
            )
            if _single_line(value, 60)
        ][:1]
        final_scene_preset = final_presets[0] if final_presets else ""
        ok = bool(image_path and os.path.exists(image_path))
        return _StageNext((actual_reference_path, backend_name, content, failure_stage, final_presets, final_scene_preset, generation_completed, generation_metadata, generation_session_key, image_path, note, ok, preset_text, reference_usage_known, session_key, used_reference))
