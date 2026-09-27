# -*- coding: utf-8 -*-
"""external_share 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（34 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1236 行）。
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
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from typing import Any
from urllib.parse import urlparse



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----



class _proactive_message_external_shareHostRef:
    """延迟引用宿主 proactive_message_external_share 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_message_external_share as _host_module

        return getattr(_host_module, name)


_proactive_message_external_share_host = _proactive_message_external_shareHostRef()
