# -*- coding: utf-8 -*-
"""PrivateImageHistoryDelayedPart02Mixin。

由 tools/split_mixin_domain.py 从 private_image_history_delayed.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 31 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageHistoryDelayedMixin）。
"""
from __future__ import annotations

from .helpers import _single_line
from .private_image_shared import _private_image_host, logger



class PrivateImageHistoryDelayedPart02Mixin:
    """PrivateImageHistoryDelayedPart02Mixin（从 PrivateImageHistoryDelayedMixin 拆出）。"""


    async def _record_private_image_vision_feedback_target(
        self,
        *,
        user_id: str,
        image_sources: list[str],
        vision_text: str,
        reply: str,
        ownership: str = "",
        intent: str = "",
    ) -> None:
        raw_sources = [str(item) for item in image_sources[:5] if str(item or "").strip()]
        image_keys = self._private_image_cache_image_keys(raw_sources)
        if not image_keys:
            return
        image_aliases = self._private_image_cache_aliases_for_sources(raw_sources)
        image_limit = self._private_image_vision_text_limit(len(raw_sources))
        try:
            async with self._data_lock:
                user = self._get_user(user_id)
                user["last_private_image_vision_feedback_target"] = {
                    "ts": _private_image_host._now_ts(),
                    "image_keys": image_keys,
                    "image_aliases": image_aliases,
                    "vision_text": _single_line(vision_text, image_limit),
                    "reply": _single_line(reply, 300),
                    "ownership": _single_line(ownership, 120),
                    "intent": _single_line(intent, 160),
                }
                self._save_data_sync(sections={"users"})
        except Exception as exc:
            logger.debug("私聊图片视觉反馈目标记录失败: %s", exc)
