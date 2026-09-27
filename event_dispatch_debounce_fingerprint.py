# -*- coding: utf-8 -*-
"""EventDispatchDebounceFingerprintMixin。

由 tools/split_mixin_domain.py 从 event_dispatch.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 546 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .event_dispatch_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from typing import Any



class EventDispatchDebounceFingerprintMixin:
    """EventDispatchDebounceFingerprintMixin（从 EventDispatchMixin 拆出）。"""


    def _strip_internal_identity_anchors(self, text: str) -> str:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return str(text or "")
        cleaned = str(text or "")
        cleaned = re.sub(r"\[QQ:\d{5,12}\]", "", cleaned)
        cleaned = re.sub(r"(?<![\w])QQ[:：]\d{5,12}", "", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        return cleaned.strip()

    def _event_component_stable_fingerprint_part(self, item: Any) -> str:
        class_name = item.__class__.__name__.lower()
        if isinstance(item, dict):
            class_name = str(item.get("type") or item.get("post_type") or "dict").lower()
            data = item.get("data") if isinstance(item.get("data"), dict) else item
        else:
            data = getattr(item, "data", None)
            if not isinstance(data, dict):
                data = {}
        if class_name == "image":
            values: list[str] = []
            for attr in (
                "file_unique",
                "file_id",
                "md5",
                "sha1",
                "file",
                "summary",
                "url",
            ):
                value = data.get(attr) if isinstance(data, dict) and attr in data else getattr(item, attr, None)
                text = _single_line(value, 260)
                if not text:
                    continue
                if attr == "url":
                    text = re.sub(r"[?#].*$", "", text).rstrip("/")
                    text = text.rsplit("/", 1)[-1] or text
                values.append(f"{attr}={text}")
            if values:
                return "Image:" + "|".join(values[:4])
            return "Image"
        text = (
            getattr(item, "text", None)
            or getattr(item, "message", None)
            or getattr(item, "content", None)
            or (data.get("text") if isinstance(data, dict) else "")
            or (data.get("file") if isinstance(data, dict) else "")
            or (data.get("id") if isinstance(data, dict) else "")
        )
        return f"{item.__class__.__name__}:{_single_line(text, 160)}"

    def _event_has_media_component(self, event: AstrMessageEvent) -> bool:
        for item in self._event_components(event):
            class_name = item.__class__.__name__.lower()
            if isinstance(item, dict):
                class_name = str(item.get("type") or "").lower()
            if class_name in {"image", "record", "video", "file", "face"}:
                return True
        raw = str(getattr(event, "message_str", "") or "")
        return bool(re.search(r"\[CQ:(?:image|record|video|file|face)\b", raw))

    def _event_content_fingerprint(self, event: AstrMessageEvent, text: str) -> str:
        pieces = [_single_line(text, 260)]
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            chain = getattr(message_obj, "message", None)
            if isinstance(chain, list):
                pieces.extend(self._event_component_stable_fingerprint_part(item) for item in chain[:8])
            raw = getattr(message_obj, "raw_message", None)
            if raw and not any(part.startswith("Image:") for part in pieces):
                normalized = re.sub(r"(?:url|cache|proxy|token|file_size|size)=[^,\]]+", "", str(raw)[:800])
                pieces.append(normalized)
        elif self._event_components(event):
            pieces.extend(self._event_component_stable_fingerprint_part(item) for item in self._event_components(event)[:8])
        source = "\n".join(part for part in pieces if part)
        if not source:
            source = _single_line(getattr(event, "message_str", ""), 260) or "empty"
        return hashlib.sha1(source.encode("utf-8", errors="ignore")).hexdigest()

    def _note_inbound_debounce_hit(self, *, kind: str, scope: str, sender_id: str, text: str, now: float) -> None:
        stats = self.data.setdefault("inbound_debounce_stats", {})
        if not isinstance(stats, dict):
            stats = {}
            self.data["inbound_debounce_stats"] = stats
        today = _today_key()
        if stats.get("day") != today:
            stats.clear()
            stats["day"] = today
            stats["total"] = 0
            stats["by_kind"] = {}
            stats["recent"] = []
        stats["total"] = _safe_int(stats.get("total"), 0, 0) + 1
        by_kind = stats.setdefault("by_kind", {})
        if not isinstance(by_kind, dict):
            by_kind = {}
            stats["by_kind"] = by_kind
        by_kind[kind] = _safe_int(by_kind.get(kind), 0, 0) + 1
        recent = stats.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            stats["recent"] = recent
        recent.append(
            {
                "ts": now,
                "kind": kind,
                "scope": _single_line(scope, 80),
                "sender_id": _single_line(sender_id, 40),
                "text": _single_line(text, 80),
            }
        )
        del recent[:-20]

    def _is_duplicate_inbound_message(
        self,
        event: AstrMessageEvent,
        *,
        scope: str,
        sender_id: str,
        text: str,
        now: float | None = None,
    ) -> bool:
        seconds = max(0.0, float(_persona_value(self, 'inbound_message_debounce_seconds', 0.0) or 0.0))
        if seconds <= 0:
            return False
        now = now or _now_ts()
        cache = getattr(self, "_recent_inbound_message_debounce", None)
        if not isinstance(cache, dict):
            cache = {}
            self._recent_inbound_message_debounce = cache
        id_window = max(60.0, seconds)
        fp_window = seconds
        prune_before = now - max(90.0, id_window, fp_window * 8)
        for key, ts in list(cache.items()):
            if _safe_float(ts, 0) < prune_before:
                cache.pop(key, None)
        message_id = self._event_message_id(event)
        media_like = self._event_has_media_component(event) or not _single_line(text, 40) or _single_line(text, 40) in {"[图片]", "【图片】", "图片"}
        checks: list[tuple[str, float, str]] = []
        if message_id:
            checks.append((f"id:{scope}:{sender_id}:{message_id}", id_window, "message_id"))
        if (not message_id) or media_like:
            checks.append((f"fp:{scope}:{sender_id}:{self._event_content_fingerprint(event, text)}", fp_window, "fingerprint"))
        if not checks:
            return False
        for key, window, kind in checks:
            last = _safe_float(cache.get(key), 0)
            if last <= 0 or now - last > window:
                continue
            logger.info(
                "用户消息防抖拦截: kind=%s scope=%s sender=%s msg_id=%s text=%s",
                kind,
                scope,
                sender_id,
                message_id or "-",
                _single_line(text, 80),
            )
            self._note_inbound_debounce_hit(kind=kind, scope=scope, sender_id=sender_id, text=text, now=now)
            cache[key] = now
            return True
        for key, _, _ in checks:
            cache[key] = now
        if len(cache) > 500:
            for key, _ in sorted(cache.items(), key=lambda item: _safe_float(item[1], 0))[:100]:
                cache.pop(key, None)
        return False

    def _semantic_buffer_key(self, scope: str, sender_id: str) -> str:
        return f"{scope}:{sender_id}"

    def _semantic_buffer_identity(self, key: str) -> tuple[str, str]:
        cleaned = str(key or "")
        if ":" not in cleaned:
            return cleaned, ""
        scope, sender_id = cleaned.rsplit(":", 1)
        return scope, sender_id

    def _group_high_intensity_buffer_key(self, group_id: str, sender_id: str = "") -> str:
        scope = str(_persona_value(self, "group_high_intensity_merge_scope", "group") or "group").lower()
        if scope == "same_user" and sender_id:
            return self._semantic_buffer_key(f"group:{group_id}:__high_intensity_sender__", str(sender_id))
        return self._semantic_buffer_key(f"group:{group_id}", "__high_intensity__")

    def _group_high_intensity_merge_wait_seconds(self) -> float:
        return max(1.0, min(30.0, float(_persona_value(self, "group_high_intensity_merge_seconds", 8) or 8)))

    def _message_debounce_max_wait_seconds(self, kind: str = "text") -> float:
        if kind not in {"text", "group_text"}:
            return 0.0
        return max(0.0, _safe_float(_persona_value(self, 'text_message_debounce_max_wait_seconds', 12.0), 12.0, 0.0))

    def _message_debounce_max_merge_messages(self, kind: str = "text") -> int:
        if kind == "group_high_intensity":
            return max(0, _safe_int(_persona_value(self, "group_high_intensity_max_merge_messages", 8), 8, 0))
        return max(0, _safe_int(_persona_value(self, 'message_debounce_max_merge_messages', 8), 8, 0))

    def _semantic_buffer_active_snapshot(self, key: str, *, wait_seconds: float | None = None, force: bool = False) -> dict[str, Any]:
        default_wait = self._message_debounce_seconds("text") if hasattr(self, "_message_debounce_seconds") else _persona_value(self, 'semantic_message_debounce_seconds', 0.0)
        wait = max(0.0, float(wait_seconds if wait_seconds is not None else default_wait or 0.0))
        if (not force and not bool(_persona_value(self, 'enable_message_debounce', _persona_value(self, 'enable_semantic_message_debounce', True)))) or wait <= 0:
            return {}
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            return {}
        buffer = buffers.get(key)
        if not isinstance(buffer, dict):
            return {}
        wait = max(0.0, _safe_float(buffer.get("wait_seconds"), wait, 0.0) or wait)
        now = _now_ts()
        first_ts = _safe_float(buffer.get("first_ts"), 0.0, 0.0)
        updated_ts = _safe_float(buffer.get("updated_ts"), first_ts, first_ts)
        max_deadline_ts = _safe_float(buffer.get("max_deadline_ts"), 0.0, 0.0)
        target_ts = updated_ts + wait
        if max_deadline_ts > 0:
            target_ts = min(target_ts, max_deadline_ts)
        if first_ts <= 0 or now - target_ts > 0.8:
            return {}
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        texts = []
        for item in messages[-8:]:
            if isinstance(item, dict):
                text = _single_line(item.get("text"), 260)
                if text:
                    texts.append(text)
        return {
            "active": True,
            "kind": _single_line(buffer.get("kind"), 40),
            "first_ts": first_ts,
            "updated_ts": updated_ts,
            "remaining": max(0.0, target_ts - now),
            "texts": texts,
        }

    def _note_semantic_message_buffer(
        self,
        key: str,
        text: str,
        *,
        sender_name: str = "",
        now: float | None = None,
        wait_seconds: float | None = None,
        force: bool = False,
        smart_debounce: dict[str, Any] | None = None,
        kind: str = "text",
    ) -> bool:
        if not force and not bool(_persona_value(self, 'enable_message_debounce', _persona_value(self, 'enable_semantic_message_debounce', True))):
            return False
        default_wait = self._message_debounce_seconds("text") if hasattr(self, "_message_debounce_seconds") else _persona_value(self, 'semantic_message_debounce_seconds', 0.0)
        wait = max(0.0, float(wait_seconds if wait_seconds is not None else default_wait or 0.0))
        if wait <= 0:
            return False
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        now = now or _now_ts()
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            buffers = {}
            self._semantic_message_buffers = buffers
        for item_key, item in list(buffers.items()):
            if not isinstance(item, dict) or now - _safe_float(item.get("updated_ts"), item.get("first_ts"), 0) > max(20.0, wait + 8.0):
                buffers.pop(item_key, None)
        current = buffers.get(key)
        scope, sender_id = self._semantic_buffer_identity(key)
        debounce_mode = "smart" if isinstance(smart_debounce, dict) and smart_debounce.get("enabled") else "fixed"
        buffer_kind = _single_line(kind, 40) or "text"
        max_merge_messages = self._message_debounce_max_merge_messages(buffer_kind)
        max_wait_seconds = self._message_debounce_max_wait_seconds(buffer_kind)
        if isinstance(current, dict) and now - _safe_float(current.get("updated_ts"), current.get("first_ts"), 0) <= wait + 0.8:
            current_deadline_ts = _safe_float(current.get("deadline_ts"), 0.0, 0.0)
            if current_deadline_ts > 0 and now >= current_deadline_ts:
                messages = current.setdefault("messages", [])
                if not isinstance(messages, list):
                    messages = []
                    current["messages"] = messages
                if cleaned not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                    messages.append({"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)})
                current["deadline_ts"] = now
                logger.info(
                    "消息收口固定窗口已到,准备立刻收口: kind=%s scope=%s sender=%s count=%s text=%s",
                    buffer_kind,
                    scope,
                    sender_id,
                    len(messages),
                    _single_line(cleaned, 80),
                )
                return True
            current_max_deadline_ts = _safe_float(current.get("max_deadline_ts"), 0.0, 0.0)
            if current_max_deadline_ts > 0 and now >= current_max_deadline_ts:
                messages = current.setdefault("messages", [])
                if not isinstance(messages, list):
                    messages = []
                    current["messages"] = messages
                if cleaned not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                    messages.append({"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)})
                current["deadline_ts"] = now
                current["max_wait_reached"] = True
                logger.info(
                    "消息收口达到最长等待,准备立刻收口: kind=%s scope=%s sender=%s max_wait=%.1fs count=%s text=%s",
                    buffer_kind,
                    scope,
                    sender_id,
                    max_wait_seconds,
                    len(messages),
                    _single_line(cleaned, 80),
                )
                return True
            current["wait_seconds"] = wait
            current["kind"] = buffer_kind
            if buffer_kind in {"image", "forward", "group_high_intensity", "group_short_wakeup"} and _safe_float(current.get("deadline_ts"), 0) <= 0:
                first_ts = _safe_float(current.get("first_ts"), now, now)
                current["deadline_ts"] = first_ts + wait
            if max_wait_seconds > 0 and _safe_float(current.get("max_deadline_ts"), 0.0) <= 0:
                first_ts = _safe_float(current.get("first_ts"), now, now)
                current["max_deadline_ts"] = first_ts + max_wait_seconds
            if smart_debounce:
                current["smart_debounce"] = dict(smart_debounce)
            messages = current.setdefault("messages", [])
            if not isinstance(messages, list):
                messages = []
                current["messages"] = messages
            appended = False
            if cleaned not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                messages.append({"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)})
                appended = True
            if appended:
                current["updated_ts"] = now
            if max_merge_messages > 0 and len(messages) >= max_merge_messages:
                current["deadline_ts"] = now
                current["max_merge_reached"] = True
                logger.info(
                    "消息收口达到最大合并条数,准备立刻收口: kind=%s scope=%s sender=%s max=%s text=%s",
                    buffer_kind,
                    scope,
                    sender_id,
                    max_merge_messages,
                    _single_line(cleaned, 80),
                )
            logger.info(
                "消息收口合并补话: kind=%s mode=%s scope=%s sender=%s wait=%.1fs count=%s appended=%s text=%s",
                buffer_kind,
                debounce_mode,
                scope,
                sender_id,
                wait,
                len(messages),
                appended,
                _single_line(cleaned, 80),
            )
            return True
        buffer = {
            "first_ts": now,
            "updated_ts": now,
            "wait_seconds": wait,
            "kind": buffer_kind,
            "messages": [{"ts": now, "text": cleaned, "sender_name": _single_line(sender_name, 40)}],
        }
        if buffer_kind in {"group_high_intensity", "group_short_wakeup"}:
            buffer["deadline_ts"] = now + wait
        if buffer_kind in {"image", "forward"}:
            buffer["deadline_ts"] = now + wait
        if max_wait_seconds > 0:
            buffer["max_deadline_ts"] = now + max_wait_seconds
        buffers[key] = buffer
        if smart_debounce:
            buffers[key]["smart_debounce"] = dict(smart_debounce)
        logger.info(
            "消息收口创建缓冲: kind=%s mode=%s scope=%s sender=%s wait=%.1fs text=%s",
            buffer_kind,
            debounce_mode,
            scope,
            sender_id,
            wait,
            _single_line(cleaned, 80),
        )
        return False

    def _smart_message_debounce_enabled(self) -> bool:
        return bool(_persona_value(self, 'enable_message_debounce', True)) and bool(_persona_value(self, 'enable_smart_message_debounce', False))

    def _message_debounce_command_text(self, event: AstrMessageEvent, text: str) -> bool:
        """Command-like messages should not be delayed or merged as chat follow-ups."""
        if bool(getattr(event, "is_command", False)) or bool(getattr(event, "is_admin_command", False)):
            return True
        # AstrBot 会先移除唤醒前缀，且不一定设置 is_command。以已通过权限、
        # 会话插件筛选的指令处理器为准；普通消息监听器也会出现在此列表中。
        # 不重新执行 filter，避免改写框架已经解析好的参数。
        extra_getter = getattr(event, "get_extra", None)
        try:
            handlers = extra_getter("activated_handlers") if callable(extra_getter) else None
        except Exception:
            handlers = None
        if isinstance(handlers, (list, tuple)) and any(
            isinstance(handler_filter, (CommandFilter, CommandGroupFilter))
            for handler in handlers
            for handler_filter in (getattr(handler, "event_filters", None) or ())
        ):
            return True
        cleaned = _single_line(text, 260).strip()
        if not cleaned:
            return False
        if cleaned.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴", "陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return True
        if cleaned.startswith(("/", "／", "!", "！", "#")) and re.search(r"[\w\u4e00-\u9fff]", cleaned[1:]):
            return True
        return False

    def _message_debounce_pending_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_message_debounce_pending_llm", None)
        if not isinstance(store, dict):
            store = {}
            self._message_debounce_pending_llm = store
        now = _now_ts()
        for key, item in list(store.items()):
            if not isinstance(item, dict) or now - _safe_float(item.get("updated_ts"), 0.0, 0.0) > 300:
                store.pop(key, None)
        return store

    def _message_debounce_pending_key(self, event: AstrMessageEvent) -> str:
        if not bool(_persona_value(self, 'enable_message_debounce', _persona_value(self, 'enable_semantic_message_debounce', True))):
            return ""
        if self._message_debounce_command_text(event, str(getattr(event, "message_str", "") or "")):
            return ""
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker):
            try:
                if not inbound_checker(event):
                    return ""
            except Exception:
                return ""
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                try:
                    sender_id = _single_line(event.get_sender_id(), 160)
                except Exception:
                    sender_id = ""
                resolver = getattr(self, "_private_user_id_for_event", None)
                if sender_id and callable(resolver):
                    try:
                        sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
                    except Exception:
                        pass
                return self._semantic_buffer_key(f"private:{sender_id}", sender_id) if sender_id else ""
        except Exception:
            return ""
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return ""
        try:
            sender_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            sender_id = ""
        return self._semantic_buffer_key(f"group:{group_id}", sender_id) if sender_id else ""

    def _message_debounce_event_id(self, event: AstrMessageEvent) -> str:
        return self._event_message_id(event) or f"object:{id(event)}"

    def _message_debounce_pending_prompt_text(self, event: AstrMessageEvent) -> str:
        """Capture the text already consumed into the current LLM request.

        The semantic buffer is normally consumed before the provider call.  A
        later follow-up therefore cannot reconstruct the old prompt from the
        event alone; keep a bounded snapshot so an expired response can be
        retried together with the follow-up.
        """
        key = self._message_debounce_pending_key(event)
        buffers = getattr(self, "_semantic_message_buffers", None)
        texts: list[str] = []
        if key and isinstance(buffers, dict):
            buffer = buffers.get(key)
            if isinstance(buffer, dict):
                messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
                for item in messages[-8:]:
                    if isinstance(item, dict):
                        text = _single_line(item.get("text"), 260)
                        if text and text not in texts:
                            texts.append(text)
        current = _single_line(getattr(event, "message_str", ""), 260)
        if current and current not in texts:
            texts.append(current)
        return "\n".join(texts[-8:])

    def _message_debounce_absorb_pending_message(self, event: AstrMessageEvent, text: str) -> bool:
        """将 LLM 处理中到达的补话放回同一缓冲区，避免并发生成第二个回复。"""
        key = self._message_debounce_pending_key(event)
        if not key:
            return False
        store = self._message_debounce_pending_store()
        pending = store.get(key)
        if not isinstance(pending, dict):
            return False
        message_id = self._message_debounce_event_id(event)
        if message_id and message_id == _single_line(pending.get("event_id"), 120):
            return False
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        wait = self._message_debounce_seconds("group" if key.startswith("group:") else "text")
        if wait <= 0:
            return False
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            buffers = {}
            self._semantic_message_buffers = buffers
        buffer = buffers.get(key)
        now = _now_ts()
        if not isinstance(buffer, dict):
            buffer = {
                "first_ts": now,
                "updated_ts": now,
                "wait_seconds": wait,
                "kind": "group_text" if key.startswith("group:") else "text",
                "messages": [],
            }
            buffers[key] = buffer
        messages = buffer.setdefault("messages", [])
        if not isinstance(messages, list):
            messages = []
            buffer["messages"] = messages
        previous_prompt = str(pending.get("prompt_text") or "")[:1800]
        if previous_prompt and not messages:
            for line in previous_prompt.splitlines()[-8:]:
                line = _single_line(line, 260)
                if line and line not in {
                    _single_line(item.get("text"), 260)
                    for item in messages
                    if isinstance(item, dict)
                }:
                    messages.append({"ts": now, "text": line, "sender_name": ""})
        if cleaned not in {
            _single_line(item.get("text"), 260)
            for item in messages
            if isinstance(item, dict)
        }:
            messages.append({"ts": now, "text": cleaned, "sender_name": ""})
        buffer["updated_ts"] = now
        # 当前事件已经在等待 LLM，不要再等待一个滑动窗口；等旧锁释放后
        # 立即用合并内容发起下一轮请求。
        buffer["deadline_ts"] = now
        stale_ids = pending.setdefault("stale_event_ids", [])
        active_id = _single_line(pending.get("event_id"), 120)
        if active_id and active_id not in stale_ids:
            stale_ids.append(active_id)
        pending["updated_ts"] = now
        try:
            setattr(event, "private_companion_debounce_pending_merged", True)
        except Exception:
            pass
        logger.info(
            "LLM 处理中收到补话，旧回复标记过期并合并等待: key=%s count=%s text=%s",
            key,
            len(messages),
            _single_line(cleaned, 80),
        )
        return True

    def _message_debounce_mark_llm_pending(self, event: AstrMessageEvent) -> None:
        key = self._message_debounce_pending_key(event)
        if not key:
            return
        message_id = self._message_debounce_event_id(event)
        store = self._message_debounce_pending_store()
        current = store.get(key)
        if not isinstance(current, dict):
            current = {"stale_event_ids": []}
            store[key] = current
        current["event_id"] = message_id
        current["updated_ts"] = _now_ts()
        current["prompt_text"] = self._message_debounce_pending_prompt_text(event)
