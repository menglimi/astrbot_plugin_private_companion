# -*- coding: utf-8 -*-
"""人格对齐/模型评审域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1048 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import hashlib
import time
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .memory_context_policy import core_memory_usage_contract_section
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _persona_provider_id
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class _proactive_engine_personaHostRef:
    """延迟引用宿主 proactive_engine_persona 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_engine_persona as _host_module

        return getattr(_host_module, name)


_proactive_engine_persona_host = _proactive_engine_personaHostRef()
