# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
from typing import Any


from .constants import _REASON_TEXT
from .helpers import (
    _normalize_outbound_punctuation_flow,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
    _record_unanswered_proactive,
    _unanswered_proactive_count,
    normalize_legacy_tag_text,
)
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger

from .daily_state_tick_shared import (
    _now_ts,
    _today_key,
    logger,
)
from .daily_state_tick_shared import logger
from .daily_state_tick_tick_core import DailyStateTickTickCoreMixin
from .daily_state_tick_route_precheck import DailyStateTickRoutePrecheckMixin
from .daily_state_tick_post_send_sidecar import DailyStateTickPostSendSidecarMixin
class DailyStateTickMixin(DailyStateTickPostSendSidecarMixin, DailyStateTickRoutePrecheckMixin, DailyStateTickTickCoreMixin):
    """Execute one user's proactive tick outside the daily-state capability module."""
