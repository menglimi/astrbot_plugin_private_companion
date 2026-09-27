# -*- coding: utf-8 -*-
"""detail 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（41 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1404 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import re
import uuid
import zoneinfo
from .conversation_prompt_section import PromptSection
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .model_routing import scope_allows
from .persona_config import runtime_persona_setting
from .planning import (
    build_detail_enhancement_prompt,
    build_detail_enhancement_prompt_section,
    generate_detail_enhancement,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _today_key(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_today_key")(*args, **kwargs)


def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)



class _daily_state_detailHostRef:
    """延迟引用宿主 daily_state_detail 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import daily_state_detail as _host_module

        return getattr(_host_module, name)


_daily_state_detail_host = _daily_state_detailHostRef()
