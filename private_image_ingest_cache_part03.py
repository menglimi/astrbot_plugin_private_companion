# -*- coding: utf-8 -*-
"""PrivateImageIngestCachePart03Mixin。

由 tools/split_mixin_domain.py 从 private_image_ingest_cache.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 169 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageIngestCacheMixin）。
"""
from __future__ import annotations

import re
from .helpers import _safe_float, _safe_int, _single_line
from .private_image_shared import _private_image_host, logger
from typing import Any



class PrivateImageIngestCachePart03Mixin:
    """PrivateImageIngestCachePart03Mixin（从 PrivateImageIngestCacheMixin 拆出）。"""


    def _set_private_image_vision_cache(
        self,
        cache_key: str,
        text: str,
        *,
        provider_id: str,
        image_keys: list[str],
        image_aliases: list[str] | None = None,
        image_count: int = 0,
        prompt: str = "",
        scope: str = "private_image",
        preview: dict[str, Any] | None = None,
    ) -> None:
        if not bool(self._private_image_setting("enable_private_image_vision_cache", True)):
            return
        cleaned = _single_line(text, 900 if scope == "forward_image" else self._private_image_vision_text_limit(image_count))
        if not cache_key or not cleaned:
            return
        cache = self._private_image_vision_cache_store()
        clean_image_keys = [str(item) for item in image_keys[:5] if str(item or "").strip()]
        clean_scope = _single_line(scope, 40)
        clean_provider = _single_line(provider_id, 160)
        clean_aliases = [str(item).strip() for item in (image_aliases or []) if str(item or "").strip()]
        clean_aliases = list(dict.fromkeys(clean_aliases))[:24]
        clean_count = max(0, int(image_count or 0))
        if clean_count <= 0:
            clean_count = len(clean_image_keys)
        prompt_sig = self._private_image_vision_cache_prompt_sig(prompt)
        removed_variants = 0
        for old_key, old_item in list(cache.items()):
            if old_key == cache_key or not isinstance(old_item, dict):
                continue
            old_keys = [str(value).strip() for value in old_item.get("image_keys", []) if str(value or "").strip()]
            old_scope = _single_line(old_item.get("scope"), 40)
            old_provider = _single_line(old_item.get("provider_id"), 160)
            old_prompt_sig = _single_line(old_item.get("prompt_sig"), 32)
            same_reusable_image = old_keys == clean_image_keys and old_scope == clean_scope
            old_aliases = {str(value).strip() for value in old_item.get("image_aliases", []) if str(value or "").strip()}
            old_count = _safe_int(old_item.get("image_count"), 0, 0)
            same_single_alias = clean_count == 1 and old_count == 1 and bool(old_aliases & set(clean_aliases)) and old_scope == clean_scope
            same_reusable_image = same_reusable_image or same_single_alias
            same_provider_variant = same_reusable_image and old_provider == clean_provider
            stale_prompt_variant = same_provider_variant and old_prompt_sig != prompt_sig
            duplicate_provider_variant = same_reusable_image and old_provider and old_provider != clean_provider and _safe_int(old_item.get("hits"), 0, 0) == 0
            if stale_prompt_variant or duplicate_provider_variant:
                if isinstance(old_item, dict):
                    self._remove_private_image_cache_preview_file(_single_line(old_item.get("preview_path"), 260))
                cache.pop(old_key, None)
                removed_variants += 1
        existing_preview_path = ""
        existing_item = cache.get(cache_key)
        if isinstance(existing_item, dict):
            existing_preview_path = _single_line(existing_item.get("preview_path"), 260)
        item = {
            "text": cleaned,
            "provider_id": clean_provider,
            "image_keys": clean_image_keys,
            "image_aliases": clean_aliases,
            "image_count": clean_count,
            "scope": clean_scope,
            "prompt_sig": prompt_sig,
            "created_ts": _private_image_host._now_ts(),
            "last_hit_ts": 0,
            "hits": 0,
        }
        if isinstance(preview, dict) and preview.get("preview_path"):
            item.update(
                {
                    "preview_path": _single_line(preview.get("preview_path"), 260),
                    "preview_width": _safe_int(preview.get("preview_width"), 0, 0),
                    "preview_height": _safe_int(preview.get("preview_height"), 0, 0),
                    "preview_size": _safe_int(preview.get("preview_size"), 0, 0),
                }
            )
            if existing_preview_path and existing_preview_path != item["preview_path"]:
                self._remove_private_image_cache_preview_file(existing_preview_path)
        elif isinstance(existing_item, dict) and existing_preview_path:
            item.update(
                {
                    "preview_path": existing_preview_path,
                    "preview_width": _safe_int(existing_item.get("preview_width"), 0, 0),
                    "preview_height": _safe_int(existing_item.get("preview_height"), 0, 0),
                    "preview_size": _safe_int(existing_item.get("preview_size"), 0, 0),
                }
            )
        cache[cache_key] = item
        if removed_variants:
            self._record_cache_metric(f"image_vision:{scope}", hit=True, detail=f"dedupe:{removed_variants}")
        max_items = int(self._private_image_setting("private_image_vision_cache_max_items", 300) or 0)
        if max_items > 0 and len(cache) > max_items:
            stale = sorted(
                cache.items(),
                key=lambda item: (
                    _safe_int((item[1] if isinstance(item[1], dict) else {}).get("hits"), 0, 0),
                    _safe_float((item[1] if isinstance(item[1], dict) else {}).get("last_hit_ts"), 0)
                    or _safe_float((item[1] if isinstance(item[1], dict) else {}).get("created_ts"), 0),
                ),
            )
            evicted = 0
            for key, _ in stale[: max(1, len(cache) - max_items)]:
                removed = cache.pop(key, None)
                if isinstance(removed, dict):
                    self._remove_private_image_cache_preview_file(_single_line(removed.get("preview_path"), 260))
                evicted += 1
            if evicted:
                self._record_cache_metric(f"image_vision:{scope}", hit=False, detail=f"evict:{evicted}")
        try:
            self._save_data_sync(sections={"private_image_vision_cache"})
        except Exception as exc:
            logger.debug("私聊图片视觉缓存保存失败: %s", exc)

    def _invalidate_private_image_vision_cache_by_image_keys(self, image_keys: list[str], *, image_aliases: list[str] | None = None, reason: str = "") -> int:
        targets = {str(item) for item in image_keys or [] if str(item or "").strip()}
        alias_targets = {str(item).strip() for item in (image_aliases or []) if str(item or "").strip()}
        if not targets and not alias_targets:
            return 0
        cache = self._private_image_vision_cache_store()
        removed = 0
        for key, item in list(cache.items()):
            if not isinstance(item, dict):
                continue
            cached_keys = {str(value) for value in item.get("image_keys", []) if str(value or "").strip()}
            cached_aliases = {str(value).strip() for value in item.get("image_aliases", []) if str(value or "").strip()}
            if (cached_keys & targets) or (cached_aliases & alias_targets):
                removed_item = cache.pop(key, None)
                if isinstance(removed_item, dict):
                    self._remove_private_image_cache_preview_file(
                        _single_line(removed_item.get("preview_path"), 260)
                    )
                removed += 1
        if removed:
            logger.info("私聊图片视觉缓存已因负反馈失效: removed=%s reason=%s", removed, _single_line(reason, 120))
            try:
                self._save_data_sync(sections={"private_image_vision_cache"})
            except Exception as exc:
                logger.debug("私聊图片视觉缓存失效保存失败: %s", exc)
        return removed

    def _is_private_image_vision_negative_feedback(self, text: str) -> bool:
        cleaned = _single_line(text, 160)
        if not cleaned:
            return False
        negative_patterns = (
            r"(识别|看|理解|读|认).{0,8}(错|不对|不准|偏了|歪了)",
            r"(不是|不对|错了).{0,12}(这个意思|这样|这意思|你说的|图里|图片|表情包)",
            r"(你|bot|机器人).{0,8}(看错|认错|理解错|识别错)",
            r"(不是.{0,8}你|不是.{0,8}bot|不是.{0,8}本人|不是.{0,8}这个)",
        )
        return any(re.search(pattern, cleaned, flags=re.I) for pattern in negative_patterns)

    def _apply_private_image_vision_negative_feedback(self, user: dict[str, Any], text: str) -> bool:
        if not self._is_private_image_vision_negative_feedback(text):
            return False
        target = user.get("last_private_image_vision_feedback_target")
        if not isinstance(target, dict):
            return False
        ts = _safe_float(target.get("ts"), 0)
        if ts <= 0 or _private_image_host._now_ts() - ts > 180:
            return False
        image_keys = [str(item) for item in target.get("image_keys", []) if str(item or "").strip()]
        image_aliases = [str(item) for item in target.get("image_aliases", []) if str(item or "").strip()]
        removed = self._invalidate_private_image_vision_cache_by_image_keys(image_keys, image_aliases=image_aliases, reason=text)
        target["negative_feedback_ts"] = _private_image_host._now_ts()
        target["negative_feedback_text"] = _single_line(text, 160)
        target["invalidated_cache_items"] = removed
        logger.info(
            "私聊图片视觉负反馈记录: user_image_keys=%s removed=%s text=%s",
            len(image_keys),
            removed,
            _single_line(text, 120),
        )
        return bool(removed or image_keys)
