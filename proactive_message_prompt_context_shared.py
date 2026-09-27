# -*- coding: utf-8 -*-
"""prompt_context 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（48 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1236 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _split_address_terms
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _proactive_prompt_part
from .reaction_expression import normalize_reaction_expression_intent, reaction_expression_high_frequency
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----



class _proactive_message_prompt_contextHostRef:
    """延迟引用宿主 proactive_message_prompt_context 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_message_prompt_context as _host_module

        return getattr(_host_module, name)


_proactive_message_prompt_context_host = _proactive_message_prompt_contextHostRef()
