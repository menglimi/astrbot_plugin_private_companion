# -*- coding: utf-8 -*-
"""QQ Zone post composition, validation, history, and image preparation."""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent

from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger

logger = get_module_logger(__name__)




class _qzone_publishHostRef:
    """延迟引用宿主 qzone_publish 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import qzone_publish as _host_module

        return getattr(_host_module, name)


_qzone_publish_host = _qzone_publishHostRef()
