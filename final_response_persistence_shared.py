from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import uuid
from dataclasses import dataclass, field, is_dataclass, replace
from functools import wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain, Record
from astrbot.core.agent.message import AssistantMessageSegment, TextPart
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from .helpers import (
    _format_history_media_marker,
    _has_history_media_marker,
    _now_ts,
    _safe_float,
    _single_line,
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
)
from .llm_tool_actions import PHOTO_TOOL_SILENT_SENTINEL
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from .logging_util import get_module_logger

if TYPE_CHECKING:
    from .final_response_persistence import DeliveryLedger

logger = get_module_logger(__name__)


_DELIVERY_TASK_LABELS = frozenset(
    {
        "segmented_llm_remainder",
        # Plugin-owned TTS sends the voice-bearing first chunk through the
        # normal result and releases the remaining text asynchronously. Keep
        # final history persistence behind that remainder as well.
        "tts_reply_remainder",
        # A platform upload can outlive AstrBot's LLM tool timeout. The same
        # send keeps running after the tool wait is cancelled, so final history
        # persistence must wait for its eventual platform acknowledgement.
        "photo_tool_delivery",
    }
)


def collect_proactive_delivery(
    function: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Attach the chains confirmed by the common proactive send primitive."""

    @wraps(function)
    async def wrapped(self: Any, umo: str, *args: Any, **kwargs: Any) -> Any:
        coordinator = self._final_response_persistence_coordinator()
        ledger = coordinator.begin_proactive(umo)
        try:
            outcome = await function(self, umo, *args, **kwargs)
        except BaseException:
            coordinator._reset_context(ledger)
            raise
        outcome = coordinator.finish_proactive(ledger, outcome)
        # TTS 会把可见正文除首块外的部分放到后台异步补发。主链返回时
        # confirmed_chains 只含首块，finish_proactive 据此覆盖出的
        # delivered_text 会丢掉消息尾部（例如承诺的后半句）。
        # 当整轮已判定 complete 时，改回用发送前的原始全文归档。
        original_text = str(args[0] or "").strip() if args else ""
        if (
            original_text
            and getattr(outcome, "complete", False)
            and str(getattr(outcome, "delivered_text", "") or "").strip()
            != original_text
        ):
            try:
                outcome = replace(outcome, delivered_text=original_text)
            except (TypeError, ValueError):
                # outcome 不是 dataclass 或字段缺失时保持原样
                pass
        return outcome

    return wrapped



class _final_response_persistenceHostRef:
    """延迟引用宿主 final_response_persistence 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import final_response_persistence as _host_module

        return getattr(_host_module, name)


_final_response_persistence_host = _final_response_persistenceHostRef()



_CURRENT_DELIVERY: contextvars.ContextVar[DeliveryLedger | None] = (
    contextvars.ContextVar("private_companion_final_delivery", default=None)
)


# Backwards-compatible re-export. These helpers live in the host module, but
# resolving them lazily avoids a circular import during host initialisation.
_HOST_REEXPORTS = frozenset(
    {
        "ConfirmedDelivery",
        "DeliveryLedger",
        "FinalResponsePersistenceCoordinator",
    }
)


def __getattr__(name: str):
    if name in _HOST_REEXPORTS:
        from . import final_response_persistence as _host

        return getattr(_host, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _HOST_REEXPORTS)
