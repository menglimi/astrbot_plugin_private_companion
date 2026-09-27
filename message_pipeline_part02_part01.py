# -*- coding: utf-8 -*-
"""handle_private_message 阶段 1（ingress / quickexit）。

由 tmp/refactor/mpp2_split.py 从 message_pipeline_part02.py 的 handle_private_message 段级拆分而来。
本模块无类：阶段为模块级 async 函数，显式接收 self。
阶段体与拆分前逐字节相同（缩进归一）；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
"""
from __future__ import annotations

from typing import Any

from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _single_line,
)
from .message_pipeline_part02_shared import _StageNext
from .message_pipeline_shared import logger


async def _handle_private_message_ingress(self, event):
    """handle_private_message 段：入站判定与最小用户档案建立（含小 data_lock 块）。"""
    if self is None:
        return
    inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
    if callable(inbound_checker) and not inbound_checker(event):
        logger.debug("非入站聊天事件跳过私聊陪伴链路")
        return
    text = _single_line(event.message_str, 120)
    if self._message_debounce_command_text(event, text):
        return
    received_ts = _now_ts()
    user_id = str(event.get_sender_id())
    self_id = self._event_self_id(event)
    if user_id and self_id and user_id == self_id:
        logger.info("忽略 Bot 自己的私聊回流事件: user=%s", user_id)
        return
    sender_display_name = _single_line(self._sender_display_name(event), 40)
    # Keep optional feedback/observation results defined when the message is
    # empty or exits through a lightweight branch.
    expression_feedback: dict[str, Any] = {}
    calendar_observation_result: dict[str, Any] = {}
    async with self._data_lock:
        private_user, auto_profile_created = self._ensure_auto_private_user_profile(
            event,
            user_id=user_id,
            sender_display_name=sender_display_name,
            now=received_ts,
        )
        if isinstance(private_user, dict):
            user_id = _single_line(private_user.get("user_id"), 160) or user_id
        migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
        if callable(migrator):
            migrator(user_id, private_user)
        self._req036_attach_unified_profile_context(
            event,
            user=private_user if isinstance(private_user, dict) else None,
            source="private_auto",
        )
        self._schedule_data_save(sections={"users", "unified_person"})
    return _StageNext((auto_profile_created, calendar_observation_result, event, received_ts, sender_display_name, text, user_id))

