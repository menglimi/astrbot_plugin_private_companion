# -*- coding: utf-8 -*-
"""ForwardMessageMessageObjInspectMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 317 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

import html
import re
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .forward_message_shared import _single_line



class ForwardMessageMessageObjInspectMixin:
    """ForwardMessageMessageObjInspectMixin（从 ForwardMessageMixin 拆出）。"""


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

    def _message_obj_sender_info(
        self,
        message_obj: Any,
        *,
        snapshot: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Read original-message author metadata without inspecting message content."""
        sources: list[Any] = []
        if isinstance(message_obj, dict):
            sources.append(message_obj)
            for key in ("data", "result"):
                nested = message_obj.get(key)
                if isinstance(nested, dict):
                    sources.append(nested)
        elif message_obj is not None:
            sources.append(message_obj)
        if isinstance(snapshot, dict):
            sources.append(snapshot)

        sender_id = ""
        sender_name = ""
        for source in sources:
            if isinstance(source, dict):
                sender = source.get("sender")
                if isinstance(sender, dict):
                    sender_id = sender_id or _single_line(
                        sender.get("user_id") or sender.get("sender_id") or sender.get("uin") or sender.get("id"),
                        80,
                    )
                    sender_name = sender_name or _single_line(
                        sender.get("card") or sender.get("nickname") or sender.get("name"),
                        60,
                    )
                elif sender is not None and not isinstance(sender, (list, tuple, set)):
                    sender_id = sender_id or _single_line(sender, 80)
                sender_id = sender_id or _single_line(
                    source.get("sender_id") or source.get("user_id") or source.get("sender_uin") or source.get("uin"),
                    80,
                )
                sender_name = sender_name or _single_line(
                    source.get("sender_name") or source.get("sender_nickname") or source.get("nickname") or source.get("card"),
                    60,
                )
            else:
                sender = getattr(source, "sender", None)
                if sender is not None:
                    sender_id = sender_id or _single_line(
                        getattr(sender, "user_id", "")
                        or getattr(sender, "sender_id", "")
                        or getattr(sender, "uin", "")
                        or getattr(sender, "id", ""),
                        80,
                    )
                    sender_name = sender_name or _single_line(
                        getattr(sender, "card", "")
                        or getattr(sender, "nickname", "")
                        or getattr(sender, "name", ""),
                        60,
                    )
                sender_id = sender_id or _single_line(
                    getattr(source, "sender_id", "") or getattr(source, "user_id", "") or getattr(source, "uin", ""),
                    80,
                )
                sender_name = sender_name or _single_line(
                    getattr(source, "sender_name", "")
                    or getattr(source, "sender_nickname", "")
                    or getattr(source, "nickname", ""),
                    60,
                )
            if sender_id and sender_name:
                break
        return sender_id, sender_name

    def _message_obj_media_types(self, message_obj: Any) -> list[str]:
        media_types: list[str] = []

        def add(label: str) -> None:
            if label and label not in media_types:
                media_types.append(label)

        def add_type(type_name: str) -> None:
            normalized = str(type_name or "").strip().lower()
            if normalized in {"record", "audio", "voice", "voice_message"}:
                add("语音")
            elif normalized in {"image", "photo", "picture"}:
                add("图片")
            elif normalized in {"video", "short_video"}:
                add("视频")
            elif normalized == "file":
                add("文件")
            elif normalized in {"json", "xml", "share", "app"}:
                add("卡片")

        def visit(value: Any, *, depth: int = 0) -> None:
            if value is None or depth > 7:
                return
            if isinstance(value, str):
                lowered = value.lower()
                if "[cq:record" in lowered or "[语音]" in value:
                    add("语音")
                if "[cq:image" in lowered or "[图片]" in value:
                    add("图片")
                if "[cq:video" in lowered or "[视频]" in value:
                    add("视频")
                parsed = self._decode_possible_json_text(value)
                if parsed is not None:
                    visit(parsed, depth=depth + 1)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit(item, depth=depth + 1)
                return
            add_type(self._component_type_name(value))
            if isinstance(value, dict):
                for key in ("message", "raw_message", "content", "messages", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested, depth=depth + 1)
                return
            data = self._component_data(value)
            if data:
                visit(data, depth=depth + 1)

        visit(message_obj)
        return media_types

    def _message_obj_known_tts_voice_text(
        self,
        message_obj: Any,
        *,
        snapshot: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        if isinstance(snapshot, dict):
            cached_spoken = _single_line(snapshot.get("tts_spoken_text"), 500)
            cached_source = _single_line(snapshot.get("tts_source_text"), 500)
            if cached_spoken:
                return cached_spoken, cached_source

        lookup = getattr(self, "_lookup_tts_record_text", None)
        matches: list[tuple[str, str]] = []

        def add_component(component: Any) -> None:
            spoken = _single_line(getattr(component, "_private_companion_tts_spoken_text", ""), 500)
            source = _single_line(getattr(component, "_private_companion_tts_source_text", ""), 500)
            if not spoken and callable(lookup):
                try:
                    spoken, source = lookup(component)
                except Exception:
                    spoken, source = "", ""
            if spoken and (spoken, source) not in matches:
                matches.append((spoken, source))

        def visit(value: Any, *, depth: int = 0) -> None:
            if value is None or depth > 7 or matches:
                return
            if isinstance(value, str):
                for match in re.finditer(r"\[CQ:record,([^\]]+)\]", value, flags=re.I):
                    data: dict[str, str] = {}
                    for part in match.group(1).split(","):
                        if "=" not in part:
                            continue
                        key, raw_value = part.split("=", 1)
                        data[key.strip()] = html.unescape(raw_value.strip())
                    add_component({"type": "record", "data": data})
                    if matches:
                        return
                parsed = self._decode_possible_json_text(value)
                if parsed is not None:
                    visit(parsed, depth=depth + 1)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit(item, depth=depth + 1)
                    if matches:
                        return
                return
            type_name = self._component_type_name(value)
            if type_name in {"record", "voice", "audio", "voice_message"}:
                add_component(value)
                if matches:
                    return
            if isinstance(value, dict):
                for key in ("message", "raw_message", "content", "messages", "data"):
                    nested = value.get(key)
                    if nested is not value:
                        visit(nested, depth=depth + 1)
                    if matches:
                        return
                return
            data = self._component_data(value)
            if data:
                visit(data, depth=depth + 1)

        visit(message_obj)
        return matches[0] if matches else ("", "")
