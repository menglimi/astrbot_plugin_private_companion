# -*- coding: utf-8 -*-
from .daily_state_meal_shared import (
    _now_ts,
    _today_key,
    logger,
)
from .daily_state_meal_shared import logger
from .daily_state_meal_part03 import DailyStateMealPart03Mixin
from .daily_state_meal_part02 import DailyStateMealPart02Mixin
from .daily_state_meal_part01 import DailyStateMealPart01Mixin
class DailyStateMealMixin(DailyStateMealPart01Mixin, DailyStateMealPart02Mixin, DailyStateMealPart03Mixin):
    """meal 域（从 DailyStateMixin 拆出）。"""
