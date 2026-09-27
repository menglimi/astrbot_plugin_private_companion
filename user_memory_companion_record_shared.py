# -*- coding: utf-8 -*-
"""伴侣记忆与对话情节开放回路。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（38 个方法 + 2 个模块级名字 + 0 个类级赋值 / 1405 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from .authoritative_private_memory import (
    AuthoritativePrivateMemoryError,
    AuthoritativePrivateMemoryStore,
    apply_private_memory_content,
    private_memory_content,
)
from .companion_memory_records import normalize_memory_items, relevant_memory_items
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_sections,
)
from .expression_scope_ownership import bind_expression_item
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import logger, _render_user_memory_background_prompt, _render_user_memory_labeled_section
from datetime import datetime
from typing import Any





_REQ041_DIALOGUE_EPISODE_FIELDS = (
    "dialogue_episodes",
    "open_loops",
    "episode_message_count",
    "last_episode_refresh_at",
    "dialogue_episode_retry_after",
    "dialogue_episode_last_error",
    "dialogue_episode_running_at",
)

_REQ041_COMPANION_MEMORY_FIELDS = (
    "companion_memory",
    "last_memory_refresh_at",
    "companion_memory_retry_after",
    "companion_memory_last_error",
    "companion_memory_running_at",
)



class _user_memory_companion_recordHostRef:
    """延迟引用宿主 user_memory_companion_record 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import user_memory_companion_record as _host_module

        return getattr(_host_module, name)


_user_memory_companion_record_host = _user_memory_companion_recordHostRef()
