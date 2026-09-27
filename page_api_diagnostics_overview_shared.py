# -*- coding: utf-8 -*-
"""概览与统计域。

由 tools/split_mixin_domain.py 从 page_api_diagnostics.py 机械抽取（9 个方法 + 1 个模块级名字 + 0 个类级赋值 / 1187 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiDiagnosticsMixin）。
"""
from __future__ import annotations

import functools
import hashlib
import time
from .helpers import _today_key
from .persona_config import runtime_persona_setting
from .planning import evaluate_daily_plan_quality
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



def _multi_persona_page_context(function):
    @functools.wraps(function)
    async def wrapper(self, *args, **kwargs):
        plugin = getattr(self, "plugin", None)
        activator = getattr(plugin, "_activate_persona_id", None)
        primary_getter = getattr(plugin, "_primary_persona_id", None)
        pid = primary_getter() if callable(primary_getter) else ""
        active_getter = getattr(plugin, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        token = (
            activator(pid, allow_inactive=True)
            if not active and callable(activator) and pid
            else None
        )
        try:
            return await function(self, *args, **kwargs)
        finally:
            deactivator = getattr(plugin, "_deactivate_persona_for_event", None)
            if token is not None and callable(deactivator):
                deactivator(token)
    return wrapper



class _page_api_diagnostics_overviewHostRef:
    """延迟引用宿主 page_api_diagnostics_overview 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_diagnostics_overview as _host_module

        return getattr(_host_module, name)


_page_api_diagnostics_overview_host = _page_api_diagnostics_overviewHostRef()
