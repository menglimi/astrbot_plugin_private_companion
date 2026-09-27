# -*- coding: utf-8 -*-
"""QQ Zone automated publishing windows, plans, and lifecycle orchestration."""
from __future__ import annotations

import json
import hashlib
import random
import re
import sys
import time
from datetime import datetime
from typing import Any


from .helpers import _day_start_ts, _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger
from .qzone_schedule_domain import (
    hhmm_to_minutes, length_profile_range, length_profile_sequence, merge_windows,
    ngram_shared_count, parse_windows, slot_is_night, subtract_ranges, text_length_ok,
)

logger = get_module_logger(__name__)


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

# Publish-window templates offered as one-click presets in the WebUI. Users stay
# free to edit them or add any number of extra windows afterwards.
QZONE_WINDOW_TEMPLATE_DOUBLE = "07:00-10:00\n18:00-22:00"
QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT = "00:30-03:30\n07:00-10:00\n18:00-22:00"
# Night range mirrors the existing insomnia-night definition (23:00-05:59).
QZONE_NIGHT_RANGES = ((0, 6 * 60), (23 * 60, 24 * 60))
QZONE_LENGTH_PROFILES = {
    "short": (20, 45),
    "medium": (45, 80),
    "long": (80, 110),
}
QZONE_LENGTH_HARD_LIMIT = 120
# Floor for spacing several posts inside one day so a high max_daily can never
# collapse into a burst of back-to-back posts.
QZONE_INTRA_DAY_GAP_FLOOR_MINUTES = 45
# A plan item that keeps failing retires instead of retrying every tick all day.
QZONE_PLAN_ITEM_MAX_ATTEMPTS = 3

_QZONE_COMPAT_BASELINE = {
    "QZONE_WINDOW_TEMPLATE_DOUBLE": QZONE_WINDOW_TEMPLATE_DOUBLE,
    "QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT": QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT,
    "QZONE_NIGHT_RANGES": QZONE_NIGHT_RANGES,
    "QZONE_LENGTH_PROFILES": dict(QZONE_LENGTH_PROFILES),
    "QZONE_LENGTH_HARD_LIMIT": QZONE_LENGTH_HARD_LIMIT,
    "QZONE_INTRA_DAY_GAP_FLOOR_MINUTES": QZONE_INTRA_DAY_GAP_FLOOR_MINUTES,
    "QZONE_PLAN_ITEM_MAX_ATTEMPTS": QZONE_PLAN_ITEM_MAX_ATTEMPTS,
}


def _qzone_compat_constant(name: str) -> Any:
    """Honor legacy patches applied through the qzone_integration facade."""
    local_value = globals()[name]
    facade = sys.modules.get(f"{__package__}.qzone_integration")
    if facade is None:
        return local_value
    facade_value = getattr(facade, name, local_value)
    if facade_value != _QZONE_COMPAT_BASELINE.get(name):
        return facade_value
    return local_value



class _qzone_scheduleHostRef:
    """延迟引用宿主 qzone_schedule 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import qzone_schedule as _host_module

        return getattr(_host_module, name)


_qzone_schedule_host = _qzone_scheduleHostRef()
