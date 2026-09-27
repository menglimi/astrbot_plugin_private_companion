# -*- coding: utf-8 -*-
from .daily_state_proactive_shared import (
    _now_ts,
    _today_key,
    logger,
)
from .daily_state_proactive_shared import logger
from .daily_state_proactive_part04 import DailyStateProactivePart04Mixin
from .daily_state_proactive_part03 import DailyStateProactivePart03Mixin
from .daily_state_proactive_part02 import DailyStateProactivePart02Mixin
from .daily_state_proactive_part01 import DailyStateProactivePart01Mixin
class DailyStateProactiveMixin(DailyStateProactivePart01Mixin, DailyStateProactivePart02Mixin, DailyStateProactivePart03Mixin, DailyStateProactivePart04Mixin):
    """proactive 域（从 DailyStateMixin 拆出）。"""
