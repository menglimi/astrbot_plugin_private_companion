# -*- coding: utf-8 -*-
# 门面保留 random 名字（拆分前定义在宿主）：tests 通过
# patch("astrbot_plugin_private_companion.group_wakeup.random.random") 注随机数。
import random

from .group_wakeup_shared import (
    DEFAULT_AI_DAILY_NEWS_SOURCE,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    _persona_value,
    logger,
)
from .group_wakeup_shared import logger
from .group_wakeup_part04 import GroupWakeupPart04Mixin
from .group_wakeup_part03 import GroupWakeupPart03Mixin
from .group_wakeup_part02 import GroupWakeupPart02Mixin
from .group_wakeup_part01 import GroupWakeupPart01Mixin
class GroupWakeupMixin(GroupWakeupPart01Mixin, GroupWakeupPart02Mixin, GroupWakeupPart03Mixin, GroupWakeupPart04Mixin):
    """群聊唤醒"""
