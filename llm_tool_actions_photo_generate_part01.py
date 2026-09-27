# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart01Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_photo_generate.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 13 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

from .helpers import _safe_float



class LlmToolActionsPhotoGeneratePart01Mixin:
    """LlmToolActionsPhotoGeneratePart01Mixin（从 LlmToolActionsPhotoGenerateMixin 拆出）。"""


    def _photo_tool_call_timeout_seconds(self) -> float:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if not callable(getter):
            return 120.0
        try:
            cfg = getter()
        except Exception:
            return 120.0
        provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        if not isinstance(provider_settings, dict):
            return 120.0
        return _safe_float(provider_settings.get("tool_call_timeout"), 120.0, 1.0, 3600.0)
