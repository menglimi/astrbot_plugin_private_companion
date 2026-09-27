# -*- coding: utf-8 -*-
"""meal 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（33 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1135 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import hashlib
import random
import re
import uuid
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from datetime import datetime
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



class _daily_state_mealHostRef:
    """延迟引用宿主 daily_state_meal 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import daily_state_meal as _host_module

        return getattr(_host_module, name)


_daily_state_meal_host = _daily_state_mealHostRef()
