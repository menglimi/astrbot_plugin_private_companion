# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Part05Mixin。

由 tmp/refactor/lta2_split.py 从 llm_tool_actions_photo_generate_part02.py 的 _pc_generate_photo_impl 段级拆分而来（delivery）。
段体与拆分前逐字节相同；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

from typing import Any

from .helpers import _single_line
from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .llm_tool_actions_shared import logger


class LlmToolActionsPhotoGeneratePart02Part05Mixin:
    """_pc_generate_photo_impl 的 delivery 段。"""

    async def _pc_generate_photo_impl_delivery(
        self,
        actual_reference_path,
        backend_name,
        content,
        event,
        failure_stage,
        final_presets,
        final_scene_preset,
        generation_completed,
        generation_metadata,
        generation_session_key,
        image_path,
        intent_kind,
        note,
        ok,
        photo_scope,
        preset_text,
        public_receipt,
        reference_usage_known,
        requester,
        requester_id,
        resolved_reference_paths,
        send_image,
        session_key,
        used_reference,
        visible_caption,
        workflow_kind,
    ):
        """_pc_generate_photo_impl 段：计费尝试、投递与记忆记录。"""
        tool_delivery_confirmed = ";tool_delivery_confirmed" in str(note or "")
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="llm_tool",
                intent_kind=intent_kind,
                sent=False,
                caption=visible_caption,
                preset_hint=preset_text,
                tool_name="pc_generate_photo",
            )
        billable_attempt = bool(ok or generation_completed)
        failure_counter = getattr(self, "_photo_generation_failure_counts_as_attempt", None)
        if not billable_attempt and callable(failure_counter):
            billable_attempt = bool(failure_counter(note))
        if billable_attempt:
            await self._note_photo_tool_quota_attempt(
                event,
                requester_id=requester_id,
                requester=requester if isinstance(requester, dict) else None,
                photo_scope=photo_scope,
                image_path=image_path if ok else "",
            )
        sent = False
        delivery_deferred = False
        delivery: dict[str, Any] = {}
        generation_trace_id = _single_line(generation_metadata.get("trace_id"), 80)
        if ok and send_image and tool_delivery_confirmed:
            sent = True
            delivery = {
                "sent": True,
                "destination": "custom_tool",
                "message": "自定义生图工具已完成图片投递",
                "external": True,
            }
        elif ok and send_image:
            # 图片本身就是成功结果。纯状态 caption 不应成为可见回执；
            # 只有包含实际语境信息的自然正文才随图发送。
            usable_caption = "" if self._photo_caption_is_generic(visible_caption) else visible_caption
            message = usable_caption
            fallback_message = _single_line(
                generation_metadata.get("reference_fallback_message"),
                260,
            )
            if fallback_message:
                message = f"{message}\n{fallback_message}".strip()
            trace_writer = getattr(self, "_append_photo_generation_trace_event_async", None)
            if callable(trace_writer):
                await trace_writer(
                    generation_trace_id,
                    "delivery_started",
                    data={"caption": message, "image_path": image_path},
                )
            delivery_deferred = bool(
                getattr(event, "private_companion_proactive_framework", False)
            )
            if delivery_deferred:
                delivery = {
                    "sent": False,
                    "destination": "proactive_framework",
                    "message": "图片已生成，等待主动消息发送链统一投递",
                    "deferred": True,
                }
                try:
                    setattr(event, "_private_companion_photo_tool_deferred", True)
                    setattr(event, "_private_companion_photo_tool_deferred_path", image_path)
                    setattr(event, "_private_companion_photo_tool_deferred_caption", message)
                    setattr(event, "_private_companion_photo_tool_deferred_intent_kind", intent_kind)
                except Exception:
                    pass
                logger.info(
                    "pc_generate_photo 成图已交由主动发送链统一投递: session=%s kind=%s",
                    session_key,
                    intent_kind,
                )
            else:
                try:
                    delivery = await self._deliver_generated_image_to_event(
                        event,
                        image_path=image_path,
                        caption=message,
                    )
                except Exception as exc:
                    delivery = {
                        "sent": False,
                        "destination": "error",
                        "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
                    }
                    logger.warning(
                        "pc_generate_photo 图片投递异常: session=%s err=%s",
                        session_key,
                        _single_line(exc, 180),
                    )
            sent = bool(delivery.get("sent"))
            if callable(trace_writer):
                trace_writer(
                    generation_trace_id,
                    "delivery_deferred"
                    if delivery_deferred
                    else "delivery_completed"
                    if sent
                    else "delivery_failed",
                    status="ok" if sent or delivery_deferred else "error",
                    data={
                        "sent": sent,
                        "deferred": delivery_deferred,
                        "destination": delivery.get("destination"),
                        "message": delivery.get("message"),
                        "review_label": delivery.get("review_label"),
                    },
                )
            if sent:
                try:
                    setattr(event, "_private_companion_photo_tool_sent", True)
                    setattr(event, "_private_companion_photo_tool_sent_caption", message)
                except Exception:
                    pass
        return _StageNext((annotator, delivery, delivery_deferred, sent))
