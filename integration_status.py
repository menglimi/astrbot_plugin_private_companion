# -*- coding: utf-8 -*-
from .integration_status_shared import (
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
from .integration_status_shared import logger
from .integration_status_part03 import IntegrationStatusPart03Mixin
from .integration_status_part02 import IntegrationStatusPart02Mixin
from .integration_status_part01 import IntegrationStatusPart01Mixin
class IntegrationStatusMixin(IntegrationStatusPart01Mixin, IntegrationStatusPart02Mixin, IntegrationStatusPart03Mixin):
    """外部插件状态、世界观适配与运行环境描述"""
