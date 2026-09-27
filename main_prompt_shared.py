# -*- coding: utf-8 -*-
"""LLM 请求提示词编排域。

由 tools/split_main_domain.py 从 main.py 机械抽取（41 个方法 / 1794 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import sys

import asyncio
import re
import time
from .conversation_injection_plan import (
    DELIVERY_GROUP_MARKER_METADATA_KEY,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_cdata, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .main_shared import _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES, _multi_persona_event_context
from .persona_config import runtime_persona_setting
from .private_scope_isolation import sanitize_private_request_group_artifacts
from .prompt_surface import CollectedPromptContext
from .segmented_message import LLM_SEGMENT_MARKER, sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from typing import Any, Iterable

from .logging_util import get_module_logger

logger = get_module_logger(__name__)


def _default_segmenting_prompt_for(owner: Any) -> str:
    """解析默认分段提示词模板。

    该模板是宿主类的 @staticmethod（仍住在 ``main.py``）。拆分到 mixin 后不能
    再用 ``PrivateCompanionPlugin.xxx()`` 硬引用宿主类（会触发循环导入），也不能
    只依赖 ``type(owner)``：单测常用轻量 harness 假对象直接调用本方法。
    因此按「实例 → 类 → 宿主类（延迟解析）」顺序查找，取第一个可用的实现。
    """
    for candidate in (owner, type(owner), _host_plugin_class()):
        if candidate is None:
            continue
        resolver = getattr(candidate, "_default_llm_controlled_segmenting_prompt", None)
        if callable(resolver):
            return str(resolver())
    raise AttributeError(
        "_default_llm_controlled_segmenting_prompt 无法解析："
        "请确认宿主类 PrivateCompanionPlugin 已混入本 mixin（main.py）"
    )


def _host_plugin_class() -> Any:
    """延迟取得宿主类 ``PrivateCompanionPlugin``，避免模块级循环导入。

    仅在 ``owner`` / ``type(owner)`` 都提供不了解析目标时才会被调用，例如单测
    用轻量 harness 假对象直调 mixin 方法。此时宿主模块一定已加载完毕。
    """
    module = sys.modules.get(f"{__package__}.main") if __package__ else None
    if module is None:
        return None
    return getattr(module, "PrivateCompanionPlugin", None)



class _main_promptHostRef:
    """延迟引用宿主 main_prompt 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_prompt as _host_module

        return getattr(_host_module, name)


_main_prompt_host = _main_promptHostRef()
