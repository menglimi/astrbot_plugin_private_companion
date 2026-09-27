# -*- coding: utf-8 -*-
"""每日终盘巡视：以脱敏运行摘要复盘插件工作并生成次日柔性纠偏。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
import zoneinfo
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


from .helpers import _redact_outbound_secrets, _safe_float, _safe_int, _single_line
from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from .logging_util import get_module_logger
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections

logger = get_module_logger(__name__)



class _daily_reviewHostRef:
    """延迟引用宿主 daily_review 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import daily_review as _host_module

        return getattr(_host_module, name)


_daily_review_host = _daily_reviewHostRef()
