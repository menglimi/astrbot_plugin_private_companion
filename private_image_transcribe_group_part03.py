# -*- coding: utf-8 -*-
"""PrivateImageTranscribeGroupPart03Mixin。

由 tools/split_mixin_domain.py 从 private_image_transcribe_group.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 69 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageTranscribeGroupMixin）。
"""
from __future__ import annotations

from .helpers import _single_line
from .private_image_shared import logger
from astrbot.api.event import AstrMessageEvent



class PrivateImageTranscribeGroupPart03Mixin:
    """PrivateImageTranscribeGroupPart03Mixin（从 PrivateImageTranscribeGroupMixin 拆出）。"""


    async def _group_reply_image_vision_for_request(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, bool]:
        """Resolve visual evidence for a group message that quotes an image."""
        finder = getattr(self, "_find_reply_image_sources_for_event", None)
        if not callable(finder):
            return "", False
        try:
            sources = [str(item).strip() for item in (await finder(event) or []) if str(item or "").strip()][:5]
        except Exception as exc:
            logger.debug("群聊引用图片来源读取失败: %s", _single_line(exc, 120))
            return "", False
        if not sources:
            return "", False

        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
        group_id = _single_line(group_id_getter(event), 80) if callable(group_id_getter) else ""
        if group_id:
            chain_getter = getattr(self, "_reply_message_chain_for_event", None)
            try:
                chain = await chain_getter(event, max_depth=3) if callable(chain_getter) else []
            except Exception:
                chain = []
            for row in chain if isinstance(chain, list) else []:
                if not isinstance(row, dict):
                    continue
                message_id = _single_line(row.get("message_id"), 120)
                if not message_id:
                    continue
                observed = self._group_image_summary_from_observation(
                    group_id=group_id,
                    sender_id="",
                    text="",
                    message_id=message_id,
                )
                if observed:
                    return observed, True

        cached = self._group_image_cached_summary_from_sources(sources)
        if cached:
            return cached, True
        if not bool(self._private_image_setting("enable_group_image_understanding", False)):
            return "", False
        text_getter = getattr(self, "_group_observation_event_text", None)
        user_text = _single_line(
            text_getter(event) if callable(text_getter) else getattr(event, "message_str", ""),
            260,
        )
        try:
            summary = await self._transcribe_private_inbound_images(
                sources,
                umo=_single_line(getattr(event, "unified_msg_origin", ""), 160),
                user_text=user_text,
                cache_scope="group_image",
                task_name="group_reply_image_vision",
                log_subject="群聊引用图片",
                namespace="group_reply_vision",
            )
        except Exception as exc:
            logger.warning("群聊引用图片识别失败: %s", _single_line(exc, 160))
            return "", False
        if summary:
            logger.info(
                "群聊引用图片已注入视觉摘要: group=%s images=%s",
                group_id or "unknown",
                len(sources),
            )
        return _single_line(summary, self._private_image_vision_text_limit(len(sources))), True
