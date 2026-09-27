# -*- coding: utf-8 -*-
"""proactive_engine 的跨域共享模块级工具（叶子模块）。

由 tmp/refactor/hoist_engine_shared.py 从 proactive_engine.py 机械提升。
**叶子模块**：只依赖标准库与本仓库的通用模块，绝不 import 任何
`proactive_engine*` 模块 —— 否则会与宿主形成环形 import。

为什么必须独立成模块
--------------------
这些函数被 proactive_engine 的**多个域**引用。若各域 `from ..proactive_engine
import x` 回导入宿主，宿主又在 import 各域 → 循环导入；若各域各留一份副本，
则宿主侧对它的改动（含测试 patch）不再统一。提升为共享叶子是唯一干净解。
"""
from __future__ import annotations

from typing import Any

from .helpers import _single_line
from .persona_config import runtime_persona_setting
def _engine_proactive_window_timezone(owner: Any) -> str:
    """Resolve the scheduling timezone without requiring another mixin."""

    getter = getattr(owner, "_proactive_window_timezone", None)
    if callable(getter):
        try:
            value = _single_line(getter(), 64)
        except Exception:
            value = ""
        if value:
            return value
    return (
        _single_line(
            getattr(owner, "environment_perception_timezone", ""),
            64,
        )
        or "Asia/Shanghai"
    )

def _persona_provider_id(owner: Any, canonical_key: str, legacy_attr: str, quick_role: str) -> str:
    """Resolve canonical persona provider settings while preserving test harnesses."""
    fallback = str(getattr(owner, legacy_attr, "") or "").strip()
    if not callable(getattr(owner, "persona_setting", None)):
        return fallback
    mode = str(getattr(owner, "provider_config_mode", "quick") or "quick").strip().lower()
    if mode != "quick":
        return str(runtime_persona_setting(owner, canonical_key, fallback) or "").strip()
    complex_id = str(runtime_persona_setting(owner, "COMPLEX_REASONING_PROVIDER_ID", "") or "").strip()
    if quick_role == "complex":
        return complex_id or fallback
    if quick_role == "creative":
        creative_id = str(runtime_persona_setting(owner, "CREATIVE_MODEL_PROVIDER_ID", "") or "").strip()
        return creative_id or complex_id or fallback
    fast_id = str(runtime_persona_setting(owner, "FAST_RESPONSE_PROVIDER_ID", "") or "").strip()
    return fast_id or complex_id or fallback



class _EngineHostRef:
    """延迟引用宿主 proactive_engine 模块，保证 monkey-patch 宿主全局名对全部域生效。"""
    def __getattr__(self, name):
        from . import proactive_engine as _host_module
        return getattr(_host_module, name)


_engine_host = _EngineHostRef()
