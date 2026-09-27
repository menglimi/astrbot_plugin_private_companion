# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Part01Mixin。

由 tmp/refactor/lta2_split.py 从 llm_tool_actions_photo_generate_part02.py 的 _pc_generate_photo_impl 段级拆分而来（gates / classify）。
段体与拆分前逐字节相同；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .helpers import (
    _photo_group_request_matches,
    _single_line,
)
from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .persona_config import runtime_persona_setting


class LlmToolActionsPhotoGeneratePart02Part01Mixin:
    """_pc_generate_photo_impl 的 gates / classify 段。"""

    async def _pc_generate_photo_impl_gates(
        self,
        caption,
        event,
        image_size,
        kind,
        kwargs,
        prompt,
        reference_image_path,
        reference_image_paths,
        scene_preset,
        send,
    ):
        """_pc_generate_photo_impl 段：入口闸门：运行时就绪、主动/用户请求授权。"""
        def public_receipt(
            payload: dict[str, Any],
            *,
            ensure_ascii: bool = False,
            known_paths: tuple[Any, ...] = (),
        ) -> str:
            return json.dumps(
                self._sanitize_photo_tool_result_payload(
                    payload,
                    known_paths=known_paths,
                ),
                ensure_ascii=ensure_ascii,
            )

        if not self._photo_generation_runtime_available():
            return public_receipt(
                {
                    "status": "unavailable",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "error_code": "image_extension_unavailable",
                    "message": "生图扩展未安装、未启用或尚未就绪。",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "自然说明当前不能生成图片，不要声称图片已经生成或发送，也不要在本轮重试。",
                },
                ensure_ascii=False,
            )

        tool_started_at = time.monotonic()
        scope_getter = getattr(self, "_photo_generation_scope", None)
        initial_scope = ""
        if callable(scope_getter):
            try:
                initial_scope = _single_line(scope_getter(event), 40).lower()
            except Exception:
                initial_scope = ""
        proactive_request = bool(
            initial_scope == "proactive"
            or getattr(event, "private_companion_proactive_framework", False)
        )
        permission_getter = getattr(
            self,
            "_user_requested_photo_generation_allowed",
            None,
        )
        if callable(permission_getter):
            try:
                user_request_allowed = bool(permission_getter(event))
            except Exception:
                user_request_allowed = False
        else:
            user_request_allowed = bool(
                runtime_persona_setting(
                    self,
                    "enable_user_requested_photo_generation",
                    True,
                )
            )
        if not proactive_request and not user_request_allowed:
            return public_receipt(
                {
                    "status": "disabled",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "message": "管理员已关闭用户请求生图/改图。",
                    "must_not_claim_sent": True,
                    "retryable": False,
                },
                ensure_ascii=False,
            )
        return _StageNext((proactive_request, public_receipt, tool_started_at))

    async def _pc_generate_photo_impl_classify(
        self,
        caption,
        event,
        image_size,
        kind,
        kwargs,
        proactive_request,
        prompt,
        public_receipt,
        reference_image_path,
        reference_image_paths,
        scene_preset,
        send,
        tool_started_at,
    ):
        """_pc_generate_photo_impl 段：开关与形态判定：模式、生图后端、workflow/intent 归类。"""
        mode = _single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', "tool_first"), 40).lower()
        if mode == "off" and not proactive_request:
            return public_receipt({"status": "disabled", "message": "非指令生图/改图已关闭；显式指令仍可使用“陪伴 生图/自拍/改图”。"}, ensure_ascii=False)
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            return public_receipt({"status": "disabled", "message": "主动拍照/生图能力未启用"}, ensure_ascii=False)
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        structured_generator = getattr(self, "_generate_photo_image_result", None)
        legacy_generator = getattr(self, "_generate_photo_image", None)
        if not callable(structured_generator) and not callable(legacy_generator):
            return public_receipt({"status": "disabled", "message": "缺少生图入口 _generate_photo_image"}, ensure_ascii=False)
        if not self._photo_text_available():
            return public_receipt({"status": "unavailable", "message": "当前没有可用生图后端，或已被负载/token 保护临时延后"}, ensure_ascii=False)

        content = _single_line(prompt or kwargs.get("text") or kwargs.get("description") or kwargs.get("prompt_text"), 900)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        raw_kind = _single_line(kind or kwargs.get("workflow_kind") or kwargs.get("type"), 40).lower()
        if raw_kind in {"sticker", "emoji", "meme", "表情包", "贴纸"}:
            workflow_kind = "selfie"
            intent_kind = "sticker"
        elif raw_kind in {"selfie", "portrait", "自拍", "人像", "拍照", "头像", "avatar", "cos", "cosplay", "穿搭"}:
            workflow_kind = "selfie"
            intent_kind = "selfie"
        elif raw_kind in {"edit", "改图", "修图", "重绘", "p图", "P图"}:
            workflow_kind = "edit"
            intent_kind = "edit"
        else:
            workflow_kind = "text2img"
            intent_kind = "text2img"
        inherited_nai_params = ""
        inbound_photo_text = str(getattr(event, "message_str", "") or "")[:4000]
        if workflow_kind != "edit":
            extractor = getattr(self, "_extract_user_photo_nai_params", None)
            if callable(extractor):
                try:
                    inherited_nai_params = extractor(inbound_photo_text)
                except Exception:
                    inherited_nai_params = ""
        if not content:
            return public_receipt(
                {
                    "status": "need_prompt",
                    "message": "缺少 prompt。请把要生成的画面或修改要求传入 prompt。",
                },
                ensure_ascii=False,
            )
        compact_prompt = re.sub(r"\s+", "", content)
        group_photo_requested = _photo_group_request_matches(content)
        bot_name = re.sub(r"\s+", "", _single_line(runtime_persona_setting(self, 'bot_name', ""), 80))
        assistant_in_frame = bool(
            (bot_name and bot_name in compact_prompt)
            or any(
                token in compact_prompt
                for token in (
                    "我本人",
                    "我在画面",
                    "我站在",
                    "我坐在",
                    "我躺在",
                    "我走在",
                    "我的背影",
                    "我的侧脸",
                    "我的全身",
                    "角色本人",
                    "本人出镜",
                )
            )
            or re.search(r"\b(?:the\s+assistant|assistant\s+persona|bot\s+character)\b", content, flags=re.I)
        )
        if intent_kind == "text2img" and any(token in compact_prompt for token in ("表情包", "贴纸", "sticker", "meme")):
            workflow_kind = "selfie"
            intent_kind = "sticker"
        elif intent_kind == "text2img" and (
            self._character_photo_request_matches(content)
            or group_photo_requested
            or assistant_in_frame
            or any(
                token in compact_prompt
                for token in ("自拍", "拍照", "头像", "人像", "角色本人", "本人出镜", "露脸", "穿搭", "镜前", "cos", "COS", "cosplay")
            )
        ):
            workflow_kind = "selfie"
            intent_kind = "selfie"
        return _StageNext((compact_prompt, content, group_photo_requested, inbound_photo_text, inherited_nai_params, intent_kind, legacy_generator, scope_checker, structured_generator, visible_caption, workflow_kind))
