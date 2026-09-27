# -*- coding: utf-8 -*-
"""planning 域的跨模块共享件（import 绑定 + 模块级常量）。

由 tmp/split4/mod_split.py 从 planning.py 机械抽取：
11 条 import 语句 + 1 个模块级常量，逐字节原样。
宿主 planning.py 与各 planning_partNN.py 均从本模块 import，
本模块不 import 任何同族模块（叶子模块，杜绝循环 import）。
"""

from __future__ import annotations

import inspect

import re

import time

from datetime import datetime

from typing import Any

from .constants import DEFAULT_DAILY_PLAN_ITEMS

from .helpers import _safe_float, _safe_int, _single_line, _today_key

from .persona_config import runtime_persona_setting

from .logging_util import get_module_logger

from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
)

logger = get_module_logger(__name__)