async def _handle_private_message_quickexit(
    self,
    auto_profile_created,
    calendar_observation_result,
    event,
    received_ts,
    sender_display_name,
    text,
    user_id,
):
    """handle_private_message 段：早已回复 / 非目标 / 自然语言生图 / 主动专属 / 转发 / 空事件闸门。"""
    if auto_profile_created:
        logger.info(
            "已建立最小用户档案: user=%s platform=%s",
            _single_line(self._canonical_private_user_id(user_id), 80),
            _single_line(self._platform_kind_for_event(event), 40),
        )
    if self._is_onebot_poke_notice_event(event):
        # 戳一戳会以私聊空文本事件进入 AstrBot；交给专用插件处理。
        logger.debug("私聊戳一戳 notice 交给专用插件")
        return
    self._qzone_note_event_bot(event)
    existing_reply_preview = self._event_existing_reply_result_preview(event)
    if existing_reply_preview:
        preview_user_id = self._canonical_private_user_id(user_id)
        preview_users = self.data.get("users", {})
        preview_user = preview_users.get(preview_user_id) if isinstance(preview_users, dict) else None
        if (
            self._private_passive_profile_available(
                preview_user_id,
                preview_user if isinstance(preview_user, dict) else None,
            )
            and not (isinstance(preview_user, dict) and not bool(preview_user.get("enabled", True)))
        ):
            await self._cancel_activity_followup_on_user_return(
                preview_user_id or user_id,
                trigger_message_id=self._event_message_id(event),
                trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                source_text=text,
            )
        logger.info(
            "已有其他链路回复,跳过私聊被动接管: user=%s text=%s result=%s",
            user_id,
            _single_line(text, 80),
            _single_line(existing_reply_preview, 120),
        )
        return
    canonical_user_id = self._canonical_private_user_id(user_id)
    raw_users = self.data.get("users", {})
    existing_user = raw_users.get(canonical_user_id) if isinstance(raw_users, dict) else None
    private_profile_available = self._private_passive_profile_available(
        canonical_user_id,
        existing_user if isinstance(existing_user, dict) else None,
    )
    if not private_profile_available:
        logger.info(
            "非目标/未启用私聊放行默认主链: user=%s text=%s reason=%s",
            _single_line(canonical_user_id or user_id, 80),
            _single_line(text, 120),
            "not_profile",
        )
        return
    self._record_c3_inbound_activity(
        event,
        text=text,
        received_ts=received_ts,
        user_id=canonical_user_id or user_id,
        sender_id=user_id,
        sender_name=sender_display_name,
    )
    await self._cancel_activity_followup_on_user_return(
        canonical_user_id or user_id,
        trigger_message_id=self._event_message_id(event),
        trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
        source_text=text,
    )
    if text and await self._maybe_answer_companion_manual_natural_question(event, text):
        return
    natural_photo_text = _single_line(event.message_str, 800)
    if natural_photo_text:
        try:
            if await self._maybe_handle_natural_language_photo_request(event, user_id, natural_photo_text):
                return
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "私聊自然语言生图前置处理缺少可选模型依赖，已降级放行普通私聊: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
    if self._proactive_only_blocks_passive_event(event, "private_event_pipeline"):
        await self._record_proactive_only_private_feedback(
            event,
            user_id=user_id,
            sender_display_name=sender_display_name,
            text=text,
            received_ts=received_ts,
        )
        return
    receipt_text = _single_line(event.message_str, 800)
    if receipt_text and await self._maybe_handle_atrelay_private_receipt_reply(event, user_id, sender_display_name, receipt_text):
        return
    forward_only_prompt = ""
    if self._feature_enabled_or_temp_unlocked("enable_forward_message_adaptation") and not text:
        try:
            forward_id, forward_payload = await self._find_forward_descriptor_for_event(event)
        except Exception as exc:
            forward_id, forward_payload = "", {}
            logger.info("私聊合并消息预解析失败: user=%s error=%s", user_id, _single_line(exc, 120))
        if forward_id or forward_payload:
            forward_only_prompt = "我转发了一段聊天记录,你看看里面在说什么。"
            text = forward_only_prompt
            try:
                event.message_str = forward_only_prompt
                message_obj = getattr(event, "message_obj", None)
                if message_obj is not None:
                    setattr(message_obj, "message_str", forward_only_prompt)
            except Exception:
                pass
            logger.info(
                "私聊纯合并消息已补触发文本: user=%s id=%s inline=%s",
                user_id,
                _single_line(forward_id, 40) or "inline",
                bool(forward_payload),
            )
    if not text and not forward_only_prompt:
        quoted_relation_text = await self._private_reply_only_relation_lookup_text(event)
        if quoted_relation_text:
            text = quoted_relation_text
            try:
                event.message_str = quoted_relation_text
                message_obj = getattr(event, "message_obj", None)
                if message_obj is not None:
                    setattr(message_obj, "message_str", quoted_relation_text)
            except Exception:
                pass
    has_nontext_content = self._private_event_has_nontext_content(event)
    if (
        not text
        and not forward_only_prompt
        and not self._private_event_has_image_safe(event, label="private_empty_guard")
        and not has_nontext_content
    ):
        component_types: list[str] = []
        try:
            for item in self._event_components(event):
                component_types.append(_single_line(self._component_type_name(item), 40))
        except Exception:
            component_types = []
        logger.info(
            "忽略空私聊事件,避免阻止默认 LLM 空跑: user=%s components=%s",
            user_id,
            ",".join([item for item in component_types if item]) or "-",
        )
        self._record_passive_no_reply(
            event,
            source="私聊事件",
            reason="空私聊事件被忽略",
            detail=",".join([item for item in component_types if item]) or "-",
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()
        return
    reference_media_with_text = False
    if text and not forward_only_prompt:
        try:
            reference_media_with_text = await self._event_references_media_or_forward_with_text(event, text)
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "私聊引用媒体检测缺少可选模型依赖，已按普通文本继续: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
            reference_media_with_text = False
        if reference_media_with_text:
            logger.info(
                "私聊引用媒体/合并消息附带文字,跳过文本收口等待: user=%s text=%s",
                user_id,
                _single_line(text, 80),
            )
    return _StageNext((calendar_observation_result, event, forward_only_prompt, received_ts, reference_media_with_text, sender_display_name, text, user_id))
