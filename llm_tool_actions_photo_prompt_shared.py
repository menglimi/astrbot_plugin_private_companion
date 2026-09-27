# -*- coding: utf-8 -*-
"""生图提示与当前媒体域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（24 个方法 + 8 个模块级名字 + 0 个类级赋值 / 1034 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _redact_outbound_secrets, _single_line, _strip_internal_message_blocks
from .llm_tool_actions_shared import (
    PHOTO_TOOL_SILENT_SENTINEL,
    _render_tool_prompt_section_labeled,
    _render_tool_prompt_section_labeled_inline,
    logger,
)
from .persona_config import runtime_persona_setting
from .reaction_expression import (
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
)
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path
from typing import Any



_PHOTO_TOOL_REDACTED_LOCAL_PATH = "[本地路径已隐藏]"

_PHOTO_TOOL_WINDOWS_PATH_START_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\(?=[^\\/]))"
)

_PHOTO_TOOL_POSIX_PATH_START_RE = re.compile(
    r"(?<![\w/])/(?=(?:"
    r"(?:Users|home|tmp|var|etc|opt|srv|root|mnt|run|private|usr|workspace|workspaces|app|data)/"
    r"|(?:[^/\s]+/){2,}[^/\s]+"
    r"|[^/\s]+/[^/\s]+\.[A-Za-z0-9]{1,12}(?:\s|$|[),;，；。])"
    r"))",
    flags=re.I,
)

_PHOTO_TOOL_RELATIVE_PATH_START_RE = re.compile(
    r"(?<![A-Za-z0-9.:/\\])(?:\.{1,2}[\\/])?(?:[^\\/\s,，;；]+[\\/]){2,}"
    r"[^\\/\s,，;；]+\.[A-Za-z0-9]{1,12}",
    flags=re.I,
)

_PHOTO_TOOL_HTTP_URL_RE = re.compile(
    r"https?://[^\s<>\[\]{}\"']+",
    flags=re.I,
)

_CURRENT_MEDIA_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".jfif", ".avif"}
)

_CURRENT_MEDIA_MAX_BYTES = 32 * 1024 * 1024

_CURRENT_MEDIA_MAX_AGE_SECONDS = 30 * 60



class _llm_tool_actions_photo_promptHostRef:
    """延迟引用宿主 llm_tool_actions_photo_prompt 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import llm_tool_actions_photo_prompt as _host_module

        return getattr(_host_module, name)


_llm_tool_actions_photo_prompt_host = _llm_tool_actions_photo_promptHostRef()
