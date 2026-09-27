# -*- coding: utf-8 -*-
"""发送闸门/决策域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1318 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import os
import random
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _engine_proactive_window_timezone
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class _proactive_engine_gateHostRef:
    """延迟引用宿主 proactive_engine_gate 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_engine_gate as _host_module

        return getattr(_host_module, name)


_proactive_engine_gate_host = _proactive_engine_gateHostRef()
