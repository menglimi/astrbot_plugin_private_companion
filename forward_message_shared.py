# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Plain
except ImportError:
    from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest

from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)
from .helpers import _group_link_message_context, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


def _render_conversation_section_labeled(section: PromptSection | None) -> str:
    if section is None:
        return ""
    return render_prompt_sections(
        [section],
        mode=PromptRenderMode.LABELED_BLOCK,
    )


def _render_conversation_section_body(section: PromptSection | None) -> str:
    if section is None:
        return ""
    return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)



class _forward_messageHostRef:
    """延迟引用宿主 forward_message 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import forward_message as _host_module

        return getattr(_host_module, name)


_forward_message_host = _forward_messageHostRef()
