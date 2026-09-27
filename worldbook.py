# -*- coding: utf-8 -*-
from .worldbook_shared import (
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
    logger,
)
from .worldbook_shared import logger
from .worldbook_part04 import WorldbookPart04Mixin
from .worldbook_part03 import WorldbookPart03Mixin
from .worldbook_part02 import WorldbookPart02Mixin
from .worldbook_part01 import WorldbookPart01Mixin
class WorldbookMixin(WorldbookPart01Mixin, WorldbookPart02Mixin, WorldbookPart03Mixin, WorldbookPart04Mixin):
    """Worldbook/关系网管理"""
