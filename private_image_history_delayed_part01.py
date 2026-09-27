# -*- coding: utf-8 -*-
"""PrivateImageHistoryDelayedPart01Mixin。

由 tools/split_mixin_domain.py 从 private_image_history_delayed.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 488 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageHistoryDelayedMixin）。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from .helpers import _safe_float, _single_line, _strip_outbound_control_blocks
from .persona_config import runtime_persona_setting
from .private_image_shared import _private_image_host, logger
from .segmented_message import sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from typing import Any



class PrivateImageHistoryDelayedPart01Mixin:
    """PrivateImageHistoryDelayedPart01Mixin（从 PrivateImageHistoryDelayedMixin 拆出）。"""


    def _route_private_image_caption_with_keyword_router(
        self, event: AstrMessageEvent, vision_text: str
    ) -> bool:
        """让绕过标准流水线的纯图片 Agent 也能应用关键词模型路由。"""
        caption = _single_line(vision_text, 8000)
        if not caption:
            return False
        context = self._private_image_framework_context()
        getter = getattr(context, "get_registered_star", None)
        if not callable(getter):
            return False
        try:
            metadata = getter("astrbot_plugin_keyword_model_router")
            router = getattr(metadata, "star_cls", None) if metadata is not None else None
            route = getattr(router, "route_companion_image_caption", None)
            if not callable(route):
                return False
            setattr(event, "private_companion_image_caption_route_text", caption)
            return bool(route(event, caption))
        except Exception as exc:
            logger.debug(
                "调用关键词模型路由失败，保留原 Provider: %s",
                _single_line(exc, 120),
            )
            return False

    def _take_buffered_private_image_context_for_event(self, event: AstrMessageEvent) -> dict[str, Any]:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return {}
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
            except Exception:
                pass
        key = self._semantic_buffer_key(f"private:{sender_id}", sender_id)
        now = _private_image_host._now_ts()
        handoffs = self._cleanup_private_image_vision_handoffs(now=now)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        max_live_age = max(30.0, self._message_debounce_seconds("image") + 30.0)
        live_updated_ts = (
            _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), 0)
            if isinstance(buffer, dict)
            else 0.0
        )
        if isinstance(buffer, dict) and now - live_updated_ts <= max_live_age:
            handoffs.pop(key, None)
            # 标记图片上下文已被本轮文字请求认领，防抖 finalizer 会跳过二次派发。
            buffer["vision_context_claimed_ts"] = now
            images = buffer.pop("images", [])
            image_limit = self._private_image_vision_text_limit(len(images))
            return {
                "images": [str(item) for item in images[:5] if str(item or "").strip()],
                "image_mode": _single_line(buffer.pop("image_mode", ""), 20),
                "vision_task": buffer.pop("vision_task", None),
                "vision_text": _single_line(buffer.pop("vision_text", ""), image_limit),
                "from_handoff": False,
            }

        handoff = handoffs.get(key)
        if not isinstance(handoff, dict):
            return {}
        stored_session = _single_line(handoff.get("session"), 500)
        current_session = self._private_image_vision_handoff_session(event)
        if stored_session != current_session:
            logger.info(
                "私聊图片视觉交接会话不匹配,保留给原会话: sender=%s stored=%s current=%s",
                sender_id,
                stored_session,
                current_session or "-",
            )
            return {}
        handoffs.pop(key, None)
        images = handoff.get("images") if isinstance(handoff.get("images"), list) else []
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_task = handoff.get("vision_task")
        vision_text = _single_line(handoff.get("vision_text"), image_limit)
        if not vision_text:
            vision_text = _single_line(
                self._completed_private_image_vision_task_text(vision_task),
                image_limit,
            )
        logger.info(
            "私聊补充文字已领取延迟图片视觉交接: sender=%s images=%s has_vision=%s pending=%s",
            sender_id,
            len(images),
            bool(vision_text),
            isinstance(vision_task, asyncio.Task) and not vision_task.done(),
        )
        return {
            "images": [str(item) for item in images[:5] if str(item or "").strip()],
            "image_mode": _single_line(handoff.get("image_mode"), 20),
            "vision_task": vision_task,
            "vision_text": vision_text,
            "from_handoff": True,
        }

    def _private_image_context_user_message(self, *, vision_text: str, image_count: int = 1) -> str:
        count = max(1, int(image_count or 1))
        image_label = "一张图片" if count == 1 else f"{count} 张图片"
        summary = _single_line(vision_text, self._private_image_vision_text_limit(count))
        if summary:
            return f"用户发送了{image_label}。[图片内容：{summary}]"
        return f"用户发送了{image_label}，但当前没有获得可靠视觉摘要。"

    @staticmethod
    def _private_image_history_content_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "").lower()
                    if item_type in {"text", "plain"}:
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    elif item_type in {"image", "image_url"}:
                        parts.append("[图片]")
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    item_type = str(getattr(item, "type", "") or "").lower()
                    item_text = getattr(item, "text", None)
                    if item_type in {"text", "plain"} and item_text:
                        parts.append(str(item_text))
                    elif "image" in item_type or "image" in item.__class__.__name__.lower():
                        parts.append("[图片]")
            return " ".join(part for part in parts if part).strip()
        if isinstance(value, dict):
            return PrivateImageHistoryDelayedPart01Mixin._private_image_history_content_text(
                value.get("content") or value.get("text") or value.get("message") or ""
            )
        item_type = str(getattr(value, "type", "") or "").lower()
        item_text = getattr(value, "text", None)
        if item_text:
            return str(item_text).strip()
        if "image" in item_type or "image" in value.__class__.__name__.lower():
            return "[图片]"
        return ""

    @staticmethod
    def _private_image_history_item_role(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("role") or item.get("type") or "").strip().lower()
        return str(getattr(item, "role", "") or "").strip().lower()

    @staticmethod
    def _private_image_history_item_content(item: Any) -> Any:
        if isinstance(item, dict):
            return item.get("content")
        return getattr(item, "content", None)

    def _private_image_append_history_marker(self, item: Any, marker: str) -> bool:
        content = self._private_image_history_item_content(item)
        current_text = self._private_image_history_content_text(content)
        if marker in current_text:
            return False
        if isinstance(content, str):
            new_content = f"{content}\n{marker}".strip()
            if isinstance(item, dict):
                item["content"] = new_content
            else:
                item.content = new_content
            return True
        if isinstance(content, list):
            for part in reversed(content):
                if isinstance(part, dict) and str(part.get("type") or "").lower() in {"text", "plain"}:
                    part["text"] = f"{part.get('text') or part.get('content') or ''}\n{marker}".strip()
                    part.pop("content", None)
                    return True
                if str(getattr(part, "type", "") or "").lower() in {"text", "plain"}:
                    part.text = f"{getattr(part, 'text', '') or ''}\n{marker}".strip()
                    return True
            content.append(TextPart(text=marker))
            return True
        new_content = marker
        if isinstance(item, dict):
            item["content"] = new_content
        else:
            item.content = new_content
        return True

    @staticmethod
    def _private_image_history_summary_line(summary: str) -> str:
        cleaned = _single_line(summary, 2400)
        return f"[图片内容：{cleaned}]" if cleaned else ""

    def _private_image_history_user_matches_event(
        self,
        item: Any,
        event: AstrMessageEvent,
    ) -> bool:
        content = self._private_image_history_content_text(
            self._private_image_history_item_content(item)
        )
        if not content:
            return False
        event_text = _single_line(getattr(event, "message_str", ""), 600)
        normalized_content = re.sub(r"\s+", "", content)
        normalized_event = re.sub(r"\s+", "", event_text)
        if normalized_event and normalized_event not in {"[图片]", "图片", "【图片】"}:
            return normalized_event in normalized_content or normalized_content in normalized_event
        return "图片" in normalized_content or "[CQ:image" in normalized_content.lower()

    async def _persist_private_image_vision_summary_to_history(
        self,
        event: AstrMessageEvent,
    ) -> bool:
        """Attach the vision result to the current user history turn.

        The caption provider is an auxiliary call and its result is not part of
        AstrBot's normal request history.  Persisting a bounded, visible user
        text marker makes the next turn able to recover what the image showed,
        while keeping the original image segment and assistant reply intact.
        """
        summary = ""
        for field_name in (
            "private_companion_delayed_image_vision_text",
            "private_companion_reply_image_vision_text",
            "private_companion_image_caption_route_text",
        ):
            summary = _single_line(getattr(event, field_name, ""), 2400)
            if summary:
                break
        marker = self._private_image_history_summary_line(summary)
        if not marker:
            return False
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        manager = getattr(getattr(self, "context", None), "conversation_manager", None)
        if not umo:
            return False
        requested_cid = _single_line(
            getattr(event, "_private_companion_response_conversation_id", ""),
            160,
        )

        async def write() -> bool:
            # The core serializes this live context after the send hooks.  Try
            # it first so the marker cannot be lost to a stale database copy.
            run_context = getattr(event, "_private_companion_run_context", None)
            run_messages = getattr(run_context, "messages", None)
            if isinstance(run_messages, list):
                for item in reversed(run_messages):
                    if self._private_image_history_item_role(item) != "user":
                        continue
                    if not self._private_image_history_user_matches_event(item, event):
                        continue
                    current_text = self._private_image_history_content_text(
                        self._private_image_history_item_content(item)
                    )
                    if marker in current_text:
                        return False
                    if self._private_image_append_history_marker(item, marker):
                        logger.info("已将图片视觉摘要附加到当前用户消息，交由 AstrBot 核心保存: session=%s", umo)
                        return True

            if manager is None:
                return False
            conversation_id = requested_cid or _single_line(
                await manager.get_curr_conversation_id(umo),
                160,
            )
            if not conversation_id:
                return False
            conversation = await manager.get_conversation(umo, conversation_id)
            if conversation is None:
                return False
            raw_history = getattr(conversation, "history", "[]")
            if isinstance(raw_history, str):
                history = json.loads(raw_history or "[]")
            elif isinstance(raw_history, list):
                history = list(raw_history)
            else:
                history = []

            for item in reversed(history):
                if not isinstance(item, dict) or str(item.get("role") or "") != "user":
                    continue
                if not self._private_image_history_user_matches_event(item, event):
                    continue
                content = item.get("content")
                current_text = self._private_image_history_content_text(content)
                if marker in current_text:
                    return False
                self._private_image_append_history_marker(item, marker)
                await manager.update_conversation(umo, conversation_id, history=history)
                logger.info("已将图片视觉摘要写入当前用户 history: session=%s", umo)
                return True
            logger.debug("未找到可附加图片视觉摘要的当前用户 history: session=%s", umo)
            return False

        db_operation = getattr(self, "_conversation_db_operation", None)
        try:
            result = db_operation("persist_private_image_vision", write) if callable(db_operation) else write()
            if hasattr(result, "__await__"):
                result = await result
            return bool(result)
        except Exception as exc:
            logger.warning("图片视觉摘要写入会话 history 失败: %s", _single_line(exc, 160))
            return False

    def _private_image_context_assistant_message(self, reply: str) -> str:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return _single_line(reply, 1200)
        cleaner = getattr(self, "_visible_text_without_tts_reading", None)
        if callable(cleaner):
            try:
                cleaned = str(cleaner(reply, limit=1200) or "").strip()
            except Exception:
                cleaned = ""
        else:
            cleaned = ""
        if not cleaned:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(reply or ""), flags=re.IGNORECASE).strip()
        # This text is persisted into AstrBot's user-visible conversation
        # history; remove plugin-only markers before it reaches that store.
        return _single_line(
            sanitize_llm_segment_control_tokens(
                _strip_outbound_control_blocks(
                    cleaned or reply,
                    tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                )
            ),
            1200,
        )

    async def _archive_private_image_turn_to_conversation(
        self,
        event: AstrMessageEvent,
        *,
        user_message: str,
        assistant_message: str,
    ) -> None:
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not umo or not user_message or not assistant_message:
            return
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if conv_mgr is None:
            return
        ensure_conv = getattr(self, "_ensure_conversation_id_for_umo", None)
        db_operation = getattr(self, "_conversation_db_operation", None)
        for attempt in range(4):
            try:
                user_msg_obj = UserMessageSegment(content=str(user_message or ""))
                assistant_msg_obj = AssistantMessageSegment(content=str(assistant_message or ""))

                async def _write() -> bool:
                    if callable(ensure_conv):
                        conv_id = await ensure_conv(umo, title="Private Companion 图片对话")
                    else:
                        conv_id = await conv_mgr.get_curr_conversation_id(umo)
                        if not conv_id:
                            try:
                                conv_id = await conv_mgr.new_conversation(umo, title="Private Companion 图片对话")
                            except TypeError:
                                conv_id = await conv_mgr.new_conversation(umo)
                    if not conv_id:
                        return False
                    await conv_mgr.add_message_pair(
                        cid=conv_id,
                        user_message=user_msg_obj,
                        assistant_message=assistant_msg_obj,
                    )
                    return True

                written = await db_operation("archive_private_image_turn", _write) if callable(db_operation) else await _write()
                if written:
                    logger.info("已将私聊图片回复写入 AstrBot 会话历史: %s", umo)
                else:
                    logger.warning("私聊图片回复写入会话历史失败: 无法获取或创建 AstrBot 会话 history umo=%s", umo)
                return
            except Exception as exc:
                text = str(exc or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("私聊图片回复写入会话历史失败: %s", _single_line(exc, 160))
                return

    async def _memory_companion_record_private_image_visible_turn(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        user_message: str,
        assistant_message: str,
        vision_text: str = "",
        image_count: int = 1,
    ) -> None:
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        try:
            bridge = bridge_getter() if callable(bridge_getter) else None
        except Exception as exc:
            optional_failed = getattr(self, "_memory_companion_optional_dependency_failed", None)
            if callable(optional_failed) and optional_failed(exc, where="private_image_visible_turn_bridge"):
                return
            logger.debug("MemoryCompanion 桥接读取失败，跳过私聊图片可见上下文写入: %s", _single_line(exc, 120))
            return
        recorder = getattr(bridge, "record_visible_turn", None) if bridge is not None else None
        if not callable(recorder) or not user_message or not assistant_message:
            return
        session_id = _single_line(getattr(event, "unified_msg_origin", ""), 200)
        if not session_id:
            return
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        user_name = ""
        try:
            user_name = _single_line(self._sender_display_name(event), 80)
        except Exception:
            user_name = _single_line(user_id, 80)
        turn_id = uuid.uuid4().hex
        summary = _single_line(vision_text, self._private_image_vision_text_limit(image_count))
        base_metadata = {
            "source": "private_companion_private_image_turn",
            "image_count": max(1, int(image_count or 1)),
            "summary": summary,
            "conversation_turn": "private_image",
        }
        try:
            await recorder(
                role="user",
                content=user_message,
                scope="private",
                session_id=session_id,
                platform=platform,
                user_id=str(user_id or ""),
                user_name=user_name,
                message_id=f"private_companion_image_turn_{turn_id}_user",
                source="private_companion_private_image_turn",
                metadata={**base_metadata, "turn_role": "user"},
            )
            await recorder(
                role="assistant",
                content=assistant_message,
                scope="private",
                session_id=session_id,
                platform=platform,
                user_id=str(user_id or ""),
                user_name=user_name,
                message_id=f"private_companion_image_turn_{turn_id}_assistant",
                source="private_companion_private_image_turn",
                metadata={**base_metadata, "turn_role": "assistant"},
            )
            logger.info("已将私聊图片回复同步为 MemoryCompanion 可见上下文: session=%s", session_id)
        except Exception as exc:
            optional_failed = getattr(self, "_memory_companion_optional_dependency_failed", None)
            if callable(optional_failed) and optional_failed(exc, where="record_private_image_visible_turn"):
                return
            logger.debug("MemoryCompanion 私聊图片可见上下文写入失败: %s", _single_line(exc, 120))

    async def _archive_private_image_turn_context(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        vision_text: str,
        reply: str,
        image_count: int = 1,
    ) -> None:
        assistant_message = self._private_image_context_assistant_message(reply)
        if not assistant_message:
            return
        user_message = self._private_image_context_user_message(vision_text=vision_text, image_count=image_count)
        await self._archive_private_image_turn_to_conversation(
            event,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        await self._memory_companion_record_private_image_visible_turn(
            event,
            user_id=user_id,
            user_message=user_message,
            assistant_message=assistant_message,
            vision_text=vision_text,
            image_count=image_count,
        )
        livingmemory_recorder = getattr(
            self,
            "_record_final_assistant_in_livingmemory",
            None,
        )
        if callable(livingmemory_recorder):
            message_id_getter = getattr(self, "_event_message_id", None)
            message_id = (
                _single_line(message_id_getter(event), 120)
                if callable(message_id_getter)
                else ""
            )
            await livingmemory_recorder(
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
                assistant_response=assistant_message,
                delivery_id=(
                    f"private_image:{message_id or user_id}:"
                    f"{_private_image_host._now_ts():.6f}"
                ),
            )
