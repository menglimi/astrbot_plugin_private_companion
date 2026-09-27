# -*- coding: utf-8 -*-
"""handle_private_message 阶段 3（lock_buffer）。

由 tmp/refactor/mpp2_split.py 从 message_pipeline_part02.py 的 handle_private_message 段级拆分而来。
本模块无类：阶段为模块级 async 函数，显式接收 self。
阶段体与拆分前逐字节相同（缩进归一）；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
"""
from __future__ import annotations

from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _safe_float,
    _single_line,
)
from .message_pipeline_part02_shared import _StageNext
from .message_pipeline_part01 import _persona_value
from .message_pipeline_shared import logger


async def _handle_private_message_lock_buffer(
    self,
    calendar_observation_result,
    event,
    forward_only_prompt,
    is_target_user,
    private_image_enhancement_enabled,
    private_image_only,
    received_ts,
    reference_media_with_text,
    rest_silence_early_block,
    rest_silence_early_text,
    sender_display_name,
    smart_debounce_state_changed,
    text,
    user,
    user_id,
):
    """handle_private_message 段：图文混合上下文、转发缓冲、单图防抖与文本收口。"""
    if (
        is_target_user
        and text
        and not forward_only_prompt
        and private_image_enhancement_enabled
        and not private_image_only
        and self._private_event_has_image_safe(event, label="private_text_image")
    ):
        try:
            async with self._temporarily_release_data_lock():
                persisted_images = await self._persist_private_inbound_images(event, user_id)
            usable_images = [source for source in persisted_images if self._private_image_source_to_model_url(source)]
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "私聊图文图片预处理缺少可选模型依赖，已按纯文本继续: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
            persisted_images = []
            usable_images = []
        if usable_images:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            try:
                has_visual_provider = self._has_private_image_visual_provider(umo)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "私聊图文视觉 provider 检测缺少可选模型依赖，已关闭本轮识图: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
                has_visual_provider = False
            setattr(event, "private_companion_delayed_image_sources", usable_images[:5])
            has_dynamic_gif_sources = (
                bool(_persona_value(self, 'enable_private_image_gif_enhancement', True))
                and self._private_image_sources_include_gif(usable_images)
            )
            image_mode = self._private_image_delivery_mode(
                has_visual_provider=has_visual_provider,
                main_provider_supports_image=self._event_main_provider_supports_image(event),
                has_dynamic_gif=has_dynamic_gif_sources,
            )
            setattr(event, "private_companion_delayed_image_mode", image_mode)
            if image_mode == "caption":
                try:
                    async with self._temporarily_release_data_lock():
                        vision_text = _single_line(
                            await self._transcribe_private_inbound_images(
                                usable_images[:5],
                                umo=umo,
                                user_text=text,
                                force_contextual=self._private_image_user_mentions_combo_result(text) or self._private_image_user_has_specific_vision_request(text),
                            ),
                            self._private_image_vision_text_limit(len(usable_images)),
                        )
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if not missing:
                        raise
                    logger.warning(
                        "私聊图文视觉摘要缺少可选模型依赖，已按无视觉摘要继续: user=%s module=%s err=%s",
                        user_id,
                        missing,
                        _single_line(exc, 160),
                    )
                    vision_text = ""
                if vision_text:
                    setattr(event, "private_companion_delayed_image_vision_text", vision_text)
            logger.info(
                "私聊文本图片混合消息已接入图片上下文: user=%s images=%s mode=%s gif=%s combo=%s vision=%s text=%s",
                user_id,
                len(usable_images),
                image_mode,
                has_dynamic_gif_sources,
                self._private_image_user_mentions_combo_result(text),
                bool(_single_line(getattr(event, "private_companion_delayed_image_vision_text", ""), 80)),
                _single_line(text, 80),
            )
        else:
            logger.info(
                "私聊文本图片混合消息未解析到可用图片源: user=%s sources=%s text=%s",
                user_id,
                len(persisted_images),
                _single_line(text, 80),
            )
    if is_target_user and forward_only_prompt:
        key = self._semantic_buffer_key(f"private:{user_id}", user_id)
        if self._note_semantic_message_buffer(
            key,
            text,
            now=received_ts,
            wait_seconds=self._message_debounce_seconds("forward"),
            kind="forward",
        ):
            if smart_debounce_state_changed:
                self._schedule_data_save(sections={"smart_message_debounce"})
            event.stop_event()
            return
    if private_image_only:
        setattr(event, "private_companion_deferred_private_image_only", True)
        key = self._semantic_buffer_key(f"private:{user_id}", user_id)
        self._note_semantic_message_buffer(
            key,
            "用户刚刚先单独发送了一张图片,可能马上会补充说明。",
            now=received_ts,
            wait_seconds=self._message_debounce_seconds("image"),
            kind="image",
        )
        buffers = getattr(self, "_semantic_message_buffers", None)
        if isinstance(buffers, dict) and isinstance(buffers.get(key), dict):
            async with self._temporarily_release_data_lock():
                persisted_images = await self._persist_private_inbound_images(event, user_id)
            has_model_usable_image = any(self._private_image_source_to_model_url(source) for source in persisted_images)
            if not persisted_images:
                buffers.pop(key, None)
                setattr(event, "private_companion_deferred_private_image_only", False)
                logger.info(
                    "私聊单图未解析到可用图片源,放行原始事件: user=%s sources=%s",
                    user_id,
                    len(persisted_images),
                )
                if smart_debounce_state_changed:
                    self._schedule_data_save(sections={"smart_message_debounce"})
                return
            if not has_model_usable_image:
                logger.info(
                    "私聊单图已保存但不可直供模型,仍进入防抖等待补充: user=%s sources=%s",
                    user_id,
                    len(persisted_images),
                )
            buffers[key]["images"] = persisted_images
            buffers[key]["original_event"] = event
            has_dynamic_gif_sources = (
                bool(_persona_value(self, 'enable_private_image_gif_enhancement', True))
                and self._private_image_sources_include_gif(persisted_images)
            )
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            has_visual_provider = self._has_private_image_visual_provider(umo)
            image_mode = self._private_image_delivery_mode(
                has_visual_provider=has_visual_provider,
                main_provider_supports_image=bool(persisted_images) and self._event_main_provider_supports_image(event),
                has_dynamic_gif=has_dynamic_gif_sources,
            )
            buffers[key]["image_mode"] = image_mode
            if persisted_images and image_mode == "caption":
                buffers[key]["vision_task"] = self._create_lifecycle_background_task(
                    self._transcribe_private_inbound_images(
                        persisted_images,
                        umo=umo,
                    ),
                    label="private_image_debounce_vision",
                )
            logger.info(
                "私聊单图已进入防抖缓冲: user=%s images=%s mode=%s vision=%s",
                user_id,
                len(persisted_images),
                image_mode,
                bool(persisted_images) and image_mode == "caption",
            )
            self._create_lifecycle_background_task(
                self._finalize_private_image_buffer_after_wait(key, user_id, received_ts),
                label="private_image_debounce_finalize",
            )
            if smart_debounce_state_changed:
                self._schedule_data_save(sections={"smart_message_debounce"})
        event.stop_event()
        return
    elif is_target_user and not forward_only_prompt and not reference_media_with_text:
        pending_debounce_merge = False
        pending_absorber = getattr(self, "_message_debounce_absorb_pending_message", None)
        if callable(pending_absorber):
            pending_debounce_merge = bool(pending_absorber(event, text))
        key = self._semantic_buffer_key(f"private:{user_id}", user_id)
        buffers = getattr(self, "_semantic_message_buffers", None)
        existing_buffer = buffers.get(key) if isinstance(buffers, dict) else None
        buffered_images = (
            isinstance(existing_buffer, dict)
            and isinstance(existing_buffer.get("images"), list)
            and bool(existing_buffer.get("images"))
            and _now_ts() - _safe_float(existing_buffer.get("first_ts"), 0) <= max(
                45.0,
                self._message_debounce_seconds("image") + 30.0,
            )
        )
        if buffered_images:
            messages = existing_buffer.setdefault("messages", [])
            if not isinstance(messages, list):
                messages = []
                existing_buffer["messages"] = messages
            cleaned_text = _single_line(text, 260)
            if cleaned_text and cleaned_text not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                messages.append({"ts": _now_ts(), "text": cleaned_text, "sender_name": ""})
            existing_buffer["updated_ts"] = _now_ts()
            if _safe_float(existing_buffer.get("deadline_ts"), 0.0) <= 0:
                first_ts = _safe_float(existing_buffer.get("first_ts"), received_ts, received_ts)
                existing_buffer["deadline_ts"] = first_ts + self._message_debounce_seconds("image")
            logger.info(
                "消息收口合并补话: kind=image mode=fixed scope=private:%s sender=%s wait=%.1fs count=%s text=%s",
                user_id,
                user_id,
                self._message_debounce_seconds("image"),
                len(messages),
                _single_line(cleaned_text, 80),
            )
        elif not pending_debounce_merge:
            try:
                async with self._temporarily_release_data_lock():
                    smart_wait = await self._smart_message_debounce_wait_seconds_for_event(
                        event,
                        key=key,
                        text=text,
                        sender_id=user_id,
                        sender_name=sender_display_name,
                        private_chat=True,
                    )
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "私聊智能收口模型缺少可选依赖，已回退固定等待: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
                smart_wait = self._message_debounce_seconds("text")
                try:
                    setattr(event, "private_companion_smart_message_debounce_result", {"decision": "fixed", "confidence": 0.0, "reason": f"missing {missing}"})
                except Exception:
                    pass
            smart_result = getattr(event, "private_companion_smart_message_debounce_result", None)
            smart_decision = str(smart_result.get("decision") or "") if isinstance(smart_result, dict) else ""
            smart_handled = smart_decision in {"complete", "incomplete"}
            smart_debounce_state_changed = smart_debounce_state_changed or smart_handled
            wait_seconds = smart_wait if smart_handled else self._message_debounce_seconds("text")
            if self._note_semantic_message_buffer(
                key,
                text,
                wait_seconds=wait_seconds,
                smart_debounce={"enabled": smart_handled, "decision": smart_decision or "fixed"},
                kind="text",
            ):
                if smart_debounce_state_changed:
                    self._schedule_data_save(sections={"smart_message_debounce"})
                event.stop_event()
                return
    return _StageNext((calendar_observation_result, event, is_target_user, received_ts, rest_silence_early_block, rest_silence_early_text, sender_display_name, smart_debounce_state_changed, text, user, user_id))
