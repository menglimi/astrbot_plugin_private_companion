# -*- coding: utf-8 -*-
from .qzone_schedule_shared import (
    QZONE_INTRA_DAY_GAP_FLOOR_MINUTES,
    QZONE_LENGTH_HARD_LIMIT,
    QZONE_LENGTH_PROFILES,
    QZONE_NIGHT_RANGES,
    QZONE_PLAN_ITEM_MAX_ATTEMPTS,
    QZONE_WINDOW_TEMPLATE_DOUBLE,
    QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT,
    _QZONE_COMPAT_BASELINE,
    _persona_provider_id,
    _qzone_compat_constant,
    logger,
)
from .qzone_schedule_shared import logger
from .qzone_schedule_part03 import QzoneSchedulePart03Mixin
from .qzone_schedule_part02 import QzoneSchedulePart02Mixin
from .qzone_schedule_part01 import QzoneSchedulePart01Mixin
class QzoneScheduleMixin(QzoneSchedulePart01Mixin, QzoneSchedulePart02Mixin, QzoneSchedulePart03Mixin):
    """Publish window planning and automated post lifecycle helpers."""


__all__ = (
    "QZONE_INTRA_DAY_GAP_FLOOR_MINUTES",
    "QZONE_LENGTH_HARD_LIMIT",
    "QZONE_LENGTH_PROFILES",
    "QZONE_NIGHT_RANGES",
    "QZONE_PLAN_ITEM_MAX_ATTEMPTS",
    "QZONE_WINDOW_TEMPLATE_DOUBLE",
    "QZONE_WINDOW_TEMPLATE_DOUBLE_NIGHT",
    "QzoneScheduleMixin",
)
