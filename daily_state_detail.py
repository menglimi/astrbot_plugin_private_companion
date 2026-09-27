# -*- coding: utf-8 -*-
from .daily_state_detail_shared import (
    _now_ts,
    _today_key,
    logger,
)
from .daily_state_detail_shared import logger
from .daily_state_detail_part03 import DailyStateDetailPart03Mixin
from .daily_state_detail_part02 import DailyStateDetailPart02Mixin
from .daily_state_detail_part01 import DailyStateDetailPart01Mixin
class DailyStateDetailMixin(DailyStateDetailPart01Mixin, DailyStateDetailPart02Mixin, DailyStateDetailPart03Mixin):
    """detail 域（从 DailyStateMixin 拆出）。"""
