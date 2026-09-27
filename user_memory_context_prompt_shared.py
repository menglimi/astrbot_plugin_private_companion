# -*- coding: utf-8 -*-
"""上下文提示词注入与画像摘要。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（44 个方法 + 1 个模块级名字 + 0 个类级赋值 / 1157 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import (
    _normalize_photo_subject_owner,
    _now_ts,
    _photo_subject_owner_prompt_label,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .persona_config import runtime_persona_setting
from .private_identity_policy import format_private_identity_anchor
from .user_memory_render_shared import _render_conversation_section_labeled
from datetime import datetime
from typing import Any



OWNER_EXCLUSIVE_RELATIONSHIP_PROMPT_MAX_CHARS = 2400



class _user_memory_context_promptHostRef:
    """延迟引用宿主 user_memory_context_prompt 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import user_memory_context_prompt as _host_module

        return getattr(_host_module, name)


_user_memory_context_prompt_host = _user_memory_context_promptHostRef()
