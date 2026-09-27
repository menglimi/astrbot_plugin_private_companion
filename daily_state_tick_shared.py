# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
from typing import Any


from .constants import _REASON_TEXT
from .helpers import (
    _normalize_outbound_punctuation_flow,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    _record_unanswered_proactive,
    _unanswered_proactive_count,
    normalize_legacy_tag_text,
)
from .persona_config import runtime_persona_setting
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



class _daily_state_tickHostRef:
    """延迟引用宿主 daily_state_tick 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import daily_state_tick as _host_module

        return getattr(_host_module, name)


_daily_state_tick_host = _daily_state_tickHostRef()
