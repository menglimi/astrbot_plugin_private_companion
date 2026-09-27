# -*- coding: utf-8 -*-
from .daily_state_timer_shared import (
    _now_ts,
    logger,
)
from .daily_state_timer_shared import logger
from .daily_state_timer_part04 import DailyStateTimerPart04Mixin
from .daily_state_timer_part03 import DailyStateTimerPart03Mixin
from .daily_state_timer_part02 import DailyStateTimerPart02Mixin
from .daily_state_timer_part01 import DailyStateTimerPart01Mixin
class DailyStateTimerMixin(DailyStateTimerPart01Mixin, DailyStateTimerPart02Mixin, DailyStateTimerPart03Mixin, DailyStateTimerPart04Mixin):
    """计时器域（从 DailyStateMixin 拆出）。"""
