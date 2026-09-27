# -*- coding: utf-8 -*-
"""ProactiveMessageSendReviewPart04Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_send_review.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 150 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageSendReviewMixin）。
"""
from __future__ import annotations

from .proactive_message_send_review_shared import At, CoreMessageComponents, Image
from .proactive_message_send_review_shared import Any
from .proactive_message_send_review_shared import _single_line
from .proactive_message_send_review_shared import os
from .proactive_message_send_review_shared import re
from .proactive_message_send_review_shared import time



class ProactiveMessageSendReviewPart04Mixin:
    """ProactiveMessageSendReviewPart04Mixin（从 ProactiveMessageSendReviewMixin 拆出）。"""


    def _pop_framework_captured_send_payload(
        self,
        umo: str,
    ) -> tuple[str, str, list[Any]]:
        cache_key = str(umo or "")
        self._cleanup_framework_delivery_caches()
        captured = self._framework_captured_send_cache.pop(cache_key, [])
        getattr(self, "_framework_captured_send_cache_at", {}).pop(cache_key, None)
        if not captured:
            return "", "", []
        text_parts: list[str] = []
        image_path = ""
        extra_components: list[Any] = []
        for call in captured:
            messages = getattr(call, "messages", [])
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "plain":
                    text_value = self._sanitize_captured_plain_text(item.get("text"))
                    if text_value:
                        text_parts.append(text_value)
                    continue
                if item_type == "image":
                    path_value = str(item.get("path") or "").strip()
                    if path_value and os.path.exists(path_value) and not image_path:
                        image_path = path_value
                        continue
                component = self._captured_framework_message_component(item)
                if component is not None:
                    extra_components.append(component)
        return "\n".join(part for part in text_parts if part).strip(), image_path, extra_components

    def _pop_framework_deferred_photo_payload(self, umo: str) -> dict[str, Any]:
        self._cleanup_framework_delivery_caches()
        cache_key = str(umo or "")
        cache = getattr(self, "_framework_deferred_photo_cache", None)
        if not isinstance(cache, dict):
            return {}
        payload = cache.pop(cache_key, None)
        getattr(self, "_framework_deferred_photo_cache_at", {}).pop(cache_key, None)
        return dict(payload) if isinstance(payload, dict) else {}

    def _cleanup_framework_delivery_caches(self, *, force: bool = False) -> None:
        """Drop abandoned framework delivery payloads and their component references."""
        now = time.time()
        try:
            ttl = max(30.0, float(getattr(self, "framework_delivery_cache_ttl_seconds", 300) or 300))
        except (TypeError, ValueError):
            ttl = 300.0

        def stamp(key: str, stamps: dict[str, Any]) -> float:
            try:
                return float(stamps.get(key, now) or now)
            except (TypeError, ValueError):
                return now

        for cache_name, stamp_name in (
            ("_framework_captured_send_cache", "_framework_captured_send_cache_at"),
            ("_framework_deferred_photo_cache", "_framework_deferred_photo_cache_at"),
        ):
            cache = getattr(self, cache_name, None)
            if not isinstance(cache, dict):
                continue
            stamps = getattr(self, stamp_name, None)
            if not isinstance(stamps, dict):
                stamps = {}
                setattr(self, stamp_name, stamps)
            if force:
                cache.clear()
                stamps.clear()
                continue
            for key in list(stamps):
                if key not in cache:
                    stamps.pop(key, None)
            stale = [key for key in cache if now - stamp(key, stamps) > ttl]
            for key in stale:
                cache.pop(key, None)
                stamps.pop(key, None)
            # A small defensive cap protects against callers that bypass the normal writer.
            max_items = 128
            if len(cache) > max_items:
                ordered = sorted(cache, key=lambda key: stamp(key, stamps))
                for key in ordered[: len(cache) - max_items]:
                    cache.pop(key, None)
                    stamps.pop(key, None)

    def _captured_framework_message_component(self, item: dict[str, Any]) -> Any | None:
        item_type = str(item.get("type") or "").strip().lower()
        path_value = str(item.get("path") or "").strip()
        url_value = str(item.get("url") or "").strip()
        if item_type == "mention_user":
            mention_user_id = item.get("mention_user_id")
            return At(qq=mention_user_id) if mention_user_id else None
        if item_type == "image":
            if url_value:
                try:
                    return Image.fromURL(url_value)
                except Exception:
                    return None
            return None
        if CoreMessageComponents is None:
            return None
        if item_type in {"record", "video"}:
            component_cls = getattr(CoreMessageComponents, item_type.capitalize(), None)
            if component_cls is None:
                return None
            try:
                if path_value and os.path.exists(path_value):
                    return component_cls.fromFileSystem(path_value)
                if url_value:
                    return component_cls.fromURL(url_value)
            except Exception:
                return None
            return None
        if item_type == "file":
            component_cls = getattr(CoreMessageComponents, "File", None)
            if component_cls is None:
                return None
            name = _single_line(item.get("text"), 120) or os.path.basename(path_value or url_value) or "file"
            if path_value and os.path.exists(path_value):
                return component_cls(name=name, file=path_value)
            if url_value:
                return component_cls(name=name, url=url_value)
        return None

    def _sanitize_captured_plain_text(self, raw_text: Any) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""
        kept: list[str] = []
        for raw_line in text.replace("\r", "\n").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if self._is_proactive_delivery_receipt_text(line):
                continue
            if self._looks_like_internal_provider_error_text(line):
                continue
            kept.append(line)
        cleaned = "\n".join(kept).strip().strip('"').strip("'")
        cleaned = cleaned.replace("（图片已送达）", "").replace("(图片已送达)", "")
        tts_cleaner = getattr(self, "_clean_tool_plain_text_tts_markup", None)
        if callable(tts_cleaner):
            cleaned = tts_cleaner(cleaned)
        else:
            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if self._looks_like_internal_provider_error_text(cleaned):
            return ""
        return cleaned[:260]
