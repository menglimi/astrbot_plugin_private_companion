# -*- coding: utf-8 -*-
"""分段回复域。

由 tools/split_main_domain.py 从 main.py 机械抽取（20 个方法 / 1150 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations
try:
    from astrbot.api.message_components import Plain, Reply
except ImportError:
    from astrbot.api.message_components import Plain
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None

import asyncio
import re
import time
from .helpers import _safe_int, _single_line
from .main_shared import _multi_persona_event_context, _strip_chain_plain_thinking
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    has_fenced_llm_segment_marker,
    parse_llm_segment_control,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
    strip_llm_segment_marker_lines,
)
from astrbot.api.event import AstrMessageEvent, filter
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _main_segmented_replyHostRef:
    """延迟引用宿主 main_segmented_reply 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_segmented_reply as _host_module

        return getattr(_host_module, name)


_main_segmented_reply_host = _main_segmented_replyHostRef()
