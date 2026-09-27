# -*- coding: utf-8 -*-
"""
ProactiveEngineMixin — 主动行为候选、决策、计划事件与动作选择
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import importlib
import inspect
import json
import math
import os
import random
import re
import shutil
import sys
import time
import unicodedata
import uuid
import zoneinfo
from copy import deepcopy
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from xml.etree import ElementTree as ET

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core.db.po import Conversation
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.star_handler import EventType
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    import chinese_calendar as calendar_cn
except Exception:
    calendar_cn = None

try:
    from lunarcalendar import Converter, Solar
except Exception:
    Converter = None
    Solar = None

from .constants import (
    DEFAULT_DAILY_PLAN_ITEMS,
    DEFAULT_HUMANIZED_STATE,
    PLUGIN_NAME,
    DATA_VERSION,
    PROACTIVE_ABILITY_REGISTRY,
    VOICE_FALLBACK_TEMPLATES,
    TIMER_TAG_PATTERN,
    SUPPORTED_TIMER_FORMATS,
    _ACTION_TEXT,
    _DATA_STORE_KEYS,
    _DEFAULT_GROUP_TEMPLATE,
    _DEFAULT_USER_TEMPLATE,
    _REASON_TEXT,
    _SIMULATION_FALLBACK_EVENTS,
)
from .dreaming import (
    build_dream_memory_fragments,
    dream_fragment_effective_weight,
    dream_theme_specs,
    extract_weighted_dream_fragments,
    fallback_diary_payload,
    fallback_dream_fragments_for_diary,
    generate_daily_diary,
    generate_enhanced_dream_pick,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .helpers import _date_key, _now_ts, _path_text, _redact_outbound_secrets, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _today_key, normalize_legacy_tag_text
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .memory_context_policy import core_memory_usage_contract_section
from .planning import (
    build_daily_plan_prompt,
    build_detail_enhancement_prompt,
    format_plan_for_diary,
    generate_daily_plan,
    generate_detail_enhancement,
    get_schedule_planning_prompt,
    normalize_long_term_events,
    normalize_story_items,
    normalize_story_plan,
    pick_detail_segment,
)
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger
from .proactive_engine_gate import ProactiveEngineGateMixin
from .proactive_engine_candidate import ProactiveEngineCandidateMixin
from .proactive_engine_persona import ProactiveEnginePersonaMixin
from .proactive_engine_mood import ProactiveEngineMoodMixin
from .proactive_engine_event import ProactiveEngineEventMixin
from .proactive_engine_reason import ProactiveEngineReasonMixin
from .proactive_engine_action import ProactiveEngineActionMixin
from .proactive_engine_photo import ProactiveEnginePhotoMixin
from .proactive_engine_topic import ProactiveEngineTopicMixin
from .proactive_engine_motive import ProactiveEngineMotiveMixin
from .proactive_engine_birthday import ProactiveEngineBirthdayMixin
from .proactive_engine_meal import ProactiveEngineMealMixin
from .proactive_engine_simulation import ProactiveEngineSimulationMixin
from .proactive_engine_audit import ProactiveEngineAuditMixin
from .proactive_engine_timer import ProactiveEngineTimerMixin
from .proactive_engine_ability import ProactiveEngineAbilityMixin
from .proactive_engine_weather import ProactiveEngineWeatherMixin
from .proactive_engine_shared import (

    _engine_proactive_window_timezone,

    _persona_provider_id,

)


logger = get_module_logger(__name__)


DEFAULT_AI_DAILY_NEWS_SOURCE = "B站 AI早报|bilibili:285286947"

# Low-frequency personal-context routes: probabilities are intentionally
# conservative, while the age/cooldown windows keep a residue from resurfacing.
DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
        DEFAULT_AI_DAILY_NEWS_SOURCE,
    ]
)

LEGACY_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
    ]
)

PREVIOUS_TECH_DEFAULT_NEWS_SOURCES = "\n".join(
    [
        "BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml",
        "Google新闻中文|https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        "Solidot|https://www.solidot.org/index.rss",
        "Hacker News|https://hnrss.org/frontpage",
        "MIT Technology Review|https://www.technologyreview.com/feed/",
        "Ars Technica|https://feeds.arstechnica.com/arstechnica/index",
    ]
)



_LUNAR_MONTH_NAMES = [
    "正月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "冬月",
    "腊月",
]
_LUNAR_DAY_NAMES = [
    "初一",
    "初二",
    "初三",
    "初四",
    "初五",
    "初六",
    "初七",
    "初八",
    "初九",
    "初十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
    "十六",
    "十七",
    "十八",
    "十九",
    "二十",
    "廿一",
    "廿二",
    "廿三",
    "廿四",
    "廿五",
    "廿六",
    "廿七",
    "廿八",
    "廿九",
    "三十",
]
_SOLAR_TERM_DATES = {
    (1, 5): "小寒",
    (1, 20): "大寒",
    (2, 4): "立春",
    (2, 19): "雨水",
    (3, 5): "惊蛰",
    (3, 20): "春分",
    (4, 4): "清明",
    (4, 20): "谷雨",
    (5, 5): "立夏",
    (5, 21): "小满",
    (6, 5): "芒种",
    (6, 21): "夏至",
    (7, 7): "小暑",
    (7, 22): "大暑",
    (8, 7): "立秋",
    (8, 23): "处暑",
    (9, 7): "白露",
    (9, 23): "秋分",
    (10, 8): "寒露",
    (10, 23): "霜降",
    (11, 7): "立冬",
    (11, 22): "小雪",
    (12, 7): "大雪",
    (12, 22): "冬至",
}
_ALMANAC_YI = ["整理房间", "写字", "散步", "读书", "听歌", "轻度创作", "复盘", "安静休息"]
_ALMANAC_JI = ["熬夜", "冲动发言", "硬撑", "反复纠结", "过度解释", "临时加压", "情绪化决定"]
_PLATFORM_DISPLAY_NAMES = {
    "aiocqhttp": "QQ",
    "qq": "QQ",
    "onebot": "QQ",
    "telegram": "Telegram",
    "wechat": "微信",
    "discord": "Discord",
}




class ProactiveEngineMixin(ProactiveEngineWeatherMixin, ProactiveEngineAbilityMixin, ProactiveEngineTimerMixin, ProactiveEngineAuditMixin, ProactiveEngineSimulationMixin, ProactiveEngineMealMixin, ProactiveEngineBirthdayMixin, ProactiveEngineMotiveMixin, ProactiveEngineTopicMixin, ProactiveEnginePhotoMixin, ProactiveEngineActionMixin, ProactiveEngineReasonMixin, ProactiveEngineEventMixin, ProactiveEngineMoodMixin, ProactiveEnginePersonaMixin, ProactiveEngineCandidateMixin, ProactiveEngineGateMixin):
    """主动行为候选、决策、计划事件与动作选择"""

    def _proactive_current_agenda_item(self) -> dict[str, Any] | None:
        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            if not isinstance(item, dict):
                return None
            # Calendar context is injected into generation/review prompts. It
            # must not silently delete a plan here: an old plan may be a
            # deliberate continuation, an uncertain transition, or a user
            # correction that still needs to be reconciled conversationally.
            return item
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None

    def _normalize_legacy_proactive_text(self, value: Any, *, limit: int = 40) -> str:
        return _single_line(normalize_legacy_tag_text(value), limit)

    # Bot 自己睡着的相位；staying_up（临时晚睡）/woken/natural_wake 属于清醒，不拦。
    _PROACTIVE_SLEEP_BLOCK_PHASES = frozenset({"falling_asleep", "light_sleep", "sleeping_again"})
    # 睡着期间仍允许发出的少数事务/安全类 reason，与 busy 闸门的豁免语义对齐。
    _PROACTIVE_SLEEP_EXEMPT_REASONS = frozenset(
        {"weather_alert", "health_alert", "memo_note_reminder", "environment_change", "timer"}
    )
