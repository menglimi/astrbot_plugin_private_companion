# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import re
from typing import Any

from .helpers import _flat_get, _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)


SCENE_CONTEXT_VERSION = 3


def _temperature_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scene_temperature_facts(weather_data: dict[str, Any], weather_text: str) -> dict[str, Any]:
    def first_number(keys: tuple[str, ...]) -> float | None:
        for key in keys:
            number = _temperature_number(weather_data.get(key))
            if number is not None:
                return number
        return None

    temperature = first_number(("temperature_c", "temperature", "temp", "temp_c", "now_temp"))
    feels_like = first_number(("feels_like_c", "feels_like", "feelsLike", "feelslike", "apparent_temperature"))
    text = str(weather_text or "")
    if feels_like is None:
        match = re.search(r"(?:体感(?:温度)?|feels[\s_-]*like)\s*[：:=]?\s*(-?\d+(?:\.\d+)?)", text, flags=re.I)
        feels_like = _temperature_number(match.group(1)) if match else None
    if temperature is None:
        match = re.search(r"(?:当前(?:温度)?|实时(?:温度)?|气温|温度|temperature|temp)\s*[：:=]?\s*(-?\d+(?:\.\d+)?)", text, flags=re.I)
        if not match:
            match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*[cC]|℃|摄氏度)", text, flags=re.I)
        temperature = _temperature_number(match.group(1)) if match else None
    effective = feels_like if feels_like is not None else temperature
    thermal = "unknown"
    if effective is not None:
        thermal = "hot" if effective >= 28 else "warm" if effective >= 24 else "mild" if effective >= 13 else "cool" if effective >= 5 else "cold"
    return {
        "temperature_c": temperature,
        "feels_like_c": feels_like,
        "effective_c": effective,
        "thermal_level": thermal,
    }


def infer_companion_scene_category(schedule_text: Any = "", location_text: Any = "") -> tuple[str, str]:
    """Infer a coarse visual scene without inventing a location when context is ambiguous."""
    location = _single_line(location_text, 120).lower().replace(" ", "")
    schedule = _single_line(schedule_text, 360).lower().replace(" ", "")
    home_markers = (
        "在家", "家里", "家中", "回到家", "已经到家", "居家", "宅家",
        "宿舍", "公寓", "租房", "房间", "卧室", "客厅", "书桌", "床上", "被窝", "室内日常",
    )
    outdoor_markers = (
        "外出", "通勤", "路上", "外面", "出门", "上班", "上学", "逛街", "旅行",
        "商场", "公司", "办公室", "工作地点", "教室", "学校", "图书馆", "咖啡店", "食堂", "街头",
    )

    if location in {"家", "家里", "家中"} or any(marker in location for marker in home_markers):
        return "home", "居家室内"
    if any(marker in location for marker in outdoor_markers):
        return "outdoor", "外出"
    if any(marker in schedule for marker in home_markers):
        return "home", "居家室内"
    if any(marker in schedule for marker in outdoor_markers):
        return "outdoor", "外出"
    return "", ""



class _scene_contextHostRef:
    """延迟引用宿主 scene_context 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import scene_context as _host_module

        return getattr(_host_module, name)


_scene_context_host = _scene_contextHostRef()
