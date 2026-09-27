# -*- coding: utf-8 -*-
"""群成员风控：用保守的模型判定维护按群隔离的成员静默状态。"""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from typing import Any


from .helpers import _safe_float, _safe_int, _single_line, _strip_group_member_safety_markers
from .persona_config import runtime_persona_setting
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    exact_text,
    prompt_document,
    prompt_section,
    prompt_text,
    render_prompt_document,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _group_member_safetyHostRef:
    """延迟引用宿主 group_member_safety 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import group_member_safety as _host_module

        return getattr(_host_module, name)


_group_member_safety_host = _group_member_safetyHostRef()
