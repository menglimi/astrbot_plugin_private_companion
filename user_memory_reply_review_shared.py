# -*- coding: utf-8 -*-
"""回答审查与行动反馈闭环。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（44 个方法 + 6 个模块级名字 + 0 个类级赋值 / 1485 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import math
import re
import time
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
from .user_memory_render_shared import (
    logger,
    _render_conversation_section_labeled,
    _render_user_memory_background_prompt,
    _render_user_memory_labeled_section,
)
from astrbot.api.event import AstrMessageEvent
from datetime import datetime
from typing import Any





_LATE_CLOCK = (
    r"(?:(?:十一|11|23)\s*[点點](?:\s*(?:半|一刻|三刻|\d{1,2}))?"
    r"(?![点點]?\s*(?:左右|前后|以后|以前|多|来))"
    r"|23\s*[:：]\s*\d{1,2})"
)

_LATE_CLOCK_INTRO = r"(?:快|差不多|都|已经|马上|就要)"

_SLEEP_CUE = (
    r"(?:困不困|困了|该睡|该休息|早点睡|去睡|睡觉|睡吧|睡了|晚安|熬夜|夜深|深夜|歇息|休息|别熬|快睡)"
)

_IMPLICIT_LATE = (
    r"(?:(?:时间|时候|天色).{0,4}(?:不早|(?:这么|很|太)晚)|"
    r"(?:都|已经|这会儿|现在).{0,4}(?:不早|(?:这么|很|太)晚)|"
    r"(?:不早|(?:这么|很|太)晚).{0,3}(?:了|啦|咯))"
)

_LATE_CLAIM_GAP = r"[^。\n]{0,6}"

_CLAUSE_BOUNDARY = r"(?:^|(?<=[。！？!?；;\n])|[。！？!?；;])"



class _user_memory_reply_reviewHostRef:
    """延迟引用宿主 user_memory_reply_review 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import user_memory_reply_review as _host_module

        return getattr(_host_module, name)


_user_memory_reply_review_host = _user_memory_reply_reviewHostRef()
