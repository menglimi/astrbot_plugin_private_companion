# -*- coding: utf-8 -*-
"""天气域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（78 个方法 + 8 个模块级名字 + 1 个类级赋值 / 2550 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
import unicodedata
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, PLACEMENT_TURN_TAIL, get_conversation_injection_plan
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



def _openmeteo_weather_description(code: Any) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "未知"
    if code == 0:
        return "晴天"
    if code == 1:
        return "少云"
    if code == 2:
        return "多云"
    if code == 3:
        return "阴天"
    if code in {45, 48}:
        return "雾"
    if 51 <= code <= 55:
        return "毛毛雨"
    if code in {56, 57}:
        return "冻毛毛雨"
    if 61 <= code <= 65:
        return "降雨"
    if code in {66, 67}:
        return "冻雨"
    if 71 <= code <= 77:
        return "降雪"
    if 80 <= code <= 82:
        return "阵雨"
    if code in {85, 86}:
        return "阵雪"
    if 95 <= code <= 99:
        return "雷暴"
    return "未知"

# 和风天气预警接口使用独立的 API Host，并支持 JWT 或 API Key 认证。
# 保留颜色等级的顺序，供缓存层和上层提示词按最低等级筛选；解析层
# 始终保留完整数据。
_QWEATHER_ALERT_COLOR_RANK = {
    "蓝": 0,
    "蓝色": 0,
    "blue": 0,
    "yellow": 1,
    "黄": 1,
    "黄色": 1,
    "orange": 2,
    "橙": 2,
    "橙色": 2,
    "red": 3,
    "红": 3,
    "红色": 3,
}

_QWEATHER_ALERT_SEVERITY_RANK = {
    "unknown": 0,
    "minor": 1,
    "moderate": 2,
    "severe": 3,
    "extreme": 4,
}

def _qweather_alert_text(value: Any, limit: int = 512) -> str:
    """Normalize a provider field without allowing multiline/oversized cache data."""

    text = _single_line(value, limit * 2)
    if not text:
        return ""
    return text[:limit]

def _qweather_alert_first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return ""

def _qweather_alert_string_list(value: Any, *, limit: int = 16, item_limit: int = 80) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            item = _qweather_alert_first(item, "code", "name", "type")
        text = _qweather_alert_text(item, item_limit)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result

def _qweather_alert_color(value: Any) -> tuple[str, str]:
    """Return a human-readable color and its provider code."""

    code = ""
    name = ""
    if isinstance(value, dict):
        code = _qweather_alert_text(value.get("code"), 32).lower()
        name = _qweather_alert_text(value.get("name"), 32)
    else:
        name = _qweather_alert_text(value, 32)
        code = name.lower()
    # QWeather's current API returns color.code (blue/yellow/orange/red), while
    # older integrations and some compatible providers return Chinese labels.
    aliases = {
        "blue": "蓝色",
        "yellow": "黄色",
        "orange": "橙色",
        "red": "红色",
        "bluealert": "蓝色",
        "yellowalert": "黄色",
        "orangealert": "橙色",
        "redalert": "红色",
    }
    normalized_code = aliases.get(code, code)
    if normalized_code in {"蓝", "蓝色"}:
        name = "蓝色"
    elif normalized_code in {"黄", "黄色"}:
        name = "黄色"
    elif normalized_code in {"橙", "橙色"}:
        name = "橙色"
    elif normalized_code in {"红", "红色"}:
        name = "红色"
    elif not name:
        name = normalized_code
    return _qweather_alert_text(name, 32), _qweather_alert_text(code, 32)

def _qweather_alert_rank(value: Any) -> int:
    text = _qweather_alert_text(value, 32).strip().lower()
    if text in _QWEATHER_ALERT_COLOR_RANK:
        return _QWEATHER_ALERT_COLOR_RANK[text]
    # A severity value is useful for non-Chinese/global warning feeds.
    return _QWEATHER_ALERT_SEVERITY_RANK.get(text, 0)




# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _today_key(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_today_key")(*args, **kwargs)


def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)



class _daily_state_weatherHostRef:
    """延迟引用宿主 daily_state_weather 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import daily_state_weather as _host_module

        return getattr(_host_module, name)


_daily_state_weather_host = _daily_state_weatherHostRef()
