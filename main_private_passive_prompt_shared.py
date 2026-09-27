# -*- coding: utf-8 -*-
"""private_passive_prompt。

由 tools/split_main_domain.py 从 main.py 机械抽取（33 个方法 / 1235 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .prompt_surface import CollectedPromptContext, PromptSurface
from .segmented_message import flatten_component_chunks
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from typing import Any

from .logging_util import get_module_logger
from .main_shared import Plain

logger = get_module_logger(__name__)



class _main_private_passive_promptHostRef:
    """延迟引用宿主 main_private_passive_prompt 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_private_passive_prompt as _host_module

        return getattr(_host_module, name)


_main_private_passive_prompt_host = _main_private_passive_promptHostRef()
