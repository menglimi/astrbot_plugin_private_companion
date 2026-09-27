# -*- coding: utf-8 -*-
"""generation 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1304 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import re
import time
from .conversation_prompt_section import (
    PromptDocument,
    PromptLabelStyle,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _safe_int, _single_line, _split_address_terms
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from .segmented_message import LLM_SEGMENT_MARKER, split_llm_controlled_text
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _proactive_message_generationHostRef:
    """延迟引用宿主 proactive_message_generation 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_message_generation as _host_module

        return getattr(_host_module, name)


_proactive_message_generation_host = _proactive_message_generationHostRef()
