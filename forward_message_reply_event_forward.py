# -*- coding: utf-8 -*-
"""ForwardMessageReplyEventForwardMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 231 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

from .forward_message_shared import logger
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .forward_message_shared import _group_link_message_context
from .forward_message_shared import _safe_int
from .forward_message_shared import _single_line



class ForwardMessageReplyEventForwardMixin:
    """ForwardMessageReplyEventForwardMixin（从 ForwardMessageMixin 拆出）。"""


    async def _call_platform_action(self, event: AstrMessageEvent, action: str, **kwargs: Any) -> Any:
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return None
        return await call_action(action, **kwargs)

    async def _call_forward_msg_action(self, event: AstrMessageEvent, forward_id: str) -> Any:
        safe_id = str(forward_id or "").strip()
        if not safe_id:
            return None
        attempts = [
            ("get_forward_msg", {"id": safe_id}),
            ("get_forward_msg", {"message_id": safe_id}),
            ("get_forward_msg", {"res_id": safe_id}),
            ("get_forward_msg", {"resid": safe_id}),
        ]
        if safe_id.isdigit():
            attempts.extend(
                [
                    ("get_forward_msg", {"message_id": int(safe_id)}),
                    ("get_msg", {"message_id": int(safe_id)}),
                ]
            )
        attempts.append(("get_msg", {"message_id": safe_id}))
        last_error = ""
        for action, kwargs in attempts:
            try:
                raw = await self._call_platform_action(event, action, **kwargs)
            except Exception as exc:
                last_error = _single_line(str(exc), 120)
                continue
            if raw:
                logger.info(
                    "合并消息平台接口返回: action=%s args=%s shape=%s",
                    action,
                    ",".join(kwargs.keys()),
                    self._forward_payload_shape(raw),
                )
                return raw
        if last_error:
            logger.info("合并消息平台接口全部失败: id=%s error=%s", _single_line(safe_id, 80), last_error)
        return None

    async def _extract_forward_from_reply(self, event: AstrMessageEvent, reply_seg: Any) -> tuple[str, dict[str, Any]]:
        message_id = self._extract_reply_message_id(reply_seg)
        if not message_id:
            logger.info("引用合并消息读取跳过: Reply 段没有 message_id")
            return "", {}
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
        if recalled_message_id:
            logger.info("引用合并消息读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
            return "", {}
        message_obj = None
        try:
            message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
        except Exception:
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
            except Exception:
                message_obj = None
        if not message_obj:
            logger.info("引用合并消息读取失败: get_msg 无返回 message_id=%s", message_id)
            return "", {}
        raw_message = message_obj.get("message") if isinstance(message_obj, dict) else message_obj
        try:
            setattr(event, "_private_companion_reply_raw_message", raw_message)
            setattr(event, "_private_companion_reply_raw_message_id", message_id)
        except Exception:
            pass
        forward_id = self._extract_forward_id_from_message_obj(raw_message)
        forward_payload = self._extract_forward_payload_from_message_obj(raw_message)
        if forward_id or forward_payload:
            logger.info(
                "引用合并消息已解析: message_id=%s id=%s inline=%s",
                message_id,
                _single_line(forward_id, 40) or "inline",
                bool(forward_payload),
            )
        else:
            logger.info(
                "引用消息未解析到合并转发: message_id=%s shape=%s",
                message_id,
                self._forward_payload_shape(raw_message),
            )
        return forward_id, forward_payload

    async def _extract_image_sources_from_reply(self, event: AstrMessageEvent, reply_seg: Any) -> list[str]:
        message_id = self._extract_reply_message_id(reply_seg)
        if not message_id:
            return []
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
        if recalled_message_id:
            logger.info("引用图片读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
            return []
        message_obj = None
        try:
            message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
        except Exception:
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
            except Exception as exc:
                logger.info("引用图片读取失败: message_id=%s error=%s", message_id, _single_line(exc, 120))
                return []
        if isinstance(message_obj, dict):
            raw_message = message_obj.get("message") or message_obj.get("raw_message") or message_obj.get("content")
        else:
            raw_message = message_obj
        sources = self._extract_image_sources_from_message_obj(raw_message)
        if sources:
            logger.info("引用消息图片已解析: message_id=%s images=%s", message_id, len(sources))
        return sources[:5]

    async def _find_reply_image_sources_for_event(self, event: AstrMessageEvent) -> list[str]:
        chain = await self._reply_message_chain_for_event(event, max_depth=3)
        for row in chain:
            sources = self._extract_image_sources_from_message_obj(row.get("raw_message"))
            if sources:
                logger.info(
                    "引用链图片已解析: depth=%s message_id=%s images=%s",
                    _safe_int(row.get("depth"), 1, 1),
                    _single_line(row.get("message_id"), 120),
                    len(sources),
                )
                return sources[:5]
        return []

    def _event_has_reply_component(self, event: AstrMessageEvent) -> bool:
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name == "reply" or "reply" in type_name:
                return True
        return False

    async def _event_reply_contains_link_payload(self, event: AstrMessageEvent) -> bool:
        """Whether a reply/quote chain contains a non-image external link/share."""
        if not self._event_has_reply_component(event):
            return False
        try:
            chain = await self._reply_message_chain_for_event(event, max_depth=3)
        except Exception:
            chain = []
        for row in chain:
            if not isinstance(row, dict):
                continue
            raw_message = row.get("raw_message")
            info = self._extract_reply_rich_card_info(raw_message)
            images = {
                _single_line(item, 600)
                for item in (info.get("images") if isinstance(info, dict) else []) or []
                if _single_line(item, 600)
            }
            links = [
                _single_line(item, 600)
                for item in (info.get("links") if isinstance(info, dict) else []) or []
                if _single_line(item, 600) and _single_line(item, 600) not in images
            ]
            if links:
                return True
            preview = _single_line(row.get("text"), 1000)
            if preview and _group_link_message_context(preview, limit=1000)[1]:
                return True
        return False

    async def _event_references_media_or_forward_with_text(self, event: AstrMessageEvent, text: str) -> bool:
        if not _single_line(text, 260):
            return False
        found_reply = False
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            found_reply = True
            try:
                forward_id, forward_payload = await self._extract_forward_from_reply(event, item)
                if forward_id or forward_payload:
                    if forward_payload and not forward_id:
                        forward_id = self._build_inline_forward_id(forward_payload)
                    self._remember_forward_descriptor_for_event(event, forward_id, forward_payload)
                    return True
            except Exception:
                pass
            try:
                if await self._extract_image_sources_from_reply(event, item):
                    return True
            except Exception:
                pass
        return False

    async def _find_forward_descriptor_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any]]:
        cached = getattr(event, "_private_companion_forward_descriptor", None)
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and isinstance(cached[0], str)
            and isinstance(cached[1], dict)
        ):
            return cached
        cached_forward_id, cached_payload = self._cached_forward_descriptor_for_event(event)
        if cached_forward_id or cached_payload:
            return cached_forward_id, cached_payload
        message_obj = getattr(event, "message_obj", None)
        forward_id = ""
        forward_payload: dict[str, Any] = {}
        reply_seg = None
        chain_items = self._message_chain_items(message_obj)
        event_items = self._event_components(event)
        if event_items:
            seen_ids: set[int] = set()
            merged_items = []
            for item in [*chain_items, *event_items]:
                item_id = id(item)
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                merged_items.append(item)
            chain_items = merged_items
        for item in chain_items:
            type_name = self._component_type_name(item)
            data = self._component_data(item)
            if "forward" in type_name:
                forward_id = (
                    str(getattr(item, "id", "") or getattr(item, "resid", "") or "").strip()
                    or self._extract_forward_id_from_segment_data(data)
                    or self._extract_forward_id_from_segment_data(item if isinstance(item, dict) else {})
                )
                if isinstance(data.get("messages"), list):
                    forward_payload = {"messages": data.get("messages", [])}
                elif isinstance(item, dict) and isinstance(item.get("messages"), list):
                    forward_payload = {"messages": item.get("messages", [])}
            elif "reply" in type_name:
                reply_seg = item
        if not (forward_id or forward_payload) and reply_seg is not None:
            forward_id, forward_payload = await self._extract_forward_from_reply(event, reply_seg)
        if forward_payload and not forward_id:
            forward_id = self._build_inline_forward_id(forward_payload)
        self._remember_forward_descriptor_for_event(event, forward_id, forward_payload)
        return forward_id, forward_payload
