# -*- coding: utf-8 -*-
"""出站守卫 / 内容净化域。

由 tools/split_main_domain.py 从 main.py 机械抽取（40 个方法 / 1382 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import (
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_outbound_control_blocks,
)
from .main_shared import _multi_persona_event_context, _plugin_instance_can_dispatch
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from .tool_history_sanitizer import sanitize_history_image_blocks, sanitize_openai_tool_history
from .wake_message_context import restore_wake_message_request
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from typing import Any

try:
    from astrbot.api.message_components import Image, Plain
except ImportError:  # pragma: no cover - 兼容旧版 AstrBot
    from astrbot.core.message.components import Image, Plain

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _main_outbound_guardHostRef:
    """延迟引用宿主 main_outbound_guard 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_outbound_guard as _host_module

        return getattr(_host_module, name)


_main_outbound_guard_host = _main_outbound_guardHostRef()
