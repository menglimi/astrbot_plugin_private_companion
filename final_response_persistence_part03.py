# -*- coding: utf-8 -*-
"""FinalResponsePersistencePart03Mixin。

由 tools/split_mixin_domain.py 从 final_response_persistence.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 100 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 FinalResponsePersistenceMixin）。
"""
from __future__ import annotations

from .final_response_persistence_shared import logger
from .helpers import _single_line
from .segmented_message import sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent
from typing import Any



class FinalResponsePersistencePart03Mixin:
    """FinalResponsePersistencePart03Mixin（从 FinalResponsePersistenceMixin 拆出）。"""


    async def _finalize_passive_delivered_response(
        self,
        event: AstrMessageEvent,
        *,
        chain: list[Any] | tuple[Any, ...] | None = None,
        fallback_text: str = "",
        llm_segments: tuple[str, ...] = (),
        force: bool = False,
    ) -> bool:
        if event is None or not bool(
            getattr(event, "_private_companion_persistence_managed", False)
        ):
            return False
        if bool(getattr(event, "private_companion_proactive_framework", False)) and str(
            getattr(event, "_private_companion_external_proactive_source", "") or ""
        ) != "proactive_chat":
            return False
        if bool(getattr(event, "_private_companion_delivery_persisted", False)):
            return True
        if not force and not bool(getattr(event, "_has_send_oper", False)):
            return False

        delivered_chain = list(
            chain
            if chain is not None
            else getattr(event, "_private_companion_final_outbound_chain", ())
        )
        response_text = self._delivered_assistant_text_from_chain(
            delivered_chain,
            fallback_text=fallback_text,
        )
        response_text = sanitize_llm_segment_control_tokens(response_text)
        if not response_text:
            return False

        delivery_id = str(
            getattr(event, "_private_companion_delivery_id", "")
            or self._event_message_id(event)
            or f"passive:{id(event)}"
        )
        setattr(event, "_private_companion_delivery_id", delivery_id)
        duplicate, local_sections = await self._record_confirmed_outbound_state(
            event,
            response_text=response_text,
            delivery_id=delivery_id,
            llm_segments=llm_segments,
        )
        if duplicate:
            setattr(event, "_private_companion_delivery_persisted", True)
            return True

        official_written = self._stage_delivered_assistant_for_official_history(
            event=event,
            assistant_response=response_text,
        )
        if not official_written:
            official_written = await self._append_delivered_assistant_to_conversation(
                event=event,
                assistant_response=response_text,
            )
        image_history_written = False
        image_history_writer = getattr(
            self,
            "_persist_private_image_vision_summary_to_history",
            None,
        )
        if callable(image_history_writer):
            try:
                image_history_written = bool(await image_history_writer(event))
            except Exception as exc:
                logger.debug(
                    "图片视觉摘要写入用户 history 失败: %s",
                    _single_line(exc, 160),
                )
        memory_written = await self._record_final_assistant_in_livingmemory(
            umo=str(getattr(event, "unified_msg_origin", "") or ""),
            assistant_response=response_text,
            delivery_id=delivery_id,
            event=event,
        )
        memory_companion_written = False
        if bool(
            getattr(event, "_private_companion_memory_companion_plugin_names", ())
        ):
            memory_companion_written = bool(
                await self._memory_companion_record_confirmed_assistant_message(
                    event,
                    content=response_text,
                    delivery_id=delivery_id,
                )
            )
        persisted = bool(
            local_sections
            or official_written
            or image_history_written
            or memory_written
            or memory_companion_written
        )
        setattr(event, "_private_companion_delivery_persisted", True)
        return persisted
