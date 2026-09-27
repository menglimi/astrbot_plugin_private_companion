# -*- coding: utf-8 -*-
"""PrivateImagePlaceholderBufferMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 656 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import asyncio
import re
from .helpers import _safe_float, _safe_int, _single_line
from .private_image_shared import CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS, _private_image_host, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from typing import Any



class PrivateImagePlaceholderBufferMixin:
    """PrivateImagePlaceholderBufferMixin（从 PrivateImageMixin 拆出）。"""


    @staticmethod
    def _context_image_placeholder_pattern() -> re.Pattern[str]:
        return re.compile(r"(?:\[图片\]|【图片】)")

    def _context_image_has_placeholder(self, text: Any) -> bool:
        return bool(self._context_image_placeholder_pattern().search(str(text or "")))

    def _context_image_plain_text(self, value: Any, *, depth: int = 0) -> str:
        if value is None or depth > 5:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            return "\n".join(self._context_image_plain_text(item, depth=depth + 1) for item in value)
        if isinstance(value, tuple):
            return "\n".join(self._context_image_plain_text(item, depth=depth + 1) for item in value)
        if isinstance(value, dict):
            parts: list[str] = []
            for key in ("content", "text", "message", "value"):
                if key in value:
                    parts.append(self._context_image_plain_text(value.get(key), depth=depth + 1))
            data = value.get("data")
            if isinstance(data, dict):
                for key in ("content", "text", "message", "value"):
                    if key in data:
                        parts.append(self._context_image_plain_text(data.get(key), depth=depth + 1))
            return "\n".join(part for part in parts if part)
        for attr in ("content", "text", "message", "value"):
            current = getattr(value, attr, None)
            if current is not None:
                text = self._context_image_plain_text(current, depth=depth + 1)
                if text:
                    return text
        return ""

    def _context_image_inline_sources(self, value: Any, *, depth: int = 0) -> list[str]:
        if value is None or depth > 5:
            return []
        sources: list[str] = []

        def add(source: Any) -> None:
            text = str(source or "").strip()
            if text and text not in sources:
                sources.append(text)

        if isinstance(value, (list, tuple)):
            for item in value:
                for source in self._context_image_inline_sources(item, depth=depth + 1):
                    add(source)
            return sources
        if isinstance(value, dict):
            type_name = str(value.get("type") or value.get("post_type") or "").strip().lower()
            data = value.get("data") if isinstance(value.get("data"), dict) else value
            if type_name == "image":
                extractor = getattr(self, "_extract_image_url_from_segment_data", None)
                if callable(extractor):
                    try:
                        add(extractor(data))
                    except Exception:
                        pass
                for key in ("url", "origin_url", "source_url", "path", "image_path", "file_path", "local_path", "file"):
                    add(data.get(key) if isinstance(data, dict) else "")
            for key in ("content", "message", "messages", "value", "data"):
                nested = value.get(key)
                if nested is not value:
                    for source in self._context_image_inline_sources(nested, depth=depth + 1):
                        add(source)
            return sources
        type_name = str(getattr(value, "type", "") or value.__class__.__name__).strip().lower()
        if type_name == "image":
            add(self._image_component_source(value))
        for attr in ("content", "message", "messages", "value", "data"):
            nested = getattr(value, attr, None)
            if nested is not None and nested is not value:
                for source in self._context_image_inline_sources(nested, depth=depth + 1):
                    add(source)
        return sources

    def _context_image_source_is_resolvable(self, source: Any) -> bool:
        """Return whether a context image source is directly resolvable."""

        text = str(source or "").strip()
        if not text:
            return False
        if re.match(r"^https?://", text, flags=re.I) or text.startswith(("data:", "base64://")):
            return True
        path = self._private_image_local_path_from_source(text)
        try:
            return path is not None and path.exists() and path.is_file()
        except (OSError, ValueError):
            return False

    def _replace_context_image_placeholder(self, value: Any, replacement: str, *, depth: int = 0) -> tuple[Any, bool]:
        if value is None or depth > 5:
            return value, False
        pattern = self._context_image_placeholder_pattern()
        if isinstance(value, str):
            inserted = False

            def repl(_match: re.Match[str]) -> str:
                nonlocal inserted
                if inserted:
                    return ""
                inserted = True
                return replacement

            updated = pattern.sub(repl, value)
            updated = re.sub(r"[ \t]{2,}", " ", updated).strip()
            return updated, updated != value
        if isinstance(value, list):
            changed = False
            updated_items: list[Any] = []
            for item in value:
                updated, item_changed = self._replace_context_image_placeholder(item, replacement, depth=depth + 1)
                changed = changed or item_changed
                updated_items.append(updated)
            return updated_items, changed
        if isinstance(value, tuple):
            updated, changed = self._replace_context_image_placeholder(list(value), replacement, depth=depth + 1)
            return tuple(updated), changed
        if isinstance(value, dict):
            changed = False
            updated = dict(value)
            for key in ("content", "text", "message", "value"):
                if key not in updated:
                    continue
                new_value, item_changed = self._replace_context_image_placeholder(updated.get(key), replacement, depth=depth + 1)
                if item_changed:
                    updated[key] = new_value
                    changed = True
            return updated, changed
        for attr in ("content", "text", "message", "value"):
            current = getattr(value, attr, None)
            if current is None:
                continue
            new_value, changed = self._replace_context_image_placeholder(current, replacement, depth=depth + 1)
            if not changed:
                continue
            try:
                setattr(value, attr, new_value)
            except Exception:
                return value, False
            return value, True
        return value, False

    def _context_image_skip_text(self, text: str) -> bool:
        raw = str(text or "")
        if not raw:
            return True
        if not self._context_image_has_placeholder(raw):
            return True
        skip_markers = (
            "【本轮延迟图片】",
            "【本轮引用图片】",
            "【当前引用图片锚点】",
            "【本轮合并消息】",
            "【本轮合并消息转述】",
            "合并消息中的图片：",
            "不要把摘要里的[图片]当成已看见原图",
            "遇到 [图片]",
            "图片占位",
        )
        return any(marker in raw for marker in skip_markers)

    @staticmethod
    def _context_image_normalize_text(text: str) -> str:
        normalized = re.sub(r"\s+", "", str(text or ""))
        normalized = re.sub(r"^(?:user|assistant|system|用户|机器人|bot|Bot)[:：]", "", normalized)
        return normalized[:1000]

    def _context_image_recall_rows_for_event(self, event: AstrMessageEvent) -> list[dict[str, Any]]:
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            return []
        cleaner = getattr(self, "_cleanup_recall_message_cache", None)
        if callable(cleaner):
            try:
                cleaner()
            except Exception:
                pass
        try:
            scope = _single_line(self._event_scope_key(event), 160)
        except Exception:
            scope = ""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for key, row in list(cache.items()):
            if not isinstance(row, dict):
                continue
            row_scope = _single_line(row.get("scope"), 160)
            if scope and row_scope and row_scope != scope:
                continue
            unique = _single_line(row.get("message_id"), 120) or _single_line(key, 120)
            if unique and unique in seen:
                continue
            text = str(row.get("text") or "")
            image_items = []
            getter = getattr(self, "_recall_image_items_from_snapshot", None)
            if callable(getter):
                try:
                    image_items = getter(row)
                except Exception:
                    image_items = []
            if not image_items and not self._context_image_has_placeholder(text):
                continue
            if unique:
                seen.add(unique)
            rows.append(row)
        rows.sort(key=lambda item: _safe_float(item.get("ts"), 0))
        return rows

    def _context_image_sources_from_recall_row(self, row: dict[str, Any]) -> list[str]:
        items = []
        getter = getattr(self, "_recall_image_items_from_snapshot", None)
        if callable(getter):
            try:
                items = getter(row)
            except Exception:
                items = []
        normalized_items = items if isinstance(items, list) else []
        sources: list[str] = []
        for item in normalized_items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            tier = _single_line(item.get("tier"), 40)
            if source and tier not in {"placeholder", "platform_file"} and source not in sources:
                sources.append(source)
        # Structured snapshots are authoritative. Falling back after filtering
        # would reintroduce platform file IDs from the legacy ``images`` field.
        if normalized_items:
            return sources[:5]
        for source in row.get("images") if isinstance(row.get("images"), list) else []:
            text = str(source or "").strip()
            if text and self._context_image_source_is_resolvable(text) and text not in sources:
                sources.append(text)
        return sources[:5]

    def _match_context_image_recall_row(
        self,
        text: str,
        rows: list[dict[str, Any]],
        used_rows: set[str],
    ) -> dict[str, Any] | None:
        normalized = self._context_image_normalize_text(text)
        if not normalized:
            return None
        for row in rows:
            unique = _single_line(row.get("message_id"), 120)
            if unique and unique in used_rows:
                continue
            row_text = self._context_image_normalize_text(str(row.get("text") or ""))
            if row_text and (row_text in normalized or normalized in row_text):
                return row
        if len(normalized) <= 220:
            for row in rows:
                unique = _single_line(row.get("message_id"), 120)
                if unique and unique in used_rows:
                    continue
                if self._context_image_sources_from_recall_row(row):
                    return row
        return None

    async def _caption_context_image_sources(self, sources: list[str], *, umo: str = "") -> str:
        if not self._private_image_enhancement_enabled():
            return ""
        clean_sources = [str(item).strip() for item in sources if str(item or "").strip()][:5]
        if not clean_sources:
            return ""
        failure_cache = getattr(self, "_context_image_caption_failure_cache", None)
        if not isinstance(failure_cache, dict):
            failure_cache = {}
            self._context_image_caption_failure_cache = failure_cache
        now = _private_image_host._now_ts()
        cache_key = tuple(clean_sources)
        retry_after = _safe_float(failure_cache.get(cache_key), 0.0)
        if retry_after > now:
            return ""
        for key, expires_at in list(failure_cache.items()):
            if _safe_float(expires_at, 0.0) <= now:
                failure_cache.pop(key, None)
        configured_wait = max(0.0, _safe_float(self._private_image_setting("context_image_caption_timeout_seconds", 30.0), 30.0, 0.0))
        provider_timeout = self._private_image_provider_timeout_seconds()
        vision_budget = self._private_image_vision_wait_budget_seconds()
        wait_seconds = (
            max(configured_wait, provider_timeout + 2.0, vision_budget)
            if configured_wait > 0 and provider_timeout > 0
            else configured_wait
        )
        task = self._transcribe_private_inbound_images(clean_sources, umo=umo)
        try:
            if wait_seconds > 0:
                caption = _single_line(await asyncio.wait_for(task, timeout=wait_seconds), self._private_image_vision_text_limit(len(clean_sources)))
            else:
                caption = _single_line(await task, self._private_image_vision_text_limit(len(clean_sources)))
            if caption:
                failure_cache.pop(cache_key, None)
            else:
                failure_cache[cache_key] = _private_image_host._now_ts() + CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS
            return caption
        except asyncio.TimeoutError:
            failure_cache[cache_key] = _private_image_host._now_ts() + CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS
            logger.warning("上下文图片补全等待超时: images=%s timeout=%.1fs", len(clean_sources), wait_seconds)
            return ""
        except Exception as exc:
            failure_cache[cache_key] = _private_image_host._now_ts() + CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS
            logger.warning("上下文图片补全失败: images=%s error=%s", len(clean_sources), _single_line(exc, 120))
            return ""

    async def _enrich_request_context_image_placeholders(self, event: AstrMessageEvent, req: ProviderRequest) -> dict[str, int]:
        if (
            not self._private_image_enhancement_enabled()
            or not bool(self._private_image_setting("enable_context_image_captioning", True))
        ):
            return {"contexts": 0, "replaced": 0, "missed": 0}
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return {"contexts": 0, "replaced": 0, "missed": 0}
        max_items = max(0, _safe_int(self._private_image_setting("context_image_caption_max_items", 12), 12, 0, 50))
        if max_items <= 0:
            return {"contexts": len(contexts), "replaced": 0, "missed": 0}

        rows = self._context_image_recall_rows_for_event(event)
        used_rows: set[str] = set()
        caption_cache: dict[tuple[str, ...], str] = {}
        changed = False
        attempted = 0
        replaced = 0
        missed = 0
        updated_contexts = list(contexts)
        umo = str(getattr(event, "unified_msg_origin", "") or "")

        for index, item in enumerate(contexts):
            if attempted >= max_items:
                break
            text = self._context_image_plain_text(item)
            inline_sources = [
                source
                for source in self._context_image_inline_sources(item)
                if self._context_image_source_is_resolvable(source)
            ]
            has_placeholder = self._context_image_has_placeholder(text)
            if not has_placeholder and not inline_sources:
                continue
            if self._context_image_skip_text(text):
                continue

            sources = inline_sources[:5]
            row = None
            if not sources:
                row = self._match_context_image_recall_row(text, rows, used_rows)
                if row:
                    sources = self._context_image_sources_from_recall_row(row)
                    unique = _single_line(row.get("message_id"), 120)
                    if unique:
                        used_rows.add(unique)
            if not sources:
                missed += 1
                continue

            attempted += 1
            cache_key = tuple(sources)
            if cache_key not in caption_cache:
                caption = await self._caption_context_image_sources(sources, umo=umo)
                caption_cache[cache_key] = caption
            else:
                caption = caption_cache[cache_key]
            if not caption:
                missed += 1
                continue

            replacement = f"【图片摘要：{caption}】"
            updated_item, item_changed = self._replace_context_image_placeholder(item, replacement)
            if not item_changed:
                missed += 1
                continue
            updated_contexts[index] = updated_item
            changed = True
            replaced += 1

        if changed:
            try:
                req.contexts = updated_contexts
            except Exception:
                return {"contexts": len(contexts), "replaced": 0, "missed": missed}
            logger.info(
                "已将历史上下文图片占位替换为视觉摘要: contexts=%s replaced=%s missed=%s",
                len(contexts),
                replaced,
                missed,
            )
        return {"contexts": len(contexts), "replaced": replaced, "missed": missed}

    def _message_debounce_seconds(self, kind: str = "text") -> float:
        if not bool(self._private_image_setting("enable_message_debounce", self._private_image_setting("enable_semantic_message_debounce", True))):
            return 0.0
        text_wait = _safe_float(self._private_image_setting("text_message_debounce_seconds", 0.0), 0.0, 0.0)
        if kind == "image":
            return max(0.0, _safe_float(self._private_image_setting("image_message_debounce_seconds", 8.0), 8.0, 0.0))
        if kind == "forward":
            return max(0.0, _safe_float(self._private_image_setting("forward_message_debounce_seconds", 0.0), 0.0, 0.0))
        if kind == "group":
            return max(0.0, text_wait)
        return max(0.0, text_wait)

    async def _consume_semantic_message_buffer_for_event(self, event: AstrMessageEvent, *, private_chat: bool) -> str:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id:
            return ""
        force_consume = False
        if private_chat:
            if not bool(self._private_image_setting("enable_message_debounce", self._private_image_setting("enable_semantic_message_debounce", True))):
                return ""
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver):
                try:
                    sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
                except Exception:
                    pass
            scope = f"private:{sender_id}"
            key = self._semantic_buffer_key(scope, sender_id)
        else:
            group_id = self._extract_group_id_from_event(event)
            if not group_id:
                return ""
            scope = f"group:{group_id}"
            high_intensity = getattr(event, "private_companion_group_high_intensity", None)
            buffers = getattr(self, "_semantic_message_buffers", None)
            high_key = self._group_high_intensity_buffer_key(group_id, sender_id)
            legacy_high_key = self._group_high_intensity_buffer_key(group_id)
            active_high_key = high_key
            if (
                isinstance(buffers, dict)
                and not isinstance(buffers.get(active_high_key), dict)
                and isinstance(buffers.get(legacy_high_key), dict)
            ):
                active_high_key = legacy_high_key
            if isinstance(high_intensity, dict) and high_intensity.get("active") and isinstance(buffers, dict) and isinstance(buffers.get(active_high_key), dict):
                key = active_high_key
                force_consume = True
            else:
                key = self._semantic_buffer_key(scope, sender_id)
                if isinstance(buffers, dict) and isinstance(buffers.get(key), dict):
                    buffer_wait = _safe_float(buffers.get(key, {}).get("wait_seconds"), 0.0, 0.0)
                    if buffer_wait <= 0:
                        return ""
                else:
                    wait = self._message_debounce_seconds("group")
                    if wait <= 0:
                        return ""
        buffers = getattr(self, "_semantic_message_buffers", None)
        if not isinstance(buffers, dict):
            return ""
        buffer = buffers.get(key)
        if not isinstance(buffer, dict):
            return ""
        wait = max(0.0, _safe_float(buffer.get("wait_seconds"), self._private_image_setting("text_message_debounce_seconds", 0.0), 0.0))
        if wait <= 0:
            return ""
        identity = getattr(self, "_semantic_buffer_identity", None)
        if callable(identity):
            log_scope, log_sender = identity(key)
        else:
            log_scope, log_sender = (key.rsplit(":", 1) + [""])[:2] if ":" in key else (key, "")
        if not log_sender:
            log_sender = sender_id
        buffer_kind = _single_line(buffer.get("kind"), 40) or ("group_high_intensity" if force_consume else "text")
        initial_messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        deadline_ts = _safe_float(buffer.get("deadline_ts"), 0.0, 0.0)
        max_deadline_ts = _safe_float(buffer.get("max_deadline_ts"), 0.0, 0.0)
        updated_ts = _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), 0.0)
        initial_target_ts = deadline_ts if deadline_ts > 0 else updated_ts + wait
        if max_deadline_ts > 0:
            initial_target_ts = min(initial_target_ts, max_deadline_ts)
        already_due = initial_target_ts > 0 and _private_image_host._now_ts() >= initial_target_ts
        logger.info(
            "消息收口等待开始: kind=%s scope=%s sender=%s wait=%.1fs count=%s deadline=%s",
            buffer_kind,
            log_scope,
            log_sender,
            wait,
            len(initial_messages),
            "fixed" if deadline_ts > 0 else "sliding",
        )
        deadline_guard = _private_image_host._now_ts() if already_due else deadline_ts if deadline_ts > 0 else _private_image_host._now_ts() + max(wait + 2.0, min(30.0, wait * 3.0 + 2.0))
        while True:
            buffer = buffers.get(key)
            if not isinstance(buffer, dict):
                return ""
            updated_ts = _safe_float(buffer.get("updated_ts"), buffer.get("first_ts"), _private_image_host._now_ts())
            deadline_ts = _safe_float(buffer.get("deadline_ts"), deadline_ts, deadline_ts)
            max_deadline_ts = _safe_float(buffer.get("max_deadline_ts"), max_deadline_ts, max_deadline_ts)
            target_ts = deadline_ts if deadline_ts > 0 else updated_ts + wait
            if max_deadline_ts > 0:
                target_ts = min(target_ts, max_deadline_ts)
            remaining = max(0.0, target_ts - _private_image_host._now_ts())
            if remaining <= 0:
                break
            if _private_image_host._now_ts() + remaining > deadline_guard:
                remaining = max(0.0, deadline_guard - _private_image_host._now_ts())
                if remaining <= 0:
                    break
            await asyncio.sleep(min(remaining, 1.0))
        buffer = buffers.pop(key, None)
        if not isinstance(buffer, dict):
            return ""
        messages = buffer.get("messages") if isinstance(buffer.get("messages"), list) else []
        smart_meta = buffer.get("smart_debounce") if isinstance(buffer.get("smart_debounce"), dict) else {}
        if smart_meta.get("enabled"):
            learned_messages = [
                _single_line(item.get("text"), 180)
                for item in messages
                if isinstance(item, dict) and _single_line(item.get("text"), 180)
            ]
            if len(learned_messages) <= 1:
                scope = self._event_scope_key(event)
                self._record_smart_message_debounce_example(
                    kind="false_incomplete",
                    scope=scope,
                    sender_id=sender_id,
                    messages=learned_messages,
                    previous_decision="incomplete",
                    note="模型判断未说完并等待,但用户没有继续补充。",
                )
                recorder = getattr(self, "_record_smart_message_debounce_log", None)
                if callable(recorder):
                    recorder(
                        scope=scope,
                        sender_id=sender_id,
                        text=" / ".join(learned_messages[:3]),
                        decision="incomplete",
                        outcome="timeout_single",
                        note="等待结束但没有等到补话,本次会作为误等样本。",
                        source="buffer",
                        message_count=len(learned_messages),
                    )
                logger.info(
                    "智能防抖等待结束未等到补话: scope=%s sender=%s messages=%s",
                    scope,
                    sender_id,
                    len(learned_messages),
                )
            else:
                scope = self._event_scope_key(event)
                recorder = getattr(self, "_record_smart_message_debounce_log", None)
                if callable(recorder):
                    recorder(
                        scope=scope,
                        sender_id=sender_id,
                        text=" / ".join(learned_messages[:3]),
                        decision="incomplete",
                        outcome="merged_followup",
                        note="等待期间收到补话,已合并为同一轮。",
                        source="buffer",
                        message_count=len(learned_messages),
                    )
                logger.info(
                    "智能防抖等待命中补话: scope=%s sender=%s messages=%s",
                    scope,
                    sender_id,
                    len(learned_messages),
                )
            scheduler = getattr(self, "_schedule_data_save", None)
            if callable(scheduler):
                scheduler(sections={"smart_message_debounce"})
        lines = []
        for item in messages[:8]:
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 260)
            if text:
                name = _single_line(item.get("sender_name"), 40)
                if force_consume and name:
                    lines.append(f"{name}: {text}")
                else:
                    lines.append(text)
        if len(lines) <= 1:
            logger.info(
                "消息收口等待结束: kind=%s scope=%s sender=%s count=%s result=single",
                buffer_kind,
                log_scope,
                log_sender,
                len(lines),
            )
            return ""
        merged = "\n".join(f"{idx + 1}. {line}" for idx, line in enumerate(lines))
        logger.info(
            "消息收口等待结束: kind=%s scope=%s sender=%s count=%s result=merged preview=%s",
            buffer_kind,
            log_scope,
            log_sender,
            len(lines),
            _single_line(merged, 120),
        )
        return merged

    def _take_buffered_private_images_for_event(self, event: AstrMessageEvent) -> list[str]:
        context = self._take_buffered_private_image_context_for_event(event)
        return [str(item) for item in context.get("images", [])[:5] if str(item or "").strip()] if isinstance(context, dict) else []

    def _completed_private_image_vision_task_text(self, vision_task: Any) -> str:
        if not isinstance(vision_task, asyncio.Task) or not vision_task.done() or vision_task.cancelled():
            return ""
        try:
            return _single_line(vision_task.result(), 1400)
        except Exception as exc:
            logger.warning("私聊单图后台视觉任务结果读取失败: %s", _single_line(exc, 120))
            return ""

    def _private_image_vision_handoff_ttl_seconds(self) -> float:
        debounce = self._message_debounce_seconds("image")
        vision_wait = self._private_image_vision_wait_budget_seconds()
        try:
            provider_timeout = self._private_image_provider_timeout_seconds()
        except Exception:
            provider_timeout = 12.0
        return max(30.0, min(180.0, debounce + max(vision_wait, provider_timeout) + 15.0))

    def _private_image_vision_handoff_session(self, event: AstrMessageEvent) -> str:
        return _single_line(getattr(event, "unified_msg_origin", ""), 500)

    def _cleanup_private_image_vision_handoffs(self, *, now: float | None = None) -> dict[Any, dict[str, Any]]:
        handoffs = getattr(self, "_private_image_vision_handoffs", None)
        if not isinstance(handoffs, dict):
            handoffs = {}
            self._private_image_vision_handoffs = handoffs
        current_ts = _private_image_host._now_ts() if now is None else float(now)
        for handoff_key, handoff in list(handoffs.items()):
            if not isinstance(handoff, dict) or _safe_float(handoff.get("expires_ts"), 0.0) <= current_ts:
                handoffs.pop(handoff_key, None)
        if len(handoffs) > 128:
            oldest = sorted(
                handoffs.items(),
                key=lambda item: _safe_float(item[1].get("created_ts"), 0.0),
            )[: len(handoffs) - 96]
            for handoff_key, _handoff in oldest:
                handoffs.pop(handoff_key, None)
        return handoffs

    def _remember_private_image_vision_handoff(
        self,
        key: Any,
        event: AstrMessageEvent,
        buffer: dict[str, Any],
    ) -> dict[str, Any]:
        now = _private_image_host._now_ts()
        handoffs = self._cleanup_private_image_vision_handoffs(now=now)
        images = [str(item) for item in (buffer.get("images") or [])[:5] if str(item or "").strip()]
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_task = buffer.get("vision_task")
        vision_text = _single_line(buffer.get("vision_text"), image_limit)
        if not vision_text:
            vision_text = _single_line(
                self._completed_private_image_vision_task_text(vision_task),
                image_limit,
            )
        handoff = {
            "created_ts": now,
            "expires_ts": now + self._private_image_vision_handoff_ttl_seconds(),
            "session": self._private_image_vision_handoff_session(event),
            "images": list(images),
            "image_mode": _single_line(buffer.get("image_mode"), 20),
            "vision_task": vision_task,
            "vision_text": vision_text,
            "delayed_dispatch_started_ts": now,
            "delayed_dispatch_finished_ts": 0.0,
            "delayed_reply_sent": False,
            "delayed_reply_sent_ts": 0.0,
        }
        handoffs[key] = handoff
        return handoff
