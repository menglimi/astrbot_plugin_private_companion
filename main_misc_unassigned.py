# -*- coding: utf-8 -*-
"""misc_unassigned。

由 tools/split_main_domain.py 从 main.py 机械抽取（8 个方法 / 494 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import PromptSection
from .event_dispatch import EventDispatchMixin, _ON_WAITING_LLM_REQUEST
from .helpers import _safe_float, _safe_int, _single_line
from .main_shared import _ACTIVE_PERSONA_ID, _multi_persona_event_context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.entities import LLMResponse
from types import SimpleNamespace
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginMiscUnassignedMixin:
    """misc_unassigned（从 PrivateCompanionPlugin 拆出）。"""

    @_ON_WAITING_LLM_REQUEST(priority=100000)
    @_multi_persona_event_context
    async def guard_pending_message_debounce_hook(
        self,
        event: AstrMessageEvent,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.guard_pending_message_debounce(
            self,
            event,
            *args,
            **kwargs,
        )

    @filter.on_llm_response(priority=100000)
    @_multi_persona_event_context
    async def settle_pending_message_debounce_hook(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        await EventDispatchMixin.settle_pending_message_debounce(
            self,
            event,
            resp,
            *args,
            **kwargs,
        )

    @staticmethod
    def _normalize_external_image_endpoint_enabled(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
            return False
        return default

    def _materialize_conversation_system_block(
        self,
        req: ProviderRequest,
        *,
        section: PromptSection,
        marker: str = "",
        priority: int = 50,
        placement: str = PLACEMENT_DYNAMIC_SYSTEM,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Materialize one authored system section without changing its wire shape."""
        if not isinstance(section, PromptSection):
            raise TypeError("conversation system block requires PromptSection")
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        return plan.materialize_system_block(
            req,
            section=section,
            marker=marker,
            priority=priority,
            placement=placement,
            metadata=metadata,
        )
