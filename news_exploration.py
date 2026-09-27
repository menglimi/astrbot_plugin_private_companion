# -*- coding: utf-8 -*-
from .news_exploration_shared import (
    BILIBILI_AI_BOT_LEGACY_DATA_NAMES,
    BILIBILI_AI_BOT_PLUGIN_NAME,
    BILIBILI_PUBLIC_INFO_PLUGIN_NAME,
    DEFAULT_AI_DAILY_JUYA_UID,
    DEFAULT_AI_DAILY_MORNING_UID,
    DEFAULT_AI_DAILY_SOURCES,
    DEFAULT_NEWS_SOURCES,
    LEGACY_DEFAULT_NEWS_SOURCES,
    PREVIOUS_TECH_DEFAULT_NEWS_SOURCES,
    _ALMANAC_JI,
    _ALMANAC_YI,
    _LUNAR_DAY_NAMES,
    _LUNAR_MONTH_NAMES,
    _NEWS_BINARY_CONTENT_TYPES,
    _NEWS_BINARY_CONTENT_TYPE_PREFIXES,
    _NEWS_BINARY_SIGNATURES,
    _NEWS_MOJIBAKE_MARKERS,
    _NEWS_TEXTUAL_CONTENT_TYPES,
    _PLATFORM_DISPLAY_NAMES,
    _SOLAR_TERM_DATES,
    _decode_news_response_text,
    _news_charset_from_content_type,
    _news_content_type_base,
    _news_meta_charset,
    _news_response_looks_binary,
    _normalize_news_charset,
    _persona_provider_id,
    _score_news_decoded_text,
    logger,
)
from .news_exploration_shared import logger
from .news_exploration_web_search_infra_custom import NewsExplorationWebSearchInfraCustomMixin
from .news_exploration_web_exploration_trends_trigger import NewsExplorationWebExplorationTrendsTriggerMixin
from .news_exploration_news_summary_wish import NewsExplorationNewsSummaryWishMixin
from .news_exploration_news_reading_ai_daily import NewsExplorationNewsReadingAiDailyMixin
from .news_exploration_external_event import NewsExplorationExternalEventMixin
from .news_exploration_bilibili_trigger_news import NewsExplorationBilibiliTriggerNewsMixin
from .news_exploration_bilibili_integration_news import NewsExplorationBilibiliIntegrationNewsMixin
from .news_exploration_bilibili_fetch import NewsExplorationBilibiliFetchMixin
from .news_exploration_bilibili_discovery import NewsExplorationBilibiliDiscoveryMixin
class NewsExplorationMixin(NewsExplorationBilibiliDiscoveryMixin, NewsExplorationBilibiliFetchMixin, NewsExplorationBilibiliIntegrationNewsMixin, NewsExplorationBilibiliTriggerNewsMixin, NewsExplorationExternalEventMixin, NewsExplorationNewsReadingAiDailyMixin, NewsExplorationNewsSummaryWishMixin, NewsExplorationWebExplorationTrendsTriggerMixin, NewsExplorationWebSearchInfraCustomMixin):
    """新闻阅读/网页探索"""
