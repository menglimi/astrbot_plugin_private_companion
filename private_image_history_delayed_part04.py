# -*- coding: utf-8 -*-
"""PrivateImageHistoryDelayedPart04Mixin。

由 tools/split_mixin_domain.py 从 private_image_history_delayed.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 61 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageHistoryDelayedMixin）。
"""
from __future__ import annotations

import asyncio
from .helpers import _safe_float, _single_line
from .private_image_shared import _private_image_host, logger



class PrivateImageHistoryDelayedPart04Mixin:
    """PrivateImageHistoryDelayedPart04Mixin（从 PrivateImageHistoryDelayedMixin 拆出）。"""


    async def _finalize_private_image_buffer_after_wait(self, key: str, user_id: str, first_ts: float) -> None:
        wait = self._message_debounce_seconds("image")
        remaining = max(0.0, first_ts + wait - _private_image_host._now_ts())
        if remaining > 0:
            await asyncio.sleep(remaining)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        if not isinstance(buffer, dict):
            return
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        placeholder = "用户刚刚先单独发送了一张图片,可能马上会补充说明。"
        has_followup = any(
            isinstance(item, dict)
            and (cleaned := _single_line(item.get("text"), 260))
            and cleaned != placeholder
            for item in messages
        )
        if has_followup:
            logger.info("私聊单图已由补充消息接管: user=%s", user_id)
            return
        claimed_ts = _safe_float(buffer.get("vision_context_claimed_ts"), 0.0)
        if claimed_ts > 0:
            logger.info(
                "私聊单图上下文已由补充文字请求认领,跳过延迟派发: user=%s claimed_ago=%.1fs",
                user_id,
                max(0.0, _private_image_host._now_ts() - claimed_ts),
            )
            buffers.pop(key, None)
            return
        original_event = buffer.get("original_event")
        delayed_buffer = dict(buffer)
        delayed_buffer["images"] = list(buffer.get("images") or [])
        delayed_buffer["messages"] = list(messages)
        handoff = (
            self._remember_private_image_vision_handoff(key, original_event, delayed_buffer)
            if isinstance(original_event, _private_image_host.AstrMessageEvent)
            else None
        )
        buffers.pop(key, None)
        if isinstance(original_event, _private_image_host.AstrMessageEvent):
            try:
                await self._send_delayed_private_image_only_event(original_event, user_id, delayed_buffer)
            finally:
                if isinstance(handoff, dict):
                    handoff["delayed_dispatch_finished_ts"] = _private_image_host._now_ts()
                    handoff["delayed_reply_sent"] = bool(delayed_buffer.get("delayed_reply_sent"))
                    handoff["delayed_reply_sent_ts"] = _safe_float(
                        delayed_buffer.get("delayed_reply_sent_ts"),
                        0.0,
                    )
                    completed_vision = self._completed_private_image_vision_task_text(handoff.get("vision_task"))
                    if completed_vision:
                        handoff["vision_text"] = _single_line(
                            completed_vision,
                            self._private_image_vision_text_limit(len(handoff.get("images") or [])),
                        )
            return
        vision_task = delayed_buffer.get("vision_task")
        if isinstance(vision_task, asyncio.Task) and not vision_task.done():
            vision_task.cancel()
        logger.info("私聊单图等待补充后无文字指示,但原事件不可用: user=%s", user_id)
