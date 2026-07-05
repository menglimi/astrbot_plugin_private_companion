# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Plain
except ImportError:
    from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest

from .helpers import _safe_float, _safe_int, _single_line, _strip_internal_message_blocks

class ForwardMessageMixin:
    """Forward-message parsing and prompt-context helpers."""

    def _forward_descriptor_cache_keys(self, event: AstrMessageEvent) -> list[str]:
        keys: list[str] = []
        message_id = ""
        event_message_id = getattr(self, "_event_message_id", None)
        if callable(event_message_id):
            try:
                message_id = _single_line(event_message_id(event), 120)
            except Exception:
                message_id = ""
        if not message_id:
            for attr in ("message_id", "id", "seq", "message_seq", "real_id"):
                value = _single_line(getattr(event, attr, ""), 120)
                if value:
                    message_id = value
                    break
        sender_id = ""
        try:
            sender_id = _single_line(event.get_sender_id(), 80)
        except Exception:
            sender_id = ""
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        if message_id:
            keys.append(f"id:{message_id}")
            if sender_id:
                keys.append(f"user:{sender_id}:id:{message_id}")
            if umo:
                keys.append(f"umo:{umo}:id:{message_id}")
        if sender_id:
            keys.append(f"user:{sender_id}:latest")
        return list(dict.fromkeys(key for key in keys if key))

    def _remember_forward_descriptor_for_event(
        self,
        event: AstrMessageEvent,
        forward_id: str,
        forward_payload: dict[str, Any],
    ) -> None:
        if not (forward_id or forward_payload):
            return
        descriptor = (_single_line(forward_id, 240), dict(forward_payload or {}))
        try:
            setattr(event, "_private_companion_forward_descriptor", descriptor)
        except Exception:
            pass
        cache = getattr(self, "_private_companion_forward_descriptor_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                setattr(self, "_private_companion_forward_descriptor_cache", cache)
            except Exception:
                return
        now = time.time()
        for key in self._forward_descriptor_cache_keys(event):
            cache[key] = {"ts": now, "descriptor": descriptor}
        for key, value in list(cache.items()):
            if not isinstance(value, dict) or now - _safe_float(value.get("ts"), 0) > 10 * 60:
                cache.pop(key, None)
        if len(cache) > 80:
            ranked = sorted(cache.items(), key=lambda item: _safe_float(item[1].get("ts") if isinstance(item[1], dict) else 0, 0))
            for key, _value in ranked[:-80]:
                cache.pop(key, None)

    def _cached_forward_descriptor_for_event(self, event: AstrMessageEvent) -> tuple[str, dict[str, Any]]:
        cache = getattr(self, "_private_companion_forward_descriptor_cache", None)
        if not isinstance(cache, dict):
            return "", {}
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        allow_latest = "转发" in message_text or "合并消息" in message_text or "聊天记录" in message_text
        now = time.time()
        for key in self._forward_descriptor_cache_keys(event):
            if key.endswith(":latest") and not allow_latest:
                continue
            value = cache.get(key)
            if not isinstance(value, dict) or now - _safe_float(value.get("ts"), 0) > 10 * 60:
                cache.pop(key, None)
                continue
            descriptor = value.get("descriptor")
            if (
                isinstance(descriptor, tuple)
                and len(descriptor) == 2
                and isinstance(descriptor[0], str)
                and isinstance(descriptor[1], dict)
            ):
                try:
                    setattr(event, "_private_companion_forward_descriptor", descriptor)
                except Exception:
                    pass
                return descriptor
        return "", {}

    def _component_type_name(self, item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or "").strip().lower()
        return str(getattr(item, "type", "") or item.__class__.__name__).strip().lower()

    def _component_data(self, item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            data = item.get("data", {})
            return data if isinstance(data, dict) else {}
        data = getattr(item, "data", {}) or {}
        return data if isinstance(data, dict) else {}

    def _message_chain_items(self, message_obj: Any) -> list[Any]:
        if message_obj is None:
            return []
        chain = getattr(message_obj, "message", None)
        if isinstance(chain, list):
            return chain
        if isinstance(message_obj, list):
            return message_obj
        if isinstance(message_obj, dict):
            raw = message_obj.get("message") or message_obj.get("content") or message_obj.get("messages")
            if isinstance(raw, list):
                return raw
            if raw:
                return [raw]
        return []

    def _extract_forward_id_from_segment_data(self, seg_data: dict[str, Any]) -> str:
        if not isinstance(seg_data, dict):
            return ""
        for key in ("id", "resid", "forward_id"):
            value = seg_data.get(key)
            if value:
                return str(value).strip()
        return ""

    def _decode_possible_json_text(self, value: Any) -> Any:
        text = html.unescape(str(value or "")).strip()
        if not text:
            return None
        text = text.replace("\\/", "/")
        text = text.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u003f", "?")
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            try:
                text = json.loads(text)
            except Exception:
                pass
        if isinstance(text, str) and text.strip().startswith(("{", "[")):
            try:
                return json.loads(text)
            except Exception:
                return None
        return None

    def _extract_forward_id_from_rich_value(self, value: Any, *, depth: int = 0) -> str:
        if value is None or depth > 8:
            return ""
        if isinstance(value, list):
            for item in value:
                found = self._extract_forward_id_from_rich_value(item, depth=depth + 1)
                if found:
                    return found
            return ""
        if isinstance(value, dict):
            app = str(value.get("app") or value.get("type") or "").lower()
            prompt = str(value.get("prompt") or value.get("desc") or "").lower()
            looks_forward_card = (
                "multimsg" in app
                or "forward" in app
                or "聊天记录" in prompt
                or "转发消息" in prompt
            )
            for key in ("resid", "forward_id", "id"):
                raw = value.get(key)
                if raw and (looks_forward_card or key != "id"):
                    return str(raw).strip()
            for key in ("meta", "detail", "data", "extra", "config"):
                found = self._extract_forward_id_from_rich_value(value.get(key), depth=depth + 1)
                if found:
                    return found
            for child in value.values():
                found = self._extract_forward_id_from_rich_value(child, depth=depth + 1)
                if found:
                    return found
            return ""
        if isinstance(value, str):
            parsed = self._decode_possible_json_text(value)
            if parsed is not None:
                found = self._extract_forward_id_from_rich_value(parsed, depth=depth + 1)
                if found:
                    return found
            normalized = value.replace("\\/", "/")
            if "聊天记录" in normalized or "转发消息" in normalized or "multimsg" in normalized:
                for pattern in (
                    r'"resid"\s*:\s*"([^"]+)"',
                    r'"forward_id"\s*:\s*"([^"]+)"',
                    r'"id"\s*:\s*"([^"]+)"',
                ):
                    match = re.search(pattern, normalized)
                    if match:
                        return html.unescape(match.group(1)).strip()
        return ""

    def _extract_image_url_from_segment_data(self, seg_data: dict[str, Any]) -> str:
        if not isinstance(seg_data, dict):
            return ""
        for key in ("url", "source_url", "src", "origin", "origin_url"):
            value = seg_data.get(key)
            if isinstance(value, str) and value.strip().startswith(("http://", "https://", "file://", "data:")):
                return value.strip()
        value = seg_data.get("file")
        if isinstance(value, str) and value.strip().startswith(("http://", "https://", "file://", "data:")):
            return value.strip()
        for key in ("path", "file"):
            value = seg_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _extract_image_sources_from_message_obj(self, message_obj: Any) -> list[str]:
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        def visit(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if isinstance(value, dict):
                type_name = self._component_type_name(value)
                data = self._component_data(value)
                if type_name == "image":
                    add(self._extract_image_url_from_segment_data(data))
                    for key in ("url", "file", "path", "src", "origin_url", "source_url"):
                        add(data.get(key))
                        add(value.get(key))
                for key in ("message", "raw_message", "content", "messages", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested)
                return
            type_name = self._component_type_name(value)
            if type_name == "image":
                add(self._image_component_source(value))
                data = self._component_data(value)
                add(self._extract_image_url_from_segment_data(data))
                for key in ("url", "file", "path", "src", "origin_url", "source_url"):
                    add(data.get(key))
            raw_text = str(value or "")
            for match in re.finditer(r"\[CQ:image,([^\]]+)\]", raw_text):
                fields: dict[str, str] = {}
                for part in match.group(1).split(","):
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    fields[key.strip()] = html.unescape(val.strip())
                add(self._extract_image_url_from_segment_data(fields))
                for key in ("url", "file", "path"):
                    add(fields.get(key))

        visit(message_obj)
        return [source for source in sources if source]

    def _extract_messages_from_forward_data(self, forward_data: Any) -> list[dict[str, Any]]:
        if isinstance(forward_data, list):
            return [dict(item) for item in forward_data if isinstance(item, dict)]
        if not isinstance(forward_data, dict):
            return []
        for key in ("messages", "message", "nodes"):
            value = forward_data.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        data = forward_data.get("data")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("messages", "message", "nodes"):
                value = data.get(key)
                if isinstance(value, list):
                    return [dict(item) for item in value if isinstance(item, dict)]
            nested = data.get("data")
            if isinstance(nested, list):
                return [dict(item) for item in nested if isinstance(item, dict)]
            if isinstance(nested, dict):
                for key in ("messages", "message", "nodes"):
                    value = nested.get(key)
                    if isinstance(value, list):
                        return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def _forward_payload_shape(self, value: Any, *, depth: int = 0) -> str:
        if depth > 2:
            return "..."
        if isinstance(value, list):
            preview = ", ".join(self._forward_payload_shape(item, depth=depth + 1) for item in value[:3])
            return f"list[{len(value)}]({preview})"
        if isinstance(value, dict):
            keys = list(value.keys())[:10]
            parts = []
            for key in keys:
                child = value.get(key)
                if isinstance(child, (dict, list)):
                    parts.append(f"{key}:{self._forward_payload_shape(child, depth=depth + 1)}")
                else:
                    parts.append(f"{key}:{type(child).__name__}")
            return "{" + ", ".join(parts) + "}"
        return type(value).__name__

    def _forward_node_data(self, node: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(node, dict):
            return {}
        data = node.get("data")
        return data if isinstance(data, dict) else {}

    def _forward_node_content_chain(self, node: dict[str, Any]) -> Any:
        if not isinstance(node, dict):
            return []
        node_data = self._forward_node_data(node)
        for source in (node, node_data):
            for key in ("content", "message", "raw_message"):
                value = source.get(key)
                if value not in (None, "", []):
                    return value
        return []

    def _extract_forward_payload_from_message_obj(self, message_obj: Any) -> dict[str, Any]:
        if isinstance(message_obj, list):
            for item in message_obj:
                found = self._extract_forward_payload_from_message_obj(item)
                if found:
                    return found
            return {}
        if not isinstance(message_obj, dict):
            return {}
        if self._component_type_name(message_obj) == "forward":
            seg_data = self._component_data(message_obj)
            if isinstance(seg_data.get("messages"), list):
                return {"messages": seg_data.get("messages", [])}
            if isinstance(message_obj.get("messages"), list):
                return {"messages": message_obj.get("messages", [])}
        for key in ("message", "messages", "content", "data"):
            found = self._extract_forward_payload_from_message_obj(message_obj.get(key))
            if found:
                return found
        return {}

    def _extract_forward_id_from_message_obj(self, message_obj: Any) -> str:
        if isinstance(message_obj, list):
            for item in message_obj:
                found = self._extract_forward_id_from_message_obj(item)
                if found:
                    return found
            return ""
        if not isinstance(message_obj, dict):
            return ""
        if self._component_type_name(message_obj) == "forward":
            found = self._extract_forward_id_from_segment_data(self._component_data(message_obj))
            if found:
                return found
            found = self._extract_forward_id_from_segment_data(message_obj)
            if found:
                return found
        found = self._extract_forward_id_from_rich_value(message_obj)
        if found:
            return found
        for key in ("message", "messages", "content", "data"):
            found = self._extract_forward_id_from_message_obj(message_obj.get(key))
            if found:
                return found
        return ""

    def _extract_reply_message_id(self, reply_seg: Any) -> str:
        candidates = [
            getattr(reply_seg, "id", None),
            getattr(reply_seg, "message_id", None),
            getattr(reply_seg, "msg_id", None),
        ]
        data = self._component_data(reply_seg)
        candidates.extend([data.get("id"), data.get("message_id"), data.get("msg_id")])
        if isinstance(reply_seg, dict):
            candidates.extend([reply_seg.get("id"), reply_seg.get("message_id"), reply_seg.get("msg_id")])
        for value in candidates:
            value_str = str(value or "").strip()
            if value_str:
                return value_str
        return ""

    def _build_inline_forward_id(self, payload: dict[str, Any]) -> str:
        try:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            raw = str(payload)
        return "inline:" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

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
                    "[PrivateCompanion] 合并消息平台接口返回: action=%s args=%s shape=%s",
                    action,
                    ",".join(kwargs.keys()),
                    self._forward_payload_shape(raw),
                )
                return raw
        if last_error:
            logger.info("[PrivateCompanion] 合并消息平台接口全部失败: id=%s error=%s", _single_line(safe_id, 80), last_error)
        return None

    async def _extract_forward_from_reply(self, event: AstrMessageEvent, reply_seg: Any) -> tuple[str, dict[str, Any]]:
        message_id = self._extract_reply_message_id(reply_seg)
        if not message_id:
            logger.info("[PrivateCompanion] 引用合并消息读取跳过: Reply 段没有 message_id")
            return "", {}
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
        if recalled_message_id:
            logger.info("[PrivateCompanion] 引用合并消息读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
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
            logger.info("[PrivateCompanion] 引用合并消息读取失败: get_msg 无返回 message_id=%s", message_id)
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
                "[PrivateCompanion] 引用合并消息已解析: message_id=%s id=%s inline=%s",
                message_id,
                _single_line(forward_id, 40) or "inline",
                bool(forward_payload),
            )
        else:
            logger.info(
                "[PrivateCompanion] 引用消息未解析到合并转发: message_id=%s shape=%s",
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
            logger.info("[PrivateCompanion] 引用图片读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
            return []
        message_obj = None
        try:
            message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
        except Exception:
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
            except Exception as exc:
                logger.info("[PrivateCompanion] 引用图片读取失败: message_id=%s error=%s", message_id, _single_line(exc, 120))
                return []
        if isinstance(message_obj, dict):
            raw_message = message_obj.get("message") or message_obj.get("raw_message") or message_obj.get("content")
        else:
            raw_message = message_obj
        sources = self._extract_image_sources_from_message_obj(raw_message)
        if sources:
            logger.info("[PrivateCompanion] 引用消息图片已解析: message_id=%s images=%s", message_id, len(sources))
        return sources[:5]

    async def _find_reply_image_sources_for_event(self, event: AstrMessageEvent) -> list[str]:
        chain = await self._reply_message_chain_for_event(event, max_depth=3)
        for row in chain:
            sources = self._extract_image_sources_from_message_obj(row.get("raw_message"))
            if sources:
                logger.info(
                    "[PrivateCompanion] 引用链图片已解析: depth=%s message_id=%s images=%s",
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

    async def _extract_forward_messages_for_prompt(
        self,
        event: AstrMessageEvent,
        forward_id: str,
        *,
        forward_payload: dict[str, Any] | None = None,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        if depth > 2:
            return [], [], 0
        messages = self._extract_messages_from_forward_data(forward_payload or {})
        if not messages and forward_id and not forward_id.startswith("inline:"):
            raw = await self._call_forward_msg_action(event, forward_id)
            messages = self._extract_messages_from_forward_data(raw)
            if raw and not messages:
                logger.info(
                    "[PrivateCompanion] 合并消息接口返回未解析出节点: id=%s shape=%s",
                    _single_line(forward_id, 80),
                    self._forward_payload_shape(raw),
                )
        if not messages:
            return [], [], 0
        rows: list[dict[str, Any]] = []
        image_urls: list[str] = []
        nested_count = 0
        for node in messages:
            if len(rows) >= self.forward_message_max_messages:
                break
            node_data = self._forward_node_data(node)
            sender = node.get("sender") if isinstance(node.get("sender"), dict) else {}
            if not sender and isinstance(node_data.get("sender"), dict):
                sender = node_data.get("sender") or {}
            sender_id = str(
                sender.get("user_id")
                or sender.get("uin")
                or node.get("user_id")
                or node.get("sender_id")
                or node.get("uin")
                or node_data.get("user_id")
                or node_data.get("sender_id")
                or node_data.get("uin")
                or ""
            ).strip()
            node_self_id = str(node.get("self_id") or node_data.get("self_id") or "").strip()
            event_self_id = ""
            event_self_id_func = getattr(self, "_event_self_id", None)
            if callable(event_self_id_func):
                try:
                    event_self_id = str(event_self_id_func(event) or "").strip()
                except Exception:
                    event_self_id = ""
            is_bot_self = bool(sender_id and ((event_self_id and sender_id == event_self_id) or (node_self_id and sender_id == node_self_id)))
            raw_name = _single_line(
                sender.get("card")
                or sender.get("nickname")
                or sender.get("name")
                or node.get("nickname")
                or node.get("name")
                or node_data.get("nickname")
                or node_data.get("name")
                or sender_id
                or "未知用户",
                60,
            )
            display_name = self._group_member_identity_name(sender_id, raw_name, limit=40) if sender_id else raw_name
            if is_bot_self:
                display_name = f"你自己/Bot自己（显示名:{raw_name or sender_id or '未知'}）"
            sent_at = ""
            ts = _safe_float(node.get("time") or node.get("timestamp") or node_data.get("time") or node_data.get("timestamp"), 0)
            if ts > 0:
                try:
                    sent_at = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M")
                except Exception:
                    sent_at = ""
            content_chain = self._forward_node_content_chain(node)
            pending_nested_rows: list[dict[str, Any]] = []
            if isinstance(content_chain, str):
                text = content_chain
            else:
                if isinstance(content_chain, dict):
                    content_chain = [content_chain]
                elif not isinstance(content_chain, list):
                    content_chain = [content_chain] if content_chain else []
                text_parts: list[str] = []
                for segment in content_chain:
                    if isinstance(segment, str):
                        text_parts.append(segment)
                        continue
                    seg_type = self._component_type_name(segment)
                    seg_data = self._component_data(segment)
                    if seg_type in {"text", "plain"}:
                        text_parts.append(str(seg_data.get("text") or ""))
                    elif seg_type == "at":
                        qq = str(seg_data.get("qq") or getattr(segment, "qq", "") or "").strip()
                        text_parts.append("@" + self._group_member_identity_name(qq, qq, limit=30))
                    elif seg_type == "image":
                        url = self._extract_image_url_from_segment_data(seg_data)
                        if url:
                            image_urls.append(url)
                        text_parts.append("[图片]")
                    elif seg_type in {"face", "mface", "emoji"}:
                        text_parts.append("[表情]")
                    elif seg_type == "video":
                        text_parts.append("[视频]")
                    elif seg_type == "record":
                        text_parts.append("[语音]")
                    elif seg_type == "file":
                        name = _single_line(seg_data.get("name") or seg_data.get("file"), 80)
                        text_parts.append(f"[文件:{name}]" if name else "[文件]")
                    elif seg_type == "forward" and self.forward_message_parse_nested:
                        nested_id = self._extract_forward_id_from_segment_data(seg_data)
                        nested_payload = {"messages": seg_data.get("messages", [])} if isinstance(seg_data.get("messages"), list) else None
                        nested_rows, nested_images, child_nested = await self._extract_forward_messages_for_prompt(
                            event,
                            nested_id or self._build_inline_forward_id(nested_payload or {}),
                            forward_payload=nested_payload,
                            depth=depth + 1,
                        )
                        nested_count += 1 + child_nested
                        pending_nested_rows.extend(nested_rows)
                        image_urls.extend(nested_images)
                        text_parts.append("[嵌套合并消息已展开]")
                    elif seg_type == "node":
                        nested_rows, nested_images, child_nested = await self._extract_forward_messages_for_prompt(
                            event,
                            self._build_inline_forward_id({"messages": [segment]}),
                            forward_payload={"messages": [segment]},
                            depth=depth + 1,
                        )
                        nested_count += child_nested
                        pending_nested_rows.extend(nested_rows)
                        image_urls.extend(nested_images)
                        if nested_rows:
                            text_parts.append("[合并消息节点已展开]")
                text = "".join(text_parts).strip()
            text = _single_line(text, 500)
            if text:
                rows.append(
                    {
                        "sender_id": sender_id,
                        "sender": display_name or raw_name,
                        "raw_sender": raw_name,
                        "is_bot_self": is_bot_self,
                        "time": sent_at,
                        "text": text,
                        "depth": depth,
                    }
                )
            rows.extend(pending_nested_rows)
        return rows[: self.forward_message_max_messages], image_urls[: max(0, self.forward_message_image_limit)], nested_count

    async def _format_forward_message_context_for_prompt(self, event: AstrMessageEvent, req: ProviderRequest) -> str:
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            if not checker("enable_forward_message_adaptation"):
                return ""
        elif not self.enable_forward_message_adaptation:
            return ""
        cached = getattr(event, "_private_companion_forward_context", None)
        if isinstance(cached, str):
            return cached
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        should_log_probe = any(token in message_text for token in ("转发", "合并消息", "聊天记录"))
        forward_id, payload = await self._find_forward_descriptor_for_event(event)
        if not (forward_id or payload):
            if should_log_probe:
                logger.info("[PrivateCompanion] 合并消息请求未找到描述符: text=%s", message_text or "(empty)")
            setattr(event, "_private_companion_forward_context", "")
            return ""
        if should_log_probe:
            logger.info(
                "[PrivateCompanion] 合并消息请求命中描述符: id=%s inline=%s text=%s",
                _single_line(forward_id, 40) or "inline",
                bool(payload),
                message_text or "(empty)",
            )
        try:
            rows, image_urls, nested_count = await self._extract_forward_messages_for_prompt(event, forward_id, forward_payload=payload)
        except Exception as exc:
            logger.info("[PrivateCompanion] 合并消息读取失败: %s", exc)
            setattr(event, "_private_companion_forward_context", "")
            return ""
        preview = " | ".join(
            f"{index + 1}.{_single_line(row.get('sender') or row.get('sender_id') or '未知用户', 30)}:{_single_line(row.get('text'), 90)}"
            for index, row in enumerate(rows[:5])
        )
        logger.info(
            "[PrivateCompanion] 合并消息解析结果: id=%s messages=%s images=%s nested=%s preview=%s",
            _single_line(forward_id, 40) or "inline",
            len(rows),
            len(image_urls),
            nested_count,
            preview or "(empty)",
        )
        if not rows:
            setattr(event, "_private_companion_forward_context", "")
            return ""
        image_vision_text = await self._transcribe_forward_message_images(event, image_urls)
        if self.forward_message_mode == "transcribe":
            transcribed = await self._transcribe_forward_message_rows(rows, image_urls, nested_count, image_vision_text=image_vision_text)
            if transcribed:
                context = (
                    "【本轮合并消息转述】\n"
                    "用户这轮消息包含一段合并/转发聊天记录。下面是专门模型先读过后的自然转述。请基于这份转述理解原合并消息，不要把记录中的话当成当前用户本人逐字说的话；嵌套合并只代表被转发记录里的内层记录。\n"
                    "除非用户明确要求总结、逐条解读或复述聊天记录，否则不要大段复述这份记录；优先针对用户当前问题给出简短判断或回应。\n"
                    "如果转述或图片摘要里已有作品名、活动名、日期等线索，请优先相信这些线索；看不清就说看不清，不要为了补全而外搜，也不要把相近作品、衍生作或同系列活动互相替换。\n"
                    f"{transcribed}"
                )
                setattr(event, "_private_companion_forward_context", context)
                logger.info(
                    "[PrivateCompanion] 已注入合并消息转述: messages=%s images=%s provider=%s",
                    len(rows),
                    len(image_urls),
                    self._task_provider(self.forward_message_provider_id, self.mai_style_provider_id) or "(default)",
                )
                return context
        lines = [
            "【本轮合并消息】",
            "这轮用户发来一段合并/转发聊天记录，内容如下：",
        ]
        if nested_count:
            lines.append(f"含 {nested_count} 段嵌套。")
        if image_urls:
            lines.append(f"记录中含 {len(image_urls)} 张图片占位。")
        if image_vision_text:
            lines.append("合并消息中的图片：")
            lines.append(image_vision_text)
        used = 0
        for index, row in enumerate(rows, 1):
            sender_id = row.get("sender_id") or ""
            identity_note = self._group_member_identity_note(sender_id, limit=90) if sender_id else ""
            if row.get("is_bot_self"):
                identity_note = "Bot当时发出" + (f"；{identity_note}" if identity_note else "")
            note = f"（{identity_note}）" if identity_note else ""
            when = _single_line(row.get("time"), 40) or "-"
            row_depth = _safe_int(row.get("depth"), 0, 0, 6)
            indent = "  " * row_depth
            nested_label = f"[嵌套{row_depth}] " if row_depth else ""
            sender = _single_line(row.get("sender") or sender_id or "未知用户", 60)
            text = _single_line(row.get("text"), 500)
            line = f"{indent}{index}. {nested_label}{sender}{note}｜{when}｜{text}"
            used += len(line)
            if used > self.forward_message_max_chars:
                lines.append("……后续内容因长度限制已省略。")
                break
            lines.append(line)
        context = "\n".join(lines)
        setattr(event, "_private_companion_forward_context", context)
        logger.info("[PrivateCompanion] 已注入合并消息上下文: id=%s messages=%s images=%s", _single_line(forward_id, 40) or "inline", len(rows), len(image_urls))
        return context

    async def _transcribe_forward_message_images(self, event: AstrMessageEvent, image_sources: list[str]) -> str:
        if not self.forward_message_image_vision:
            logger.info("[PrivateCompanion] 合并/引用图片视觉跳过: forward_message_image_vision=false")
            return ""
        limit = max(0, int(getattr(self, "forward_message_image_limit", 0) or 0))
        if limit <= 0:
            logger.info("[PrivateCompanion] 合并/引用图片视觉跳过: forward_message_image_limit=%s", limit)
            return ""
        original_sources = [str(item).strip() for item in (image_sources or []) if str(item or "").strip()][:limit]
        if not original_sources:
            logger.info("[PrivateCompanion] 合并/引用图片视觉跳过: 未抽取到图片源")
            return ""
        sources = await self._prepare_private_image_sources_for_model(
            original_sources,
            namespace="forward_vision",
        )
        if not sources:
            logger.info(
                "[PrivateCompanion] 合并/引用图片视觉跳过: 图片源无法转为模型可读源 original=%s",
                len(original_sources),
            )
            return ""
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        image_items, source_image_count, has_gif_frames = self._private_image_model_image_items_with_meta(sources)
        image_keys = [key for key, _ in image_items]
        image_urls = [url for _, url in image_items]
        if not image_urls:
            logger.info(
                "[PrivateCompanion] 合并/引用图片视觉跳过: 已准备图片但无模型可用 URL sources=%s prepared=%s",
                len(original_sources),
                len(sources),
            )
            return ""
        image_aliases = self._private_image_cache_aliases_for_sources([*original_sources, *sources])
        original_image_keys = self._private_image_cache_image_keys(original_sources or sources)
        if original_image_keys:
            image_keys = original_image_keys
        image_count = source_image_count or len(original_sources) or len(sources)
        gif_hint = (
            "如果同一张动态 GIF 被抽成多帧,这些帧属于同一张动图；请按整体动图主体判断归属,不要因某一帧局部相似就误判。"
            if has_gif_frames
            else ""
        )
        default_prompt = (
            "请按出现顺序把合并消息里的图片压缩成短摘要。每张图只写一行,不要写标题、分析过程或长篇描述。\n"
            "格式：第N张：<图片类型>；内容=<可见文字/主体/动作/关键细节,125字内>；表达=<用户可能借图表达的情绪、态度、疑问、用途或梗,125字内>；归属=<疑似当前角色/非当前角色/无法判断>。\n"
            "完整性规则：每张图都要同时保留客观内容和表达意图；这是在原有基础上的增强,不是二选一。"
            "若是照片/截图/漫画/聊天记录,内容描述更细一点；若是表情包/贴纸/GIF,表达意图和情绪梗分析更多一点。看不清就写看不清；不要猜测人物关系。"
            "若图中有游戏/作品/角色/活动/节日/日期等文字,必须尽量照抄可见原文；不能把同系列作品或相近活动名互换,不能用联网印象补全看不清的内容。"
            "如果同一张动态 GIF 被抽成多帧,请把连续帧当作同一个动态表情包理解,概括动作/表情变化。"
            f"{gif_hint}"
        )
        attempts = 0
        seen_providers: set[str] = set()
        skipped_providers: list[str] = []
        for provider_id, provider_source, _configured_prompt in self._private_image_visual_provider_candidates(umo):
            provider_id = _single_line(provider_id, 160)
            if not provider_id:
                skipped_providers.append(f"{provider_source}:empty")
                continue
            if provider_id in seen_providers:
                continue
            seen_providers.add(provider_id)
            if self._private_image_provider_in_failure_cooldown(provider_id, provider_source):
                skipped_providers.append(f"{provider_source}:cooldown")
                continue
            provider = self._private_image_provider_by_id(provider_id)
            if provider is None or not self._provider_supports_image(provider):
                skipped_providers.append(f"{provider_source}:no_image_provider")
                continue
            attempts += 1
            prompt = default_prompt
            self_recognition_prompt = self._private_image_self_recognition_context_prompt()
            if self_recognition_prompt and self_recognition_prompt not in prompt:
                prompt = f"{prompt}\n\n{self_recognition_prompt}"
            cache_prompt_sig = self._private_image_vision_cache_prompt_signature(default_prompt)
            cache_key = self._private_image_vision_cache_key(image_keys, provider_id, cache_prompt_sig, scope="forward_image")
            cached_text = self._get_private_image_vision_cache(
                cache_key,
                provider_id=provider_id,
                image_keys=image_keys,
                image_aliases=image_aliases,
                image_count=image_count,
                scope="forward_image",
            )
            if cached_text:
                logger.info(
                    "[PrivateCompanion] 合并消息图片视觉命中缓存: provider=%s images=%s preview=%s",
                    provider_id,
                    len(image_urls),
                    _single_line(cached_text, 220),
                )
                return cached_text
            if not self._can_run_llm_task(provider_id, task="forward_message_image_vision"):
                self._record_llm_budget_skip(provider_id=provider_id, task="forward_message_image_vision", prompt=prompt)
                skipped_providers.append(f"{provider_source}:budget")
                continue
            try:
                start = time.time()
                timeout = max(0.0, float(getattr(self, "forward_message_image_vision_timeout_seconds", 6.0) or 0.0))
                if timeout > 0:
                    result = await asyncio.wait_for(provider.text_chat(prompt=prompt, image_urls=image_urls, max_tokens=260), timeout=timeout)
                else:
                    result = await provider.text_chat(prompt=prompt, image_urls=image_urls, max_tokens=260)
                text = str(getattr(result, "completion_text", result) or "").strip()
                cleaned_text = _single_line(_strip_internal_message_blocks(text), 900)
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="forward_message_image_vision",
                    prompt=prompt,
                    completion=text,
                    resp=result,
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=True,
                    budget_exempt=True,
                )
                self._clear_private_image_provider_failure(provider_id, provider_source)
                logger.info(
                    "[PrivateCompanion] 合并消息图片视觉完成: provider=%s source=%s images=%s chars=%s preview=%s",
                    provider_id,
                    provider_source,
                    len(image_urls),
                    len(cleaned_text),
                    _single_line(cleaned_text, 220),
                )
                self._set_private_image_vision_cache(
                    cache_key,
                    cleaned_text,
                    provider_id=provider_id,
                    image_keys=image_keys,
                    image_aliases=image_aliases,
                    image_count=image_count,
                    prompt=cache_prompt_sig,
                    scope="forward_image",
                )
                return cleaned_text
            except asyncio.TimeoutError as exc:
                elapsed_ms = int((time.time() - start) * 1000) if "start" in locals() else 0
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="forward_message_image_vision",
                    prompt=prompt,
                    completion="",
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=f"timeout after {timeout:.1f}s",
                    budget_exempt=True,
                )
                self._mark_private_image_provider_failure(provider_id, provider_source, exc, task="forward_message_image_vision")
                continue
            except Exception as exc:
                self._record_llm_usage(
                    provider_id=provider_id,
                    task="forward_message_image_vision",
                    prompt=prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000) if "start" in locals() else 0,
                    success=False,
                    error=_single_line(exc, 180),
                    budget_exempt=True,
                )
                self._mark_private_image_provider_failure(provider_id, provider_source, exc, task="forward_message_image_vision")
                continue
        logger.info(
            "[PrivateCompanion] 合并消息图片视觉失败: 所有候选 provider 均不可用或失败 attempts=%s skipped=%s images=%s",
            attempts,
            ",".join(skipped_providers[:8]) or "-",
            len(image_urls),
        )
        return ""

    def _message_obj_reply_message_ids(self, message_obj: Any) -> list[str]:
        ids: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 120)
            if text and text not in ids:
                ids.append(text)

        def visit(value: Any, *, depth: int = 0) -> None:
            if value is None or depth > 8:
                return
            if isinstance(value, str):
                for match in re.finditer(r"\[CQ:reply,[^\]]*(?:id|message_id|msg_id)=([^,\]]+)", value, flags=re.I):
                    add(html.unescape(match.group(1)).strip())
                parsed = self._decode_possible_json_text(value)
                if parsed is not None:
                    visit(parsed, depth=depth + 1)
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, depth=depth + 1)
                return
            type_name = self._component_type_name(value)
            if type_name == "reply" or "reply" in type_name:
                add(self._extract_reply_message_id(value))
            if isinstance(value, dict):
                data = self._component_data(value)
                if data is not value:
                    visit(data, depth=depth + 1)
                for key in ("message", "raw_message", "content", "messages"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested, depth=depth + 1)
                return
            data = self._component_data(value)
            if data:
                visit(data, depth=depth + 1)

        visit(message_obj)
        return ids

    def _message_obj_text_preview(self, message_obj: Any, *, limit: int = 260) -> str:
        parts: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 180)
            if text:
                parts.append(text)

        def visit(value: Any, *, depth: int = 0) -> None:
            if value is None or depth > 6 or len(parts) >= 8:
                return
            if isinstance(value, str):
                cleaned = re.sub(r"\[CQ:reply,[^\]]+\]", "[引用]", value)
                cleaned = re.sub(r"\[CQ:image[^\]]+\]", "[图片]", cleaned)
                cleaned = re.sub(r"\[CQ:record[^\]]+\]", "[语音]", cleaned)
                add(cleaned)
                parsed = self._decode_possible_json_text(value)
                if parsed is not None:
                    visit(parsed, depth=depth + 1)
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, depth=depth + 1)
                return
            type_name = self._component_type_name(value)
            data = self._component_data(value)
            if type_name == "text" or type_name == "plain":
                add(getattr(value, "text", "") or data.get("text") or data.get("content"))
                return
            if type_name == "image":
                add("[图片]")
                return
            if type_name == "record":
                add("[语音]")
                return
            if type_name == "reply" or "reply" in type_name:
                return
            if isinstance(value, dict):
                for key in ("text", "content", "summary", "title", "desc", "prompt"):
                    if key in value:
                        add(value.get(key))
                for key in ("message", "raw_message", "messages", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested, depth=depth + 1)
                return
            for attr in ("text", "message", "content"):
                add(getattr(value, attr, ""))
            if data:
                visit(data, depth=depth + 1)

        visit(message_obj)
        text = " ".join(part for part in parts if part).strip()
        return _single_line(text, limit)

    async def _get_message_obj_by_id(self, event: AstrMessageEvent, message_id: str) -> Any:
        message_id = _single_line(message_id, 120)
        if not message_id:
            return None
        for value in (int(message_id) if str(message_id).isdigit() else None, message_id):
            if value is None:
                continue
            try:
                raw = await self._call_platform_action(event, "get_msg", message_id=value)
            except Exception:
                raw = None
            if raw:
                return raw
        cache = getattr(self, "_recall_message_cache", None)
        snapshot = cache.get(message_id) if isinstance(cache, dict) else None
        if isinstance(snapshot, dict):
            return {
                "message_id": message_id,
                "message": snapshot.get("raw_message") if snapshot.get("raw_message") is not None else snapshot.get("text", ""),
                "raw_message": snapshot.get("raw_message") if snapshot.get("raw_message") is not None else snapshot.get("text", ""),
                "_private_companion_snapshot": snapshot,
            }
        return None

    def _raw_message_from_message_obj(self, message_obj: Any) -> Any:
        if isinstance(message_obj, dict):
            for key in ("message", "raw_message", "content", "messages"):
                value = message_obj.get(key)
                if value is not None:
                    return value
        return message_obj

    async def _reply_message_chain_for_event(self, event: AstrMessageEvent, *, max_depth: int = 3) -> list[dict[str, Any]]:
        cached = getattr(event, "_private_companion_reply_message_chain", None)
        if isinstance(cached, list):
            return [item for item in cached if isinstance(item, dict)]

        direct_ids: list[str] = []
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            message_id = _single_line(self._extract_reply_message_id(item), 120)
            if message_id and message_id not in direct_ids:
                direct_ids.append(message_id)
        queue: list[tuple[str, int]] = [(message_id, 1) for message_id in direct_ids]
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        current_scope = _single_line(self._event_scope_key(event), 160)
        while queue and len(rows) < max(1, max_depth):
            message_id, depth = queue.pop(0)
            message_id = _single_line(message_id, 120)
            if not message_id or message_id in seen or depth > max_depth:
                continue
            seen.add(message_id)
            recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 引用链读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
                continue
            message_obj = await self._get_message_obj_by_id(event, message_id)
            if not message_obj:
                continue
            snapshot = message_obj.get("_private_companion_snapshot") if isinstance(message_obj, dict) else None
            if isinstance(snapshot, dict):
                snapshot_scope = _single_line(snapshot.get("scope"), 160)
                if current_scope and snapshot_scope and snapshot_scope != current_scope:
                    continue
            raw_message = self._raw_message_from_message_obj(message_obj)
            text = self._message_obj_text_preview(raw_message, limit=280)
            rows.append({"message_id": message_id, "depth": depth, "raw_message": raw_message, "text": text})
            next_ids = self._message_obj_reply_message_ids(raw_message)
            if not next_ids and isinstance(snapshot, dict) and isinstance(snapshot.get("reply_message_ids"), list):
                next_ids = [_single_line(item, 120) for item in snapshot.get("reply_message_ids") if _single_line(item, 120)]
            for next_id in next_ids:
                if next_id and next_id not in seen:
                    queue.append((next_id, depth + 1))

        try:
            setattr(event, "_private_companion_reply_message_chain", list(rows))
        except Exception:
            pass
        return rows

    async def _format_reply_chain_context_for_prompt(self, event: AstrMessageEvent) -> str:
        chain = await self._reply_message_chain_for_event(event, max_depth=3)
        if len(chain) <= 1:
            return ""
        lines = [
            "【引用链上下文】",
            "用户这轮回复/引用了一条消息；被引用消息本身还引用了更早的消息。下面按距离当前消息由近到远列出，请优先理解最深层原始消息和用户当前文字之间的关系。",
        ]
        for row in chain:
            depth = _safe_int(row.get("depth"), 1, 1)
            message_id = _single_line(row.get("message_id"), 80)
            text = _single_line(row.get("text"), 280) or "[无可读文字]"
            label = "直接被引用" if depth == 1 else f"第 {depth} 层原始引用"
            lines.append(f"- {label}（{message_id or '无ID'}）：{text}")
        return "\n".join(lines)

    async def _reply_raw_message_for_event(self, event: AstrMessageEvent) -> tuple[str, Any]:
        cached = getattr(event, "_private_companion_reply_raw_message", None)
        if cached is not None:
            cached_id = _single_line(getattr(event, "_private_companion_reply_raw_message_id", ""), 120)
            return cached_id, cached
        chain = await self._reply_message_chain_for_event(event, max_depth=3)
        if chain:
            row = chain[-1]
            message_id = _single_line(row.get("message_id"), 120)
            raw_message = row.get("raw_message")
            try:
                setattr(event, "_private_companion_reply_raw_message", raw_message)
                setattr(event, "_private_companion_reply_raw_message_id", message_id)
            except Exception:
                pass
            return message_id, raw_message
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            message_id = self._extract_reply_message_id(item)
            if not message_id:
                continue
            recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
            if recalled_message_id:
                logger.info("[PrivateCompanion] 引用原消息读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
                return message_id, None
            message_obj = None
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
            except Exception:
                try:
                    message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
                except Exception:
                    message_obj = None
            if not message_obj:
                continue
            raw_message = message_obj.get("message") if isinstance(message_obj, dict) else message_obj
            try:
                setattr(event, "_private_companion_reply_raw_message", raw_message)
                setattr(event, "_private_companion_reply_raw_message_id", message_id)
            except Exception:
                pass
            return message_id, raw_message
        return "", None

    def _extract_reply_rich_card_info(self, message_obj: Any) -> dict[str, Any]:
        texts: list[str] = []
        links: list[str] = []
        images: list[str] = []
        seen_values: set[str] = set()

        def normalize_card_text(value: Any) -> str:
            text = html.unescape(str(value or "")).strip()
            text = text.replace("\\/", "/")
            text = text.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u003f", "?")
            text = text.replace("\\\\", "\\")
            return text

        def looks_like_image_source(value: str) -> bool:
            text = normalize_card_text(value)
            if not text:
                return False
            if text.startswith(("http://", "https://", "file://", "data:")):
                return bool(re.search(r"\.(?:png|jpe?g|gif|webp|bmp)(?:[?#].*)?$", text, re.I) or "/bfs/" in text or "image" in text.lower())
            return bool(
                re.search(r"(?:^|[A-Za-z]:\\|/).+\.(?:png|jpe?g|gif|webp|bmp)$", text, re.I)
            )

        def add_text(value: Any) -> None:
            text = _single_line(normalize_card_text(value), 160)
            if text and text not in seen_values and not text.startswith(("http://", "https://")):
                seen_values.add(text)
                texts.append(text)

        def add_link(value: Any) -> None:
            text = normalize_card_text(value)
            if text and text.startswith(("http://", "https://")) and text not in links:
                links.append(text)

        def add_image(value: Any) -> None:
            text = normalize_card_text(value)
            if text and looks_like_image_source(text) and text not in images:
                images.append(text)

        def visit(value: Any, *, key_hint: str = "", depth: int = 0) -> None:
            if value is None or depth > 8:
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, key_hint=key_hint, depth=depth + 1)
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key or "").lower()
                    if isinstance(child, str):
                        if any(token in key_text for token in ("image", "img", "pic", "picture", "cover", "preview", "thumb", "icon", "file", "path")):
                            add_image(child)
                        if any(token in key_text for token in ("url", "jump", "link", "uri")):
                            add_link(child)
                        if any(token in key_text for token in ("title", "desc", "summary", "content", "text", "prompt", "tag")):
                            add_text(child)
                    visit(child, key_hint=key_text, depth=depth + 1)
                return
            raw = str(value or "").strip()
            if not raw:
                return
            unescaped = normalize_card_text(raw)
            compact = unescaped.strip()
            if compact.startswith(("{", "[")):
                try:
                    visit(json.loads(compact), key_hint=key_hint, depth=depth + 1)
                    return
                except Exception:
                    pass
            quoted_json = compact
            if (quoted_json.startswith('"') and quoted_json.endswith('"')) or (quoted_json.startswith("'") and quoted_json.endswith("'")):
                try:
                    visit(json.loads(quoted_json), key_hint=key_hint, depth=depth + 1)
                    return
                except Exception:
                    pass
            for url in re.findall(r"https?://[^\s\"'<>\\)）]+", unescaped):
                add_link(url)
                if looks_like_image_source(url):
                    add_image(url)
            for file_path in re.findall(r"[A-Za-z]:\\[^\s\"'<>|]+?\.(?:png|jpe?g|gif|webp|bmp)", unescaped, re.I):
                add_image(file_path)
            for file_uri in re.findall(r"file://[^\s\"'<>]+?\.(?:png|jpe?g|gif|webp|bmp)", unescaped, re.I):
                add_image(file_uri)
            for cq_match in re.finditer(r"\[CQ:image,([^\]]+)\]", unescaped):
                fields: dict[str, str] = {}
                for part in cq_match.group(1).split(","):
                    if "=" not in part:
                        continue
                    key, val = part.split("=", 1)
                    fields[key.strip()] = normalize_card_text(val)
                for key in ("url", "file", "path"):
                    candidate = fields.get(key)
                    if candidate:
                        if looks_like_image_source(candidate):
                            add_image(candidate)
                        elif candidate.startswith(("http://", "https://")):
                            add_link(candidate)
            for image_match in re.findall(
                r'"(?:image|img|pic|picture|cover|preview|thumb|icon|src|url|file|path)"\s*:\s*"([^"]+)"',
                unescaped,
                re.I,
            ):
                normalized_image = normalize_card_text(image_match)
                if looks_like_image_source(normalized_image):
                    add_image(normalized_image)
                elif normalized_image.startswith(("http://", "https://")):
                    add_link(normalized_image)
            if key_hint in {"data"} and ("bilibili" in unescaped or "哔哩" in unescaped or "明日方舟" in unescaped):
                for text_match in re.findall(r'"(?:title|desc|summary|content|text|prompt)"\s*:\s*"([^"]{2,160})"', unescaped):
                    add_text(text_match)

        visit(message_obj)
        return {"texts": texts[:8], "links": links[:8], "images": images[:6]}

    @staticmethod
    def _reply_rich_card_music_album_context(texts: list[str], links: list[str]) -> dict[str, str]:
        joined = " ".join([*texts, *links])
        compact = re.sub(r"\s+", "", joined)
        if not compact:
            return {}
        if not any(token in compact for token in ("网易云音乐", "专辑", "歌手", "歌曲", "曲目", "歌单", "music.163.com")):
            return {}
        album = ""
        artist = ""
        platform = ""
        for text in texts:
            normalized = _single_line(text, 120)
            if not album:
                match = re.search(r"(?:专辑|album)\s*[：:]\s*([^\s，。！？!?]{2,60})", normalized, re.I)
                if match:
                    album = _single_line(match.group(1), 60)
            if not artist:
                match = re.search(r"(?:歌手|artist)\s*[：:]\s*([^\s，。！？!?]{2,40})", normalized, re.I)
                if match:
                    artist = _single_line(match.group(1), 40)
            if not platform and any(token in normalized for token in ("网易云音乐", "music.163.com")):
                platform = "网易云音乐"
        if not album and not artist and not platform:
            return {}
        return {
            "album": album,
            "artist": artist,
            "platform": platform,
            "summary": "；".join(
                part for part in (
                    f"专辑：{album}" if album else "",
                    f"歌手：{artist}" if artist else "",
                    platform or "",
                )
                if part
            ),
        }

    async def _format_reply_rich_card_context_for_prompt(self, event: AstrMessageEvent) -> str:
        message_id, raw_message = await self._reply_raw_message_for_event(event)
        if raw_message is None:
            return ""
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
        if recalled_message_id:
            logger.info("[PrivateCompanion] 引用卡片上下文跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
            return ""
        info = self._extract_reply_rich_card_info(raw_message)
        texts = [item for item in info.get("texts", []) if item]
        links = [item for item in info.get("links", []) if item]
        images = [item for item in info.get("images", []) if item]
        if not (texts or links or images):
            return ""
        music_context = self._reply_rich_card_music_album_context(texts, links)
        image_vision_text = await self._transcribe_forward_message_images(event, images)
        lines = [
            "【本轮引用卡片/动态】",
            "这轮用户引用了一条卡片/动态，内容如下：",
        ]
        if message_id:
            lines.append(f"引用消息ID：{message_id}")
        if texts:
            lines.append("卡片文字：" + "；".join(texts[:5]))
            compact_text = re.sub(r"\s+", "", " ".join(texts))
            if "最近撤回消息" in compact_text and "撤回" in compact_text:
                recalled_rows = self._recent_recalled_messages_for_scope(self._event_scope_key(event), limit=5)
                status_parts = [self._recall_image_status_summary(row) for row in recalled_rows]
                status_text = "；".join(part for part in status_parts if part)
                if status_text:
                    lines.append(
                        f"引用内容是插件的撤回查询摘要，不是原始撤回消息本体；当前短期缓存图片状态：{status_text}。"
                        "不要把摘要里的[图片]当成已看见原图。"
                    )
                else:
                    lines.append("引用内容是插件的撤回查询摘要，不是原始撤回消息本体；摘要里的[图片]只表示对方撤回过图片。")
        if links:
            lines.append("卡片链接：" + "；".join(links[:4]))
        if images:
            lines.append(f"卡片图片数：{len(images)}")
        if music_context:
            lines.append("音乐卡片识别：")
            lines.append(
                "这是一张音乐专辑/点歌卡片，卡片里的专辑名和歌手名应当直接视为有效线索；"
                "如果用户是在让你发出这张专辑的几首歌，不要再追问“哪个专辑”，优先按卡片里的信息继续。"
            )
            lines.append(f"音乐卡片摘要：{music_context.get('summary') or '（未提取到完整信息）'}")
            try:
                setattr(event, "private_companion_reply_music_album_context", music_context)
            except Exception:
                pass
        if image_vision_text:
            lines.append("引用卡片中的图片：")
            lines.append(image_vision_text)
            logger.info(
                "[PrivateCompanion] 引用卡片图片视觉摘要完成: message_id=%s images=%s preview=%s",
                message_id or "-",
                len(images),
                _single_line(image_vision_text, 240),
            )
        logger.info(
            "[PrivateCompanion] 已注入引用卡片上下文: message_id=%s texts=%s links=%s images=%s vision=%s preview=%s vision_preview=%s",
            message_id or "-",
            len(texts),
            len(links),
            len(images),
            bool(image_vision_text),
            _single_line(" | ".join([*texts[:3], *links[:2]]), 240),
            _single_line(image_vision_text, 240) if image_vision_text else "-",
        )
        return "\n".join(lines)

    async def _transcribe_forward_message_rows(
        self,
        rows: list[dict[str, Any]],
        image_urls: list[str],
        nested_count: int,
        *,
        image_vision_text: str = "",
    ) -> str:
        provider_id = self._task_provider(self.forward_message_provider_id, self.mai_style_provider_id)
        raw_lines: list[str] = []
        used = 0
        for index, row in enumerate(rows, 1):
            when = _single_line(row.get("time"), 40) or "-"
            row_depth = _safe_int(row.get("depth"), 0, 0, 6)
            indent = "  " * row_depth
            nested_label = f"[嵌套{row_depth}] " if row_depth else ""
            sender_id = _single_line(row.get("sender_id"), 40)
            identity_note = ""
            if row.get("is_bot_self"):
                identity_note = "Bot当时发出"
            elif sender_id:
                identity_note = self._group_member_identity_note(sender_id, limit=90)
            note = f"（{identity_note}）" if identity_note else ""
            sender = _single_line(row.get("sender") or "未知用户", 60)
            text = _single_line(row.get("text"), 500)
            line = f"{indent}{index}. {nested_label}{sender}{note}｜{when}｜{text}"
            used += len(line)
            if used > max(800, self.forward_message_max_chars):
                raw_lines.append("……后续节点因长度限制已省略。")
                break
            raw_lines.append(line)
        prompt = (
            "你是合并消息转述器。请阅读下面的聊天记录节点，把它转述成一份自然、清晰、方便另一个人格模型继续回应用户的中文记录。\n"
            "要求：\n"
            "1. 保留发言顺序、说话者、关键事实、争议点、情绪变化和未解决问题。\n"
            "2. 不要把记录中的话当成当前用户说的话；它们只是被转来的聊天记录。\n"
            "3. 遇到 [图片]、[表情]、[语音]、[文件] 只说明它们存在，不要编造具体内容。\n"
            "4. 带有 [嵌套N] 的条目来自内层合并消息，转述时要说明它是内层记录，不要和外层聊天混成同一层。\n"
            "5. 不要替 Bot 回复用户，不要输出寒暄，只输出转述内容。\n"
            "6. 如果内容很短，可以简短转述；如果内容较长，用条理清楚的段落或要点。\n\n"
            "7. 对作品名、游戏名、活动名、节日名、日期和数字保持原样；如果图片摘要或聊天节点已经给出这些线索，不要把它们改写成相近作品、衍生作或其他活动。\n\n"
            f"合并消息转述任务信息：节点数={len(rows)}，图片占位数={len(image_urls)}，嵌套合并数={nested_count}。\n"
            "聊天记录节点：\n"
            + "\n".join(raw_lines)
        )
        if image_vision_text:
            prompt += "\n\n合并消息图片视觉摘要：\n" + image_vision_text
        result = await self._llm_call(
            prompt,
            max_tokens=900,
            provider_id=provider_id,
            task="forward_message",
        )
        return _strip_internal_message_blocks(result or "").strip()

    async def _append_forward_message_context_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_forward_message_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        should_log_probe = any(token in message_text for token in ("转发", "合并消息", "聊天记录"))
        if marker in current_prompt or marker in current_turn_prompt:
            if should_log_probe:
                logger.info("[PrivateCompanion] 合并消息请求注入跳过: 已存在 marker text=%s", message_text or "(empty)")
            return
        if should_log_probe:
            logger.info("[PrivateCompanion] 合并消息请求开始注入检查: text=%s", message_text or "(empty)")
        context = await self._format_forward_message_context_for_prompt(event, req)
        if context:
            try:
                setattr(event, "private_companion_forward_context_injected", True)
            except Exception:
                pass
            placement = "system_prompt"
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                try:
                    placement = "prompt" if helper(req, marker, context, priority=65, source="forward_message") else "system_prompt"
                except TypeError:
                    placement = "prompt" if helper(req, marker, context) else "system_prompt"
            if placement == "system_prompt":
                req.system_prompt = f"{current_prompt}\n\n{marker}\n{context}".strip()
            recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(recorder):
                await recorder(
                    event,
                    title="合并转发上下文注入",
                    key="forward.message",
                    text=context,
                    source="forward_message",
                    mode="forward",
                    metadata={"注入位置": placement},
                )
            return
        reply_chain_context = await self._format_reply_chain_context_for_prompt(event)
        if reply_chain_context:
            chain_marker = "<!-- private_companion_reply_chain_v1 -->"
            placement = "system_prompt"
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                try:
                    placement = "prompt" if helper(req, chain_marker, reply_chain_context, priority=64, source="forward_message") else "system_prompt"
                except TypeError:
                    placement = "prompt" if helper(req, chain_marker, reply_chain_context) else "system_prompt"
            if placement == "system_prompt":
                req.system_prompt = f"{req.system_prompt or ''}\n\n{chain_marker}\n{reply_chain_context}".strip()
            try:
                setattr(event, "private_companion_reply_chain_context_injected", True)
            except Exception:
                pass
            recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(recorder):
                await recorder(
                    event,
                    title="引用链上下文注入",
                    key="reply.chain",
                    text=reply_chain_context,
                    source="forward_message",
                    mode="reply_chain",
                    metadata={"注入位置": placement},
                )
        rich_card_context = await self._format_reply_rich_card_context_for_prompt(event)
        if rich_card_context:
            placement = "system_prompt"
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                try:
                    placement = "prompt" if helper(req, marker, rich_card_context, priority=65, source="forward_message") else "system_prompt"
                except TypeError:
                    placement = "prompt" if helper(req, marker, rich_card_context) else "system_prompt"
            if placement == "system_prompt":
                req.system_prompt = f"{current_prompt}\n\n{marker}\n{rich_card_context}".strip()
            recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(recorder):
                await recorder(
                    event,
                    title="引用卡片上下文注入",
                    key="forward.message",
                    text=rich_card_context,
                    source="forward_message",
                    mode="rich_card",
                    metadata={"注入位置": placement},
                )
        elif should_log_probe:
            logger.info("[PrivateCompanion] 合并消息请求未生成上下文: text=%s", message_text or "(empty)")

