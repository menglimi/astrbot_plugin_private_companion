# -*- coding: utf-8 -*-
"""ForwardMessageDescriptorExtractMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 397 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .forward_message_shared import PLACEMENT_DYNAMIC_SYSTEM
from .forward_message_shared import PromptSection
from .forward_message_shared import ProviderRequest
from .forward_message_shared import _safe_float
from .forward_message_shared import _single_line
from .forward_message_shared import get_conversation_injection_plan



class ForwardMessageDescriptorExtractMixin:
    """ForwardMessageDescriptorExtractMixin（从 ForwardMessageMixin 拆出）。"""


    @staticmethod
    def _reply_actor_binding_prompt_lines() -> list[str]:
        return [
            "引用内容属于原消息作者，用户当前文字属于本轮发言者；理解时请区分当前发言、引用作者和被提到的第三方。",
            "人物与动作优先逐项对应：用户说“让甲做某事”通常只是提议由甲执行该动作；引用中信使、第三方或地点里的动作仍沿用原对象，除非上下文明确说明它们是同一个人。",
            "旧记忆用于补充语气和连续性；若人物关系与本轮引用不一致，优先采用当前文字和直接引用。",
        ]

    @staticmethod
    def _materialize_forward_context(
        req: ProviderRequest,
        *,
        section: PromptSection,
        marker: str,
        priority: int,
    ) -> bool:
        plan = get_conversation_injection_plan(req)
        if plan is None or plan.contains_marker(marker):
            return False
        return plan.materialize_system_block(
            req,
            section=section,
            marker=marker,
            priority=priority,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )

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
